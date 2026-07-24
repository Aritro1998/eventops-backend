from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Custom user model (set as AUTH_USER_MODEL in settings), extending
    Django's built-in AbstractUser with an application-level `role`.

    `role` is the business permission system for this project — it's what
    core.permissions.IsAdminOrOrganizer and IsRoleAdmin actually check,
    deliberately kept separate from Django admin's own is_staff/is_superuser
    flags (which only control access to the /admin/ site itself, not the API).
    """
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('ORGANIZER', 'Organizer'),
        ('USER', 'User')
    ]

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='USER')