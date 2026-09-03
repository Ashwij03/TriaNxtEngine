# tria_engine/apps/licensing/services.py
#
# Task 6 (Ashwij) — Referral & Limited Free License Model.
# Direct backend port of the exact 8-step algorithm implemented and unit-
# tested in the frontend's src/shared/services/referralService.js. Every
# function name/return shape below has a 1:1 counterpart there, so porting
# the frontend from localStorage to this API later is a mechanical swap,
# not a redesign.
#
# The one thing pure localStorage cannot do that this layer can:
# `redeem_referral_code()` runs inside `transaction.atomic()` with
# `select_for_update()` on the referral code row, so two simultaneous
# redemption requests for the same code (e.g. two browser tabs, or two
# people racing the last of 3 redemption slots) can never both succeed
# past the R5 cap — the second request blocks until the first commits,
# then re-reads the now-updated redemption_count and is correctly rejected
# if the cap was reached. This is the exact race window flagged as
# "cannot be closed by localStorage alone" in the frontend delivery plan.

from django.db import transaction
from django.utils import timezone

from .models import (
    LicenseEntitlement,
    MAX_REDEMPTIONS_PER_CODE,
    REFERRAL_DAYS_GRANTED,
    ReferralCode,
    ReferralProgramSettings,
    ReferralUsage,
    generate_referral_code,
)

REJECTION_MESSAGES = {
    "invalid_code": "That referral code isn't valid.",
    "self_referral": "You can't redeem your own referral code.",
    "already_redeemed": "You've already redeemed a referral code before.",
    "referral_limit_reached": "This referral code has reached its redemption limit (3/3 used).",
}


def get_redemption_error_message(reason):
    return REJECTION_MESSAGES.get(reason, "This referral code could not be redeemed.")


# ---------------------------------------------------------------------------
# §4 — Referral code generation (R4: static per user, never regenerated)
# ---------------------------------------------------------------------------

def get_or_create_referral_code(user):
    """Idempotent — safe to call on every page load, mirrors
    getOrCreateReferralCode() in referralService.js exactly."""
    try:
        return user.referral_code, False
    except ReferralCode.DoesNotExist:
        pass

    prefix = (getattr(user, "username", "") or getattr(user, "email", "") or "USR")

    # Extremely low collision odds, but guard it explicitly rather than
    # trusting probability alone — same defensive stance as the frontend.
    for _attempt in range(5):
        candidate = generate_referral_code(prefix)
        if not ReferralCode.objects.filter(code=candidate).exists():
            code_record = ReferralCode.objects.create(user=user, code=candidate)
            return code_record, True

    # Fallback: timestamp-suffixed, guaranteed unique.
    fallback_code = f"{generate_referral_code(prefix)}-{int(timezone.now().timestamp())}"
    code_record = ReferralCode.objects.create(user=user, code=fallback_code)
    return code_record, True


def find_referral_code_owner(code_string):
    normalized = (code_string or "").strip().upper()
    if not normalized:
        return None
    try:
        record = ReferralCode.objects.get(code=normalized, active=True)
    except ReferralCode.DoesNotExist:
        return None
    return record.user_id


# ---------------------------------------------------------------------------
# §6 — License entitlement read + auto-expiry (R2)
# ---------------------------------------------------------------------------

def get_license_entitlement(user):
    """Recomputes the active flag against `timezone.now()` on every call —
    R2's auto-expiry, computed on read, never trusted as a stale stored
    boolean. Creates an (inactive) row on first access rather than
    returning None, matching getLicenseEntitlement()'s frontend shape."""
    entitlement, _created = LicenseEntitlement.objects.get_or_create(user=user)
    return entitlement


# ---------------------------------------------------------------------------
# §5.2 — Stacking date math (R3)
# ---------------------------------------------------------------------------

def compute_stacked_end_date(entitlement, days_to_add=REFERRAL_DAYS_GRANTED, now=None):
    """
    New end date = max(now, subscription_end_date, referral_extension_end_date)
    + days_to_add — but only FUTURE dates count as "still active"; a
    stale/past end date is treated the same as having none. Mirrors
    computeStackedEndDate() in referralService.js exactly (same worked
    example: 10 days left on subscription -> 25 days out after redemption).
    """
    now = now or timezone.now()

    candidates = [
        entitlement.subscription_end_date,
        entitlement.referral_extension_end_date,
    ]
    future_candidates = [d for d in candidates if d and d > now]

    base = max(future_candidates) if future_candidates else now
    return base + timezone.timedelta(days=days_to_add)


