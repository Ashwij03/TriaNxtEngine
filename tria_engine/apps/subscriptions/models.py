# tria_engine/apps/subscriptions/models.py
#
# Task 6b (backend) — Subscription & Plan Catalog.
# Backend counterpart to the frontend's localStorage-based
# subscriptionService.js / planCatalogService.js. Field names mirror the
# frontend schema exactly (see Task6b_Subscription_Management_
# Development_Prompt.md §0.1 and §4.2) so a future frontend swap-over is a
# drop-in replacement, the same relationship licensing/ has to
# referralService.js.
#
# Scope note: licensing.LicenseEntitlement.subscription_end_date is a
# SEPARATE, already-shipped concept (referral-day-stacking math in
# licensing/services.py) and is deliberately NOT merged with the
# Subscription model below. Flagged for potential future integration;
# not built here.

from django.core.exceptions import ValidationError
from django.db import models

from tria_engine.apps.organizations.models import Organization

# Matches the frontend's subscription status values exactly
# (subscriptionService.js) — kept as capitalized display strings, not
# lowercase slugs, so the API payload is byte-identical to the
# localStorage object a future frontend swap-over will replace.
STATUS_CHOICES = [
    ("Active", "Active"),
    ("Expired", "Expired"),
    ("Suspended", "Suspended"),
]


class Plan(models.Model):
    """One row per catalog tier (Basic / Professional / Enterprise, or any
    Admin-defined tier). Mirrors the field set SubscriptionEditModal.js
    already validates on the frontend, adapted from subscription-instance
    fields to tier-definition fields.

    `price` is a display-only Decimal (a catalog price tag), NOT a live
    billing concept — payments/invoicing are explicitly out of scope for
    this task (frontend spec §0.1: Plan rows are a catalog of tiers with
    limits and a display price)."""

    name = models.CharField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_studies = models.PositiveIntegerField()
    max_users = models.PositiveIntegerField()
    storage_limit_gb = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return self.name


class Subscription(models.Model):
    """One row per Organization (see Ground Truth #4 of the task spec —
    NOT a global singleton, despite the frontend currently treating it as
    one; today's single-Organization deployment makes the two behave
    identically). Tenant-scoped data belongs on the tenant, so this is a
    OneToOneField to Organization rather than a get_solo()-style
    singleton — that pattern is only correct for genuinely
    non-tenant-scoped data like an admin feature toggle."""

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,  # mirrors the frontend's "block delete if assigned" rule
        related_name="subscriptions",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Active")
    start_date = models.DateField()
    end_date = models.DateField()
    auto_renewal = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organization.name} — {self.plan.name} ({self.status})"

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError("endDate cannot be before startDate.")

    # NOTE: no stored "is currently active" boolean here — the derived
    # "is this subscription currently active?" answer is always recomputed
    # against end_date / timezone.now() by the caller (see
    # services.assert_can_create_study / assert_can_approve_user), never
    # trusted from a stale stored flag. This mirrors
    # LicenseEntitlement.is_referral_license_active() in licensing/, which
    # recomputes against timezone.now() on every call.