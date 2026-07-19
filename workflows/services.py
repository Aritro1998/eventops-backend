"""
Plain functions rather than a class-based *Service like the other apps —
these are simple enough (two small queries, dispatch to Celery) that a
class wrapper wouldn't add anything. Split from tasks.py so this logic
can be imported without pulling in Celery's @shared_task machinery.
"""

from .models import WorkflowJob
from django.utils import timezone


def requeue_pending_jobs():
    """
    Requeue pending workflow jobs that are either:
        - Expiry jobs that are due (booking expires_at <= now)
        - Immediate jobs that are still pending (e.g., email sending)
    """
    from .tasks import process_workflow_job

    now = timezone.now()

    # Expiry jobs (time-based)
    expiry_jobs = WorkflowJob.objects.filter(
        status="PENDING",
        job_type="BOOKING_EXPIRY",
        booking__expires_at__lte=now
    )

    # Immediate jobs (email etc.)
    immediate_jobs = WorkflowJob.objects.filter(
        status="PENDING",
        job_type__in=["BOOKING_CONFIRMATION", "KNOWLEDGE_CHUNKING"]
    )

    for job in list(expiry_jobs) + list(immediate_jobs):
        process_workflow_job.delay(job.id)


def schedule_job(job, delay_seconds=0):
    """Dispatch a freshly-created WorkflowJob to Celery, immediately or
    after a delay (used by BookingService.create_booking to schedule a
    BOOKING_EXPIRY job for exactly when the booking's hold runs out)."""
    from .tasks import process_workflow_job

    if delay_seconds > 0:
        process_workflow_job.apply_async(args=[job.id], countdown=delay_seconds)
    else:
        process_workflow_job.delay(job.id)
