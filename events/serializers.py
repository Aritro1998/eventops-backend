from time import timezone

from rest_framework import serializers
from .models import Event, Seat

class EventReadSerializer(serializers.ModelSerializer):

    # This is a derived field that calculates available seats on the fly (Non-optimized)
    # available_seats = serializers.SerializerMethodField()

    # This method is triggered from the SerializerMethodField to calculate available seats (Non-optimized)
    # def get_available_seats(self, obj):
    #     return obj.seats.filter(is_booked=False).count()

    # This is an optimized field that relies on the annotation in the queryset to get available seats
    available_seats = serializers.IntegerField(read_only=True)

    class Meta:
        model = Event
        fields = [
            'id',
            'name',
            'start_time',
            'end_time',
            'total_seats',
            'available_seats',
            'price',
        ]


class EventSummerySerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            'id',
            'name',
            'start_time',
            'end_time',
            'price',
        ]


class SeatSummerySerializer(serializers.ModelSerializer):
    class Meta:
        model = Seat
        fields = [
            'id',
            'seat_number',
        ]


class EventWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        read_only_fields = ['id', 'created_by']
        fields = [
            'name',
            'description',
            'start_time',
            'end_time',
            'total_seats',
            'price',
        ]

    def validate(self, data):
        start = data.get('start_time')
        end = data.get('end_time')
        if start and end and end <= start:
            raise serializers.ValidationError("End time must be after start time.")
        if start and start < timezone.now():
            raise serializers.ValidationError("Event cannot start in the past.")
        return data
    
    def validate_total_seats(self, value):
        if value <= 0:
            raise serializers.ValidationError("Total seats must be a positive integer.")
        return value
    
    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price must be a non-negative value.")
        return value
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
    
    

    