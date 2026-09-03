from django.contrib import admin

from .models import MonitoringAccessRequest


@admin.register(MonitoringAccessRequest)
class MonitoringAccessRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "requested_by",
        "requester_role_label",
        "site",
        "start_date",
        "end_date",
        "status",
        "approved_by",
        "created_at",
    )
    list_filter = ("status", "site")
    search_fields = ("requested_by__email", "site__name")
