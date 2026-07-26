import json
import logging
import tiktoken

from asgiref.sync import sync_to_async
from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage

from events.models import Event
from ai_assistant.chat_state import ChatState
from ai_assistant.models import UsageLog
from ai_assistant.services import get_system_prompt
from ai_assistant.langchain_tools.memory import build_message_list
from core.settings.base import OPENAI_API_KEY, OPENAI_CHAT_MODEL
from ai_assistant.actions.booking_actions import get_pending_booking_draft
from ai_assistant.actions.payment_actions import get_pending_payment_retry_draft
from ai_assistant.actions.cancellation_actions import get_pending_cancellation_draft

from ai_assistant.langchain_tools.event_tools import (
    get_all_events, search_events, get_event_detail, get_available_seats,
)
from ai_assistant.langchain_tools.booking_tools import (
    get_my_bookings, prepare_booking, prepare_payment_retry, prepare_cancel_booking,
)
from ai_assistant.langchain_tools.knowledge_tools import search_knowledge_base


logger = logging.getLogger(__name__)

# A single conversation turn can bounce between the model and its tools
# at most this many times before giving up and returning a fallback reply.
MAX_TOOL_CALLS = 5

# One entry per tool the model is allowed to call. "tool" is the actual
# callable. The other keys describe values that must be supplied by this
# file, never by the model: a logged-in user, the shared conversation
# state, and the conversation's id.
LANGCHAIN_TOOL_REGISTRY = {
    "get_all_events": {"tool": get_all_events},
    "search_events": {"tool": search_events, "requires_chat_state": True, "requires_conversation_id": True},
    "get_event_detail": {"tool": get_event_detail, "requires_chat_state": True},
    "get_available_seats": {"tool": get_available_seats, "requires_chat_state": True, "requires_conversation_id": True},
    "get_my_bookings": {"tool": get_my_bookings, "requires_auth": True},
    "prepare_booking": {"tool": prepare_booking, "requires_auth": True, "requires_chat_state": True, "requires_conversation_id": True},
    "prepare_payment_retry": {"tool": prepare_payment_retry, "requires_auth": True},
    "prepare_cancel_booking": {"tool": prepare_cancel_booking, "requires_auth": True},
    "search_knowledge_base": {"tool": search_knowledge_base, "requires_chat_state": True},
}


async def _run_tool(tool_name, args, user, conversation_id, chat_state):
    """
    Execute a single tool call requested by the model.

    Looks the tool up by name, checks whether it needs a logged-in user,
    adds whichever server-side values that specific tool needs on top of
    the arguments the model provided, then runs it and turns any failure
    into a plain error dict instead of letting an exception escape.
    """
    tool_config = LANGCHAIN_TOOL_REGISTRY.get(tool_name)
    if not tool_config:
        return {"error": "Tool not found"}

    require_auth = tool_config.get("requires_auth", False)
    if require_auth and not user.is_authenticated:
        return {"error": "Authentication required"}

    # Start from what the model provided, then add whichever server-side
    # values this particular tool declares that it needs.
    tool_input = dict(args)
    if require_auth:
        tool_input["user"] = user
    if tool_config.get("requires_chat_state"):
        tool_input["chat_state"] = chat_state
    if tool_config.get("requires_conversation_id"):
        tool_input["conversation_id"] = conversation_id

    try:
        # The tool runs ordinary Django ORM code, which is synchronous,
        # so this hands it off to a worker thread instead of blocking
        # the event loop.
        return await sync_to_async(tool_config["tool"].invoke)(tool_input)
    except ValueError as e:
        # A tool raises ValueError on purpose for an ordinary rejection
        # (e.g. "seat already booked") - expected, not a bug, so it's
        # just handed back as a normal error result.
        return {"error": str(e)}
    except Exception as e:
        # Anything else is unexpected and worth a full log entry.
        logger.exception("ai_tool_call_failed", extra={"event": "ai_tool_call_failed", "tool_name": tool_name})
        return {"error": str(e)}


