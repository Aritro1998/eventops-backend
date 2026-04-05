from celery import shared_task
from django.db import transaction
from django.core.mail import send_mail
from workflows.models import WorkflowJob
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from django.conf import settings
from workflows.services import requeue_pending_jobs


@shared_task(bind=True, max_retries=3, acks_late=True)
def process_workflow_job(self, job_id):
    """
    Celery task to process a workflow job.
    This task retrieves the job by ID, checks its status, and processes it accordingly.
    If the job is not in a "PENDING" state, it will be ignored.
    If an error occurs during processing, the task will retry up to 3 times with a delay of 5 seconds between retries. 
    The job's status and retry count are updated in the database
    """

    
    with transaction.atomic():

        job = WorkflowJob.objects.select_for_update().get(id=job_id)

        if job.status != "PENDING":
            return

        job.status = "IN_PROGRESS"
        job.save()

    try:

        # Process the job based on its type
        print(f"=> Processing job {job.id} of type {job.job_type}")
        if job.job_type.strip().upper() == "BOOKING_CONFIRMATION":
            handle_booking_confirmation(job)
        elif job.job_type.strip().upper() == "BOOKING_EXPIRY":
            handle_booking_expiry(job)
        else:
            print(f"=> Unknown job type: {job.job_type}")

        # mark success only after processing
        job.status = "COMPLETED"
        job.save(update_fields=["status"])

    except Exception as exc:

        job.retry_count += 1
        job.status = "FAILED"
        job.last_error = str(exc)
        job.save()

        raise self.retry(exc=exc, countdown=5)


@shared_task
def requeue_pending_jobs_task():
    """
    Celery task to requeue pending workflow jobs.
    """
    requeue_pending_jobs()


def handle_booking_confirmation(job):
    """
    Handle email sending for booking confirmation.
    Ensures idempotency by checking if the email has already been sent for this job.
    If email sending fails, the exception is raised to trigger a retry.
    """

    print("=> HANDLE BOOKING CALLED")

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

    try:
        # Check if email is configured
        if not settings.EMAIL_HOST_USER:
            print("Email not configured. Logging instead.")
            print(text_content)
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

        print(f"Email sent to {email}")

    except Exception as e:
        print(f"Email sending failed: {str(e)}")
        raise


def handle_booking_expiry(job):
    """
    Handle booking expiry by marking the booking as expired and freeing up the seat.
    This function should be called by a workflow job scheduled at the booking's expiry time.
    """

    booking = job.booking

    now = timezone.now()

    # WAIT until actual expiry time
    if now < booking.expires_at:
        return

    # critical safety check
    if booking.status not in ["PENDING", "FAILED"]:
        return

    booking.status = "EXPIRED"
    booking.save()

    booking.seat.is_booked = False
    booking.seat.save()