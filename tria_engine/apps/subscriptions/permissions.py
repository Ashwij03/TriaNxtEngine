# tria_engine/apps/subscriptions/permissions.py
#
# Role gating for the subscriptions app — built exactly the way
# monitoring/permissions.py is: a _role_name(user) helper that reads the
# Role.name string through the User.role FK (this codebase's convention;
# is_staff/is_superuser alone is never used), wrapped in a BasePermission
# subclass.

from rest_framework.permissions import BasePermission


def _role_name(user):
    role = getattr(user, "role", None)
    return (role.name if role else "").strip().lower()


class IsAdminRole(BasePermission):
    """Admin-only gate for plan/subscription mutation endpoints. Mirrors
    monitoring's CanDecideMonitoringRequest: superuser passes, otherwise
    the user's Role.name (via FK, case-insensitive) must be 'admin'."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return _role_name(request.user) == "admin"