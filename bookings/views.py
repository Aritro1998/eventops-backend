import logging

from rest_framework import status
from django.http import Http404
from django.db import OperationalError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .services import BookingService
from core.pagination import CustomPagination
from payments.services import PaymentService
from core.throttles import BookingThrottle, DefaultThrottle
from .serializers import BookingWriteSerializer, BookingReadSerializer

logger = logging.getLogger(__name__)


class BookingListView(APIView):
    """
    Handles:
    - POST → Create booking
    - GET → List bookings
    """

    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        """
        Apply throttling only to POST requests to prevent abuse of booking creation.
        GET requests (listing) are not throttled to allow users to view their bookings without limits.
        get_throttles is called for every request, so we can conditionally apply throttling based on the HTTP method.
        """
        if self.request.method == "POST":
            return [BookingThrottle()]
        return [DefaultThrottle()]

    def post(self, request):
        """
        Create a booking with:

        ✔ Idempotency (safe retries)
        ✔ Concurrency control (row locking)
        ✔ Transaction safety
        ✔ Payment integration

        Flow:
        1. Fast idempotency check (no DB locks)
        2. Validate request
        3. Delegate business logic to BookingService
        4. Trigger payment workflow
        5. Return final booking state
        """
        
        user = request.user
        
        try:
            # Validate request data
            serializer = BookingWriteSerializer(
                data=request.data,
                context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            booking, is_existing = BookingService.create_booking_for_user(
                user=request.user,
                event=serializer.validated_data["event"],
                seat_ids=[seat.id for seat in serializer.validated_data["seats"]],
                idempotency_key=request.data.get("idempotency_key")
            )
        except OperationalError:
            logger.warning(
                "booking_create_database_contention",
                extra={
                    "event": "booking_create_database_contention",
                    "user_id": user.id,
                }
            )
            return Response(
                {"detail": "Booking could not be completed right now. Please retry."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            BookingReadSerializer(booking).data,
            status=status.HTTP_200_OK if is_existing else status.HTTP_201_CREATED
        )

    def get(self, request):
        """
        List bookings for the authenticated user.

        Features:
        - Filtering by status
        - Pagination
        - Query optimization (select_related)
        """
        
        status_param = request.query_params.get("status")
        
        try:
            bookings = BookingService.get_user_bookings(
                user=request.user, 
                status_filter=status_param
            )
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        #  Pagination
        paginator = CustomPagination()
        paginated = paginator.paginate_queryset(bookings, request)

        if paginated is not None:
            serializer = BookingReadSerializer(paginated, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = BookingReadSerializer(bookings, many=True)
        return Response(serializer.data)


class BookingDetailView(APIView):
    """
    Retrieve a single booking.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, booking_id):
        booking = BookingService.get_booking_for_user(booking_id, request.user)
        if booking is None:
            raise Http404

        serializer = BookingReadSerializer(booking)
        return Response(serializer.data)


class BookingCancelView(APIView):
    """
    Cancel a booking.

    Business rule:
    - Only CONFIRMED bookings can be cancelled
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]

    def post(self, request, booking_id):

        booking = BookingService.get_booking_for_user(booking_id, request.user)
        if booking is None:
            raise Http404

        try:
            booking = BookingService.cancel_booking(booking)
        except ValueError as e:
            logger.warning(
                "booking_cancel_rejected",
                extra={
                    "event": "booking_cancel_rejected",
                    "user_id": request.user.id,
                    "booking_id": booking.id,
                    "status": booking.status,
                }
            )
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = BookingReadSerializer(booking)
        return Response(serializer.data)
    

class BookingRetryPaymentView(APIView):
    """
    Retry payment for a booking.

    Business rules:
    - Only PENDING, FAILED bookings can retry payment
    - Max 3 retries allowed
    - Booking expires after 15 minutes or 3 failed attempts
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [BookingThrottle]

    def post(self, request, booking_id):

        booking = BookingService.get_booking_for_user(booking_id, request.user)
        if booking is None:
            raise Http404

        # Validate booking status
        if booking.status not in ["FAILED", "PENDING"]:
            logger.warning(
                "booking_retry_payment_rejected",
                extra={
                    "event": "booking_retry_payment_rejected",
                    "user_id": request.user.id,
                    "booking_id": booking.id,
                    "status": booking.status,
                }
            )
            return Response(
                {"detail": "Payment cannot be retried for this booking status."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            PaymentService.process_payment(booking.id)
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Refresh booking to get updated status
        booking.refresh_from_db()

        serializer = BookingReadSerializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
