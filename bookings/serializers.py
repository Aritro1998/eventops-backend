from rest_framework import serializers

from .models import Booking
from events.serializers import EventSummerySerializer, SeatSummerySerializer

class BookingWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = [
            'event',
            'seat',
            'amount',
            'idempotency_key',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at', 'status']

    def validate(self, data):
        event = data.get('event')
        seat = data.get('seat')
        idempotency_key = data.get('idempotency_key')

        # Ensure that the selected seat belongs to the specified event
        if seat and event and seat.event_id != event.id:
            raise serializers.ValidationError("Selected seat does not belong to the specified event.")
        
        # Ensure that the selected seat is not already booked
        if Booking.objects.filter(seat=seat, status='CONFIRMED').exists():
            raise serializers.ValidationError("Selected seat is already booked.")
        
        # Ensure that the idempotency key is unique for the user
        request = self.context.get('request')
        user = request.user if request else None
        if idempotency_key and user and Booking.objects.filter(user=user, idempotency_key=idempotency_key).exists():
            raise serializers.ValidationError("A booking with this idempotency key already exists for the user.")
        
        return data
    
    def validate_amount(self, value):
        """ Ensure that the amount is a positive number. """
        if value <= 0:
            raise serializers.ValidationError("Amount must be a positive number.")
        return value
    
    def create(self, validated_data):
        """ Override create method to associate the booking with the authenticated user. """
        request = self.context.get('request')
        user = request.user if request else None

        validated_data['user'] = user
        validated_data['status'] = 'PENDING'  # Default status for new bookings

        if user is None or not user.is_authenticated:
            raise serializers.ValidationError("User must be authenticated to create a booking.")
        
        return Booking.objects.create(**validated_data)
    

class BookingReadSerializer(serializers.ModelSerializer):
    event = EventSummerySerializer(read_only=True)
    seat = SeatSummerySerializer(read_only=True)
    class Meta:
        model = Booking
        fields = [
            'id',
            'event',
            'seat',
            'status',
            'amount',
            'created_at',
        ]
        read_only_fields = fields