"""
Keeps the "Organizers" Django-admin Group in sync with the business-level
role field. role can change from several places (the admin's own User
change form, the users API, a script) and a signal is the one hook
guaranteed to run for all of them — see bookings/signals.py's module
docstring for why this project reserves signals for exactly this kind of
"must fire no matter which code path triggers it" case.

Group membership only opens Django's own model-level permissions
(change_event, delete_event, ...) — the "front door lock" described when
this was set up. Which specific events an organizer can then touch is
still enforced separately by EventAdmin/SeatAdmin's get_queryset /
has_change_permission overrides in events/admin.py.
"""

from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_save
from django.dispatch import receiver

from users.models import User

ORGANIZER_GROUP_NAME = "Organizers"
ORGANIZER_PERMISSION_CODENAMES = [
    "add_event", "change_event", "delete_event", "view_event",
    "add_seat", "change_seat", "delete_seat", "view_seat",
]


def get_or_create_organizer_group():
    group, created = Group.objects.get_or_create(name=ORGANIZER_GROUP_NAME)
    if created:
        # Only set permissions at creation time, not on every call — an
        # admin who later tweaks this group's permissions by hand in
        # /admin/auth/group/ shouldn't have that fought on the next
        # unrelated user save.
        permissions = Permission.objects.filter(
            content_type__app_label="events",
            codename__in=ORGANIZER_PERMISSION_CODENAMES,
        )
        group.permissions.set(permissions)
    return group


@receiver(post_save, sender=User)
def sync_organizer_group(sender, instance, **kwargs):
    group = get_or_create_organizer_group()
    if instance.role == "ORGANIZER":
        instance.groups.add(group)
    else:
        instance.groups.remove(group)
