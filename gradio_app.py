import gradio as gr
import requests

DJANGO_API_URL = "http://web:8000/api/ai-assistant/chat/"


def convert_history(history):
    messages = []

    for item in history or []:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if role in {"user", "assistant"} and isinstance(content, str) and content:
            messages.append({
                "role": role,
                "content": content
            })

    return messages


def chat_with_ai(message, history):
    converted_history = convert_history(history)

    response = requests.post(
        DJANGO_API_URL,
        json={
            "message": message,
            "history": converted_history
        }
    )

    data = response.json()
    return data["response"]


demo = gr.ChatInterface(
    fn=chat_with_ai,
    title="EventOps AI Assistant",
    description="AI assistant for EventOps platform",
    type="messages",
)

demo.launch(server_name="0.0.0.0", server_port=7860)
