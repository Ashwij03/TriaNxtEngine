# tria_engine/apps/billing/admin.py

from django.contrib import admin

from .models import (
    PaymentTransaction,
    PlanTier,
    Subscription,
    SubscriptionEvent,
)


@admin.register(PlanTier)
class PlanTierAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "max_studies", "max_users",
                    "storage_limit_gb", "is_default", "is_active")
    list_filter = ("is_default", "is_active")
    search_fields = ("name",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "start_date",
                    "end_date", "auto_renewal")
    list_filter = ("status", "auto_renewal", "plan")
    search_fields = ("organization__name", "notes")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    """Append-only audit trail — intentionally has no add/delete shortcuts
    exposed through Django admin defaults beyond the model-level behavior
    (rows are only ever created by the service layer)."""

    list_display = ("subscription", "event_type", "created_at")
    list_filter = ("event_type",)
    search_fields = ("subscription__organization__name", "metadata")
    readonly_fields = ("subscription", "event_type", "metadata", "created_at")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "subscription", "plan", "gateway", "amount",
                    "currency", "status", "gateway_order_id")
    list_filter = ("status", "gateway", "currency")
    search_fields = ("gateway_order_id", "gateway_payment_id")
    readonly_fields = (
        "subscription", "plan", "gateway", "amount", "currency", "status",
        "raw_webhook_payload", "created_at", "updated_at",
    )
