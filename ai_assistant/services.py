"""
The AI assistant's core: builds the system prompt (get_system_prompt) and
runs the OpenAI tool-calling loop (AIAssistantService). This is where the
model's tools (ai_assistant/tools/) actually get invoked, and where the
"propose vs execute" safety rules from ai_assistant/models.py get
reinforced in plain English so the model doesn't claim an action happened
that it didn't actually call a tool for.

MAX_TOOL_CALLS bounds one turn's tool-calling loop — if the model somehow
never settles on a plain-text answer within that many rounds (a runaway
loop, or genuine confusion), the loop gives up with a visible error
instead of hanging or looping forever.
"""

import json
import logging

from openai import OpenAI
from events.models import Event
from django.utils import timezone
from core.settings.base import OPENAI_API_KEY
from ai_assistant.chat_state import ChatState
from ai_assistant.models import PendingBooking
from events.services import EventService
from ai_assistant.tools.registry import TOOL_REGISTRY
from ai_assistant.actions.booking_actions import get_pending_booking_draft
from ai_assistant.actions.payment_actions import get_pending_payment_retry_draft
from ai_assistant.actions.cancellation_actions import get_pending_cancellation_draft

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5


def get_system_prompt(user=None, request=None, chat_state=None):
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
        pending_booking = PendingBooking.objects.filter(user=user).first()
        if pending_booking and pending_booking.is_expired:
            pending_booking.delete()  # Delete the expired pending booking
            pending_booking = None
    else:
        pending_booking = None

    if pending_booking:
        seat_display = EventService.describe_seats(pending_booking.event, pending_booking.seat_numbers)
        seats_text = (
            f"{seat_display['seat_count']} ticket(s), general admission (no specific seats)"
            if seat_display["is_general_admission"]
            else ", ".join(seat_display["seat_labels"])
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
            - Event ID: {pending_booking.event.id}
            - Event Name: {pending_booking.event.name}
            - Seats: {seats_text}
            - Amount: {pending_booking.amount}
        """
    else:
        pending_booking_info = ""

    selected_event_info = ""

    selected_event_id = (chat_state or {}).get("selected_event_id")
    if selected_event_id:
        selected_event = Event.objects.filter(id=selected_event_id).first()
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
        described in plain sentences.

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

        If multiple events with similar names are found, ask the user to clarify which event they mean before proceeding.

        MANDATORY TOOL: When a user refers to an event by name, use the search_events tool to locate the event id every time.
        Do not infer event ids from event listings, list positions, previous messages, or memory.

        Users do not know event ids or seat ids.

        When a booking request is made:
        1. Use search_events if needed.
        2. Use get_available_seats.
        If the result has general_admission: true, do NOT display a seat grid
        and do NOT ask the user to pick seats — tell them how many tickets
        are available and ask how many they want.
        Otherwise, don't print the seats as a list — use a grid format
        (10 columns and N rows) showing each seat's label field (e.g. "A1"),
        never its seat_number. seat_number is an internal id — never show it
        or ask the user for it.
        Always call get_available_seats again before telling the user which
        seats are available or accepting seat labels/quantity for a new booking,
        even if you already showed this earlier in this conversation.
        Availability can change between messages — never reuse a previous
        seats list or count from memory.
        3. For labeled events, ask which seats they want and pass seat_labels
        to prepare_booking — the exact label strings from get_available_seats
        (e.g. ["A1", "B4"]), never a seat_number, and never a label you have
        not actually seen returned by get_available_seats in this conversation.
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

class AIAssistantService:
    """
    Wraps the OpenAI client and runs the tool-calling conversation loop.
    chat() and chat_stream() implement the same loop twice — once
    buffered (returns one finished response) and once streamed (yields
    pieces as OpenAI produces them) — because OpenAI's streaming and
    non-streaming responses have a genuinely different shape to parse
    (chat_stream has to reassemble tool-call fragments that arrive across
    many chunks; chat() gets them whole). _run_tool is shared between the
    two so the auth/kwargs/error-handling rules only exist in one place.
    chat_stream is what the real frontend (gradio_app.py) actually calls.
    """

    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        
    def _run_tool(self, tool_name, args, user, request, conversation_id, chat_state):
        """Execute one tool call. Shared by chat() and chat_stream() so the
        auth/kwargs/error-handling rules live in exactly one place.
        """
        tool_config = TOOL_REGISTRY.get(tool_name)

        if not tool_config:
            return {"error": "Tool not found"}

        require_auth = tool_config.get("requires_auth", False)
        if require_auth and not user.is_authenticated:
            return {"error": "Authentication required"}

        kwargs = dict(args)
        if require_auth:
            kwargs["user"] = user
        if tool_config.get("requires_request"):
            kwargs["request"] = request
        if tool_config.get("requires_chat_state"):
            kwargs["conversation_id"] = conversation_id
            kwargs["chat_state"] = chat_state

        tool_function = tool_config["function"]

        try:
            tool_result = tool_function(**kwargs)
        except ValueError as e:
            tool_result = {"error": str(e)}
        except Exception as e:
            logger.exception(
                "ai_tool_call_failed",
                extra={"event": "ai_tool_call_failed", "tool_name": tool_name}
            )
            tool_result = {"error": str(e)}

        return tool_result

    def chat(self, user_prompt, user=None, request=None, conversation_id=None, chat_state=None):
        """
        Non-streaming variant — kept working but not what production
        traffic uses (see class docstring). Returns
        (response_text, draft, cancellation, payment_retry) once the
        model settles on a final plain-text answer, after running
        whatever tool calls it requested along the way.
        """
        # 1. Create the system prompt
        messages = [
            {
                "role": "system",
                "content": get_system_prompt(
                    user=user,
                    request=request,
                    chat_state=chat_state,
                )
            },
            *chat_state["history"],
            {"role": "user", "content": user_prompt},
        ]

        # 2. Create the tools
        tools = [
            tool_config["schema"]
            for tool_config in TOOL_REGISTRY.values()
        ]

        # 3. Make the first OpenAI call
        try:
            response = self.client.chat.completions.create(
                model='gpt-4o-mini',
                messages=messages,
                temperature=0,
                tools=tools,
                parallel_tool_calls=False
            )
        except Exception:
            logger.exception("openai_call_failed")
            raise


        # 4. Make the tool calls
        for _ in range(MAX_TOOL_CALLS):
            message = response.choices[0].message
            # If there are no tool calls, add the turn and return the response
            if not message.tool_calls:
                # Persist only completed visible turns. Tool calls are local to
                # this request; booking selection is separate structured state.
                ChatState.add_turn(chat_state, "user", user_prompt)
                ChatState.add_turn(chat_state, "assistant", message.content)
                ChatState.save(conversation_id, chat_state)

                # Always reflects whatever is actually in the database right
                # now — same rule "actions" already follows — not gated on
                # whether this exact turn's tool call happened to run.
                draft = get_pending_booking_draft(user)
                cancellation = get_pending_cancellation_draft(user)
                payment_retry = get_pending_payment_retry_draft(user)
                return message.content, draft, cancellation, payment_retry

            messages.append(message)
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                tool_result = self._run_tool(
                    tool_name, args, user, request, conversation_id, chat_state
                )

                # Add the tool result to the conversation history
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result)
                    }
                )

            # 5. Make the second OpenAI call
            try:
                response = self.client.chat.completions.create(
                    model='gpt-4o-mini',
                    messages=messages,
                    temperature=0,
                    tools=tools,
                    parallel_tool_calls=False
                )
            except Exception:
                logger.exception("openai_followup_call_failed")
                raise

        return (
            "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I unfortunately I was unable to complete that request. "
            "Please try again later."
        ), None, None, None
        
    def chat_stream(self, user_prompt, user=None, request=None, conversation_id=None, chat_state=None, tool_call_log=None):
        """
        Conversation loop that yields pieces as they arrive instead of returning one finished answer.
        """
        messages = [
            {
                "role": "system",
                "content": get_system_prompt(
                    user=user, 
                    request=request,
                    chat_state=chat_state,
                ),
            },
            *chat_state["history"],
            {"role": "user", "content": user_prompt},
        ]
        
        tools = [tool_config["schema"] for tool_config in TOOL_REGISTRY.values()]

        full_text = ""
        
        for _ in range(MAX_TOOL_CALLS):
            try:
                stream = self.client.chat.completions.create(
                    model='gpt-4o-mini',
                    messages=messages,
                    temperature=0,
                    tools=tools,
                    parallel_tool_calls=False,
                    stream=True
                )
            except Exception:
                logger.exception("openai_call_failed")
                raise
            
            content_buffer = ""
            # Tool-call fragments arrive by index (0, 1, ...) across many
            # chunks — id/name/arguments each get built up piece by piece.
            tool_calls_by_index = {}
            
            for chunk in stream:
                delta = chunk.choices[0].delta
                
                if delta.content:
                    content_buffer += delta.content
                    full_text += delta.content
                    # This is the actual "streaming" moment — hand this
                    # fragment to the view immediately, don't wait.
                    yield {"type": "chunk", "content": delta.content}
                    
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        entry = tool_calls_by_index.setdefault(
                            tc_delta.index,
                            {"id": None, "name": "", "arguments": ""}
                        )
                        
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            entry["name"] += tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments
            
            if not tool_calls_by_index:
                # Plain-text answer — every piece was already streamed above.
                # This is just the wrap-up: save history, report what to show.
                ChatState.add_turn(chat_state, "user", user_prompt)
                ChatState.add_turn(chat_state, "assistant", full_text)
                ChatState.save(conversation_id, chat_state)

                # Always reflects whatever is actually in the database right
                # now — same rule "actions" already follows — not gated on
                # whether this exact turn's tool call happened to run.
                yield {
                    "type": "done",
                    "draft": get_pending_booking_draft(user),
                    "cancellation": get_pending_cancellation_draft(user),
                    "payment_retry": get_pending_payment_retry_draft(user),
                }
                return
            
            # Otherwise, one or more tools were requested. Rebuild the
            # assistant message OpenAI expects to see next, then run the tools
            messages.append(
                {
                    "role": "assistant",
                    "content": content_buffer or None,
                    "tool_calls": [
                        {
                            "id": entry["id"],
                            "type": "function",
                            "function": {
                                "name": entry["name"],
                                "arguments": entry["arguments"]
                            }
                        }
                        for entry in tool_calls_by_index.values()
                    ]
                }
            )
            
            for entry in tool_calls_by_index.values():
                args = json.loads(entry["arguments"])
                tool_result = self._run_tool(
                    entry["name"],
                    args,
                    user,
                    request,
                    conversation_id,
                    chat_state
                )
                
                if tool_call_log is not None:
                    tool_call_log.append({"tool": entry["name"], "args": args})

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": entry["id"],
                        "content": json.dumps(tool_result)
                    }
                )

        # Ran out of tool-call rounds without a plain-text answer.
        fallback_message = (
            "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I unfortunately I was unable to "
            "complete that request. Please try again later."
        )
        yield {"type": "chunk", "content": fallback_message}
        yield {"type": "done", "draft": None, "cancellation": None, "payment_retry": None}