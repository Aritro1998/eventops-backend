"""
Generates the sidebar's dynamic quick-action suggestions - a lightweight,
single-completion call (no tool loop) run concurrently with the main
chat response, not part of the model's own tool-calling turn.
"""

import logging

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

from events.models import Event
from ai_assistant.models import UsageLog
from ai_assistant.langchain_tools.memory import build_message_list
from core.settings.base import OPENAI_API_KEY, OPENAI_CHAT_MODEL


logger = logging.getLogger(__name__)

MAX_LABEL_LENGTH = 60
NUM_QUICK_ACTIONS = 4

DEFAULT_QUICK_ACTIONS = [
    "Show me all events",
    "What events are available this weekend?",
    "Show me the 2 cheapest events",
    "Show me my confirmed bookings",
]

CAPABILITIES_BLURB = """
The user can click a suggested prompt to send it as their next message.
Capabilities you can suggest prompts for:
- Browsing events (by date, price, name)
- Viewing and managing their own bookings (booking, cancelling, retrying a failed payment)
- View seats for a particular event. (Eg. - Show me the seats for ....)
- Create booking for the event. (Eg. - Book seats A1, A2)
- Answering policy/venue questions from the knowledge base: refund policy,
  prohibited items, accessibility, parking, house rules, venue location
"""


class QuickAction(BaseModel):
    label: str = Field(..., description=f"A short, clickable prompt, under {MAX_LABEL_LENGTH} characters.")


class QuickActionsSuggestion(BaseModel):
    needs_update: bool = Field(
        ...,
        description="""
            False if the current quick actions are still good enough for this conversation;
            true if they should be replaced.
        """,
    )
    actions: list[QuickAction] = Field(
        default_factory=list,
        description="""
            Replacement suggestions, exactly matching the count asked for in the
            prompt - only populated when needs_update is true.
        """,
    )


async def get_deterministic_quick_actions(chat_state, user):
    """
    Suggestions the app decides on its own, with no LLM call - for the
    handful of cases where the "obvious next step" is already known from
    state we're tracking anyway, so there's no reason to trust a model to
    reconstruct it from prose. Kept deliberately small: this runs before
    the tool-calling loop (see maybe_generate_quick_actions), so it can
    only see state as of the *start* of this turn, same limitation the
    AI-generated slots have.
    """
    actions = []

    selected_event_id = chat_state.get("selected_event_id")
    if selected_event_id:
        # select_related("space") - is_general_admission below reads
        # event.space, which without it triggers a sync DB fetch that
        # raises SynchronousOnlyOperation from inside this async function.
        event = await Event.objects.filter(id=selected_event_id).select_related("space").afirst()
        if event:
            verb = "tickets for" if event.is_general_admission else "seats for"
            actions.append(f"Book {verb} {event.name}")

    return actions


