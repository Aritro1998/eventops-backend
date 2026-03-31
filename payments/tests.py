import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from payments.models import Payment
from bookings.models import Booking
from events.models import Event, Seat
from payments.services import PaymentService


User = get_user_model()


class PaymentTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="testuser")

        self.event = Event.objects.create(
            name="Test Event",
            description="A test event",
            price=100.00,
            total_seats=100,
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=4),
            created_by=self.user
        )

        self.seat = Seat.objects.create(
            event=self.event,
            seat_number=1
        )

        # IMPORTANT: expiry must be in future
        self.booking = Booking.objects.create(
            event=self.event,
            user=self.user,
            seat=self.seat,
            status="PENDING",
            amount=self.event.price,
            idempotency_key=str(uuid.uuid4()),
            expires_at=timezone.now() + timedelta(minutes=10),
            retry_count=0
        )

    # -------------------------
    # MODEL TESTS
    # -------------------------

    def test_payment_created_for_booking(self):

        payment = Payment.objects.create(
            booking=self.booking,
            amount=self.booking.amount,
            status="SUCCESS",
            transaction_id=str(uuid.uuid4()),
        )

        self.assertEqual(payment.status, "SUCCESS")
        self.assertEqual(payment.booking, self.booking)

    def test_payment_failure_model(self):

        payment = Payment.objects.create(
            booking=self.booking,
            amount=self.booking.amount,
            status="FAILED",
            transaction_id=str(uuid.uuid4()),
        )

        self.assertEqual(payment.status, "FAILED")

    # -------------------------
    # SERVICE TESTS
    # -------------------------

    @patch("payments.services.random.choice")
    def test_payment_service_success(self, mock_choice):

        mock_choice.return_value = True

        payment = PaymentService.process_payment(self.booking.id)

        self.assertEqual(payment.status, "SUCCESS")

    @patch("payments.services.random.choice")
    def test_payment_service_failure(self, mock_choice):

        mock_choice.return_value = False

        payment = PaymentService.process_payment(self.booking.id)

        self.assertEqual(payment.status, "FAILED")

    # -------------------------
    # BUSINESS BEHAVIOR
    # -------------------------

    @patch("payments.services.random.choice")
    def test_booking_status_updated_on_success(self, mock_choice):

        mock_choice.return_value = True

        payment = PaymentService.process_payment(self.booking.id)

        self.booking.refresh_from_db()

        self.assertEqual(payment.status, "SUCCESS")
        self.assertEqual(self.booking.status, "CONFIRMED")

    @patch("payments.services.random.choice")
    def test_booking_status_updated_on_failure(self, mock_choice):

        mock_choice.return_value = False

        payment = PaymentService.process_payment(self.booking.id)

        self.booking.refresh_from_db()

        self.assertEqual(payment.status, "FAILED")
        self.assertEqual(self.booking.status, "FAILED")

    @patch("payments.services.random.choice")
    def test_retry_limit_expires_booking(self, mock_choice):

        mock_choice.return_value = False

        self.booking.retry_count = 3
        self.booking.save()

        with self.assertRaises(ValueError):
            PaymentService.process_payment(self.booking.id)

        self.booking.refresh_from_db()

        # transaction rolled back → status unchanged
        self.assertEqual(self.booking.status, "PENDING")