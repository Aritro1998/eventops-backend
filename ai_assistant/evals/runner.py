from ai_assistant.chat_state import ChatState
from ai_assistant.services import AIAssistantService


def run_eval(question, user):
    """Run one eval question through the real chat_stream() method — the
    same code path the actual app uses — and check the outcome against
    what the question declares as correct."""
    if "setup" in question:
        question = {**question, **question["setup"]()}

    # Evals must never see a leftover draft from a previous run — otherwise
    # the model correctly (and confusingly) reports "you already have a
    # pending draft" instead of exercising the behavior being tested.
    from ai_assistant.models import PendingBooking, PendingBookingCancellation, PendingPaymentRetry
    PendingBooking.objects.filter(user=user).delete()
    PendingBookingCancellation.objects.filter(user=user).delete()
    PendingPaymentRetry.objects.filter(user=user).delete()

    conversation_id, chat_state = ChatState.create(user=user)
    tool_call_log = []

    ai_service = AIAssistantService()
    response_text = ""
    for event in ai_service.chat_stream(
        question["prompt"],
        user=user,
        request=None,
        conversation_id=conversation_id,
        chat_state=chat_state,
        tool_call_log=tool_call_log,
    ):
        if event["type"] == "chunk":
            response_text += event["content"]
        # "done" carries no text — it's already fully accumulated by then.

    failures = []
    called_tools = [entry["tool"] for entry in tool_call_log]

    expected_tool = question.get("expected_tool")
    if expected_tool and expected_tool not in called_tools:
        failures.append(f"expected tool '{expected_tool}' to be called, got {called_tools}")

    expected_args = question.get("expected_tool_args_contains")
    if expected_args:
        matching_calls = [e for e in tool_call_log if e["tool"] == expected_tool]
        if not any(
            all(call["args"].get(k) == v for k, v in expected_args.items())
            for call in matching_calls
        ):
            failures.append(f"no call to '{expected_tool}' had args containing {expected_args}")

    expected_content = question.get("expected_content_contains")
    if expected_content:
        for phrase in expected_content:
            if phrase.lower() not in response_text.lower():
                failures.append(f"response did not contain '{phrase}'")

    return {
        "id": question["id"],
        "passed": not failures,
        "failures": failures,
        "response_text": response_text,
        "tool_call_log": tool_call_log,
    }


def run_eval_suite(questions, user):
    results = [run_eval(q, user) for q in questions]
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results}