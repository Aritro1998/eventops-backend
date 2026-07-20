"""
Eval questions covering booking/cancellation/retry conversations. The
last three questions here (cancel_needs_lookup_first,
retry_needs_lookup_first) exist specifically to verify the model asks
get_my_bookings for a booking id rather than guessing one when it isn't
given — the same "don't let it guess an identifier" principle the
searched_event_ids guardrail enforces on the discovery side, checked here
at the conversational level instead of via a code-level allowlist.
"""


def _book_one_seat_setup():
    from events.models import Event
    from events.services import EventService
    # Must be an event with no Space at all — not just order_by("id").first(),
    # which silently started resolving to a general admission event once
    # GA seed data existed, and a GA event correctly gets asked for a
    # quantity instead of accepting a seat number, breaking this eval for
    # a reason unrelated to what it's actually testing. A space-less event
    # keeps display_label null, so the seat's label falls back to its
    # plain seat_number as a string — see SeatSummarySerializer.get_label.
    event = Event.objects.filter(space__isnull=True).order_by("id").first()
    seat_number = EventService.get_available_seats(event.id).first().seat_number
    return {
        "prompt": f"Book seat {seat_number} for {event.name}",
        "expected_tool_args_contains": {"seat_labels": [str(seat_number)]},
    }


def _book_two_seats_setup():
    from events.models import Event
    from events.services import EventService
    # See _book_one_seat_setup for why this must exclude general admission
    # events rather than just taking the lowest id.
    event = Event.objects.filter(space__isnull=True).order_by("id").first()
    seat_numbers = list(
        EventService.get_available_seats(event.id).values_list("seat_number", flat=True)[:2]
    )
    return {
        "prompt": f"Book seats {seat_numbers[0]} and {seat_numbers[1]} for {event.name}",
        "expected_tool_args_contains": {"seat_labels": [str(sn) for sn in seat_numbers]},
    }


BOOKING_QUESTIONS = [
    {
        "id": "book_specific_seat",
        "setup": _book_one_seat_setup,
        "expected_tool": "prepare_booking",
    },
    {
        "id": "book_two_seats",
        "setup": _book_two_seats_setup,
        "expected_tool": "prepare_booking",
    },
    {
        "id": "show_my_bookings",
        "prompt": "Show me my bookings",
        "expected_tool": "get_my_bookings",
    },
    {
        "id": "cancel_needs_lookup_first",
        "prompt": "I want to cancel my confirmed booking for Night Life",
        "expected_tool": "get_my_bookings",
    },
    {
        "id": "retry_needs_lookup_first",
        "prompt": "Can you retry the payment on my failed booking?",
        "expected_tool": "get_my_bookings",
    },
]