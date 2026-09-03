# tria_engine/apps/billing/services.py
#
# Real, payable subscription flow — business rules only, no view logic, no
# DB writes anywhere in views.py (the licensing/services.py contract). Every
# function with a race condition runs inside transaction.atomic() with
# select_for_update() on the rows it mutates — the exact locking stance
# redeem_referral_code() established for referral redemptions — because the
# races here are the same class:
#
#   * two admins double-click "Apply" on the same subscription,
#   * the gateway retries a webhook while the client's confirm call lands,
#   * two webhook retries land concurrently.
#
# The one thing the frontend's localStorage-only implementation cannot do
# (and this layer can) is make the seat/status guards non-bypassable and the
# money transitions exactly-once.
#
# Free-tier decision (open product question, resolved here — see README.md):
# the DEFAULT plan tier is a price-0 "Free" tier that get_or_create_subscription()
# activates with status ACTIVE and no end date, so a brand-new organization is
# usable immediately with no payment. If product later wants a paid-only
# funnel, set the default tier's price > 0 and this same function will leave
# new organizations in PENDING_PAYMENT instead. The logic is data-driven off
# the default tier's price — there is no separate code branch to maintain.

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from tria_engine.apps.organizations.models import Organization

from . import gateway
from .models import (
    DEFAULT_PERIOD_DAYS,
    PaymentTransaction,
    PlanTier,
    Subscription,
    SubscriptionEvent,
)

# ===========================================================================
# Errors — the single vocabulary views (and future call sites like a studies
# service) map to HTTP responses.
# ===========================================================================


class BillingError(Exception):
    """Base class for every business-rule error raised by this module."""


class SubscriptionLimitError(BillingError):
    """Seat/status block — mirrors the plain `throw new Error(...)` guards in
    subscriptionGuard.js. Maps to a 4xx (403 for approval/creation blocks)."""


class PaymentSignatureError(BillingError):
    """A payment/webhook signature failed server-side verification. Nothing
    may be activated when this is raised."""


class ServiceConflictError(BillingError):
    """A state conflict the caller must surface as HTTP 409 (plan in use,
    default plan, already-cancelled, etc.)."""


class PermissionDeniedError(BillingError):
    """Actor is not an admin of the subscription's organization."""


# ===========================================================================
# Read helpers
# ===========================================================================

def _today():
    return timezone.localdate()


def _period_days():
    return getattr(settings, "BILLING_DEFAULT_PERIOD_DAYS", DEFAULT_PERIOD_DAYS)


def _default_plan_tier():
    """The catalog's single default tier. Raise only if the deployment is
    misconfigured (no active default tier) — the 0002 data migration seeds
    one, and deactivate_plan_tier() refuses to remove the last one."""
    tier = PlanTier.objects.filter(is_default=True, is_active=True).first()
    if tier is None:
        raise BillingError(
            "No active default plan tier is configured. Contact your administrator."
        )
    return tier


def compute_stacked_end_date(subscription, days_to_add, now=None):
    """Port of licensing/services.py::compute_stacked_end_date: the new end
    date stacks from the LATER of today and the current end date — but only a
    FUTURE end date counts (a lapsed one is treated as having none, so an
    org that let its window lapse and then pays again starts fresh rather
    than stacking onto an already-expired period)."""
    now = now or _today()
    candidates = [d for d in [subscription.end_date] if d and d > now]
    base = max(candidates) if candidates else now
    return base + timezone.timedelta(days=days_to_add)


# ---------------------------------------------------------------------------
# get_or_create_subscription — idempotent lazy activation
# ---------------------------------------------------------------------------

def get_or_create_subscription(organization):
    """Idempotent — safe to call on every /subscription/me/ read and every
    login settle, mirroring getOrCreateSubscription() semantics on the
    frontend. Lazy-creates the org's Subscription pointing at the default
    tier:

      * default tier price is 0  -> ACTIVE free tier, no end date, no
        auto-renewal (nothing to renew). This is the resolved free-tier-on-
        signup decision (see module docstring / README.md).
      * default tier price is > 0 -> PENDING_PAYMENT until the first payment
        clears.

    A SubscriptionEvent(created) is written exactly once — only when the row
    is actually created, so repeated calls never spam the audit log.

    Implementation note: the org's reverse OneToOne cache is deliberately NOT
    consulted — Django caches a failed reverse lookup on the instance, so a
    stale negative cache on a caller's org object would make this function
    see a "missing" subscription even after it exists. Everything goes
    through DB queries instead."""
    with transaction.atomic():
        # Serialize on the org row so two concurrent first-access calls
        # (e.g. two users logging in at signup) can't both attempt creation;
        # the OneToOne unique constraint backstops the loser regardless.
        try:
            organization = Organization.objects.select_for_update().get(
                pk=organization.pk
            )
        except Organization.DoesNotExist:
            raise BillingError("Organization not found.") from None

        existing = (
            Subscription.objects.select_related("plan")
            .filter(organization=organization)
            .first()
        )
        if existing is not None:
            return existing, False

        tier = _default_plan_tier()
        is_free = Decimal(tier.price) == 0
        subscription = Subscription.objects.create(
            organization=organization,
            plan=tier,
            status=(
                Subscription.STATUS_ACTIVE if is_free
                else Subscription.STATUS_PENDING_PAYMENT
            ),
            start_date=_today() if is_free else None,
            end_date=None if is_free else None,
            auto_renewal=False if is_free else True,
        )
        SubscriptionEvent.objects.create(
            subscription=subscription,
            event_type=SubscriptionEvent.EVENT_CREATED,
            metadata={
                "status": subscription.status,
                "plan": tier.name,
                "reason": (
                    "Free default tier activated on signup" if is_free
                    else "Awaiting first payment"
                ),
            },
        )
        return subscription, True


