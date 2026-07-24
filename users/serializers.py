from rest_framework import serializers
from django.db import IntegrityError
from django.contrib.auth.password_validation import validate_password

from .models import User

class RegisterSerializer(serializers.ModelSerializer):
    """Public self-registration. Always creates a plain 'USER' — there is
    no API path to register as ADMIN/ORGANIZER; those roles are assigned
    manually via the Django admin."""

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

        # Use create_user to ensure password is hashed and handle user creation.
        # validate_email's uniqueness check above is only a fast, early
        # user-facing check — it can't stop two concurrent registrations
        # with the same email from both passing it before either commits.
        # The database's unique constraint on email is the real guarantee;
        # this except is what turns that race into a clean error instead
        # of a 500.
        try:
            return User.objects.create_user(**validated_data)
        except IntegrityError:
            raise serializers.ValidationError({
                "email": "A user with this email already exists."
            })