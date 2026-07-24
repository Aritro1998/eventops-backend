"""
Payment is a real gateway integration's shape, but `gateway` defaults to
'SIMULATED' and PaymentService (see payments/services.py) never actually
calls out to a real payment provider — this project simulates success/
failure so booking flows can be built and tested without one.
"""

from django.db import models
from bookings.models import Booking


class Payment(models.Model):
    """One-to-one with Booking — a booking has at most one Payment record,
    reused across retries (see PaymentService.process_payment) rather than
    creating a new Payment row per attempt."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
    ]

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='payment')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    transaction_id = models.CharField(max_length=255, blank=True, null=True)
    payment_method = models.CharField(max_length=20, null=True, blank=True)
    gateway = models.CharField(max_length=50, default='SIMULATED')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['transaction_id'],
                condition=~models.Q(transaction_id=None),
                name='unique_transaction_id'
            )
        ]

    def __str__(self):
        return f"Payment:{self.id} - Booking: {self.booking.id} - Status: {self.status}"