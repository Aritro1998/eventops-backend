from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from rest_framework.permissions import IsAuthenticated

from .models import Booking
from events.models import Seat
from core.pagination import CustomPagination
from .serializers import BookingWriteSerializer, BookingReadSerializer

# Create your views here.
class BookingListView(APIView):
    permission_classes = [IsAuthenticated]

    def get_existing_booking(self, user, key):
        return Booking.objects.select_related("event", "seat").filter(
            user=user,
            idempotency_key=key
        ).first()

    def post(self, request):
        """
        Create a booking with idempotency and concurrency safety.

        This endpoint ensures that seat booking is safe under concurrent requests
        and supports idempotent retries from clients.

        Flow:
        1. Idempotency Pre-check (Fast Path):
        - If an `idempotency_key` is provided, check if a booking already exists
            for the given (user, idempotency_key).
        - If found, return the existing booking (HTTP 200) to avoid duplicate processing.

        2. Input Validation:
        - Validate request data using BookingWriteSerializer.
        - Ensures seat-event mapping, amount validity, and basic constraints.

        3. Concurrency Control (Atomic Transaction):
        - Start a database transaction.
        - Lock the selected seat row using `select_for_update()` to prevent
            concurrent modifications.
        - Re-check idempotency inside the transaction to handle race conditions.
        - Verify that no CONFIRMED booking already exists for the seat.
        - Create the booking using the locked seat instance.

        4. Database Constraint Handling:
        - In case of a race condition, the database may raise an IntegrityError
            (e.g., due to unique constraints on idempotency key).
        - Catch the exception and return the already created booking instead
            of failing the request.

        5. Response:
        - Return the created (or existing) booking using BookingReadSerializer.
        - HTTP 201 for new booking, HTTP 200 for idempotent retry.

        Key Guarantees:
        - Prevents double booking of seats.
        - Ensures idempotent behavior for retry-safe APIs.
        - Maintains data integrity using both application logic and database constraints.

        Args:
            request: HTTP request containing booking data:
                - event (int)
                - seat (int)
                - amount (decimal)
                - idempotency_key (optional, string)

        Returns:
            Response:
                - 201 Created: Booking successfully created.
                - 200 OK: Existing booking returned (idempotent retry).
                - 400 Bad Request: Validation or business rule failure.
        """
        
        user = request.user
        key = request.data.get("idempotency_key")

        # 1️⃣ Fast path — idempotency pre-check
        if key:
            existing = self.get_existing_booking(user, key)
            if existing:
                return Response(
                    BookingReadSerializer(existing).data,
                    status=status.HTTP_200_OK
                )

        # 2️⃣ Validate input
        serializer = BookingWriteSerializer(
            data=request.data,
            context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():

                # 3️⃣ Lock seat
                seat = Seat.objects.select_for_update().get(
                    id=serializer.validated_data["seat"].id
                )

                # 4️⃣ Re-check idempotency within transaction
                if key:
                    existing = self.get_existing_booking(user, key)
                    if existing:
                        return Response(
                            BookingReadSerializer(existing).data,
                            status=status.HTTP_200_OK
                        )

                # 5️⃣ Check seat availability
                if Booking.objects.filter(
                    seat=seat,
                    status="CONFIRMED"
                ).exists():
                    return Response(
                        {"detail": "Seat already booked"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # 6️⃣ Create booking using locked seat
                booking = serializer.save(seat=seat)

        except IntegrityError:
            # 7️⃣ Handle race condition via DB constraint
            if key:
                existing = self.get_existing_booking(user, key)
                if existing:
                    return Response(
                        BookingReadSerializer(existing).data,
                        status=status.HTTP_200_OK
                    )
            raise  # re-raise if unknown error

        # 8️⃣ Optimized fetch
        booking = Booking.objects.select_related("event", "seat").get(id=booking.id)

        # 9️⃣ Return response
        return Response(
            BookingReadSerializer(booking).data,
            status=status.HTTP_201_CREATED
        )
    
    def get(self, request):
        booking = Booking.objects.filter(user=request.user)
        valid_statuses = [choice[0] for choice in Booking.STATUS_CHOICES]

        # Optional filtering by status (e.g., ?status=CONFIRMED)
        status_param = request.query_params.get("status")
        if status_param:
            if status_param not in valid_statuses:
                return Response(
                    {"detail": f"Invalid status filter. Valid options: {valid_statuses}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            booking = booking.filter(status=status_param)

        # Optimize query with select_related to avoid N+1 problem
        booking = booking.select_related("event", "seat")

        # Apply pagination
        paginator = CustomPagination()
        paginated_booking = paginator.paginate_queryset(booking, request)

        # If pagination is applied, return paginated response
        if paginated_booking is not None:
            serializer = BookingReadSerializer(paginated_booking, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = BookingReadSerializer(booking, many=True)
        return Response(serializer.data)
    

class BookingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, booking_id):
        booking = get_object_or_404(
            Booking.objects.select_related("event", "seat"), id=booking_id, user=request.user
        )
        serializer = BookingReadSerializer(booking)
        return Response(serializer.data)


class BookingCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, booking_id):
        booking = get_object_or_404(
            Booking.objects.select_related("event", "seat"), id=booking_id, user=request.user
        )

        if booking.status != "CONFIRMED":
            return Response(
                {"detail": "Only CONFIRMED bookings can be CANCELLED."},
                status=status.HTTP_400_BAD_REQUEST
            )

        booking.status = "CANCELLED"
        booking.save()

        serializer = BookingReadSerializer(booking)
        return Response(serializer.data)


# class BookingListView(ListAPIView):
#     serializer_class = BookingReadSerializer
#     permission_classes = [IsAuthenticated]

#     def get_queryset(self):
#         return Booking.objects.filter(user=self.request.user).select_related("event", "seat")