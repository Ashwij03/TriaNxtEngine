# tria_engine/apps/subscriptions/services.py
#
# Task 6b (backend) — Subscription & Plan Catalog business rules.
# Backend counterpart to the frontend's subscriptionGuard.js /
# adminService.js limit enforcement. Every function name/return shape
# below has a 1:1 counterpart in the frontend spec, so porting the
# frontend from localStorage to this API later is a mechanical swap, not
# a redesign — the same relationship licensing/services.py has to
# referralService.js.
#
# Conventions (mirroring licensing/services.py):
#   - Fail-soft on reads: read-only helpers never raise for missing
#     data/DB availability (see get_usage's storage placeholder).
#   - Fail-loud only on business rules: SubscriptionLimitError is the
#     single exception type that propagates to a 4xx response, matching
#     the frontend's plain `throw new Error(...)` pattern in
#     studyService.js / adminService.js.
#   - The one thing pure localStorage cannot do that this layer can:
#     `delete_plan()` runs inside `transaction.atomic()` with
#     `select_for_update()` on the plan row, so a plan deletion racing
#     against a concurrent subscription assignment can never succeed
#     past the PROTECT constraint — this is the exact race window flagged
#     as "cannot be closed by localStorage alone" in the frontend spec.

from django.db import transaction
from django.db.models import ProtectedError

from tria_engine.apps.accounts.models import User

from .models import Plan, Subscription


class SubscriptionLimitError(Exception):
    """Raised for business-rule violations only — mirrors the frontend's
    plain `throw new Error(...)` pattern in studyService.js /
    adminService.js. Never raised for DB/storage availability reasons."""


def get_subscription_for_organization(organization):
    """The single Subscription row for an Organization (OneToOne — see
    models.py). Selects the plan and organization in one query since every
    caller reads both."""
    return Subscription.objects.select_related("plan", "organization").get(
        organization=organization
    )


def get_usage(organization):
    """Studies/Users/Storage used vs. limit, for the three KPICards on
    both MyLicense.js and SubscriptionManagement.js (frontend spec §4.1,
    §4.2). User count comes from the real User queryset; the study count
    comes from the real Organization-scoped study queryset IF one exists.

    NOTE on studies: this backend has no Study model yet (audited at
    implementation time), so `organization.studies.count()` would raise
    AttributeError. It fails soft to 0 instead — consistent with what the
    Organization-scoped Study queryset actually contains (nothing) —
    and the real one-liner slots in the moment a Study model with an
    `organization` FK lands.

    Storage usage has no real metric yet anywhere in this backend either
    (the same caveat subscriptionService.js already notes about
    storageLimitGb), so storage_used_gb stays a fixed placeholder until a
    real metric exists."""
    subscription = get_subscription_for_organization(organization)

    studies_manager = getattr(organization, "studies", None)
    studies_used = studies_manager.count() if studies_manager is not None else 0

    users_used = User.objects.filter(
        organization=organization, is_active=True
    ).count()

    return {
        "studies_used": studies_used,
        "studies_limit": subscription.plan.max_studies,
        "users_used": users_used,
        "users_limit": subscription.plan.max_users,
        "storage_used_gb": 0,  # placeholder — no real storage metric exists yet
        "storage_limit_gb": subscription.plan.storage_limit_gb,
    }


def assert_can_create_study(organization):
    """Backend counterpart to assertCanCreateStudy() in the frontend's
    subscriptionGuard.js (frontend spec §3.4). Call this from
    studies/services.py::create_study() the same way the frontend calls
    it as the first line of createStudy() — NOT built in this task since
    the Study model/service lives outside this checklist's file list; this
    function is provided for that future call site to import.

    The status check is recomputed here on every call (never trusted as a
    stale stored value) — mirrors the read-then-recompute stance of
    licensing's is_referral_license_active()."""
    subscription = get_subscription_for_organization(organization)
    if subscription.status != "Active":
        raise SubscriptionLimitError(
            f"Cannot create study: subscription status is {subscription.status}."
        )
    usage = get_usage(organization)
    if usage["studies_used"] >= usage["studies_limit"]:
        raise SubscriptionLimitError(
            "Study limit reached for your current plan. Contact your Admin to upgrade."
        )


def assert_can_approve_user(organization):
    """Backend counterpart to assertCanApproveUser() (frontend spec §3.5)
    — blocks approving a new user onto the organization once the plan's
    user cap is hit or the subscription is not Active."""
    subscription = get_subscription_for_organization(organization)
    if subscription.status != "Active":
        raise SubscriptionLimitError(
            f"Cannot approve user: subscription status is {subscription.status}."
        )
    usage = get_usage(organization)
    if usage["users_used"] >= usage["users_limit"]:
        raise SubscriptionLimitError(
            "User limit reached for your current plan. Contact your Admin to upgrade."
        )


@transaction.atomic
def delete_plan(plan_id):
    """Blocks deleting a plan that's currently assigned to any
    Subscription — matches acceptance criterion #1 in the frontend spec
    ('deleting the currently-assigned one is blocked with a clear
    error'). PROTECT on Subscription.plan already enforces this at the DB
    level; this wrapper turns the resulting ProtectedError into the same
    SubscriptionLimitError shape every other business-rule violation in
    this module uses, so the view layer doesn't need two exception types.

    Runs inside a transaction with select_for_update() locking the plan
    row, so a concurrent subscription assignment to this plan (which
    takes a FOR KEY SHARE lock on the row) serializes against the delete
    rather than racing it."""
    try:
        Plan.objects.select_for_update().get(pk=plan_id).delete()
    except ProtectedError:
        raise SubscriptionLimitError(
            "This plan is currently assigned to an active subscription and cannot be deleted."
        )