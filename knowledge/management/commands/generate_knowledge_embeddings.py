import asyncio

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from knowledge.models import KnowledgeDocument
from knowledge.services import KnowledgeService


class Command(BaseCommand):
    help = "Regenerate chunks and embeddings for all (or one) KnowledgeDocument."

    def add_arguments(self, parser):
        parser.add_argument("--document-id", type=int, default=None)
        
    async def _sync_all(self, documents):
        """
        Sync chunks for all documents concurrently.
        Asyncio is used to run the sync_chunks method for each document in parallel, 
        improving performance when dealing with multiple documents.
        """
        async def sync_one(doc):
            # thread_sensitive defaults to True, which forces every
            # sync_to_async call onto one shared thread — silently
            # serializing this regardless of asyncio.gather below. Safe to
            # disable here since each call touches a different document's
            # own transaction.atomic() block, with no shared row between them.
            await sync_to_async(KnowledgeService.sync_chunks, thread_sensitive=False)(doc)
            self.stdout.write(f"Synced chunks for: {doc.title}")
        
        # Run the sync_one function for each document concurrently    
        await asyncio.gather(*(sync_one(doc) for doc in documents))

    def handle(self, *args, **options):
        documents = KnowledgeDocument.objects.all()
        if options["document_id"]:
            documents = documents.filter(pk=options["document_id"])
        documents = list(documents)
        
        # Run the asynchronous sync_all method using asyncio.run to ensure that the event loop is properly managed.
        asyncio.run(self._sync_all(documents))

       