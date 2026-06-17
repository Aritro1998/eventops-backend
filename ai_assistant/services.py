import json
from openai import OpenAI
from core.settings.base import OPENAI_API_KEY
from ai_assistant.tools.registry import TOOL_REGISTRY

from django.utils import timezone

ALLOWED_HISTORY_ROLES = {"user", "assistant"}

MAX_TOOL_CALLS = 5

def get_system_prompt(user=None):
    now = timezone.localtime()
    
    if user and user.is_authenticated:
        user_info = f"Current user is authenticated. Username: {user.username}, Email: {user.email}."
    else:
        user_info = "Current user is not authenticated."

    system_prompt = f"""
        You are an assistant for EventOps platform. 
        Answer user queries about events, bookings, and payments in a helpful and concise manner.
        Only provide information that is relevant to the user's query. If you don't know the answer, say you don't know instead of making something up.
        
        When a user refers to an event by name rather than id, use the available tools to discover the event id. Do not ask the user for internal ids if they can be found using tools.
        Always use available tools to retrieve the correct identifier before performing actions or answering questions that depend on those identifiers.
        
        If multiple events with similar names are found, ask the user to clarify which event they mean before proceeding.
        
        MANDATORY TOOL: When a user refers to an event by name, use the search_events tool to locate the event id every time.
        Do not infer event ids from event listings, list positions, previous messages, or memory.   
             
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

    def chat(self, user_prompt, history, user=None):
        
        messages = [
            {"role": "system", "content": get_system_prompt(user)},
            *normalize_history(history),
            {"role": "user", "content": user_prompt},
        ]

        tools = [
            tool_config["schema"]
            for tool_config in TOOL_REGISTRY.values()
        ]
        
        try:
            response = self.client.chat.completions.create(
                model='gpt-4o-mini',
                messages=messages,
                temperature=0.3,
                tools=tools
            )
        except Exception as e:
            print("OPENAI CALL FAILED:", str(e))
            raise


        for _ in range(MAX_TOOL_CALLS):
            message = response.choices[0].message
            
            if not message.tool_calls:
                return message.content
            
            tool_call = message.tool_calls[0]
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            # Find function in registry
            tool_config = TOOL_REGISTRY.get(tool_name)
            # Add the tool call message to the conversation history
            messages.append(message)

            if not tool_config:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": "Tool not found"})
                    }
                )
                return (
                    "Sorry, I unfortunately cannot "
                    "perform that action right now."
                )
                
                
            tool_function = tool_config["function"]
            require_auth = tool_config.get("requires_auth", False)
            
            if require_auth and not user.is_authenticated:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": "Authentication required"})
                    }
                )
                return (
                    "Please login first to use this feature."
                )
            
            try:
                if require_auth:
                    tool_result = tool_function(user=user, **args)
                else:
                    tool_result = tool_function(**args)
                    
                print("=> Tool execution result for", tool_name, ":", tool_result)
            except Exception as e:
                print(f"=> Error occurred while executing tool {tool_name}: {e}")
                raise
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result)
                }
            )
            # Get final response after tool execution
            try:
                response = self.client.chat.completions.create(
                    model='gpt-4o-mini',
                    messages=messages,
                    temperature=0.3,
                    tools=tools
                )
            except Exception as e:
                print("SECOND OPENAI CALL FAILED:", str(e))
                raise
            
        return (
            "[MAX_TOOL_CALLS_EXCEEDED] Sorry, I unfortunately I was unable to complete that request. "
            "Please try again later."
        )
