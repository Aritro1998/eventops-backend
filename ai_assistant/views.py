from .chat_state import ChatState
from rest_framework import status
from rest_framework.views import APIView
from .services import AIAssistantService
from rest_framework.response import Response



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
            response = ai_service.chat(
                user_prompt,
                user=request.user,
                request=request,
                conversation_id=conversation_id,
                chat_state=chat_state
            )

            return Response({'response': response, 'conversation_id': conversation_id})

        except Exception as e:
            return Response(
                {'error': 'Failed to get AI response'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
