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
                }
            }
        },
        "required": []
    }
}