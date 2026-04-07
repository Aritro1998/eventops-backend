from rest_framework import status
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView
from django_filters.rest_framework import DjangoFilterBackend


from workflows.models import WorkflowJob
from rest_framework.views import APIView
from core.permissions import IsRoleAdmin
from rest_framework.response import Response
from workflows.tasks import process_workflow_job
from workflows.serializers import WorkflowJobSerializer

class FailedJobsView(ListAPIView):
    """
    Small admin endpoint for inspecting failed workflow jobs.
    Using ListAPIView keeps the code short while still allowing filtering.
    """
    permission_classes = [IsRoleAdmin]
    serializer_class = WorkflowJobSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["job_type", "created_at"]

    def get_queryset(self):
        return WorkflowJob.objects.filter(status="FAILED").order_by("-created_at")
    

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
        job.save(update_fields=["status", "retry_count", "last_error", "updated_at"])

        process_workflow_job.delay(job.id)

        serializer = WorkflowJobSerializer(job)

        return Response(serializer.data)
