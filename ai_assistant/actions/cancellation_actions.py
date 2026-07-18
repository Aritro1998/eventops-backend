"""
Execute half of "propose vs execute" for cancellations — mirrors
booking_actions.py's structure exactly (get_pending_*_actions/draft for
the frontend, confirm_*/dismiss_* triggered only by a human button click).
"""

from ai_assistant.models import PendingBookingCancellation
from bookings.services import BookingService


def get_pending_cancellation_actions(user):
    """Return UI actions only when the user has a booking staged for cancellation."""
    
    pending = PendingBookingCancellation.for_user(user)
    
    if not pending:
        return []
    
    if pending.is_expired:
        pending.delete()
        return []
    
    return [
        {"type": "confirm_cancel_booking", "label": "Confirm Cancellation"},
        {"type": "keep_booking", "label": "Keep Booking"},
    ]
    

def get_pending_cancellation_draft(user):
    """Return the staged cancellation's details for rendering, or None."""
    
    pending = PendingBookingCancellation.for_user(user)
    
    if not pending or pending.is_expired:
        return None
    
    booking = pending.booking
    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
    }


def confirm_cancellation(user):
    """Actually cancel the booking the user staged for cancellation."""
    
    pending = PendingBookingCancellation.for_user(user)
    
    if not pending:
        raise ValueError("No pending cancellation found")
    
    if pending.is_expired:
        pending.delete()
        raise ValueError("This cancellation request has expired. Please try again.")
    
    booking = pending.booking
    seat_display = booking.seat_display()

    try:
        # This raises ValueError if the booking is no longer CONFIRMED (e.g. it
        # expired or was already cancelled through another path since staging) —
        # that's the re-validation this design relies on instead of an expiry field.
        booking = BookingService.cancel_booking(booking)
    except ValueError:
        # Whatever blocked this attempt means there's nothing left to stage —
        # clear the pending row so the buttons don't linger for a booking
        # that can no longer be cancelled. 
        pending.delete()
        raise
    
    pending.delete()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **seat_display,
        "status": booking.status,  # "CANCELLED"
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
    }


def dismiss_cancellation(user):
    """Back out of a staged cancellation — the booking is untouched."""

    pending = PendingBookingCancellation.for_user(user)

    if not pending:
        raise ValueError("No pending cancellation found")

    booking = pending.booking
    seat_display = booking.seat_display()
    pending.delete()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **seat_display,
        # "KEPT" is a synthetic status for the frontend's benefit only — it
        # is never written to Booking.status (whose real STATUS_CHOICES
        # don't include it). The booking itself is untouched; this just
        # tells the UI "the cancel-or-keep decision resolved to keep".
        "status": "KEPT",
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
    }