async def maybe_generate_quick_actions(
    current_actions, chat_state, user, conversation_id, user_prompt, deterministic_actions=None
):
    """
    Decide whether the AI-controlled quick-action suggestions still fit
    the conversation, and generate replacements in the same call if not.
    Returns a list of up to NUM_QUICK_ACTIONS label strings, or None if
    no update is needed.

    deterministic_actions (see get_deterministic_quick_actions) are always
    placed first and are never the model's to invent or replace - they're
    only described to it so it doesn't waste its slots re-suggesting the
    same thing. The model is asked for exactly NUM_QUICK_ACTIONS minus
    however many deterministic ones there are.

    user_prompt is the message the user just sent this turn - without it,
    this only ever saw history from *before* the current turn, so it
    could never react to what the user just asked. Note this still can't
    see this turn's own tool results (e.g. a booking prepare_booking is
    about to create) - the task that calls this is started before the
    tool-calling loop runs, specifically so it overlaps with the main
    model call instead of adding sequential latency after it. Seeing
    tool outcomes too would mean starting this after that loop instead,
    trading away most of the overlap it's built to hide behind.
    """
    deterministic_actions = deterministic_actions or []
    num_ai_actions = max(NUM_QUICK_ACTIONS - len(deterministic_actions), 0)
    # Whatever the model decided last time, minus the slots deterministic
    # actions now occupy - this is the set the model is actually in charge of.
    current_ai_actions = current_actions[len(deterministic_actions):]
    deterministic_changed = deterministic_actions != current_actions[:len(deterministic_actions)]

    if num_ai_actions == 0:
        # Deterministic actions filled every slot - no reason to pay for
        # an LLM call the result of which would just get truncated away.
        return deterministic_actions[:NUM_QUICK_ACTIONS] if deterministic_changed else None

    already_decided_blurb = ""
    if deterministic_actions:
        already_decided_blurb = f"""
        The app has already decided {len(deterministic_actions)} of the quick-action
        suggestions on its own, independent of your judgment - they will be shown to
        the user no matter what you decide below: {deterministic_actions}.
        Do not repeat them, and do not suggest anything that means the same thing.
        Your job is only to decide the other {num_ai_actions}.
        """

    system_prompt = f"""
    {CAPABILITIES_BLURB}
    {already_decided_blurb}

    The user's current AI-suggested quick-action suggestions are: {current_ai_actions}.
    Decide if these are still relevant given the conversation so far,
    including the message the user just sent.

    Mark needs_update=True whenever ANY of the following apply:
    - Any current suggestion duplicates or closely paraphrases what the
      user just asked (e.g. they just asked "show me all events" and one
      suggestion is "Show me all events" - that suggestion is now stale
      and must be replaced, even if the others are fine).
    - The user just booked, viewed, or discussed a specific event -
      prefer suggesting related follow-ups (other events, or a policy
      question relevant to that event/venue) over generic ones.
    Only mark needs_update=False if none of the above apply and the
    current suggestions still make sense as next steps.

    When you DO replace suggestions, check the full conversation history
    below, not just the user's most recent message - never re-suggest
    something the user has already asked about earlier in this same
    conversation. For example, if they asked about the cheapest events
    two turns ago and are now asking about the most popular events,
    don't suggest "cheapest events" again as a follow-up; suggest
    something genuinely not yet covered in this conversation.

    Every suggestion must be concrete enough to send as-is, with no
    placeholder wording like "a specific event" or "a particular event" -
    those describe a capability, they are not a real message a user would
    send. If the conversation above already lists or names specific events
    (e.g. from a prior "show me all events" result), pick one of those real
    event names and use it in the suggestion, e.g. "View seats for Arijit
    Singh Live in Concert" instead of "View seats for a specific event".
    Only fall back to a generic, non-named suggestion (like "Show me all
    events") for capabilities that are inherently generic and don't refer
    to one particular event.

    Generate exactly {num_ai_actions} suggestion(s).
    """

    message = build_message_list(system_prompt, chat_state["history"], user_prompt)

    llm = ChatOpenAI(model=OPENAI_CHAT_MODEL, api_key=OPENAI_API_KEY, temperature=0.3)
    # Use a structured output parser to get a clear, typed result from the model.
    structured_llm = llm.with_structured_output(QuickActionsSuggestion, include_raw=True)

    # Run the model call in the background, so it can run concurrently with the main chat response.
    result = await structured_llm.ainvoke(message)
    # The result is a dict with two keys:
    # "parsed" (the typed QuickActionsSuggestion) and
    # "raw" (the raw LLM output, including usage metadata).
    parsed : QuickActionsSuggestion = result["parsed"]
    raw = result["raw"]

    # Belt-and-suspenders on top of the model's own judgment: if the user's
    # own message is already one of the current AI-controlled suggestions
    # verbatim, the model has been inconsistent about catching that itself.
    # Only forces a refresh if the model actually gave us something to
    # refresh with - forcing needs_update=True with an empty actions list
    # would just get discarded below anyway.
    prompt_matches_current = any(
        user_prompt.strip().lower() == action.strip().lower()
        for action in current_ai_actions
    )
    if prompt_matches_current and parsed.actions:
        parsed.needs_update = True

    usage = raw.usage_metadata
    if usage is not None:
        await UsageLog.objects.acreate(
            conversation_id=conversation_id,
            user=user if user and user.is_authenticated else None,
            model=OPENAI_CHAT_MODEL,
            prompt_tokens=usage["input_tokens"],
            completion_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            tool_called="quick_actions",
        )

    ai_actions = None
    if parsed.needs_update and parsed.actions:
        # Enforced here, not just prompted - a model instruction on length
        # and count is a suggestion, not a guarantee.
        ai_actions = [a.label[:MAX_LABEL_LENGTH] for a in parsed.actions[:num_ai_actions]]

    if not deterministic_changed and ai_actions is None:
        return None

    final_ai_part = ai_actions if ai_actions is not None else current_ai_actions[:num_ai_actions]
    return (deterministic_actions + final_ai_part)[:NUM_QUICK_ACTIONS]
