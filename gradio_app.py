"""
The actual production chat frontend (see docker-compose.yml's `gradio`
service) — a Gradio Blocks app that talks to the Django backend purely
over HTTP, the same API any other client could use. Structure, top to
bottom: URL/constant definitions, CSS, then four groups of plain
functions (format_*/render_* build HTML strings for chat bubbles and
cards, request_json/stream_chat_with_ai make the actual HTTP calls,
login/register/logout handle auth, and the rest are Gradio event
handlers), and finally the `with gr.Blocks(...)` block that declares the
UI and wires each handler to a button/event.

Gradio's model: gr.State holds values across interactions without a
database (token_state, conversation_id_state, action_state below);
component.click(fn=..., inputs=[...], outputs=[...]) wires a handler
function's return values (in order) to update named components — the
lengths and order of a handler's return tuple and its `outputs` list
must match exactly, which is why several handlers below return long,
positional tuples. A handler can also be a generator (yield instead of
return) to push multiple UI updates over time — that's what makes the
streamed chat reply visibly type itself out (see get_ai_response).

Only chat/stream is actually used (DJANGO_STREAM_API_URL) — chat/
(DJANGO_API_URL, non-streaming) is defined but not called anywhere in
this file; see ai_assistant/views.py's module docstring for why that
endpoint still exists.
"""

import json
import gradio as gr
import requests
from datetime import datetime
from html import escape


DJANGO_API_URL = "http://web:8000/api/ai-assistant/chat/"
DJANGO_STREAM_API_URL = "http://web:8000/api/ai-assistant/chat/stream/"
CONFIRM_DRAFT_URL = (
    "http://web:8000/api/ai-assistant/actions/confirm-pending-booking/"
)
CANCEL_DRAFT_URL = (
    "http://web:8000/api/ai-assistant/actions/cancel-pending-booking/"
)
CONFIRM_CANCEL_URL = (
    "http://web:8000/api/ai-assistant/actions/confirm-cancel-booking/"
)
KEEP_BOOKING_URL = (
    "http://web:8000/api/ai-assistant/actions/keep-booking/"
)
CONFIRM_PAYMENT_RETRY_URL = (
    "http://web:8000/api/ai-assistant/actions/confirm-payment-retry/"
)
DISMISS_PAYMENT_RETRY_URL = (
    "http://web:8000/api/ai-assistant/actions/dismiss-payment-retry/"
)
DJANGO_LOGIN_URL = "http://web:8000/api/auth/token/"
DJANGO_REGISTER_URL = "http://web:8000/api/auth/register/"

WELCOME_MESSAGE = {
    "role": "assistant",
    "content": "Hi! I can help you discover events, compare prices, and manage bookings.",
}

BRAND_HEADER_HTML = (
    "<div class='brand-header'>"
    "<div class='brand-mark'>E</div>"
    "<div class='brand-text'><h1>EventOps</h1><p>Browse events with AI</p></div>"
    "</div>"
)

