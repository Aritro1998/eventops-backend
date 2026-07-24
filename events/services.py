from datetime import datetime, time
from rapidfuzz import process, fuzz

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db.models import Count, Q, F
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError

from venues.models import Space
from events.models import Event, Seat
from bookings.models import BookingSeat
from bookings.services import BookingService


class EventService:
    """
    All Event/Seat business logic that doesn't belong on the model itself
    or in a view — querying, seat generation, and seat-inventory syncing.
    Both EventViewSet (the API) and EventAdmin (the Django admin) call into
    this class for the same operations, so the two write paths can't drift
    apart into different behavior.
    """

    ALLOWED_ORDERINGS = {
        "price": "price",
        "-price": "-price",
        "start_time": "start_time",
        "-start_time": "-start_time",
    }
    
    @staticmethod
    def _alpha_row_label(n):
        """
        Convert a positive integer into an Excel-style alphabetic label.
        Examples:
            1  -> "A"
            2  -> "B"
            26 -> "Z"
            27 -> "AA"
            28 -> "AB"
            52 -> "AZ"
            53 -> "BA"
        Args:
            n (int): A positive integer (1-based).
        Returns:
            str: The corresponding alphabetic label.
        """
        label = ""
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            label = chr(65 + remainder) + label
        return label
    
    @staticmethod
    def build_seat_specs(space, total_seats_fallback):
        """
        Build the seat definitions for a venue space.
        If no space configuration is provided, or if the space uses general
        admission seating, seats are generated with sequential seat numbers only.
        For labeled seating, generates a specification for every seat based on
        the configured rows, columns, and label style. Each specification includes
        the seat number, row, column, and display label (e.g. "A1", "B5", or "2-3").
        """
        if space is None:
            return total_seats_fallback, [
                {"seat_number": i} for i in range(1, total_seats_fallback + 1)
            ]
        
        if space.seating_type == Space.SEATING_GENERAL:
            return space.capacity, [
                {"seat_number": i} for i in range(1, space.capacity + 1)
            ]
            
        specs = []
        seat_number = 1
        for r in range(1, space.rows + 1):
            row_label = (
                EventService._alpha_row_label(r)
                if space.label_style == Space.LABEL_ALPHA_NUMERIC
                else str(r)
            )
            for c in range(1, space.columns + 1):
                display_label = (
                    f"{row_label}{c}"
                    if space.label_style == Space.LABEL_ALPHA_NUMERIC
                    else f"{row_label}-{c}"
                )
                specs.append(
                    {
                        "seat_number": seat_number,
                        "row": r,
                        "column": c,
                        "display_label": display_label,
                    }
                )
                seat_number += 1
                
        return space.rows * space.columns, specs      
    
    @staticmethod
    def sync_seats_on_create(event):
        """Call once, right after a new Event is first saved (no seats exist yet)."""
        total_seats, seat_specs = EventService.build_seat_specs(event.space, event.total_seats)
        event.total_seats = total_seats
        event.save(update_fields=['total_seats'])
        Seat.objects.bulk_create([Seat(event=event, **spec) for spec in seat_specs])

    @staticmethod
    def _apply_space_layout(event):
        """
        Re-derive total_seats and Seat rows from event.space's CURRENT
        layout, replacing whatever seats currently exist. Shared by the
        space_changed branch of sync_seats_on_update (the event's space
        assignment itself just changed) and resync_seats_from_space (an
        explicit, deliberate refresh when the space's layout changed but
        the assignment didn't). Callers must already be inside an open
        transaction — this uses select_for_update().
        """
        total_seats, seat_specs = EventService.build_seat_specs(event.space, event.total_seats)
        event.total_seats = total_seats
        event.save(update_fields=['total_seats'])

        # Lock the event's existing seats before replacing them —
        # create_booking() locks a Seat row, not the Event row, so
        # without this a concurrent booking could commit in the gap
        # between the caller's own check and the delete below.
        old_seat_ids = list(
            Seat.objects.select_for_update().filter(event=event).values_list('id', flat=True)
        )
        has_any_booking = BookingSeat.objects.filter(seat_id__in=old_seat_ids).exists()
        if has_any_booking:
            raise DjangoValidationError(
                "Cannot regenerate seats for an event that already has booking history."
            )
        Seat.objects.filter(id__in=old_seat_ids).delete()
        Seat.objects.bulk_create([Seat(event=event, **spec) for spec in seat_specs])

    @staticmethod
    def sync_seats_on_update(event, old_total_seats, old_space_id):
        """
        Call after an existing Event is saved with possibly-changed total_seats/space.

        Deliberately does NOT recompute total_seats/Seat rows from the
        space's current layout on every save when the space assignment
        itself is unchanged — editing a Space's rows/columns elsewhere
        (venues/admin.py) must never silently change an already-existing
        Event's seat count just because that event happens to get resaved
        for something unrelated (renaming it, changing price). This used
        to recompute unconditionally here, which meant admin.py's own
        "total_seats is just a throwaway placeholder" note was actually
        wrong for a space-backed event's *second* save onward — it silently
        overwrote total_seats from whatever the space's layout had become
        by then, with no matching Seat regeneration unless the space FK
        itself had also changed. See EventAdmin.get_exclude/
        EventWriteSerializer, which now lock total_seats once a space is
        attached, and resync_seats_from_space for the explicit, deliberate
        way to pick up a Space's updated layout on an existing event.

        Event.clean() already validates this is safe (space can't change with
        any booking history; total_seats can't shrink past booked seats) —
        but that check runs before this transaction's row lock is acquired,
        so a concurrent booking could still slip in during the gap. The
        space-change branch below re-checks under the lock as the
        authoritative, race-safe guard, same reasoning as
        resize_plain_seats. BookingSeat.seat is on_delete=PROTECT regardless
        of booking status, so this can never be silently bypassed at the DB
        level either — these checks exist purely so the caller gets a clean,
        expected error instead of an unhandled ProtectedError.
        """
        space_changed = event.space_id != old_space_id
        if space_changed:
            EventService._apply_space_layout(event)
        elif not event.space_id and event.total_seats != old_total_seats:
            EventService.resize_plain_seats(event, old_total_seats)

    @staticmethod
    def resync_seats_from_space(event):
        """
        Explicitly re-derive total_seats/Seat rows from event.space's
        current layout, even though the space assignment itself hasn't
        changed — the deliberate, on-demand counterpart to
        sync_seats_on_update, which intentionally never does this
        automatically (see that method's docstring). This is what
        EventAdmin's "Resync seats from space" action calls.

        Opens its own transaction rather than relying on a caller-provided
        one, since (unlike sync_seats_on_update) this isn't always invoked
        from inside an existing save_model/perform_update atomic block.
        """
        if not event.space_id:
            raise DjangoValidationError(
                "This event has no space assigned — there is no layout to resync from."
            )

        with transaction.atomic():
            EventService._apply_space_layout(event)

    @staticmethod
    def has_booking_history(event):
        """
        True if this event has ever had a single booked seat, active or
        not. Mirrors exactly what BookingSeat.seat's on_delete=PROTECT
        already enforces at the database level — this exists purely so
        callers (perform_destroy below) can give a clean 400 error before
        attempting the delete, instead of letting Django raise a raw,
        unhandled ProtectedError.
        """
        return BookingSeat.objects.filter(seat__event=event).exists()

    @staticmethod
    def archive_event(event):
        """Retire an event from discovery/booking without touching its
        booking history — the only way to get rid of an event that
        has_booking_history() would otherwise block from real deletion."""
        event.is_archived = True
        event.save(update_fields=['is_archived'])

    @staticmethod
    def resize_plain_seats(event, old_total_seats):
        """
        Resize the sequential seat inventory for an event.

        If the total seat count increases, new seats are created with sequential
        seat numbers starting after the previous maximum.

        If the total seat count decreases, the reduction is blocked entirely
        if any booking — active or historical (CANCELLED/EXPIRED/old FAILED)
        — still references a seat being removed. Deleting a seat that still
        has *any* booking pointed at it would hit BookingSeat.seat's
        on_delete=PROTECT and crash with an unhandled ProtectedError;
        excluding only CONFIRMED bookings (the previous behavior here) isn't
        enough, since a historical booking still protects its seat. This
        check also re-runs under a row lock on the seats themselves (not just
        the Event row locked by the caller), since create_booking() locks a
        Seat row — without this, a concurrent booking on one of these seats
        could commit in the gap between Event.clean()'s check and this
        method's delete.
        """
        if event.total_seats > old_total_seats:
            Seat.objects.bulk_create([
                Seat(event=event, seat_number=i)
                for i in range(old_total_seats + 1, event.total_seats + 1)
            ])
        else:
            seat_ids_to_remove = list(
                Seat.objects.select_for_update().filter(
                    event=event, seat_number__gt=event.total_seats
                ).values_list('id', flat=True)
            )
            has_any_booking = BookingSeat.objects.filter(seat_id__in=seat_ids_to_remove).exists()
            if has_any_booking:
                raise DjangoValidationError(
                    "Cannot reduce total seats because some higher-numbered seats have booking history (active or past)."
                )
            Seat.objects.filter(id__in=seat_ids_to_remove).delete()
    
    @staticmethod
    def build_date_range_query(start_date, end_date):
        """Turn two "YYYY-MM-DD" strings into a Q object matching Event.start_time
        within that inclusive day range, in the server's local timezone."""
        parsed_start = parse_date(start_date)
        parsed_end = parse_date(end_date)

        if parsed_start is None:
            raise ValidationError({
                "date": "Invalid date format. Use YYYY-MM-DD."
            })

        if parsed_end is None:
            raise ValidationError({
                "date": "Invalid date format. Use YYYY-MM-DD."
            })
        
        if parsed_start > parsed_end:
            raise ValidationError({
                "date_range": "start_date cannot be after end_date."
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
        """Base queryset for every event-listing/detail use in the project
        (API list/retrieve, get_event_detail below, the AI's get_all_events
        tool). Annotates `available_seats` on the fly instead of storing it,
        so it's always correct even as bookings change. Excludes archived
        events — they're retired from discovery/booking but their data
        (and any existing bookings' own event reference) is untouched;
        Django admin manages them directly via Event.objects, bypassing
        this filter, since organizers still need to see/unarchive them."""
        queryset = Event.objects.filter(is_archived=False)

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

        # Same "taken" rule used everywhere else (BookingService's seat
        # availability checks): CONFIRMED always blocks; PENDING/FAILED
        # blocks only while its hold hasn't expired yet.
        return queryset.annotate(
            taken_seats=Coalesce(
                Count(
                    'seats__booking_seats',
                    filter=BookingService.active_booking_q(prefix='seats__booking_seats__booking__')
                ), 0
            ),
            available_seats=F('total_seats') - F('taken_seats')
        )
        
    @staticmethod
    def describe_seats(event, seat_numbers):
        """
        Presentation-only summary of a set of seat_numbers on an event —
        never used for identity/lookup (that's still plain seat_number
        everywhere else: PendingBooking.seat_numbers, BookingSeat, AI tool
        args). Used wherever a booking/draft needs to be *shown* to a
        user or the AI: general admission has no meaningful individual
        seat identity, so only a count is returned; labeled events return
        each seat's display_label (e.g. "A5"), falling back to the raw
        seat_number for any seat somehow missing one (pre-existing data
        from before display_label existed).

        Returns {"is_general_admission": bool, "seat_labels": [...], "seat_count": int}.
        """
        if event.is_general_admission:
            return {
                "is_general_admission": True,
                "seat_labels": [],
                "seat_count": len(seat_numbers),
            }

        label_by_seat_number = dict(
            Seat.objects.filter(
                event=event, seat_number__in=seat_numbers
            ).values_list('seat_number', 'display_label')
        )
        seat_labels = [
            label_by_seat_number.get(sn) or str(sn)
            for sn in seat_numbers
        ]
        return {
            "is_general_admission": False,
            "seat_labels": seat_labels,
            "seat_count": len(seat_numbers),
        }

    @staticmethod
    def get_event_detail(event_id):
        """Single event, with the same available_seats annotation as the
        list view — raises Event.DoesNotExist if event_id is invalid."""
        return EventService.get_events_with_available_seats().get(
            id=event_id
        )

    @staticmethod
    def get_available_seats(event_id):
        """
        Seats for this event that aren't currently taken, in seat_number order.
        Mirrors BookingService.get_unavailable_seat_ids so a seat shown here
        as available is never rejected as taken when actually booked, and
        vice versa: CONFIRMED is always taken, PENDING/FAILED only while
        their hold hasn't expired yet.
        """
        unavailable_seat_ids = BookingSeat.objects.filter(
            booking__event_id=event_id
        ).filter(
            BookingService.active_booking_q()
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
        """
        Fuzzy name search — the AI assistant's search_events tool uses this
        so users can type an approximate/misspelled event name and still
        resolve the correct event id. `score >= 80` is a WRatio threshold
        (0-100 scale); below that, treat it as "no real match" rather than
        returning a wrong event.
        """
        events = list(
            Event.objects.filter(is_archived=False).values(
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
            if score >= 80
        ]

        return Event.objects.filter(
            id__in=matching_ids
        )
        
