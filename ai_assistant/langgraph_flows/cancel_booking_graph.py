from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

from ai_assistant.langgraph_flows.checkpointer import get_checkpointer


class CancelBookingGraphState(TypedDict):
    booking_id: int
    decision: Optional[str]
    result: Optional[dict]
    

def await_cancel_decision_node(state: CancelBookingGraphState) -> dict:
    # First pause - waiting for a Confirm Cancellation / Keep Booking click.
    decision = interrupt({"booking_id": state["booking_id"]})
    return {"decision": decision}


def await_double_confirm_node(state: CancelBookingGraphState) -> dict:
    # Second pause - the exact same two buttons, resumed with the same
    # "confirm"/"keep" values, but this time "confirm" actually cancels.
    decision = interrupt({"booking_id": state["booking_id"]})
    return {"decision": decision}


def cancel_node(state: CancelBookingGraphState) -> dict:
    from bookings.models import Booking
    from bookings.services import BookingService
    
    booking = Booking.objects.get(id=state["booking_id"])
    # Raises ValueError if the booking is no longer CONFIRMED (e.g. it
    # expired or was already cancelled through another path since staging)
    # - cancellation_actions.py's confirm_cancellation catches this.
    booking = BookingService.cancel_booking(booking)
    
    return {"result": {"booking_id": booking.id, "status": booking.status}}


def keep_node(state: CancelBookingGraphState) -> dict:
    from bookings.models import Booking
    
    booking = Booking.objects.get(id=state["booking_id"])
    return {"result": {"booking_id": booking.id, "status": "KEPT"}}


def route_after_decision(state: CancelBookingGraphState) -> str:
    return "await_double_confirm" if state["decision"] == "confirm" else "keep"


def route_after_double_confirm(state: CancelBookingGraphState) -> str:
    return "cancel" if state["decision"] == "confirm" else "keep"


builder = StateGraph(CancelBookingGraphState)
builder.add_node("await_cancel_decision", await_cancel_decision_node)
builder.add_node("await_double_confirm", await_double_confirm_node)
builder.add_node("cancel", cancel_node)
builder.add_node("keep", keep_node)

builder.set_entry_point("await_cancel_decision")
builder.add_conditional_edges(
    "await_cancel_decision",
    route_after_decision,
    {"await_double_confirm": "await_double_confirm", "keep": "keep"},
)
builder.add_conditional_edges(
    "await_double_confirm",
    route_after_double_confirm,
    {"cancel": "cancel", "keep": "keep"},
)
builder.add_edge("cancel", END)
builder.add_edge("keep", END)


_compiled_graph = None


def get_cancel_booking_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = builder.compile(checkpointer=get_checkpointer())
    return _compiled_graph