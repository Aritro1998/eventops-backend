from django.contrib import admin
from .models import Booking, BookingSeat


class BookingSeatInline(admin.TabularInline):
    """Shows a Booking's seats directly on its admin edit page instead of
    requiring a separate lookup in BookingSeatAdmin below."""
    model = BookingSeat
    extra = 0
    autocomplete_fields = ["seat"]


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "event", "payment", "retry_count", "created_at", "expires_at", "status", "amount"]
    list_filter = ["status", "event"]
    search_fields = ["user__username"]
    list_display_links = ["id", "user", "event"]
    inlines = [BookingSeatInline]


@admin.register(BookingSeat)
class BookingSeatAdmin(admin.ModelAdmin):
    # is_active is the field that actually decides whether this row still
    # holds a live claim on its seat (see BookingSeat's docstring) —
    # surfaced here so a stuck/conflicting seat claim can be inspected
    # directly without digging through Booking history.
    list_display = [
        "id", "booking", "seat", "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = [
        "booking__user__username",
    ]
