from rest_framework.permissions import BasePermission

class IsAdminOrOrganizer(BasePermission):
    """
    Custom permission to only allow create/edit access for admins or organizers.
    - Admins have full access to all events.
    - Organizers can only manage events they have created.
    - Read-only access is allowed to everyone.
    """

    def has_permission(self, request, view):
        """
        Triggers before any object is retrieved from the database.
        Ensure that only authenticated users with the role of 'ADMIN' or 'ORGANIZER' can access the view.
        """
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            # Allow read-only access to everyone
            return True

        return (
            request.user.is_authenticated and 
            getattr(request.user, "role", None) in ['ADMIN', 'ORGANIZER']
        )

    def has_object_permission(self, request, view, obj):
        """
        Triggers after object is retrieved from the database.
        Ensure that:
        - Admins have full access to all events.
        - Organizers can only access events they have created.
        """
        user = request.user

        if not user.is_authenticated:
            return False

        # Admins have full access
        if user.role == 'ADMIN':
            return True

        # Organizers can access their own events
        if obj.created_by_id == user.id:
            return True

        return False