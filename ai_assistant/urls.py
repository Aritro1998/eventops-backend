from django.urls import path
from . import views

urlpatterns = [
    path('chat/', views.ChatView.as_view(), name='chat'),
    path('actions/confirm-pending-booking/', views.ConfirmPendingBookingActionView.as_view(), name='confirm_pending_booking'),
    path('actions/cancel-pending-booking/', views.CancelPendingBookingActionView.as_view(), name='cancel_pending_booking'),
    path('actions/confirm-cancel-booking/', views.ConfirmCancellationActionView.as_view(), name='confirm_cancel_booking'),
    path('actions/keep-booking/', views.DismissCancellationActionView.as_view(), name='keep-booking'),
    path('actions/confirm-payment-retry/', views.ConfirmPaymentRetryActionView.as_view(), name='confirm_payment_retry'),
    path('actions/dismiss-payment-retry/', views.DismissPaymentRetryActionView.as_view(), name='dismiss_payment_retry'),
]