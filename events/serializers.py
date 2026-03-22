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
            'available_seats'
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
        ]

    def validate(self, data):
        start = data.get('start_time')
        end = data.get('end_time')
        if start and end and end <= start:
            raise serializers.ValidationError("End time must be after start time.")
        return data
    
    def validate_total_seats(self, value):
        if value <= 0:
            raise serializers.ValidationError("Total seats must be a positive integer.")
        return value
    
    

    