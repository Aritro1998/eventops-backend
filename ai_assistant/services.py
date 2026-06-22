import json
from openai import OpenAI
from core.settings.base import OPENAI_API_KEY
from ai_assistant.tools.registry import TOOL_REGISTRY
from ai_assistant.models  import PendingBooking

from django.utils import timezone

ALLOWED_HISTORY_ROLES = {"user", "assistant"}

MAX_TOOL_CALLS = 5

def get_system_prompt(user=None, request=None):
    now = timezone.localtime()
    
    if user and user.is_authenticated:
        user_info = f"Current user is authenticated. Username: {user.username}, Email: {user.email}."
    else:
        user_info = "Current user is not authenticated."
        
    if user and user.is_authenticated:
        pending_booking = PendingBooking.objects.filter(user=user).first()
    else:
        pending_booking = None
    
    if pending_booking:
        pending_booking_info = f"""
            Pending booking rules take priority over the normal booking flow.
            If a pending booking exists, first decide whether the user is:
            - confirming it
            - changing seats
            - changing event
            - cancelling it
            - asking an unrelated question
            
            There is already a pending booking, pending booking rules and details are below.

            If there is a pending booking and the user's message is an affirmative confirmation 
            such as "yes", "confirm", "proceed", "book it", "go ahead", or "looks good":
                you MUST call create_booking in the same response.
                Do not ask for confirmation again.
                Do not call prepare_booking.

            If there is a pending booking and the user provides replacement seats:
                call prepare_booking again with new seats and DO NOT call create_booking.
                you MUST call prepare_booking in the same response.
                Do not say "I will prepare" unless the tool call has already been made.

            If the user wants a different event:
                call search_events to search for the event id using the event name and call prepare_booking again.
                
            If the user doesn't want to proceed with the booking with No, Never mind etc:
                call cancel_pending_booking immediately
            
            Pending booking details:
            - Event ID: {pending_booking.event.id}
            - Event Name: {pending_booking.event.name}
            - Seats: {pending_booking.seat_numbers}
            - Amount: {pending_booking.amount}
        """
    else:
        pending_booking_info = ""
   
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
        5. Show booking summary.
        6. Ask the user for confirmation.
        
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


def normalize_history(history):
    messages = []

    for item in history or []:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if role in ALLOWED_HISTORY_ROLES and isinstance(content, str) and content:
            messages.append({
                "role": role,
                "content": content
            })

    return messages


class AIAssistantService:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)

    def chat(self, user_prompt, history, user=None, request=None):
        # 1. Create the system prompt
        messages = [
            {"role": "system", "content": get_system_prompt(user, request)},
            *normalize_history(history),
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
        for _ in range(MAX_TOOL_CALLS):
            message = response.choices[0].message
            # If there are no tool calls, return the response
            if not message.tool_calls:
                return message.content
            
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
                
                try:
                    tool_result = tool_function(**kwargs)
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
        )
