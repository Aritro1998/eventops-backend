from datetime import datetime, time
from rapidfuzz import process, fuzz

from django.utils import timezone
from django.db.models import Count, Q, F
from django.utils.dateparse import parse_date
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from events.models import Event, Seat
from bookings.models import Booking


class EventService:
    
    ALLOWED_ORDERINGS = {
        "price": "price",
        "-price": "-price",
        "start_time": "start_time",
        "-start_time": "-start_time",
    }
    
    @staticmethod
    def build_date_range_query(start_date, end_date):
        parsed_start = parse_date(start_date)
        parsed_end = parse_date(end_date)
        
        if parsed_start > parsed_end:
            raise ValidationError({
                "date_range": "start_date cannot be after end_date."
            })

        if parsed_start is None:
            raise ValidationError({
                "start_date": "Invalid date format. Use YYYY-MM-DD."
            })

        if parsed_end is None:
            raise ValidationError({
                "end_date": "Invalid date format. Use YYYY-MM-DD."
            })
            
        # Build the selected day's boundaries in the active timezone because start_time is timezone-aware.
        current_timezone = timezone.get_current_timezone()
        
        start_of_day = timezone.make_aware(
            datetime.combine(parsed_start, time.min),
            current_timezone
        )
        end_of_day = timezone.make_aware(
            datetime.combine(parsed_end, time.max),
            current_timezone
        )
            
        return Q(start_time__range=(start_of_day, end_of_day))

    @staticmethod
    def get_events_with_available_seats(date_filter=None, start_date=None, end_date=None, ordering=None):
        queryset = Event.objects.all()

        if date_filter: 
            # Keep this as a range on start_time so the database can use the start_time index.
            queryset = queryset.filter(EventService.build_date_range_query(date_filter, date_filter))
            
        elif start_date and end_date:     
            queryset = queryset.filter(EventService.build_date_range_query(start_date, end_date))
            
        if ordering:
            if ordering not in EventService.ALLOWED_ORDERINGS:
                raise ValidationError({
                    "ordering": f"Invalid ordering value. Allowed values are: {', '.join(EventService.ALLOWED_ORDERINGS.keys())}"
                })
            queryset = queryset.order_by(EventService.ALLOWED_ORDERINGS[ordering])
        else:
            queryset = queryset.order_by('start_time')  # Default ordering by start_time

        # Annotate each event with confirmed booking count and derived available seats.
        return queryset.annotate(
            confirmed_bookings=Coalesce(
                Count('bookings', filter=Q(bookings__status='CONFIRMED')),
                0
            ),
            available_seats=F('total_seats') - F('confirmed_bookings')
        )
        
    @staticmethod
    def get_event_detail(event_id):
        return EventService.get_events_with_available_seats().get(
            id=event_id
        )
        
    @staticmethod
    def get_available_seats(event_id):
        unavailable_seat_ids = Booking.objects.filter(
            event_id=event_id,
            status__in=['CONFIRMED', 'PENDING']
        ).values_list(
            "seat_id",
            flat=True
        )
        
        return Seat.objects.filter(
            event_id=event_id
        ).exclude(
            id__in=unavailable_seat_ids
        ).order_by('seat_number')

    @staticmethod
    def search_events_by_name(event_name):

        events = list(
            Event.objects.values(
                "id",
                "name"
            )
        )

        if not events:
            return Event.objects.none()

        choices = {
            event["name"]: event["id"]
            for event in events
        }

        matches = process.extract(
            event_name,
            choices.keys(),
            scorer=fuzz.WRatio,
            limit=5
        )

        matching_ids = [
            choices[name]
            for name, score, _ in matches
            if score >= 70
        ]

        return Event.objects.filter(
            id__in=matching_ids
        )