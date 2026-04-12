import logging

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from .models import Booking
from .services import BookingService
from core.throttles import BookingThrottle, DefaultThrottle
from core.pagination import CustomPagination
from payments.services import PaymentService
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
        key = request.data.get("idempotency_key")

        def respond_existing(booking):
            return Response(
                BookingReadSerializer(booking).data,
                status=status.HTTP_200_OK
            )

        # 1. Fast idempotency check (no locking)
        if key:
            existing = BookingService.get_existing_booking(user, key)
            if existing:
                logger.info(
                    "booking_idempotency_hit",
                    extra={
                        "event": "booking_idempotency_hit",
                        "user_id": user.id,
                        "booking_id": existing.id,
                    }
                )
                return respond_existing(existing)

        # 2. Validate request data
        serializer = BookingWriteSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        # 3. Delegate to service layer
        booking, seat_unavailable, is_existing = BookingService.create_booking(
            user=user,
            validated_data=serializer.validated_data,
            key=key
        )

        # Handle seat conflict
        if seat_unavailable:
            logger.warning(
                "booking_seat_unavailable",
                extra={
                    "event": "booking_seat_unavailable",
                    "user_id": user.id,
                    "event_id": serializer.validated_data["event"].id,
                    "seat_id": serializer.validated_data["seat"].id,
                }
            )
            return Response(
                {"detail": "Seat already booked"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Idempotent retry
        if is_existing:
            logger.info(
                "booking_idempotency_recovered",
                extra={
                    "event": "booking_idempotency_recovered",
                    "user_id": user.id,
                    "booking_id": booking.id,
                }
            )
            return respond_existing(booking)

        # 4. Trigger payment workflow (outside transaction)
        if booking.status == "PENDING":
            PaymentService.process_payment(booking.id)

        # Fetch optimized object (avoids N+1)
        booking = Booking.objects.select_related(
            "event", "seat", "payment"
        ).get(id=booking.id)

        # 5. Return response
        return Response(
            BookingReadSerializer(booking).data,
            status=status.HTTP_201_CREATED
        )

    def get(self, request):
        """
        List bookings for the authenticated user.

        Features:
        - Filtering by status
        - Pagination
        - Query optimization (select_related)
        """

        bookings = Booking.objects.filter(user=request.user)

        valid_statuses = [choice[0] for choice in Booking.STATUS_CHOICES]

        #  Optional filtering
        status_param = request.query_params.get("status")
        if status_param:
            if status_param not in valid_statuses:
                return Response(
                    {"detail": f"Invalid status filter. Valid options: {valid_statuses}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            bookings = bookings.filter(status=status_param)

        #  Avoid N+1 queries
        bookings = bookings.select_related("event", "seat", "payment").order_by("-created_at")

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
        booking = get_object_or_404(
            Booking.objects.select_related("event", "seat", "payment"),
            id=booking_id,
            user=request.user
        )

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

        booking = get_object_or_404(
            Booking.objects.select_related("event", "seat", "payment"),
            id=booking_id,
            user=request.user
        )

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

        booking = get_object_or_404(
            Booking.objects.select_related("event", "seat", "payment"),
            id=booking_id,
            user=request.user
        )

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
            payment = PaymentService.process_payment(booking.id)
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Refresh booking to get updated status
        booking.refresh_from_db()

        serializer = BookingReadSerializer(booking)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
