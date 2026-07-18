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

    @classmethod
    def for_user(cls, user):
        """Every Pending* model repeats this same one-row-per-user lookup
        shape (see for_user on the other two below) — kept as a
        classmethod per model rather than a shared helper because each
        needs different select_related/prefetch_related paths."""
        return cls.objects.select_related("event").filter(user=user).first()

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="pending_booking_event")
    seat_numbers = models.JSONField(default=list)
    amount = models.DecimalField(max_digits=10, decimal_places=2)


class PendingBookingCancellation(PendingActionBase):
    """Staged by ai_assistant/tools/booking_tools.py's prepare_cancel_booking.
    Points at an existing, already-CONFIRMED Booking — confirming this
    draft is what actually flips it to CANCELLED."""

    @classmethod
    def for_user(cls, user):
        return (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .first()
        )

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='pending_cancellations')


class PendingPaymentRetry(PendingActionBase):
    """Staged by ai_assistant/tools/booking_tools.py's prepare_payment_retry.
    Points at a FAILED/PENDING Booking — confirming this draft is what
    actually calls PaymentService.process_payment again."""

    @classmethod
    def for_user(cls, user):
        return (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .first()
        )

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="pending_payment_retries")
    
