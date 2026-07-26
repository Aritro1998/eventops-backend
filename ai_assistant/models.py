"""
These models are the core of this project's AI safety design: "propose
vs execute". The AI's tools (ai_assistant/langchain_tools/) never
directly create a Booking, cancel one, or retry a payment. A new booking
or a payment retry pauses a LangGraph graph (ai_assistant/langgraph_flows/)
via interrupt() and waits for a human to click a real button on a
dedicated view (see ai_assistant/actions/) before resuming it with
Command(resume=...); a cancellation instead stages a
PendingBookingCancellation row the same way the graphs used to be staged.
This means an LLM hallucination — claiming an action succeeded when the
tool wasn't even called, or the model misunderstanding a result — can
produce a confusing chat message, but it can never produce a wrong
database write. That guarantee has held up through everything found and
fixed in this project's testing so far.

PendingBookingThread and PendingBookingCancellation both expire
(get_pending_action_expiry below), and workflows/tasks.py's
cleanup_expired_* Celery tasks garbage-collect stale ones periodically —
an unconfirmed draft doesn't linger forever.
"""

from django.db import models
from users.models import User
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
    PendingBookingThread and PendingBookingCancellation both need identical
    expiry tracking, just against different underlying data.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='%(class)s_user')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True, default=get_pending_action_expiry)
    
    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    class Meta:
        abstract = True


class PendingBookingThread(PendingActionBase):
    """
    Marker only — the actual draft content (event, seats, amount) lives in
    booking_graph's own checkpoint, keyed by conversation_id, not here. A
    LangGraph checkpoint can only be looked up if you already know its
    thread_id; there's no "find this user's paused thread" query against
    the checkpoint store. This row exists purely so that lookup stays
    possible: "does this user have an active draft, and which conversation
    is it on."
    """
    conversation_id = models.CharField(max_length=255)

    @classmethod
    def for_user(cls, user):
        return cls.objects.filter(user=user).first()

    @classmethod
    async def afor_user(cls, user):
        return await cls.objects.filter(user=user).afirst()


class PendingBookingCancellation(PendingActionBase):
    """Staged by ai_assistant/langchain_tools/booking_tools.py's
    prepare_cancel_booking. Points at an existing, already-CONFIRMED
    Booking — confirming this draft is what actually flips it to
    CANCELLED."""
    
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
    