def get_subscription_status(subscription):
    """Server-side port of getSubscriptionStatus() — the effective status,
    recomputed on read: an ACTIVE subscription whose end_date has passed IS
    Expired even if the column still says ACTIVE (the settle-on-login job
    persists the snapshot later; guards never wait for it). Returns the
    stored DB constant, not the display string — serializers translate."""
    return subscription.recompute_status()


def get_effective_limits(subscription):
    """Server-side port of getEffectiveLimits(). Values are ints or None;
    None means unlimited (the serializer renders it as the frontend's
    UNLIMITED_LIMIT sentinel)."""
    return subscription.effective_limits()


def get_usage(organization):
    """Studies/users/storage used for the KPICards and for enforcement.

    NOTE on studies: this backend has no Study model yet (audited at
    implementation time), so `organization.studies.count()` would raise
    AttributeError. It fails soft to 0 — consistent with what an
    Organization-scoped study queryset actually contains (nothing) — and the
    real one-liner slots in the moment a Study model with an `organization`
    FK lands (same caveat subscriptions/services.py::get_usage documents).

    Storage usage has no real metric in this backend either (the same
    caveat subscriptionService.js notes about storageLimitGb), so
    storage_used_gb stays a fixed 0 placeholder until a real metric exists.

    User count is REAL: active users in the org, counted live on every read
    so seat enforcement can never be bypassed by stale state."""
    studies_manager = getattr(organization, "studies", None)
    studies_used = studies_manager.count() if studies_manager is not None else 0

    User = get_user_model()
    users_used = User.objects.filter(
        organization=organization, is_active=True
    ).count()

    return {
        "studies_used": studies_used,
        "users_used": users_used,
        "storage_used_gb": 0,  # placeholder — no real storage metric exists yet
    }


# ===========================================================================
# Enforcement guards — server-side ports of subscriptionGuard.js
# ===========================================================================
#
# These are what make the client-only frontend rules non-bypassable. Today's
# backend has no Study model and no admin "approve user" endpoint yet, so
# can_approve_user() is already wired into the one real capacity-changing
# path that exists (accounts registration — see accounts/views.py
# RegisterAPI), and can_create_study() is import-ready for the future
# studies service (studies/services.py::create_study should call
# assert_can_create_study() as its first line, exactly like the frontend
# calls assertCanCreateStudy() first). See billing/README.md.

def _subscription_for_guards(organization):
    """Subscription backing enforcement for an org. Lazy-creates (free
    default -> ACTIVE) so pre-billing legacy orgs and brand-new orgs are
    treated identically to orgs that already have a row. Returns None only
    when the deployment is misconfigured (no default tier at all), in which
    case guards fail CLOSED — an unconfigured catalog should not silently
    grant unlimited capacity."""
    try:
        return get_or_create_subscription(organization)[0]
    except BillingError:
        return None


def can_create_study(organization):
    """Port of canCreateStudy() in subscriptionGuard.js — a study may only
    be created when the subscription is Active (recomputed on read) and
    studiesUsed < maxStudies (an unlimited tier never blocks)."""
    subscription = _subscription_for_guards(organization)
    if subscription is None or not subscription.is_usable():
        return False
    limit = get_effective_limits(subscription)["maxStudies"]
    if limit is None:
        return True
    return get_usage(organization)["studies_used"] < limit


def can_approve_user(organization):
    """Port of canApproveUser() in subscriptionGuard.js — a new user may only
    join/be approved onto an org when the subscription is Active and
    usersUsed < maxUsers."""
    subscription = _subscription_for_guards(organization)
    if subscription is None or not subscription.is_usable():
        return False
    limit = get_effective_limits(subscription)["maxUsers"]
    if limit is None:
        return True
    return get_usage(organization)["users_used"] < limit


