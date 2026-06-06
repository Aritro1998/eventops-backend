from events.services import EventService
from events.serializers import EventReadSerializer, EventWriteSerializer

def get_all_events(date_filter=None):
    events = EventService.get_events_with_available_seats(date_filter=date_filter)
    serializer = EventReadSerializer(events, many=True)
    return serializer.data
