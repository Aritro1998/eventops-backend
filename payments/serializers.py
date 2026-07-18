from rest_framework import serializers
from .models import Payment


class PaymentReadSerializer(serializers.ModelSerializer):
    """Nested inside BookingReadSerializer — deliberately minimal (just
    status/transaction_id), since a booking's own status already conveys
    most of what a caller needs."""
    class Meta:
        model = Payment
        fields = [
            "status",
            "transaction_id"
        ]