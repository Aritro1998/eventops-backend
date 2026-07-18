from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


# Rebrand the default Django admin site chrome for this project.
admin.site.site_header = "EventOps Admin"
admin.site.site_title = "EventOps Admin Portal"
admin.site.index_title = "Welcome to EventOps Admin"

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Extends Django's built-in UserAdmin so the custom `role` field
    (ADMIN/ORGANIZER/USER — see users/models.py) is visible and editable
    in admin, not just the stock username/email/permissions fields."""

    # Add role to list view
    list_display = ("username", "email", "role", "is_staff")

    # Add role to edit form
    fieldsets = UserAdmin.fieldsets + (
        ("Custom Fields", {"fields": ("role",)}),
    )

    # Add role when creating user from admin
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Custom Fields", {"fields": ("role",)}),
    )