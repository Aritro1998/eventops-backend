from django.core.management.base import BaseCommand

from knowledge.models import KnowledgeDocument
from knowledge.services import KnowledgeService


class Command(BaseCommand):
    help = "Regenerate chunks and embeddings for all (or one) KnowledgeDocument."

    def add_arguments(self, parser):
        parser.add_argument("--document-id", type=int, default=None)

    def handle(self, *args, **options):
        documents = KnowledgeDocument.objects.all()
        if options["document_id"]:
            documents = documents.filter(pk=options["document_id"])

        for doc in documents:
            KnowledgeService.sync_chunks(doc)
            self.stdout.write(f"Synced chunks for: {doc.title}")