# tria_engine/apps/monitoring/models.py
#
# Monitoring Access Requests: lets a CRA / Sponsor / CRO user ask an Admin or
# Site Staff user for time-boxed, view-only access to a site (Organization)
# for a specific date range (e.g. an upcoming monitoring visit). Approval
# scopes access to exactly that site + date range; it never grants edit
# rights.

from django.conf import settings
from django.db import models
from django.utils import timezone

from tria_engine.apps.organizations.models import Organization

# Role names (case-insensitive) allowed to submit a monitoring access
# request. Kept as a plain tuple (rather than a DB-enforced choice) because
# Role names are free text per-organization in this codebase — this just
# governs who *can* request monitoring access, not what their Role record
# is named.
MONITORING_REQUESTER_ROLE_NAMES = ("cra", "cro", "sponsor")


class MonitoringAccessRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_REVOKED = "revoked"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_REVOKED, "Revoked"),
    ]

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monitoring_access_requests",
    )
    # Snapshot of the requester's role name at submission time. Role is
    # editable free text per-org elsewhere in the app, so this keeps an
    # audit-stable label even if the Role record is later renamed/removed.
    requester_role_label = models.CharField(max_length=100, blank=True)

    site = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="monitoring_access_requests",
    )

    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="monitoring_access_decisions",
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"MonitoringAccessRequest({self.requested_by_id} -> "
            f"site={self.site_id}, {self.start_date}..{self.end_date}, "
            f"{self.status})"
        )

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("end_date cannot be before start_date.")

    def approve(self, approved_by, note=""):
        self.status = self.STATUS_APPROVED
        self.approved_by = approved_by
        self.decision_note = note
        self.decided_at = timezone.now()
        self.save(update_fields=[
            "status", "approved_by", "decision_note", "decided_at", "updated_at",
        ])

    def reject(self, approved_by, note=""):
        self.status = self.STATUS_REJECTED
        self.approved_by = approved_by
        self.decision_note = note
        self.decided_at = timezone.now()
        self.save(update_fields=[
            "status", "approved_by", "decision_note", "decided_at", "updated_at",
        ])

    def revoke(self, approved_by, note=""):
        self.status = self.STATUS_REVOKED
        self.approved_by = approved_by
        self.decision_note = note
        self.decided_at = timezone.now()
        self.save(update_fields=[
            "status", "approved_by", "decision_note", "decided_at", "updated_at",
        ])

    @property
    def is_active_today(self):
        today = timezone.localdate()
        return (
            self.status == self.STATUS_APPROVED
            and self.start_date <= today <= self.end_date
        )

    @classmethod
    def has_active_view_access(cls, user, site_id, on_date=None):
        """True if `user` currently holds approved, date-in-range, view-only
        access to `site_id`. Used to gate read-only monitoring views."""
        on_date = on_date or timezone.localdate()
        return cls.objects.filter(
            requested_by=user,
            site_id=site_id,
            status=cls.STATUS_APPROVED,
            start_date__lte=on_date,
            end_date__gte=on_date,
        ).exists()
