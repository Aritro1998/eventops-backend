from rest_framework import serializers

from .models import Booking, BookingSeat
from events.models import Seat
from events.serializers import EventSummarySerializer, SeatSummarySerializer
from payments.serializers import PaymentReadSerializer

class BookingWriteSerializer(serializers.ModelSerializer):
    """
    Accepts a list of Seat ids for a given event. This only validates
    shape (seats belong to the event, no duplicates) — it does NOT check
    availability; that's BookingService's job under a row lock, since
    availability can change between this validation and the actual write
    (see BookingService.create_booking).
    """

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
    """Thin wrapper so BookingReadSerializer.seats returns Seat details
    (via the nested SeatSummarySerializer) rather than raw BookingSeat
    join-row fields like is_active."""
    seat = SeatSummarySerializer(read_only=True)

    class Meta:
        model = BookingSeat
        fields = [
            "seat"
        ]


class BookingReadSerializer(serializers.ModelSerializer):
    """The shape returned by get_my_bookings, and the booking-detail API.
    `source="booking_seats"` reads through the join table but only shows
    every seat currently linked — including inactive/historical ones,
    since this doesn't filter on is_active. Callers that only want live
    seats should filter separately.

    is_general_admission/seat_labels/seat_count (via Booking.seat_display)
    are what the AI assistant's system prompt is told to use when
    describing a booking — `seats` alone would show raw seat_number
    values that mean nothing for a general admission booking.
    """
    event = EventSummarySerializer(read_only=True)
    payment = PaymentReadSerializer(read_only=True)
    seats = BookingSeatReadSerializer(source="booking_seats", many=True, read_only=True)
    is_general_admission = serializers.SerializerMethodField()
    seat_labels = serializers.SerializerMethodField()
    seat_count = serializers.SerializerMethodField()

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
            'is_general_admission',
            'seat_labels',
            'seat_count',
        ]
        read_only_fields = fields

    def get_is_general_admission(self, obj) -> bool:
        return obj.seat_display()["is_general_admission"]

    def get_seat_labels(self, obj) -> list[str]:
        return obj.seat_display()["seat_labels"]

    def get_seat_count(self, obj) -> int:
        return obj.seat_display()["seat_count"]
        
