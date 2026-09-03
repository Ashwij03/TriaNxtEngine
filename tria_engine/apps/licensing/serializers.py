# tria_engine/apps/licensing/serializers.py

from rest_framework import serializers

from .models import (
    LicenseEntitlement,
    MAX_REDEMPTIONS_PER_CODE,
    ReferralCode,
    ReferralProgramSettings,
    ReferralUsage,
)


class ReferralCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralCode
        fields = ["id", "code", "redemption_count", "active", "created_at"]
        read_only_fields = fields


class LicenseEntitlementSerializer(serializers.ModelSerializer):
    is_referral_license_active = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()

    class Meta:
        model = LicenseEntitlement
        fields = [
            "subscription_end_date",
            "referral_extension_end_date",
            "referral_extension_days_total",
            "is_referral_license_active",
            "days_remaining",
            "last_checked_at",
        ]
        read_only_fields = fields

    def get_is_referral_license_active(self, obj):
        return obj.is_referral_license_active()

    def get_days_remaining(self, obj):
        return obj.days_remaining()


class ReferralStatsSerializer(serializers.Serializer):
    """Plain (non-ModelSerializer) shape matching
    getReferralStatsForUser()'s return value in referralService.js —
    the response body for GET /api/licensing/referral/me/."""

    code = serializers.CharField()
    redemption_count = serializers.IntegerField()
    max_redemptions = serializers.IntegerField(default=MAX_REDEMPTIONS_PER_CODE)
    remaining_redemptions = serializers.IntegerField()
    is_referral_license_active = serializers.BooleanField()
    days_remaining = serializers.IntegerField()
    referral_extension_end_date = serializers.DateTimeField(allow_null=True)
    has_redeemed_a_code = serializers.BooleanField()


class ReferralRedeemRequestSerializer(serializers.Serializer):
    """Input serializer for POST /api/licensing/referral/redeem/."""

    code = serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=True)

    # =====================================================
    # DATABASE VALIDATION CHANGE (matches the pattern used in
    # organizations/serializers.py): reject an obviously-empty code before
    # it ever reaches the service layer.
    # =====================================================
    def validate_code(self, value):
        if not value.strip():
            raise serializers.ValidationError("Referral code cannot be blank.")
        return value.strip()


class ReferralUsageSerializer(serializers.ModelSerializer):
    referee_id = serializers.IntegerField(source="referee.id", read_only=True)
    referrer_id = serializers.IntegerField(source="referrer.id", read_only=True)
    code = serializers.CharField(source="code.code", read_only=True)

    class Meta:
        model = ReferralUsage
        fields = [
            "id",
            "referee_id",
            "referrer_id",
            "code",
            "redeemed_at",
            "referee_days_granted",
            "referrer_days_granted",
            "referee_license_start_date",
            "referee_license_end_date",
        ]
        read_only_fields = fields


class ReferralProgramSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralProgramSettings
        fields = ["referrer_bonus_enabled", "updated_at"]
        read_only_fields = ["updated_at"]
