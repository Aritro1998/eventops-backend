"""
Read/write are deliberately split into separate serializers for Event:
EventReadSerializer exposes computed fields (available_seats,
is_general_admission) that only make sense coming out of the annotated
queryset in EventService.get_events_with_available_seats, while
EventWriteSerializer only accepts the raw fields an organizer actually
submits. Reusing one serializer for both would mean either exposing
write access to derived fields, or awkwardly making them optional.
"""

from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers
from .models import Event, Seat

class EventReadSerializer(serializers.ModelSerializer):
    """
    available_seats and is_general_admission are both computed, not stored
    columns — available_seats depends on the request-time annotation from
    EventService (a per-request SQL COUNT, not a query-per-object
    SerializerMethodField, which would be much slower for a list of
    events); is_general_admission mirrors the Event.is_general_admission
    property. Both must be declared here AND listed in Meta.fields below —
    DRF raises an AssertionError at request time if a declared field is
    missing from fields, a real bug this project hit once.
    """
    available_seats = serializers.IntegerField(read_only=True)
    is_general_admission = serializers.BooleanField(read_only=True)
    venue_name = serializers.CharField(source='venue.name', read_only=True, default=None)
    space_name = serializers.CharField(source='space.name', read_only=True, default=None)

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
            'venue',
            'venue_name',
            'space',
            'space_name',
            'is_general_admission',
        ]


class EventSummarySerializer(serializers.ModelSerializer):
    """Minimal event shape for the AI assistant's search_events tool — just
    enough for the model to identify and disambiguate events, kept small
    since this gets fed straight into the LLM's context window."""
    class Meta:
        model = Event
        fields = [
            'id',
            'name',
            'start_time',
            'end_time',
            'price',
        ]


class SeatSummarySerializer(serializers.ModelSerializer):
    """
    Minimal seat shape for the AI assistant's get_available_seats tool.
    Only used for labeled events — general admission events return a plain
    count instead (see ai_assistant/tools/event_tools.py).

    `label` is always present, falling back to the plain seat_number for
    a custom event with no Space (no display_label). This is what the
    model shows the user and what prepare_booking expects back — it
    never has to compute or guess a label-to-seat_number mapping itself,
    it only ever echoes back a label it was already shown.
    """
    label = serializers.SerializerMethodField()

    class Meta:
        model = Seat
        fields = [
            'id',
            'seat_number',
            'label',
        ]

    def get_label(self, obj):
        return obj.display_label or str(obj.seat_number)


class EventWriteSerializer(serializers.ModelSerializer):
    """Used for create/update via the API (EventViewSet). created_by is
    read-only here and set explicitly in create()/perform_create — never
    trust a client-submitted created_by."""
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
            'venue',
            'space',
            'is_archived',
        ]

    def validate(self, data):
        start = data.get('start_time')
        end = data.get('end_time')
        if start and end and end <= start:
            raise serializers.ValidationError("End time must be after start time.")
        if start and start < timezone.now():
            raise serializers.ValidationError("Event cannot start in the past.")

        # Reuse Event.clean() so the API enforces the exact same venue/space
        # consistency and booking-safety rules as the admin panel, instead of
        # duplicating that logic here.
        instance = self.instance or Event()
        for attr, value in data.items():
            setattr(instance, attr, value)
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, 'message_dict') else e.messages)

        return data
    
    def validate_total_seats(self, value):
        """DRF calls validate_<field> before the object-level validate()
        above. Note: if a space is selected, this value gets silently
        overwritten by EventService.sync_seats_on_create/update anyway —
        it's still required as a positive placeholder for now (see the
        note in events/admin.py's docstring)."""
        if value <= 0:
            raise serializers.ValidationError("Total seats must be a positive integer.")
        return value

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price must be a non-negative value.")
        return value

    def create(self, validated_data):
        """created_by comes from the authenticated request, never from the
        client payload — read_only_fields above already blocks it from
        validated_data, this is the actual assignment."""
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
    
    

    