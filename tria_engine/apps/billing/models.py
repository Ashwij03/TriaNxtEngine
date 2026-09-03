# tria_engine/apps/billing/models.py
#
# Real, payable subscription system — the org-level billing model, kept in
# its own `billing` app deliberately SEPARATE from `licensing` (which is
# scoped to referrals + user-level free-trial days) and from the
# display-oriented `subscriptions` catalog task.
#
# Field names/shapes mirror the already-live frontend localStorage schema
# (planCatalogService.js -> `planCatalog`, subscriptionService.js ->
# `trianxtSubscription`) so the eventual frontend swap-over is a drop-in
# replacement with no shape translation required — the same principle
# licensing/models.py states for referrals. Where the frontend uses
# camelCase keys (maxStudies, maxUsers, storageLimitGb, planId, ...), the
# JSON *wire* contract is camelCase (see serializers.py) while the DB
# columns below stay snake_case.
#
# Money is never a float anywhere: `price`/`amount` are DecimalFields.
# Status is always RECOMPUTED on read against end_date (never trusted as
# a stale stored value) — the same "computed on read" stance as
# LicenseEntitlement.is_referral_license_active() in licensing/.

from decimal import Decimal

from django.db import models
from django.utils import timezone

from tria_engine.apps.organizations.models import Organization

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Mirrors planCatalogService.js's UNLIMITED_LIMIT sentinel: the wire format
# for a numeric limit with no cap. The DB stores NULL for "unlimited" (a
# clean relational encoding); the serializers translate NULL <-> this
# sentinel so API payloads are byte-identical to the localStorage plan rows
# the frontend already understands. Keep this value equal to the frontend
# constant — it is the one number to reconcile during the swap-over if the
# frontend ever changes it.
UNLIMITED_LIMIT = -1

# Billing period used to compute end_date when a payment clears. The plan
# tier has no per-tier period field (frontend schema has none either), so
# the product defaults to a monthly cycle; settable via the
# BILLING_DEFAULT_PERIOD_DAYS Django setting.
DEFAULT_PERIOD_DAYS = 30


class PlanTier(models.Model):
    """One row per catalog tier (Free / Professional / Enterprise, or any
    Admin-defined tier). Mirrors the field set SubscriptionManagement.js's
    PlanFormModal already validates on the frontend.

    `features` is a JSONField list of strings rather than a related
    PlanFeature model: the only thing features need is per-tier display
    order, and a JSON array preserves insertion order natively with zero
    extra joins; a child table would buy nothing but referential ceremony
    for an attribute that has no cross-tier relationships.

    Deletion is always SOFT (`is_active=False`), never a hard row delete,
    because historical Subscription rows FK to a tier (on_delete=PROTECT on
    Subscription.plan is the DB backstop that makes a hard delete of an
    in-use tier impossible even if someone bypasses the service layer).
    """

    name = models.CharField(max_length=100, unique=True)
    # Money is a Decimal, never a float.
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    # Numeric limits: NULL means "unlimited" (the frontend encodes the same
    # idea as its UNLIMITED_LIMIT sentinel). PositiveIntegerField + null=True
    # gives us a DB-level guard that no garbage negative value sneaks in.
    max_studies = models.PositiveIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    storage_limit_gb = models.PositiveIntegerField(null=True, blank=True)
    features = models.JSONField(default=list)
    # Exactly one tier is the default at all times — enforced three ways:
    #   1. the partial unique constraint below caps the count at one,
    #   2. save() transfers the flag off any previous default, and
    #   3. the service layer refuses to deactivate the default (the "can't
    #      delete the last plan" business rule from SubscriptionManagement.js).
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="single_default_plan_tier",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Transfer the default flag atomically: making this tier the default
        # silently un-defaults whatever tier previously held the flag, so the
        # exactly-one-default invariant holds without callers orchestrating a
        # two-row update themselves.
        if self.is_default:
            PlanTier.objects.filter(is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )
        super().save(*args, **kwargs)


