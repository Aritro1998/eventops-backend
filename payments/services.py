import random
import uuid
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404

from .models import Payment
from bookings.models import Booking

class PaymentService:

    @staticmethod
    def process_payment(booking_id):

        # success = random.choice([True] * 9 + [False])
        success = random.choice([True, False])

        with transaction.atomic():

            # Lock booking row
            try:
                booking = Booking.objects.select_for_update().get(id=booking_id)
            except Booking.DoesNotExist:
                raise ValueError("Booking not found")

            now = timezone.now()

            # Expiry check (normalize early)
            if booking.expires_at < now:
                booking.status = "EXPIRED"
                booking.save()
                raise ValueError("Booking has expired")

            # Retry limit check
            if booking.retry_count >= 3:
                booking.status = "EXPIRED"
                booking.save()
                raise ValueError("Retry limit exceeded")

            # Already confirmed → idempotent return
            if booking.status == "CONFIRMED":
                return Payment.objects.filter(
                    booking=booking,
                    status="SUCCESS"
                ).first()

            # Lock existing payment (if any)
            payment = Payment.objects.select_for_update().filter(booking=booking).first()

            # Create if not exists
            if not payment:
                payment = Payment.objects.create(
                    booking=booking,
                    amount=booking.amount,
                    status="PENDING"
                )
            else:
                # If already successful → return
                if payment.status == "SUCCESS":
                    return payment

                # Reset for retry
                payment.status = "PENDING"

            # Simulate payment
            if success:
                payment.status = "SUCCESS"
                payment.transaction_id = str(uuid.uuid4())
                booking.status = "CONFIRMED"

            else:
                booking.retry_count += 1
                payment.status = "FAILED"

                # Decide next state
                if booking.retry_count >= 3 or booking.expires_at < now:
                    booking.status = "EXPIRED"
                else:
                    booking.status = "FAILED"

            payment.save()
            booking.save()

        return payment
       
