from django.urls import path
from .views import BookingListView, BookingDetailView, BookingCancelView

urlpatterns = [
    path('', BookingListView.as_view(), name='booking-list'),
    path('<int:booking_id>/', BookingDetailView.as_view(), name='booking-detail'),
    path('<int:booking_id>/cancel/', BookingCancelView.as_view(), name='booking-cancel'),
]