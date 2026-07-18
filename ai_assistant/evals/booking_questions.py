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
    event = Event.objects.order_by("id").first()
    # This event has no Space, so display_label is null and the seat's
    # label falls back to its plain seat_number as a string — see
    # SeatSummarySerializer.get_label.
    seat_number = EventService.get_available_seats(event.id).first().seat_number
    return {
        "prompt": f"Book seat {seat_number} for {event.name}",
        "expected_tool_args_contains": {"seat_labels": [str(seat_number)]},
    }


def _book_two_seats_setup():
    from events.models import Event
    from events.services import EventService
    event = Event.objects.order_by("id").first()
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