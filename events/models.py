from django.db import models
from django.db.models import Q, F

from users.models import User

# Create your models here.
class Event(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    total_seats = models.IntegerField()
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['-created_at']
        # Ensure total_seats is positive and end_time is after start_time
        constraints = [
            models.CheckConstraint(
                condition=Q(total_seats__gt=0), 
                name='total_seats_non_negative'
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=F('start_time')), 
                name='end_time_after_start_time'
            ),
        ]


class Seat(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.IntegerField()

    def __str__(self):
        return f'Seat {self.seat_number} (Event {self.event_id})'
    
    class Meta:
        ordering = ['seat_number']
        # Ensure seat_number is unique per event and positive
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'seat_number'], 
                name='unique_seat_number_per_event'
            ),
            models.CheckConstraint(
                condition=Q(seat_number__gt=0), 
                name='seat_number_positive'
            ),
        ]
        
            