from django.db import models
from users.models import User
from events.models import Event


# Create your models here.
class PendingBooking(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="pending_booking_user")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="pending_booking_event")
    seat_numbers = models.JSONField(default=list)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)