from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from knowledge.models import KnowledgeDocument
from workflows.models import WorkflowJob
from workflows.tasks import process_workflow_job


@receiver(post_save, sender=KnowledgeDocument)
def knowledge_document_saved_handler(sender, instance, **kwargs):
    """Chunk/embed on every save (admin edit, future API, bulk import) —
    unlike the booking-confirmation signal, there's no dedup check here:
    re-chunking on every edit is the desired behavior, not a one-time event."""
    
    def create_workflow():
        job = (
            WorkflowJob.objects
            .create(
                job_type="KNOWLEDGE_CHUNKING",
                payload={"document_id": instance.id}
            )
        )
        
        process_workflow_job.delay(job.id)
        
    transaction.on_commit(create_workflow)