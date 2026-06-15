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