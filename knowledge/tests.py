from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from venues.models import Venue
from events.models import Event
from knowledge.models import KnowledgeDocument
from knowledge.services import KnowledgeService
from workflows.models import WorkflowJob

User = get_user_model()


class TestChunkText(TestCase):
    """Pure text-splitting logic - no OpenAI calls, no database, safe to
    test directly without any API key. sync_chunks/search (which do call
    OpenAI to embed the resulting chunks) are deliberately NOT covered
    here."""

    def test_empty_content_returns_no_chunks(self):
        self.assertEqual(KnowledgeService.chunk_text(""), [])

    def test_single_paragraph_becomes_one_chunk(self):
        content = "A single short paragraph, well under the chunk size limit."
        self.assertEqual(KnowledgeService.chunk_text(content), [content])

    def test_short_paragraph_merges_into_the_one_that_follows(self):
        # A short paragraph (almost always a heading on its own line) must
        # never become its own near-empty, low-signal chunk.
        heading = "Refund Policy"
        body = (
            "Refunds are available up to 24 hours before the event start "
            "time, minus a processing fee."
        )
        content = f"{heading}\n\n{body}"

        chunks = KnowledgeService.chunk_text(content)

        self.assertEqual(chunks, [f"{heading}\n\n{body}"])

    def test_normalizes_windows_line_endings(self):
        # Browser <textarea> submissions arrive with \r\n - without
        # normalizing first, '\n\n'.split() silently matches nothing and
        # the whole document falls through to the sliding-window fallback
        # instead of splitting per paragraph.
        para_one = "A" * 150  # over MIN_CHUNK_CHARS so it isn't merged away
        para_two = "B" * 150
        content = f"{para_one}\r\n\r\n{para_two}"

        chunks = KnowledgeService.chunk_text(content)

        self.assertEqual(chunks, [para_one, para_two])

    def test_long_paragraph_falls_back_to_overlapping_sliding_window(self):
        content = "X" * 1200  # over MAX_CHUNK_CHARS (1000)

        chunks = KnowledgeService.chunk_text(content)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(chunks[0]), KnowledgeService.MAX_CHUNK_CHARS)
        self.assertEqual(chunks[1], content[850:1200])
        # The overlap exists so a sentence split by the fallback doesn't
        # lose surrounding context - confirm the tail of chunk 1 actually
        # reappears at the head of chunk 2.
        self.assertEqual(
            chunks[0][-KnowledgeService.OVERLAP_CHARS:],
            chunks[1][:KnowledgeService.OVERLAP_CHARS],
        )


class TestKnowledgeDocumentSavedSignal(TestCase):
    """knowledge_document_saved_handler schedules its WorkflowJob inside
    transaction.on_commit, which never fires under a plain TestCase (each
    test runs inside a transaction that's rolled back, never committed) -
    captureOnCommitCallbacks is what lets this be tested without the
    slower TransactionTestCase."""

    @patch("knowledge.signals.process_workflow_job.delay")
    def test_saving_a_document_schedules_a_chunking_job(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            document = KnowledgeDocument.objects.create(
                title="Refund Policy",
                content="Refunds are available up to 24 hours before the event.",
            )

        job = WorkflowJob.objects.get(job_type="KNOWLEDGE_CHUNKING")
        self.assertEqual(job.payload, {"document_id": document.id})
        mock_delay.assert_called_once_with(job.id)


class TestEventDeletedKnowledgeCleanupSignal(TestCase):
    """KnowledgeDocument.event is on_delete=SET_NULL so a document also
    attached to a Venue survives an Event deletion - but a document
    scoped to ONLY that event has no standalone value and must actually
    be deleted, or SET_NULL alone would leave it behind looking like a
    real global FAQ (venue=None, event=None is indistinguishable from
    one otherwise)."""

    def setUp(self):
        self.organizer = User.objects.create_user(
            username="organizer",
            email="org@test.com",
            password="StrongPass@123",
            role="ORGANIZER",
        )
        self.venue = Venue.objects.create(
            name="Test Venue",
            address="123 Main St",
            city="Kolkata",
            created_by=self.organizer,
        )
        self.event = Event.objects.create(
            name="Test Event",
            total_seats=100,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            created_by=self.organizer,
            price=100,
        )

    @patch("knowledge.signals.process_workflow_job.delay")
    def test_deleting_event_removes_event_only_documents(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            doc = KnowledgeDocument.objects.create(
                title="Event-only doc",
                content="Doors open one hour before showtime.",
                event=self.event,
            )

        self.event.delete()

        self.assertFalse(KnowledgeDocument.objects.filter(id=doc.id).exists())

    @patch("knowledge.signals.process_workflow_job.delay")
    def test_deleting_event_keeps_documents_also_attached_to_a_venue(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            doc = KnowledgeDocument.objects.create(
                title="Venue+event doc",
                content="Parking is available on Lot B.",
                event=self.event,
                venue=self.venue,
            )

        self.event.delete()

        doc.refresh_from_db()
        self.assertIsNone(doc.event)
        self.assertEqual(doc.venue, self.venue)
