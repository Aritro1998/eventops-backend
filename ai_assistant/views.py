"""
ChatStreamView (Server-Sent Events) is the sole chat entry point — the
real frontend (gradio_app.py) calls it exclusively, for token-by-token
streaming. A non-streaming ChatView/chat() pairing used to exist
alongside it but was removed as unused.

Everything below ChatStreamView is one APIView per confirm/dismiss button
a Pending* draft can show (see ai_assistant/models.py's module docstring
for the propose-vs-execute design these exist to support). Each one:
calls its action function (ai_assistant/actions/), builds a human-readable
response_text, persists that text into the chat's own history via
persist_action_outcome so a later message in the same conversation has
context for "what just happened", and returns the fresh list of any
still-pending actions.
"""

import json
import logging

from .chat_state import ChatState
from .services import AIAssistantService
from core.throttles import BookingThrottle

from django.views import View
from rest_framework import status
from django.http import JsonResponse
from asgiref.sync import sync_to_async
from rest_framework.views import APIView
from rest_framework.response import Response
from django.http import StreamingHttpResponse
from rest_framework.exceptions import APIException
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import AnonymousUser
from django.utils.decorators import method_decorator
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed


from .actions.booking_actions import (
    confirm_pending_booking,
    get_pending_booking_actions,
    cancel_pending_booking_draft,
)

from .actions.cancellation_actions import (
    confirm_cancellation,
    dismiss_cancellation,
    get_pending_cancellation_actions,
)

from .actions.payment_actions import (
    confirm_payment_retry,
    dismiss_payment_retry,
    get_pending_payment_retry_actions,
)


logger = logging.getLogger(__name__)


def persist_action_outcome(request, response_text):
    """Store a button result as assistant context when its chat is still live."""
    conversation_id = request.data.get("conversation_id")
    if not conversation_id:
        return

    try:
        conversation_id, chat_state = ChatState.get(
            conversation_id,
            user=request.user,
        )
    except APIException:
        # A stale chat must not block a valid booking action.
        return

    ChatState.add_turn(chat_state, "assistant", response_text)
    ChatState.save(conversation_id, chat_state)
    
    
def get_all_pending_actions(user):
    """Merge every action family into one list for the frontend.

    All three are independent and can be non-empty at once (e.g. a leftover
    seat draft, a staged cancellation, AND a staged payment retry are three
    unrelated bookings). Every place that reports "actions" — the chat
    endpoint and every action view — should call this instead of hand-rolling
    a subset or hardcoding [], so a click on one button never wipes out an
    unrelated pending item that's still waiting on its own click.
    """
    
    return (
        get_pending_booking_actions(user)
        + get_pending_cancellation_actions(user)
        + get_pending_payment_retry_actions(user)
    )


