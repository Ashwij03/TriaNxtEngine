# tria_engine/apps/billing/serializers.py
#
# Wire contract = camelCase, matching the frontend localStorage schemas the
# API is a drop-in replacement for (planCatalogService.js / 
# subscriptionService.js). This codebase's DRF config has NO global
# camelCase renderer (djangorestframework-camel-case is not installed), and
# adding one would silently re-shape every other app's responses, so the
# aliasing is done per-field with explicit `source=` declarations instead —
# the DB columns stay snake_case, the JSON payload is camelCase.

from typing import ClassVar

from rest_framework import serializers

from .models import (
    UNLIMITED_LIMIT,
    PlanTier,
    Subscription,
)

# ---------------------------------------------------------------------------
# Limit field — DB NULL (unlimited) <-> UNLIMITED_LIMIT sentinel on the wire
# ---------------------------------------------------------------------------


class LimitField(serializers.IntegerField):
    """Serializes a nullable positive DB column as the frontend's
    UNLIMITED_LIMIT sentinel when the column is NULL (unlimited), so API
    payloads are byte-identical to localStorage plan rows. On write it maps
    the sentinel back to NULL and otherwise enforces a positive integer.

    NOTE: PlanFormModal.validate() on the frontend requires limits > 0 (no
    unlimited tiers through the form). This field additionally accepts the
    sentinel so an Admin-edited unlimited tier round-trips through the API
    without exploding — the agreement with client validation is preserved
    for every real form submission, and server-side seeds stay expressible.
    """

    def get_attribute(self, instance):
        # DRF's Serializer.to_representation deliberately SKIPS a field's
        # to_representation() when the attribute is None (it emits None
        # directly), so the NULL -> UNLIMITED_LIMIT substitution must happen
        # here, before the None check, or unlimited tiers would leak as None
        # instead of the sentinel.
        value = super().get_attribute(instance)
        return UNLIMITED_LIMIT if value is None else value

    def to_representation(self, value):
        # Never receives None (see get_attribute) — kept for safety.
        return UNLIMITED_LIMIT if value is None else value

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value == UNLIMITED_LIMIT:
            return None
        if value <= 0:
            raise serializers.ValidationError("Limit must be greater than 0.")
        return value


# ---------------------------------------------------------------------------
# Plan tiers
# ---------------------------------------------------------------------------

