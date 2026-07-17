from bookings.models import Booking
from events.models import Event, Seat
from events.services import EventService
from bookings.services import BookingService
from payments.services import PaymentService
from bookings.serializers import BookingReadSerializer
from ai_assistant.models import PendingBooking, get_pending_booking_expiry


def get_my_bookings(user, status=None):
    print("=> Executing get_my_bookings tool with status:", status)
    bookings = BookingService.get_user_bookings(user, status_filter=status)
    serializer = BookingReadSerializer(bookings, many=True)
    return serializer.data


def prepare_booking(user, request, seat_numbers, conversation_id, chat_state):
    # get_available_seats stores this value in Redis. The model never supplies
    # an event ID here, so it cannot switch the booking to a guessed event.
    event_id = chat_state.get("selected_event_id")
    if event_id is None:
        raise ValueError("Choose an event and view its available seats before selecting seats.")

    print(f"=> Executing prepare_booking tool for event_id: {event_id} with seat_numbers: {seat_numbers}")

    # Reject duplicates before calculating the price or creating the draft.
    if not seat_numbers or len(seat_numbers) != len(set(seat_numbers)):
        raise ValueError("Choose at least one unique seat number.")

    event = Event.objects.get(id=event_id)

    # Confirm each requested number belongs to the currently selected event.
    # Seat numbers are only unique within one event, not globally.
    existing_seats = set(
        Seat.objects.filter(
            event=event,
            seat_number__in=seat_numbers
        ).values_list('seat_number', flat=True)
    )

    missing_seats = set(seat_numbers) - existing_seats
    if missing_seats:
        raise ValueError(f"Seats {sorted(missing_seats)} do not exist for {event.name}.")

    # This is an early user-facing availability check, not a reservation.
    # BookingService repeats the check under row locks after confirmation,
    # which prevents two users from booking the same seat concurrently.
    available_seats = set(
        EventService.get_available_seats(event.id)
        .filter(seat_number__in=seat_numbers)
        .values_list('seat_number', flat=True)
    )
    unavailable_seats = set(seat_numbers) - available_seats
    if unavailable_seats:
        raise ValueError(f"Seats {sorted(unavailable_seats)} are no longer available for {event.name}.")

    # One user has one active draft. Choosing a different event or seat set
    # replaces the previous draft until the user explicitly confirms it.
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
        "event_id": event_id,
        "event_name": event.name,
        "seat_numbers": seat_numbers,
        "status": "awaiting_confirmation",
        "amount": str(
            event.price * len(seat_numbers)
        ),
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


def cancel_booking(user, booking_id):
    print("=> Executing cancel_booking tool with booking_id:", booking_id)
    booking = (
        Booking.objects
        .select_related("event", "payment")
        .prefetch_related("booking_seats__seat")
        .filter(id=booking_id, user=user)
        .first()
    )

    if not booking:
        raise ValueError("Booking not found.")

    # BookingService permits cancellation only for CONFIRMED bookings.
    booking = BookingService.cancel_booking(booking)

    return BookingReadSerializer(booking).data
