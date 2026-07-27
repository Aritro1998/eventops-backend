"""
Execute half of "propose vs execute" for payment retries - same
structure as booking_actions.py/cancellation_actions.py. PendingPaymentRetry
is a marker only, same role as PendingBookingCancellation for
cancel_booking_graph: the actual retry state lives in payment_retry_graph's
own checkpoint, keyed by booking_id, not in this row. Without a marker,
every function here would have to independently guess "which booking" via
Booking.objects.filter(status="FAILED").order_by("-created_at").first() -
fine with exactly one failed booking, but silently wrong the moment a user
has two: whatever prepare_payment_retry actually staged and whatever this
guess picks can disagree.
"""

from asgiref.sync import sync_to_async
from langgraph.types import Command

from payments.services import PaymentService
from ai_assistant.models import PendingPaymentRetry
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


def _get_marked_booking(user):
    """
    The one booking currently relevant to retry actions - whichever was
    last explicitly staged via prepare_payment_retry or auto-staged the
    instant a fresh booking attempt failed. Returns None (and cleans up
    a stale/expired/already-resolved marker) if there's nothing eligible.
    """
    pending = PendingPaymentRetry.for_user(user)

    if not pending:
        return None

    if pending.is_expired:
        pending.delete()
        return None

    if not _is_awaiting_retry_decision(pending.booking):
        pending.delete()
        return None

    return pending.booking


def get_pending_payment_retry_actions(user):
    """Return UI actions only when the user has a booking eligible for retry."""

    if not _get_marked_booking(user):
        return []

    return [
        {"type": "confirm_payment_retry", "label": "Retry Payment Now"},
        {"type": "dismiss_payment_retry", "label": "Not Now"},
    ]


async def get_pending_payment_retry_draft(user):
    """Return the marked booking's details for rendering, or None."""

    booking = await sync_to_async(_get_marked_booking)(user)

    if not booking:
        return None

    # seat_display touches event.space (not covered by for_user's
    # select_related) and is shared with sync callers elsewhere, so it's
    # wrapped here rather than converted natively.
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
    """Actually attempt the payment again for the marked booking."""

    booking = _get_marked_booking(user)

    if not booking:
        raise ValueError("No pending payment retry found")

    graph = get_payment_retry_graph()
    graph.invoke(Command(resume="retry"), config=_retry_thread_config(booking))
    booking.refresh_from_db()

    if not _is_awaiting_retry_decision(booking):
        # Resolved either way (CONFIRMED or retries exhausted -> EXPIRED)
        # - nothing left to mark.
        PendingPaymentRetry.objects.filter(user=user).delete()

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
    """Back out of the marked retry — the booking is left exactly as it was."""

    booking = _get_marked_booking(user)

    if not booking:
        raise ValueError("No pending payment retry found.")

    graph = get_payment_retry_graph()
    graph.invoke(Command(resume="give_up"), config=_retry_thread_config(booking))
    PendingPaymentRetry.objects.filter(user=user).delete()

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
