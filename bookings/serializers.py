from rest_framework import serializers

from .models import Booking
from events.serializers import EventSummerySerializer, SeatSummerySerializer
from payments.serializers import PaymentReadSerializer

class BookingWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = [
            'event',
            'seat',
            'idempotency_key',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at', 'status', 'amount']

    def validate(self, data):
        event = data.get('event')
        seat = data.get('seat')
        idempotency_key = data.get('idempotency_key')

        # Ensure that the selected seat belongs to the specified event
        if seat and event and seat.event_id != event.id:
            raise serializers.ValidationError("Selected seat does not belong to the specified event.")

        # Ensure that the selected seat is not already booked
        if Booking.objects.filter(seat=seat, status='CONFIRMED').exists():
            raise serializers.ValidationError({
                "seat": "Seat is already booked."
            })

        # Ensure that the idempotency key is unique for the user
        request = self.context.get('request')
        user = request.user if request else None
        if idempotency_key and user and Booking.objects.filter(user=user, idempotency_key=idempotency_key).exists():
            raise serializers.ValidationError("A booking with this idempotency key already exists for the user.")

        return data    

class BookingReadSerializer(serializers.ModelSerializer):
    event = EventSummerySerializer(read_only=True)
    seat = SeatSummerySerializer(read_only=True)
    payment = PaymentReadSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = [
            'id',
            'event',
            'seat',
            "payment",
            'status',
            'amount',
            'created_at',
        ]
        read_only_fields = fields