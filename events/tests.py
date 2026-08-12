from datetime import datetime
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from rest_framework.test import APIClient

from events.models import Event
from events.services import EventService


User = get_user_model()


class TestEvent(TestCase):

    def setUp(self):

        self.client = APIClient()

        # Organizer user
        self.organizer = User.objects.create_user(
            username="organizer",
            email="org@test.com",
            password="StrongPass@123",
            role="ORGANIZER"
        )

        # Normal user
        self.user = User.objects.create_user(
            username="user",
            email="user@test.com",
            password="StrongPass@123",
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

    def test_list_events_filters_by_start_date(self):
        matching_event = Event.objects.create(
            name="Matching Event",
            total_seats=100,
            start_time=timezone.make_aware(datetime(2026, 6, 10, 14, 30)),
            end_time=timezone.make_aware(datetime(2026, 6, 10, 16, 30)),
            created_by=self.organizer,
            price=100
        )
        Event.objects.create(
            name="Other Event",
            total_seats=100,
            start_time=timezone.make_aware(datetime(2026, 6, 11, 14, 30)),
            end_time=timezone.make_aware(datetime(2026, 6, 11, 16, 30)),
            created_by=self.organizer,
            price=100
        )

        response = self.client.get("/api/events/?date=2026-06-10")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], matching_event.id)

    def test_list_events_rejects_invalid_date_filter(self):
        response = self.client.get("/api/events/?date=06-10-2026")

        self.assertEqual(response.status_code, 400)
        self.assertIn("date", response.data)

    # -------------------------
    # FUZZY NAME SEARCH (pg_trgm)
    # -------------------------
    # Regression coverage for the rapidfuzz -> pg_trgm swap - this exact
    # scenario (a short, partial/misspelled query against a name with a
    # long subtitle) is what caught TrigramSimilarity scoring too low and
    # required switching to TrigramWordSimilarity instead.

    def test_search_events_by_name_exact_match(self):
        event = Event.objects.create(
            name="Oppenheimer: IMAX Re-release",
            total_seats=100,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            created_by=self.organizer,
            price=300,
        )

        results = EventService.search_events_by_name("Oppenheimer: IMAX Re-release")

        self.assertEqual(list(results), [event])

    def test_search_events_by_name_resolves_misspelling(self):
        event = Event.objects.create(
            name="Oppenheimer: IMAX Re-release",
            total_seats=100,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            created_by=self.organizer,
            price=300,
        )

        # Real typos a user hit live during development - both previously
        # scored below TrigramSimilarity's threshold despite being an
        # obvious match, due to the long, unrelated "IMAX Re-release"
        # subtitle diluting the whole-string similarity score.
        for query in ["openhimer", "opphenhimer"]:
            with self.subTest(query=query):
                results = EventService.search_events_by_name(query)
                self.assertIn(event, list(results))

    def test_search_events_by_name_no_match(self):
        Event.objects.create(
            name="Oppenheimer: IMAX Re-release",
            total_seats=100,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            created_by=self.organizer,
            price=300,
        )

        results = EventService.search_events_by_name("completely unrelated query")

        self.assertEqual(list(results), [])

    def test_search_events_by_name_excludes_archived(self):
        Event.objects.create(
            name="Archived Concert",
            total_seats=100,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            created_by=self.organizer,
            price=300,
            is_archived=True,
        )

        results = EventService.search_events_by_name("Archived Concert")

        self.assertEqual(list(results), [])
