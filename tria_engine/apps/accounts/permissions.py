# accounts/permissions.py

from rest_framework.permissions import BasePermission


class IsSuperUserOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return request.user.is_superuser


class IsAuthenticatedAndSameOrganizationPatientAccess(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True
        # Allow if user has no organization (will be Forbidden)
        if not user.organization_id:
            return False
        return obj.site_id == user.organization_id


class IsAuthenticatedAndSameOrganizationRoleAccess(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True
        if not user.organization_id:
            return False
        return obj.organization_id == user.organization_id