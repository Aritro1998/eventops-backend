import logging

from datetime import timedelta
from django.db.models import Q
from django.utils import timezone
from django.db import transaction, IntegrityError

from .models import Booking, BookingSeat
from events.models import Seat
from workflows.models import WorkflowJob
from workflows.services import schedule_job
from events.caching import invalidate_event_cache
from events.broadcasts import broadcast_seat_update

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
    def get_booking_for_user(booking_id, user):
        """
        Fetch a single booking a user owns, fully loaded for detail/action use.

        Returns None if it doesn't exist or belongs to someone else — callers
        decide how to surface that (404 for HTTP views, ValueError for AI
        tools), since each call site has a different error-reporting
        convention.
        """
        return (
            Booking.objects
            .select_related("event", "payment")
            .prefetch_related("booking_seats__seat")
            .filter(id=booking_id, user=user)
            .first()
        )
    
    @staticmethod
    def active_booking_q(prefix = 'booking__'):
        """
        Q object matching an actively-held booking: CONFIRMED always counts
        as taken, PENDING/FAILED counts only while the hold hasn't expired
        yet. `prefix` lets this be reused from different starting points in
        the relation graph — default 'booking__' covers queries rooted at
        BookingSeat (the common case); pass a longer path like
        'seats__booking_seats__booking__' when starting from Event instead.
        """
        now = timezone.now()
        return (
            Q(**{f'{prefix}status': 'CONFIRMED'}) |
            Q(**{
                f'{prefix}status__in': ['PENDING', 'FAILED'],
                f'{prefix}expires_at__gt': now,
            })
        )
    
    @staticmethod
    def is_seat_available(seat):
        """
        Check if seat is available.

        A seat is considered unavailable if there exists a booking:
        - with CONFIRMED status OR
        - with PENDING / FAILED status AND not expired
        """

        return not BookingSeat.objects.filter(
            seat=seat
        ).filter(
           BookingService.active_booking_q()
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

        return set(
            BookingSeat.objects.filter(
                seat_id__in=seat_ids
            ).filter(
                BookingService.active_booking_q()
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
                        
                        # Tell the live seat picker these seats just got
                        # locked - status is always "locked" here, since
                        # a freshly created booking is always PENDING;
                        # if payment succeeds moments later, that's a
                        # separate transition, broadcast from
                        # PaymentService.process_payment instead.
                        for seat in seats:
                            transaction.on_commit(
                                lambda seat_number=seat.seat_number:
                                    broadcast_seat_update(
                                        event.id, seat_number, "locked"
                                    )
                            )
                        
                        # Invalidate event cache after successful booking creation
                        transaction.on_commit(
                            lambda: invalidate_event_cache(event.id)
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
        """
        Higher-level wrapper around create_booking, used by the AI
        assistant's confirm_pending_booking action. Adds two things
        create_booking doesn't do itself: an unlocked fast-path
        idempotency check (create_booking still does the real,
        lock-protected one — this just avoids unnecessary work on a
        confirmed retry), and immediately kicking off payment processing
        for a freshly-created PENDING booking. create_booking stays lower-
        level and reusable (e.g. directly from BookingWriteSerializer)
        without forcing payment processing on every caller.
        """
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
            # every seat this booking ever held. release_seats() itself now
            # broadcasts the live update and invalidates the event cache,
            # so nothing further is needed here.
            booking.release_seats()

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
        """All bookings for a user, optionally narrowed to one status.
        Used by both the API's booking-list endpoint and the AI
        assistant's get_my_bookings tool."""
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
    
    @staticmethod
    def get_seat_statuses_for_event(event_id):
        """
        Return {seat_number: "available" | "locked" | "booked"} for every
        seat belonging to this event, in one query rather than one query per
        seat. This is what a client sees immediately upon connecting to the
        seat-picker WebSocket, before any live update has arrived yet.

        "booked" = a CONFIRMED booking holds this seat, permanently.
        "locked" = a PENDING/FAILED booking holds it and hasn't expired yet -
        someone else is mid-checkout; it releases automatically if they
        don't finish in time.
        "available" = neither of the above.
        """
        active_claims = (
            BookingSeat.objects
            .filter(seat__event_id=event_id)
            .filter(BookingService.active_booking_q())
            .values_list('seat__seat_number', 'booking__status')
        )

        claim_status_by_seat_number = {
            seat_number: ("booked" if booking_status == "CONFIRMED" else "locked")
            for seat_number, booking_status in active_claims
        }

        # seat_number is never shown to the user anywhere else in this
        # app (see get_system_prompt: "seat_number is an internal id —
        # never show it") - display_label is the public identity, with
        # the same string-of-seat_number fallback used everywhere else
        # (SeatSummarySerializer, EventService.describe_seats) for
        # plain-numbered events that have no display_label at all.
        # row/column (when present) let the frontend lay the grid out to
        # match the venue's actual shape instead of an arbitrary flow.
        seats = {}
        for seat_number, display_label, row, column in Seat.objects.filter(event_id=event_id).values_list(
            'seat_number', 'display_label', 'row', 'column'
        ):
            seats[seat_number] = {
                "label": display_label or str(seat_number),
                "row": row,
                "column": column,
                "status": claim_status_by_seat_number.get(seat_number, "available"),
            }

        return seats