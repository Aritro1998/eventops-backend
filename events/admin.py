from django.contrib import admin
from .models import Event, Seat


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "start_time", "end_time", "total_seats"]
    search_fields = ["name"]


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ["id", "event", "seat_number"]
    list_filter = ["event"]