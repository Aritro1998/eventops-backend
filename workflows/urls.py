from django.urls import path

from workflows.views import FailedJobsView, RetryJobView, WorkflowJobListView, WorkflowJobDetailView, StuckJobsView

urlpatterns = [
    path('jobs/', WorkflowJobListView.as_view(), name='workflow-jobs'),
    path('jobs/<int:id>/', WorkflowJobDetailView.as_view(), name='workflow-job-detail'),
    path('stuck-jobs/', StuckJobsView.as_view(), name='stuck-jobs'),
    path('failed-jobs/', FailedJobsView.as_view(), name='failed-jobs'),
    path('retry-job/<int:job_id>/', RetryJobView.as_view(), name='retry-job'),
]