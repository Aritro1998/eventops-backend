from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from events.models import Event
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


@receiver(pre_delete, sender=Event)
def event_deleted_knowledge_cleanup_handler(sender, instance, **kwargs):
    """
    KnowledgeDocument.event is on_delete=SET_NULL (see that model's
    docstring) so a document also attached to a Venue survives an Event
    deletion. But a document scoped to ONLY that event (no venue) has no
    standalone value once it's gone — same reasoning the model docstring
    gives for venue's CASCADE — so those still need to be deleted here,
    since SET_NULL alone would otherwise leave them behind as orphaned,
    unintentional "global" documents (venue=None, event=None looks
    identical to a real global FAQ).
    """
    instance.knowledge_documents.filter(venue__isnull=True).delete()