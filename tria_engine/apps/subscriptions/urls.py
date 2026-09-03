# tria_engine/apps/subscriptions/urls.py

from django.urls import path

from .views import (
    PlanDetailAPI,
    PlanListCreateAPI,
    SubscriptionMeAPI,
    SubscriptionUsageAPI,
)

urlpatterns = [
    path("plans/", PlanListCreateAPI.as_view(), name="subscription-plan-list-create"),
    path("plans/<int:pk>/", PlanDetailAPI.as_view(), name="subscription-plan-detail"),
    path("me/", SubscriptionMeAPI.as_view(), name="subscription-me"),
    path("usage/", SubscriptionUsageAPI.as_view(), name="subscription-usage"),
]