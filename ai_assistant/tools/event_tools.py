from events.services import EventService
from ai_assistant.chat_state import ChatState
from events.serializers import EventReadSerializer, EventSummarySerializer, SeatSummarySerializer

def get_all_events(date_filter=None, start_date=None, end_date=None, ordering=None):
    print(f"=> Executing get_all_events tool with date_filter: {date_filter}, start_date: {start_date}, end_date: {end_date}, ordering: {ordering}")
    events = EventService.get_events_with_available_seats(
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        ordering=ordering
    )
    serializer = EventReadSerializer(events, many=True)
    return serializer.data


def get_event_detail(event_id):
    print("=> Executing get_event_detail tool with event_id:", event_id)
    event = EventService.get_event_detail(event_id)
    serializer = EventReadSerializer(event)
    return serializer.data


def get_available_seats(request, event_id, conversation_id, chat_state):
    print("=> Executing get_available_seats tool with event_id:", event_id)
    event = EventService.get_event_detail(event_id)

    # Seat selection may happen in a later request, so retain the event in
    # server-side state instead of trusting the model to remember its ID.
    chat_state['selected_event_id'] = event.id
    ChatState.save(conversation_id, chat_state)

    available_seats = EventService.get_available_seats(event_id)
    serializer = SeatSummarySerializer(available_seats, many=True)

    return {
        "event_id": event.id,
        "event_name": event.name,
        "seats": serializer.data,
    }


def search_events(event_name):
    print("=> Executing search_events tool with event_name:", event_name)
    events = EventService.search_events_by_name(event_name)
    serializer = EventSummarySerializer(events, many=True)
    return serializer.data
