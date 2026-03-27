from django.shortcuts import render
from django.db.models import Count, Q, F
from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import AllowAny

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
        """Override to set the created_by field to the current user on event creation."""
        serializer.save(created_by=self.request.user)

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # Only authenticated users can create, update, or delete events
            return [IsAdminOrOrganizer()]
        return [AllowAny()]