class Subscription(models.Model):
    """One row per Organization. Tenant-scoped billing data belongs on the
    tenant, so this is a OneToOneField to Organization rather than a
    get_solo()-style singleton (the same reasoning subscriptions/models.py
    documents for its own org-scoped Subscription)."""

    # Organization link — mirrors the FK pattern accounts/organizations use
    # everywhere (a Subscription belongs to an organization, not a user).
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="billing_subscription",
    )

    # PROTECT: a tier that any subscription references can never be hard-
    # deleted, only soft-deactivated (and the service layer blocks even that
    # while the tier is in active use or is the default).
    plan = models.ForeignKey(
        PlanTier,
        on_delete=models.PROTECT,
        related_name="billing_subscriptions",
    )

    # DB values are UPPER slugs for clean code-level identity; the API layer
    # translates them to the frontend's display values ("Active",
    # "Suspended", "Expired", ...) — see STATUS_DISPLAY below. The frontend
    # statuses this product ships today are Active/Suspended/Expired; the
    # two extra machine states exist for the payment lifecycle.
    STATUS_ACTIVE = "ACTIVE"
    STATUS_SUSPENDED = "SUSPENDED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_PENDING_PAYMENT = "PENDING_PAYMENT"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_PENDING_PAYMENT, "Pending Payment"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    # Display strings the frontend subscriptionService.js compares against —
    # these must stay byte-identical to the localStorage `status` values.
    STATUS_DISPLAY = {
        STATUS_ACTIVE: "Active",
        STATUS_SUSPENDED: "Suspended",
        STATUS_EXPIRED: "Expired",
        STATUS_PENDING_PAYMENT: "Pending Payment",
        STATUS_CANCELLED: "Cancelled",
    }

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING_PAYMENT
    )
    # Nullable until first activation: a lazy-created row waiting on payment
    # (or the seeded free default, which has no expiry) has no window yet.
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    auto_renewal = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    # Per-org override fields — NULL means "inherit from the plan tier",
    # which is exactly the frontend's `undefined`-means-inherit convention in
    # subscriptionService.js (same fallback rule, see effective_limits()).
    max_studies_override = models.PositiveIntegerField(null=True, blank=True)
    max_users_override = models.PositiveIntegerField(null=True, blank=True)
    storage_limit_gb_override = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organization.name} — {self.plan.name} ({self.status})"

    def status_display_value(self):
        """Stored status translated to the frontend display string. Callers
        that need the *effective* status (recomputed against end_date) must
        use recompute_status() instead — a stored "Active" with a lapsed
        end_date is not really Active."""
        return self.STATUS_DISPLAY.get(self.status, self.status)

    def recompute_status(self):
        """Effective status, computed on read: a subscription stored as
        ACTIVE whose end_date has passed is EXPIRED, period. Mirrors the
        auto-expiry stance of licensing's
        LicenseEntitlement.is_referral_license_active() — the stored status
        column is a snapshot written by the last state transition, never a
        thing to trust in a guard without this check."""
        if (
            self.status == self.STATUS_ACTIVE
            and self.end_date is not None
            and self.end_date < timezone.localdate()
        ):
            return self.STATUS_EXPIRED
        return self.status

    def is_usable(self):
        """True when the org may actually use the product today. Reproduces
        subscriptionGuard.js's usable-status rule (only "Active" unlocks
        study creation / user approval) on top of the recomputed status, so
        an org whose window lapsed is locked out even before the settle-on-
        login job persists the EXPIRED snapshot."""
        return self.recompute_status() == self.STATUS_ACTIVE

    def effective_limits(self):
        """Resolve the limits that actually apply, reproducing
        getEffectiveLimits() in subscriptionService.js: the subscription's
        own override wins when set, otherwise fall back to the referenced
        plan tier's number. Values are ints or None — None means unlimited,
        which the serializer layer renders as the frontend's UNLIMITED_LIMIT
        sentinel so payloads stay byte-identical to localStorage.

        Note the resolution nuance: an override of "unlimited" is expressed
        as the sentinel by the API and stored as a NULL DB value, but NULL on
        an *override* column means "inherit" — so an org cannot override a
        capped plan tier to unlimited through the current admin forms (which
        require limits > 0 client-side, per PlanFormModal.validate()). That
        matches the client; unlimited remains a plan-tier-level concept.
        """
        return {
            "maxStudies": (
                self.max_studies_override
                if self.max_studies_override is not None
                else self.plan.max_studies
            ),
            "maxUsers": (
                self.max_users_override
                if self.max_users_override is not None
                else self.plan.max_users
            ),
            "storageLimitGb": (
                self.storage_limit_gb_override
                if self.storage_limit_gb_override is not None
                else self.plan.storage_limit_gb
            ),
        }


