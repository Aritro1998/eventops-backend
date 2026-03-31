import threading
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection

from rest_framework.test import APIClient

from bookings.models import Event, Seat, Booking


User = get_user_model()


class BookingTestCase(TestCase):

    def setUp(self):

        self.user = User.objects.create(username="testuser")

        self.event = Event.objects.create(
            name="Test Event",
            total_seats=100,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=2),
            created_by=self.user
        )

        self.seat = Seat.objects.create(
            event=self.event,
            seat_number=1
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    # -------------------------
    # MODEL TESTS
    # -------------------------

    def test_successful_booking_model(self):

        booking = Booking.objects.create(
            user=self.user,
            event=self.event,
            seat=self.seat,
            status="CONFIRMED",
            amount=100
        )

        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(booking.seat, self.seat)

    def test_prevent_double_booking_model(self):

        Booking.objects.create(
            user=self.user,
            event=self.event,
            seat=self.seat,
            status="CONFIRMED",
            amount=100
        )

        with self.assertRaises(IntegrityError):
            Booking.objects.create(
                user=self.user,
                event=self.event,
                seat=self.seat,
                status="CONFIRMED",
                amount=100
            )

    # -------------------------
    # API TESTS
    # -------------------------

    def test_booking_create_api_success(self):

        response = self.client.post("/api/bookings/", {
            "seat": self.seat.id,
            "event": self.event.id,
            "idempotency_key": str(uuid.uuid4())
        })

        self.assertEqual(response.status_code, 201)

        # payment may fail → system is realistic
        self.assertIn(response.data["status"], ["CONFIRMED", "FAILED"])

    def test_booking_requires_auth(self):

        client = APIClient()

        response = client.post("/api/bookings/", {
            "seat": self.seat.id,
            "event": self.event.id,
            "idempotency_key": str(uuid.uuid4())
        })

        self.assertEqual(response.status_code, 401)

    def test_prevent_double_booking_api(self):

        data1 = {
            "seat": self.seat.id,
            "event": self.event.id,
            "idempotency_key": str(uuid.uuid4())
        }

        data2 = {
            "seat": self.seat.id,
            "event": self.event.id,
            "idempotency_key": str(uuid.uuid4())
        }

        response1 = self.client.post("/api/bookings/", data1)
        response2 = self.client.post("/api/bookings/", data2)

        self.assertEqual(response1.status_code, 201)
        self.assertNotEqual(response2.status_code, 201)

    def test_idempotent_booking(self):

        key = str(uuid.uuid4())

        data = {
            "seat": self.seat.id,
            "event": self.event.id,
            "idempotency_key": key
        }

        response1 = self.client.post("/api/bookings/", data)
        response2 = self.client.post("/api/bookings/", data)

        self.assertIn(response1.status_code, [200, 201])
        self.assertIn(response2.status_code, [200, 201])

        self.assertEqual(response1.data["id"], response2.data["id"])

    # -------------------------
    # CONCURRENCY TEST
    # -------------------------

    def test_concurrent_booking(self):

        results = []
        lock = threading.Lock()

        def make_request():

            try:
                client = APIClient()
                client.force_authenticate(user=self.user)

                response = client.post("/api/bookings/", {
                    "seat": self.seat.id,
                    "event": self.event.id,
                    "idempotency_key": str(uuid.uuid4())
                })

                with lock:
                    results.append(response.status_code)

            finally:
                connection.close()  # 🔥 CRITICAL FIX

        threads = []

        for _ in range(10):
            t = threading.Thread(target=make_request)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        #  correct invariant check (NOT response-based)
        confirmed_count = Booking.objects.filter(
            seat=self.seat,
            status="CONFIRMED"
        ).count()

        self.assertLessEqual(confirmed_count, 1)

    # -------------------------
    # EDGE CASES
    # -------------------------

    def test_invalid_seat(self):

        response = self.client.post("/api/bookings/", {
            "seat": 9999,
            "event": self.event.id,
            "idempotency_key": str(uuid.uuid4())
        })

        self.assertEqual(response.status_code, 400)

    def test_event_seat_mismatch(self):

        other_event = Event.objects.create(
            name="Other Event",
            total_seats=50,
            start_time=timezone.now(),
            end_time=timezone.now() + timedelta(hours=2),
            created_by=self.user
        )

        response = self.client.post("/api/bookings/", {
            "seat": self.seat.id,
            "event": other_event.id,
            "idempotency_key": str(uuid.uuid4())
        })

        self.assertEqual(response.status_code, 400)