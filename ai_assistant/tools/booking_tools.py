"""
Every prepare_* tool here follows the same shape: validate, then stage a
Pending* row (never touch Booking/BookingSeat directly) — see
ai_assistant/models.py's module docstring for why. Confirming a draft
happens only through the dedicated action views in ai_assistant/views.py.
"""

import logging

from events.models import Event, Seat
from events.services import EventService
from bookings.services import BookingService
from bookings.serializers import BookingReadSerializer
from ai_assistant.actions.payment_actions import stage_payment_retry
from ai_assistant.models import PendingBooking, get_pending_action_expiry, PendingBookingCancellation

logger = logging.getLogger(__name__)


def get_my_bookings(user, status=None):
    """The only read-only tool in this file — everything else here stages
    a pending write."""
    logger.info(
        "ai_tool_get_my_bookings",
        extra={"event": "ai_tool_get_my_bookings", "user_id": user.id, "status": status}
    )
    bookings = BookingService.get_user_bookings(user, status_filter=status)
    serializer = BookingReadSerializer(bookings, many=True)
    return serializer.data


def prepare_booking(user, request, conversation_id, chat_state, seat_labels=None, quantity=None):
    """
    Stage a booking draft for whichever event get_available_seats last
    selected server-side. Branches entirely on event.is_general_admission:
    GA events take `quantity` and auto-pick that many available seats
    (their identities are never shown to the user); labeled events take
    `seat_labels` — the exact label strings get_available_seats showed —
    and resolve them to real seats server-side. The model never computes
    or guesses a label-to-seat_number mapping itself; it only ever echoes
    back a label it was already shown, and the resolution below is what
    actually turns that into a seat identity, failing loudly if the label
    doesn't match anything real. Either way, the result lands in
    PendingBooking.seat_numbers in the same shape, so confirm_pending_booking
    (ai_assistant/actions/booking_actions.py) doesn't need to know or care
    which path was taken.
    """
    # get_available_seats stores this value in Redis. The model never supplies
    # an event ID here, so it cannot switch the booking to a guessed event.
    event_id = chat_state.get("selected_event_id")
    if event_id is None:
        raise ValueError("Choose an event and view its available seats before selecting seats.")

    logger.info(
        "ai_tool_prepare_booking",
        extra={
            "event": "ai_tool_prepare_booking",
            "user_id": user.id,
            "event_id": event_id,
            "seat_labels": seat_labels,
            "quantity": quantity,
        }
    )

    event = Event.objects.select_related('space').get(id=event_id)

    if event.is_general_admission:
        if quantity is None or quantity <= 0:
            raise ValueError("This is a general admission event. Specify a quantity of tickets, not seats.")
        if seat_labels:
            raise ValueError("This is a general admission event; specific seats cannot be chosen. Use quantity instead.")

        # Anonymous seats are picked here, not shown to the user — GA bookings
        # only ever surface a ticket count, never seat identities.
        seat_numbers = list(
            EventService.get_available_seats(event.id).values_list('seat_number', flat=True)[:quantity]
        )
        if len(seat_numbers) < quantity:
            raise ValueError(f"Only {len(seat_numbers)} tickets are available for {event.name}.")
    else:
        if not seat_labels:
            raise ValueError("Choose at least one seat for this event.")
        if quantity:
            raise ValueError("This event uses specific seats, not a quantity. Provide seat_labels instead.")

        # Reject duplicates before calculating the price or creating the draft.
        if len(seat_labels) != len(set(seat_labels)):
            raise ValueError("Choose at least one unique seat.")

        # Resolve labels to seat_numbers server-side — mirrors how
        # search_events resolves an event name into an id. The model
        # never has a legitimate way to compute this mapping itself
        # (row/column arithmetic without knowing the layout), so it must
        # never be asked to; it only echoes back a label it was shown.
        label_to_seat_number = {
            (seat.display_label or str(seat.seat_number)): seat.seat_number
            for seat in Seat.objects.filter(event=event)
        }
        missing_labels = [label for label in seat_labels if label not in label_to_seat_number]
        if missing_labels:
            raise ValueError(f"Seats {missing_labels} do not exist for {event.name}.")

        seat_numbers = [label_to_seat_number[label] for label in seat_labels]

        # This is an early user-facing availability check, not a reservation.
        # BookingService repeats the check under row locks after confirmation,
        # which prevents two users from booking the same seat concurrently.
        available_seat_numbers = set(
            EventService.get_available_seats(event.id)
            .filter(seat_number__in=seat_numbers)
            .values_list('seat_number', flat=True)
        )
        unavailable_seat_numbers = set(seat_numbers) - available_seat_numbers
        if unavailable_seat_numbers:
            seat_number_to_label = {v: k for k, v in label_to_seat_number.items()}
            unavailable_labels = [seat_number_to_label[sn] for sn in unavailable_seat_numbers]
            raise ValueError(f"Seats {unavailable_labels} are no longer available for {event.name}.")

    # One user has one active draft. Choosing a different event or seat set
    # replaces the previous draft until the user explicitly confirms it.
    PendingBooking.objects.update_or_create(
        user=user,
        defaults={
            "event": event,
            "seat_numbers": seat_numbers,
            "amount": event.price * len(seat_numbers),
            "expires_at": get_pending_action_expiry(),
        }
   )

    return {
        "event_id": event_id,
        "event_name": event.name,
        **EventService.describe_seats(event, seat_numbers),
        "status": "awaiting_confirmation",
        "amount": str(
            event.price * len(seat_numbers)
        ),
    }


def prepare_payment_retry(user, request, booking_id):
    """Stage a retry for a FAILED/PENDING booking the user owns. The
    actual payment attempt only happens when the human clicks the
    displayed confirmation control — see stage_payment_retry's own
    docstring in ai_assistant/actions/payment_actions.py."""
    logger.info(
        "ai_tool_prepare_payment_retry",
        extra={"event": "ai_tool_prepare_payment_retry", "user_id": user.id, "booking_id": booking_id}
    )

    # Fetch the booking with the given booking_id and ensure it belongs to the user
    booking = BookingService.get_booking_for_user(booking_id, user)

    if not booking:
        raise ValueError("Booking not found.")

    if booking.status not in ["FAILED", "PENDING"]:
        raise ValueError("Payment cannot be retried for this booking status.")

   # Stage the request — this does NOT attempt payment. The real attempt
    # only happens if the user clicks "Retry Payment Now" (see
    # ConfirmPaymentRetryActionView), never this tool.
    stage_payment_retry(booking)

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "amount": str(booking.amount),
        "status": "awaiting_retry_confirmation",
    }


def prepare_cancel_booking(user, booking_id):
    """Stage a cancellation for a CONFIRMED booking the user owns."""
    logger.info(
        "ai_tool_prepare_cancel_booking",
        extra={"event": "ai_tool_prepare_cancel_booking", "user_id": user.id, "booking_id": booking_id}
    )
    
    booking = BookingService.get_booking_for_user(booking_id, user)

    if not booking:
        raise ValueError("Booking not found.")
    
    if booking.status != "CONFIRMED":
        raise ValueError("Only CONFIRMED bookings can be cancelled.")

    # Stage the request — this does NOT cancel anything. The real cancellation
    # only happens if the user clicks "Confirm Cancellation", which hits a
    # dedicated endpoint (see ConfirmCancellationActionView), never this tool.
    PendingBookingCancellation.objects.update_or_create(
        user=user,
        defaults={"booking": booking, "expires_at": get_pending_action_expiry()},
    )

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "amount": str(booking.amount),
        "status": "awaiting_cancellation_confirmation",
    }
