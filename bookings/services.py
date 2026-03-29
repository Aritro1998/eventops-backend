from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404

from .models import Booking
from events.models import Seat


class BookingService:
    """
    Service layer responsible for all booking-related business logic.

    Why this exists:
    - Keeps views thin (HTTP layer only)
    - Makes logic reusable (API, Celery, tests)
    - Prepares system for async workflows
    """

    @staticmethod
    def get_existing_booking(user, key):
        """
        Fetch an existing booking for idempotency.

        Uses select_related to avoid N+1 queries when serializing.
        """
        return Booking.objects.select_related("event", "seat").filter(
            user=user,
            idempotency_key=key
        ).first()

    @staticmethod
    def create_booking(user, validated_data, key):
        """
        Core booking creation logic with:
        - Concurrency safety (row locking)
        - Idempotency
        - Transaction management

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
                seat = get_object_or_404(
                    Seat.objects.select_for_update(),
                    id=validated_data["seat"].id
                )

                # Idempotency re-check inside transaction (race condition safe)
                if key:
                    existing_booking = BookingService.get_existing_booking(user, key)
                    booking = existing_booking

                # Create booking only if it doesn't already exist
                if booking is None:

                    # Prevent double booking of same seat
                    if Booking.objects.filter(
                        seat=seat,
                        status="CONFIRMED"
                    ).exists():
                        seat_unavailable = True
                    else:
                        event = validated_data["event"]

                        # IMPORTANT: derive amount from backend (never trust client)
                        amount = event.price

                        booking = Booking.objects.create(
                            user=user,
                            event=event,
                            seat=seat,
                            amount=amount,
                            idempotency_key=key,
                            status="PENDING"
                        )

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