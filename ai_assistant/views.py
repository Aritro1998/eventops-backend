from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .services import AIAssistantService


class ChatView(APIView):
    def post(self, request):
        user_prompt = request.data.get('message', '').strip()
        history = request.data.get('history', [])

        if not user_prompt:
            return Response(
                {'error': 'Message is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            ai_service = AIAssistantService()
            response = ai_service.chat(user_prompt, history)
            return Response({'response': response})
        except Exception as e:
            return Response(
                {'error': 'Failed to get AI response'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