CUSTOM_CSS = """
.gradio-container {
    max-width: 1320px !important;
    margin: 0 auto !important;
    font-size: 16px !important;
}

/* ---------- Sidebar shell ---------- */
.sidebar {
    background: var(--background-fill-secondary);
    border: 1px solid var(--border-color-primary);
    border-radius: 18px;
    padding: 22px 18px !important;
    max-height: 88vh;
    overflow-y: auto;
}

.brand-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 24px;
}
.brand-mark {
    width: 42px;
    height: 42px;
    border-radius: 12px;
    background: linear-gradient(135deg, var(--color-accent), #a78bfa);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 20px;
    flex-shrink: 0;
}
.brand-text h1 {
    font-size: 21px;
    margin: 0;
    font-weight: 800;
}
.brand-text p {
    margin: 2px 0 0;
    font-size: 12.5px;
    color: var(--body-text-color-subdued);
}

/* ---------- Auth ---------- */
.auth-hint p {
    font-size: 13px !important;
    color: var(--body-text-color-subdued) !important;
    margin: 0 0 10px !important;
}
.auth-error {
    font-size: 12.5px;
    color: #dc2626;
    margin-top: 8px;
    min-height: 0;
}
.auth-btn {
    margin-top: 6px !important;
}

/* ---------- Profile card ---------- */
.profile-card {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    border: 1px solid var(--border-color-primary);
    border-radius: 14px;
    background: var(--background-fill-primary);
    margin-bottom: 10px;
}
.avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: var(--color-accent);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 16px;
    flex-shrink: 0;
}
.profile-meta {
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.profile-name {
    font-weight: 700;
    font-size: 14.5px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.profile-role {
    font-size: 12px;
    color: var(--body-text-color-subdued);
}

/* ---------- Section label + quick actions ---------- */
.section-label, .section-label p {
    font-size: 12px !important;
    text-transform: uppercase;
    letter-spacing: .05em;
    color: var(--body-text-color-subdued) !important;
    margin: 20px 0 8px !important;
}

.quick-actions button {
    justify-content: flex-start !important;
    border-radius: 10px !important;
    font-weight: 500 !important;
    padding: 11px 14px !important;
    font-size: 14.5px !important;
    transition: transform .15s ease, box-shadow .15s ease;
}
.quick-actions button:hover {
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
    transform: translateY(-1px);
}

/* ---------- Chat panel ---------- */
.chat-panel {
    border: 1px solid var(--border-color-primary);
    border-radius: 18px !important;
    overflow: hidden;
    background: var(--background-fill-primary);
}

.message-row {
    margin-top: 18px !important;
}

/* The cap must live on the outer row, not the inner message div: the inner
   div is 100%-wide all the way down to the text, so a percentage max-width
   there resolves against an already-collapsed ancestor and makes short
   messages (e.g. "book 50") wrap after just a couple of characters. */
.message-row.bubble.user-row, .message-row.bubble.bot-row {
    max-width: 74% !important;
}

.bot.message, .user.message {
    font-size: 15px !important;
    line-height: 1.5 !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

/* Gradio groups consecutive same-role turns into one bubble with several
   .message-content children; without a divider they read as one wall of text. */
.message-content + .message-content {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px dashed var(--border-color-primary);
}

.user.message, .user.message * {
    color: #fff !important;
    background: transparent;
}
.user.message {
    background: var(--color-accent) !important;
    border: none !important;
}

.draft-actions {
    padding: 14px 20px !important;
    gap: 12px !important;
    border-top: 1px solid var(--border-color-primary);
    background: var(--background-fill-secondary);
}
.draft-actions button {
    border-radius: 999px !important;
    font-size: 15px !important;
    padding: 12px 20px !important;
    font-weight: 600 !important;
}

.composer {
    padding: 16px 20px 20px !important;
    gap: 10px !important;
    align-items: center !important;
}
.composer textarea {
    border-radius: 999px !important;
    font-size: 15px !important;
    padding: 12px 18px !important;
}
.composer button {
    border-radius: 999px !important;
    min-width: 90px;
    font-size: 15px !important;
    font-weight: 600 !important;
}
"""

# ---------- Formatting / HTML-card rendering helpers ----------

def format_event_time(iso_string):
    """ISO datetime string -> human-readable ("18 Jul 2026, 07:30 PM").
    Falls back to returning the raw string unchanged if it doesn't parse,
    rather than raising and breaking the whole card render."""
    try:
        dt = datetime.fromisoformat(iso_string)
    except (TypeError, ValueError):
        return iso_string
    return dt.strftime("%d %b %Y, %I:%M %p")


