import logging

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from core.throttles import AuthThrottle
from .serializers import RegisterSerializer

logger = logging.getLogger(__name__)

# Create your views here.
class RegisterView(APIView):
    throttle_classes = [AuthThrottle]
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        logger.info(
            "user_registered",
            extra={
                "event": "user_registered",
                "user_id": user.id,
                "username": user.username,
            }
        )
        
        return Response(
            {
                "message": "User registered successfully",
                "username": user.username, 
                "email": user.email,
            }, 
            status=status.HTTP_201_CREATED
        )
