from openai import OpenAI
from django.db.models import Q
from django.db import transaction
from pgvector.django import CosineDistance

from knowledge.models import KnowledgeChunk
from core.settings.base import OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL


class KnowledgeService:
    """
    Chunking and embedding logic for KnowledgeDocument.
    """

    MAX_CHUNK_CHARS = 1000
    MIN_CHUNK_CHARS = 100
    OVERLAP_CHARS = 150
    EMBEDDING_MODEL = OPENAI_EMBEDDING_MODEL
    
    @staticmethod
    def chunk_text(content):
        """
        Split text into chunks, preferring natural paragraph boundaries
        (blank-line-separated sections). A paragraph shorter than
        MIN_CHUNK_CHARS — almost always a section heading on its own line —
        gets merged into the paragraph that follows it, so a heading never
        becomes its own near-empty, low-signal chunk. Falls back to a sliding
        window with overlap only for a paragraph that's still too long on its
        own — the overlap exists so a sentence split by the fallback doesn't
        lose surrounding context on either side.
        """
        # Browser <textarea> submissions arrive with \r\n line endings
        # (unlike the plain \n in script-seeded content) — normalize first,
        # or this split silently matches nothing and the whole document
        # falls through to the fixed-size fallback below instead of
        # chunking per paragraph.
        normalized = content.replace('\r\n', '\n')
        paragraphs = [p.strip() for p in normalized.split('\n\n') if p.strip()]
        
        merged = []
        for paragraph in paragraphs:
            if merged and len(merged[-1]) < KnowledgeService.MIN_CHUNK_CHARS:
                merged[-1] = merged[-1] + '\n\n' + paragraph
            else:
                merged.append(paragraph)
        
        chunks = []
        for paragraph in merged:
            if len(paragraph) <= KnowledgeService.MAX_CHUNK_CHARS:
                chunks.append(paragraph)
            else:
                start = 0
                while start < len(paragraph):
                    end = start + KnowledgeService.MAX_CHUNK_CHARS
                    chunks.append(paragraph[start:end])
                    start = end - KnowledgeService.OVERLAP_CHARS
                    
        return chunks
    
    @staticmethod
    def sync_chunks(document):
        """
        Re-chunk and re-embed a document, replacing any existing chunks.
        Idempotent — safe to call again after editing a document's content
        (triggered automatically on every save via knowledge/signals.py,
        or manually via the generate_knowledge_embeddings command).

        Embeddings are computed BEFORE touching the database, so a failed
        OpenAI call (network issue, rate limit) leaves the old chunks
        intact rather than wiping them out with nothing to replace them.
        """
        
        texts = KnowledgeService.chunk_text(document.content)
        
        embeddings = []
        if texts:
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.embeddings.create(model=KnowledgeService.EMBEDDING_MODEL, input=texts)
            embeddings = [item.embedding for item in response.data]

        with transaction.atomic():
            document.chunks.all().delete()
            
            KnowledgeChunk.objects.bulk_create([
                KnowledgeChunk(document=document, content=text, embedding=embedding, chunk_index=i)
                for i, (text, embedding) in enumerate(zip(texts, embeddings))
            ])
            
    @staticmethod
    def search(query, venue=None, event=None, top_k=5):
        """
        Return the top_k most relevant KnowledgeChunks for a query,
        optionally scoped to a venue and/or event. Embeds the query with
        the same model used for chunks, then ranks by cosine distance —
        pgvector's `<=>` operator, exposed here via CosineDistance.
        """
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.embeddings.create(model=KnowledgeService.EMBEDDING_MODEL, input=[query])
        query_embedding = response.data[0].embedding
        queryset = KnowledgeChunk.objects.select_related('document')
        
        if venue is not None or event is not None:
            scope = Q(document__venue__isnull=True, document__event__isnull=True)
            if venue is not None:
                scope |= Q(document__venue=venue)
            if event is not None:
                scope |= Q(document__event=event)
            queryset = queryset.filter(scope)
            
        return list(
            queryset.annotate(
                distance=CosineDistance('embedding', query_embedding)
            ).order_by('distance')[:top_k]
        )