def assert_can_create_study(organization):
    """assert_can_create_study() raising variant for the future study-creation
    service — same message wording the frontend guard surfaces."""
    if not can_create_study(organization):
        subscription = _subscription_for_guards(organization)
        if subscription is not None and not subscription.is_usable():
            raise SubscriptionLimitError(
                f"Cannot create study: subscription status is "
                f"{subscription.status_display_value()}."
            )
        raise SubscriptionLimitError(
            "Study limit reached for your current plan. Contact your Admin to upgrade."
        )


def assert_can_approve_user(organization):
    """Raising variant used by the registration/user-approval call sites."""
    if not can_approve_user(organization):
        subscription = _subscription_for_guards(organization)
        if subscription is not None and not subscription.is_usable():
            raise SubscriptionLimitError(
                f"Cannot approve user: subscription status is "
                f"{subscription.status_display_value()}."
            )
        raise SubscriptionLimitError(
            "User limit reached for your current plan. Contact your Admin to upgrade."
        )


# ===========================================================================
# Plan-tier lifecycle (create / update / soft-delete)
# ===========================================================================

def create_plan_tier(validated_data):
    """Admin-only plan tier creation. The serializer enforces the
    PlanFormModal.validate() field rules; here we keep the exactly-one-
    default invariant: a first-ever tier must be the default (so
    get_or_create_subscription() always has something to point at)."""
    if not PlanTier.objects.filter(is_default=True).exists():
        validated_data["is_default"] = True
    try:
        with transaction.atomic():
            return PlanTier.objects.create(**validated_data)
    except IntegrityError:
        raise ServiceConflictError(
            "Another plan was made the default at the same time. "
            "Please refresh and retry."
        ) from None


def update_plan_tier(plan_tier, validated_data):
    """Admin-only tier edit. save() (models.py) transfers the default flag
    off any previous default when this tier is promoted; the partial unique
    index + IntegrityError mapping covers the concurrent-promotion race."""
    try:
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(plan_tier, field, value)
            plan_tier.save()
        return plan_tier
    except IntegrityError:
        raise ServiceConflictError(
            "Another plan was made the default at the same time. "
            "Please refresh and retry."
        ) from None


def deactivate_plan_tier(plan_tier):
    """Soft-delete (is_active=False) — the DELETE endpoint's implementation.
    Blocked (ServiceConflictError -> 409, mirroring SubscriptionManagement.js's
    "deleting the last plan / deleting an in-use plan throws" rules) when the
    tier:
      * is the default (a catalog must always have one), or
      * is referenced by a subscription that is not expired/cancelled
        (historical rows may keep referencing a retired tier — PROTECT
        already forbids hard-deleting those), or
      * is the last active tier on the catalog.
    """
    with transaction.atomic():
        locked = PlanTier.objects.select_for_update().get(pk=plan_tier.pk)
        if locked.is_default:
            raise ServiceConflictError(
                "This plan is the default plan and cannot be deleted. "
                "Make another plan the default first."
            )
        in_use = Subscription.objects.filter(
            plan=locked,
            status__in=[
                Subscription.STATUS_ACTIVE,
                Subscription.STATUS_SUSPENDED,
                Subscription.STATUS_PENDING_PAYMENT,
            ],
        ).exists()
        if in_use:
            raise ServiceConflictError(
                "This plan is currently assigned to an active subscription "
                "and cannot be deleted."
            )
        # The "deleting the last plan" rule: a usable catalog needs at least
        # one active tier.
        if PlanTier.objects.filter(is_active=True).count() <= 1:
            raise ServiceConflictError(
                "Cannot delete the last plan in the catalog."
            )
        locked.is_active = False
        locked.save(update_fields=["is_active", "updated_at"])
        return locked


# ===========================================================================
# Admin plan assignment (no payment) + cancellation + auto-renewal toggle
# ===========================================================================

def _ensure_org_admin(actor, subscription):
    """Actor must be an admin of the subscription's own org (role-based, not
    just Django superuser) — the views gate before calling, this is the
    defense-in-depth re-check inside the locked section."""
    from .permissions import is_org_admin

    if not is_org_admin(actor, subscription.organization):
        raise PermissionDeniedError(
            "Only an Admin of this organization can perform that action."
        )


