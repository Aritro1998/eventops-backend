"""
Plain functions rather than a class-based *Service like the other apps —
these are simple enough (two small queries, dispatch to Celery) that a
class wrapper wouldn't add anything. Split from tasks.py so this logic
can be imported without pulling in Celery's @shared_task machinery.
"""

from django.db import transaction
from django.utils import timezone

from .models import WorkflowJob, STUCK_IN_PROGRESS_THRESHOLD


def requeue_pending_jobs():
    """
    Requeue pending workflow jobs that are either:
        - Expiry jobs that are due (booking expires_at <= now)
        - Immediate jobs that are still pending (e.g., email sending)

    Also recovers jobs abandoned mid-processing — see reset_stuck_jobs.
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

    reset_stuck_jobs()


def reset_stuck_jobs():
    """
    Recover jobs whose worker died after claiming them but before
    finishing (OOM kill, restart, etc.). process_workflow_job's own
    `if job.status != "PENDING": return` guard means simply re-dispatching
    an IN_PROGRESS job is a silent no-op, so it has to be reset to PENDING
    first — this is the automated counterpart to RetryJobView's manual
    "retry a stuck job" path in workflows/views.py, both driven by the
    same STUCK_IN_PROGRESS_THRESHOLD.

    This is a heuristic, not a lock: if a worker is merely slow rather
    than dead and finishes right as this runs, the job could theoretically
    get processed twice. Every handler (handle_booking_expiry,
    handle_booking_confirmation, handle_knowledge_chunking) is already
    idempotent for exactly this reason, so a rare double-dispatch is
    harmless rather than a correctness bug.
    """
    from .tasks import process_workflow_job

    stale_before = timezone.now() - STUCK_IN_PROGRESS_THRESHOLD

    with transaction.atomic():
        stuck_job_ids = list(
            WorkflowJob.objects.select_for_update()
            .filter(status="IN_PROGRESS", started_at__lt=stale_before)
            .values_list("id", flat=True)
        )
        WorkflowJob.objects.filter(id__in=stuck_job_ids).update(status="PENDING")

    for job_id in stuck_job_ids:
        process_workflow_job.delay(job_id)


def schedule_job(job, delay_seconds=0):
    """Dispatch a freshly-created WorkflowJob to Celery, immediately or
    after a delay (used by BookingService.create_booking to schedule a
    BOOKING_EXPIRY job for exactly when the booking's hold runs out)."""
    from .tasks import process_workflow_job

    if delay_seconds > 0:
        process_workflow_job.apply_async(args=[job.id], countdown=delay_seconds)
    else:
        process_workflow_job.delay(job.id)
