import logging
from typing import Optional
from typing import Annotated

from langchain_core.tools import tool, InjectedToolArg

from events.services import EventService
from ai_assistant.chat_state import ChatState
from events.caching import get_events_list_cached, get_event_detail_cached
from events.serializers import EventSummarySerializer, SeatSummarySerializer


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
    
    Args:
        date_filter: Date in YYYY-MM-DD format. For example: 2026-06-08.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
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
    
    events = EventService.search_events_by_name(event_name)
    
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
    Get all available seats for an event.

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
    
    chat_state['selected_event_id'] = event.id
    ChatState.save(conversation_id, chat_state)
    
    if event.is_general_admission:
        available_count = EventService.get_available_seats(event_id).count()
        return {
            "event_id": event.id,
            "event_name": event.name,
            "general_admission": True,
            "available_count": available_count,
        }

    available_seats = EventService.get_available_seats(event_id)
    serializer = SeatSummarySerializer(available_seats, many=True)
    
    return {
        "event_id": event.id,
        "event_name": event.name,
        "general_admission": False,
        "seats": serializer.data,
    }
    
