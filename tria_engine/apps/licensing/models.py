# tria_engine/apps/licensing/models.py
#
# Task 6 (Ashwij) — Referral & Limited Free License Model.
# New, self-contained app — deliberately decoupled from `accounts` the same
# way `organizations` is, so it can be developed/tested independently of the
# rest of the account system. Field names and shapes mirror the already-live
# frontend localStorage schema (referralCodes / referralUsages /
# licenseEntitlements) exactly, so the eventual frontend swap-over from
# localStorage to this API is a drop-in replacement inside
# referralService.js with no shape translation required.

import random
import string

from django.conf import settings
from django.db import models
from django.utils import timezone

MAX_REDEMPTIONS_PER_CODE = 3
REFERRAL_DAYS_GRANTED = 15


def generate_referral_code(prefix="REF"):
    """
    Mirrors the frontend's generateUniqueCode() in referralService.js:
    a 3-letter prefix, a dash, and 5 random uppercase base-36 characters
    (e.g. "ASH-7F3K9"). Collision retry happens in the service layer
    (services.py::get_or_create_referral_code), not here, since checking
    uniqueness needs a DB query this helper deliberately doesn't make.
    """
    cleaned_prefix = "".join(ch for ch in prefix.upper() if ch.isalpha())[:3]
    if len(cleaned_prefix) < 3:
        cleaned_prefix = (cleaned_prefix + "REF")[:3]

    suffix = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=5)
    )
    return f"{cleaned_prefix}-{suffix}"


class ReferralCode(models.Model):
    """
    One static, permanent row per user (R4). Never regenerated — there is
    deliberately no "rotate code" method anywhere in this app.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral_code",
    )
    code = models.CharField(max_length=32, unique=True, db_index=True)
    redemption_count = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} (user {self.user_id})"

    def has_redemptions_remaining(self):
        return self.active and self.redemption_count < MAX_REDEMPTIONS_PER_CODE


class ReferralUsage(models.Model):
    """
    One row per successful redemption — the audit trail AND the
    duplicate-redemption guard. The OneToOneField on `referee` is what
    enforces "a user may redeem any code only once, ever" at the database
    level (R5's sibling rule, needed so R6 can't be gamed by re-redeeming
    for repeated bonuses) — matching referralUsages in the frontend schema.
    """

    referee = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="redeemed_referral",
    )
    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="successful_referrals",
    )
    code = models.ForeignKey(
        ReferralCode,
        on_delete=models.CASCADE,
        related_name="usages",
    )
    redeemed_at = models.DateTimeField(auto_now_add=True)
    referee_days_granted = models.PositiveIntegerField(default=REFERRAL_DAYS_GRANTED)
    referrer_days_granted = models.PositiveIntegerField(default=0)
    referee_license_start_date = models.DateTimeField()
    referee_license_end_date = models.DateTimeField()

    class Meta:
        ordering = ["-redeemed_at"]

    def __str__(self):
        return f"referee={self.referee_id} referrer={self.referrer_id} code={self.code_id}"


class LicenseEntitlement(models.Model):
    """
    One row per user — the actual license/expiry state. `is_active()`
    recomputes against `timezone.now()` on every call rather than trusting
    a stored boolean (R2 — auto-expiry is computed on read, mirroring
    isReferralLicenseActive() in the frontend service).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="license_entitlement",
    )
    subscription_end_date = models.DateTimeField(null=True, blank=True)
    referral_extension_end_date = models.DateTimeField(null=True, blank=True)
    referral_extension_days_total = models.PositiveIntegerField(default=0)
    referral_extension_source = models.ForeignKey(
        ReferralUsage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_checked_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"entitlement for user {self.user_id}"

    def is_referral_license_active(self):
        if not self.referral_extension_end_date:
            return False
        return self.referral_extension_end_date > timezone.now()

    def days_remaining(self):
        if not self.is_referral_license_active():
            return 0
        delta = self.referral_extension_end_date - timezone.now()
        # Whole days remaining, rounded up — matches Math.ceil() in the
        # frontend's getDaysRemaining().
        seconds = delta.total_seconds()
        return max(0, -(-int(seconds) // 86400))


class ReferralProgramSettings(models.Model):
    """
    Singleton row (always pk=1) holding the R6 admin toggle. There is no
    existing global "AdminSettings" table in this backend yet for this to
    ride on (unlike the frontend, which piggybacks on adminService's
    existing adminSettings localStorage key) — so this app owns its own
    single-row settings table instead. get_solo() below is the only
    supported way to read/write it.
    """

    referrer_bonus_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"referrer_bonus_enabled={self.referrer_bonus_enabled}"

    @classmethod
    def get_solo(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj
