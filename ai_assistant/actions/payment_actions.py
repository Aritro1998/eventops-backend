"""
Execute half of "propose vs execute" for payment retries — same structure
as booking_actions.py/cancellation_actions.py, with one addition:
stage_payment_retry is also called automatically (not just from the AI
tool) right after a payment attempt fails, from confirm_pending_booking
and confirm_payment_retry themselves, so the retry controls reappear
without the user having to ask the AI again.
"""

from asgiref.sync import sync_to_async

from payments.services import PaymentService
from ai_assistant.models import PendingPaymentRetry, get_pending_action_expiry

def stage_payment_retry(booking):
    """Create/replace the retry-confirmation row for this booking.

    Shared by prepare_payment_retry (the AI tool, conversational path) and
    confirm_pending_booking / confirm_payment_retry (the auto-staged path,
    called the instant a payment attempt fails) — both need the identical
    "remember this booking is awaiting a retry click" step, just reached
    differently. Callers are responsible for validating ownership/status
    first; this function only stages.
    """
    
    PendingPaymentRetry.objects.update_or_create(
        user=booking.user,
        defaults={"booking": booking, "expires_at": get_pending_action_expiry()},
    )
    

def get_pending_payment_retry_actions(user):
    """Return UI actions only when the user has a booking staged for retry."""
    
    pending = PendingPaymentRetry.for_user(user)
    
    if not pending:
        return []
    
    if pending.is_expired:
        pending.delete()
        return []
    
    if pending.booking.status not in ("FAILED", "PENDING"):
        pending.delete()
        return []
    
    return [
        {"type": "confirm_payment_retry", "label": "Retry Payment Now"},
        {"type": "dismiss_payment_retry", "label": "Not Now"},
    ]
    
    
async def get_pending_payment_retry_draft(user):
    """Return the staged retry's details for rendering, or None."""

    pending = await PendingPaymentRetry.afor_user(user)
    
    if not pending or pending.is_expired:
        return None

    if pending.booking.status not in ("FAILED", "PENDING"):
        await pending.adelete()
        return None

    booking = pending.booking
    # seat_display touches event.space (not covered by afor_user's
    # prefetch) and is shared with sync callers elsewhere, so it's wrapped
    # here rather than converted natively.
    seat_display = await sync_to_async(booking.seat_display)()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **seat_display,
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }


def confirm_payment_retry(user):
    """Actually attempt the payment again for the staged booking."""
    
    pending = PendingPaymentRetry.for_user(user)
    
    if not pending:
        raise ValueError("No pending payment retry found")
    
    if pending.is_expired:
        pending.delete()
        raise ValueError("This retry request has expired. Please try again.")
    
    if pending.booking.status not in ("FAILED", "PENDING"):
        pending.delete()
        raise ValueError("This booking is no longer eligible for a payment retry.")
    
    booking = pending.booking
    
    try:
        PaymentService.process_payment(booking.id)
    except ValueError:
        # Whatever blocked this attempt (expired, retry limit hit) means
        # there's nothing left to stage — clear the pending row so the
        # buttons don't linger for a booking that can no longer be retried.
        pending.delete()
        raise
    
    booking.refresh_from_db()

    if booking.status == "FAILED" and booking.retry_count < PaymentService.MAX_RETRIES:
        # Still eligible — replace the pending row so the buttons reappear
        # for another attempt, instead of leaving a stale one around.
        stage_payment_retry(booking)
    else:
        # CONFIRMED or EXPIRED — no more retries possible either way.
        pending.delete()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "status": booking.status,  # CONFIRMED, FAILED, or EXPIRED
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }
    
    
def dismiss_payment_retry(user):
    """Back out of a staged retry — the booking is left exactly as it was."""
    
    pending = PendingPaymentRetry.for_user(user)
    
    if not pending:
        raise ValueError("No pending payment retry found.")
    
    booking = pending.booking
    seat_display = booking.seat_display()
    pending.delete()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **seat_display,
        "status": booking.status,  # untouched — still whatever it was
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }
    