def _grant_days_to_user(user, days_to_add, source_usage):
    entitlement = get_license_entitlement(user)
    new_end_date = compute_stacked_end_date(entitlement, days_to_add)

    entitlement.referral_extension_end_date = new_end_date
    entitlement.referral_extension_days_total = (
        entitlement.referral_extension_days_total or 0
    ) + days_to_add
    entitlement.referral_extension_source = source_usage
    entitlement.save(update_fields=[
        "referral_extension_end_date",
        "referral_extension_days_total",
        "referral_extension_source",
        "last_checked_at",
    ])
    return entitlement


# ---------------------------------------------------------------------------
# §8 — Admin toggle (R6)
# ---------------------------------------------------------------------------

def is_referrer_bonus_enabled():
    """Default OFF (R6): only the referee benefits unless an Admin turns
    this on."""
    return ReferralProgramSettings.get_solo().referrer_bonus_enabled


def set_referrer_bonus_enabled(enabled):
    """Admin-only action — the calling view must gate this behind
    request.user.is_superuser (see views.py)."""
    settings_row = ReferralProgramSettings.get_solo()
    settings_row.referrer_bonus_enabled = bool(enabled)
    settings_row.save(update_fields=["referrer_bonus_enabled", "updated_at"])
    return settings_row


# ---------------------------------------------------------------------------
# §5 — Redemption flow (R1, R3, R5, R6) — the atomic, race-safe core
# ---------------------------------------------------------------------------

@transaction.atomic
def redeem_referral_code(referee, raw_code):
    """
    Redeems `raw_code` on behalf of `referee`. Wrapped in a single DB
    transaction with select_for_update() locking the referral code row, so
    concurrent redemption attempts against the same code are serialized —
    the exact race window flagged as unfixable in the localStorage-only
    frontend implementation.

    Returns a dict shaped exactly like redeemReferralCode()'s return value
    in referralService.js:
        {"ok": True, "days_granted": 15, "new_end_date": <datetime>,
         "referrer_bonus_granted": bool}
      or
        {"ok": False, "reason": <str>, "message": <str>}
    """
    # Step 1 — normalize.
    normalized_code = (raw_code or "").strip().upper()
    if not normalized_code:
        return {
            "ok": False,
            "reason": "invalid_code",
            "message": get_redemption_error_message("invalid_code"),
        }

    # Step 2 — look up the code, locking the row for the duration of this
    # transaction so a concurrent redemption against the same code can't
    # read a stale redemption_count.
    try:
        code_record = (
            ReferralCode.objects.select_for_update()
            .get(code=normalized_code, active=True)
        )
    except ReferralCode.DoesNotExist:
        return {
            "ok": False,
            "reason": "invalid_code",
            "message": get_redemption_error_message("invalid_code"),
        }

    # Step 3 — self-referral guard.
    if code_record.user_id == referee.id:
        return {
            "ok": False,
            "reason": "self_referral",
            "message": get_redemption_error_message("self_referral"),
        }

    # Step 4 — duplicate-redemption guard: a referee may redeem any code
    # only once, ever. Enforced both here (fast, friendly error message)
    # and at the DB level by ReferralUsage.referee being OneToOneField
    # (the actual race-proof guarantee).
    if ReferralUsage.objects.filter(referee=referee).exists():
        return {
            "ok": False,
            "reason": "already_redeemed",
            "message": get_redemption_error_message("already_redeemed"),
        }

    # Step 5 — cap guard (R5): max 3 redemptions per code. Safe against
    # races because of the select_for_update() lock acquired in Step 2.
    if code_record.redemption_count >= MAX_REDEMPTIONS_PER_CODE:
        return {
            "ok": False,
            "reason": "referral_limit_reached",
            "message": get_redemption_error_message("referral_limit_reached"),
        }

    # Step 6/7 — compute the referee's new window; read the admin toggle (R6).
    now = timezone.now()
    bonus_on = is_referrer_bonus_enabled()

    # Step 8a — grant the referee's days (always happens). The usage row is
    # created first (without a start/end date filled in) purely so we have
    # its primary key to reference as referral_extension_source; it's
    # updated with the real dates immediately after.
    referee_entitlement = get_license_entitlement(referee)
    new_referee_end = compute_stacked_end_date(referee_entitlement, REFERRAL_DAYS_GRANTED, now)

    usage = ReferralUsage.objects.create(
        referee=referee,
        referrer=code_record.user,
        code=code_record,
        referee_days_granted=REFERRAL_DAYS_GRANTED,
        referrer_days_granted=0,  # filled in below if the toggle is ON
        referee_license_start_date=now,
        referee_license_end_date=new_referee_end,
    )

    _grant_days_to_user(referee, REFERRAL_DAYS_GRANTED, usage)

    # Step 8b — grant the referrer's days too, only if the toggle is ON.
    # This stacks independently against the REFERRER's own existing
    # entitlement, not the referee's.
    referrer_bonus_granted = False
    if bonus_on:
        _grant_days_to_user(code_record.user, REFERRAL_DAYS_GRANTED, usage)
        usage.referrer_days_granted = REFERRAL_DAYS_GRANTED
        usage.save(update_fields=["referrer_days_granted"])
        referrer_bonus_granted = True

    # Step 8d — increment the code's redemption count.
    code_record.redemption_count += 1
    code_record.save(update_fields=["redemption_count"])

    # Step 9 — return success.
    return {
        "ok": True,
        "days_granted": REFERRAL_DAYS_GRANTED,
        "new_end_date": new_referee_end,
        "referrer_bonus_granted": referrer_bonus_granted,
    }


