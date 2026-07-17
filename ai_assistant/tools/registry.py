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
    retry_payment,
)

from ai_assistant.tools.schemas import (
    PREPARE_CANCEL_BOOKING_TOOL,
    GET_ALL_EVENTS_TOOL,
    GET_AVAILABLE_SEATS_TOOL,
    GET_EVENT_DETAIL_TOOL,
    GET_MY_BOOKINGS_TOOL,
    SEARCH_EVENTS_TOOL,
    PREPARE_BOOKING_TOOL,
    RETRY_PAYMENT_TOOL,
)

TOOL_REGISTRY = {
    "get_all_events": {
        "requires_auth": False,
        "function": get_all_events,
        "schema": GET_ALL_EVENTS_TOOL,
    },
    "get_event_detail": {
        "requires_auth": False,
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
    "retry_payment": {
        "requires_auth": True,
        "requires_request": True,
        "function": retry_payment,
        "schema": RETRY_PAYMENT_TOOL,
    },
        "prepare_cancel_booking": {
        "requires_auth": True,
        "function": prepare_cancel_booking,
        "schema": PREPARE_CANCEL_BOOKING_TOOL,
    },
}