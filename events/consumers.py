"""
The WebSocket "view" for live seat availability - one instance of this
class exists per connected browser tab watching a given event's seat
picker (see events/routing.py for the URL that creates one).
"""

import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from bookings.services import BookingService
from events.broadcasts import group_name_for_event


class SeatAvailabilityConsumer(AsyncWebsocketConsumer):
    """
    Server -> all clients only: whenever a seat is actually locked,
    booked, or released elsewhere in the app,
    events.broadcasts.broadcast_seat_update() sends a message into this
    event's group, and Channels delivers it here as seat_update() below,
    which forwards it to this browser. No client -> server messages are
    expected (an earlier hover-relay feature lived here and was removed).
    """

    async def connect(self):
        """Runs once, the moment a browser opens the connection."""

        # The URLRouter in core/asgi.py has already parsed the URL and
        # put the event_id into self.scope["url_route"]["kwargs"].
        self.event_id = self.scope["url_route"]["kwargs"]["event_id"]
        self.group_name = group_name_for_event(self.event_id)

        # Join this event's group - from this point on, broadcasts sent
        # to this group by broadcast_seat_update will reach this consumer.
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Send the CURRENT state right away. Without this, a client that
        # connects partway through would only ever see FUTURE changes,
        # with no idea what's already locked or booked right now.
        # get_seat_statuses_for_event is an ordinary synchronous Django
        # ORM query - not safe to call directly from this async method,
        # so it's wrapped in sync_to_async, exactly like the wrapping
        # used throughout ai_assistant/services.py.
        statuses = await sync_to_async(BookingService.get_seat_statuses_for_event)(self.event_id)

        await self.send(text_data=json.dumps({
            "type": "snapshot",
            "seats": statuses,
        }))

    async def disconnect(self, close_code):
        """Runs when the browser tab closes the connection or navigates away."""
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # --- Called BY Channels itself, not directly by the browser - matched
    # to the "type" field of whatever was passed to group_send (dots
    # become underscores in the method name) ---
    async def seat_update(self, event):
        """A real seat status change arrived from the channel layer - forward it to this browser."""
        await self.send(text_data=json.dumps({
            "type": "seat_update",
            "seat_number": event["seat_number"],
            "status": event["status"],
        }))