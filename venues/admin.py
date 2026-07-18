from django.contrib import admin
from venues.models import Venue, Space


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'city', 'created_by', 'created_at')
    search_fields = ('name', 'city')


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    # total_seats works directly here even though it's a @property, not a
    # model field — Django admin's list_display accepts any callable
    # attribute on the model, not just real columns.
    list_display = ('id', 'name', 'venue', 'seating_type', 'total_seats')
    list_filter = ('seating_type', 'venue')
    search_fields = ('name', 'venue__name')
