# tria_engine/apps/monitoring/urls.py

from django.urls import path

from .views import (
    MonitoringAccessCheckAPI,
    MonitoringAccessRequestDecisionAPI,
    MonitoringAccessRequestListCreateAPI,
)

urlpatterns = [
    path(
        "requests/",
        MonitoringAccessRequestListCreateAPI.as_view(),
        name="monitoring-request-list-create",
    ),
    path(
        "requests/<int:pk>/<str:action>/",
        MonitoringAccessRequestDecisionAPI.as_view(),
        name="monitoring-request-decision",
    ),
    path(
        "access-check/",
        MonitoringAccessCheckAPI.as_view(),
        name="monitoring-access-check",
    ),
]
