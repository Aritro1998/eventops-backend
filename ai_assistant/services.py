import json
from openai import OpenAI
from core.settings.base import OPENAI_API_KEY
from ai_assistant.tools.registry import TOOL_REGISTRY

from django.utils import timezone

ALLOWED_HISTORY_ROLES = {"user", "assistant"}

def get_system_prompt():
    now = timezone.localtime()

    system_prompt = f"""
        You are an assistant for EventOps platform. 
        Answer user queries about events, bookings, and payments in a helpful and concise manner.
        Only provide information that is relevant to the user's query. If you don't know the answer, say you don't know instead of making something up.
        Current date: {now.date().isoformat()}
        Current time: {now.strftime("%H:%M:%S")}
        Current day: {now.strftime('%A')}
        Timezone: {str(now.tzinfo)}
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

    def chat(self, user_prompt, history):

        messages = [
            {"role": "system", "content": get_system_prompt()},
            *normalize_history(history),
            {"role": "user", "content": user_prompt},
        ]

        tools = [
            tool_config["schema"]
            for tool_config in TOOL_REGISTRY.values()
        ]

        response = self.client.chat.completions.create(
            model='gpt-4o-mini',
            messages=messages,
            temperature=0.3,
            tools=tools
        )

        message = response.choices[0].message

        if message.tool_calls:
            tool_call = message.tool_calls[0]
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            # Find function in registry and execute
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
            try:
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
                final_response = self.client.chat.completions.create(
                    model='gpt-4o-mini',
                    messages=messages,
                    temperature=0.3
                )
            except Exception as e:
                print("SECOND OPENAI CALL FAILED:", str(e))
                raise
            return final_response.choices[0].message.content
            
        return message.content
