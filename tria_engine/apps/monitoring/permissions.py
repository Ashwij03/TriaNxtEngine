# tria_engine/apps/monitoring/permissions.py

from rest_framework.permissions import BasePermission

from .models import MONITORING_REQUESTER_ROLE_NAMES


def _role_name(user):
    role = getattr(user, "role", None)
    return (role.name if role else "").strip().lower()


def is_monitoring_requester(user):
    """CRA / Sponsor / CRO — allowed to submit monitoring access requests."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _role_name(user) in MONITORING_REQUESTER_ROLE_NAMES


def is_monitoring_approver(user):
    """Admin / Site Staff — allowed to approve/reject/revoke requests."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _role_name(user) in ("admin", "sitestaff", "site staff", "site_staff")


class CanSubmitMonitoringRequest(BasePermission):
    def has_permission(self, request, view):
        return is_monitoring_requester(request.user)


class CanDecideMonitoringRequest(BasePermission):
    def has_permission(self, request, view):
        return is_monitoring_approver(request.user)
