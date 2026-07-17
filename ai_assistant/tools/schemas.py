from bookings.models import Booking

GET_ALL_EVENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_all_events",
        "description": (
            "Get all upcoming events. "
            "Optionally filter events by date."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_filter": {
                    "type": "string",
                    "description": (
                        "Date in YYYY-MM-DD format. "
                        "For example: 2026-06-08"
                    )
                },
                "start_date": {
                    "type": "string",
                    "description": (
                        "Start date in YYYY-MM-DD format"
                    )
                },
                "end_date": {
                    "type": "string",
                    "description": (
                        "End date in YYYY-MM-DD format"
                    )
                },
                "ordering": {
                    "type": "string",
                    "description": (
                        "Sort order. "
                        "Use price for cheapest events. "
                        "Use -price for most expensive events. "
                        "Use start_time for earliest events. "
                        "Use -start_time for latest events."
                    )
                }
            },
            "required": []
        }
    }
}


GET_EVENT_DETAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "get_event_detail",
        "description": (
            "Get detailed information for a specific event."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The event id"
                }
            },
            "required": ["event_id"]
        }
    }
}


GET_MY_BOOKINGS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_my_bookings",
        "description": (
            "Get bookings belonging to the currently authenticated user. "
            "Optionally filter bookings by status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [choice[0] for choice in Booking.STATUS_CHOICES],
                    "description": (
                        "Get bookings for the currently authenticated user. "
                        "Use this when the user asks to see their bookings, booking history, "
                        "confirmed bookings, pending bookings, failed bookings, or cancelled bookings."
                    )
                }
            }
        },
        "required": []
    }
}


GET_AVAILABLE_SEATS_TOOL = {
    "type": "function",
    "function": {
        "name": "get_available_seats",
        "description": (
            "Get all available seats for an event."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "Event id"
                }
            },
            "required": ["event_id"]
        }
    }
}


SEARCH_EVENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_events",
        "description": (
            "MANDATORY TOOL. "
            "Use this tool FIRST whenever a user mentions an event by name. "
            "This tool converts event names into event ids. "
            "All other event tools require a valid event id. "
            "Never derive event ids from conversation history, list positions, "
            "memory, assumptions, previous tool outputs, or event ordering. "
            "Always call this tool before get_event_detail, "
            "get_available_seats, or prepare_booking when the user provides an event name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_name": {
                    "type": "string",
                    "description": (
                        "The event name provided by the user."
                    )
                }
            },
            "required": [
                "event_name"
            ]
        }
    }
}


PREPARE_BOOKING_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_booking",
        "description": (
            "Prepare or replace a pending booking before final confirmation. "
            "Call this when the user chooses seats for a new booking, or when an existing "
            "pending booking exists and the user asks to change the seats. "
            "After this tool succeeds, summarize the updated pending booking and direct the user "
            "to the displayed confirmation controls. "
            "Do not call this for affirmative confirmations like yes, confirm, proceed, or book it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "seat_numbers": {
                    "type": "array",
                    "items": {
                        "type": "integer"
                    }
                }
            },
            "required": ["seat_numbers"]
        }
    }
}


RETRY_PAYMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "retry_payment",
        "description": (
            "Retry payment for one of the current user's existing bookings. "
            "Use this when the user asks to retry payment, pay again, complete payment, "
            "or fix a failed/pending payment. "
            "Requires the booking id. If the user does not provide a booking id, "
            "call get_my_bookings first or ask which booking they mean."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "integer",
                    "description": "The user's booking id to retry payment for."
                }
            },
            "required": ["booking_id"]
        }
    }
}


PREPARE_CANCEL_BOOKING_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_cancel_booking",
        "description": (
            "Stage the cancellation of one of the authenticated user's CONFIRMED "
            "bookings. This does NOT cancel the booking — it only prepares it for "
            "confirmation. After this tool succeeds, tell the user to use the "
            "displayed Confirm Cancellation or Keep Booking controls. Never say "
            "the booking was cancelled unless a backend tool result confirms it. "
            "If the booking ID is unknown, call get_my_bookings with status "
            "CONFIRMED first. If more than one booking matches what the user "
            "described (e.g. two bookings for the same event), list the "
            "distinguishing details — seat number and booking id — for each "
            "match and ask the user to pick one. Never guess or default to the "
            "most recent match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "integer",
                    "description": "The confirmed booking ID to stage for cancellation."
                }
            },
            "required": ["booking_id"]
        }
    }
}

