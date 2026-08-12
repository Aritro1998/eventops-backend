"""
Response-shape declarations for the confirm/dismiss action views in
views.py, purely for drf-spectacular's schema generation. These views
return plain dicts assembled from service-layer functions (see
ai_assistant/actions/), not serialized model instances, so there was
nothing for automatic schema introspection to find without these.
"""

from rest_framework import serializers


class PendingActionSerializer(serializers.Serializer):
    type = serializers.CharField(help_text="e.g. 'confirm_pending_booking', 'keep_booking'")
    label = serializers.CharField(help_text="Button text for the frontend, e.g. 'Confirm booking'")


class BookingOutcomeSerializer(serializers.Serializer):
    """The shape of the "booking"/"draft" dict every action view below
    returns — assembled by the ai_assistant/actions/ functions from
    Booking.seat_display() plus a few extra fields, not a ModelSerializer
    output, so field presence varies slightly by action (e.g.
    expires_at/attempts_remaining only appear after a payment attempt)."""
    booking_id = serializers.IntegerField()
    event_name = serializers.CharField()
    status = serializers.CharField(required=False)
    amount = serializers.CharField(required=False)
    event_start_time = serializers.DateTimeField(required=False)
    is_general_admission = serializers.BooleanField()
    seat_labels = serializers.ListField(child=serializers.CharField(), required=False)
    seat_count = serializers.IntegerField(required=False)
    expires_at = serializers.DateTimeField(required=False)
    attempts_remaining = serializers.IntegerField(required=False)


class ActionOutcomeSerializer(serializers.Serializer):
    """Response shape for ConfirmPendingBookingActionView,
    ConfirmCancellationActionView (resolved case), DismissCancellationActionView,
    ConfirmPaymentRetryActionView, and DismissPaymentRetryActionView."""
    response = serializers.CharField(help_text="Human-readable outcome text, also persisted into chat history")
    booking = BookingOutcomeSerializer()
    actions = PendingActionSerializer(many=True)


class CancelDraftOutcomeSerializer(serializers.Serializer):
    """Response shape for CancelPendingBookingActionView specifically —
    "draft" instead of "booking", since the seat hold is being discarded,
    not turned into a real booking."""
    response = serializers.CharField()
    draft = BookingOutcomeSerializer()
    actions = PendingActionSerializer(many=True)
