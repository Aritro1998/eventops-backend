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
            "get_available_seats, or create_booking when the user provides an event name."
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


CREATE_BOOKING_TOOL = {
    "type": "function",
    "function": {
        "name": "create_booking",
        "description": (
            "Finalize the existing pending booking after explicit user confirmation. "
            "Only call this when a pending booking exists and the user clearly confirms. "
            "Never call this when the user is changing seats, changing event, cancelling event or asking a question."
        ),
        "parameters": {
            "type": "object",
            "properties": {}
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
            "After this tool succeeds, summarize the updated pending booking and ask for confirmation. "
            "Do not call this for affirmative confirmations like yes, confirm, proceed, or book it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer"
                },
                "seat_numbers": {
                    "type": "array",
                    "items": {
                        "type": "integer"
                    }
                }
            },
            "required": [
                "event_id",
                "seat_numbers"
            ]
        }
    }
}


