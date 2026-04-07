from django.urls import path

from workflows.views import FailedJobsView, RetryJobView

urlpatterns = [
    path('failed-jobs/', FailedJobsView.as_view(), name='failed-jobs'),
    path('retry-job/<int:job_id>/', RetryJobView.as_view(), name='retry-job'),
]