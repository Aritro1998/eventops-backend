from rest_framework.serializers import ModelSerializer

from workflows.models import WorkflowJob

class WorkflowJobSerializer(ModelSerializer):

    class Meta:
        model = WorkflowJob
        fields = [
            "id",
            "job_type",
            "status",
            "last_error",
            "retry_count",
            "created_at"
        ]