@transaction.atomic
def assign_plan(subscription, plan_tier, actor):
    """Admin-only plan switch WITHOUT a new payment (downgrades, internally
    comped upgrades). Mirrors handleAssignPlan() on the frontend:
      * clears the per-org override fields back to NULL so effective_limits()
        falls through to the new tier (a stale override for the old tier's
        limits must never survive onto the new plan),
      * activates the subscription (an admin assignment grants access),
      * recomputes end_date: a paid tier stacks from today (or the existing
        future end date, if the org is mid-window); a price-0 tier has no
        expiry.

    select_for_update() on the subscription row serializes two concurrent
    assignments (double-clicked "Apply") so the last-write-wins plan is
    deterministic and exactly one plan_changed event is written."""
    _ensure_org_admin(actor, subscription)

    subscription = Subscription.objects.select_for_update().get(
        pk=subscription.pk
    )
    if not plan_tier.is_active:
        raise ServiceConflictError(
            "That plan is no longer active and cannot be assigned."
        )
    if subscription.plan_id == plan_tier.pk and subscription.status in (
        Subscription.STATUS_ACTIVE,
        Subscription.STATUS_PENDING_PAYMENT,
    ):
        # Same-plan assignment is a no-op except for the override reset — the
        # admin may still have wanted to clear overrides.
        subscription.max_studies_override = None
        subscription.max_users_override = None
        subscription.storage_limit_gb_override = None
        subscription.save(update_fields=[
            "max_studies_override", "max_users_override",
            "storage_limit_gb_override", "updated_at",
        ])
        return subscription

    prior_plan_name = subscription.plan.name
    prior_status = subscription.status

    subscription.plan = plan_tier
    subscription.max_studies_override = None
    subscription.max_users_override = None
    subscription.storage_limit_gb_override = None
    subscription.status = Subscription.STATUS_ACTIVE
    if subscription.start_date is None:
        subscription.start_date = _today()
    if Decimal(plan_tier.price) == 0:
        subscription.end_date = None
    else:
        subscription.end_date = compute_stacked_end_date(
            subscription, _period_days()
        )
    subscription.save()

    SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type=SubscriptionEvent.EVENT_PLAN_CHANGED,
        metadata={
            "from_plan": prior_plan_name,
            "to_plan": plan_tier.name,
            "from_status": prior_status,
            "to_status": subscription.status,
            "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
            "actor_user_id": getattr(actor, "id", None),
            "note": "Plan assigned by admin (no payment)",
        },
    )
    return subscription


@transaction.atomic
def cancel_subscription(subscription, actor):
    """Admin-only cancel. Semantics chosen to avoid locking a paying org out
    of its already-paid window:
      * never-activated rows (PENDING_PAYMENT) are cancelled outright, and
      * an ACTIVE subscription has auto_renewal switched off and keeps
        working until end_date — settle-on-login then flips it to EXPIRED at
        the end of the paid period. This is the standard "cancel at end of
        billing period" behavior.
    """
    _ensure_org_admin(actor, subscription)
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription.pk
    )

    if subscription.status == Subscription.STATUS_CANCELLED:
        return subscription

    prior_status = subscription.status
    if prior_status == Subscription.STATUS_PENDING_PAYMENT:
        subscription.status = Subscription.STATUS_CANCELLED
        subscription.auto_renewal = False
    else:
        subscription.auto_renewal = False

    subscription.save()

    SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type=SubscriptionEvent.EVENT_CANCELLED,
        metadata={
            "from_status": prior_status,
            "to_status": subscription.status,
            "auto_renewal": False,
            "actor_user_id": getattr(actor, "id", None),
            "note": (
                "Cancelled before activation — no payment was made."
                if prior_status == Subscription.STATUS_PENDING_PAYMENT
                else "Cancelled — access continues until the end of the paid period."
            ),
        },
    )
    return subscription


@transaction.atomic
def toggle_auto_renewal(subscription, enabled, actor):
    """Admin-only auto-renewal toggle. Not a state change in the audit
    event vocabulary (cancelled/expired etc.), so no SubscriptionEvent row is
    written — updated_at plus the cancelled event at cancel time tell the
    story."""
    _ensure_org_admin(actor, subscription)
    subscription = Subscription.objects.select_for_update().get(
        pk=subscription.pk
    )
    if subscription.status == Subscription.STATUS_CANCELLED:
        raise ServiceConflictError(
            "This subscription is cancelled and cannot be re-enabled for "
            "auto-renewal."
        )
    subscription.auto_renewal = bool(enabled)
    subscription.save(update_fields=["auto_renewal", "updated_at"])
    return subscription


# ===========================================================================
# Checkout (order creation) — money is only ever touched server-side
# ===========================================================================

