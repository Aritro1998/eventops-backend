import gradio as gr
import requests

DJANGO_API_URL = "http://web:8000/api/ai-assistant/chat/"
DJANGO_LOGIN_URL = "http://web:8000/api/auth/token/"
DJANGO_REGISTER_URL = "http://web:8000//api/auth/register/"

def format_error(data):
    if isinstance(data, dict):
        parts = []
        for field, messages in data.items():
            if isinstance(messages, list):
                parts.append(f"{field}: {', '.join(map(str, messages))}")
            else:
                parts.append(f"{field}: {messages}")
        return "; ".join(parts)

    return str(data)


def chat_with_ai(message, token, conversation_id):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        response = requests.post(
            DJANGO_API_URL,
            json=payload,
            headers=headers,
            timeout=60,
        )
        data = response.json()
    except requests.RequestException:
        return "The chat service is unavailable. Please try again.", conversation_id
    except ValueError:
        return "The chat service returned an invalid response.", conversation_id

    return (
        data.get("response", data.get("error", "Unknown error")),
        data.get("conversation_id", conversation_id),
    )


def register(username, email, password):
    if not username or not email or not password:
        return (
            None,
            "Please enter username, email, and password.",
            gr.update(visible=True),
            gr.update(visible=False),
        )

    response = requests.post(
        DJANGO_REGISTER_URL,
        json={
            "username": username,
            "email": email,
            "password": password,
        }
    )

    if response.status_code != 201:
        try:
            data = response.json()
        except Exception:
            data = {}

        return (
            None,
            f"Registration failed: {format_error(data)}",
            gr.update(visible=True),
            gr.update(visible=False),
        )

    return login(username, password)


def login(username, password):
    response = requests.post(
        DJANGO_LOGIN_URL,
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
        gr.update(visible=False),
        None,
        [
            {
                "role": "assistant",
                "content": "Hi! I can help you discover events, compare prices, and later manage bookings.",
            }
        ],
    )


with gr.Blocks(
    title="EventOps AI Assistant",
    theme=gr.themes.Soft(),
) as demo:

    token_state = gr.State(None)
    conversation_id_state = gr.State(None)

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
                email = gr.Textbox(label="Email", placeholder="Only for registration")

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
            conversation_id_state,
            chatbot,
        ]
    )

    register_btn.click(
        fn=register,
        inputs=[username, email, password],
        outputs=[
            token_state,
            login_status,
            login_section,
            logged_in_actions,
        ]
    )

    def chat_wrapper(message, history, token, conversation_id):
        response, conversation_id = chat_with_ai(
            message,
            token,
            conversation_id,
        )

        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ]

        return history, "", conversation_id

    send_btn.click(
        fn=chat_wrapper,
        inputs=[msg, chatbot, token_state, conversation_id_state],
        outputs=[chatbot, msg, conversation_id_state]
    )

    msg.submit(
        fn=chat_wrapper,
        inputs=[msg, chatbot, token_state, conversation_id_state],
        outputs=[chatbot, msg, conversation_id_state]
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
