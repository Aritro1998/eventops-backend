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


def chat_with_ai(message, history, token):
    converted_history = convert_history(history)

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.post(
        DJANGO_API_URL,
        json={
            "message": message,
            "history": converted_history
        },
        headers=headers
    )

    data = response.json()
    print("DJANGO RESPONSE:", data)

    return data.get("response", data.get("error", "Unknown error"))


def login(username, password):
    response = requests.post(
        "http://web:8000/api/auth/token/",
        json={"username": username, "password": password}
    )

    if response.status_code == 200:
        token = response.json().get("access")

        return (
            token,
            f"""
<div style='text-align:center; font-size:40px; font-weight:700;'>
👤 {username}
</div>
""",
            gr.update(visible=False),
            gr.update(visible=True)
        )

    return (
        None,
        "❌ Login failed",
        gr.update(),
        gr.update()
    )


def logout():
    return (
        None,
        """
<div style='text-align:center; font-size:32px; font-weight:600;'>
🔓 Guest User
</div>
""",
        gr.update(visible=True),
        gr.update(visible=False)
    )




with gr.Blocks(
    title="EventOps AI Assistant",
    theme=gr.themes.Soft(),
) as demo:

    token_state = gr.State(None)

    with gr.Row(equal_height=False):

        with gr.Column(scale=1, min_width=220):
            gr.Markdown("# 🎟️ EventOps")
            gr.Markdown("### Browse events with AI")

            login_status = gr.Markdown(
    """
    <div style='text-align:center; font-size:32px; font-weight:600;'>
    🔓 Guest User
    </div>
    """
)

            logged_in_actions = gr.Column(visible=False)

            with gr.Accordion("Login / Register", open=False) as login_section:
                username = gr.Textbox(label="Username")

                password = gr.Textbox(
                    label="Password",
                    type="password"
                )

                login_btn = gr.Button(
                    "Login",
                    variant="primary"
                )
                register_btn = gr.Button(
                    "Register",
                    variant="secondary"
                )

            with logged_in_actions:
                logout_btn = gr.Button(
                    "Logout",
                    variant="secondary"
                )

            gr.Markdown("### Quick Actions")

            weekend_btn = gr.Button("🎟️ Events This Weekend")
            cheap_btn = gr.Button("💰 Cheapest Events")
            expensive_btn = gr.Button("🏆 Most Expensive Events")
            next_month_btn = gr.Button("📅 Events Next Month")

        with gr.Column(scale=4):

            chatbot = gr.Chatbot(
                type="messages",
                height="80vh",
                value=[
                    {
                        "role": "assistant",
                        "content": "Hi! I can help you discover events, compare prices, and later manage bookings."
                    }
                ]
            )

            msg = gr.Textbox(
                placeholder="Ask about events...",
                show_label=False,
                container=False
            )

            send_btn = gr.Button(
                "Send",
                variant="primary"
            )

    login_btn.click(
        fn=login,
        inputs=[username, password],
        outputs=[
            token_state,
            login_status,
            login_section,
            logged_in_actions,
        ]
    )

    logout_btn.click(
        fn=logout,
        outputs=[
            token_state,
            login_status,
            login_section,
            logged_in_actions,
        ]
    )

    register_btn.click(
        fn=lambda: "Registration will be added in a later milestone. Please use an existing account for now.",
        outputs=login_status
    )

    def chat_wrapper(message, history, token):
        response = chat_with_ai(message, history, token)

        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response}
        ]

        return history, ""

    send_btn.click(
        fn=chat_wrapper,
        inputs=[msg, chatbot, token_state],
        outputs=[chatbot, msg]
    )

    msg.submit(
        fn=chat_wrapper,
        inputs=[msg, chatbot, token_state],
        outputs=[chatbot, msg]
    )

    def set_prompt(text):
        return text

    weekend_btn.click(
        fn=lambda: "What events are available this weekend?",
        outputs=msg
    )

    cheap_btn.click(
        fn=lambda: "Show me the cheapest events",
        outputs=msg
    )

    expensive_btn.click(
        fn=lambda: "Show me the most expensive events",
        outputs=msg
    )

    next_month_btn.click(
        fn=lambda: "What events are available next month?",
        outputs=msg
    )


demo.launch(server_name="0.0.0.0", server_port=7860)
