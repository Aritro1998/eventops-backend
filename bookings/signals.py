"""
The only place in this project that uses Django signals — deliberately,
because "send a confirmation email when a booking becomes CONFIRMED" needs
to fire no matter which code path changes the status (the payment flow,
a future admin edit, a management command), and a signal is the one hook
guaranteed to run on every .save() regardless of caller. Everywhere else
in this codebase prefers explicit service-method calls over signals, since
signals make control flow harder to trace — this is the one case where
"runs automatically for every save, from anywhere" is actually the
property that's wanted.

Registered in bookings/apps.py's BookingsConfig.ready(), which is the
only place Django reliably calls signal-registration code at startup.
"""

from django.db import transaction
from django.utils import timezone
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

    post_save alone can't tell "status just changed to CONFIRMED" from
    "status was already CONFIRMED and something else changed" — Django's
    save() doesn't carry the old value. That's what store_previous_status
    (pre_save, above) is for: it stashes the pre-save status onto the
    instance so this handler can compare against it.
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
                # Presentation only — see Booking.seat_display for why this
                # is a count for general admission, labels otherwise.
                **instance.seat_display(),
                # %d %b %Y, %I:%M %p matches the human-readable format
                # already used elsewhere (see gradio_app.py's format_datetime)
                # rather than the raw isoformat/microseconds str() produced.
                "event_time": timezone.localtime(instance.event.start_time).strftime("%d %b %Y, %I:%M %p"),
                "venue_name": instance.event.venue.name if instance.event.venue else None,
                "space_name": instance.event.space.name if instance.event.space else None,
            }
        )

        process_workflow_job.delay(job.id)

    # Ensure this runs only after successful DB commit
    transaction.on_commit(create_workflow)