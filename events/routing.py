"""
WebSocket URL patterns - the WebSocket equivalent of urls.py. Wired into
core/asgi.py's ProtocolTypeRouter, so WebSocket connections get routed
here instead of through Django's normal (HTTP-only) URL resolver.
"""

from django.urls import re_path

from events.consumers import SeatAvailabilityConsumer

websocket_urlpatterns = [
    re_path(r'ws/events/(?P<event_id>\d+)/seats/$', SeatAvailabilityConsumer.as_asgi()),
]