from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

from ai_assistant.langgraph_flows.checkpointer import get_checkpointer


class BookingGraphState(TypedDict):
    conversation_id: str
    draft_id: str
    user_id: int
    event_id: int
    seat_numbers: list[int]
    amount: str
    decision: Optional[str]
    booking_id: Optional[int]
    result: Optional[dict]


def prepare_booking_node(state: BookingGraphState) -> dict:
    # Pauses here. Whoever is running the graph gets this payload back
    # immediately - nothing below this line runs until a resume arrives
    decision = interrupt(
        {
            "event_id": state["event_id"],
            "seat_numbers": state["seat_numbers"],
            "amount": state["amount"],
        }
    )
    return {"decision": decision}


def confirm_node(state: BookingGraphState) -> dict:
    from users.models import User
    from events.models import Event, Seat
    from bookings.services import BookingService

    user = User.objects.get(id=state["user_id"])
    event = Event.objects.get(id=state["event_id"])

    # BookingService needs real Seat primary keys, not seat_number - the
    # same translation the vanilla confirm_pending_booking action does.
    seat_ids = list(
        Seat.objects.filter(
            event=event,
            seat_number__in=state["seat_numbers"],
        ).values_list("id", flat=True)
    )
    if len(seat_ids) != len(set(state["seat_numbers"])):
        raise ValueError("The pending booking contains invalid seats. Please choose seats again.")

    booking, _ = BookingService.create_booking_for_user(
        user=user,
        event=event,
        seat_ids=seat_ids,
        # Keyed by draft_id, not conversation_id/thread_id: the same
        # conversation can prepare and confirm several unrelated bookings
        # back to back (thread_id is stable for the whole chat session,
        # not per-booking), and a stale key would make the second confirm
        # silently return the first booking's idempotency hit instead of
        # creating a new one. draft_id is minted fresh by prepare_booking
        # every time it (re)pauses this graph, so it stays stable only
        # across repeat/duplicate confirm clicks on the *same* draft.
        idempotency_key=f"ai-confirm-{state['draft_id']}",
    )

    # Whether this comes back CONFIRMED or FAILED, this graph's job ends
    # here - a FAILED payment's retry loop is a separate graph
    # (payment_retry_graph), keyed by booking_id instead of
    # conversation_id, since retrying can legitimately happen from a
    # brand new conversation with no memory of this one.
    return {"booking_id": booking.id, "result": {"booking_id": booking.id, "status": booking.status}}


def cancel_node(state: BookingGraphState) -> dict:
    return {"result": {"status": "cancelled"}}


def route_after_interrupt(state: BookingGraphState) -> str:
    return "confirm" if state["decision"] == "confirm" else "cancel"


# Register the nodes in a StateGraph, which is the main entry point for the booking flow.
builder = StateGraph(BookingGraphState)
builder.add_node("prepare_booking", prepare_booking_node)
builder.add_node("confirm", confirm_node)
builder.add_node("cancel", cancel_node)

# Add edges between the nodes to define the flow of the booking process.
# The edges are conditional, based on the state of the booking process and user decisions.
builder.set_entry_point("prepare_booking")
builder.add_conditional_edges(
    "prepare_booking",
    route_after_interrupt,
    {"confirm": "confirm", "cancel": "cancel"},
)
builder.add_edge("confirm", END)
builder.add_edge("cancel", END)

_compiled_graph = None


def get_booking_graph():
    """
    Lazily compiles the graph with a checkpointer on first real use - see
    checkpointer.get_checkpointer's docstring for why this can't happen
    at module import time. Call this instead of building/importing a
    module-level graph object directly.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = builder.compile(checkpointer=get_checkpointer())
    return _compiled_graph
