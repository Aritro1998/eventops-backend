"""
Eval questions for the RAG knowledge-base tool — see runner.py's module
docstring for the question-dict schema. Each setup pulls a real
KnowledgeDocument at run time rather than hardcoding titles/events, so
these fail fast with a clear assertion if the underlying seed data is
ever removed, rather than silently testing nothing.
"""
from knowledge.models import KnowledgeDocument


def _event_refund_policy_setup():
    doc = KnowledgeDocument.objects.filter(
        event__isnull=False,
        title__icontains="Refund"
    ).first()
    
    assert doc is not None, "No event-scoped refund policy document found in seed data"
    
    return {
        "prompt": f"What is the refund policy for {doc.event.name}?",
        "expected_tool": "search_knowledge_base",
        # "7 days" is this fixture's actual cancellation-window number —
        # a specific fact the model should carry through, not paraphrase away.
        "expected_content_contains": ["7 days"],
    }
    

def _venue_specific_policy_setup():
    doc = KnowledgeDocument.objects.filter(
        venue__isnull=False,
        title__icontains="Cinema"
    ).first()
    assert doc is not None, "No cinema venue policy document found in seed data"
    
    event = doc.venue.events.first()
    assert event is not None, "Cinema venue has no events to scope this question to"
    
    return {
        "prompt": f"Can I bring outside food to {event.name}?",
        "expected_tool": "search_knowledge_base",
        # Deliberately contrasts with the Open-Air venue's food policy,
        # which is permissive — if venue scoping ever breaks and this
        # picks up the wrong venue's document, this assertion catches it.
        # "multiplex" is the anchor rather than "not permitted" — the
        # model reliably paraphrases a common phrase like "not permitted"
        # into "cannot bring", but preserves specific/unusual justifying
        # details like "multiplex chain policy" almost verbatim.
        "expected_content_contains": ["multiplex"],
    }
    
    
def _accessibility_setup():
    doc = KnowledgeDocument.objects.filter(
        title__icontains="Accessibility"
    ).first()
    
    assert doc is not None, "No accessibility document found in seed data"
    
    return {
        "prompt": "How far in advance should I request sign language interpretation for an event?",
        "expected_tool": "search_knowledge_base",
        "expected_content_contains": ["48 hours"],
    }
    
    
def _faq_setup():
    doc = KnowledgeDocument.objects.filter(
        title__icontains="Frequently Asked"
    ).first()
    
    assert doc is not None, "No FAQ document found in seed data"

    return {
        # Deliberately not the labeled-vs-general-admission question — that
        # distinction is already taught directly in the system prompt's own
        # booking instructions, so the model can answer it correctly without
        # calling search_knowledge_base at all, which doesn't actually test
        # retrieval. The refund timeline is FAQ-only information the system
        # prompt never states, so answering it requires a real tool call.
        "prompt": "How long does it typically take for an approved refund to be processed?",
        "expected_tool": "search_knowledge_base",
        "expected_content_contains": ["5-7 business days"],
    }
    
    
KNOWLEDGE_QUESTIONS = [
    {"id": "event_refund_policy", "setup": _event_refund_policy_setup},
    {"id": "venue_specific_policy", "setup": _venue_specific_policy_setup},
    {"id": "accessibility_advance_notice", "setup": _accessibility_setup},
    {"id": "faq_refund_timeline", "setup": _faq_setup},
    {
        "id": "no_hallucination_fallback",
        "prompt": "Does EventOps offer a loyalty rewards program with free popcorn every Tuesday?",
        "expected_tool": "search_knowledge_base",
        # Matches the system prompt's own instructed wording ("tell the
        # user you don't have that information") for a fabricated policy —
        # the tool must still be called (it should try), but the answer
        # must not invent a program that doesn't exist.
        "expected_content_contains": ["don't have"],
    },
]    