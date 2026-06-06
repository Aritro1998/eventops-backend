from events.services import EventService
from events.serializers import EventReadSerializer, EventWriteSerializer

def get_all_events():
    events = EventService.get_events_with_available_seats()
    serializer = EventReadSerializer(events, many=True)
    return serializer.data
