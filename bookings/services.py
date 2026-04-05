from django.db import transaction, IntegrityError
from django.utils import timezone
from datetime import timedelta

from .models import Booking
from events.models import Seat
from workflows.models import WorkflowJob
from workflows.services import schedule_job


class BookingService:
    """
    Service layer responsible for all booking-related business logic.

    Why this exists:
    - Keeps views thin (HTTP layer only)
    - Makes logic reusable (API, Celery, tests)
    - Prepares system for async workflows
    """

    EXPIRY_MINUTES = 15

    @staticmethod
    def get_existing_booking(user, key):
        """
        Fetch an existing booking for idempotency.

        Uses select_related to avoid N+1 queries when serializing.
        """
        return Booking.objects.select_related(
            "event", "seat", "payment"
        ).filter(
            user=user,
            idempotency_key=key
        ).first()

    @staticmethod
    def is_seat_available(seat):
        """
        Check if seat is available.

        A seat is considered unavailable if there exists a booking:
        - with CONFIRMED status OR
        - with PENDING / FAILED status AND not expired
        """

        now = timezone.now()

        return not Booking.objects.filter(
            seat=seat
        ).filter(
            status__in=["CONFIRMED", "PENDING", "FAILED"],
            expires_at__gt=now
        ).exists()

    @staticmethod
    def create_booking(user, validated_data, key):
        """
        Core booking creation logic with:
        - Concurrency safety (row locking)
        - Idempotency
        - Transaction management
        - Expiry handling

        IMPORTANT:
        - Pricing logic lives here (not in serializer)
        - This ensures client cannot tamper with amount

        Returns:
            booking: Booking instance
            seat_unavailable: bool
            is_existing: bool (idempotent retry)
        """

        booking = None
        seat_unavailable = False
        existing_booking = None

        try:
            with transaction.atomic():

                # Lock seat row to prevent concurrent booking attempts
                seat = Seat.objects.select_for_update().get(
                    id=validated_data["seat"].id
                )

                # Idempotency re-check inside transaction (race condition safe)
                if key:
                    existing_booking = BookingService.get_existing_booking(user, key)
                    booking = existing_booking

                # Create booking only if it doesn't already exist
                if booking is None:

                    # Check seat availability (considers expiry)
                    if not BookingService.is_seat_available(seat):
                        seat_unavailable = True
                    else:
                        event = validated_data["event"]

                        # IMPORTANT: derive amount from backend (never trust client)
                        amount = event.price

                        # Set expiry time dynamically
                        expires_at = timezone.now() + timedelta(
                            minutes=BookingService.EXPIRY_MINUTES
                        )

                        booking = Booking.objects.create(
                            user=user,
                            event=event,
                            seat=seat,
                            amount=amount,
                            idempotency_key=key,
                            status="PENDING",
                            expires_at=expires_at,
                            retry_count=0
                        )

                        job = WorkflowJob.objects.create(
                            job_type="BOOKING_EXPIRY",
                            booking=booking,
                            status="PENDING",
                            payload={
                                "booking_id": booking.id,
                            },
                        )

                        schedule_job(job, delay_seconds=BookingService.EXPIRY_MINUTES * 60)

        except IntegrityError:
            # Final safety net for idempotency (DB constraint)
            if key:
                existing = BookingService.get_existing_booking(user, key)
                if existing:
                    return existing, False, True
            raise

        return booking, seat_unavailable, existing_booking is not None

    @staticmethod
    def cancel_booking(booking):
        """
        Cancel a booking safely.

        Business rules:
        - Only CONFIRMED bookings can be cancelled
        """

        if booking.status != "CONFIRMED":
            raise ValueError("Only CONFIRMED bookings can be CANCELLED.")

        booking.status = "CANCELLED"
        booking.save()

        return booking