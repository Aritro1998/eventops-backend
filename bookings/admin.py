from django.contrib import admin
from .models import Booking


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "event", "seat", "status", "amount"]
    list_filter = ["status", "event"]
    search_fields = ["user__username"]