@method_decorator(csrf_exempt, name='dispatch')
class ChatStreamView(View):
    async def post(self, request):
        # Plain Django View skips DRF's authentication pipeline entirely —
        # replicate the one piece this endpoint actually relies on (JWT)
        # so request.user resolves correctly from the Authorization header
        # instead of silently defaulting to AnonymousUser for every real user.
        try:
            auth_result = await sync_to_async(JWTAuthentication().authenticate)(request)
        except (InvalidToken, AuthenticationFailed):
            return JsonResponse(
                {"error": "Invalid or expired token"},
                status = status.HTTP_401_UNAUTHORIZED
            )
        
        request.user = auth_result[0] if auth_result is not None else AnonymousUser()
        
        # The request body is expected to be JSON, but the client may send
        # invalid JSON or no body at all. In that case, treat it as an empty dict
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            body = {}
            
        user_prompt = body.get('message', '').strip()
        conversation_id = body.get('conversation_id')
        
        if not user_prompt:
            return JsonResponse(
                {'error': 'Message is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Deliberately done here, outside the generator below: if the
        # conversation_id is invalid/expired, ChatState.get raises a DRF
        # exception that the normal view machinery turns into a clean 400/403
        # response. Once we're inside the streaming generator, the HTTP
        # headers are already committed — errors there can't cleanly become
        # a different status code anymore.
        if conversation_id:
            conversation_id, chat_state = await sync_to_async(ChatState.get)(conversation_id, user=request.user)
        else:
            conversation_id, chat_state = await sync_to_async(ChatState.create)(user=request.user)
            
        async def event_stream():
            ai_service = AIAssistantService()
            try:
                async for event in ai_service.chat_stream(
                    user_prompt,
                    user=request.user,
                    request=request,
                    conversation_id=conversation_id,
                    chat_state=chat_state,
                ):
                    if event["type"] == "done":
                        event["conversation_id"] = conversation_id
                        event["actions"] = (
                            await sync_to_async(get_all_pending_actions)(request.user)
                            if request.user.is_authenticated else []
                        )
                    
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception:
                logger.exception("chat_stream_failed")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Failed to get AI response'})}\n\n"

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        return response


class ConfirmPendingBookingActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]

    def post(self, request):
        try:
            booking = confirm_pending_booking(request.user)
        except ValueError as error:
            return Response(
                {'detail': str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )

        if booking["status"] == "CONFIRMED":
            response_text = (
                f"Your booking for {booking['event_name']} has been confirmed. "
                f"Booking ID: {booking['booking_id']}."
            )
        else:
            response_text = (
                f"Your booking for {booking['event_name']} was created with "
                f"status {booking['status']}. Booking ID: {booking['booking_id']}."
            )

        persist_action_outcome(request, response_text)

        return Response(
            {
                "response": response_text,
                "booking": booking,
                "actions": get_all_pending_actions(request.user),
            }
        )


class CancelPendingBookingActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]

    def post(self, request):
        try:
            result = cancel_pending_booking_draft(request.user)
        except ValueError as error:
            return Response(
                {'detail': str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )

        response_text = "Your pending booking draft has been cancelled."
        persist_action_outcome(request, response_text)

        return Response(
            {
                "response": response_text,
                "draft": result,
                "actions": get_all_pending_actions(request.user),
            }
        )
        
        
class ConfirmCancellationActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]
    
    def post(self, request):
        try:
            booking = confirm_cancellation(request.user)
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        response_text = (
            f"Your booking for {booking['event_name']} has been cancelled. "
            f"Booking ID: {booking['booking_id']}."
        )
        
        persist_action_outcome(request, response_text)
        
        return Response(
            {
                "response": response_text,
                # Reusing the "booking" key on purpose, same as
                # ConfirmPendingBookingActionView — the frontend's card
                # renderer already knows how to display any dict shaped
                # like a booking, it just switches on booking["status"].
                "booking": booking,
                "actions": get_all_pending_actions(request.user),
            }
        )


class DismissCancellationActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]
    
    def post(self, request):
        try:
            booking = dismiss_cancellation(request.user)
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        response_text = f"Kept your booking for {booking['event_name']}. It was not cancelled."
        persist_action_outcome(request, response_text)

        return Response(
            {
                "response": response_text,
                "booking": booking,
                "actions": get_all_pending_actions(request.user),
            }
        )
        
        
class ConfirmPaymentRetryActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]

    def post(self, request):
        try:
            booking = confirm_payment_retry(request.user)
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )

        if booking["status"] == "CONFIRMED":
            response_text = (
                f"Your payment for {booking['event_name']} succeeded. "
                f"Booking ID: {booking['booking_id']}."
            )
        else:
            response_text = (
                f"That attempt for {booking['event_name']} ended with status "
                f"{booking['status']}. Booking ID: {booking['booking_id']}."
            )

        persist_action_outcome(request, response_text)

        return Response(
            {
                "response": response_text,
                "booking": booking,
                "actions": get_all_pending_actions(request.user),
            }
        )


class DismissPaymentRetryActionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]

    def post(self, request):
        try:
            booking = dismiss_payment_retry(request.user)
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST
            )

        response_text = f"Okay, leaving the payment for {booking['event_name']} as-is for now."
        persist_action_outcome(request, response_text)

        return Response(
            {
                "response": response_text,
                "booking": booking,
                "actions": get_all_pending_actions(request.user),
            }
        )
        
        
        