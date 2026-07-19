import logging

from events.models import Event
from knowledge.services import KnowledgeService

logger = logging.getLogger(__name__)


def search_knowledge_base(query, conversation_id, chat_state):
    """
    Search venue/event knowledge documents by meaning, scoped to whatever
    event is currently selected (if any) — same selected_event_id the
    booking tools already rely on, so this never needs the model to
    supply a venue/event id itself.
    """
    logger.info(
        "ai_tool_search_knowledge_base",
        extra={"event": "ai_tool_search_knowledge_base", "query": query}
    )
    
    event_id = chat_state.get('selected_event_id')
    venue = None
    event = None
    
    if event_id is not None:
        event = Event.objects.filter(id=event_id).select_related("venue").first()
        if event:
            venue = event.venue
            
    chunks = KnowledgeService.search(query, venue=venue, event=event)
    
    return {
        "results": [
            {
                "source": chunk.document.title,
                "content": chunk.content
            } for chunk in chunks
        ]
    }
    