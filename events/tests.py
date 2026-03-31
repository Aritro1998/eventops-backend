import uuid
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework.test import APIClient

from events.models import Event


User = get_user_model()


class EventTestCase(TestCase):

    def setUp(self):

        self.client = APIClient()

        # Organizer user
        self.organizer = User.objects.create_user(
            username="organizer",
            email="org@test.com",
            password="password",
            role="ORGANIZER"
        )

        # Normal user
        self.user = User.objects.create_user(
            username="user",
            email="user@test.com",
            password="password",
            role="USER"
        )

        self.event_data = {
            "name": "Music Fest",
            "description": "Concert",
            "start_time": (timezone.now() + timedelta(days=1)).isoformat(),
            "end_time": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
            "total_seats": 100,
            "price": 500.00
        }

    # -------------------------
    # CREATE EVENT
    # -------------------------

    def test_organizer_can_create_event(self):

        self.client.force_authenticate(user=self.organizer)

        response = self.client.post("/api/events/", self.event_data, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Event.objects.count(), 1)

    def test_normal_user_cannot_create_event(self):

        self.client.force_authenticate(user=self.user)

        response = self.client.post("/api/events/", self.event_data, format="json")

        self.assertEqual(response.status_code, 403)

    # -------------------------
    # VALIDATION
    # -------------------------

    def test_invalid_event_time(self):

        self.client.force_authenticate(user=self.organizer)

        data = self.event_data.copy()
        data["end_time"] = data["start_time"]  # invalid

        response = self.client.post("/api/events/", data, format="json")

        self.assertEqual(response.status_code, 400)

    def test_negative_price_not_allowed(self):

        self.client.force_authenticate(user=self.organizer)

        data = self.event_data.copy()
        data["price"] = -100

        response = self.client.post("/api/events/", data, format="json")

        self.assertEqual(response.status_code, 400)

    # -------------------------
    # PUBLIC ACCESS
    # -------------------------

    def test_list_events_public(self):

        Event.objects.create(
            name="Test Event",
            total_seats=100,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=2),
            created_by=self.organizer,
            price=100
        )

        response = self.client.get("/api/events/")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)

    def test_event_detail_public(self):

        event = Event.objects.create(
            name="Test Event",
            total_seats=100,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=2),
            created_by=self.organizer,
            price=100
        )

        response = self.client.get(f"/api/events/{event.id}/")

        self.assertEqual(response.status_code, 200)