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