# ---------------------------------------------------------------------------
# §11 — non-blocking "settle expiry" hook for the login flow (R2 §6.2)
# ---------------------------------------------------------------------------

def settle_license_entitlement_on_login(user):
    """Reads (and therefore settles/recomputes) a user's entitlement so any
    expired referral bonus is reflected immediately once a session starts.
    Fails soft — never raises, never blocks login."""
    try:
        return get_license_entitlement(user)
    except Exception:  # noqa: BLE001 - deliberately broad, must never block login
        return None


# ---------------------------------------------------------------------------
# §3.2 / Admin stats — read helpers
# ---------------------------------------------------------------------------

def get_referral_stats_for_user(user):
    """Everything the ReferralCard endpoint needs for one user in a single
    call: their code (creating it if needed), redemption usage against
    that code, and their own current license status (as a referee, if they
    redeemed someone else's code)."""
    code_record, _created = get_or_create_referral_code(user)
    entitlement = get_license_entitlement(user)
    my_own_redemption = ReferralUsage.objects.filter(referee=user).first()

    return {
        "code": code_record.code,
        "redemption_count": code_record.redemption_count,
        "max_redemptions": MAX_REDEMPTIONS_PER_CODE,
        "remaining_redemptions": max(
            0, MAX_REDEMPTIONS_PER_CODE - code_record.redemption_count
        ),
        "is_referral_license_active": entitlement.is_referral_license_active(),
        "days_remaining": entitlement.days_remaining(),
        "referral_extension_end_date": entitlement.referral_extension_end_date,
        "has_redeemed_a_code": my_own_redemption is not None,
    }


def get_all_referral_usages():
    """Admin-wide audit view — every successful redemption, most recent
    first."""
    return ReferralUsage.objects.select_related("referee", "referrer", "code").all()


def get_referral_program_summary():
    """Admin-wide summary numbers for the Referral Program settings
    screen."""
    codes = ReferralCode.objects.all()
    usages = ReferralUsage.objects.all()

    total_days_granted = sum(
        (u.referee_days_granted or 0) + (u.referrer_days_granted or 0)
        for u in usages
    )

    top_referrers = list(
        codes.filter(redemption_count__gt=0)
        .order_by("-redemption_count")[:5]
        .values("user_id", "code", "redemption_count")
    )

    return {
        "total_codes_issued": codes.count(),
        "total_redemptions": usages.count(),
        "total_days_granted": total_days_granted,
        "top_referrers": top_referrers,
    }
