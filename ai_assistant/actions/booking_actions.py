"""
The execute half of "propose vs execute" for bookings — every function
here is called from a dedicated action view (ai_assistant/views.py),
triggered only by a human clicking a real button, never by the AI
directly. get_pending_booking_actions/get_pending_booking_draft are also
read by the chat views themselves, so the frontend always knows whether
to show the Confirm/Cancel controls.
"""

from asgiref.sync import sync_to_async
from langgraph.types import Command

from events.models import Event
from events.services import EventService
from payments.services import PaymentService
from ai_assistant.models import PendingBookingThread
from ai_assistant.langgraph_flows.booking_graph import get_booking_graph
from ai_assistant.langgraph_flows.payment_retry_graph import get_payment_retry_graph


def get_pending_booking_actions(user):
    """Return UI actions only when the authenticated user has a live draft."""

    pending = PendingBookingThread.for_user(user)

    if not pending:
        return []

    if pending.is_expired:
        pending.delete()
        return []

    return [
        {"type": "confirm_pending_booking", "label": "Confirm booking"},
        {"type": "cancel_pending_booking", "label": "Cancel Draft"},
    ]


async def get_pending_booking_draft(user):
    """Return the current draft's details for rendering, or None."""

    pending = await PendingBookingThread.afor_user(user)

    if not pending or pending.is_expired:
        return None

    graph = get_booking_graph()
    config = {"configurable": {"thread_id": pending.conversation_id}}
    state = await sync_to_async(graph.get_state)(config)

    if not state.next:
        # The graph already finished (confirmed/cancelled some other way)
        # but the marker wasn't cleaned up - treat it as gone.
        await pending.adelete()
        return None

    event = await Event.objects.select_related("space").aget(id=state.values["event_id"])
    seat_display = await sync_to_async(EventService.describe_seats)(event, state.values["seat_numbers"])

    return {
        "event_id": event.id,
        "event_name": event.name,
        **seat_display,
        "amount": state.values["amount"],
        "event_start_time": event.start_time.isoformat(),
    }


def confirm_pending_booking(user):
    """Resume the paused booking graph with the user's confirmation,
    turning the draft into a real booking using the existing service."""

    pending = PendingBookingThread.for_user(user)

    if not pending:
        raise ValueError("No pending booking found")

    if pending.is_expired:
        pending.delete()
        raise ValueError("Pending booking has expired. Please choose seats again.")

    graph = get_booking_graph()
    config = {"configurable": {"thread_id": pending.conversation_id}}
    state = graph.get_state(config)
    if not state.next:
        pending.delete()
        raise ValueError("No pending booking found")

    result = graph.invoke(Command(resume="confirm"), config=config)
    booking_result = result.get("result")
    pending.delete()

    if not booking_result:
        raise ValueError("No pending booking found")

    from bookings.models import Booking
    booking = (
        Booking.objects
        .select_related("event")
        .prefetch_related("booking_seats__seat")
        .get(id=booking_result["booking_id"])
    )

    if booking.status == "FAILED" and booking.retry_count < PaymentService.MAX_RETRIES:
        # Pause the retry graph immediately so the Retry Payment Now /
        # Not Now controls are available right away, without the user
        # needing to ask the AI again.
        retry_graph = get_payment_retry_graph()
        retry_graph.invoke(
            {"booking_id": booking.id},
            config={"configurable": {"thread_id": f"booking-{booking.id}"}},
        )

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "status": booking.status,
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }


def cancel_pending_booking_draft(user):
    """Discard only the authenticated user's unconfirmed booking draft."""

    pending = PendingBookingThread.for_user(user)

    if not pending:
        raise ValueError("No pending booking found")

    graph = get_booking_graph()
    config = {"configurable": {"thread_id": pending.conversation_id}}
    state = graph.get_state(config)
    if not state.next:
        pending.delete()
        raise ValueError("No pending booking found")

    event = Event.objects.get(id=state.values["event_id"])
    seat_display = EventService.describe_seats(event, state.values["seat_numbers"])
    event_name = event.name

    graph.invoke(Command(resume="cancel"), config=config)
    pending.delete()

    return {
        "event_name": event_name,
        **seat_display,
        "status": "cancelled",
    }
