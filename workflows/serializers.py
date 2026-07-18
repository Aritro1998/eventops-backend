from rest_framework.serializers import ModelSerializer

from workflows.models import WorkflowJob

class WorkflowJobSerializer(ModelSerializer):
    """Read-only shape for the ops endpoints in workflows/views.py — no
    write serializer exists since jobs are only ever created by
    BookingService/signals, never directly via the API."""

    class Meta:
        model = WorkflowJob
        fields = [
            "id",
            "job_type",
            "status",
            "payload",           
            "result",            
            "last_error",
            "retry_count",
            "started_at",        
            "completed_at",      
            "created_at"
        ]
