from rest_framework.serializers import ModelSerializer

from workflows.models import WorkflowJob

class WorkflowJobSerializer(ModelSerializer):

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