class PlanTierSerializer(serializers.ModelSerializer):
    """Read serializer for the catalog (GET /api/billing/plans/) and for the
    nested planDetails block of SubscriptionSerializer. Field names are the
    planCatalogService.js plan-tier fields: id, name, price, maxStudies,
    maxUsers, storageLimitGb, features, isDefault."""

    price = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=False, read_only=True
    )
    maxStudies = LimitField(source="max_studies", read_only=True)
    maxUsers = LimitField(source="max_users", read_only=True)
    storageLimitGb = LimitField(source="storage_limit_gb", read_only=True)
    isDefault = serializers.BooleanField(source="is_default", read_only=True)
    isActive = serializers.BooleanField(source="is_active", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = PlanTier
        fields: ClassVar[list[str]] = [
            "id",
            "name",
            "price",
            "maxStudies",
            "maxUsers",
            "storageLimitGb",
            "features",
            "isDefault",
            "isActive",
            "createdAt",
            "updatedAt",
        ]


class PlanFormSerializer(serializers.ModelSerializer):
    """Admin create/edit serializer for plan tiers (POST/PUT/PATCH
    /api/billing/plans/). Validation mirrors PlanFormModal.validate() in
    SubscriptionManagement.js exactly: name required (duplicates rejected
    case-insensitively, like the org/role serializers do), price >= 0, and
    maxStudies/maxUsers/storageLimitGb > 0 — so client and server validation
    agree and a client bug can't mint a negative-limit tier."""

    price = serializers.DecimalField(max_digits=10, decimal_places=2, coerce_to_string=False)
    maxStudies = LimitField(source="max_studies")
    maxUsers = LimitField(source="max_users")
    storageLimitGb = LimitField(source="storage_limit_gb")
    features = serializers.JSONField(default=list)
    isDefault = serializers.BooleanField(source="is_default", required=False, default=False)
    # Name uniqueness is case-insensitive here — mirroring the org/role
    # serializers' validate_name / validate patterns in this codebase.
    name = serializers.CharField(max_length=100, allow_blank=False)

    class Meta:
        model = PlanTier
        fields: ClassVar[list[str]] = [
            "name",
            "price",
            "maxStudies",
            "maxUsers",
            "storageLimitGb",
            "features",
            "isDefault",
        ]

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("Plan name is required.")
        if PlanTier.objects.filter(name__iexact=value.strip()).exclude(
            pk=self.instance.pk if self.instance else None
        ).exists():
            raise serializers.ValidationError(
                "A plan with this name already exists."
            )
        return value.strip()

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value

    def validate_features(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Features must be a list of strings.")
        for feature in value:
            if not isinstance(feature, str) or not feature.strip():
                raise serializers.ValidationError(
                    "Each feature must be a non-empty string."
                )
        return value


# ---------------------------------------------------------------------------
# Subscription (read) + the org's live usage
# ---------------------------------------------------------------------------

class SubscriptionSerializer(serializers.ModelSerializer):
    """Read serializer for /subscription/me — the drop-in replacement for
    subscriptionService.js's `trianxtSubscription` localStorage object:
    status/planId/plan/startDate/endDate/autoRenewal/notes plus the
    per-org overrides, and three read-only extras the UI needs:
      * planDetails — the full tier object (features etc.),
      * effectiveLimits — resolved limits (override wins, else plan tier),
        with UNLIMITED_LIMIT for uncapped values,
      * usage — live studiesUsed/usersUsed/storageUsedGb.

    `status` is the RECOMPUTED status (end_date re-checked on read), so an
    expired window shows "Expired" even before settle-on-login persists it —
    the frontend guard never sees a stale "Active".
    """

    planId = serializers.IntegerField(source="plan_id", read_only=True)
    plan = serializers.CharField(source="plan.name", read_only=True)
    planDetails = PlanTierSerializer(source="plan", read_only=True)
    status = serializers.SerializerMethodField()
    startDate = serializers.DateField(source="start_date", read_only=True)
    endDate = serializers.DateField(source="end_date", read_only=True)
    autoRenewal = serializers.BooleanField(source="auto_renewal", read_only=True)
    maxStudies = serializers.IntegerField(
        source="max_studies_override", read_only=True, allow_null=True
    )
    maxUsers = serializers.IntegerField(
        source="max_users_override", read_only=True, allow_null=True
    )
    storageLimitGb = serializers.IntegerField(
        source="storage_limit_gb_override", read_only=True, allow_null=True
    )
    effectiveLimits = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = Subscription
        # NOTE: tria_engine.apps.subscriptions also defines a
        # `SubscriptionSerializer`. drf_yasg derives a schema component
        # name ("ref_name") from the class name by default, so with both
        # apps installed the two serializers collide on the same
        # "Subscription" ref_name and swagger generation fails with
        # "... they implicitly share the same ref_name; explicitly set
        # the ref_name attribute on both serializers' Meta classes".
        # Give each an explicit, distinct ref_name to fix that.
        ref_name = "BillingSubscription"
        fields: ClassVar[list[str]] = [
            "id",
            "planId",
            "plan",
            "planDetails",
            "status",
            "startDate",
            "endDate",
            "autoRenewal",
            "notes",
            "maxStudies",
            "maxUsers",
            "storageLimitGb",
            "effectiveLimits",
            "usage",
            "createdAt",
            "updatedAt",
        ]

    def get_status(self, obj):
        effective = obj.recompute_status()
        return Subscription.STATUS_DISPLAY.get(effective, effective)

    def get_effectiveLimits(self, obj):
        limits = obj.effective_limits()
        return {
            "maxStudies": (
                UNLIMITED_LIMIT if limits["maxStudies"] is None else limits["maxStudies"]
            ),
            "maxUsers": (
                UNLIMITED_LIMIT if limits["maxUsers"] is None else limits["maxUsers"]
            ),
            "storageLimitGb": (
                UNLIMITED_LIMIT
                if limits["storageLimitGb"] is None
                else limits["storageLimitGb"]
            ),
        }

    def get_usage(self, obj):
        from .services import get_usage

        usage = get_usage(obj.organization)
        return {
            "studiesUsed": usage["studies_used"],
            "usersUsed": usage["users_used"],
            "storageUsedGb": usage["storage_used_gb"],
        }


# ---------------------------------------------------------------------------
# Checkout / payment-confirm request+response shapes
# ---------------------------------------------------------------------------

class CheckoutRequestSerializer(serializers.Serializer):
    """POST /api/billing/subscription/checkout/ body: {planId}."""

    planId = serializers.IntegerField(min_value=1)


class CheckoutResponseSerializer(serializers.Serializer):
    """The only gateway data the frontend checkout widget may see: the order
    id, amount, currency, the PUBLISHABLE key (never the secret) and the
    local transaction id."""

    gatewayOrderId = serializers.CharField(allow_null=True)
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, coerce_to_string=False
    )
    currency = serializers.CharField()
    gatewayKey = serializers.CharField()
    paymentTransactionId = serializers.IntegerField()


class PaymentConfirmSerializer(serializers.Serializer):
    """POST /api/billing/subscription/confirm/ body — the values Razorpay's
    checkout callback hands the frontend. amount/status are deliberately NOT
    accepted here: they are only ever set from the server-side verified
    gateway response."""

    paymentTransactionId = serializers.IntegerField(min_value=1)
    gatewayPaymentId = serializers.CharField(max_length=64, allow_blank=False)
    gatewaySignature = serializers.CharField(max_length=512, allow_blank=False)


class PlanIdBodySerializer(serializers.Serializer):
    """Shared body for plan-referencing admin actions: {planId}."""

    planId = serializers.IntegerField(min_value=1)


class AutoRenewalSerializer(serializers.Serializer):
    """PATCH /api/billing/subscription/auto-renewal/ body: {enabled}."""

    enabled = serializers.BooleanField()