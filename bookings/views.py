from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction, IntegrityError

from .serializers import BookingWriteSerializer, BookingReadSerializer
from .models import Booking
from events.models import Seat

# Create your views here.
class BookingCreateView(APIView):

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