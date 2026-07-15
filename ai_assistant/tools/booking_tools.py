import uuid

from bookings.models import Booking
from events.models import Event, Seat
from bookings.services import BookingService
from payments.services import PaymentService
from bookings.serializers import BookingReadSerializer
from ai_assistant.models import PendingBooking, get_pending_booking_expiry


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
    if pending_booking.is_expired:
        pending_booking.delete()  # Delete the expired pending booking
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
            "expires_at": get_pending_booking_expiry(),
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
    
    
def cancel_pending_booking(user, request):
    print("=> Executing cancel_pending_booking tool")
    
    pending_booking = PendingBooking.objects.filter(user=user).first()
    
    if not pending_booking:
        return {
            "status": "no_pending_booking",
            "message": "No pending booking found."
        }
        
    event_name = pending_booking.event.name
    seat_numbers = pending_booking.seat_numbers
    amount = pending_booking.amount
    
    pending_booking.delete()
    
    return {
        "status": "cancelled",
        "event_name": event_name,
        "seat_numbers": seat_numbers,
        "amount": str(amount),
    }
    
    
def retry_payment(user, request, booking_id):
    print("=> Executing retry_payment tool with booking_id:", booking_id)
    
    # Fetch the booking with the given booking_id and ensure it belongs to the user
    booking = (
        Booking.objects
        .select_related("event", "payment")
        .prefetch_related("booking_seats__seat")
        .filter(id=booking_id, user=user)
        .first()
    )
    
    if not booking:
        raise ValueError("Booking not found.")
    
    if booking.status not in ["FAILED", "PENDING"]:
        raise ValueError("Payment cannot be retried for this booking status.")
    
    # Retry the payment using the PaymentService
    PaymentService.process_payment(booking.id)
    # Refresh the booking instance to get the updated status and payment details
    booking.refresh_from_db()
    serializer = BookingReadSerializer(booking)
    
    return serializer.data

