import json
import logging

from asgiref.sync import sync_to_async
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, ToolMessage

from ai_assistant.chat_state import ChatState
from ai_assistant.services import get_system_prompt
from ai_assistant.langchain_tools.memory import build_message_list
from core.settings.base import OPENAI_API_KEY, OPENAI_CHAT_MODEL

from ai_assistant.langchain_tools.event_tools import (
    get_all_events, search_events, get_event_detail, get_available_seats,
)
from ai_assistant.langchain_tools.booking_tools import (
    get_my_bookings, prepare_booking, prepare_payment_retry, prepare_cancel_booking,
)
from ai_assistant.langchain_tools.knowledge_tools import search_knowledge_base


logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5

LANGCHAIN_TOOL_REGISTRY = {
    "get_all_events": {"tool": get_all_events},
    "search_events": {"tool": search_events, "requires_chat_state": True, "requires_conversation_id": True},
    "get_event_detail": {"tool": get_event_detail, "requires_chat_state": True},
    "get_available_seats": {"tool": get_available_seats, "requires_chat_state": True, "requires_conversation_id": True},
    "get_my_bookings": {"tool": get_my_bookings, "requires_auth": True},
    "prepare_booking": {"tool": prepare_booking, "requires_auth": True, "requires_chat_state": True},
    "prepare_payment_retry": {"tool": prepare_payment_retry, "requires_auth": True},
    "prepare_cancel_booking": {"tool": prepare_cancel_booking, "requires_auth": True},
    "search_knowledge_base": {"tool": search_knowledge_base, "requires_chat_state": True}
}


async def _run_tool(tool_name, args, user, conversation_id, chat_state):
    tool_config = LANGCHAIN_TOOL_REGISTRY.get(tool_name)
    if not tool_config:
        return {"error": "Tool not found"}

    require_auth = tool_config.get("requires_auth", False)
    if require_auth and not user.is_authenticated:
        return {"error": "Authentication required"}
    
    tool_input = dict(args)
    if require_auth:
        tool_input["user"] = user
    if tool_config.get("requires_chat_state"):
        tool_input["chat_state"] = chat_state
    if tool_config.get("requires_conversation_id"):
        tool_input["conversation_id"] = conversation_id
        
    try:
        return await sync_to_async(tool_config["tool"].invoke)(tool_input)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("ai_tool_call_failed", extra={"event": "ai_tool_call_failed", "tool_name": tool_name})
        return {"error": str(e)}
    

async def run_turn(user_prompt, user=None, conversation_id=None, chat_state=None):
    system_prompt = await get_system_prompt(user=user, request=None, chat_state=chat_state)
    messages = build_message_list(system_prompt, chat_state["history"], user_prompt)
    
    llm = ChatOpenAI(model=OPENAI_CHAT_MODEL, api_key=OPENAI_API_KEY, temperature=0)
    all_tools = [cfg["tool"] for cfg in LANGCHAIN_TOOL_REGISTRY.values()]
    llm_with_tools = llm.bind_tools(all_tools)
    
    for _ in range(MAX_TOOL_CALLS):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        
        if not response.tool_calls:
            return response.content
        
        for call in response.tool_calls:
            result = await _run_tool(call["name"], call["args"], user, conversation_id, chat_state)
            messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call["id"]))
            # search_events/get_available_seats persist their chat_state mutation
            # to Redis themselves, but LangChain's Pydantic validation gives the
            # tool function a COPY of chat_state, not this loop's own object —
            # so this loop's copy never sees that mutation unless we reload it.
            if LANGCHAIN_TOOL_REGISTRY.get(call["name"], {}).get("requires_chat_state"):
                _, chat_state = await sync_to_async(ChatState.get)(conversation_id, user)
            
    return "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I was unable to complete that request."
        
    