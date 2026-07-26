# Create your tests here.
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from events.models import Event, Seat
from bookings.models import Booking, BookingSeat
from langgraph.types import Command
from ai_assistant.langgraph_flows.booking_graph import get_booking_graph
from ai_assistant.langgraph_flows.payment_retry_graph import get_payment_retry_graph
from ai_assistant.langgraph_flows.checkpointer import close_checkpointer
from ai_assistant.actions.payment_actions import (
    dismiss_payment_retry,
    confirm_payment_retry,
    get_pending_payment_retry_actions,
)

User = get_user_model()


def tearDownModule():
    # Runs once, after every TestCase in this module has finished -
    # not per-class. The checkpointer's connection pool holds sessions
    # open against the test database; closing it per-class would leave
    # other classes' already-cached compiled graphs pointing at a pool
    # that's already closed, since closing the checkpointer doesn't
    # retroactively invalidate graphs built with it.
    close_checkpointer()


class TestBookingGraph(TestCase):

    def setUp(self):
        # get_booking_graph() must be called from inside a test method (or
        # setUp), never at module import time - see its own docstring for
        # why: only here is Django's test database guaranteed to already
        # be live.
        self.graph = get_booking_graph()

        self.user = User.objects.create_user(username="testuser", email="t@example.com", password="StrongPass@123")
        self.event = Event.objects.create(
            name="Test Event", description="", price=100.00, total_seats=10,
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=4),
            created_by=self.user,
        )
        self.seats = [Seat.objects.create(event=self.event, seat_number=i) for i in range(1, 4)]

        self.event2 = Event.objects.create(
            name="Test Event 2", description="", price=50.00, total_seats=10,
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=4),
            created_by=self.user,
        )
        Seat.objects.create(event=self.event2, seat_number=1)

    def _start(self, seat_numbers, thread_id=None, event=None):
        thread_id = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        self.graph.invoke(
            {"conversation_id": thread_id, "draft_id": str(uuid.uuid4()), "user_id": self.user.id,
             "event_id": (event or self.event).id, "seat_numbers": seat_numbers, "amount": "100.00"},
            config=config,
        )
        return config

    def test_cancel_leaves_no_booking(self):
        config = self._start([1])
        result = self.graph.invoke(Command(resume="cancel"), config=config)
        self.assertEqual(result["result"]["status"], "cancelled")
        self.assertFalse(Booking.objects.filter(event=self.event).exists())

    @patch("payments.services.random.choice")
    def test_confirm_success_creates_real_booking(self, mock_choice):
        mock_choice.return_value = True
        config = self._start([1])
        result = self.graph.invoke(Command(resume="confirm"), config=config)
        self.assertEqual(result["result"]["status"], "CONFIRMED")
        booking = Booking.objects.get(id=result["result"]["booking_id"])
        self.assertEqual(booking.status, "CONFIRMED")
        # regression check for the seat_number -> Seat.id bug
        self.assertEqual(
            set(BookingSeat.objects.filter(booking=booking).values_list("seat__seat_number", flat=True)),
            {1},
        )

    @patch("payments.services.random.choice")
    def test_confirm_failure_ends_graph_without_retrying(self, mock_choice):
        # booking_graph no longer handles retries itself - it should just
        # end with a FAILED result, leaving the retry loop entirely to
        # payment_retry_graph.
        mock_choice.return_value = False
        config = self._start([2])
        result = self.graph.invoke(Command(resume="confirm"), config=config)
        self.assertEqual(result["result"]["status"], "FAILED")
        # No pending nodes left - the graph truly ended here instead of
        # pausing for a retry decision like it used to.
        self.assertEqual(self.graph.get_state(config).next, ())

    @patch("payments.services.random.choice")
    def test_two_confirms_on_same_conversation_create_two_bookings(self, mock_choice):
        # Regression test: confirm_node's idempotency key must not be
        # scoped to conversation_id/thread_id alone - a single chat
        # conversation legitimately books more than one event back to
        # back, and thread_id stays the same for the whole conversation.
        # A stale/reused key would make the second confirm silently
        # return the first booking instead of creating a new one.
        mock_choice.return_value = True
        thread_id = str(uuid.uuid4())

        config = self._start([1], thread_id=thread_id, event=self.event)
        first = self.graph.invoke(Command(resume="confirm"), config=config)

        config = self._start([1], thread_id=thread_id, event=self.event2)
        second = self.graph.invoke(Command(resume="confirm"), config=config)

        self.assertNotEqual(first["result"]["booking_id"], second["result"]["booking_id"])
        self.assertTrue(Booking.objects.filter(event=self.event).exists())
        self.assertTrue(Booking.objects.filter(event=self.event2).exists())


