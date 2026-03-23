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
    CRUD API for events with optimized read operations including available seat count.
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
                    'booking',
                    filter=Q(booking__status='CONFIRMED')
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