def format_time_remaining(iso_string):
    # A snapshot computed once at render time, not a live-ticking clock — see
    # the retry-payment design discussion for why a real tick isn't worth it
    # here (chat history is a static list of already-rendered strings).
    try:
        expires_at = datetime.fromisoformat(iso_string)
    except (TypeError, ValueError):
        return "unknown"

    now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
    remaining_seconds = (expires_at - now).total_seconds()
    if remaining_seconds <= 0:
        return "expired"

    minutes = int(remaining_seconds // 60)
    return f"~{minutes} min" if minutes >= 1 else "<1 min"


def seat_display_row(data):
    """
    Shared (label, escaped value) row for the "which seats" line every
    booking/draft/cancellation/retry card shows — data is expected to
    carry is_general_admission/seat_labels/seat_count (see
    Booking.seat_display / EventService.describe_seats on the backend).
    General admission has no individual seat identity, only a count.
    """
    if data.get("is_general_admission"):
        return ("Tickets", escape(str(data.get("seat_count", 0))))
    return ("Seat", escape(", ".join(data.get("seat_labels", []))))


def render_status_card(icon, accent, title, subtitle, rows):
    """Shared HTML-card renderer — every render_*_card function below
    (booking, draft, cancel-prepare, payment-retry-prepare) is really just
    "pick an icon/color/title and a list of (label, value) rows" and calls
    through to this one. `rows` values are expected to already be
    HTML-escaped by the caller (see the render_*_card functions using
    `escape()` on every value before passing it in)."""
    rows_html = "".join(
        f"<tr>"
        f"<td style='padding:6px 0;color:var(--body-text-color-subdued);'>{label}</td>"
        f"<td style='padding:6px 0;text-align:right;font-weight:600;'>{value}</td>"
        f"</tr>"
        for label, value in rows
    )

    return (
        "<div style='border:1px solid var(--border-color-primary);border-radius:12px;"
        "padding:20px 24px;max-width:380px;background:var(--background-fill-primary);'>"
        "<div style='display:flex;align-items:center;gap:8px;margin-bottom:4px;'>"
        f"<span style='color:{accent};font-size:18px;'>{icon}</span>"
        f"<span style='font-size:17px;font-weight:700;'>{title}</span>"
        "</div>"
        f"<div style='color:var(--body-text-color-subdued);font-size:13px;margin-bottom:14px;'>{subtitle}</div>"
        f"<table style='width:100%;font-size:14px;border-collapse:collapse;'>{rows_html}</table>"
        "</div>"
    )


# Maps a booking's status to how its card looks. "KEPT" isn't a real Django
# Booking status (see bookings/models.py) — it's a value dismiss_cancellation()
# invents so a "kept my booking" outcome can reuse this same card/renderer
# instead of a separate code path.
BOOKING_STATUS_CARD_STYLES = {
    "CONFIRMED": ("&#10003;", "#16a34a", "Booking Confirmed", "Your booking has been successfully confirmed."),
    "CANCELLED": ("&#10005;", "#dc2626", "Booking Cancelled", "This booking has been cancelled."),
    "KEPT": ("&#10003;", "#16a34a", "Booking Kept", "Your booking was not cancelled."),
    "FAILED": ("&#9888;", "#d97706", "Booking Failed", "Payment didn't go through this time."),
    "EXPIRED": ("&#10005;", "#dc2626", "Booking Expired", "No more retries are available for this booking."),
}


def render_booking_card(booking):
    """Card shown after a confirm/cancel/retry action resolves — style and
    copy driven entirely by booking["status"], via BOOKING_STATUS_CARD_STYLES
    above."""
    status = booking.get("status", "")

    if status in BOOKING_STATUS_CARD_STYLES:
        icon, accent, title, subtitle = BOOKING_STATUS_CARD_STYLES[status]
    else:
        # Anything else falls back to a generic "still pending" style rather
        # than needing an entry per status.
        icon, accent, title = "&#8987;", "#d97706", "Booking Pending"
        subtitle = f"Your booking was created with status {escape(status)}."

    rows = [
        ("Event", escape(booking.get("event_name", ""))),
        seat_display_row(booking),
        ("Time", escape(format_event_time(booking.get("event_start_time", "")))),
        ("Amount", f"₹{escape(booking.get('amount', ''))}"),
        ("Booking ID", escape(str(booking.get("booking_id", "")))),
    ]

    # FAILED is the one status where "what happens next" matters enough to
    # earn extra rows — these are exactly the numbers that decide whether
    # clicking Retry Payment Now is worth it.
    if status == "FAILED":
        if "attempts_remaining" in booking:
            rows.append(("Attempts Remaining", escape(str(booking["attempts_remaining"]))))
        if "expires_at" in booking:
            rows.append(("Expires In", escape(format_time_remaining(booking["expires_at"]))))

    return render_status_card(icon, accent, title, subtitle, rows)


def render_draft_card(draft):
    """Shown when the AI just staged a new/replacement booking
    (prepare_booking ran this turn) — replaces the assistant's chat bubble
    with this card so the user sees exactly what they're about to confirm."""
    rows = [
        ("Event", escape(draft.get("event_name", ""))),
        seat_display_row(draft),
        ("Time", escape(format_event_time(draft.get("event_start_time", "")))),
        ("Amount", f"₹{escape(draft.get('amount', ''))}"),
    ]
    return render_status_card(
        "&#128221;",
        "var(--color-accent)",
        "Booking Draft",
        "Review the details, then confirm or cancel below.",
        rows,
    )


def render_cancel_prepare_card(cancellation):
    # Shown when the AI stages a cancellation (prepare_cancel_booking ran this
    # turn). Unlike render_draft_card, this includes the Booking ID — that's
    # the detail a user checks to catch the AI staging the wrong one of their
    # bookings (e.g. two bookings for the same event) before clicking confirm.
    rows = [
        ("Event", escape(cancellation.get("event_name", ""))),
        seat_display_row(cancellation),
        ("Time", escape(format_event_time(cancellation.get("event_start_time", "")))),
        ("Amount", f"₹{escape(cancellation.get('amount', ''))}"),
        ("Booking ID", escape(str(cancellation.get("booking_id", "")))),
    ]
    return render_status_card(
        "&#9888;",
        "#d97706",
        "Cancel This Booking?",
        "Review the details, then confirm the cancellation or keep your booking below.",
        rows,
    )


def render_payment_retry_prepare_card(payment_retry):
    # Shown when the AI stages a retry conversationally (prepare_payment_retry
    # ran this turn). Attempts Remaining and Expires In matter more here than
    # anywhere else in the app — a retry isn't guaranteed to succeed, so this
    # is the number that decides whether clicking is even worth it.
    rows = [
        ("Event", escape(payment_retry.get("event_name", ""))),
        seat_display_row(payment_retry),
        ("Time", escape(format_event_time(payment_retry.get("event_start_time", "")))),
        ("Amount", f"₹{escape(payment_retry.get('amount', ''))}"),
        ("Booking ID", escape(str(payment_retry.get("booking_id", "")))),
        ("Attempts Remaining", escape(str(payment_retry.get("attempts_remaining", "")))),
        ("Expires In", escape(format_time_remaining(payment_retry.get("expires_at", "")))),
    ]
    return render_status_card(
        "&#8635;",
        "var(--color-accent)",
        "Retry This Payment?",
        "A retry is not guaranteed to succeed. Review the details, then retry now or wait.",
        rows,
    )


def render_profile_card(username):
    """Small sidebar card shown once logged in — just an avatar initial
    and the username, swapped in for the login/register panel."""
    initial = escape(username[:1].upper()) if username else "?"
    return (
        "<div class='profile-card'>"
        f"<div class='avatar'>{initial}</div>"
        "<div class='profile-meta'>"
        f"<div class='profile-name'>{escape(username)}</div>"
        "<div class='profile-role'>Signed in</div>"
        "</div></div>"
    )


def render_error(text):
    """Wrap an error string in the auth-error CSS class, or return an
    empty string for a falsy/no-error input — Gradio's gr.HTML component
    renders an empty string as nothing visible."""
    return f"<div class='auth-error'>{escape(text)}</div>" if text else ""


def format_error(data):
    """Flatten a DRF-style error payload (field -> [messages] or
    field -> message) into one readable string for display."""
    if not isinstance(data, dict):
        return str(data)

    parts = []
    for field, messages in data.items():
        if isinstance(messages, list):
            parts.append(f"{field}: {', '.join(map(str, messages))}")
        else:
            parts.append(f"{field}: {messages}")
    return "; ".join(parts)


# ---------- HTTP calls to the Django backend ----------

def request_json(url, token=None, payload=None):
    """Shared POST helper for every non-streaming Django API call (login,
    register, and every confirm/dismiss action button). Never raises for
    network/JSON failures — always returns a dict, with an "error" key on
    failure, so callers can use the same `.get("error")` check regardless
    of what actually went wrong."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=60,
        )
        data = response.json()
    except requests.RequestException:
        return {"error": "The service is unavailable. Please try again."}
    except ValueError:
        return {"error": "The service returned an invalid response."}

    if not response.ok:
        return {"error": data.get("detail", format_error(data))}

    return data


def stream_chat_with_ai(message, token, conversation_id):
    """Yield ("chunk", text) as each piece of the reply arrives, then exactly
    one ("done", event_dict) once the server signals the turn is finished, or
    ("error", text) if the connection or the stream itself fails."""
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(
            DJANGO_STREAM_API_URL,
            json=payload,
            headers=headers,
            stream=True,
            timeout=60,
        )
    except requests.RequestException:
        yield "error", "The service is unavailable. Please try again."
        return

    if not response.ok:
        yield "error", "The service returned an error. Please try again."
        return

    # SSE frames the response as one "data: <json>" line per event followed
    # by a blank line — iter_lines() hands us one line at a time, so we just
    # skip anything that isn't a data line.
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue

        try:
            event = json.loads(line[len("data: "):])
        except ValueError:
            continue

        if event["type"] == "chunk":
            yield "chunk", event["content"]
        elif event["type"] == "done":
            yield "done", event
        elif event["type"] == "error":
            yield "error", event.get("message", "Something went wrong.")


# ---------- Auth click handlers ----------

def login(username, password):
    """Gradio click handler for the Log In button. The long return tuples
    here map positionally onto `auth_outputs` (token, profile card,
    guest/profile/quick-actions panel visibility) plus the login form's
    own error/password-field outputs — see login_btn.click(...) near the
    bottom of this file for the exact output list they must match."""
    data = request_json(
        DJANGO_LOGIN_URL,
        payload={"username": username, "password": password},
    )
    token = data.get("access")

    if token:
        return (
            token,
            render_profile_card(username),
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=True),
            "",
            "",
        )

    return (
        None,
        "",
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        render_error(f"Login failed: {data.get('error', 'Unknown error')}"),
        "",
    )


def register(username, email, password):
    """Click handler for Create Account — on success, logs the new user
    in immediately by delegating to login() rather than duplicating its
    return-tuple shape."""
    if not username or not email or not password:
        return (
            None,
            "",
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            render_error("Please enter username, email, and password."),
            "",
        )

    data = request_json(
        DJANGO_REGISTER_URL,
        payload={"username": username, "email": email, "password": password},
    )
    if "error" in data:
        return (
            None,
            "",
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            render_error(f"Registration failed: {data['error']}"),
            "",
        )

    return login(username, password)


def action_updates(actions):
    """
    Translate the backend's flat `actions` list (each a {"type": ...,
    "label": ...} dict) into gr.update() visibility toggles for every
    action button/row in the UI. Called after every chat turn and every
    action-button click so the buttons shown always match what the
    backend currently reports as pending — never left stale from a
    previous turn.
    """
    # Three independent action "families" can appear in the same list at
    # once — a leftover seat draft, a staged cancellation, and a staged
    # payment retry are all unrelated bookings and can coexist — so each
    # row's visibility is computed from its own action types instead of
    # treating the whole list as one mutually-exclusive state.
    action_types = {action["type"] for action in actions}
    has_draft_actions = bool({"confirm_pending_booking", "cancel_pending_booking"} & action_types)
    has_cancel_actions = bool({"confirm_cancel_booking", "keep_booking"} & action_types)
    has_retry_actions = bool({"confirm_payment_retry", "dismiss_payment_retry"} & action_types)

    return (
        actions,
        gr.update(visible=has_draft_actions),
        gr.update(visible="confirm_pending_booking" in action_types),
        gr.update(visible="cancel_pending_booking" in action_types),
        gr.update(visible=has_cancel_actions),
        gr.update(visible="confirm_cancel_booking" in action_types),
        gr.update(visible="keep_booking" in action_types),
        gr.update(visible=has_retry_actions),
        gr.update(visible="confirm_payment_retry" in action_types),
        gr.update(visible="dismiss_payment_retry" in action_types),
        # Hide the composer whenever anything is pending, not just drafts.
        gr.update(visible=not actions),
    )


def logout():
    """Reset auth state, chat history, and every action row back to a
    fresh-visit state."""
    return (
        None,
        "",
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        [WELCOME_MESSAGE],
        *action_updates([]),
    )


# ---------- Chat event handlers ----------

def add_user_message(message, history):
    # Runs instantly (no network call) so the user's own message appears
    # right away instead of waiting alongside the AI's reply.
    if not message or not message.strip():
        return history, gr.update()
    return history + [{"role": "user", "content": message}], ""


def get_ai_response(history, token, conversation_id, current_actions):
    """
    Generator that drives the streamed chat reply — a plain `return` here
    would give Gradio one final UI state; `yield`-ing repeatedly instead
    is what makes the chatbot visibly grow the assistant's reply chunk by
    chunk as stream_chat_with_ai produces them, rather than the message
    popping in all at once when the whole response finally finishes.
    """
    # Nothing to do if add_user_message skipped an empty message.
    if not history or history[-1]["role"] != "user":
        yield history, conversation_id, *action_updates(current_actions)
        return

    message = history[-1]["content"]

    # Start the assistant's bubble empty; each streamed chunk grows it in
    # place, so re-yielding the same list (with this last entry mutated) is
    # what makes the reply visibly type itself out instead of appearing whole.
    history = history + [{"role": "assistant", "content": ""}]
    accumulated_text = ""
    final_event = None

    for event_type, payload in stream_chat_with_ai(message, token, conversation_id):
        if event_type == "chunk":
            accumulated_text += payload
            history[-1]["content"] = accumulated_text
            yield history, conversation_id, *action_updates(current_actions)
        elif event_type == "done":
            final_event = payload
        elif event_type == "error":
            history[-1]["content"] = payload
            yield history, conversation_id, *action_updates(current_actions)
            return

    if final_event is None:
        # Connection dropped mid-stream with no "done" event at all — keep
        # whatever text did arrive rather than losing it silently.
        yield history, conversation_id, *action_updates(current_actions)
        return

    conversation_id = final_event.get("conversation_id", conversation_id)
    actions = final_event.get("actions")
    # Keep draft controls visible when the chat request itself failed.
    if actions is None:
        actions = current_actions

    draft = final_event.get("draft")
    cancellation = final_event.get("cancellation")
    payment_retry = final_event.get("payment_retry")

    # Same rule as before streaming existed: when a draft/cancellation/retry
    # was staged this turn, its card replaces the plain text — it's just
    # that here, the plain text was already visibly streamed in first.
    if draft:
        history[-1]["content"] = render_draft_card(draft)
    elif cancellation:
        history[-1]["content"] = render_cancel_prepare_card(cancellation)
    elif payment_retry:
        history[-1]["content"] = render_payment_retry_prepare_card(payment_retry)

    yield history, conversation_id, *action_updates(actions)


def run_draft_action(url, token, history, current_actions, conversation_id, action_label):
    """
    Shared handler behind every confirm/dismiss button (Confirm booking,
    Cancel draft, Confirm Cancellation, Keep Booking, Retry Payment Now,
    Not Now) — each just calls this with its own action URL and button
    label; see the .click(...) wiring near the bottom of this file for
    which button maps to which URL/label pair.
    """
    # Log the click as its own chat turn so it visually separates the prior
    # assistant message from the outcome, instead of both sharing one bubble.
    history = history + [{"role": "user", "content": action_label}]

    data = request_json(
        url,
        token=token,
        payload={"conversation_id": conversation_id},
    )
    booking = data.get("booking")
    if booking:
        content = render_booking_card(booking)
    else:
        content = data.get("response", data.get("error", "Action failed."))
    history = history + [{"role": "assistant", "content": content}]

    # A failed action keeps the existing controls available for a retry.
    actions = data.get("actions", current_actions)
    return history, *action_updates(actions)


# ---------- UI layout + wiring ----------
# Everything below declares the actual page (sidebar, chatbot, buttons)
# and binds each interaction to one of the handler functions above via
# .click()/.submit(). gr.State components hold values (auth token,
# conversation id, pending actions) across interactions without a
# database, since this frontend is intentionally stateless server-side —
# all real state lives in Django/Redis, this just carries the handles to
# it (token, conversation_id) between one browser interaction and the next.
with gr.Blocks(
    title="EventOps AI Assistant",
    theme=gr.themes.Soft(),
    css=CUSTOM_CSS,
) as demo:
    token_state = gr.State(None)
    conversation_id_state = gr.State(None)
    action_state = gr.State([])

    with gr.Row(equal_height=True):
        with gr.Column(scale=1, min_width=280, elem_classes=["sidebar"]):
            gr.HTML(BRAND_HEADER_HTML)

            with gr.Column(visible=True) as guest_panel:
                gr.Markdown("Sign in to manage your bookings.", elem_classes=["auth-hint"])
                with gr.Tabs():
                    with gr.Tab("Log In"):
                        login_username = gr.Textbox(label="Username")
                        login_password = gr.Textbox(label="Password", type="password")
                        login_btn = gr.Button(
                            "Log In", variant="primary", elem_classes=["auth-btn"]
                        )
                        login_error = gr.HTML("")
                    with gr.Tab("Create Account"):
                        reg_username = gr.Textbox(label="Username")
                        reg_email = gr.Textbox(label="Email")
                        reg_password = gr.Textbox(label="Password", type="password")
                        register_btn = gr.Button(
                            "Create Account", variant="primary", elem_classes=["auth-btn"]
                        )
                        register_error = gr.HTML("")

            with gr.Column(visible=False) as profile_panel:
                profile_card = gr.HTML("")
                logout_btn = gr.Button("Log Out", variant="secondary")

            with gr.Column(visible=False) as quick_actions_panel:
                gr.Markdown("Quick Actions", elem_classes=["section-label"])
                with gr.Column(elem_classes=["quick-actions"]):
                    weekend_btn = gr.Button("Events This Weekend")
                    cheap_btn = gr.Button("Cheapest Events")
                    expensive_btn = gr.Button("Most Expensive Events")
                    next_month_btn = gr.Button("Events Next Month")

        with gr.Column(scale=4, elem_classes=["chat-panel"]):
            chatbot = gr.Chatbot(
                type="messages",
                height="72vh",
                value=[WELCOME_MESSAGE],
                show_label=False,
            )
            with gr.Row(visible=False, elem_classes=["draft-actions"]) as action_row:
                confirm_draft_btn = gr.Button("Confirm booking", variant="primary", visible=False)
                cancel_draft_btn = gr.Button("Cancel draft", variant="secondary", visible=False)

            # Separate row from action_row on purpose: a seat draft and a
            # staged cancellation are unrelated and can both be pending at
            # once (see action_updates), so each needs its own visibility.
            with gr.Row(visible=False, elem_classes=["draft-actions"]) as cancellation_row:
                confirm_cancel_btn = gr.Button("Confirm Cancellation", variant="stop", visible=False)
                keep_booking_btn = gr.Button("Keep Booking", variant="secondary", visible=False)

            # A third independent row — a staged payment retry is also
            # unrelated to the other two and can be pending alongside them.
            with gr.Row(visible=False, elem_classes=["draft-actions"]) as payment_retry_row:
                confirm_retry_btn = gr.Button("Retry Payment Now", variant="primary", visible=False)
                dismiss_retry_btn = gr.Button("Not Now", variant="secondary", visible=False)

            with gr.Row(elem_classes=["composer"]) as composer_row:
                msg = gr.Textbox(
                    placeholder="Ask about events...",
                    show_label=False,
                    container=False,
                    scale=5,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)

    auth_outputs = [token_state, profile_card, guest_panel, profile_panel, quick_actions_panel]

    login_btn.click(
        fn=login,
        inputs=[login_username, login_password],
        outputs=[*auth_outputs, login_error, login_password],
    )
    register_btn.click(
        fn=register,
        inputs=[reg_username, reg_email, reg_password],
        outputs=[*auth_outputs, register_error, reg_password],
    )
    logout_btn.click(
        fn=logout,
        outputs=[
            *auth_outputs,
            conversation_id_state,
            chatbot,
            action_state,
            action_row,
            confirm_draft_btn,
            cancel_draft_btn,
            cancellation_row,
            confirm_cancel_btn,
            keep_booking_btn,
            payment_retry_row,
            confirm_retry_btn,
            dismiss_retry_btn,
            composer_row,
        ],
    )

    ai_response_inputs = [chatbot, token_state, conversation_id_state, action_state]
    ai_response_outputs = [
        chatbot,
        conversation_id_state,
        action_state,
        action_row,
        confirm_draft_btn,
        cancel_draft_btn,
        cancellation_row,
        confirm_cancel_btn,
        keep_booking_btn,
        payment_retry_row,
        confirm_retry_btn,
        dismiss_retry_btn,
        composer_row,
    ]
    send_btn.click(
        fn=add_user_message, inputs=[msg, chatbot], outputs=[chatbot, msg]
    ).then(fn=get_ai_response, inputs=ai_response_inputs, outputs=ai_response_outputs)
    msg.submit(
        fn=add_user_message, inputs=[msg, chatbot], outputs=[chatbot, msg]
    ).then(fn=get_ai_response, inputs=ai_response_inputs, outputs=ai_response_outputs)

    action_inputs = [token_state, chatbot, action_state, conversation_id_state]
    action_outputs = [
        chatbot,
        action_state,
        action_row,
        confirm_draft_btn,
        cancel_draft_btn,
        cancellation_row,
        confirm_cancel_btn,
        keep_booking_btn,
        payment_retry_row,
        confirm_retry_btn,
        dismiss_retry_btn,
        composer_row,
    ]
    confirm_draft_btn.click(
        fn=lambda token, history, actions, conversation_id: run_draft_action(
            CONFIRM_DRAFT_URL,
            token,
            history,
            actions,
            conversation_id,
            "Confirm booking",
        ),
        inputs=action_inputs,
        outputs=action_outputs,
    )
    cancel_draft_btn.click(
        fn=lambda token, history, actions, conversation_id: run_draft_action(
            CANCEL_DRAFT_URL,
            token,
            history,
            actions,
            conversation_id,
            "Cancel draft",
        ),
        inputs=action_inputs,
        outputs=action_outputs,
    )
    confirm_cancel_btn.click(
        fn=lambda token, history, actions, conversation_id: run_draft_action(
            CONFIRM_CANCEL_URL,
            token,
            history,
            actions,
            conversation_id,
            "Confirm Cancellation",
        ),
        inputs=action_inputs,
        outputs=action_outputs,
    )
    keep_booking_btn.click(
        fn=lambda token, history, actions, conversation_id: run_draft_action(
            KEEP_BOOKING_URL,
            token,
            history,
            actions,
            conversation_id,
            "Keep Booking",
        ),
        inputs=action_inputs,
        outputs=action_outputs,
    )
    confirm_retry_btn.click(
        fn=lambda token, history, actions, conversation_id: run_draft_action(
            CONFIRM_PAYMENT_RETRY_URL,
            token,
            history,
            actions,
            conversation_id,
            "Retry Payment Now",
        ),
        inputs=action_inputs,
        outputs=action_outputs,
    )
    dismiss_retry_btn.click(
        fn=lambda token, history, actions, conversation_id: run_draft_action(
            DISMISS_PAYMENT_RETRY_URL,
            token,
            history,
            actions,
            conversation_id,
            "Not Now",
        ),
        inputs=action_inputs,
        outputs=action_outputs,
    )

    weekend_btn.click(fn=lambda: "What events are available this weekend?", outputs=msg)
    cheap_btn.click(fn=lambda: "Show me the cheapest events", outputs=msg)
    expensive_btn.click(fn=lambda: "Show me the most expensive events", outputs=msg)
    next_month_btn.click(fn=lambda: "What events are available next month?", outputs=msg)


demo.launch(server_name="0.0.0.0", server_port=7860)
