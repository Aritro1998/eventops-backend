"""
Admin-only monitoring/ops endpoints for the WorkflowJob system (see
workflows/models.py and workflows/tasks.py) — browsing jobs, spotting
stuck/failed ones, and manually requeuing a failed job. All require
IsRoleAdmin (core/permissions.py) — this is an operational surface, not
something regular users or organizers touch.
"""

import logging

from datetime import UTC, datetime, time

from rest_framework import status
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView
from rest_framework.generics import RetrieveAPIView
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.pagination import CustomPagination
from workflows.models import WorkflowJob, STUCK_IN_PROGRESS_THRESHOLD
from rest_framework.views import APIView
from core.permissions import IsRoleAdmin
from rest_framework.response import Response
from workflows.tasks import process_workflow_job
from workflows.serializers import WorkflowJobSerializer

logger = logging.getLogger(__name__)


def utc_day_bounds(date_value):
    """Turn a date into a (start, end) datetime range covering that whole
    day in UTC — used by the created_date query param filters below."""
    start = datetime.combine(date_value, time.min, tzinfo=UTC)
    end = datetime.combine(date_value, time.max, tzinfo=UTC)
    return start, end


class WorkflowJobListView(ListAPIView):
    """
    Paginated monitoring endpoint for browsing workflow jobs.
    Supports lightweight filters that are convenient from the browser or Postman.
    """
    permission_classes = [IsRoleAdmin]
    queryset = WorkflowJob.objects.all().order_by("-created_at")
    serializer_class = WorkflowJobSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        jobs = self.queryset

        job_type = self.request.query_params.get("job_type")
        status_param = self.request.query_params.get("status")
        created_date = parse_date(self.request.query_params.get("created_date", ""))

        if job_type:
            jobs = jobs.filter(job_type=job_type)

        if status_param:
            jobs = jobs.filter(status=status_param)

        if created_date:
            start, end = utc_day_bounds(created_date)
            jobs = jobs.filter(created_at__range=(start, end))

        return jobs


class WorkflowJobDetailView(RetrieveAPIView):
    """Single job by id — for drilling into a specific job's payload/
    result/last_error found via the list/stuck/failed views above."""
    permission_classes = [IsRoleAdmin]
    queryset = WorkflowJob.objects.all()
    serializer_class = WorkflowJobSerializer
    lookup_field = "id"


class StuckJobsView(ListAPIView):
    """
    Show jobs that appear stuck in IN_PROGRESS for longer than the threshold.
    Keeping this paginated makes the endpoint usable even if many jobs pile up.
    """
    permission_classes = [IsRoleAdmin]
    serializer_class = WorkflowJobSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        threshold = timezone.now() - STUCK_IN_PROGRESS_THRESHOLD
        return WorkflowJob.objects.filter(
            status="IN_PROGRESS",
            started_at__lt=threshold
        ).order_by("-started_at")

   
class FailedJobsView(ListAPIView):
    """
    Small admin endpoint for inspecting failed workflow jobs.
    Supports simple filtering by job type or creation date using YYYY-MM-DD.
    """
    permission_classes = [IsRoleAdmin]
    serializer_class = WorkflowJobSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        jobs = WorkflowJob.objects.filter(status="FAILED").order_by("-created_at")

        job_type = self.request.query_params.get("job_type")
        created_date = parse_date(self.request.query_params.get("created_date", ""))

        if job_type:
            jobs = jobs.filter(job_type=job_type)

        if created_date:
            start, end = utc_day_bounds(created_date)
            jobs = jobs.filter(created_at__range=(start, end))

        return jobs
    

class RetryJobView(APIView):
    """
    Reset a failed (or stuck) job back to PENDING so the worker can
    attempt it again. Clears retry_count/last_error/timestamps back to a
    fresh-job state rather than just flipping status, so
    process_workflow_job's own retry-limit logic doesn't immediately
    re-fail it as already exhausted.

    Also accepts a job stuck IN_PROGRESS past STUCK_IN_PROGRESS_THRESHOLD
    (the same jobs StuckJobsView surfaces) — before this, a stuck job was
    visible to admins but nothing could actually recover it: the
    automated requeue_pending_jobs only scanned PENDING jobs, and this
    view only accepted FAILED ones. workflows/services.py's
    reset_stuck_jobs handles the same recovery automatically on the next
    beat tick; this is the immediate, manual version of that.
    """
    permission_classes = [IsRoleAdmin]

    def post(self, request, job_id):

        job = get_object_or_404(WorkflowJob, id=job_id)

        is_stuck_in_progress = (
            job.status == "IN_PROGRESS"
            and job.started_at is not None
            and job.started_at < timezone.now() - STUCK_IN_PROGRESS_THRESHOLD
        )

        if job.status != "FAILED" and not is_stuck_in_progress:
            logger.warning(
                "workflow_retry_rejected",
                extra={
                    "event": "workflow_retry_rejected",
                    "workflow_job_id": job.id,
                    "status": job.status,
                    "requested_by_user_id": request.user.id,
                }
            )
            return Response({"error": "Job not in failed or stuck state"}, status=status.HTTP_400_BAD_REQUEST)

        job.status = "PENDING"
        job.retry_count = 0
        job.last_error = ""
        job.started_at = None
        job.completed_at = None
        job.result = None
        job.is_email_sent = False
        job.save(
            update_fields=[
                "status",
                "retry_count",
                "last_error",
                "started_at",
                "completed_at",
                "result",
                "is_email_sent",
                "updated_at",
            ]
        )

        process_workflow_job.delay(job.id)
        logger.info(
            "workflow_job_requeued",
            extra={
                "event": "workflow_job_requeued",
                "workflow_job_id": job.id,
                "job_type": job.job_type,
                "requested_by_user_id": request.user.id,
            }
        )

        serializer = WorkflowJobSerializer(job)

        return Response(serializer.data)
