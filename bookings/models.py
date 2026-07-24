"""
A Booking is one user's reservation for one or more Seats at an Event.
Booking and BookingSeat are deliberately two tables (not seat_ids on
Booking directly) so a single seat's claim can be tracked, locked, and
released independently — see BookingSeat.is_active below for why that
matters.
"""

from django.db.models import Q
from django.utils import timezone
from django.db import models, transaction

from users.models import User
from events.models import Event, Seat


class Booking(models.Model):
    """
    Status lifecycle: PENDING (just created, payment in flight) ->
    CONFIRMED (payment succeeded) or FAILED (payment failed, retriable via
    the AI assistant's prepare_payment_retry) or EXPIRED (nobody confirmed
    in time — see workflows/tasks.py's cleanup task). A CONFIRMED booking
    can later become CANCELLED by the user.
    """

    STATUS_CHOICES = [
        ('CONFIRMED', 'Confirmed'),
        ('PENDING', 'Pending'),
        ('CANCELLED', 'Cancelled'),
        ('FAILED', 'Failed'), # Payment failed but retriable
        ('EXPIRED', 'Expired'), # Booking expired without confirmation
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="bookings")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING',
        db_index=True
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # Lets the same confirm-booking click be safely retried (e.g. a network
    # blip and the user clicks Confirm twice) without creating two
    # bookings — see BookingService.create_booking_for_user's idempotency
    # check, and unique_idempotency_key_per_user below which enforces it.
    idempotency_key = models.CharField(max_length=255, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # For a PENDING booking, the deadline by which payment must be
    # confirmed before it's treated as abandoned (see workflows/tasks.py).
    # Meaningless once CONFIRMED/CANCELLED/FAILED.
    expires_at = models.DateTimeField(default=timezone.now)
    retry_count = models.IntegerField(default=0)

    def __str__(self):
        return f"Booking {self.id} (Event {self.event_id}, User {self.user_id}) - {self.status}"
    
    def release_seats(self):
        """
        Release this booking's claim on its seats — flips is_active to
        False rather than deleting the rows, so booking history still shows
        every seat this booking ever held. Call this whenever the booking
        transitions to a state that no longer holds its seats (CANCELLED,
        EXPIRED).
        
        Also broadcasts the live seat update and invalidates the event
        cache. So that no missed seat release can leave the event's seat map or cache stale.
        
        transaction.on_commit is safe here regardless of whether the
        caller has an open atomic block: Django fires it immediately if
        none is active, and defers it to the real commit if one is
        (verified directly) — so this doesn't need to know which of its
        callers wraps it in a transaction and which doesn't.
        """
        from events.broadcasts import broadcast_seats_update_for_booking
        from events.caching import invalidate_event_cache
        
        # Mark the booking's seats as no longer active, so they can be claimed by someone else.
        self.booking_seats.update(is_active=False)
        # Broadcast the seat release to any live clients watching this event.
        transaction.on_commit(lambda: broadcast_seats_update_for_booking(self, "available"))
        # Invalidate the event cache so that any cached seat map or availability data is refreshed.
        transaction.on_commit(lambda: invalidate_event_cache(self.event_id))

    def seat_display(self):
        """
        Presentation-only summary of this booking's seats — for showing
        to a user or the AI (confirmation cards, emails, chat responses),
        never for identity/lookup. Includes inactive seats too
        (release_seats doesn't delete rows, so a cancelled booking's
        history is still describable here).

        Deliberately doesn't delegate to EventService.describe_seats:
        that method looks up display_label by re-querying Seat, which
        would waste a query here since booking_seats__seat is typically
        already prefetched (see BookingService.get_user_bookings) — this
        reads straight off the already-loaded Seat objects instead.
        """
        is_general_admission = self.event.is_general_admission
        booking_seats = list(self.booking_seats.all())
        return {
            "is_general_admission": is_general_admission,
            "seat_labels": [] if is_general_admission else [
                bs.seat.display_label or str(bs.seat.seat_number) for bs in booking_seats
            ],
            "seat_count": len(booking_seats),
        }
    
    class Meta:
        ordering = ['-created_at']

        constraints = [
            models.UniqueConstraint(
                fields=['idempotency_key', 'user'],
                name='unique_idempotency_key_per_user'
            ),
            models.CheckConstraint(
                condition=Q(retry_count__gte=0),
                name='retry_count_non_negative'
            )
        ]

        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['event']),
            models.Index(fields=['status']),
            models.Index(fields=['expires_at']),
        ]
        

class BookingSeat(models.Model):
    """Join row: one seat claimed by one booking. on_delete=PROTECT on
    `seat` means Django refuses to delete a Seat (and therefore its
    parent Event) while any BookingSeat still references it — including
    inactive/historical ones, since PROTECT doesn't check is_active. In
    practice this means an Event can't be deleted once it has ever had a
    single booking, confirmed or not (verified directly; see the note on
    EventViewSet.perform_destroy in events/views.py)."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="booking_seats")
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name="booking_seats")
    # True while this row represents a live claim on the seat. Flipped to
    # False (never deleted) once the booking becomes CANCELLED/EXPIRED, so
    # booking history keeps showing every seat a booking ever involved.
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['booking', 'seat'],
                name='unique_seat_per_booking'
            ),
            # Partial constraint: a seat can only have one *active* claim at
            # a time. Postgres can't condition this on booking.status (a
            # joined table's column), which is exactly why is_active exists
            # here — a plain field on this same table the index can check.
            models.UniqueConstraint(
                fields=['seat'],
                condition=Q(is_active=True),
                name='unique_seat_claim'
            )
        ]

        indexes = [
            models.Index(fields=['booking']),
            models.Index(fields=['seat']),
        ]
