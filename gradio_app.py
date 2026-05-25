import gradio as gr
import requests

DJANGO_API_URL = "http://web:8000/api/ai-assistant/chat/"


def chat_with_ai(message, history):
    response = requests.post(
        DJANGO_API_URL,
        json={"message": message}
    )

    data = response.json()

    return data["response"]


demo = gr.ChatInterface(
    fn=chat_with_ai,
    title="EventOps AI Assistant",
    description="AI assistant for EventOps platform"
)

demo.launch(server_name="0.0.0.0", server_port=7860)