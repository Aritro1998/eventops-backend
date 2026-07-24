"""
Django admin is the primary way organizers manage Events/Seats in this
project (the API's EventViewSet still exists and works, but admin is where
the space-aware seat generation actually gets exercised day to day).
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from .models import Event, Seat
from .services import EventService
from venues.models import Space
from core.admin_widgets import ChainedSelect


class SpaceSelect(ChainedSelect):
    """Only offer Spaces belonging to whichever Venue is currently
    selected — see static/admin/chained_select.js for the client-side
    filtering this drives."""
    parent_field_id = "id_venue"

    def get_parent_map(self):
        return dict(Space.objects.values_list("id", "venue_id"))


class EventAdminForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = "__all__"
        widgets = {"space": SpaceSelect}


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """
    Note: total_seats is still a required field on the admin form when
    creating a brand-new event with a space selected — Django doesn't know
    ahead of time that save_model below is about to overwrite it with the
    space's real capacity, so typing any positive placeholder number
    (e.g. 1) is enough; it gets replaced automatically. Once an event
    already has a space assigned, total_seats is excluded from the form
    entirely (see get_exclude) — it's derived, not editable, from then on.
    Use the "Resync seats from space's current layout" action to
    deliberately pick up a changed Space layout on an existing event.
    """
    form = EventAdminForm
    list_display = ["id", "name", "venue", "space", "start_time", "end_time", "total_seats", "price", "is_archived"]
    list_filter = ["venue", "is_archived"]
    search_fields = ["name"]
    list_display_links = ["id", "name"]
    actions = ["archive_events", "resync_seats_from_space"]

    class Media:
        js = ["admin/chained_select.js"]

    def _is_admin(self, request):
        return request.user.is_superuser or getattr(request.user, "role", None) == "ADMIN"

    def get_queryset(self, request):
        """Mirrors IsAdminOrOrganizer.has_object_permission on the API side:
        admins see everything, organizers see only events they created,
        anyone else (shouldn't normally have change_event permission at
        all, but just in case) sees nothing."""
        qs = super().get_queryset(request)
        if self._is_admin(request):
            return qs
        if getattr(request.user, "role", None) == "ORGANIZER":
            return qs.filter(created_by=request.user)
        return qs.none()

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or self._is_admin(request):
            return True
        return (
            getattr(request.user, "role", None) == "ORGANIZER"
            and obj.created_by_id == request.user.id
        )

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or self._is_admin(request):
            return True
        return (
            getattr(request.user, "role", None) == "ORGANIZER"
            and obj.created_by_id == request.user.id
        )

    def get_exclude(self, request, obj=None):
        """Organizers can't hand-pick created_by — same as the API's
        perform_create, which forces it to request.user regardless of
        what the client submits.

        total_seats is additionally excluded once an event already has a
        space assigned (obj is the pre-edit DB instance, so this reflects
        the space assignment as of before this save) — it's derived from
        the space's layout and must never be overwritten by a stale
        resubmitted form value now that sync_seats_on_update no longer
        recomputes it on every save. A brand-new event (obj is None) still
        needs it as a placeholder, and a fully custom event (no space)
        still edits it directly — see EventService.sync_seats_on_update
        and resync_seats_from_space."""
        exclude = list(super().get_exclude(request, obj) or ())
        if not self._is_admin(request):
            exclude.append("created_by")
        if obj is not None and obj.space_id:
            exclude.append("total_seats")
        return exclude

    def archive_events(self, request, queryset):
        """
        Bulk action: the intended way to retire an event with booking
        history. Real deletion is still available via the default "Delete
        selected" action, but Django will refuse it (showing its own
        protected-objects page) the moment an event has ever had a single
        booking — see EventViewSet.perform_destroy for the same rule
        enforced on the API side with a clean error instead of Django's
        default page.
        """
        updated = queryset.update(is_archived=True)
        self.message_user(request, f"Archived {updated} event(s).")

    archive_events.short_description = "Archive selected events"

    def resync_seats_from_space(self, request, queryset):
        """
        The deliberate, on-demand way to make selected events pick up
        their Space's CURRENT layout. Editing a Space's rows/columns
        elsewhere (venues/admin.py) never automatically flows into an
        already-existing Event — see EventService.sync_seats_on_update's
        docstring for why — so this is the explicit, auditable action for
        when that IS what you want.
        """
        resynced = 0
        skipped_no_space = 0
        errors = []

        for event in queryset:
            if not event.space_id:
                skipped_no_space += 1
                continue
            try:
                EventService.resync_seats_from_space(event)
                resynced += 1
            except DjangoValidationError as e:
                messages_list = e.messages if hasattr(e, "messages") else [str(e)]
                errors.append(f"{event.name}: {'; '.join(messages_list)}")

        if resynced:
            self.message_user(request, f"Resynced seats for {resynced} event(s) from their space.")
        if skipped_no_space:
            self.message_user(
                request,
                f"Skipped {skipped_no_space} event(s) with no space assigned.",
                level=messages.WARNING,
            )
        if errors:
            self.message_user(request, "Could not resync: " + " | ".join(errors), level=messages.ERROR)

    resync_seats_from_space.short_description = "Resync seats from space's current layout"

    def save_model(self, request, obj, form, change):
        """
        Save an event and synchronize its associated seats via EventService.
        Event.clean() (run by the admin form before this is called) has
        already validated the change is safe.
        """
        with transaction.atomic():
            if change:
                previous = Event.objects.select_for_update().get(pk=obj.pk)
                old_total_seats = previous.total_seats
                old_space_id = previous.space_id
                obj.save()
                EventService.sync_seats_on_update(obj, old_total_seats, old_space_id)
            else:
                if not self._is_admin(request):
                    obj.created_by = request.user
                obj.save()
                EventService.sync_seats_on_create(obj)


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    """Seats are normally managed indirectly through EventAdmin.save_model,
    not created/edited here directly — this registration exists mainly for
    inspecting/spot-fixing individual seat rows."""
    list_display = ["id", "event", "seat_number", "row", "column", "display_label"]
    list_filter = ["event"]
    search_fields = ["seat_number", "event__name"]
    list_display_links = ["id", "event"]

    def _is_admin(self, request):
        return request.user.is_superuser or getattr(request.user, "role", None) == "ADMIN"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self._is_admin(request):
            return qs
        if getattr(request.user, "role", None) == "ORGANIZER":
            return qs.filter(event__created_by=request.user)
        return qs.none()

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or self._is_admin(request):
            return True
        return (
            getattr(request.user, "role", None) == "ORGANIZER"
            and obj.event.created_by_id == request.user.id
        )

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or self._is_admin(request):
            return True
        return (
            getattr(request.user, "role", None) == "ORGANIZER"
            and obj.event.created_by_id == request.user.id
        )
