from .models import WorkflowJob


def requeue_pending_jobs():
    from .tasks import process_workflow_job

    jobs = WorkflowJob.objects.filter(status="PENDING")

    for job in jobs:
        process_workflow_job.delay(job.id)