# tria_engine/apps/billing/urls.py

from django.urls import path

from .views import (
    GatewayWebhookAPI,
    PlanTierDetailAPI,
    PlanTierListCreateAPI,
    SubscriptionAssignAPI,
    SubscriptionAutoRenewalAPI,
    SubscriptionCancelAPI,
    SubscriptionCheckoutAPI,
    SubscriptionConfirmAPI,
    SubscriptionMeAPI,
)

urlpatterns = [
    path("plans/", PlanTierListCreateAPI.as_view(), name="plan-tier-list-create"),
    path("plans/<int:pk>/", PlanTierDetailAPI.as_view(), name="plan-tier-detail"),
    path("subscription/me/", SubscriptionMeAPI.as_view(), name="subscription-me"),
    path(
        "subscription/checkout/",
        SubscriptionCheckoutAPI.as_view(),
        name="subscription-checkout",
    ),
    path(
        "subscription/confirm/",
        SubscriptionConfirmAPI.as_view(),
        name="subscription-confirm",
    ),
    path(
        "subscription/assign/",
        SubscriptionAssignAPI.as_view(),
        name="subscription-assign",
    ),
    path(
        "subscription/cancel/",
        SubscriptionCancelAPI.as_view(),
        name="subscription-cancel",
    ),
    path(
        "subscription/auto-renewal/",
        SubscriptionAutoRenewalAPI.as_view(),
        name="subscription-auto-renewal",
    ),
    # Gateway calls this directly — no session auth, verified by signature.
    path("webhooks/<str:gateway_name>/", GatewayWebhookAPI.as_view(), name="gateway-webhook"),
]
