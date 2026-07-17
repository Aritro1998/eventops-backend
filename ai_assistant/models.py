from django.db import models
from users.models import User
from events.models import Event
from django.utils import timezone
from bookings.models import Booking

from datetime import timedelta


def get_pending_booking_expiry():
    from bookings.services import BookingService

    return timezone.now() + timedelta(minutes=BookingService.EXPIRY_MINUTES)


# Create your models here.
class PendingBooking(models.Model):
    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="pending_booking_user")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="pending_booking_event")
    seat_numbers = models.JSONField(default=list)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True, default=get_pending_booking_expiry)
    

class PendingBookingCancellation(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='pending_cancellation_user')
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='pending_cancellations')
    created_at = models.DateTimeField(auto_now_add=True)
    
