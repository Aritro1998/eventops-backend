"""
The core domain models: an Event happens at a point in time and has a pool
of Seats. Everything else in the project (bookings, payments, the AI
assistant) ultimately revolves around booking one or more of an Event's
Seat rows.
"""

from django.db import models
from django.db.models import Q, F
from django.contrib.postgres.indexes import GinIndex

from users.models import User
from venues.models import Venue, Space


class Event(models.Model):
    """
    A single bookable event. `venue`/`space` are both optional — an event
    with neither is fully custom (organizer types total_seats by hand, no
    RAG-answerable venue info). An event with a `space` gets its seats
    auto-generated from that space's layout (see EventService.build_seat_specs)
    and `total_seats` becomes derived rather than freely editable.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    total_seats = models.IntegerField()
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    venue = models.ForeignKey(Venue, null=True, blank=True, on_delete=models.SET_NULL, related_name='events')
    space = models.ForeignKey(Space, null=True, blank=True, on_delete=models.SET_NULL, related_name='events')
    # The way to retire an event that has any booking history — real
    # deletion is blocked once a single Booking has ever referenced it
    # (BookingSeat.seat is on_delete=PROTECT), by design: this project
    # never destroys booking history (see BookingSeat.release_seats).
    # Archiving hides the event from discovery/booking without touching
    # that history at all.
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def clean(self):
        """
        Validate the event before it is saved.
        
        This method enforces business rules that cannot be expressed using field-level validation alone, including:
        - A space cannot be assigned unless a venue is also selected.
        - The selected space must belong to the selected venue.
        - An event's space cannot be changed once it has any booking history
        (active or past) — BookingSeat.seat is on_delete=PROTECT regardless
        of status, so this must never be narrower than "any booking at all."
        - The total seat count cannot be reduced below the highest confirmed
        seat number, or while higher-numbered seats have any booking history.

        Raises:
            ValidationError: If any of the above validation rules are violated.
        """
        from django.core.exceptions import ValidationError
        
        # Ensure a space is never assigned without an associated venue.
        if self.space and not self.venue:
            raise ValidationError("A space cannot be set without its venue.")
        
        # Ensure the selected space belongs to the selected venue.
        if self.space and self.venue and self.space.venue_id != self.venue_id:
            raise ValidationError("The selected space does not belong to the selected venue.")

        # The remaining validations apply only when updating an existing event
        if self.pk:
            from bookings.models import BookingSeat
            from django.db.models import Max
            from events.services import EventService

            # Retrieve the current persisted version for comparison
            previous = Event.objects.get(pk=self.pk)

            # Prevent changing the venue space after ANY booking history —
            # not just active bookings. BookingSeat.seat is on_delete=PROTECT
            # regardless of booking status, so even a long-cancelled booking
            # would make the seat-replacement in
            # EventService.sync_seats_on_update crash with an unhandled
            # ProtectedError if this only checked active bookings.
            if self.space_id != previous.space_id:
                if EventService.has_booking_history(self):
                    raise ValidationError("Cannot change the space for an event that already has booking history.")

            # Validate seat reduction only when the total seat count decreases
            elif self.total_seats < previous.total_seats:
                # Find the highest seat number that has been confirmed
                max_booked_seat = BookingSeat.objects.filter(
                    booking__event=self, booking__status='CONFIRMED'
                ).aggregate(max_seat_number=Max('seat__seat_number'))['max_seat_number'] or 0

                if self.total_seats < max_booked_seat:
                    raise ValidationError(f"Cannot reduce total seats below the highest booked seat number ({max_booked_seat}).")

                # Ensure no booking history — active or past — exists for
                # seats that would be removed. Same PROTECT-crash reasoning
                # as the space-change check above.
                has_booking_history_above_limit = BookingSeat.objects.filter(
                    seat__event=self, seat__seat_number__gt=self.total_seats
                ).exists()
                if has_booking_history_above_limit:
                    raise ValidationError("Cannot reduce total seats because some higher-numbered seats have booking history (active or past).")

    @property
    def is_general_admission(self):
        """True when this event's seats are anonymous tickets rather than
        specific labeled seats — the single source of truth every GA-aware
        code path (booking tools, presentation logic) checks, instead of
        each re-deriving `space.seating_type` independently."""
        return self.space is not None and self.space.seating_type == Space.SEATING_GENERAL
    
    class Meta:
        ordering = ['-created_at']
        
        # Add a GIN index on the name field for efficient trigram searches
        # Trigram search means that the search will match substrings of the name, not just exact matches or prefixes.
        indexes = [
            GinIndex(fields=['name'], name='event_name_trgm_idx', opclasses=['gin_trgm_ops']),
        ]
        
        # Ensure total_seats is positive and end_time is after start_time
        constraints = [
            models.CheckConstraint(
                condition=Q(total_seats__gt=0), 
                name='total_seats_non_negative'
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=F('start_time')), 
                name='end_time_after_start_time'
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0),
                name='price_non_negative'
            ),
        ]


class Seat(models.Model):
    """
    One bookable slot within an Event. `seat_number` is the canonical
    integer identity — always present, unique per event, used everywhere
    seats are referenced (bookings, AI tool arguments). `row`/`column`/
    `display_label` are purely cosmetic and only populated for labeled
    (reserved-seating) events; general admission seats leave them null —
    there's no meaningful "seat" to show the user, only a count.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.IntegerField()
    row = models.PositiveIntegerField(null=True, blank=True)
    column = models.PositiveIntegerField(null=True, blank=True)
    display_label = models.CharField(max_length=20, null=True, blank=True)

    def __str__(self):
        return f'Seat {self.seat_number} (Event {self.event_id})'
    
    class Meta:
        ordering = ['seat_number']
        # Ensure seat_number is unique per event and positive
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'seat_number'], 
                name='unique_seat_number_per_event'
            ),
            models.CheckConstraint(
                condition=Q(seat_number__gt=0), 
                name='seat_number_positive'
            ),
            models.UniqueConstraint(
                fields=['event', 'row', 'column'],
                condition=Q(row__isnull=False, column__isnull=False),
                name='unique_grid_position_per_event',
            ),
        ]
        
            