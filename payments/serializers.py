from rest_framework import serializers
from .models import Payment


class PaymentReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "status",
            "transaction_id"
        ]