async def run_turn(user_prompt, user=None, conversation_id=None, chat_state=None, tool_call_log=None):
    """
    Run one full conversation turn: send the user's message and
    conversation history to the model, execute whatever tools it asks
    for, keep going until it settles on a plain-text answer, and stream
    the pieces of that answer back as they arrive.
    """
    # Build the instructions the model always sees, and measure how many
    # tokens that costs - useful for tracking spend over time.
    system_prompt = await get_system_prompt(user=user, request=None, chat_state=chat_state)
    system_prompt_tokens = len(tiktoken.encoding_for_model(OPENAI_CHAT_MODEL).encode(system_prompt))

    # Turn the stored conversation history plus this new message into
    # the actual list of messages the model is shown.
    messages = build_message_list(system_prompt, chat_state["history"], user_prompt)

    llm = ChatOpenAI(model=OPENAI_CHAT_MODEL, api_key=OPENAI_API_KEY, temperature=0)
    all_tools = [cfg["tool"] for cfg in LANGCHAIN_TOOL_REGISTRY.values()]
    # Keeping the model to one tool call at a time means each round of
    # the loop below only ever has a single tool call to handle.
    llm_with_tools = llm.bind_tools(all_tools, parallel_tool_calls=False)

    full_text = ""

    for _ in range(MAX_TOOL_CALLS):
        full_response = None

        # Stream the model's reply piece by piece, forwarding each piece
        # of text immediately. Every incoming chunk is also merged into
        # full_response, so once streaming finishes full_response holds
        # the complete reply - its full text, and any tool calls the
        # model decided to make.
        async for chunk in llm_with_tools.astream(messages):
            if chunk.content:
                full_text += chunk.content
                yield {"type": "chunk", "content": chunk.content}
            full_response = chunk if full_response is None else full_response + chunk

        # Add the model's turn to the running conversation so the next
        # round, if there is one, has the full picture.
        messages.append(full_response)

        # Record how many tokens this round of the conversation cost.
        usage = full_response.usage_metadata
        tool_called = full_response.tool_calls[0]["name"] if full_response.tool_calls else None
        if usage is not None:
            await UsageLog.objects.acreate(
                conversation_id=conversation_id,
                user=user if user and user.is_authenticated else None,
                model=OPENAI_CHAT_MODEL,
                prompt_tokens=usage["input_tokens"],
                completion_tokens=usage["output_tokens"],
                total_tokens=usage["total_tokens"],
                system_prompt_tokens=system_prompt_tokens,
                tool_called=tool_called,
            )

        if not full_response.tool_calls:
            # The model gave a plain-text answer instead of calling a
            # tool - this turn is finished. Store the user's message and
            # the model's reply so they're part of the history for
            # future turns.
            ChatState.add_turn(chat_state, "user", user_prompt)
            ChatState.add_turn(chat_state, "assistant", full_text)
            await sync_to_async(ChatState.save)(conversation_id, chat_state)

            # Look up anything currently awaiting the user's
            # confirmation, so the frontend knows which controls to show.
            if user and user.is_authenticated:
                booking = await get_pending_booking_draft(user)
                cancellation = await get_pending_cancellation_draft(user)
                payment_retry = await get_pending_payment_retry_draft(user)
            else:
                booking = cancellation = payment_retry = None

            # Work out whether the frontend should open a live seat map
            # for whichever event is currently selected. General
            # admission events have no individual seats to pick, so no
            # seat map is needed for those.
            selected_event_id = chat_state.get("selected_event_id")
            show_seat_picker = False
            if selected_event_id:
                selected_event = await (
                    Event.objects
                    .filter(id=selected_event_id)
                    .select_related("space")
                    .afirst()
                )
                show_seat_picker = bool(selected_event) and not selected_event.is_general_admission

            yield {
                "type": "done",
                "draft": booking,
                "cancellation": cancellation,
                "payment_retry": payment_retry,
                "seat_picker_event_id": selected_event_id if show_seat_picker else None,
            }
            return

        # The model asked for a tool - run it, note the call in the
        # tool call log, and feed the result back into the conversation
        # as a tool message so the next round can use it.
        for call in full_response.tool_calls:
            result = await _run_tool(call["name"], call["args"], user, conversation_id, chat_state)

            if tool_call_log is not None:
                tool_call_log.append({"tool": call["name"], "args": call["args"]})

            messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call["id"]))

            # Some tools update the shared conversation state and save
            # it themselves, but the copy this loop is holding never
            # sees that update automatically - reload it from storage so
            # the next tool call in this turn works with the latest state.
            if LANGCHAIN_TOOL_REGISTRY.get(call["name"], {}).get("requires_chat_state"):
                _, chat_state = await sync_to_async(ChatState.get)(conversation_id, user)

    # Ran out of rounds without the model settling on a plain-text answer.
    fallback_message = "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I was unable to complete that request."
    yield {"type": "chunk", "content": fallback_message}
    yield {"type": "done", "draft": None, "cancellation": None, "payment_retry": None}


async def chat_stream(user_prompt, user=None, request=None, conversation_id=None, chat_state=None, tool_call_log=None):
    """
    Entry point with the exact same arguments as the production chat
    function, so callers that don't need request at all can still be
    swapped straight in. request is accepted but never used here.
    """
    async for event in run_turn(
        user_prompt,
        user=user,
        conversation_id=conversation_id,
        chat_state=chat_state,
        tool_call_log=tool_call_log
    ):
        yield event
