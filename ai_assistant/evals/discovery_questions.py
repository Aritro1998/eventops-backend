def _event_by_name_setup():
    from events.models import Event
    event = Event.objects.order_by("id").first()
    return {
        "prompt": f"Tell me about {event.name}",
        "expected_tool_args_contains": {"event_name": event.name},
        "expected_content_contains": [event.name],
    }


def _seats_for_event_setup():
    from events.models import Event
    event = Event.objects.order_by("id").first()
    return {
        "prompt": f"What seats are available for {event.name}?",
        "expected_content_contains": [event.name],
    }


def _event_price_setup():
    from events.models import Event
    event = Event.objects.order_by("id").first()
    return {
        "prompt": f"How much does {event.name} cost?",
        "expected_content_contains": [event.name],
    }
    

DISCOVERY_QUESTIONS = [
    {
        "id": "weekend_events",
        "prompt": "What events are happening this weekend?",
        "expected_tool": "get_all_events",
    },
    {
        "id": "cheapest_events",
        "prompt": "Show me the cheapest events",
        "expected_tool": "get_all_events",
        "expected_tool_args_contains": {"ordering": "price"},
    },
    {
        "id": "event_by_name",
        "setup": _event_by_name_setup,
        "expected_tool": "search_events",
    },
    {
        "id": "seats_for_event",
        "setup": _seats_for_event_setup,
        "expected_tool": "search_events",
    },
    {
        "id": "event_price",
        "setup": _event_price_setup,
        "expected_tool": "search_events",
    },
]