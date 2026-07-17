import logging

from .chat_state import ChatState
from rest_framework import status
from rest_framework.views import APIView
from .services import AIAssistantService
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import APIException
from core.throttles import BookingThrottle

logger = logging.getLogger(__name__)

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


class ChatView(APIView):
    def post(self, request):
        user_prompt = request.data.get('message', '').strip()
        conversation_id = request.data.get("conversation_id")

        if not user_prompt:
            return Response(
                {'error': 'Message is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if conversation_id:
            conversation_id, chat_state = ChatState.get(
                conversation_id,
                user=request.user
            )
        else:
            conversation_id, chat_state = ChatState.create(user=request.user)

        try:
            ai_service = AIAssistantService()
            response, draft, cancellation = ai_service.chat(
                user_prompt,
                user=request.user,
                request=request,
                conversation_id=conversation_id,
                chat_state=chat_state
            )
            
            actions = []
            if request.user.is_authenticated:
                # Both lists can be non-empty at once — e.g. a leftover seat
                # draft AND a staged cancellation are unrelated and can coexist.
                actions = get_pending_booking_actions(request.user) + get_pending_cancellation_actions(request.user)

            return Response(
                {
                    'response': response,
                    'conversation_id': conversation_id,
                    'actions': actions,
                    'draft': draft,
                    'cancellation': cancellation
                }
            )

        except Exception:
            # The client only ever sees the generic message below (never leak
            # internals to an API response) — but without this log line, every
            # 500 here looks identical in `docker logs` regardless of cause,
            # which is exactly what made this bug hard to diagnose.
            logger.exception("chat_view_failed")
            return Response(
                {'error': 'Failed to get AI response'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
                "actions": [],
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
                "actions": []
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
                "actions": [],
            }
        )
        

class DismissCancellationActionView(APIView):
    parser_classes = [IsAuthenticated]
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
                "actions": [],
            }
        )
        
