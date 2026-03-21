from django.db import models
from django.db.models import Q

from users.models import User
from events.models import Event, Seat

# Create your models here.
class Booking(models.Model):

    STATUS_CHOICES = [
        ('CONFIRMED', 'Confirmed'),
        ('PENDING', 'Pending'),
        ('CANCELLED', 'Cancelled'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="bookings")
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, related_name="bookings")
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

    def __str__(self):
        return f"Booking {self.id} (Seat {self.seat_id}, User {self.user_id})"
    
    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['idempotency_key', 'user'],
                name='unique_idempotency_key_per_user'
            ),
            models.UniqueConstraint(
                fields=['seat'],
                condition=Q(status='CONFIRMED'),
                name='unique_confirmed_seat_booking'
            )
        ]
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['event']),
            models.Index(fields=['seat']),
        ]