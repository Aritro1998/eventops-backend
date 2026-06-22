import uuid

from datetime import datetime, timedelta
from django.utils import timezone
from events.models import Event, Seat
from bookings.services import BookingService
from bookings.serializers import BookingReadSerializer
from ai_assistant.models import PendingBooking

def get_my_bookings(user, status=None):
    print("=> Executing get_my_bookings tool with status:", status)
    bookings = BookingService.get_user_bookings(user, status_filter=status)
    serializer = BookingReadSerializer(bookings, many=True)
    return serializer.data


def create_booking(user, request):
    idempotency_key = str(uuid.uuid4())
    pending_booking = PendingBooking.objects.filter(user=user).first()
    
    if not pending_booking:
        raise ValueError(
            "No pending booking found"
        )
    
    # Check if the pending booking has expired
    current_time = timezone.now()
    pending_time = pending_booking.created_at
    
    if current_time - pending_time > timedelta(minutes=BookingService.EXPIRY_MINUTES):
        raise ValueError(
            f"Pending booking has expired. Please start over."
        )

    event_id = pending_booking.event.id
    seat_numbers = pending_booking.seat_numbers
    idempotency_key = str(uuid.uuid4())

    print("=> Executing create_booking tool with event_id:", event_id, "seat_numbers:", seat_numbers, "idempotency_key:", idempotency_key)
    
    event = Event.objects.get(
        id=event_id
    )
    
    seat_ids = list(
        Seat.objects.filter(
            event=event, 
            seat_number__in=seat_numbers
        ).values_list('id', flat=True)
    )
    
    booking, is_existing = BookingService.create_booking_for_user(
        user=user,
        event=event,
        seat_ids=seat_ids,
        idempotency_key=idempotency_key
    )
    
    pending_booking.delete()
    
    return {
        "booking_id": booking.id,
        "event_name": event.name,
        "seat_numbers": seat_numbers,
        "status": booking.status,
        "amount": str(booking.amount),
        "is_existing": is_existing,
    }
    
    
def prepare_booking(user, request, event_id, seat_numbers):
    print("=> Executing prepare_booking tool with event_id:", event_id, "seat_numbers:", seat_numbers)
    
    event = Event.objects.get(id=event_id)
    
    PendingBooking.objects.update_or_create(
        user=user,
        defaults={
            "event": event,
            "seat_numbers": seat_numbers,
            "amount": event.price * len(seat_numbers),
        }
   )
    
    return {
        "event_name": event.name,
        "status": "awaiting_confirmation",
        "event_id": event_id,
        "seat_numbers": seat_numbers,
        "amount": str(
            event.price * len(seat_numbers)
        ),
    }
    
        
    