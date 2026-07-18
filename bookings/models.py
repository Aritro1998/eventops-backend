from django.db import models
from django.db.models import Q
from django.utils import timezone

from users.models import User
from events.models import Event, Seat

# Create your models here.
class Booking(models.Model):

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
    idempotency_key = models.CharField(max_length=255, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
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
        """
        self.booking_seats.update(is_active=False)
    
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