def initiate_checkout(subscription, plan_tier):
    """Admin-only checkout start. Creates a PaymentTransaction row
    (status=CREATED), calls the gateway's order-creation API, and returns
    ONLY the public data the frontend checkout widget needs — the gateway
    order id, amount, currency, the publishable key id (RAZORPAY_KEY_ID —
    never the secret) and the local transaction id.

    Re-entrancy: the subscription row is locked for the whole decision, so a
    double-clicked checkout either reuses the still-open transaction (and
    its existing gateway order) or, if none is young enough, creates exactly
    one new one — two live gateway orders the org could accidentally pay
    twice are never spawned.

    Order-creation failure: the FAILED status + payment_failed audit event
    are written INSIDE the locked transaction, and the transaction is then
    allowed to COMMIT (no rollback) before the error is raised to the view —
    the failed attempt must be visible in the ledger, not silently undone."""
    failure_error = None
    checkout_payload = None
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(
            pk=subscription.pk
        )
        if not plan_tier.is_active:
            raise ServiceConflictError(
                "That plan is no longer active and cannot be purchased."
            )
        if Decimal(plan_tier.price) == 0:
            raise ServiceConflictError(
                "This plan is free — no payment is required. "
                "Use plan assignment instead."
            )

        cutoff = timezone.now() - timezone.timedelta(minutes=15)
        open_transaction = (
            PaymentTransaction.objects.filter(
                subscription=subscription,
                status__in=[
                    PaymentTransaction.STATUS_CREATED,
                    PaymentTransaction.STATUS_AUTHORIZED,
                ],
                created_at__gte=cutoff,
            )
            .select_for_update()
            .order_by("-created_at")
            .first()
        )
        if open_transaction is not None and open_transaction.gateway_order_id:
            return _checkout_payload(open_transaction)

        transaction_obj = open_transaction
        if transaction_obj is None:
            transaction_obj = PaymentTransaction.objects.create(
                subscription=subscription,
                plan=plan_tier,
                gateway=PaymentTransaction.GATEWAY_RAZORPAY,
                amount=plan_tier.price,
                currency="INR",
                status=PaymentTransaction.STATUS_CREATED,
            )

        # Gateway I/O stays inside the locked section: the order id is the
        # link that lets a later webhook correlate back to this row, so the
        # row must not be committed without it, and holding the subscription
        # lock guarantees no sibling checkout races us.
        try:
            order = gateway.create_order(
                amount=transaction_obj.amount,
                currency=transaction_obj.currency,
                receipt=f"sub-{subscription.pk}-tx-{transaction_obj.pk}",
                notes={
                    "plan": plan_tier.name,
                    "organization": subscription.organization.name,
                },
            )
        except gateway.GatewayError as exc:
            # Record the failed attempt in the ledger, then let this atomic
            # COMMIT and re-raise below — no rollback of the audit.
            transaction_obj.status = PaymentTransaction.STATUS_FAILED
            transaction_obj.save(update_fields=["status", "updated_at"])
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
                metadata={
                    "reason": "gateway_order_creation_failed",
                    "amount": str(transaction_obj.amount),
                },
            )
            failure_error = exc
        else:
            transaction_obj.gateway_order_id = order.get("id")
            transaction_obj.save(update_fields=["gateway_order_id", "updated_at"])
            checkout_payload = _checkout_payload(transaction_obj)

    if failure_error is not None:
        raise BillingError(
            "The payment gateway could not create an order. Please retry."
        ) from failure_error
    return checkout_payload


class _CaptureFailed(Exception):
    """Internal control-flow exception carrying the gateway capture error."""

    def __init__(self, original):
        super().__init__(str(original))
        self.original = original


def _checkout_payload(payment_transaction):
    return {
        "gateway_order_id": payment_transaction.gateway_order_id,
        "amount": payment_transaction.amount,
        "currency": payment_transaction.currency,
        "gateway_key": gateway.get_key_id(),  # publishable key ONLY
        "payment_transaction_id": payment_transaction.pk,
    }


# ===========================================================================
# Payment capture (client confirm fast-path) + webhook (source of truth)
# ===========================================================================

