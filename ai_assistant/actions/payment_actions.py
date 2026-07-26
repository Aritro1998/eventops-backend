"""
Execute half of "propose vs execute" for payment retries - same
structure as booking_actions.py. Unlike the booking draft (which needs
PendingBookingThread as a marker), no separate marker is needed here: a
real Booking row already exists by the time a retry is possible, and its
own status ("FAILED" = eligible, anything else = not) is a reliable
signal on its own. booking_actions.confirm_pending_booking already
pauses the retry graph the instant a payment fails, so "status ==
FAILED" and "has an active paused retry thread" stay in sync by
construction, not by a second piece of state that could drift from it.
"""

from asgiref.sync import sync_to_async
from langgraph.types import Command

from bookings.models import Booking
from payments.services import PaymentService
from ai_assistant.langgraph_flows.payment_retry_graph import get_payment_retry_graph


def _retry_thread_config(booking):
    return {"configurable": {"thread_id": f"booking-{booking.id}"}}


def _is_awaiting_retry_decision(booking):
    """
    Booking.status == "FAILED" alone isn't enough to know whether the
    Retry Payment Now / Not Now controls should still show. Giving up
    (dismiss_payment_retry) ends the retry graph without changing the
    booking's status - there's nothing else to flip since the booking
    genuinely is still FAILED - so without this check the actions/draft
    below would keep reporting a retry as available forever after it was
    explicitly dismissed. The same check also correctly hides these
    controls for a FAILED booking that was never routed through this
    graph at all (e.g. a payment retried directly via the plain REST API
    instead of through the AI).
    """
    graph = get_payment_retry_graph()
    return bool(graph.get_state(_retry_thread_config(booking)).next)


def get_pending_payment_retry_actions(user):
    """Return UI actions only when the user has a booking eligible for retry."""

    booking = Booking.objects.filter(user=user, status="FAILED").order_by("-created_at").first()

    if not booking or not _is_awaiting_retry_decision(booking):
        return []

    return [
        {"type": "confirm_payment_retry", "label": "Retry Payment Now"},
        {"type": "dismiss_payment_retry", "label": "Not Now"},
    ]


async def get_pending_payment_retry_draft(user):
    """Return the eligible booking's details for rendering, or None."""

    booking = await (
        Booking.objects
        .filter(user=user, status="FAILED")
        .select_related("event")
        .prefetch_related("booking_seats__seat")
        .order_by("-created_at")
        .afirst()
    )

    if not booking or not await sync_to_async(_is_awaiting_retry_decision)(booking):
        return None

    # seat_display touches event.space (not covered by the prefetch
    # above) and is shared with sync callers elsewhere, so it's wrapped
    # here rather than converted natively.
    seat_display = await sync_to_async(booking.seat_display)()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **seat_display,
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }


def confirm_payment_retry(user):
    """Actually attempt the payment again for the eligible booking."""

    booking = Booking.objects.filter(user=user, status="FAILED").order_by("-created_at").first()

    if not booking:
        raise ValueError("No pending payment retry found")

    if not _is_awaiting_retry_decision(booking):
        raise ValueError("This booking is no longer eligible for a payment retry.")

    graph = get_payment_retry_graph()
    graph.invoke(Command(resume="retry"), config=_retry_thread_config(booking))
    booking.refresh_from_db()

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "status": booking.status,  # CONFIRMED, FAILED, or EXPIRED
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }


def dismiss_payment_retry(user):
    """Back out of a staged retry — the booking is left exactly as it was."""

    booking = Booking.objects.filter(user=user, status="FAILED").order_by("-created_at").first()

    if not booking:
        raise ValueError("No pending payment retry found.")

    if _is_awaiting_retry_decision(booking):
        graph = get_payment_retry_graph()
        graph.invoke(Command(resume="give_up"), config=_retry_thread_config(booking))

    return {
        "booking_id": booking.id,
        "event_name": booking.event.name,
        **booking.seat_display(),
        "status": booking.status,  # untouched — still whatever it was
        "amount": str(booking.amount),
        "event_start_time": booking.event.start_time.isoformat(),
        "expires_at": booking.expires_at.isoformat(),
        "attempts_remaining": max(0, PaymentService.MAX_RETRIES - booking.retry_count),
    }
