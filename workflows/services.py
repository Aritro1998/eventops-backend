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
        job_type__in=["BOOKING_CONFIRMATION"]
    )

    for job in list(expiry_jobs) + list(immediate_jobs):
        process_workflow_job.delay(job.id)


def schedule_job(job, delay_seconds=0):
    from .tasks import process_workflow_job

    if delay_seconds > 0:
        process_workflow_job.apply_async(args=[job.id], countdown=delay_seconds)
    else:
        process_workflow_job.delay(job.id)
