from django.contrib import admin
from .models import Booking, BookingSeat


class BookingSeatInline(admin.TabularInline):
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
    list_display = [
        "id", "booking", "seat",
    ]
    search_fields = [
        "booking__user__username",
    ]
