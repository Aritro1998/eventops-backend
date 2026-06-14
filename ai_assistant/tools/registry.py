from ai_assistant.tools.event_tools import (
    get_all_events,
    get_event_detail,
)

from ai_assistant.tools.schemas import (
    GET_ALL_EVENTS_TOOL,
    GET_EVENT_DETAIL_TOOL,
)

TOOL_REGISTRY = {
    "get_all_events": {
        "function": get_all_events,
        "schema": GET_ALL_EVENTS_TOOL,
    },
    "get_event_detail": {
        "function": get_event_detail,
        "schema": GET_EVENT_DETAIL_TOOL,
    },
}