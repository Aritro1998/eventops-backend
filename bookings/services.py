import logging

from datetime import timedelta
from django.db.models import Q
from django.utils import timezone
from django.core.cache import cache
from django.db import transaction, IntegrityError

from .models import Booking, BookingSeat
from events.models import Seat
from workflows.models import WorkflowJob
from workflows.services import schedule_job

logger = logging.getLogger(__name__)


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
    def invalidate_event_cache(event_id):  
        cache.delete(f"event:{event_id}")
        if hasattr(cache, "delete_pattern"):
            cache.delete_pattern("events:list:*")
        logger.info(
            "event_cache_invalidated",
            extra={
                "event": "event_cache_invalidated",
                "event_id": event_id,
                "source": "booking_service",
            }
        )

    @staticmethod
    def get_existing_booking(user, key):
        """
        Fetch an existing booking for idempotency.

        Uses select_related to avoid N+1 queries when serializing.
        """
        return Booking.objects.select_related(
            "event", "payment"
        ).prefetch_related(
            "booking_seats__seat"
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

        return not BookingSeat.objects.filter(
            seat=seat
        ).filter(
            Q(booking__status="CONFIRMED") |
            Q(
                booking__status__in=["PENDING", "FAILED"],
                booking__expires_at__gt=now
            )
        ).exists()
        
    @staticmethod
    def get_unavailable_seat_ids(seat_ids):
        """
        Return a set of seat ids that are currently unavailable.

        A seat is unavailable if:
        - booking is CONFIRMED
        OR
        - booking is PENDING / FAILED and not expired
        """

        now = timezone.now()

        return set(
            BookingSeat.objects.filter(
                seat_id__in=seat_ids
            ).filter(
                Q(
                    booking__status="CONFIRMED"
                )
                |
                Q(
                    booking__status__in=["PENDING", "FAILED"],
                    booking__expires_at__gt=now
                )
            ).values_list(
                "seat_id",
                flat=True
            )
        )

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
                seats = list(
                    Seat.objects.select_for_update().filter(
                        id__in=validated_data["seats"]
                    )
                )
                
                # Extract seat ids from validated data (handles both seat objects and ids)
                requested_seat_ids = [
                    seat.id if hasattr(seat, "id") else seat 
                    for seat in validated_data["seats"]
                ]
                
                # Validate that at least one seat is requested
                if not requested_seat_ids:
                    raise ValueError(
                        "At least one seat must be selected."
                    )
                # Validate that all requested seat ids exist
                if len(seats) != len(requested_seat_ids):
                    raise ValueError("One or more seat ids are invalid.")
                # Validate that there are no duplicate seat ids in the request
                if len(set(requested_seat_ids)) != len(requested_seat_ids):
                    raise ValueError("Duplicate seat ids are not allowed.")
                # Validate that all seats belong to the specified event
                invalid_seat_ids = [
                    seat.id
                    for seat in seats
                    if seat.event_id != validated_data["event"].id
                ]

                if invalid_seat_ids:
                    raise ValueError(
                        f"Seats {invalid_seat_ids} do not belong to event "
                        f"{validated_data['event'].id}"
                    )
                
                # Idempotency re-check inside transaction (race condition safe)
                if key:
                    existing_booking = BookingService.get_existing_booking(user, key)
                    booking = existing_booking

                # Create booking only if it doesn't already exist
                if booking is None:

                    # Check seat availability (considers expiry)
                    unavailable_seat_ids = BookingService.get_unavailable_seat_ids(
                        requested_seat_ids
                    )
                        
                    if unavailable_seat_ids:
                        seat_unavailable = True
                        logger.warning(
                            "booking_create_seat_unavailable",
                            extra={
                                "event": "booking_create_seat_unavailable",
                                "user_id": user.id,
                                "event_id": validated_data["event"].id,
                                "requested_seat_ids": requested_seat_ids,
                                "unavailable_seat_ids": list(unavailable_seat_ids),
                            }
                        )
                    else:
                        event = validated_data["event"]

                        # IMPORTANT: derive amount from backend (never trust client)
                        amount = (event.price * len(seats))

                        # Set expiry time dynamically
                        expires_at = timezone.now() + timedelta(
                            minutes=BookingService.EXPIRY_MINUTES
                        )

                        booking = Booking.objects.create(
                            user=user,
                            event=event,
                            amount=amount,
                            idempotency_key=key,
                            status="PENDING",
                            expires_at=expires_at,
                            retry_count=0
                        )
                        
                        # Create BookingSeat entries in bulk for efficiency
                        BookingSeat.objects.bulk_create(
                            [BookingSeat(booking=booking, seat=seat) for seat in seats]
                        )
                        
                        # Invalidate event cache after successful booking creation
                        transaction.on_commit(
                            lambda: BookingService.invalidate_event_cache(event.id)
                        )

                        logger.info(
                            "booking_created",
                            extra={
                                "event": "booking_created",
                                "user_id": user.id,
                                "event_id": event.id,
                                "seat_ids": [seat.id for seat in seats],
                                "booking_id": booking.id,
                            }
                        )

                        job = WorkflowJob.objects.create(
                            job_type="BOOKING_EXPIRY",
                            booking=booking,
                            status="PENDING",
                            payload={
                                "booking_id": booking.id,
                            },
                        )

                        logger.info(
                            "workflow_job_scheduled",
                            extra={
                                "event": "workflow_job_scheduled",
                                "job_type": "BOOKING_EXPIRY",
                                "booking_id": booking.id,
                                "workflow_job_id": job.id,
                            }
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
    def create_booking_for_user(user, event, seat_ids, idempotency_key=None):
        # Fast idempotency check (no locking)
        if idempotency_key:
            existing = BookingService.get_existing_booking(user, idempotency_key)
            if existing:
                logger.info(
                    "booking_idempotency_hit",
                    extra={
                        "event": "booking_idempotency_hit",
                        "user_id": user.id,
                        "booking_id": existing.id,
                    }
                )
                return existing, True

        booking, seat_unavailable, is_existing = BookingService.create_booking(
            user=user,
            validated_data={
                "event": event,
                "seats": seat_ids,
            },
            key=idempotency_key,
        )
        
        if booking is None and not seat_unavailable:
            raise ValueError(
                "Booking creation failed unexpectedly."
            )

        if seat_unavailable:
            raise ValueError("Seat already booked")

        if booking.status == "PENDING":
            from payments.services import PaymentService
            PaymentService.process_payment(booking.id)

        booking = Booking.objects.select_related(
            "event", "payment"
        ).prefetch_related(
            "booking_seats__seat"
        ).get(id=booking.id)

        return booking, is_existing

    @staticmethod
    def cancel_booking(booking):
        """
        Cancel a booking safely.

        Business rules:
        - Only CONFIRMED bookings can be cancelled
        """

        if booking.status != "CONFIRMED":
            raise ValueError("Only CONFIRMED bookings can be CANCELLED.")

        event_id = booking.event_id

        with transaction.atomic():
            booking.status = "CANCELLED"
            booking.save(update_fields=["status", "updated_at"])
            # unique_seat_claim only enforces uniqueness while is_active=True,
            # so a cancelled booking releases its seats by flipping this flag
            # rather than deleting the rows — booking history still shows
            # every seat this booking ever held.
            booking.booking_seats.update(is_active=False)
            transaction.on_commit(
                lambda: BookingService.invalidate_event_cache(event_id)
            )

        logger.info(
            "booking_cancelled",
            extra={
                "event": "booking_cancelled",
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "event_id": booking.event_id,
                "seat_ids": [booking_seat.seat_id for booking_seat in booking.booking_seats.all()],
            }
        )

        return booking
    
    @staticmethod
    def get_user_bookings(user, status_filter=None):
        bookings = Booking.objects\
                    .filter(user=user)\
                    .select_related("event", "payment")\
                    .prefetch_related("booking_seats__seat")\
                    .order_by("-created_at")
                    
        if status_filter:
            valid_statuses = [choice[0] for choice in Booking.STATUS_CHOICES]

            if status_filter not in valid_statuses:
                raise ValueError(
                    f"Invalid status '{status_filter}'. "
                    f"Allowed values are: {', '.join(valid_statuses)}"
                )

            bookings = bookings.filter(
                status=status_filter
            )
            
        return bookings
