import logging
import random
import uuid
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache

from .models import Payment
from bookings.models import Booking

logger = logging.getLogger(__name__)

class PaymentService:

    @staticmethod
    def invalidate_event_cache(event_id):
        """Invalidate caches related to the event."""
        cache.delete(f"event:{event_id}")
        cache.delete_pattern("events:list:*")
        logger.info(
            "event_cache_invalidated",
            extra={
                "event": "event_cache_invalidated",
                "event_id": event_id,
                "source": "payment_service",
            }
        )

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
                logger.warning(
                    "payment_booking_missing",
                    extra={
                        "event": "payment_booking_missing",
                        "booking_id": booking_id,
                    }
                )
                raise ValueError("Booking not found")

            now = timezone.now()

            # Expiry check (normalize early)
            if booking.expires_at < now:
                booking.status = "EXPIRED"
                booking.save(update_fields=["status", "updated_at"])
                error_message = "Booking has expired"
                logger.warning(
                    "payment_booking_expired",
                    extra={
                        "event": "payment_booking_expired",
                        "booking_id": booking.id,
                        "event_id": booking.event_id,
                    }
                )

            # Retry limit check
            elif booking.retry_count >= 3:
                booking.status = "EXPIRED"
                booking.save(update_fields=["status", "updated_at"])
                error_message = "Retry limit exceeded"
                logger.warning(
                    "payment_retry_limit_exceeded",
                    extra={
                        "event": "payment_retry_limit_exceeded",
                        "booking_id": booking.id,
                        "event_id": booking.event_id,
                        "retry_count": booking.retry_count,
                    }
                )

            if error_message:
                payment = None
            else:
                # Already confirmed → idempotent return
                if booking.status == "CONFIRMED":
                    logger.info(
                        "payment_idempotent_confirmed_booking",
                        extra={
                            "event": "payment_idempotent_confirmed_booking",
                            "booking_id": booking.id,
                            "event_id": booking.event_id,
                        }
                    )
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
                    logger.info(
                        "payment_succeeded",
                        extra={
                            "event": "payment_succeeded",
                            "booking_id": booking.id,
                            "event_id": booking.event_id,
                            "payment_id": payment.id,
                        }
                    )

                else:
                    booking.retry_count += 1
                    payment.status = "FAILED"

                    # Decide next state
                    if booking.retry_count >= 3 or booking.expires_at < now:
                        booking.status = "EXPIRED"
                    else:
                        booking.status = "FAILED"
                    logger.warning(
                        "payment_failed",
                        extra={
                            "event": "payment_failed",
                            "booking_id": booking.id,
                            "event_id": booking.event_id,
                            "payment_id": payment.id,
                            "retry_count": booking.retry_count,
                            "booking_status": booking.status,
                        }
                    )

                payment.save(update_fields=["status", "transaction_id", "updated_at"])
                booking.save(update_fields=["status", "retry_count", "updated_at"])

                if event_id_to_invalidate is not None:   # ADDED
                    transaction.on_commit(
                        lambda: PaymentService.invalidate_event_cache(event_id_to_invalidate)
                    )
                    
        if error_message:
            raise ValueError(error_message)

        return payment
       
