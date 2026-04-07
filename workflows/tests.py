import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from bookings.models import Booking
from events.models import Event, Seat
from workflows.models import WorkflowJob
from workflows.tasks import handle_booking_confirmation, handle_booking_expiry, process_workflow_job


User = get_user_model()


class TestWorkflow(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="StrongPass@123",
            role="ADMIN",
        )

        self.normal_user = User.objects.create_user(
            username="user",
            email="user@example.com",
            password="StrongPass@123",
            role="USER",
        )

        self.event = Event.objects.create(
            name="Workflow Event",
            description="Workflow test event",
            price=100.00,
            total_seats=10,
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=4),
            created_by=self.admin_user,
        )

        self.seat = Seat.objects.create(
            event=self.event,
            seat_number=1,
        )

        self.booking = Booking.objects.create(
            user=self.normal_user,
            event=self.event,
            seat=self.seat,
            status="PENDING",
            amount=self.event.price,
            idempotency_key=str(uuid.uuid4()),
            expires_at=timezone.now() + timedelta(minutes=10),
            retry_count=0,
        )

    def test_failed_jobs_view_returns_only_failed_jobs(self):
        failed_job = WorkflowJob.objects.create(
            job_type="BOOKING_CONFIRMATION",
            booking=self.booking,
            status="FAILED",
            last_error="boom",
        )
        WorkflowJob.objects.create(
            job_type="BOOKING_EXPIRY",
            booking=self.booking,
            status="PENDING",
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/api/workflows/failed-jobs/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], failed_job.id)
        self.assertEqual(response.data[0]["status"], "FAILED")

    def test_failed_jobs_view_requires_admin_role(self):
        WorkflowJob.objects.create(
            job_type="BOOKING_CONFIRMATION",
            booking=self.booking,
            status="FAILED",
        )

        self.client.force_authenticate(user=self.normal_user)
        response = self.client.get("/api/workflows/failed-jobs/")

        self.assertEqual(response.status_code, 403)

    @patch("workflows.views.process_workflow_job.delay")
    def test_retry_job_resets_state_and_requeues(self, mock_delay):
        job = WorkflowJob.objects.create(
            job_type="BOOKING_CONFIRMATION",
            booking=self.booking,
            status="FAILED",
            retry_count=2,
            last_error="smtp failed",
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f"/api/workflows/retry-job/{job.id}/")

        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, "PENDING")
        self.assertEqual(job.retry_count, 0)
        self.assertEqual(job.last_error, "")
        mock_delay.assert_called_once_with(job.id)

    @patch("workflows.views.process_workflow_job.delay")
    def test_retry_job_rejects_non_failed_job(self, mock_delay):
        job = WorkflowJob.objects.create(
            job_type="BOOKING_CONFIRMATION",
            booking=self.booking,
            status="PENDING",
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f"/api/workflows/retry-job/{job.id}/")

        self.assertEqual(response.status_code, 400)
        job.refresh_from_db()
        self.assertEqual(job.status, "PENDING")
        mock_delay.assert_not_called()

    def test_handle_booking_expiry_marks_pending_booking_expired(self):
        self.booking.expires_at = timezone.now() - timedelta(minutes=1)
        self.booking.status = "PENDING"
        self.booking.save(update_fields=["expires_at", "status", "updated_at"])

        job = WorkflowJob.objects.create(
            job_type="BOOKING_EXPIRY",
            booking=self.booking,
            status="PENDING",
        )

        handle_booking_expiry(job)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "EXPIRED")

    @override_settings(EMAIL_HOST_USER="")
    def test_handle_booking_confirmation_skips_when_email_not_configured(self):
        job = WorkflowJob.objects.create(
            job_type="BOOKING_CONFIRMATION",
            booking=self.booking,
            status="PENDING",
            payload={
                "email": self.normal_user.email,
                "booking_id": self.booking.id,
                "event_name": self.event.name,
                "seat_number": self.seat.seat_number,
                "event_time": str(self.event.start_time),
            },
        )

        handle_booking_confirmation(job)

        job.refresh_from_db()
        self.assertFalse(job.is_email_sent)

    @patch("workflows.tasks.process_workflow_job.apply_async")
    def test_unknown_job_type_eventually_fails(self, mock_apply_async):
        job = WorkflowJob.objects.create(
            job_type="UNKNOWN_JOB",
            booking=self.booking,
            status="PENDING",
        )

        process_workflow_job.run(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, "PENDING")
        self.assertEqual(job.retry_count, 1)
        self.assertIn("Unknown job type", job.last_error)

        process_workflow_job.run(job.id)
        process_workflow_job.run(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.retry_count, 3)
        self.assertIn("Unknown job type", job.last_error)
        self.assertEqual(mock_apply_async.call_count, 2)
