import logging

from django.db.models import Max
from django.db import transaction
from django.utils import timezone
from django.core.cache import cache
from rest_framework import serializers
from django.db.models import Count, Q, F
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import OrderingFilter

from .models import Event, Seat
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

    def invalidate_event_cache(self, event_id):
        """
        Invalidate cache for a specific event and the event list when an event is updated or deleted.
        """
        cache.delete(f"event:{event_id}")
        cache.delete_pattern("events:list:*")
        logger.info(
            "event_cache_invalidated",
            extra={
                "event": "event_cache_invalidated",
                "event_id": event_id,
            }
        )

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

    def get_queryset(self):
        """
        Override get_queryset to ensure available_seats is always annotated for both list and retrieve actions.
        For create/update actions, we return the base queryset without annotation since available_seats is not relevant.
        """
        if self.action in ['list', 'retrieve']:
            # Annotate the queryset with available seats for both list and retrieve actions
            return self.queryset.annotate(
                confirmed_bookings=Count(
                    'bookings',
                    filter=Q(bookings__status='CONFIRMED')
                ),
                available_seats=F('total_seats') - F('confirmed_bookings')
            )
        return self.queryset
    
    def get_serializer_class(self):
        """Use different serializers for read and write operations."""
        if self.action in ['list', 'retrieve']:
            return self.serializer_class
        return EventWriteSerializer
    
    def perform_create(self, serializer):
        """
        Override to set the created_by field to the current user on event creation.
        After saving the event, we also create the corresponding seats based on total_seats.
        """
        with transaction.atomic():
            event = serializer.save(created_by=self.request.user)

            # After creating the event, create the corresponding seats based on total_seats
            Seat.objects.bulk_create([
                Seat(event=event, seat_number=i) 
                for i in range(1, event.total_seats + 1)
            ])

            logger.info(
                "event_created",
                extra={
                    "event": "event_created",
                    "event_id": event.id,
                    "created_by_user_id": self.request.user.id,
                    "total_seats": event.total_seats,
                }
            )

            # Invalidate cache for the event list after creating a new event
            transaction.on_commit(lambda: self.invalidate_event_cache(event.id))

    def perform_update(self, serializer):
        # We lock the event row so seat-count changes and seat table updates happen together.
        with transaction.atomic():
            event = Event.objects.select_for_update().get(id=self.get_object().id)

            # Get current total_seats before update
            old_total_seats = event.total_seats

            # Update the event with new data
            new_total_seats = serializer.validated_data.get('total_seats', old_total_seats)

            # Get the count of currently booked seats for the event
            max_booked_seat = event.bookings.filter(
                status='CONFIRMED'
            ).aggregate(
                max_seat_number=Max('seat__seat_number')
            )['max_seat_number'] or 0


            # Validate that the new total_seats is not less than the number of already booked seats
            if new_total_seats < max_booked_seat:
                # Validate that the new total_seats is not less than the number of already booked seats
                logger.warning(
                    "event_seat_reduction_blocked",
                    extra={
                        "event": "event_seat_reduction_blocked",
                        "event_id": event.id,
                        "requested_total_seats": new_total_seats,
                        "max_booked_seat": max_booked_seat,
                    }
                )
                raise serializers.ValidationError(
                    f"Cannot reduce total seats below the highest booked seat number ({max_booked_seat})"
                )
            elif new_total_seats == old_total_seats:
                # If total_seats is unchanged, we can simply save the event without modifying seats
                updated_event = serializer.save()
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
                transaction.on_commit(lambda: self.invalidate_event_cache(updated_event.id))
                return

            # Save after validation
            updated_event = serializer.save()

            # Adjust seats based on the new total_seats value
            if updated_event.total_seats > old_total_seats:
                # If total_seats has increased, just add new seats
                Seat.objects.bulk_create([
                    Seat(event=updated_event, seat_number=i) 
                    for i in range(old_total_seats + 1, updated_event.total_seats + 1)
                ])
            elif updated_event.total_seats < old_total_seats:
                seats_above_limit = Seat.objects.filter(
                    event=updated_event,
                    seat_number__gt=updated_event.total_seats
                )

                active_bookings_exist = event.bookings.filter(
                    seat__in=seats_above_limit
                ).filter(
                    Q(status='CONFIRMED') |
                    Q(status__in=['PENDING', 'FAILED'], expires_at__gt=timezone.now())
                ).exists()

                if active_bookings_exist:
                    logger.warning(
                        "event_active_booking_reduction_blocked",
                        extra={
                            "event": "event_active_booking_reduction_blocked",
                            "event_id": updated_event.id,
                            "requested_total_seats": updated_event.total_seats,
                        }
                    )
                    raise serializers.ValidationError(
                        "Cannot reduce total seats because some higher-numbered seats have active bookings."
                    )

                # Shrinking is only safe after we prove those higher-numbered seats are not
                # still referenced by active bookings.
                seats_to_remove = seats_above_limit.exclude(
                    id__in=event.bookings.filter(status='CONFIRMED').values_list('seat_id', flat=True)
                )
                # Remove the unbooked seats that are above the new total_seats
                seats_to_remove.delete()

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
            transaction.on_commit(lambda: self.invalidate_event_cache(updated_event.id))

    def perform_destroy(self, instance):
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
            transaction.on_commit(lambda: self.invalidate_event_cache(event_id))
            
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # Only authenticated users can create, update, or delete events
            return [IsAdminOrOrganizer()]
        return [AllowAny()]
