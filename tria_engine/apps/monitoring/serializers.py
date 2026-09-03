# tria_engine/apps/monitoring/serializers.py

from rest_framework import serializers

from tria_engine.apps.organizations.models import Organization

from .models import MonitoringAccessRequest


class MonitoringAccessRequestSerializer(serializers.ModelSerializer):
    requested_by_name = serializers.SerializerMethodField()
    requested_by_email = serializers.SerializerMethodField()
    site_name = serializers.CharField(source="site.name", read_only=True)
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringAccessRequest
        fields = [
            "id",
            "requested_by",
            "requested_by_name",
            "requested_by_email",
            "requester_role_label",
            "site",
            "site_name",
            "start_date",
            "end_date",
            "reason",
            "status",
            "approved_by",
            "approved_by_name",
            "decision_note",
            "decided_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "requested_by",
            "requester_role_label",
            "status",
            "approved_by",
            "decision_note",
            "decided_at",
            "created_at",
            "updated_at",
        ]

    def get_requested_by_name(self, obj):
        user = obj.requested_by
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.username

    def get_requested_by_email(self, obj):
        return obj.requested_by.email

    def get_approved_by_name(self, obj):
        if not obj.approved_by:
            return None
        full_name = f"{obj.approved_by.first_name} {obj.approved_by.last_name}".strip()
        return full_name or obj.approved_by.username

    def validate_site(self, value):
        if not Organization.objects.filter(id=value.id).exists():
            raise serializers.ValidationError("Referenced site does not exist.")
        return value

    def validate(self, attrs):
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError(
                {"end_date": "End date cannot be before start date."}
            )
        return attrs


class MonitoringAccessDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)
