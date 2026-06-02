# tria_engine/apps/organizations/urls.py

from django.urls import path
from .views import OrganizationListCreateAPI, RoleListCreateAPI

urlpatterns = [
    path("", OrganizationListCreateAPI.as_view(), name="organization-list-create"),
    path("roles/", RoleListCreateAPI.as_view(), name="role-list-create"),
]