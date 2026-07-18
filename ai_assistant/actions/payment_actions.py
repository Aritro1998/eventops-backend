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
    
    
def get_pending_payment_retry_draft(user):
    """Return the staged retry's details for rendering, or None."""
    
    pending = PendingPaymentRetry.for_user(user)
    
    if not pending or pending.is_expired:
        return None
    
    if pending.booking.status not in ("FAILED", "PENDING"):
        pending.delete()
        return None

    booking = pending.booking
    
    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        "seat_numbers": [bs.seat.seat_number for bs in booking.booking_seats.all()],
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
    seat_numbers = [bs.seat.seat_number for bs in booking.booking_seats.all()]
    
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
        "seat_numbers": seat_numbers,
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
    seat_numbers = [bs.seat.seat_number for bs in booking.booking_seats.all()]
    pending.delete()
    
    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        "seat_numbers": seat_numbers,
        "status": booking.status,  # untouched — still whatever it was
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }
    