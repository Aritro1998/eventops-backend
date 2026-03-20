from django.db import models
from django.db.models import Q

from bookings.models import Booking

# Create your models here.
class WorkflowJob(models.Model):

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
