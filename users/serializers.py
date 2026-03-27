from rest_framework import serializers
from django.db import IntegrityError
from django.contrib.auth.password_validation import validate_password

from .models import User

class RegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'username', 
            'email',
            'password'
        ]
        # Make password write-only
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists")
        return value
    
    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        # Normalize email to lowercase and strip whitespace
        validated_data['email'] = validated_data['email'].strip().lower()
        # Set default role to 'USER' for all new registrations
        validated_data['role'] = "USER"
        
        # Use create_user to ensure password is hashed and handle user creation
        try:
            return User.objects.create_user(**validated_data)
        except IntegrityError:
            raise serializers.ValidationError({
                "email": "A user with this email already exists."
            })