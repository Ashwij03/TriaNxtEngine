# tria_engine/apps/licensing/views.py

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema

from . import services
from .models import ReferralProgramSettings
from .serializers import (
    ReferralProgramSettingsSerializer,
    ReferralRedeemRequestSerializer,
    ReferralStatsSerializer,
    ReferralUsageSerializer,
)


class ReferralMeAPI(APIView):
    """GET /api/licensing/referral/me/ — the signed-in user's own referral
    code, redemption count, and current license status. Creates the code
    on first access (R4 — idempotent, lazy creation), matching
    getReferralStatsForUser() in referralService.js."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        stats = services.get_referral_stats_for_user(request.user)

        # =====================================================
        # DATABASE VALIDATION CHANGE:
        # Data Retrieval Validation
        # Verify the referral code was actually created/found before
        # returning it — mirrors the "no organizations found" style check
        # in organizations/views.py.
        # =====================================================
        if not stats.get("code"):
            return Response(
                {"message": "Unable to generate a referral code for this user"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = ReferralStatsSerializer(stats)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ReferralRedeemAPI(APIView):
    """POST /api/licensing/referral/redeem/ — redeem a referral code on
    behalf of the signed-in user. Body: {"code": "ASH-7F3K9"}."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(request_body=ReferralRedeemRequestSerializer)
    def post(self, request):
        serializer = ReferralRedeemRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = services.redeem_referral_code(
            request.user, serializer.validated_data["code"]
        )

        if not result["ok"]:
            return Response(
                {"reason": result["reason"], "message": result["message"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "days_granted": result["days_granted"],
                "new_end_date": result["new_end_date"],
                "referrer_bonus_granted": result["referrer_bonus_granted"],
            },
            status=status.HTTP_200_OK,
        )


class ReferralProgramSettingsAPI(APIView):
    """
    GET  /api/licensing/referral/program-settings/  — admin-only: current
         R6 toggle state.
    PATCH /api/licensing/referral/program-settings/ — admin-only: flip the
         R6 toggle. Body: {"referrer_bonus_enabled": true}
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_superuser:
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        settings_row = ReferralProgramSettings.get_solo()
        serializer = ReferralProgramSettingsSerializer(settings_row)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=ReferralProgramSettingsSerializer)
    def patch(self, request):
        if not request.user.is_superuser:
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        enabled = request.data.get("referrer_bonus_enabled")
        if enabled is None:
            return Response(
                {"message": "referrer_bonus_enabled is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        settings_row = services.set_referrer_bonus_enabled(enabled)

        # =====================================================
        # DATABASE VALIDATION CHANGE:
        # Post-write verification — confirm the toggle actually persisted
        # as requested before returning success, matching the
        # "...creation validation failed" checks in organizations/views.py.
        # =====================================================
        settings_row.refresh_from_db()
        if settings_row.referrer_bonus_enabled != bool(enabled):
            return Response(
                {"message": "Referral program settings update validation failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = ReferralProgramSettingsSerializer(settings_row)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ReferralProgramSummaryAPI(APIView):
    """GET /api/licensing/referral/summary/ — admin-only: program-wide
    totals + top referrers, for the Referral Program admin settings
    screen."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_superuser:
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        summary = services.get_referral_program_summary()
        return Response(summary, status=status.HTTP_200_OK)


class ReferralUsageListAPI(APIView):
    """GET /api/licensing/referral/usages/ — admin-only: recent redemptions
    audit list, most recent first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_superuser:
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        usages = services.get_all_referral_usages()[:50]
        serializer = ReferralUsageSerializer(usages, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
