import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from bookings.models import Booking
from workflows.models import WorkflowJob
from django.core.mail import EmailMultiAlternatives

from django.conf import settings
from workflows.services import requeue_pending_jobs

logger = logging.getLogger(__name__)

@shared_task(bind=True, acks_late=True)
def process_workflow_job(self, job_id):
    """
    Celery task to process a workflow job.
    The task claims the job first, performs the side effect outside the lock,
    and then re-locks the row before writing the final state.
    This pattern keeps the critical section small while still protecting the
    workflow status transitions from concurrent workers.
    """

    MAX_RETRIES = 3

    with transaction.atomic():

        job = WorkflowJob.objects.select_for_update().get(id=job_id)

        if job.status != "PENDING":
            logger.info(
                "workflow_job_skipped",
                extra={
                    "event": "workflow_job_skipped",
                    "workflow_job_id": job.id,
                    "status": job.status,
                }
            )
            return

        job.status = "IN_PROGRESS"
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        logger.info(
            "workflow_job_started",
            extra={
                "event": "workflow_job_started",
                "workflow_job_id": job.id,
                "job_type": job.job_type,
                "booking_id": job.booking_id,
            }
        )

    try:

        # Process the job based on its type
        if job.job_type.strip().upper() == "BOOKING_CONFIRMATION":
            handle_booking_confirmation(job)
        elif job.job_type.strip().upper() == "BOOKING_EXPIRY":
            handle_booking_expiry(job)
        else:
            raise ValueError(f"Unknown job type: {job.job_type}")

        # mark success only after processing
        with transaction.atomic():
            job = WorkflowJob.objects.select_for_update().get(id=job_id)
            job.status = "COMPLETED"
            job.completed_at = timezone.now()
            job.result = {
                "message": "Processed successfully",
                "job_type": job.job_type,
                "booking_id": job.booking_id,
            }
            job.save(update_fields=["status", "completed_at", "result", "updated_at"])
            logger.info(
                "workflow_job_completed",
                extra={
                    "event": "workflow_job_completed",
                    "workflow_job_id": job.id,
                    "job_type": job.job_type,
                    "booking_id": job.booking_id,
                }
            )

    except Exception as e:
        with transaction.atomic():
            job = WorkflowJob.objects.select_for_update().get(id=job_id)
            job.retry_count += 1
            job.last_error = str(e)

            if job.retry_count >= MAX_RETRIES:
                job.status = "FAILED"
                job.completed_at = timezone.now()
                job.save(update_fields=["status", "retry_count", "last_error", "updated_at", "completed_at"])
                logger.exception(
                    "workflow_job_failed",
                    extra={
                        "event": "workflow_job_failed",
                        "workflow_job_id": job.id,
                        "job_type": job.job_type,
                        "booking_id": job.booking_id,
                        "retry_count": job.retry_count,
                    }
                )
            else:
                job.status = "PENDING"
                job.completed_at = None
                job.result = None
                job.save(update_fields=["status", "retry_count", "last_error", "updated_at", "completed_at", "result"])
                logger.warning(
                    "workflow_job_retry_scheduled",
                    extra={
                        "event": "workflow_job_retry_scheduled",
                        "workflow_job_id": job.id,
                        "job_type": job.job_type,
                        "booking_id": job.booking_id,
                        "retry_count": job.retry_count,
                    }
                )

        if job.status == "PENDING":
            # Exponential backoff delay calculation
            delay = 5 * (2 ** (job.retry_count - 1))
            # Requeue the job with a delay for retry
            process_workflow_job.apply_async(
                args=[job.id],
                countdown=delay
            )


@shared_task
def requeue_pending_jobs_task():
    """
    Celery task to requeue pending workflow jobs.
    """
    logger.info(
        "workflow_requeue_started",
        extra={
            "event": "workflow_requeue_started",
        }
    )
    requeue_pending_jobs()


def handle_booking_confirmation(job):
    """
    Handle email sending for booking confirmation.
    Ensures idempotency by checking if the email has already been sent for this job.
    If email sending fails, the exception is raised to trigger a retry.
    """

    if job.is_email_sent:
        return

    email = job.payload.get("email")
    booking_id = job.payload.get("booking_id")
    event_name = job.payload.get("event_name", "Event")
    seat_number = job.payload.get("seat_number", "N/A")
    event_time = job.payload.get("event_time", "TBD")

    if not email:
        raise ValueError("Email not found in payload")

    subject = f"Booking Confirmed - {event_name}"

    text_content = f"""
    Your booking is confirmed.

    Event: {event_name}
    Seat: {seat_number}
    Time: {event_time}
    Booking ID: {booking_id}
    """

    html_content = f"""
    <html>
        <body style="font-family: Arial, sans-serif; background-color: #f6f6f6; padding: 20px;">
            <div style="max-width: 600px; margin: auto; background: white; padding: 20px; border-radius: 8px;">
                <h2 style="color: #333;">Booking Confirmed</h2>
                
                <p>Your booking has been successfully confirmed.</p>

                <table style="width: 100%; border-collapse: collapse; margin-top: 20px;">
                    <tr>
                        <td style="padding: 8px; font-weight: bold;">Event</td>
                        <td style="padding: 8px;">{event_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; font-weight: bold;">Seat</td>
                        <td style="padding: 8px;">{seat_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; font-weight: bold;">Time</td>
                        <td style="padding: 8px;">{event_time}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; font-weight: bold;">Booking ID</td>
                        <td style="padding: 8px;">{booking_id}</td>
                    </tr>
                </table>

                <p style="margin-top: 20px;">Thank you for using EventOps.</p>
            </div>
        </body>
    </html>
    """

    # Skip delivery cleanly when SMTP is not configured.
    if not settings.EMAIL_HOST_USER:
        logger.info(
            "workflow_confirmation_email_skipped",
            extra={
                "event": "workflow_confirmation_email_skipped",
                "workflow_job_id": job.id,
                "booking_id": job.booking_id,
            }
        )
        return

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )

    msg.attach_alternative(html_content, "text/html")
    msg.send()

    job.is_email_sent = True
    job.save(update_fields=["is_email_sent"])
    logger.info(
        "workflow_confirmation_email_sent",
        extra={
            "event": "workflow_confirmation_email_sent",
            "workflow_job_id": job.id,
            "booking_id": job.booking_id,
        }
    )


def handle_booking_expiry(job):
    """
    Expire bookings that have timed out before payment/confirmation finished.
    We re-fetch the booking under a row lock because payment retry/confirmation
    may be updating the same booking at roughly the same time.
    """
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(id=job.booking_id)
        now = timezone.now()

        # The task may wake up slightly early; return quietly until the booking is truly due.
        if now < booking.expires_at:
            return

        # If another workflow already moved the booking out of an expirable state,
        # we leave it alone.
        if booking.status not in ["PENDING", "FAILED"]:
            return

        booking.status = "EXPIRED"
        booking.save(update_fields=["status", "updated_at"])
        logger.info(
            "booking_expired",
            extra={
                "event": "booking_expired",
                "booking_id": booking.id,
                "event_id": booking.event_id,
                "seat_id": booking.seat_id,
            }
        )
