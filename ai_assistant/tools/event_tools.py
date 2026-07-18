"""
Read-only AI tools for browsing events/seats. All raise a plain
ValueError (never a custom exception type) for a bad/out-of-order call —
AIAssistantService._run_tool catches ValueError specifically and turns it
into {"error": "..."} in the tool result, which the model sees and can
self-correct from within the same turn (e.g. call search_events first,
then retry), rather than the request failing outright.
"""

import logging

from events.services import EventService
from ai_assistant.chat_state import ChatState
from events.serializers import EventReadSerializer, EventSummarySerializer, SeatSummarySerializer

logger = logging.getLogger(__name__)


def get_all_events(date_filter=None, start_date=None, end_date=None, ordering=None):
    """List/browse events — no guardrail needed here since it doesn't
    take an event id the model could guess."""
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
    events = EventService.get_events_with_available_seats(
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        ordering=ordering
    )
    serializer = EventReadSerializer(events, many=True)
    return serializer.data


def get_event_detail(event_id, conversation_id, chat_state):
    """
    Requires event_id to already be in chat_state's searched_event_ids
    allowlist (populated by search_events below) — otherwise the model
    could hallucinate a plausible-looking id instead of actually
    resolving one. This was a real, intermittently-reproducible bug found
    via eval testing (temperature=0 does not guarantee this on its own),
    not a hypothetical concern.
    """
    logger.info(
        "ai_tool_get_event_detail",
        extra={"event": "ai_tool_get_event_detail", "event_id": event_id}
    )

    if event_id not in chat_state.get("searched_event_ids", []):
        raise ValueError("Use search_events to look up this event before getting its details.")

    event = EventService.get_event_detail(event_id)
    serializer = EventReadSerializer(event)
    return serializer.data


def get_available_seats(request, event_id, conversation_id, chat_state):
    """
    Same searched_event_ids guardrail as get_event_detail. Also the one
    place that sets chat_state["selected_event_id"] — the server-side
    "which event is this booking about" the model can no longer override
    with a hallucinated id once it's set (see prepare_booking, which reads
    this and ignores anything the model might claim about event choice).
    Branches the response shape on is_general_admission: GA events return
    a bare count (there's no meaningful individual seat to list), labeled
    events return the full seat list.
    """
    logger.info(
        "ai_tool_get_available_seats",
        extra={"event": "ai_tool_get_available_seats", "event_id": event_id}
    )
    
    if event_id not in chat_state.get("searched_event_ids", []):
        raise ValueError("Use search_events to look up this event before checking its seats.")
    
    event = EventService.get_event_detail(event_id)

    # Seat selection may happen in a later request, so retain the event in
    # server-side state instead of trusting the model to remember its ID.
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


def search_events(event_name, conversation_id, chat_state):
    """The only tool that populates searched_event_ids — every other
    id-taking tool in this file depends on having been called first."""
    logger.info(
        "ai_tool_search_events",
        extra={"event": "ai_tool_search_events", "event_name": event_name}
    )
    events = EventService.search_events_by_name(event_name)
    
    # Track every id this tool has legitimately resolved this conversation,
    # so get_available_seats can tell "the model looked this up" apart from
    # "the model guessed a number" — both currently arrive as the same
    # plain integer argument otherwise.
    searched_ids = set(chat_state.get("searched_event_ids", []))
    searched_ids.update(event.id for event in events)
    chat_state["searched_event_ids"] = list(searched_ids)
    ChatState.save(conversation_id, chat_state)
    
    serializer = EventSummarySerializer(events, many=True)
    return serializer.data
