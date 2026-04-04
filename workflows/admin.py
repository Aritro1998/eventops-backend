from django.contrib import admin
from .models import WorkflowJob


@admin.register(WorkflowJob)
class WorkflowJobAdmin(admin.ModelAdmin):
    list_display = ["id", "job_type", "booking", "status", "retry_count", "created_at", "updated_at"]
    list_filter = ["job_type", "status", "created_at"]
    search_fields = ["booking__user__username"]
    list_display_links = ["id", "booking", "job_type"]