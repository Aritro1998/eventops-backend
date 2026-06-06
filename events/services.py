from events.models import Event
from django.db.models import Count, Q, F


class EventService:

    @staticmethod
    def get_events_with_available_seats():
        return Event.objects.annotate(
            confirmed_bookings=Count(
                'bookings',
                filter=Q(bookings__status='CONFIRMED')
            ),
            available_seats=F('total_seats') - F('confirmed_bookings')
        ).order_by('start_time')