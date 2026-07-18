from django.contrib import admin
from ai_assistant.models import PendingBooking, PendingBookingCancellation, PendingPaymentRetry


# Mostly for debugging a specific user's stuck/confusing draft state — in
# normal operation these rows are created/deleted by the AI tools and
# action views (see ai_assistant/models.py's module docstring), not admin.
@admin.register(PendingBooking)
class PendingBookingAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "event", "amount", "created_at", "expires_at"]
    list_filter = ["event"]
    search_fields = ["user__username"]
    list_display_links = ["id", "user", "event"]


@admin.register(PendingBookingCancellation)
class PendingBookingCancellationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "booking", "created_at", "expires_at"]
    search_fields = ["user__username", "booking__id"]
    list_display_links = ["id", "user", "booking"]


@admin.register(PendingPaymentRetry)
class PendingPaymentRetryAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "booking", "created_at", "expires_at"]
    search_fields = ["user__username", "booking__id"]
    list_display_links = ["id", "user", "booking"]