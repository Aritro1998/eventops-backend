"""
Thin wrapper around Channels' channel layer for broadcasting live seat
status changes. Deliberately has NO import of BookingService or any
model - this module gets imported from bookings/services.py and
workflows/tasks.py (to SEND broadcasts) and from events/consumers.py (to
receive them). If broadcasting lived in consumers.py instead, bookings/
services.py importing from it would create a circular import, since
consumers.py needs BookingService too. Keeping this module free of
business-logic imports avoids that entirely.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def group_name_for_event(event_id):
    """
    One Channels "group" per event - every browser tab currently
    watching this event's seat map joins the same group (see
    SeatAvailabilityConsumer.connect), so one broadcast reaches every
    one of them at once.
    """
    return f"event_{event_id}_seats"


def broadcast_seat_update(event_id, seat_number, status):
    """
    Tell every connected client watching this event that one seat's
    status just changed. status is one of "available", "locked", "booked".

    Called from ordinary SYNCHRONOUS code - BookingService, PaymentService,
    a Celery task - never from inside the consumer itself (the consumer is
    already async and can call the channel layer directly). Channels'
    channel layer is async-native under the hood, so calling it from sync
    code needs async_to_sync - the exact mirror image of sync_to_async,
    just crossing the sync/async boundary the other direction.
    """
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        group_name_for_event(event_id),
        {
            # "type" here isn't arbitrary - Channels uses it to pick which
            # METHOD to call on the consumer that receives this message.
            # Dots become underscores, so "seat.update" here means
            # SeatAvailabilityConsumer.seat_update(...) gets called below.
            "type": "seat.update",
            "seat_number": seat_number,
            "status": status,
        }
    )


def broadcast_seats_update_for_booking(booking, status):
    """
    Broadcast the same status to every seat attached to this booking -
    the common case whenever a booking transitions as a whole (confirmed,
    cancelled, expired) and every one of its seats needs the identical
    update, instead of repeating a booking.booking_seats.all() loop at
    every call site. Callers inside an open transaction should wrap this
    call itself in transaction.on_commit(...), so the broadcast doesn't
    fire if the transaction later rolls back.
    """
    for booking_seat in booking.booking_seats.all():
        broadcast_seat_update(booking.event_id, booking_seat.seat.seat_number, status)
    
    
    