class SubscriptionEvent(models.Model):
    """Append-only audit log — one row per state change, never updated,
    never deleted. This is what Admin's "Active Subscription" history and
    support's "why did my org get suspended" questions read."""

    # Event slugs are lowercase DB identifiers (not wire/display strings).
    EVENT_CREATED = "created"
    EVENT_PLAN_CHANGED = "plan_changed"
    EVENT_PAYMENT_SUCCEEDED = "payment_succeeded"
    EVENT_PAYMENT_FAILED = "payment_failed"
    EVENT_RENEWED = "renewed"
    EVENT_CANCELLED = "cancelled"
    EVENT_SUSPENDED = "suspended"
    EVENT_EXPIRED = "expired"

    EVENT_CHOICES = [
        (EVENT_CREATED, "Created"),
        (EVENT_PLAN_CHANGED, "Plan changed"),
        (EVENT_PAYMENT_SUCCEEDED, "Payment succeeded"),
        (EVENT_PAYMENT_FAILED, "Payment failed"),
        (EVENT_RENEWED, "Renewed"),
        (EVENT_CANCELLED, "Cancelled"),
        (EVENT_SUSPENDED, "Suspended"),
        (EVENT_EXPIRED, "Expired"),
    ]

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES, db_index=True)
    # Free-form context for support/audit: actor ids, amounts, prior/next
    # plan names, gateway ids, reasons. Never store secrets here.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.event_type} for subscription {self.subscription_id}"


class PaymentTransaction(models.Model):
    """One row per payment attempt against a gateway — the ledger behind
    the audit trail. `amount`/`currency`/`status` are ONLY ever set from a
    server-side verified gateway response or webhook; the client's confirm
    call is verified end-to-end (order signature HMAC) before anything is
    trusted, and the raw verified webhook body is kept for audits/disputes."""

    STATUS_CREATED = "CREATED"
    STATUS_AUTHORIZED = "AUTHORIZED"
    STATUS_CAPTURED = "CAPTURED"
    STATUS_FAILED = "FAILED"
    STATUS_REFUNDED = "REFUNDED"

    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_AUTHORIZED, "Authorized"),
        (STATUS_CAPTURED, "Captured"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    GATEWAY_RAZORPAY = "razorpay"
    GATEWAY_CHOICES = [
        (GATEWAY_RAZORPAY, "Razorpay"),
    ]

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="payment_transactions",
    )
    # What the money bought: activation/upgrade targets this tier. Recording
    # it on the transaction (not just reading subscription.plan at webhook
    # time) is what lets a webhook activate the *upgrade* the user paid for
    # even if they closed the tab before the client confirm call fired.
    plan = models.ForeignKey(
        PlanTier,
        on_delete=models.PROTECT,
        related_name="payment_transactions",
    )
    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, default=GATEWAY_RAZORPAY)
    gateway_order_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    gateway_payment_id = models.CharField(max_length=64, db_index=True, null=True, blank=True)
    gateway_signature = models.CharField(max_length=512, null=True, blank=True)
    # Amount in major currency units (INR), Decimal not float. The razorpay
    # adapter converts to/from paise at the boundary.
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_CREATED)
    raw_webhook_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return (
            f"PaymentTransaction({self.gateway}/{self.gateway_order_id or '-'}, "
            f"{self.amount} {self.currency}, {self.status})"
        )
