from django.shortcuts import render
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from core.throttles import AuthThrottle
from .serializers import RegisterSerializer

# Create your views here.
class RegisterView(APIView):
    throttle_classes = [AuthThrottle]
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        return Response(
            {
                "message": "User registered successfully",
                "username": user.username, 
                "email": user.email,
            }, 
            status=status.HTTP_201_CREATED
        )
