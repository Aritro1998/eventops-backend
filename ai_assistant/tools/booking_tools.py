from bookings.services import BookingService
from bookings.serializers import BookingReadSerializer

def get_my_bookings(user, status=None):
    print("=> Executing get_my_bookings tool with status:", status)
    bookings = BookingService.get_user_bookings(user, status_filter=status)
    serializer = BookingReadSerializer(bookings, many=True)
    return serializer.data
