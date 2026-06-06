from datetime import datetime, time

from django.utils import timezone
from django.db.models import Count, Q, F
from django.utils.dateparse import parse_date
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from events.models import Event


class EventService:

    @staticmethod
    def get_events_with_available_seats(date_filter=None):
        queryset = Event.objects.all()

        if date_filter:
            # Query params arrive as strings, so parse the expected YYYY-MM-DD format first.
            filter_date = parse_date(date_filter)

            if filter_date is None:
                raise ValidationError({
                    "date": "Invalid date format. Use YYYY-MM-DD."
                })

            # Build the selected day's boundaries in the active timezone because start_time is timezone-aware.
            current_timezone = timezone.get_current_timezone()
            start_of_day = timezone.make_aware(
                datetime.combine(filter_date, time.min),
                current_timezone
            )
            end_of_day = timezone.make_aware(
                datetime.combine(filter_date, time.max),
                current_timezone
            )
            
            # Keep this as a range on start_time so the database can use the start_time index.
            queryset = queryset.filter(start_time__range=(start_of_day, end_of_day))

        # Annotate each event with confirmed booking count and derived available seats.
        return queryset.annotate(
            confirmed_bookings=Coalesce(
                Count('bookings', filter=Q(bookings__status='CONFIRMED')),
                0
            ),
            available_seats=F('total_seats') - F('confirmed_bookings')
        ).order_by('start_time')
        
