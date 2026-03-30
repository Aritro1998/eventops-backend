from django.contrib import admin
from .models import Booking


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "event", "seat", "payment", "retry_count", "created_at", "expires_at", "status", "amount"]
    list_filter = ["status", "event"]
    search_fields = ["user__username"]
    list_display_links = ["id", "user", "event"]