from django.db import models
from users.models import User
from events.models import Event
from django.utils import timezone
from bookings.models import Booking

from datetime import timedelta


def get_pending_action_expiry():
    from bookings.services import BookingService

    return timezone.now() + timedelta(minutes=BookingService.EXPIRY_MINUTES)


# Create your models here.
class PendingActionBase(models.Model):
    """
    Shared shape for every "AI staged this, a human must confirm it" row —
    PendingBooking, PendingBookingCancellation, PendingPaymentRetry all need
    identical expiry tracking, just against different underlying data.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='%(class)s_user')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True, default=get_pending_action_expiry)
    
    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    class Meta:
        abstract = True


class PendingBooking(PendingActionBase):
    @classmethod
    def for_user(cls, user):
        return cls.objects.select_related("event").filter(user=user).first()
    
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="pending_booking_event")
    seat_numbers = models.JSONField(default=list)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    

class PendingBookingCancellation(PendingActionBase):
    @classmethod
    def for_user(cls, user):
        return (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .first()
        )
    
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='pending_cancellations')
    
    
class PendingPaymentRetry(PendingActionBase):
    @classmethod
    def for_user(cls, user):
        return (
            cls.objects
            .select_related("booking__event")
            .prefetch_related("booking__booking_seats__seat")
            .filter(user=user)
            .first()
        )
    
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="pending_payment_retries")
    
