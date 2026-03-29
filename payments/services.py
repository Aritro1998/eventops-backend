import random
import uuid
from django.db import transaction
from django.shortcuts import get_object_or_404

from .models import Payment
from bookings.models import Booking

class PaymentService:

    @staticmethod
    def process_payment(booking_id):

        success = random.choice([True]*9 + [False])

        with transaction.atomic():
            # Lock the booking record to prevent concurrent modifications
            booking = get_object_or_404(Booking.objects.select_for_update(), id=booking_id)

             # Idempotency protection
            if hasattr(booking, "payment"):
                return booking.payment

            # Create payment (PENDING)
            payment = Payment.objects.create(
                booking=booking,
                amount=booking.amount,
                status="PENDING"
            )

            # Simulate payment outcome
            if success:
                payment.status = "SUCCESS"
                payment.transaction_id = str(uuid.uuid4())
                booking.status = "CONFIRMED"
            else:
                payment.status = "FAILED"
                booking.status = "FAILED"

            # Save changes
            payment.save()
            booking.save()

        try:
            return payment
        except Exception as e:
            pass

