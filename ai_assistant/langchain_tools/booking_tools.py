import logging
from typing import Annotated, Optional

from langchain_core.tools import tool, InjectedToolArg

from users.models import User
from events.models import Event, Seat
from events.services import EventService
from bookings.services import BookingService
from bookings.serializers import BookingReadSerializer
from ai_assistant.actions.payment_actions import stage_payment_retry
from ai_assistant.models import PendingBooking, get_pending_action_expiry, PendingBookingCancellation


logger = logging.getLogger(__name__)


@tool
def get_my_bookings(
    user: Annotated[User, InjectedToolArg],
    status: Optional[str] = None,
) -> list[dict]:
    """
    Get bookings belonging to the currently authenticated user.
    Optionally filter bookings by status.

    Args:
        status: Filter to a booking status — CONFIRMED, PENDING, FAILED,
            CANCELLED, or EXPIRED. Omit to get all bookings.
    """
    logger.info(
        "ai_tool_get_my_bookings",
        extra={"event": "ai_tool_get_my_bookings", "user_id": user.id, "status": status}
    )
    
    bookings = BookingService.get_user_bookings(user, status_filter=status)
    return BookingReadSerializer(bookings, many=True).data


@tool
def prepare_payment_retry(
    user: Annotated[User, InjectedToolArg],
    booking_id: int,
) -> dict:
    """
    Stage a payment retry for one of the current user's FAILED or
    PENDING bookings. This does NOT attempt the payment — it only
    prepares it for confirmation. After this tool succeeds, tell the
    user to use the displayed Retry Payment Now or Not Now controls.
    Never say the payment was retried unless a backend tool result
    confirms it. If the booking id is not known, call get_my_bookings
    first. If more than one booking could match, list the distinguishing
    details — seat number and booking id — for each match and ask the
    user to pick one. Never guess or default to the most recent match.

    Args:
        booking_id: The user's booking id to stage a payment retry for.
    """
    logger.info(
        "ai_tool_prepare_payment_retry",
        extra={"event": "ai_tool_prepare_payment_retry", "user_id": user.id, "booking_id": booking_id}
    )
    
    booking = BookingService.get_booking_for_user(booking_id, user)
    if not booking:
        raise ValueError("Booking not found.")
    if booking.status not in ["FAILED", "PENDING"]:
        raise ValueError("Payment cannot be retried for this booking status.")

    stage_payment_retry(booking)

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "amount": str(booking.amount),
        "status": "awaiting_retry_confirmation",
    }


@tool
def prepare_cancel_booking(
    user: Annotated[User, InjectedToolArg],
    booking_id: int,
) -> dict:
    """Stage the cancellation of one of the authenticated user's CONFIRMED
    bookings. This does NOT cancel the booking — it only prepares it for
    confirmation. After this tool succeeds, tell the user to use the
    displayed Confirm Cancellation or Keep Booking controls. Never say
    the booking was cancelled unless a backend tool result confirms it.
    If the booking ID is unknown, call get_my_bookings with status
    CONFIRMED first. If more than one booking matches, list the
    distinguishing details — seat number and booking id — for each
    match and ask the user to pick one. Never guess or default to the
    most recent match.

    Args:
        booking_id: The confirmed booking ID to stage for cancellation.
    """
    logger.info(
        "ai_tool_prepare_cancel_booking",
        extra={"event": "ai_tool_prepare_cancel_booking", "user_id": user.id, "booking_id": booking_id}
    )

    booking = BookingService.get_booking_for_user(booking_id, user)
    if not booking:
        raise ValueError("Booking not found.")
    if booking.status != "CONFIRMED":
        raise ValueError("Only CONFIRMED bookings can be cancelled.")

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
    

@tool
def prepare_booking(
    user: Annotated[User, InjectedToolArg],
    chat_state: Annotated[dict, InjectedToolArg],
    seat_labels: Optional[list[str]] = None,
    quantity: Optional[int] = None,
) -> dict:
    """
    Prepare or replace a pending booking before final confirmation.
    Call this when the user chooses seats for a new booking, or when an
    existing pending booking exists and the user asks to change the
    seats. For labeled/reserved-seating events, pass seat_labels — the
    exact label strings shown in get_available_seats's seats list (e.g.
    ['A1', 'B4']), never a seat_number, and never a label you have not
    actually seen returned by get_available_seats in this conversation.
    For general admission events (get_available_seats returned
    general_admission: true), pass quantity instead — never ask a
    general admission user to pick seats. After this tool succeeds,
    summarize the updated pending booking and direct the user to the
    displayed confirmation controls. Do not call this for affirmative
    confirmations like yes, confirm, proceed, or book it.

    Args:
        seat_labels: Seat labels exactly as shown by get_available_seats
            (e.g. ['A1', 'B4']). Only for labeled/reserved-seating events.
        quantity: Number of tickets. Only for general admission events.
    """
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

        if len(seat_labels) != len(set(seat_labels)):
            raise ValueError("Choose at least one unique seat.")

        label_to_seat_number = {
            (seat.display_label or str(seat.seat_number)): seat.seat_number
            for seat in Seat.objects.filter(event=event)
        }
        missing_labels = [label for label in seat_labels if label not in label_to_seat_number]
        if missing_labels:
            raise ValueError(f"Seats {missing_labels} do not exist for {event.name}.")

        seat_numbers = [label_to_seat_number[label] for label in seat_labels]

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
        "amount": str(event.price * len(seat_numbers)),
    }
    