def verify_and_capture_payment(payment_transaction_id, gateway_payment_id, gateway_signature):
    """Server-side verification of the client's "payment succeeded" callback
    — the fast-path UX optimization, never the authority. Order of events:
      1. Lock the payment row, then the subscription row (consistent lock
         order with handle_gateway_webhook so the two paths can never
         deadlock).
      2. If the row is already CAPTURED a racing webhook settled it first —
         return the live subscription unchanged (idempotent, no double
         charge, no double end-date extension).
      3. Verify the signature HMAC server-side. The signature covers
         <gateway_order_id>|<gateway_payment_id> with the API secret — the
         order id comes from OUR row, never from the client, and a client-
         asserted "paid: true" boolean is never consulted.
      4. Capture the payment with the gateway (tolerating 'already captured'
         from a racing webhook), mark CAPTURED, then activate.

    Returns the Subscription so the view can serialize the fresh state.
    Raises PaymentSignatureError on mismatch — nothing is activated. A
    gateway capture failure is recorded (FAILED + payment_failed event,
    committed durably) and re-raised as BillingError."""
    try:
        with transaction.atomic():
            payment = (
                PaymentTransaction.objects.select_for_update()
                .select_related("subscription")
                .get(pk=payment_transaction_id)
            )

            if payment.status == PaymentTransaction.STATUS_CAPTURED:
                return payment.subscription  # idempotent — webhook got there first

            if payment.status not in (
                PaymentTransaction.STATUS_CREATED,
                PaymentTransaction.STATUS_AUTHORIZED,
            ):
                raise ServiceConflictError(
                    f"This payment attempt is {payment.status} and cannot be confirmed."
                )

            subscription = Subscription.objects.select_for_update().get(
                pk=payment.subscription_id
            )

            if not payment.gateway_order_id:
                raise ServiceConflictError(
                    "This payment attempt has no gateway order. Start a new checkout."
                )

            # Server-side signature verification — never trust the client's word.
            try:
                gateway.verify_payment_signature(
                    payment.gateway_order_id,
                    gateway_payment_id,
                    gateway_signature,
                )
            except gateway.GatewaySignatureError as exc:
                raise PaymentSignatureError(
                    "Payment signature verification failed. The payment was "
                    "not activated — please contact support if you were charged."
                ) from exc

            payment.gateway_payment_id = gateway_payment_id
            payment.gateway_signature = gateway_signature
            payment.save(update_fields=[
                "gateway_payment_id", "gateway_signature", "updated_at",
            ])

            _capture_and_activate(payment, subscription)
            return subscription
    except _CaptureFailed as exc:
        # Capture genuinely failed at the gateway. Re-read under lock: if a
        # racing webhook settled it in the meantime, report success; else
        # persist the FAILED audit in its own committed atomic and raise.
        with transaction.atomic():
            payment = (
                PaymentTransaction.objects.select_for_update()
                .select_related("subscription")
                .get(pk=payment_transaction_id)
            )
            if payment.status == PaymentTransaction.STATUS_CAPTURED:
                return payment.subscription
            if payment.status != PaymentTransaction.STATUS_FAILED:
                payment.status = PaymentTransaction.STATUS_FAILED
                payment.save(update_fields=["status", "updated_at"])
                SubscriptionEvent.objects.create(
                    subscription=payment.subscription,
                    event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
                    metadata={
                        "reason": "gateway_capture_failed",
                        "detail": str(exc.original),
                        "gateway_payment_id": payment.gateway_payment_id,
                    },
                )
        raise BillingError(
            "The payment could not be captured. Please retry or contact support."
        ) from exc.original


def _capture_and_activate(payment, subscription):
    """Money-settling step shared by the confirm path and webhook path. Both
    rows must already be locked by the caller. On a genuine gateway error the
    payment row's FAILED state and audit event are written by the CALLER's
    failure handler (which owns its own committed atomic), so here we only
    signal via _CaptureFailed — mirroring how _gateway_order_failed works."""
    try:
        gateway.capture_payment(
            payment.gateway_payment_id, payment.amount, payment.currency
        )
    except gateway.GatewayError as exc:
        payment.refresh_from_db()
        if payment.status == PaymentTransaction.STATUS_CAPTURED:
            return  # racing webhook captured it — success either way
        raise _CaptureFailed(exc) from exc

    payment.status = PaymentTransaction.STATUS_CAPTURED
    payment.save(update_fields=["status", "updated_at"])

    _activate_subscription_for_payment(subscription, payment)


def _activate_subscription_for_payment(subscription, payment):
    """Apply a settled payment to the (locked) subscription: switch to the
    paid tier, ACTIVATE, stack end_date from max(today, current future end),
    and write exactly ONE audit event describing the change that actually
    happened (plan_changed / renewed / payment_succeeded — the type is
    chosen so support can answer 'what happened here' from a single row)."""
    prior_plan_name = subscription.plan.name
    prior_status = subscription.status
    plan_changed = subscription.plan_id != payment.plan_id
    is_renewal = (
        subscription.status == Subscription.STATUS_ACTIVE
        and not plan_changed
        and subscription.end_date is not None
    )

    subscription.plan = payment.plan
    subscription.status = Subscription.STATUS_ACTIVE
    if subscription.start_date is None:
        subscription.start_date = _today()
    subscription.end_date = compute_stacked_end_date(
        subscription, _period_days()
    )
    subscription.save()

    if plan_changed:
        event_type = SubscriptionEvent.EVENT_PLAN_CHANGED
    elif is_renewal:
        event_type = SubscriptionEvent.EVENT_RENEWED
    else:
        event_type = SubscriptionEvent.EVENT_PAYMENT_SUCCEEDED

    SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type=event_type,
        metadata={
            "from_plan": prior_plan_name,
            "to_plan": payment.plan.name,
            "from_status": prior_status,
            "to_status": subscription.status,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "payment_transaction_id": payment.pk,
            "gateway_payment_id": payment.gateway_payment_id,
            "end_date": (
                subscription.end_date.isoformat()
                if subscription.end_date else None
            ),
        },
    )


