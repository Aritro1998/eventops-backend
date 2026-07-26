"""
Execute half of "propose vs execute" for cancellations. Two sequential
LangGraph pauses live behind the same two buttons (Confirm Cancellation /
Keep Booking, see gradio_app.py's render_cancel_prepare_card): the first
click moves cancel_booking_graph from its initial pause to a second "are
you sure" pause instead of finishing outright, and only the second click
on Confirm Cancellation actually cancels. PendingBookingCancellation is a
marker only - like PendingBookingThread for booking_graph, it's what lets
get_pending_cancellation_actions/draft find which booking (if any) has an
active paused cancel_booking_graph thread for this user; the draft
content itself lives in the graph's own checkpoint.
"""

from langgraph.types import Command
from asgiref.sync import sync_to_async

from ai_assistant.models import PendingBookingCancellation
from ai_assistant.langgraph_flows.cancel_booking_graph import get_cancel_booking_graph


def _cancel_thread_config(booking):
    return {"configurable": {"thread_id": f"cancel-{booking.id}"}}


def _cancel_graph_stage(booking):
    """Which node the cancel graph is currently paused at for this
    booking, or None if it's already finished (or never started).
    "await_cancel_decision" = first pause, "await_double_confirm" = the
    "are you sure" pause."""
    graph = get_cancel_booking_graph()
    state = graph.get_state(_cancel_thread_config(booking))
    return state.next[0] if state.next else None


def get_pending_cancellation_actions(user):
    """Return UI actions only when the user has a booking staged for cancellation."""
    
    pending = PendingBookingCancellation.for_user(user)
    
    if not pending:
        return []
    
    if pending.is_expired:
        pending.delete()
        return []
    
    if not _cancel_graph_stage(pending.booking):
        # The graph already finished (cancelled/kept some other way) but
        # the marker wasn't cleaned up - treat it as gone.
        pending.delete()
        return []
    
    return [
        {"type": "confirm_cancel_booking", "label": "Confirm Cancellation"},
        {"type": "keep_booking", "label": "Keep Booking"},
    ]
    

async def get_pending_cancellation_draft(user):
    """Return the staged cancellation's details for rendering, or None."""

    pending = await PendingBookingCancellation.afor_user(user)
    
    if not pending or pending.is_expired:
        return None
    
    booking = pending.booking
    stage = await sync_to_async(_cancel_graph_stage)(booking)
    if not stage:
        await pending.adelete()
        return None
    
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
        "prompt": (
            "Are you sure you want to cancel? This can't be undone."
            if stage == "await_double_confirm" else None
        ),
    }


def confirm_cancellation(user):
    """Advance the cancel graph one step. The first call moves it from
    its initial pause to the "are you sure" pause and returns
    {"resolved": False, "cancellation": {...with a "prompt"...}}. The
    second call is what actually cancels the booking, returning
    {"resolved": True, "booking": {...}}."""
    
    pending = PendingBookingCancellation.for_user(user)
    
    if not pending:
        raise ValueError("No pending cancellation found")
    
    if pending.is_expired:
        pending.delete()
        raise ValueError("This cancellation request has expired. Please try again.")
    
    booking = pending.booking
    
    if not _cancel_graph_stage(booking):
        pending.delete()
        raise ValueError("No pending cancellation found")
    
    seat_display = booking.seat_display()
    event_name = booking.event.name
    
    graph = get_cancel_booking_graph()
    config = _cancel_thread_config(booking)

    try:
        # This raises ValueError if the booking is no longer CONFIRMED (e.g. it
        # expired or was already cancelled through another path since staging) —
        # that's the re-validation this design relies on instead of an expiry field.
        result = graph.invoke(Command(resume="confirm"), config=config)
    except ValueError:
        # Whatever blocked this attempt means there's nothing left to stage —
        # clear the pending row so the buttons don't linger for a booking
        # that can no longer be cancelled. 
        pending.delete()
        raise
    
    if _cancel_graph_stage(booking):
        # Still paused (moved from the initial pause to "are you sure") -
        # leave the marker alone, nothing is resolved yet.
        return {
            "resolved": False,
            "cancellation": {
                "booking_id": booking.id,
                "event_name": event_name,
                **seat_display,
                "amount": str(booking.amount),
                "event_start_time": booking.event.start_time.isoformat(),
                "prompt": "Are you sure you want to cancel? This can't be undone.",
            },
        }
    
    pending.delete()
    booking_result = result["result"]

    return {
        "resolved": True,
        "booking": {
            "booking_id": booking_result["booking_id"],
            "event_name": event_name,
            **seat_display,
            "status": booking_result["status"],  # "CANCELLED"
            "amount": str(booking.amount),
            "event_start_time": booking.event.start_time.isoformat(),
        },
    }


def dismiss_cancellation(user):
    """Back out of a staged cancellation at either pause point — the
    booking is untouched."""

    pending = PendingBookingCancellation.for_user(user)

    if not pending:
        raise ValueError("No pending cancellation found")

    booking = pending.booking
    seat_display = booking.seat_display()
    event_name = booking.event.name
    
    if _cancel_graph_stage(booking):
        graph = get_cancel_booking_graph()
        graph.invoke(Command(resume="keep"), config=_cancel_thread_config(booking))

    pending.delete()

    return {
        "booking_id": booking.id,
        "event_name": event_name,
        **seat_display,
        # "KEPT" is a synthetic status for the frontend's benefit only — it
        # is never written to Booking.status (whose real STATUS_CHOICES
        # don't include it). The booking itself is untouched; this just
        # tells the UI "the cancel-or-keep decision resolved to keep".
        "status": "KEPT",
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
    }