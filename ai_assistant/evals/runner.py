"""
A minimal LLM eval harness — not a testing framework in the pytest sense,
but the same idea: run real prompts through the real production code path
(chat_stream, not a mock) and assert on the outcome, because an LLM's
correctness is genuinely different to verify than deterministic code —
the same prompt at temperature=0 can still occasionally take a different
tool-calling path (this is exactly how this project caught and fixed two
intermittent reliability bugs, not through code review alone).

A "question" dict (see discovery_questions.py/booking_questions.py) can
declare any combination of:
- expected_tool: a tool name that must appear somewhere in the turn's
  tool_call_log
- expected_tool_args_contains: a dict of args that some call to
  expected_tool must have included
- expected_content_contains: substrings the final response text must
  contain
- setup: a callable returning the above dynamically at run time, for
  questions that depend on live data (a real event name/seat number)
  rather than hardcoded values that would go stale as fixtures change.
"""

from asgiref.sync import sync_to_async

from ai_assistant.chat_state import ChatState
from ai_assistant.services import chat_stream as default_chat_stream


async def run_eval(question, user, chat_fn=None):
    """Run one eval question through a chat_stream()-shaped async
    generator and check the outcome against what the question declares
    as correct. Defaults to the real production code path
    (ai_assistant.services.chat_stream) when chat_fn isn't given, so
    passing an alternate implementation (matching the same call
    signature) is the only thing needed to run the identical questions
    against it instead."""
    if "setup" in question:
        # setup callables (discovery_questions.py etc.) are plain sync
        # functions doing ordinary ORM queries — they're eval fixtures, not
        # part of the actual async chat surface, so they're wrapped here
        # rather than individually converted.
        question = {**question, **(await sync_to_async(question["setup"])())}

    # Evals must never see a leftover draft from a previous run — otherwise
    # the model correctly (and confusingly) reports "you already have a
    # pending draft" instead of exercising the behavior being tested.
    from ai_assistant.models import PendingBookingThread, PendingBookingCancellation
    await sync_to_async(PendingBookingThread.objects.filter(user=user).delete)()
    await sync_to_async(PendingBookingCancellation.objects.filter(user=user).delete)()

    conversation_id, chat_state = await sync_to_async(ChatState.create)(user=user)
    tool_call_log = []

    if chat_fn is None:
        chat_fn = default_chat_stream

    response_text = ""
    async for event in chat_fn(
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


async def run_eval_suite(questions, user, chat_fn=None):
    """Run every question in a list (see discovery_questions.py's
    DISCOVERY_QUESTIONS / booking_questions.py's BOOKING_QUESTIONS) and
    summarize pass/fail counts. chat_fn is forwarded to run_eval
    unchanged - see its docstring."""
    results = [await run_eval(q, user, chat_fn=chat_fn) for q in questions]
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results}