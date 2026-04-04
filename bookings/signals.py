from django.db import transaction
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from bookings.models import Booking
from workflows.models import WorkflowJob
from workflows.tasks import process_workflow_job


@receiver(pre_save, sender=Booking)
def store_previous_status(sender, instance, **kwargs):
    """
    Store the previous status of the booking before it is saved.
    This allows us to detect state transitions in post_save.
    """

    if not instance.pk:
        instance._previous_status = None
        return

    try:
        old_instance = Booking.objects.get(pk=instance.pk)
        instance._previous_status = old_instance.status
    except Booking.DoesNotExist:
        instance._previous_status = None


@receiver(post_save, sender=Booking)
def booking_status_change_handler(sender, instance, created, **kwargs):
    """
    Trigger workflow only when booking transitions to CONFIRMED state.
    """

    previous_status = getattr(instance, "_previous_status", None)

    # Only act when status transitions to CONFIRMED
    if previous_status == "CONFIRMED":
        return

    if instance.status != "CONFIRMED":
        return

    def create_workflow():
        # Prevent duplicate workflow jobs
        if WorkflowJob.objects.filter(
            booking=instance,
            job_type="BOOKING_CONFIRMATION"
        ).exists():
            return

        job = WorkflowJob.objects.create(
            job_type="BOOKING_CONFIRMATION",
            booking=instance,
            payload={
                "email": instance.user.email,
                "booking_id": instance.id,
                "event_name": instance.event.name,
                "seat_number": instance.seat.seat_number,
                "event_time": str(instance.event.start_time),
            }
        )

        process_workflow_job.delay(job.id)

    # Ensure this runs only after successful DB commit
    transaction.on_commit(create_workflow)