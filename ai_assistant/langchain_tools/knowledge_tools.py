"""
Tool that lets the assistant answer policy and venue-information
questions by searching stored knowledge documents instead of guessing.
"""

import logging
from typing import Annotated

from langchain_core.tools import tool, InjectedToolArg

from events.models import Event
from ai_assistant.models import UsageLog
from knowledge.services import KnowledgeService


logger = logging.getLogger(__name__)


@tool
def search_knowledge_base(
    query: str,
    chat_state: Annotated[dict, InjectedToolArg],
    conversation_id: Annotated[str, InjectedToolArg],
) -> dict:
    """
    Search venue and event knowledge documents (policies, rules,
    FAQs, venue guides) for information relevant to the user's
    question. Use this whenever asked about refund policy, prohibited
    items, accessibility, parking, house rules, or any other
    venue/event-specific or general policy question. If the returned
    results don't actually answer the question, say you don't have
    information on that rather than guessing — never invent a policy
    that wasn't returned here.

    Args:
        query: The user's question, in their own words.
    """
    logger.info(
        "ai_tool_search_knowledge_base",
        extra={"event": "ai_tool_search_knowledge_base", "query": query}
    )

    # If an event is currently selected in this conversation, narrow the
    # search to that event and its venue. Otherwise search only
    # general, non-venue-specific documents.
    event_id = chat_state.get('selected_event_id')
    venue = None
    event = None

    if event_id is not None:
        event = Event.objects.filter(id=event_id).select_related("venue").first()
        if event:
            venue = event.venue

    chunks, usage = KnowledgeService.search(query, venue=venue, event=event)
    
    # The RAG search itself is a real, separate OpenAI call (embeddings,
    # not chat completions) - logged here rather than in run_turn since
    # that's the only place the usage numbers are actually available.
    UsageLog.objects.create(
        conversation_id=conversation_id,
        model=KnowledgeService.EMBEDDING_MODEL,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=0,
        total_tokens=usage["total_tokens"],
        tool_called="search_knowledge_base",
    )

    return {
        "results": [
            {"source": chunk.document.title, "content": chunk.content}
            for chunk in chunks
        ]
    }
