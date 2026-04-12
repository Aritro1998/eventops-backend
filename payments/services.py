import random
import uuid
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache

from .models import Payment
from bookings.models import Booking

class PaymentService:

    @staticmethod
    def invalidate_event_cache(event_id):
        """Invalidate caches related to the event."""
        cache.delete(f"event:{event_id}")
        cache.delete_pattern("events:list:*")

    @staticmethod
    def process_payment(booking_id):

        # success = random.choice([True] * 9 + [False])
        success = random.choice([True, False])
        error_message = None
        event_id_to_invalidate = None

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
                booking.save(update_fields=["status", "updated_at"])
                error_message = "Booking has expired"

            # Retry limit check
            elif booking.retry_count >= 3:
                booking.status = "EXPIRED"
                booking.save(update_fields=["status", "updated_at"])
                error_message = "Retry limit exceeded"

            if error_message:
                payment = None
            else:
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
                    event_id_to_invalidate = booking.event_id

                else:
                    booking.retry_count += 1
                    payment.status = "FAILED"

                    # Decide next state
                    if booking.retry_count >= 3 or booking.expires_at < now:
                        booking.status = "EXPIRED"
                    else:
                        booking.status = "FAILED"

                payment.save(update_fields=["status", "transaction_id", "updated_at"])
                booking.save(update_fields=["status", "retry_count", "updated_at"])

                if event_id_to_invalidate is not None:   # ADDED
                    transaction.on_commit(
                        lambda: PaymentService.invalidate_event_cache(event_id_to_invalidate)
                    )
                    
        if error_message:
            raise ValueError(error_message)

        return payment
       
