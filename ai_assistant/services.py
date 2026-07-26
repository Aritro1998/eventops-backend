"""
The AI assistant's core: builds the system prompt (get_system_prompt) and
runs the LangChain/LangGraph tool-calling loop (run_turn/chat_stream). This
is where the model's tools (ai_assistant/langchain_tools/) actually get
invoked, and where the "propose vs execute" safety rules from
ai_assistant/models.py get reinforced in plain English so the model doesn't
claim an action happened that it didn't actually call a tool for.

MAX_TOOL_CALLS bounds one turn's tool-calling loop — if the model somehow
never settles on a plain-text answer within that many rounds (a runaway
loop, or genuine confusion), the loop gives up with a visible error
instead of hanging or looping forever.
"""

import json
import logging
import tiktoken

from asgiref.sync import sync_to_async
from django.utils import timezone
from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage

from events.models import Event
from ai_assistant.chat_state import ChatState
from ai_assistant.models import UsageLog
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

MAX_TOOL_CALLS = 5

# One entry per tool the model is allowed to call. "tool" is the actual
# callable. The other keys describe values that must be supplied by this
# file, never by the model: a logged-in user, the shared conversation
# state, and the conversation's id.
TOOL_REGISTRY = {
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


async def get_system_prompt(user=None, request=None, chat_state=None):
    """
    Rebuilt fresh on every call (not cached) since it embeds live,
    per-request state: the current time, whether there's a pending
    booking draft right now, and which event (if any) is currently
    selected in this conversation's server-side chat_state. Baking these
    into the prompt text, rather than making the model track them itself,
    is what makes the guardrails elsewhere (chat_state's
    selected_event_id/searched_event_ids allowlist) actually enforceable —
    the model is told the ground truth every single turn instead of being
    trusted to remember it correctly.
    """
    now = timezone.localtime()

    if user and user.is_authenticated:
        user_info = f"Current user is authenticated. Username: {user.username}, Email: {user.email}."
    else:
        user_info = "Current user is not authenticated."

    if user and user.is_authenticated:
        pending_booking = await get_pending_booking_draft(user)
    else:
        pending_booking = None

    if pending_booking:
        seats_text = (
            f"{pending_booking['seat_count']} ticket(s), general admission (no specific seats)"
            if pending_booking["is_general_admission"]
            else ", ".join(pending_booking["seat_labels"])
        )
        pending_booking_info = f"""
            There is an existing pending booking draft. The interface displays
            Confirm booking and Cancel draft controls for it.

            Do not create or cancel this draft yourself. If the user says
            "yes", "confirm", "proceed", "cancel", or similar, briefly direct
            them to the displayed controls. Never claim the booking was confirmed
            or cancelled unless a backend tool result confirms it.

            If there is a pending booking and the user provides replacement seats:
                call prepare_booking again with new seats.
                you MUST call prepare_booking in the same response.
                Do not say "I will prepare" unless the tool call has already been made.

            If the user wants a different event:
                call search_events to find the new event. Then call get_available_seats
                for that event before calling prepare_booking. get_available_seats updates
                the server-side selected event. Do not call prepare_booking until it succeeds.

            Pending booking details:
            - Event ID: {pending_booking['event_id']}
            - Event Name: {pending_booking['event_name']}
            - Seats: {seats_text}
            - Amount: {pending_booking['amount']}
        """
    else:
        pending_booking_info = ""

    selected_event_info = ""

    selected_event_id = (chat_state or {}).get("selected_event_id")
    if selected_event_id:
        selected_event = await Event.objects.filter(id=selected_event_id).afirst()
        if selected_event:
            selected_event_info = f"""
            - Current selected event: {selected_event.name}
            - Current selected event ID: {selected_event.id}
            prepare_booking uses this server-side event. Pass seat_labels for
            labeled events (exact labels from get_available_seats, e.g.
            ["A1", "B4"]), or quantity for general admission events.
            """

    system_prompt = f"""
        You are an assistant for EventOps platform.
        Answer user queries about events, bookings, and payments in a helpful and concise manner.
        Only provide information that is relevant to the user's query.
        If you don't know the answer, say you don't know instead of making something up.

        When showing more than one item (multiple bookings, multiple events, or
        search results), always format them as a Markdown table — one row per
        item, with columns for the fields relevant to that data (e.g. Event, Seat,
        Amount, Status, Booking ID for bookings). Never use a numbered list with
        bold field labels for multi-item results. A single item can still be
        described in plain sentences. The one exception to this whole rule is
        individual seat labels from get_available_seats — never list, table, or
        enumerate those in any form; see the booking-flow instructions below for
        why. Never include an is_general_admission /
        "General Admission" column in an event table — it's there for you to
        reason with (e.g. deciding whether to ask for seats or a quantity),
        not for the user to see as a listed field.

        When describing any booking, draft, cancellation, or payment retry
        (from prepare_booking, get_my_bookings, or any confirm/dismiss
        result), check its is_general_admission field: if true, describe
        it by seat_count (e.g. "3 tickets"), never by seat number — there
        are no individual seats to name. If false, use seat_labels (e.g.
        "A1, A2"), not raw seat numbers.

        When a user refers to an event by name rather than id, use the available tools to discover the event id.
        Do not ask the user for internal ids if they can be found using tools.
        Always use available tools to retrieve the correct identifier before performing actions or
        answering questions that depend on those identifiers.

        When the user asks about policies, rules, refunds, prohibited items,
        accessibility, parking, or anything venue/event-specific that you don't
        already know from a prior tool result, call search_knowledge_base with
        their question. If the results returned don't actually address the
        question, tell the user you don't have that information — never answer
        a policy question from general knowledge or guesswork.

        If multiple events with similar names are found, ask the user to clarify which event they mean before proceeding.

        MANDATORY TOOL: When a user refers to an event by name, use the search_events tool to locate the event id every time.
        Do not infer event ids from event listings, list positions, previous messages, or memory.

        Users do not know event ids or seat ids.

        MANDATORY, and applies every single time get_available_seats is
        called, for ANY reason - a new booking request, "show me the
        seats", "show the seats again", "list the available seats",
        re-checking availability mid-conversation, or anything else -
        regardless of how the user phrased the request and regardless of
        whether you've already shown this event's seats earlier in this
        conversation:
        If the result has general_admission: true, do NOT display a seat grid
        and do NOT ask the user to pick seats — tell them how many tickets
        are available and ask how many they want.
        Otherwise, your entire reply must be exactly this kind of sentence,
        nothing before or after it: "Seats are available for [event name]
        — check the live seat map next to this chat and let me know which
        ones you'd like (e.g. 'A1, B4')." This holds even if the user
        explicitly asks you to "show", "list", or "print" the seats, or to
        show them "again" - never a list, a table, bullet points, or a
        seat count in that case either. get_available_seats does not give
        you individual seat labels at all for these events (only a count) -
        the user already sees every seat's real-time status in the visual
        map; your only job here is to point them at it. Never show or ask
        for seat_number — it's an internal id.
        Always call get_available_seats again before telling the user which
        seats are available or accepting seat labels/quantity for a new booking,
        even if you already showed this earlier in this conversation.
        Availability can change between messages — never reuse a previous
        seats list or count from memory.

        When a booking request is made:
        1. Use search_events if needed.
        2. Use get_available_seats (see the mandatory reply rule above for
        how to present whatever it returns).
        3. For labeled events, ask which seats they want and pass seat_labels
        to prepare_booking exactly as the user typed them (e.g. ["A1", "B4"]),
        never a seat_number. You are never shown the individual seat labels
        yourself — get_available_seats only reports a count, never a list —
        so relay whatever labels the user gives you straight through;
        prepare_booking validates each one against the real seat map and
        will return an error for anything invalid or no longer free.
        For general admission events, ask how many tickets they want and pass
        quantity to prepare_booking.
        4. Call prepare_booking.
        5. Show the booking summary.
        6. Do not ask the user to type "yes" or another confirmation message.
        Tell them to use the displayed Confirm booking or Cancel draft button below the chat.

        When the user asks to retry payment, complete payment, pay again, or fix a failed payment:
        1. If the booking id is known, call prepare_payment_retry.
        2. If it is not known, call get_my_bookings and ask which booking to retry.
        3. Disambiguation is about distinct booking ids, not seats. A single
           booking can have multiple seats (get_my_bookings returns one entry
           per booking, with a "seats" list nested inside it) — that is one
           match, not several. Only ask the user to pick between bookings
           when get_my_bookings actually returns more than one distinct
           booking id eligible for retry. If there is exactly one eligible
           booking, call prepare_payment_retry for it directly — do not ask
           the user to choose between its individual seats.
        4. If there truly is more than one eligible booking (e.g. two failed
           bookings for the same event), list seat labels and booking id for
           each match and ask the user to pick — never guess which one they mean.
        5. Never claim the payment was retried — only a click on the displayed
           Retry Payment Now control actually attempts it.
        6. Never retry payment for a booking unless it belongs to the current authenticated user.

        When the user asks to cancel a confirmed booking:
        1. If the booking ID is known and the request is clear, call prepare_cancel_booking.
        2. If it is unknown, call get_my_bookings with status CONFIRMED and ask the user which booking to cancel.
        3. Disambiguation is about distinct booking ids, not seats. A single
           booking can have multiple seats (get_my_bookings returns one entry
           per booking, with a "seats" list nested inside it) — that is one
           match, not several. Only ask the user to pick between bookings
           when get_my_bookings actually returns more than one distinct
           booking id. If there is exactly one matching booking, call
           prepare_cancel_booking for it directly — do not ask the user to
           choose between its individual seats.
        4. If there truly is more than one matching booking (e.g. two bookings
           for the same event), list seat labels and booking id for each
           match and ask the user to pick — never guess which one they mean.
        5. Never claim the booking was cancelled — only a click on the displayed
           Confirm Cancellation control actually cancels it.
        6. For an unconfirmed draft, direct the user to the displayed Cancel draft control.

        If a tool result contains an error field:
        - Explain the error clearly to the user.
        - Do not claim the action succeeded.
        - If useful, suggest the next safe action, such as viewing bookings or starting a new booking.

        {selected_event_info}

        {pending_booking_info}

        Never create a booking without explicit confirmation.
        Never guess event ids or seat ids.
        Always retrieve them through tools.

        After any tool call succeeds, speak in the past tense.
        Say what was done using the tool result.
        Never claim an action was completed unless the tool result confirms it.
        Never say a booking, cancellation, or payment retry has been prepared
        or staged unless you have actually called prepare_booking,
        prepare_cancel_booking, or prepare_payment_retry in this exact
        response and its tool result confirms it.

        Never announce that you are about to check something or call a tool
        as your entire response (e.g. "I will now check...", "Checking...",
        "Booking now..."). If you have enough information to act, call the
        tool immediately in this same response instead of describing what
        you are going to do next turn. Only respond with text alone when you
        are actually waiting on the user for missing information (e.g. which
        seats they want), never as a placeholder before an action you could
        already take.

        Current date: {now.date().isoformat()}
        Current time: {now.strftime("%H:%M:%S")}
        Current day: {now.strftime('%A')}
        Timezone: {str(now.tzinfo)}
        Currency is in INR. Always show prices with the currency unit, for example: ₹500.
        {user_info}
    """

    return system_prompt


async def _run_tool(tool_name, args, user, conversation_id, chat_state):
    """
    Execute a single tool call requested by the model.

    Looks the tool up by name, checks whether it needs a logged-in user,
    adds whichever server-side values that specific tool needs on top of
    the arguments the model provided, then runs it and turns any failure
    into a plain error dict instead of letting an exception escape.
    """
    tool_config = TOOL_REGISTRY.get(tool_name)
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
    all_tools = [cfg["tool"] for cfg in TOOL_REGISTRY.values()]
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
            if TOOL_REGISTRY.get(call["name"], {}).get("requires_chat_state"):
                _, chat_state = await sync_to_async(ChatState.get)(conversation_id, user)

    # Ran out of rounds without the model settling on a plain-text answer.
    fallback_message = "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I was unable to complete that request."
    yield {"type": "chunk", "content": fallback_message}
    yield {"type": "done", "draft": None, "cancellation": None, "payment_retry": None}


async def chat_stream(user_prompt, user=None, request=None, conversation_id=None, chat_state=None, tool_call_log=None):
    """
    The sole chat entry point — it's what the real frontend (gradio_app.py)
    actually calls via ChatStreamView, reassembling streamed chunks and
    tool calls into one conversation turn. request is accepted but never
    used here (kept so callers with a request object don't need a special
    case to call this).
    """
    async for event in run_turn(
        user_prompt,
        user=user,
        conversation_id=conversation_id,
        chat_state=chat_state,
        tool_call_log=tool_call_log,
    ):
        yield event
