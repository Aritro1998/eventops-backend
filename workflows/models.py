"""
A WorkflowJob is a durable, retryable record of "something needs to happen
asynchronously" — currently used for booking-expiry timers and
confirmation emails. It's a DB row, not just a Celery task, specifically
so retries survive a worker crash: workflows/tasks.py's
requeue_pending_jobs_task (see CELERY_BEAT_SCHEDULE in settings) scans for
jobs stuck in PENDING/IN_PROGRESS and re-dispatches them, something a
bare Celery task with no DB record can't recover from.
"""

from datetime import timedelta

from django.db import models
from django.db.models import Q

from bookings.models import Booking

# Shared by StuckJobsView (what counts as "stuck" for visibility) and
# workflows/services.py's requeue_pending_jobs (what actually gets reset
# and re-dispatched) — a job sitting IN_PROGRESS past this long almost
# certainly means the worker that claimed it died (OOM kill, restart)
# before it could finish, since acks_late redelivery of the same message
# just gets skipped by process_workflow_job's own `status != PENDING`
# guard rather than actually retried.
STUCK_IN_PROGRESS_THRESHOLD = timedelta(minutes=5)


class WorkflowJob(models.Model):
    """`job_type` is a free-text label (not an FK/enum) matched against in
    workflows/tasks.py's process_workflow_job dispatcher — see that file
    for the actual set of handled types (e.g. BOOKING_EXPIRY,
    BOOKING_CONFIRMATION). `payload` carries whatever that handler needs,
    shaped differently per job_type."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    job_type = models.CharField(max_length=50, db_index=True)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="workflow_jobs", null=True, blank=True)
    status = models.CharField(max_length=20, default='PENDING', choices=STATUS_CHOICES, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    retry_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_error = models.TextField(blank=True)
    is_email_sent = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"Workflow Job {self.id} - {self.job_type} - {self.status}"
    
    class Meta:
        ordering = ['-created_at']
        # Ensure retry_count is non-negative and does not exceed a reasonable limit (e.g., 5)
        constraints = [
            models.CheckConstraint(
                condition=Q(retry_count__gte=0),
                name='check_retry_count_non_negative'
            ),
            models.CheckConstraint(
                condition=Q(retry_count__lte=5),
                name='check_retry_count_max'
            )
        ]
        # Composite index for efficient querying by status and job_type
        indexes = [
            models.Index(fields=['status', 'job_type']),
        ]
