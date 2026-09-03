# tria_engine/apps/billing/permissions.py
#
# Role gating helpers for billing endpoints. Follows the pattern
# monitoring/permissions.py introduced: roles are free text per-org records
# in this codebase (accounts.Role.name), so gating compares a normalized
# role name, and a Django superuser bypasses everything (the convention
# every existing Admin-only endpoint uses).
#
# "Admin only" for the subscription endpoints means: the caller is an Admin
# of the SAME organization the subscription belongs to — NOT merely a Django
# superuser and NOT an Admin of some other org. Views pair these helpers
# with an explicit organization match against the row they are about to
# mutate (subscriptions are org-scoped; there is no cross-org admin action
# in this product).

def _role_name(user):
    role = getattr(user, "role", None)
    return (role.name if role else "").strip().lower()


def is_org_admin(user, organization):
    """True when `user` administers `organization`: Django superusers bypass
    (existing backend convention), otherwise the user must belong to that
    exact org with an Admin role."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.organization_id or user.organization_id != organization.id:
        return False
    return _role_name(user) == "admin"


def can_manage_plan_catalog(user):
    """True when `user` may create/edit/deactivate catalog plan tiers
    (SubscriptionManagement.js's PlanFormModal — an Admin-managed screen).

    NOTE (multi-tenant caveat): the plan catalog is a GLOBAL, shared list,
    but this codebase has no platform-admin concept beyond the Django
    superuser. SubscriptionManagement.js lives behind the org Admin role in
    today's single-org deployment, so org Admins are allowed to manage the
    catalog too. If the product later runs true multi-tenant, plan CRUD
    should move behind a platform-admins table and this helper should check
    it — the endpoints already funnel through this one function so the
    change is a one-liner."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.organization_id:
        return False
    return _role_name(user) == "admin"