class TestPaymentRetryGraph(TestCase):

    def setUp(self):
        self.booking_graph = get_booking_graph()
        self.retry_graph = get_payment_retry_graph()

        self.user = User.objects.create_user(username="testuser2", email="t2@example.com", password="StrongPass@123")
        self.event = Event.objects.create(
            name="Test Event 2", description="", price=100.00, total_seats=10,
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=4),
            created_by=self.user,
        )
        Seat.objects.create(event=self.event, seat_number=1)

    def _make_failed_booking(self):
        # random.choice is patched by each test method itself (see below) -
        # this only runs while that patch is active, forcing the first
        # payment attempt to fail.
        booking_thread = str(uuid.uuid4())
        config = {"configurable": {"thread_id": booking_thread}}
        self.booking_graph.invoke(
            {"conversation_id": booking_thread, "draft_id": str(uuid.uuid4()), "user_id": self.user.id,
             "event_id": self.event.id, "seat_numbers": [1], "amount": "100.00"},
            config=config,
        )
        result = self.booking_graph.invoke(Command(resume="confirm"), config=config)
        self.assertEqual(result["result"]["status"], "FAILED")
        return result["result"]["booking_id"]

    @patch("payments.services.random.choice")
    def test_retry_succeeds_from_a_fresh_thread(self, mock_choice):
        mock_choice.return_value = False
        booking_id = self._make_failed_booking()

        # This is the key behavior being tested: a completely separate
        # thread_id, keyed only by booking_id, with no connection at all
        # to the conversation that originally created the booking.
        config = {"configurable": {"thread_id": f"booking-{booking_id}"}}
        self.retry_graph.invoke({"booking_id": booking_id}, config=config)

        mock_choice.return_value = True
        result = self.retry_graph.invoke(Command(resume="retry"), config=config)
        self.assertEqual(result["result"]["status"], "CONFIRMED")

        booking = Booking.objects.get(id=booking_id)
        self.assertEqual(booking.status, "CONFIRMED")

    @patch("payments.services.random.choice")
    def test_retries_exhausted_expires(self, mock_choice):
        mock_choice.return_value = False
        booking_id = self._make_failed_booking()

        config = {"configurable": {"thread_id": f"booking-{booking_id}"}}
        self.retry_graph.invoke({"booking_id": booking_id}, config=config)  # pauses, no result yet
        result = self.retry_graph.invoke(Command(resume="retry"), config=config)
        while result["result"]["status"] == "FAILED":
            result = self.retry_graph.invoke(Command(resume="retry"), config=config)
        self.assertEqual(result["result"]["status"], "EXPIRED")

    @patch("payments.services.random.choice")
    def test_give_up_leaves_booking_failed(self, mock_choice):
        mock_choice.return_value = False
        booking_id = self._make_failed_booking()

        config = {"configurable": {"thread_id": f"booking-{booking_id}"}}
        self.retry_graph.invoke({"booking_id": booking_id}, config=config)
        result = self.retry_graph.invoke(Command(resume="give_up"), config=config)
        self.assertEqual(result["result"]["booking_id"], booking_id)

        booking = Booking.objects.get(id=booking_id)
        self.assertEqual(booking.status, "FAILED")

    @patch("payments.services.random.choice")
    def test_dismissed_retry_does_not_reappear_in_pending_actions(self, mock_choice):
        # Regression test: Booking.status stays FAILED after a user gives
        # up on a retry (nothing else changes it), so the pending-actions
        # check can't rely on status alone or "Not Now" would never
        # actually make the Retry Payment Now / Not Now controls go away.
        mock_choice.return_value = False
        booking_id = self._make_failed_booking()
        config = {"configurable": {"thread_id": f"booking-{booking_id}"}}
        self.retry_graph.invoke({"booking_id": booking_id}, config=config)

        self.assertTrue(get_pending_payment_retry_actions(self.user))

        dismiss_payment_retry(self.user)

        self.assertEqual(get_pending_payment_retry_actions(self.user), [])
        with self.assertRaises(ValueError):
            confirm_payment_retry(self.user)
