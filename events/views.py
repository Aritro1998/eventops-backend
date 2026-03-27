from django.shortcuts import render
from django.db import transaction
from rest_framework import serializers
from django.db.models import Count, Q, F
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import OrderingFilter

from .models import Event, Seat
from .serializers import EventReadSerializer, EventWriteSerializer
from core.permissions import IsAdminOrOrganizer

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
        event = serializer.save(created_by=self.request.user)

        # After creating the event, create the corresponding seats based on total_seats
        Seat.objects.bulk_create([
            Seat(event=event, seat_number=i) 
            for i in range(1, event.total_seats + 1)
        ])

    def perform_update(self, serializer):
        # Use a transaction to ensure atomicity of the update and seat adjustments
        with transaction.atomic():
            event = Event.objects.select_for_update().get(id=self.get_object().id)

            # Get current total_seats before update
            old_total_seats = event.total_seats

            # Update the event with new data
            new_total_seats = serializer.validated_data.get('total_seats', old_total_seats)

            # Get the count of currently booked seats for the event
            max_booked_seat = event.bookings.seats.aggregate(
                max_seat_number=Max('seat_number', filter=Q(status='CONFIRMED'))
            )['max_seat_number'] or 0

            # Validate that the new total_seats is not less than the number of already booked seats
            if new_total_seats < max_booked_seat:
                # Validate that the new total_seats is not less than the number of already booked seats
                raise serializers.ValidationError(
                    f"Cannot reduce total seats below the highest booked seat number ({max_booked_seat})"
                )
            elif new_total_seats == old_total_seats:
                # If total_seats is unchanged, we can simply save the event without modifying seats
                serializer.save()
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
                # If total_seats has decreased, need to remove the extra seats
                # We will only remove unbooked seats to avoid affecting existing bookings
                seats_to_remove = Seat.objects.filter(
                    event=updated_event,
                    seat_number__gt=updated_event.total_seats
                ).exclude(
                    id__in=event.bookings.filter(status='CONFIRMED').values_list('seat_id', flat=True)
                )
                # Remove the unbooked seats that are above the new total_seats
                seats_to_remove.delete()

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # Only authenticated users can create, update, or delete events
            return [IsAdminOrOrganizer()]
        return [AllowAny()]

