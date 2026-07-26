from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt

from ai_assistant.langgraph_flows.checkpointer import get_checkpointer


class PaymentRetryState(TypedDict):
    booking_id: int
    retry_decision: Optional[str]
    result: Optional[dict]


def await_retry_decision_node(state: PaymentRetryState) -> dict:
    retry_decision = interrupt({"booking_id": state["booking_id"]})
    return {"retry_decision": retry_decision}


def retry_payment_node(state: PaymentRetryState) -> dict:
    from bookings.models import Booking
    from payments.services import PaymentService

    try:
        PaymentService.process_payment(state["booking_id"])
    except ValueError:
        pass

    booking = Booking.objects.get(id=state["booking_id"])
    return {"result": {"booking_id": booking.id, "status": booking.status}}


def give_up_node(state: PaymentRetryState) -> dict:
    from bookings.models import Booking

    booking = Booking.objects.get(id=state["booking_id"])
    return {"result": {"booking_id": booking.id, "status": booking.status}}


def route_after_retry_decision(state: PaymentRetryState) -> str:
    return "retry_payment" if state["retry_decision"] == "retry" else "give_up"


def route_after_payment_attempt(state: PaymentRetryState) -> str:
    return "await_retry_decision" if state["result"]["status"] == "FAILED" else "end"


builder = StateGraph(PaymentRetryState)
builder.add_node("await_retry_decision", await_retry_decision_node)
builder.add_node("retry_payment", retry_payment_node)
builder.add_node("give_up", give_up_node)

builder.set_entry_point("await_retry_decision")
builder.add_conditional_edges(
    "await_retry_decision",
    route_after_retry_decision,
    {"retry_payment": "retry_payment", "give_up": "give_up"}
)
builder.add_conditional_edges(
    "retry_payment",
    route_after_payment_attempt,
    {"await_retry_decision": "await_retry_decision", "end": END}
)
builder.add_edge("give_up", END)

_compiled_graph = None


def get_payment_retry_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = builder.compile(checkpointer=get_checkpointer())
    return _compiled_graph