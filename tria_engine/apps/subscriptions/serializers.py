# tria_engine/apps/subscriptions/serializers.py

from typing import ClassVar

from rest_framework import serializers

from .models import Plan, Subscription


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields: ClassVar[list[str]] = [
            "id", "name", "price", "max_studies", "max_users",
            "storage_limit_gb", "is_active", "created_at", "updated_at",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=Plan.objects.all(), source="plan", write_only=True
    )

    class Meta:
        model = Subscription
        # NOTE: tria_engine.apps.billing also defines a
        # `SubscriptionSerializer` (different fields/shape, same class
        # name). Without an explicit ref_name, drf_yasg's default naming
        # collides between the two and swagger schema generation 500s.
        # See the matching note in billing/serializers.py.
        ref_name = "SubscriptionsSubscription"
        fields: ClassVar[list[str]] = [
            "id", "organization", "plan", "plan_id", "status",
            "start_date", "end_date", "auto_renewal", "notes", "updated_at",
        ]
        read_only_fields: ClassVar[list[str]] = ["organization"]

    # Duplicates the model-level clean() check at the API boundary so a
    # bad date range is rejected with a field-scoped DRF validation error
    # instead of surfacing only when full_clean() happens to run — same
    # pattern as MonitoringAccessRequestSerializer in monitoring/.
    def validate(self, attrs):
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError(
                {"end_date": "endDate cannot be before startDate."}
            )
        return attrs


class SubscriptionUsageSerializer(serializers.Serializer):
    """Not a ModelSerializer — shapes the dict returned by
    services.get_usage() for the three KPICards, matching
    getSubscriptionUsage()'s return shape on the frontend."""
    studies_used = serializers.IntegerField()
    studies_limit = serializers.IntegerField()
    users_used = serializers.IntegerField()
    users_limit = serializers.IntegerField()
    storage_used_gb = serializers.IntegerField()
    storage_limit_gb = serializers.IntegerField()