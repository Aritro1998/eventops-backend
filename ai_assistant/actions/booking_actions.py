from events.models import Event, Seat
from payments.services import PaymentService
from bookings.services import BookingService
from ai_assistant.models import PendingBooking
from ai_assistant.actions.payment_actions import stage_payment_retry


def get_pending_booking_actions(user):
    """Return UI actions only when the authenticated user has a live draft."""

    pending = PendingBooking.for_user(user)

    if not pending:
        return []

    if pending.is_expired:
        pending.delete()
        return []

    return [
        {"type": "confirm_pending_booking", "label": "Confirm booking"},
        {"type": "cancel_pending_booking", "label": "Cancel Draft"},
    ]


def get_pending_booking_draft(user):
    """Return the current draft's details for rendering, or None."""

    pending = PendingBooking.for_user(user)

    if not pending or pending.is_expired:
        return None

    return {
        "event_name": pending.event.name,
        "seat_numbers": pending.seat_numbers,
        "amount": str(pending.amount),
        "event_start_time": pending.event.start_time.isoformat(),
    }


def confirm_pending_booking(user):
    """Turn the user's draft into a real booking using the existing service."""

    pending = PendingBooking.for_user(user)

    if not pending:
        raise ValueError("No pending booking found")

    if pending.is_expired:
        pending.delete()
        raise ValueError("Pending booking has expired. Please choose seats again.")

    event = Event.objects.get(id=pending.event_id)
    seat_numbers = pending.seat_numbers
    seat_ids = list(
        Seat.objects.filter(
            event=event,
            seat_number__in=seat_numbers
        ).values_list('id', flat=True)
    )

    # Do not silently drop a seat if a stale draft references invalid data.
    if len(seat_ids) != len(set(seat_numbers)):
        raise ValueError("The pending booking contains invalid seats. Please choose seats again.")

    booking, is_existing = BookingService.create_booking_for_user(
        user=user,
        event=event,
        seat_ids=seat_ids,
        # Stable per pending-booking row, not random, so two racing confirm
        # clicks against the same draft share one key instead of each minting
        # its own — letting the idempotency lookup actually catch the second one.
        idempotency_key=f"ai-confirm-{pending.id}"
    )

    pending.delete()
    
    if booking.status == "FAILED" and booking.retry_count < PaymentService.MAX_RETRIES:
        stage_payment_retry(booking)

    return {
        "booking_id": booking.id,
        "event_name": event.name,
        "seat_numbers": seat_numbers,
        "status": booking.status,
        "amount": str(booking.amount),
        "is_existing": is_existing,
        "event_start_time": event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }


def cancel_pending_booking_draft(user):
    """Discard only the authenticated user's unconfirmed booking draft."""

    pending = PendingBooking.for_user(user)

    if not pending:
        raise ValueError("No pending booking found")

    event_name = pending.event.name
    seat_numbers = pending.seat_numbers
    pending.delete()

    return {
        "event_name": event_name,
        "seat_numbers": seat_numbers,
        "status": "cancelled",
    }