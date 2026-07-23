import logging

from django.core.cache import cache

from .serializers import EventReadSerializer


logger = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 300  # 5 minutes


def get_events_list_cached(date_filter=None, start_date=None, end_date=None, ordering=None):
    """
    Cached, already-serialized event listing - the one place both
    EventViewSet.list() and the AI's get_all_events tool now get their
    data from, so a request through either path can be answered by a
    cache entry the OTHER path filled in.

    Cache-aside: check Redis first - on a hit, return it straight away,
    no database query at all. On a miss, run the real query, serialize
    it, THEN store it in Redis for next time, before returning it. The
    key is built from this function's own arguments (not an HTTP
    querystring) - that's what makes sharing possible: a REST request
    and a tool call that happen to want the identical combination of
    filters land on the exact same Redis key.
    """
    from .services import EventService

    cache_key = f"events:list:{date_filter!r}:{start_date!r}:{end_date!r}:{ordering!r}"

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    events = EventService.get_events_with_available_seats(
        date_filter=date_filter,
        start_date=start_date,
        end_date=end_date,
        ordering=ordering
    )
    
    data = EventReadSerializer(events, many=True).data
    cache.set(cache_key, data, timeout=CACHE_TTL_SECONDS)
    
    return data


def get_event_detail_cached(event_id):
    """
    Cached, already-serialized single-event lookup - shared by
    EventViewSet.retrieve() and the AI's get_event_detail tool.
    """
    from .services import EventService

    cache_key = f"event:{event_id}"

    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    event = EventService.get_event_detail(event_id)
    data = EventReadSerializer(event).data
    cache.set(cache_key, data, timeout=CACHE_TTL_SECONDS)
    
    return data


def invalidate_event_cache(event_id):
    """
    Bust every cache entry tied to one event - called whenever something
    changes what get_events_list_cached/get_event_detail_cached would
    return: a booking/seat status change (create, cancel, expire, payment
    success), or an event write itself (create/update/delete).
    """
    cache.delete(f"event:{event_id}")
    
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("events:list:*")
        
    logger.info(
        "event_cache_invalidated",
        extra={"event": "event_cache_invalidated", "event_id": event_id},
    )