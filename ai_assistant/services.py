import json

from openai import OpenAI
from django.utils import timezone

from ai_assistant.booking_actions import get_pending_booking_draft
from ai_assistant.chat_state import ChatState
from ai_assistant.models import PendingBooking
from core.settings.base import OPENAI_API_KEY
from events.models import Event
from ai_assistant.tools.registry import TOOL_REGISTRY

MAX_TOOL_CALLS = 5

def get_system_prompt(user=None, request=None, chat_state=None):
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
            - Seats: {pending_booking.seat_numbers}
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
            prepare_booking uses this server-side event and accepts only seat numbers.
            """

    system_prompt = f"""
        You are an assistant for EventOps platform.
        Answer user queries about events, bookings, and payments in a helpful and concise manner.
        Only provide information that is relevant to the user's query. If you don't know the answer, say you don't know instead of making something up.

        When a user refers to an event by name rather than id, use the available tools to discover the event id. Do not ask the user for internal ids if they can be found using tools.
        Always use available tools to retrieve the correct identifier before performing actions or answering questions that depend on those identifiers.

        If multiple events with similar names are found, ask the user to clarify which event they mean before proceeding.

        MANDATORY TOOL: When a user refers to an event by name, use the search_events tool to locate the event id every time.
        Do not infer event ids from event listings, list positions, previous messages, or memory.

        Users do not know event ids or seat ids.

        When a booking request is made:
        1. Use search_events if needed.
        2. Use get_available_seats. Don't print the seats as a list, use a grid format (10 columns and N rows).
        3. Ask the user which seats they want. Pass seat numbers to prepare_booking.
        4. Call prepare_booking.
        5. Show the booking summary.
        6. Do not ask the user to type "yes" or another confirmation message.
        Tell them to use the displayed Confirm booking or Cancel draft button below the chat.

        When the user asks to retry payment, complete payment, pay again, or fix a failed payment:
        1. If the booking id is known from the user's message or conversation history, call retry_payment.
        2. If the booking id is not known, call get_my_bookings to show the user's bookings and ask which booking to retry.
        3. Never retry payment for a booking unless it belongs to the current authenticated user.

        When the user asks to cancel a confirmed booking:
        1. If the booking ID is known and the request is clear, call cancel_booking.
        2. If it is unknown, call get_my_bookings with status CONFIRMED and ask the user which booking to cancel.
        3. For an unconfirmed draft, direct the user to the displayed Cancel draft control.

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

        Current date: {now.date().isoformat()}
        Current time: {now.strftime("%H:%M:%S")}
        Current day: {now.strftime('%A')}
        Timezone: {str(now.tzinfo)}
        Currency is in INR. Always show prices with the currency unit, for example: ₹500.
        {user_info}
    """

    return system_prompt

class AIAssistantService:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)

    def chat(self, user_prompt, user=None, request=None, conversation_id=None, chat_state=None):
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
                temperature=0.3,
                tools=tools,
                parallel_tool_calls=False
            )
        except Exception as e:
            print("OPENAI CALL FAILED:", str(e))
            raise


        # 4. Make the tool calls
        booking_drafted = False
        for _ in range(MAX_TOOL_CALLS):
            message = response.choices[0].message
            # If there are no tool calls, add the turn and return the response
            if not message.tool_calls:
                # Persist only completed visible turns. Tool calls are local to
                # this request; booking selection is separate structured state.
                ChatState.add_turn(chat_state, "user", user_prompt)
                ChatState.add_turn(chat_state, "assistant", message.content)
                ChatState.save(conversation_id, chat_state)

                # Only surface a draft card on the turn that actually created
                # or updated it, not on every later unrelated message.
                draft = get_pending_booking_draft(user) if booking_drafted else None
                return message.content, draft

            messages.append(message)
            # Loop through the tool calls
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                tool_config = TOOL_REGISTRY.get(tool_name)

                if not tool_config:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({"error": "Tool not found"})
                        }
                    )
                    continue

                tool_function = tool_config["function"]
                require_auth = tool_config.get("requires_auth", False)
                # If the tool requires authentication and the user is not authenticated, return an error
                if require_auth and not user.is_authenticated:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps({"error": "Authentication required"})
                        }
                    )
                    continue

                # Create the kwargs for the tool
                kwargs = dict(args)

                if require_auth:
                    kwargs["user"] = user

                if tool_config.get("requires_request"):
                    kwargs["request"] = request

                if tool_config.get("requires_chat_state"):
                    # State is server-owned, so a model cannot supply or alter
                    # another conversation ID through tool arguments.
                    kwargs["conversation_id"] = conversation_id
                    kwargs["chat_state"] = chat_state

                try:
                    tool_result = tool_function(**kwargs)
                    if tool_name == "prepare_booking" and "error" not in tool_result:
                        booking_drafted = True
                except Exception as e:
                    tool_result = {"error": str(e)}

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
                    temperature=0.3,
                    tools=tools,
                    parallel_tool_calls=False
                )
            except Exception as e:
                print("SECOND OPENAI CALL FAILED:", str(e))
                raise

        return (
            "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I unfortunately I was unable to complete that request. "
            "Please try again later."
        ), None
