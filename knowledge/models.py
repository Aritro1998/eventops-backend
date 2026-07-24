from django.db import models
from pgvector.django import VectorField

from venues.models import Venue
from events.models import Event
from core.settings.base import EMBEDDING_DIMENSIONS

# Create your models here.
class KnowledgeDocument(models.Model):
    """
    A piece of RAG-answerable content, optionally attached to a Venue
    and/or an Event — both null means a global document (e.g. a general
    FAQ not specific to any venue/event). Deliberately two plain nullable
    FKs, not a GenericForeignKey: only two possible targets exist, so a
    GFK's extra indirection (content_type + object_id, no real FK
    constraint) would cost more than it buys here.

    venue stays on_delete=CASCADE: a KnowledgeDocument has no standalone
    value once its subject is gone — a venue's refund policy doc isn't
    worth keeping as an orphan once that venue no longer exists.

    event is on_delete=SET_NULL rather than CASCADE, specifically because
    a document can be attached to BOTH a venue and an event at once (see
    the admin's EventSelect). Deleting the event alone used to cascade
    the entire row away, destroying venue-relevant content that has
    independent value (e.g. "Parking & refund policy for Venue X,
    written for Event Y" would lose its parking/refund info for Venue X
    entirely, not just its event association). knowledge/signals.py's
    pre_delete handler on Event still deletes event-only documents (no
    venue set) to preserve the original "no standalone value" behavior
    for that case — only the dual-attached case is protected here.
    """
    venue = models.ForeignKey(Venue, null=True, blank=True, on_delete=models.CASCADE, related_name='knowledge_documents')
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.SET_NULL, related_name='knowledge_documents')
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-updated_at']
        

class KnowledgeChunk(models.Model):
    """
    One embeddable passage of a KnowledgeDocument. A document is chunked
    because embeddings only make sense at the granularity of a focused
    passage — embedding an entire multi-topic document (parking info,
    refund policy, accessibility, all in one) would blur those distinct
    topics into one vector, defeating the point of semantic search.
    """
    document = models.ForeignKey(KnowledgeDocument, on_delete=models.CASCADE, related_name='chunks')
    content = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS) # must match OPENAI_EMBEDDING_MODEL's output size
    chunk_index = models.PositiveIntegerField() # position within the parent document
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['document', 'chunk_index']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'chunk_index'], 
                name='unique_chunk_index_per_document'
            ),
        ]