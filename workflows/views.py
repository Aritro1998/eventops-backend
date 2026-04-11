from datetime import timedelta

from rest_framework import status
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView
from rest_framework.generics import RetrieveAPIView
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.pagination import CustomPagination
from workflows.models import WorkflowJob
from rest_framework.views import APIView
from core.permissions import IsRoleAdmin
from rest_framework.response import Response
from workflows.tasks import process_workflow_job
from workflows.serializers import WorkflowJobSerializer


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
            jobs = jobs.filter(created_at__date=created_date)

        return jobs


class WorkflowJobDetailView(RetrieveAPIView):
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
        threshold = timezone.now() - timedelta(minutes=5)
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
            jobs = jobs.filter(created_at__date=created_date)

        return jobs
    

class RetryJobView(APIView):
    """
    Reset a failed job back to PENDING so the worker can attempt it again.
    """
    permission_classes = [IsRoleAdmin]

    def post(self, request, job_id):

        job = get_object_or_404(WorkflowJob, id=job_id)

        if job.status != "FAILED":
            return Response({"error": "Job not in failed state"}, status=status.HTTP_400_BAD_REQUEST)

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

        serializer = WorkflowJobSerializer(job)

        return Response(serializer.data)
