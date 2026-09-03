# tria_engine/apps/licensing/urls.py

from django.urls import path

from .views import (
    ReferralMeAPI,
    ReferralProgramSettingsAPI,
    ReferralProgramSummaryAPI,
    ReferralRedeemAPI,
    ReferralUsageListAPI,
)

urlpatterns = [
    path("referral/me/", ReferralMeAPI.as_view(), name="referral-me"),
    path("referral/redeem/", ReferralRedeemAPI.as_view(), name="referral-redeem"),
    path(
        "referral/program-settings/",
        ReferralProgramSettingsAPI.as_view(),
        name="referral-program-settings",
    ),
    path(
        "referral/summary/",
        ReferralProgramSummaryAPI.as_view(),
        name="referral-program-summary",
    ),
    path(
        "referral/usages/",
        ReferralUsageListAPI.as_view(),
        name="referral-usage-list",
    ),
]
