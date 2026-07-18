import logging

from django.db import transaction
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import OrderingFilter
from rest_framework.exceptions import ValidationError

from .models import Event
from .services import EventService
from bookings.services import BookingService
from core.permissions import IsAdminOrOrganizer
from .serializers import EventReadSerializer, EventWriteSerializer

logger = logging.getLogger(__name__)

# Create your views here.
class EventViewSet(ModelViewSet):
    """
    CRUD viewset for managing events with available seats annotation.
    This viewset provides the following features:
    1. List and Retrieve with Available Seats:
       - Both list and retrieve actions return the number of available seats for each event.
       - This is achieved by annotating the queryset with a count of confirmed bookings and calculating available seats on the fly.
    2. Create and Update:
       - Allows creation and updating of events with appropriate permissions.
       - The available seats count is not relevant for create/update operations, so it is only annotated for read operations.
    3. Ordering:
       - Supports ordering events by start_time for better client-side sorting.
    4. Permissions:
       - Read operations (list/retrieve) are open to all users.
       - Write operations (create/update/destroy) are restricted to authenticated users with admin or organizer roles.
    5. Performance:
       - Uses select_related and annotations to minimize database queries and optimize performance for read operations.
    """
    queryset = Event.objects.all()
    serializer_class = EventReadSerializer

    # Adding ordering filter to allow clients to order events by start_time
    filter_backends = [OrderingFilter]
    ordering = ['start_time']

    def get_queryset(self):
        """
        Override get_queryset to ensure available_seats is always annotated for both list and retrieve actions.
        For create/update actions, we return the base queryset without annotation since available_seats is not relevant.
        """
        if self.action in ['list', 'retrieve']:
            # Get date filter from query params
            date_filter = self.request.query_params.get('date', None)
            # Annotate the queryset with available seats for both list and retrieve actions
            return EventService.get_events_with_available_seats(date_filter=date_filter)
    
        return self.queryset

    def get_serializer_class(self):
        """Use different serializers for read and write operations."""
        if self.action in ['list', 'retrieve']:
            return self.serializer_class
        return EventWriteSerializer
    
    def list(self, request, *args, **kwargs):
        """Override list to implement caching for event listings."""

        # Use the full query string so different filtered/ordered requests do not share one cache entry.
        query_string = request.META.get("QUERY_STRING", "")
        cache_key = f"events:list:{query_string or 'default'}"
        # Check if the event list is already cached
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return Response(cached_data)
        
        response = super().list(request, *args, **kwargs)
        # Cache the response data for future requests with TTL of 5 minutes
        cache.set(cache_key, response.data, timeout=300)

        return response

    def retrieve(self, request, *args, **kwargs):
        """Override retrieve to implement caching for event details."""
        # Determine the event ID from the URL kwargs using the lookup field or lookup URL kwarg.
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        # Get the event ID from the URL kwargs to use as part of the cache key for caching individual event details.
        event_id = self.kwargs[lookup_url_kwarg]
        # Retrieve event details with caching to improve performance for frequently accessed events.
        cache_key = f'event:{event_id}'
        # Check if the event details are already cached
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return Response(cached_data)
        
        response = super().retrieve(request, *args, **kwargs)
        # Cache the response data for future requests with TTL of 5 minutes
        cache.set(cache_key, response.data, timeout=300)

        return response
   
    def perform_create(self, serializer):
        """
        Override to set the created_by field to the current user on event creation.
        Seat generation (space-aware or plain sequential) is delegated to
        EventService, the same logic the admin panel uses.
        """
        with transaction.atomic():
            event = serializer.save(created_by=self.request.user)
            EventService.sync_seats_on_create(event)

            logger.info(
                "event_created",
                extra={
                    "event": "event_created",
                    "event_id": event.id,
                    "created_by_user_id": self.request.user.id,
                    "total_seats": event.total_seats,
                }
            )

            # transaction.on_commit (not a direct call) so the cache is only
            # invalidated after the DB transaction actually commits — if it
            # ran immediately and the transaction later rolled back, a stale
            # cache entry could get invalidated for an event that was never
            # really created/changed, or a reader could refill the cache
            # from data that's about to disappear.
            transaction.on_commit(lambda: BookingService.invalidate_event_cache(event.id))

    def perform_update(self, serializer):
        """
        Safety validation (space/venue consistency, seat-reduction guards)
        already ran in EventWriteSerializer.validate() via Event.clean().
        This only saves the event and delegates seat synchronization to
        EventService, the same logic the admin panel uses.
        """
        # We lock the event row so seat-count changes and seat table updates happen together.
        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=serializer.instance.pk)
            old_total_seats = event.total_seats
            old_space_id = event.space_id
            serializer.instance = event

            updated_event = serializer.save()
            EventService.sync_seats_on_update(updated_event, old_total_seats, old_space_id)

            logger.info(
                "event_updated",
                extra={
                    "event": "event_updated",
                    "event_id": updated_event.id,
                    "updated_by_user_id": self.request.user.id,
                    "old_total_seats": old_total_seats,
                    "new_total_seats": updated_event.total_seats,
                }
            )

            # Invalidate cache for the updated event and the event list after updating an event
            transaction.on_commit(lambda: BookingService.invalidate_event_cache(updated_event.id))

    def perform_destroy(self, instance):
        """
        Seat.event and Booking.event are both on_delete=CASCADE, but
        BookingSeat.seat is on_delete=PROTECT — Django refuses to delete
        an Event that has ANY booking at all, cascade or not, active or
        not. Checking has_booking_history() first turns that into a clean
        400 instead of letting Django's ProtectedError surface as an
        unhandled 500. There's no "delete anyway" path: an event with
        booking history should be archived (see EventService.archive_event
        / Event.is_archived), never actually deleted.
        """
        if EventService.has_booking_history(instance):
            raise ValidationError(
                "This event has booking history and cannot be deleted. Archive it instead."
            )

        with transaction.atomic():
            event_id = instance.id
            super().perform_destroy(instance)
            logger.info(
                "event_deleted",
                extra={
                    "event": "event_deleted",
                    "event_id": event_id,
                    "deleted_by_user_id": self.request.user.id,
                }
            )
            # Invalidate cache for the deleted event and the event list after deleting an event
            transaction.on_commit(lambda: BookingService.invalidate_event_cache(event_id))
            
    def get_permissions(self):
        """list/retrieve are public; every write action requires
        IsAdminOrOrganizer (see core/permissions.py) — admins can touch
        any event, organizers only their own."""
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # Only authenticated users can create, update, or delete events
            return [IsAdminOrOrganizer()]
        return [AllowAny()]
