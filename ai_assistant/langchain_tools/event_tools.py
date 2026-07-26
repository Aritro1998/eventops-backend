"""
Tools the assistant can call to browse events and check seat
availability. Each function below is exposed to a chat model as
something it's allowed to call, with the argument schema built
automatically from the function's own type hints and docstring.

A few parameters are marked as values the model is never allowed to
supply itself - conversation state and identifiers this file fills in
before the tool runs, kept separate from whatever the model provides.
"""

import logging
from typing import Optional, Annotated

from langchain_core.tools import tool, InjectedToolArg

from events.services import EventService
from ai_assistant.chat_state import ChatState
from events.caching import get_events_list_cached, get_event_detail_cached
from events.serializers import EventSummarySerializer


logger = logging.getLogger(__name__)


@tool
def get_all_events(
    date_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    ordering: Optional[str] = None,
) -> list[dict]:
    """
    Get all upcoming events. Optionally filter events by date.

    Do not pass date_filter, start_date, or end_date unless the user's
    request actually names a date or date range (e.g. "today", "this
    weekend", "next month", "on June 8th"). A generic request like "show
    me events", "what's happening", or "list events" must call this tool
    with no date arguments at all, so every upcoming event is returned —
    never narrow it to today just because today's date is known.

    Args:
        date_filter: Date in YYYY-MM-DD format, only when the user asked
            about one specific day (e.g. "today", "on 2026-06-08").
        start_date: Start date in YYYY-MM-DD format, only when the user
            gave or implied a date range.
        end_date: End date in YYYY-MM-DD format, only when the user gave
            or implied a date range.
        ordering: Sort order. Use price for cheapest events. Use -price
            for most expensive events. Use start_time for earliest
            events. Use -start_time for latest events.
    """
    logger.info(
        "ai_tool_get_all_events",
        extra={
            "event": "ai_tool_get_all_events",
            "date_filter": date_filter,
            "start_date": start_date,
            "end_date": end_date,
            "ordering": ordering,
        }
    )

    # Read the event list from cache, applying whichever filters and
    # ordering were given.
    return get_events_list_cached(
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        ordering=ordering
    )


@tool
def search_events(
    event_name: str,
    conversation_id: Annotated[str, InjectedToolArg],
    chat_state: Annotated[dict, InjectedToolArg],
) -> list[dict]:
    """
    MANDATORY TOOL. Use this tool FIRST whenever a user mentions an
    event by name. This tool converts event names into event ids. All
    other event tools require a valid event id. Never derive event ids
    from conversation history, list positions, memory, assumptions,
    previous tool outputs, or event ordering. Always call this tool
    before get_event_detail, get_available_seats, or prepare_booking
    when the user provides an event name.

    Args:
        event_name: The event name provided by the user.
    """
    logger.info(
        "ai_tool_search_events",
        extra={"event": "ai_tool_search_events", "event_name": event_name}
    )

    # Find events whose name closely matches what the user typed.
    events = EventService.search_events_by_name(event_name)

    # Remember every event id this search has legitimately turned up,
    # so a later tool call can check an id was actually looked up here
    # instead of guessed.
    searched_ids = set(chat_state.get("searched_event_ids", []))
    searched_ids.update(event.id for event in events)
    chat_state["searched_event_ids"] = list(searched_ids)
    ChatState.save(conversation_id, chat_state)

    return EventSummarySerializer(events, many=True).data


@tool
def get_event_detail(
    event_id: int,
    chat_state: Annotated[dict, InjectedToolArg],
) -> dict:
    """
    Get detailed information for a specific event.

    Args:
        event_id: The event id.
    """
    logger.info(
        "ai_tool_get_event_detail",
        extra={"event": "ai_tool_get_event_detail", "event_id": event_id}
    )

    # Refuse to look up an id that was never actually returned by a
    # search earlier in this conversation.
    if event_id not in chat_state.get("searched_event_ids", []):
        raise ValueError("Use search_events to look up this event before getting its details.")

    return get_event_detail_cached(event_id)


@tool
def get_available_seats(
    event_id: int,
    conversation_id: Annotated[str, InjectedToolArg],
    chat_state: Annotated[dict, InjectedToolArg],
) -> dict:
    """
    Check seat availability for an event. Never returns individual seat
    labels — only whether it's general admission and how many seats are
    open. The user sees the real, live seat grid in a separate panel next
    to this chat; this tool exists so you know an event has open seats
    before proceeding, not so you can list, describe, or count them out
    to the user yourself. When the user names specific seat labels (e.g.
    "A1, B4"), pass them straight through to prepare_booking as they were
    typed — it validates each one against the real seat map itself.

    Args:
        event_id: Event id.
    """
    logger.info(
        "ai_tool_get_available_seats",
        extra={"event": "ai_tool_get_available_seats", "event_id": event_id}
    )

    if event_id not in chat_state.get("searched_event_ids", []):
        raise ValueError("Use search_events to look up this event before checking its seats.")

    event = EventService.get_event_detail(event_id)

    # Remember which event the conversation is currently about, so a
    # later booking step knows what to book without the model having
    # to repeat the id correctly.
    chat_state['selected_event_id'] = event.id
    ChatState.save(conversation_id, chat_state)

    # General admission events only ever show a ticket count - there
    # are no individual seats to list.
    if event.is_general_admission:
        available_count = EventService.get_available_seats(event_id).count()
        return {
            "event_id": event.id,
            "event_name": event.name,
            "general_admission": True,
            "available_count": available_count,
        }

    # Labeled events: report only how many seats are open, never their
    # individual labels - the live seat map (a separate WebSocket-driven
    # panel, not this tool result) is what shows the user actual labels.
    available_count = EventService.get_available_seats(event_id).count()

    return {
        "event_id": event.id,
        "event_name": event.name,
        "general_admission": False,
        "available_count": available_count,
    }
