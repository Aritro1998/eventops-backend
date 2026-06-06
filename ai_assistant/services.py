import json
from openai import OpenAI
from core.settings.base import OPENAI_API_KEY
from ai_assistant.tools.registry import TOOL_REGISTRY
from ai_assistant.tools.schemas import GET_ALL_EVENTS_TOOL

SYSTEM_PROMPT = """
    You are an assistant for EventOps platform. 
    Answer user queries about events, bookings, and payments in a helpful and concise manner.
    Only provide information that is relevant to the user's query. If you don't know the answer, say you don't know instead of making something up.
"""

ALLOWED_HISTORY_ROLES = {"user", "assistant"}


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
            {"role": "system", "content": SYSTEM_PROMPT},
            *normalize_history(history),
            {"role": "user", "content": user_prompt},
        ]

        tools = [GET_ALL_EVENTS_TOOL]

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
            date_filter = args.get("date_filter", None)
            
            # Find function in registry and execute
            tool_function = TOOL_REGISTRY.get(tool_name)
            # Add the tool call message to the conversation history
            messages.append(message)

            if tool_function:
                tool_result = tool_function(date_filter=date_filter)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result)
                    }
                )
                # Get final response after tool execution
                final_response = self.client.chat.completions.create(
                    model='gpt-4o-mini',
                    messages=messages,
                    temperature=0.3
                )
                return final_response.choices[0].message.content
            else:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": "Tool not found"})
                    }
                )
                return "Sorry, I unfortunately cannot perform that action right now."
            
        return message.content

            
