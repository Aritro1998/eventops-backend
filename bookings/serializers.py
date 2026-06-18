from rest_framework import serializers

from .models import Booking, BookingSeat
from events.models import Seat
from events.serializers import EventSummarySerializer, SeatSummarySerializer
from payments.serializers import PaymentReadSerializer

class BookingWriteSerializer(serializers.ModelSerializer):
    
    seats = serializers.PrimaryKeyRelatedField(
        queryset=Seat.objects.all(),
        many=True,
    )
    
    class Meta:
        model = Booking
        fields = [
            'event',
            'seats',
            'idempotency_key',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at', 'status', 'amount']

    def validate(self, data):
        event = data.get('event')
        seats = data.get('seats', [])
        
        # Validate that all seats belong to the specified event
        for seat in seats:
            if seat.event_id != event.id:
                raise serializers.ValidationError({
                    "seats": f"Seat {seat.id} does not belong to the specified event."
                })

        # Validate that there are no duplicate seat ids in the request
        seat_ids = [seat.id for seat in seats]
        if len(seat_ids) != len(set(seat_ids)):
            raise serializers.ValidationError(
                "Duplicate seat ids are not allowed."
            )
        
        return data    


class BookingSeatReadSerializer(serializers.ModelSerializer):
    seat = SeatSummarySerializer(read_only=True)

    class Meta:
        model = BookingSeat
        fields = [
            "seat"
        ]


class BookingReadSerializer(serializers.ModelSerializer):
    event = EventSummarySerializer(read_only=True)
    payment = PaymentReadSerializer(read_only=True)
    seats = BookingSeatReadSerializer(source="booking_seats", many=True, read_only=True)

    class Meta:
        model = Booking
        fields = [
            'id',
            'event',
            'seats',
            "payment",
            'status',
            'amount',
            'created_at',
        ]
        read_only_fields = fields
        
