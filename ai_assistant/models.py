"""
These three Pending* models are the core of this project's AI safety
design: "propose vs execute". The AI's tools (ai_assistant/tools/) never
directly create a Booking, cancel one, or retry a payment — they only
create/update one of these Pending* rows (via update_or_create, so a user
has at most one live draft of each kind at a time). The actual state
change only happens when a human clicks a real button that hits a
dedicated view (see ai_assistant/actions/), never as a side effect of the
AI's tool call itself. This means an LLM hallucination — claiming an
action succeeded when the tool wasn't even called, or the model
misunderstanding a result — can produce a confusing chat message, but it
can never produce a wrong database write. That guarantee has held up
through everything found and fixed in this project's testing so far.

Each Pending* row also expires (get_pending_action_expiry below), and
workflows/tasks.py's cleanup_expired_* Celery tasks garbage-collect stale
ones periodically — an unconfirmed draft doesn't linger forever.
"""

from django.db import models
from users.models import User
from events.models import Event
from django.utils import timezone
from bookings.models import Booking

from datetime import timedelta


def get_pending_action_expiry():
    from bookings.services import BookingService

    return timezone.now() + timedelta(minutes=BookingService.EXPIRY_MINUTES)


# Create your models here.
class PendingActionBase(models.Model):
    """
    Shared shape for every "AI staged this, a human must confirm it" row —
    PendingBooking, PendingBookingCancellation, PendingPaymentRetry all need
    identical expiry tracking, just against different underlying data.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='%(class)s_user')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True, default=get_pending_action_expiry)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    class Meta:
        abstract = True


class PendingBooking(PendingActionBase):
    """Staged by ai_assistant/tools/booking_tools.py's prepare_booking.
    seat_numbers holds the chosen (or, for general admission events,
    auto-picked) seat numbers — confirming this draft (see
    ai_assistant/actions/booking_actions.py) re-resolves them to real Seat
    ids and creates the actual Booking."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="pending_booking_event")
    seat_numbers = models.JSONField(default=list)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    @classmethod
    def for_user(cls, user):
        """Every Pending* model repeats this same one-row-per-user lookup
        shape (see for_user on the other two below) — kept as a
        classmethod per model rather than a shared helper because each
        needs different select_related/prefetch_related paths."""
        return cls.objects.select_related("event").filter(user=user).first()

    @classmethod
    async def afor_user(cls, user):
        "Async version of for_user()"
        return await cls.objects.select_related("event").filter(user=user).afirst()


class PendingBookingThread(PendingActionBase):
    """
    Marker only — unlike PendingBooking, the actual draft content (event,
    seats, amount) lives in booking_graph's own checkpoint, keyed by
    conversation_id, not here. A LangGraph checkpoint can only be looked
    up if you already know its thread_id; there's no "find this user's
    paused thread" query against the checkpoint store. This row exists
    purely so that lookup stays possible: "does this user have an active
    draft, and which conversation is it on."
    """
    conversation_id = models.CharField(max_length=255)

    @classmethod
    def for_user(cls, user):
        return cls.objects.filter(user=user).first()

    @classmethod
    async def afor_user(cls, user):
        return await cls.objects.filter(user=user).afirst()


class PendingBookingCancellation(PendingActionBase):
    """Staged by ai_assistant/tools/booking_tools.py's prepare_cancel_booking.
    Points at an existing, already-CONFIRMED Booking — confirming this
    draft is what actually flips it to CANCELLED."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='pending_cancellations')

    @classmethod
    def for_user(cls, user):
        return (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .first()
        )

    @classmethod
    async def afor_user(cls, user):
        return await (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .afirst()
        )


class PendingPaymentRetry(PendingActionBase):
    """Staged by ai_assistant/tools/booking_tools.py's prepare_payment_retry.
    Points at a FAILED/PENDING Booking — confirming this draft is what
    actually calls PaymentService.process_payment again."""

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="pending_payment_retries")

    @classmethod
    def for_user(cls, user):
        return (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .first()
        )

    @classmethod
    async def afor_user(cls, user):
        return await (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .afirst()
        )


class UsageLog(models.Model):
    """
    One row per OpenAI API call (not per user turn - see chat_stream in
    ai_assistant/services.py, which can make up to MAX_TOOL_CALLS calls
    for a single user message, each logged separately here).
    """
    conversation_id = models.CharField(max_length=255, db_index=True)
    # SET_NULL, not CASCADE - a deleted user's usage history is still real
    # spend that happened and shouldn't silently disappear with them.
    # Nullable because anonymous (not-logged-in) chat is allowed.
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_logs")
    model = models.CharField(max_length=100)
    prompt_tokens = models.IntegerField()
    completion_tokens = models.IntegerField()
    total_tokens = models.IntegerField()
    system_prompt_tokens = models.IntegerField(null=True, blank=True)
    tool_called = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['created_at'])
        ]