def handle_gateway_webhook(payload):
    """Process a SIGNATURE-VERIFIED gateway webhook (the view verifies the
    raw-body signature before calling this — see the webhook view). This
    function is the source of truth for payment success: it must be able to
    activate a subscription even if the client never called
    verify_and_capture_payment() (user closed the tab after paying).

    Idempotency: gateways retry webhooks, and the client confirm call races
    this path. The PaymentTransaction row is the natural dedupe key — the
    row lock + the already-CAPTURED check mean a duplicate delivery (or a
    late confirm) is a no-op: subscription activated once, one event, one
    end-date extension. Returns a dict the view turns into HTTP 200 quickly
    (gateways retry on non-2xx)."""
    event_type = (payload or {}).get("event")
    entity = _extract_payment_entity(payload)

    if entity is None:
        # Unknown/unsupported events (order.paid, payment.refunded, ...) are
        # acknowledged and ignored — never error, never feed a retry storm.
        return {"status": "ignored", "reason": f"unsupported_event:{event_type}"}

    gateway_payment_id = entity.get("id")
    order_entity = (payload.get("payload") or {}).get("order") or {}
    gateway_order_id = entity.get("order_id") or (order_entity.get("entity") or {}).get("id")

    payment = _find_payment_transaction(gateway_payment_id, gateway_order_id)
    if payment is None:
        # A payment for an order we never created: acknowledge to stop
        # retries, log nothing into the ledger.
        return {"status": "ignored", "reason": "no_matching_payment_transaction"}

    if event_type in ("payment.failed", "payment.pending", "payment.declined"):
        _record_payment_failure(payment, payload, event_type)
        return {"status": "ok", "reason": f"recorded:{event_type}"}

    if event_type not in ("payment.authorized", "payment.captured"):
        return {"status": "ignored", "reason": f"unsupported_event:{event_type}"}

    with transaction.atomic():
        # Lock payment row, then subscription row — same order as
        # verify_and_capture_payment() to prevent deadlocks between paths.
        payment = (
            PaymentTransaction.objects.select_for_update()
            .select_related("subscription")
            .get(pk=payment.pk)
        )
        if payment.status == PaymentTransaction.STATUS_CAPTURED:
            return {"status": "ok", "reason": "already_processed"}

        subscription = Subscription.objects.select_for_update().get(
            pk=payment.subscription_id
        )

        # Never trust a client-asserted amount/status: compare the amount the
        # gateway actually settled against the amount WE created the order
        # for. A mismatch is recorded as failed and never activates anything.
        expected_paise = gateway.amount_in_paise(payment.amount)
        settled_paise = entity.get("amount")
        settled_currency = entity.get("currency") or "INR"
        if (
            settled_paise is None
            or int(settled_paise) != expected_paise
            or settled_currency != payment.currency
        ):
            payment.status = PaymentTransaction.STATUS_FAILED
            payment.raw_webhook_payload = payload
            payment.save(update_fields=["status", "raw_webhook_payload", "updated_at"])
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
                metadata={
                    "reason": "amount_currency_mismatch",
                    "expected_amount_paise": expected_paise,
                    "settled_amount_paise": settled_paise,
                    "settled_currency": settled_currency,
                },
            )
            return {"status": "ok", "reason": "amount_mismatch_not_activated"}

        payment.gateway_payment_id = gateway_payment_id
        payment.raw_webhook_payload = payload
        payment.save(update_fields=[
            "gateway_payment_id", "raw_webhook_payload", "updated_at",
        ])

        if event_type == "payment.authorized":
            # Funds are authorized but not yet settled. Settle them now so the
            # webhook alone can complete a purchase the client never
            # confirmed (tab closed after paying). If a racing confirm call
            # already captured, the re-read below makes this a no-op.
            try:
                gateway.capture_payment(
                    gateway_payment_id, payment.amount, payment.currency
                )
            except gateway.GatewayError as exc:
                payment.refresh_from_db()
                if payment.status != PaymentTransaction.STATUS_CAPTURED:
                    payment.status = PaymentTransaction.STATUS_FAILED
                    payment.save(update_fields=["status", "updated_at"])
                    SubscriptionEvent.objects.create(
                        subscription=subscription,
                        event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
                        metadata={
                            "reason": "webhook_capture_failed",
                            "detail": str(exc),
                            "gateway_payment_id": gateway_payment_id,
                        },
                    )
                    return {"status": "ok", "reason": "capture_failed"}

        # payment.captured (or authorized+captured above): money is settled —
        # activate. The row lock guarantees this runs at most once.
        payment.status = PaymentTransaction.STATUS_CAPTURED
        payment.save(update_fields=["status", "updated_at"])
        _activate_subscription_for_payment(subscription, payment)

    return {"status": "ok", "reason": "activated"}


def _extract_payment_entity(payload):
    """Pull the payment entity out of a razorpay webhook payload. Supported
    events carry it under payload.payment.entity; others return None."""
    event_type = (payload or {}).get("event")
    if not event_type or not event_type.startswith("payment."):
        return None
    inner = (payload.get("payload") or {}).get("payment") or {}
    return inner.get("entity")


