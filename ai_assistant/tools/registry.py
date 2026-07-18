"""
Dict-based dispatch table: one entry per tool the model can call, mapping
its name (as OpenAI sees it in tool_calls) to the actual Python function
plus what AIAssistantService._run_tool needs to inject before calling it —
requires_auth (adds `user`, and 401s if not logged in), requires_request
(adds `request`), requires_chat_state (adds `conversation_id`/`chat_state`).
A tool's OpenAI-facing schema (ai_assistant/tools/schemas.py) is looked up
from the same entry, so schema and implementation can't drift apart into
two different sources of truth for one tool's shape.
"""

from ai_assistant.tools.event_tools import (
    get_all_events,
    get_available_seats,
    get_event_detail,
    search_events,
)

from ai_assistant.tools.booking_tools import (
    prepare_cancel_booking,
    get_my_bookings,
    prepare_booking,
    prepare_payment_retry,
)

from ai_assistant.tools.schemas import (
    PREPARE_CANCEL_BOOKING_TOOL,
    GET_ALL_EVENTS_TOOL,
    GET_AVAILABLE_SEATS_TOOL,
    GET_EVENT_DETAIL_TOOL,
    GET_MY_BOOKINGS_TOOL,
    SEARCH_EVENTS_TOOL,
    PREPARE_BOOKING_TOOL,
    PREPARE_PAYMENT_RETRY_TOOL,
)

TOOL_REGISTRY = {
    "get_all_events": {
        "requires_auth": False,
        "function": get_all_events,
        "schema": GET_ALL_EVENTS_TOOL,
    },
    "get_event_detail": {
        "requires_auth": False,
        "requires_chat_state": True,
        "function": get_event_detail,
        "schema": GET_EVENT_DETAIL_TOOL,
    },
    "get_my_bookings": {
        "requires_auth": True,
        "function": get_my_bookings,
        "schema": GET_MY_BOOKINGS_TOOL,
    },
    "get_available_seats": {
        "requires_auth": False,
        "requires_request": True,
        "requires_chat_state": True,
        "function": get_available_seats,
        "schema": GET_AVAILABLE_SEATS_TOOL,
    },
    "search_events": {
        "requires_auth": False,
        "requires_chat_state": True,
        "function": search_events,
        "schema": SEARCH_EVENTS_TOOL,
    },
    "prepare_booking": {
        "requires_auth": True,
        "requires_request": True,
        "requires_chat_state": True,
        "function": prepare_booking,
        "schema": PREPARE_BOOKING_TOOL,
    },
    "prepare_payment_retry": {
        "requires_auth": True,
        "function": prepare_payment_retry,
        "schema": PREPARE_PAYMENT_RETRY_TOOL,
    },
    "prepare_cancel_booking": {
        "requires_auth": True,
        "function": prepare_cancel_booking,
        "schema": PREPARE_CANCEL_BOOKING_TOOL,
    },
}