import logging
import random
import uuid
from django.utils import timezone
from django.db import transaction

from .models import Payment
from bookings.models import Booking
from bookings.services import BookingService

logger = logging.getLogger(__name__)

class PaymentService:
    """Simulated payment processing — see the module docstring in
    payments/models.py for why. process_payment is the single entry point
    used by both a fresh booking (BookingService.create_booking_for_user)
    and a manual retry (the AI assistant's payment-retry flow, or
    BookingRetryPaymentView)."""

    MAX_RETRIES = 3

    @staticmethod
    def process_payment(booking_id):
        """
        Attempt (or re-attempt) payment for a booking. Coin-flip success/
        failure since there's no real gateway (see class docstring).

        State machine, in order of precedence:
        1. Booking already expired, or already hit MAX_RETRIES -> booking
           becomes EXPIRED, seats released, raises ValueError. No payment
           attempt happens at all.
        2. Booking already CONFIRMED -> idempotent no-op, returns the
           existing successful Payment without touching anything.
        3. Otherwise, attempt payment:
           - success -> Payment SUCCESS, Booking CONFIRMED.
           - failure -> retry_count += 1; Booking becomes EXPIRED if that
             was the last allowed retry (or the hold already expired),
             otherwise FAILED (still retriable by calling this again).

        The whole thing runs under a row lock on the Booking (and, once
        created, the Payment) so two retry-payment clicks racing each
        other can't both succeed or both increment retry_count past the
        limit.
        """
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
            if booking.expires_at and booking.expires_at < now:
                booking.status = "EXPIRED"
                booking.save(update_fields=["status", "updated_at"])
                # unique_seat_claim only enforces uniqueness while
                # is_active=True, so an expired booking releases its seats by
                # flipping this flag rather than deleting the rows.
                booking.release_seats()
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
            elif booking.retry_count >= PaymentService.MAX_RETRIES:
                booking.status = "EXPIRED"
                booking.save(update_fields=["status", "updated_at"])
                booking.release_seats()
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
                    if booking.retry_count >= PaymentService.MAX_RETRIES or (booking.expires_at and booking.expires_at < now):
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

                if booking.status == "EXPIRED":
                    booking.release_seats()

                if event_id_to_invalidate is not None:
                    transaction.on_commit(
                        lambda event_id=event_id_to_invalidate:
                            BookingService.invalidate_event_cache(event_id)
                    )
                    
        if error_message:
            raise ValueError(error_message)

        return payment
       