def _find_payment_transaction(gateway_payment_id, gateway_order_id):
    """Locate the local PaymentTransaction for a webhook delivery. The
    gateway payment id is the preferred key (set once capture starts); the
    order id covers the window between checkout and capture."""
    if gateway_payment_id:
        payment = (
            PaymentTransaction.objects.filter(gateway_payment_id=gateway_payment_id)
            .select_related("subscription")
            .order_by("-created_at")
            .first()
        )
        if payment is not None:
            return payment
    if gateway_order_id:
        return (
            PaymentTransaction.objects.filter(gateway_order_id=gateway_order_id)
            .select_related("subscription")
            .order_by("-created_at")
            .first()
        )
    return None


@transaction.atomic
def _record_payment_failure(payment, payload, event_type):
    """A payment.failed webhook for a transaction we know about. Idempotent:
    a terminal FAILED row stays failed; the SubscriptionEvent is written only
    on the ACTUAL transition so retried deliveries don't spam the log."""
    payment = (
        PaymentTransaction.objects.select_for_update()
        .select_related("subscription")
        .get(pk=payment.pk)
    )
    if payment.status == PaymentTransaction.STATUS_FAILED:
        return
    subscription = Subscription.objects.select_for_update().get(
        pk=payment.subscription_id
    )
    payment.status = PaymentTransaction.STATUS_FAILED
    payment.raw_webhook_payload = payload
    payment.save(update_fields=["status", "raw_webhook_payload", "updated_at"])
    entity = _extract_payment_entity(payload) or {}
    SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
        metadata={
            "reason": event_type,
            "gateway_payment_id": entity.get("id"),
            "error_description": entity.get("error_description"),
        },
    )


# ===========================================================================
# Settle-on-login (the analogue of licensing's settle hook) + auto-renewal
# ===========================================================================

def settle_subscription_on_login(organization):
    """Server-side analogue of licensing/services.py's
    settle_license_entitlement_on_login(): recomputes EXPIRED when end_date
    has passed and auto-renewal is off (persisting the snapshot + a single
    `expired` audit event), and attempts an auto-renewal charge when it is
    on. Fails SOFT — never raises, never blocks login.

    Implemented as a call-on-login hook (flagged: no Celery exists in this
    codebase yet, so this is the hook the accounts login flow calls; a
    periodic task can later call the same function per org for orgs that
    haven't logged in)."""
    try:
        subscription, _created = get_or_create_subscription(organization)
    except Exception:  # noqa: BLE001 — must never block login
        return None

    try:
        with transaction.atomic():
            subscription = Subscription.objects.select_for_update().get(
                pk=subscription.pk
            )
            if subscription.status != Subscription.STATUS_ACTIVE:
                return subscription
            if subscription.end_date is None or subscription.end_date >= _today():
                return subscription  # still inside the window — nothing to settle

            if subscription.auto_renewal:
                renewed = attempt_auto_renewal(subscription)
                if renewed:
                    return subscription

            # Auto-renewal off (or the renewal charge could not be made —
            # this MVP has no saved-card/token infra; see attempt_auto_renewal)
            # -> persist the EXPIRED snapshot guards have been computing on
            # read all along.
            subscription.status = Subscription.STATUS_EXPIRED
            subscription.auto_renewal = False
            subscription.save(update_fields=["status", "auto_renewal", "updated_at"])
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type=SubscriptionEvent.EVENT_EXPIRED,
                metadata={
                    "end_date": subscription.end_date.isoformat(),
                    "auto_renewal_attempted": True,
                },
            )
            return subscription
    except Exception:  # noqa: BLE001 — settle must never raise
        return None


def attempt_auto_renewal(subscription):
    """Attempt to charge for the next period when auto-renewal is on and the
    window has lapsed. Called only from settle_subscription_on_login() with
    the subscription row already locked.

    Integration note (open product question, see README.md): a real recurring
    charge needs a stored payment instrument — a Razorpay saved card/token
    or a recurring-subscription agreement — and this MVP has no token vault
    yet. Rather than silently charging nothing, this function records why no
    charge was possible (a payment_failed event on a CREATED transaction)
    and returns False so the caller persists EXPIRED. The moment a token
    store exists, this is the single place to slot in the recurring-order +
    capture call."""
    try:
        with transaction.atomic():
            transaction_obj = PaymentTransaction.objects.create(
                subscription=subscription,
                plan=subscription.plan,
                gateway=PaymentTransaction.GATEWAY_RAZORPAY,
                amount=subscription.plan.price,
                currency="INR",
                status=PaymentTransaction.STATUS_CREATED,
            )
            SubscriptionEvent.objects.create(
                subscription=subscription,
                event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
                metadata={
                    "reason": "auto_renewal_no_payment_method",
                    "note": (
                        "Auto-renewal could not be charged: no saved payment "
                        "instrument exists yet (see attempt_auto_renewal)."
                    ),
                    "payment_transaction_id": transaction_obj.pk,
                },
            )
        return False
    except Exception:  # noqa: BLE001 — settle must never raise
        return False
