"""
Tools that let the assistant read a user's bookings and stage changes
to them - a new booking, a payment retry, or a cancellation. None of
these tools make the change immediately: each one only creates a draft
that still needs the user's explicit confirmation through the app's own
confirm/cancel controls before anything actually happens.
"""

import logging
import uuid
from typing import Annotated, Optional

from langchain_core.tools import tool, InjectedToolArg

from users.models import User
from events.models import Event, Seat
from events.services import EventService
from bookings.services import BookingService
from bookings.serializers import BookingReadSerializer
from ai_assistant.langgraph_flows.booking_graph import get_booking_graph
from ai_assistant.langgraph_flows.payment_retry_graph import get_payment_retry_graph
from ai_assistant.langgraph_flows.cancel_booking_graph import get_cancel_booking_graph
from ai_assistant.models import PendingBookingThread, get_pending_action_expiry, PendingBookingCancellation


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

    # Only the caller's own booking can be found here, and only in a
    # state where a retry actually makes sense.
    booking = BookingService.get_booking_for_user(booking_id, user)
    if not booking:
        raise ValueError("Booking not found.")
    if booking.status not in ["FAILED", "PENDING"]:
        raise ValueError("Payment cannot be retried for this booking status.")

    # Pause the retry graph, waiting for a human to click Retry Payment
    # Now / Not Now - no payment is attempted here.
    retry_graph = get_payment_retry_graph()
    retry_graph.invoke(
        {"booking_id": booking.id},
        config={"configurable": {"thread_id": f"booking-{booking.id}"}},
    )

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
    
    # Pause the cancel graph right here, waiting for a human to click
    # Confirm Cancellation or Keep Booking - thread_id=f"cancel-{booking.id}",
    # so a later resume (see ai_assistant/actions/cancellation_actions.py)
    # knows exactly which paused thread to continue.
    graph = get_cancel_booking_graph()
    graph.invoke(
        {"booking_id": booking.id},
        config={"configurable": {"thread_id": f"cancel-{booking.id}"}}
    )

    # Create or replace the pending cancellation - the booking itself
    # stays CONFIRMED until the user actually confirms this.
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
    conversation_id: Annotated[str, InjectedToolArg],
    seat_labels: Optional[list[str]] = None,
    quantity: Optional[int] = None,
) -> dict:
    """
    Prepare or replace a pending booking before final confirmation.
    Call this when the user chooses seats for a new booking, or when an
    existing pending booking exists and the user asks to change the
    seats. For labeled/reserved-seating events, pass seat_labels — exactly
    the label strings the user typed or selected (e.g. ['A1', 'B4']),
    never a seat_number. You are never shown the individual seat labels
    yourself (get_available_seats only reports a count); the user picks
    them from a live seat map next to the chat, and this tool validates
    each one against the real seat map itself, returning an error for any
    label that's invalid or no longer free.
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
    # The event being booked is whatever get_available_seats last looked
    # at - never something the model names directly.
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
        # General admission: reserve however many open seats are
        # needed - their individual identity is never shown to the user.
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
        # Labeled seating: turn the seat labels the user chose back
        # into real seat numbers, and check every one is still free.
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

    # Pause the booking graph right here, waiting for a human to click
    # Confirm or Cancel - thread_id=conversation_id, so a later resume
    # (see ai_assistant/actions/booking_actions.py) knows exactly which
    # paused thread to continue. Calling invoke() again on this same
    # thread (e.g. the user changes their seat selection before
    # confirming) restarts the pause with the new state, replacing
    # whatever was paused there before.
    graph = get_booking_graph()
    graph.invoke(
        {
            "conversation_id": conversation_id,
            # Minted fresh every time a draft is (re)staged, so confirm_node's
            # idempotency key can't collide with an earlier, unrelated
            # booking confirmed earlier in this same conversation - see
            # booking_graph.py's confirm_node for why conversation_id alone
            # isn't safe to use there.
            "draft_id": str(uuid.uuid4()),
            "user_id": user.id,
            "event_id": event_id,
            "seat_numbers": seat_numbers,
            "amount": str(event.price * len(seat_numbers)),
        },
        config={"configurable": {"thread_id": conversation_id}},
    )

    # This marker is the only thing that lets a later request find its
    # way back to the paused thread above - see PendingBookingThread's
    # own docstring for why a plain graph checkpoint isn't enough on
    # its own.
    PendingBookingThread.objects.update_or_create(
        user=user,
        defaults={"conversation_id": conversation_id, "expires_at": get_pending_action_expiry()},
    )

    return {
        "event_id": event_id,
        "event_name": event.name,
        **EventService.describe_seats(event, seat_numbers),
        "status": "awaiting_confirmation",
        "amount": str(event.price * len(seat_numbers)),
    }
