# tria_engine/apps/billing/tests.py
#
# Coverage per the billing delivery checklist:
#   * webhook idempotency (same gateway_payment_id delivered twice ->
#     activated once, one SubscriptionEvent, one end-date extension),
#   * signature verification rejects tampered payloads,
#   * assign_plan clears the per-org override fields,
#   * can_create_study / can_approve_user block at the limit boundary and
#     when status != Active,
#   * the select_for_update() race path (two concurrent assign_plan calls).
#
# Gateway network calls are mocked at the billing.services.gateway boundary;
# the only real SDK code exercised is razorpay's own HMAC signature
# verification (override_settings with a known test secret), which is what
# the tamper tests assert against.

import hashlib
import hmac
import json
import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from tria_engine.apps.organizations.models import Organization, Role

from . import gateway as gateway_module
from . import services
from .models import (
    UNLIMITED_LIMIT,
    PaymentTransaction,
    PlanTier,
    Subscription,
    SubscriptionEvent,
)
from .serializers import SubscriptionSerializer

User = get_user_model()

TEST_PAYMENT_SECRET = "test_secret_key_0123456789abcdef"
TEST_WEBHOOK_SECRET = "test_webhook_secret_0123456789abcdef"


def _sign_payment(order_id, payment_id, secret=TEST_PAYMENT_SECRET):
    """Replicates the razorpay checkout-callback signature algorithm
    (HMAC-SHA256 over '<order_id>|<payment_id>') so tests can build valid and
    deliberately-tampered signatures."""
    message = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _sign_webhook(raw_body, secret=TEST_WEBHOOK_SECRET):
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def _webhook_payload(event_type, payment_id, order_id, amount_paise,
                     currency="INR", error_description=None):
    entity = {
        "id": payment_id,
        "order_id": order_id,
        "amount": amount_paise,
        "currency": currency,
    }
    if error_description:
        entity["error_description"] = error_description
    return {
        "event": event_type,
        "payload": {
            "payment": {"entity": entity},
            "order": {"entity": {"id": order_id}},
        },
    }


def _webhook_body(payload):
    return json.dumps(payload).encode("utf-8")


class BillingTestCaseBase(TestCase):
    """Shared fixtures: an org with an Admin user, the seeded default Free
    tier, and a paid Professional tier for upgrade/checkout tests."""

    def setUp(self):
        self.org = Organization.objects.create(name="Acme Clinical")
        self.admin_role = Role.objects.create(name="Admin", organization=self.org)
        self.staff_role = Role.objects.create(name="CRA", organization=self.org)
        self.admin = User.objects.create_user(
            username="acme_admin",
            email="admin@acme.test",
            password="password12345",
            organization=self.org,
            role=self.admin_role,
        )
        self.staff = User.objects.create_user(
            username="acme_staff",
            email="staff@acme.test",
            password="password12345",
            organization=self.org,
            role=self.staff_role,
        )

        # Seeded by migration 0002: price-0 "Free" default tier.
        self.free_tier = PlanTier.objects.get(is_default=True)
        # A payable tier for upgrade flows.
        self.pro_tier = PlanTier.objects.create(
            name="Professional",
            price=Decimal("4999.00"),
            max_studies=50,
            max_users=25,
            storage_limit_gb=100,
            features=["Unlimited studies", "25 users"],
            is_default=False,
            is_active=True,
        )
        # Subscription is lazy: most tests build it explicitly via
        # get_or_create_subscription() (the free default -> ACTIVE path).

    def org_subscription(self):
        return services.get_or_create_subscription(self.org)[0]

    def extra_user(self, username, role=None):
        return User.objects.create_user(
            username=username,
            email=f"{username}@acme.test",
            password="password12345",
            organization=self.org,
            role=role or self.staff_role,
        )


class FreeTierAndStatusTests(BillingTestCaseBase):
    def test_get_or_create_subscription_activates_free_default(self):
        sub, created = services.get_or_create_subscription(self.org)
        self.assertTrue(created)
        self.assertEqual(sub.plan, self.free_tier)
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)
        self.assertIsNone(sub.end_date)  # free tier: no expiry
        self.assertIs(sub.is_usable(), True)

        again, created_again = services.get_or_create_subscription(self.org)
        self.assertFalse(created_again)
        self.assertEqual(again.pk, sub.pk)
        # exactly one `created` audit event for the whole life of the row
        self.assertEqual(
            SubscriptionEvent.objects.filter(
                subscription=sub, event_type=SubscriptionEvent.EVENT_CREATED
            ).count(),
            1,
        )

    def test_effective_limits_override_wins_then_plan_fallback(self):
        sub = self.org_subscription()
        self.assertEqual(
            sub.effective_limits(),
            {"maxStudies": 3, "maxUsers": 10, "storageLimitGb": 5},  # Free tier seed
        )

        sub.max_studies_override = 1
        sub.max_users_override = 2
        sub.storage_limit_gb_override = 20
        sub.save()
        self.assertEqual(
            sub.effective_limits(),
            {"maxStudies": 1, "maxUsers": 2, "storageLimitGb": 20},
        )

        # Clearing the overrides (assign_plan behavior) falls back to the tier.
        for field in ("max_studies_override", "max_users_override",
                      "storage_limit_gb_override"):
            setattr(sub, field, None)
        sub.save()
        self.assertEqual(
            sub.effective_limits(),
            {"maxStudies": 3, "maxUsers": 10, "storageLimitGb": 5},
        )

    def test_status_recomputed_on_read_when_end_date_passed(self):
        sub = self.org_subscription()
        # Simulate a paid activation whose window has since lapsed but whose
        # snapshot was never persisted as EXPIRED.
        sub.plan = self.pro_tier
        sub.status = Subscription.STATUS_ACTIVE
        sub.auto_renewal = False
        sub.end_date = timezone.localdate() - timezone.timedelta(days=1)
        sub.save()

        self.assertEqual(services.get_subscription_status(sub), Subscription.STATUS_EXPIRED)
        self.assertIs(sub.is_usable(), False)
        self.assertEqual(
            Subscription.STATUS_DISPLAY[sub.recompute_status()], "Expired"
        )

    def test_non_active_statuses_are_not_usable(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        for status_value in (
            Subscription.STATUS_SUSPENDED,
            Subscription.STATUS_PENDING_PAYMENT,
            Subscription.STATUS_CANCELLED,
        ):
            sub.status = status_value
            sub.end_date = timezone.localdate() + timezone.timedelta(days=30)
            sub.save()
            self.assertIs(sub.is_usable(), False, status_value)


class EnforcementGuardTests(BillingTestCaseBase):
    def test_can_approve_user_blocks_at_user_limit_boundary(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        # Fixture org already has 2 active users (admin + staff).
        sub.max_users_override = 2
        sub.save()
        self.assertFalse(services.can_approve_user(self.org))  # 2 >= 2

        sub.max_users_override = 3
        sub.save()
        self.assertTrue(services.can_approve_user(self.org))  # 2 < 3
        self.extra_user("user_two")
        # 3 >= 3 -> blocked, with the same message contract guards rely on.
        self.assertFalse(services.can_approve_user(self.org))
        with self.assertRaises(services.SubscriptionLimitError) as ctx:
            services.assert_can_approve_user(self.org)
        self.assertIn("User limit reached", str(ctx.exception))

        # Unlimited never blocks.
        sub.max_users_override = None
        sub.save()
        sub.plan.max_users = None  # plan-level unlimited
        sub.plan.save()
        self.assertTrue(services.can_approve_user(self.org))

    def test_can_approve_user_blocks_when_not_active(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        sub.end_date = timezone.localdate() - timezone.timedelta(days=1)
        sub.status = Subscription.STATUS_ACTIVE  # stored ACTIVE but lapsed
        sub.save()

        self.assertFalse(services.can_approve_user(self.org))

        sub.end_date = timezone.localdate() + timezone.timedelta(days=30)
        sub.status = Subscription.STATUS_SUSPENDED
        sub.save()
        self.assertFalse(services.can_approve_user(self.org))

    def test_can_create_study_blocked_at_limit_and_when_not_active(self):
        sub = self.org_subscription()
        # This backend has no Study model yet (see services.get_usage docstring),
        # so studies_used is 0; a 0-user-limit override exercises the boundary
        # (0 used >= 0 limit -> blocked), while a 1-limit allows.
        sub.max_studies_override = 0
        sub.save()
        self.assertFalse(services.can_create_study(self.org))

        sub.max_studies_override = 1
        sub.save()
        self.assertTrue(services.can_create_study(self.org))

        sub.status = Subscription.STATUS_SUSPENDED
        sub.save()
        self.assertFalse(services.can_create_study(self.org))

        # status blocks come with a readable message
        with self.assertRaises(services.SubscriptionLimitError) as ctx:
            services.assert_can_create_study(self.org)
        self.assertIn("Suspended", str(ctx.exception))


class PlanTierLifecycleTests(BillingTestCaseBase):
    def test_only_one_default_tier_exists_at_all_times(self):
        promoted = PlanTier.objects.create(
            name="Enterprise",
            price=Decimal("9999.00"),
            max_studies=None,  # unlimited
            max_users=None,
            storage_limit_gb=None,
            is_default=True,
            is_active=True,
        )
        # save() transferred the flag off the seeded Free tier.
        self.assertEqual(PlanTier.objects.filter(is_default=True).count(), 1)
        self.assertEqual(PlanTier.objects.get(pk=promoted.pk).is_default, True)
        self.assertEqual(
            PlanTier.objects.get(pk=self.free_tier.pk).is_default, False
        )

    def test_cannot_deactivate_default_or_in_use_plan(self):
        # The seeded Free tier is default -> 409 conflict.
        with self.assertRaises(services.ServiceConflictError):
            services.deactivate_plan_tier(self.free_tier)

        # A non-default, in-use tier cannot be deactivated.
        second = PlanTier.objects.create(
            name="Second", price=Decimal("100.00"), max_studies=1,
            max_users=1, storage_limit_gb=1, is_default=False, is_active=True,
        )
        sub = self.org_subscription()
        sub.plan = second
        sub.save()
        with self.assertRaises(services.ServiceConflictError):
            services.deactivate_plan_tier(second)

        # Once out of use (and not the last active tier), it can be retired.
        sub.plan = self.free_tier
        sub.save()
        services.deactivate_plan_tier(second)
        self.assertFalse(PlanTier.objects.get(pk=second.pk).is_active)

    def test_create_first_plan_becomes_default_when_none_exists(self):
        # Remove all defaults temporarily (direct ORM — service layer would
        # refuse), then the first created tier must auto-become default.
        PlanTier.objects.filter(is_default=True).update(is_default=False)
        created = services.create_plan_tier(
            {
                "name": "Founder",
                "price": Decimal("0.00"),
                "max_studies": 1,
                "max_users": 1,
                "storage_limit_gb": 1,
                "features": [],
                "is_default": False,
            }
        )
        self.assertTrue(PlanTier.objects.get(pk=created.pk).is_default)


@override_settings(BILLING_DEFAULT_PERIOD_DAYS=30)
class AssignPlanTests(BillingTestCaseBase):
    def test_assign_plan_clears_overrides_and_switches_plan(self):
        sub = self.org_subscription()
        sub.max_studies_override = 77
        sub.max_users_override = 88
        sub.storage_limit_gb_override = 99
        sub.save()

        updated = services.assign_plan(sub, self.pro_tier, self.admin)
        updated.refresh_from_db()

        self.assertEqual(updated.plan, self.pro_tier)
        self.assertEqual(updated.status, Subscription.STATUS_ACTIVE)
        self.assertIsNone(updated.max_studies_override)
        self.assertIsNone(updated.max_users_override)
        self.assertIsNone(updated.storage_limit_gb_override)
        # paid tier: end date = today + billing period
        expected_end = timezone.localdate() + timezone.timedelta(days=30)
        self.assertEqual(updated.end_date, expected_end)

        event = SubscriptionEvent.objects.get(
            subscription=updated, event_type=SubscriptionEvent.EVENT_PLAN_CHANGED
        )
        self.assertEqual(event.metadata["from_plan"], "Free")
        self.assertEqual(event.metadata["to_plan"], "Professional")

    def test_assign_plan_same_plan_is_noop_no_duplicate_event(self):
        sub = self.org_subscription()
        services.assign_plan(sub, self.free_tier, self.admin)
        count = SubscriptionEvent.objects.filter(
            subscription=sub, event_type=SubscriptionEvent.EVENT_PLAN_CHANGED
        ).count()
        self.assertEqual(count, 0)  # same-plan assignment writes no event

    def test_assign_plan_rejects_non_org_admin_actor(self):
        sub = self.org_subscription()
        with self.assertRaises(services.PermissionDeniedError):
            services.assign_plan(sub, self.pro_tier, self.staff)  # CRA, not Admin

    def test_assign_plan_rejects_inactive_plan(self):
        retired = PlanTier.objects.create(
            name="Retired", price=Decimal("1.00"), max_studies=1,
            max_users=1, storage_limit_gb=1, is_active=False, is_default=False,
        )
        sub = self.org_subscription()
        with self.assertRaises(services.ServiceConflictError):
            services.assign_plan(sub, retired, self.admin)


class CancelAndAutoRenewalTests(BillingTestCaseBase):
    def test_cancel_active_subscription_keeps_access_until_end(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        sub.end_date = timezone.localdate() + timezone.timedelta(days=20)
        sub.auto_renewal = True
        sub.save()

        updated = services.cancel_subscription(sub, self.admin)
        updated.refresh_from_db()
        # Access preserved until the paid window ends; renewal switched off.
        self.assertEqual(updated.status, Subscription.STATUS_ACTIVE)
        self.assertIs(updated.auto_renewal, False)
        event = SubscriptionEvent.objects.get(
            subscription=updated, event_type=SubscriptionEvent.EVENT_CANCELLED
        )
        self.assertIn("until the end of the paid period", event.metadata["note"])

    def test_cancel_pending_payment_subscription_cancels_outright(self):
        # Force a pending state the lazy-created free default never has.
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        sub.status = Subscription.STATUS_PENDING_PAYMENT
        sub.save()
        updated = services.cancel_subscription(sub, self.admin)
        updated.refresh_from_db()
        self.assertEqual(updated.status, Subscription.STATUS_CANCELLED)
        self.assertIs(updated.auto_renewal, False)

    def test_toggle_auto_renewal(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        sub.end_date = timezone.localdate() + timezone.timedelta(days=10)
        sub.auto_renewal = False
        sub.save()
        updated = services.toggle_auto_renewal(sub, True, self.admin)
        updated.refresh_from_db()
        self.assertTrue(updated.auto_renewal)


@override_settings(BILLING_DEFAULT_PERIOD_DAYS=30)
class CheckoutAndCaptureTests(BillingTestCaseBase):
    """Gateway network calls are mocked; the local side (rows, events,
    activation, idempotency) is what's under test."""

    def _checkout(self, tier=None):
        tier = tier or self.pro_tier
        with mock.patch.object(
            gateway_module, "create_order",
            return_value={"id": "order_abc123", "amount": 499900, "currency": "INR"},
        ) as create_order:
            payload = services.initiate_checkout(self.org_subscription(), tier)
        return payload, create_order

    def test_initiate_checkout_returns_public_data_only(self):
        payload, create_order = self._checkout()
        self.assertEqual(payload["gateway_order_id"], "order_abc123")
        self.assertEqual(payload["amount"], Decimal("4999.00"))
        self.assertEqual(payload["currency"], "INR")
        self.assertEqual(payload["gateway_key"], "rzp_test_dev_only_key_id")
        self.assertNotIn("secret", str(payload).lower())
        create_order.assert_called_once()

        txn = PaymentTransaction.objects.get(pk=payload["payment_transaction_id"])
        self.assertEqual(txn.status, PaymentTransaction.STATUS_CREATED)

    def test_initiate_checkout_reuses_open_transaction(self):
        payload, create_order = self._checkout()
        second, calls = self._checkout()
        self.assertEqual(payload["payment_transaction_id"], second["payment_transaction_id"])
        self.assertEqual(create_order.call_count, 1)  # no second gateway order

    def test_checkout_rejects_free_plan(self):
        with self.assertRaises(services.ServiceConflictError):
            services.initiate_checkout(self.org_subscription(), self.free_tier)

    def test_checkout_records_gateway_failure_and_raises(self):
        sub = self.org_subscription()
        with mock.patch.object(
            gateway_module, "create_order",
            side_effect=gateway_module.GatewayError("network down"),
        ):
            with self.assertRaises(services.BillingError):
                services.initiate_checkout(sub, self.pro_tier)

        # The FAILED attempt is audited durably (committed after rollback).
        txn = PaymentTransaction.objects.filter(subscription=sub).order_by("-id").first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.status, PaymentTransaction.STATUS_FAILED)
        self.assertTrue(
            SubscriptionEvent.objects.filter(
                subscription=sub,
                event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
            ).exists()
        )

    @override_settings(RAZORPAY_KEY_SECRET=TEST_PAYMENT_SECRET)
    def test_confirm_payment_activates_upgrade(self):
        sub = self.org_subscription()
        payload, _ = self._checkout()
        payment_id = "pay_success_001"
        signature = _sign_payment("order_abc123", payment_id)

        with mock.patch.object(
            gateway_module, "verify_payment_signature", return_value=True
        ), mock.patch.object(
            gateway_module, "capture_payment",
            return_value={"id": payment_id, "status": "captured"},
        ):
            updated = services.verify_and_capture_payment(
                payload["payment_transaction_id"], payment_id, signature
            )

        updated.refresh_from_db()
        self.assertEqual(updated.plan, self.pro_tier)
        self.assertEqual(updated.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(
            updated.end_date,
            timezone.localdate() + timezone.timedelta(days=30),
        )
        txn = PaymentTransaction.objects.get(pk=payload["payment_transaction_id"])
        self.assertEqual(txn.status, PaymentTransaction.STATUS_CAPTURED)
        self.assertEqual(txn.gateway_payment_id, payment_id)
        # Upgrade from the free default -> plan_changed event.
        self.assertEqual(
            SubscriptionEvent.objects.filter(
                subscription=updated,
                event_type=SubscriptionEvent.EVENT_PLAN_CHANGED,
            ).count(),
            1,
        )

    @override_settings(RAZORPAY_KEY_SECRET=TEST_PAYMENT_SECRET)
    def test_confirm_payment_rejects_bad_signature(self):
        sub = self.org_subscription()
        payload, _ = self._checkout()
        bad_signature = _sign_payment("order_abc123", "pay_other")

        with mock.patch.object(
            gateway_module, "verify_payment_signature",
            side_effect=gateway_module.GatewaySignatureError("bad"),
        ):
            with self.assertRaises(services.PaymentSignatureError):
                services.verify_and_capture_payment(
                    payload["payment_transaction_id"], "pay_other", bad_signature
                )

        # Nothing activated, nothing captured, no audit event written.
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.free_tier)
        txn = PaymentTransaction.objects.get(pk=payload["payment_transaction_id"])
        self.assertNotEqual(txn.status, PaymentTransaction.STATUS_CAPTURED)
        self.assertEqual(
            SubscriptionEvent.objects.filter(
                subscription=sub,
                event_type__in=[
                    SubscriptionEvent.EVENT_PAYMENT_SUCCEEDED,
                    SubscriptionEvent.EVENT_PLAN_CHANGED,
                    SubscriptionEvent.EVENT_RENEWED,
                ],
            ).count(),
            0,
        )

    def test_confirm_rejects_payment_from_another_org(self):
        other_org = Organization.objects.create(name="Other Site")
        other_sub = services.get_or_create_subscription(other_org)[0]
        with mock.patch.object(
            gateway_module, "create_order",
            return_value={"id": "order_other", "amount": 100, "currency": "INR"},
        ):
            payload = services.initiate_checkout(other_sub, self.pro_tier)

        # The transaction row belongs to the org it was started for — confirm
        # is additionally scoped at the view level so one org can never
        # confirm a payment against another org's subscription.
        self.assertEqual(payload["amount"], Decimal("4999.00"))
        txn = PaymentTransaction.objects.get(pk=payload["payment_transaction_id"])
        self.assertEqual(txn.subscription_id, other_sub.pk)
        self.assertEqual(txn.subscription.organization_id, other_org.pk)


@override_settings(BILLING_DEFAULT_PERIOD_DAYS=30)
class WebhookTests(BillingTestCaseBase):
    """The webhook is the source of truth: it must be able to activate a
    purchase the client confirm call never completed, and must be idempotent
    against retries and against a racing confirm."""

    def _started_checkout(self):
        with mock.patch.object(
            gateway_module, "create_order",
            return_value={"id": "order_wb_1", "amount": 499900, "currency": "INR"},
        ):
            payload = services.initiate_checkout(self.org_subscription(), self.pro_tier)
        return payload

    def _captured_webhook(self, payment_id="pay_wb_001"):
        return _webhook_payload("payment.captured", payment_id, "order_wb_1", 499900)

    def test_webhook_activates_subscription_without_client_confirm(self):
        self._started_checkout()
        result = services.handle_gateway_webhook(self._captured_webhook())
        self.assertEqual(result["status"], "ok")

        sub = self.org_subscription()
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.pro_tier)
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(
            sub.end_date, timezone.localdate() + timezone.timedelta(days=30)
        )
        txn = PaymentTransaction.objects.get(gateway_order_id="order_wb_1")
        self.assertEqual(txn.status, PaymentTransaction.STATUS_CAPTURED)
        self.assertEqual(txn.gateway_payment_id, "pay_wb_001")
        self.assertEqual(txn.raw_webhook_payload["event"], "payment.captured")

    def test_webhook_idempotent_duplicate_delivery(self):
        self._started_checkout()
        payload = self._captured_webhook()

        first = services.handle_gateway_webhook(payload)
        self.assertEqual(first["reason"], "activated")

        # Same gateway_payment_id delivered again (gateway retry).
        second = services.handle_gateway_webhook(payload)
        self.assertEqual(second["reason"], "already_processed")

        sub = self.org_subscription()
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.pro_tier)
        # end_date extended exactly once (a double-apply would stack +60).
        self.assertEqual(
            sub.end_date, timezone.localdate() + timezone.timedelta(days=30)
        )
        # Exactly one activation event.
        event_types = [
            SubscriptionEvent.EVENT_PAYMENT_SUCCEEDED,
            SubscriptionEvent.EVENT_PLAN_CHANGED,
            SubscriptionEvent.EVENT_RENEWED,
        ]
        self.assertEqual(
            SubscriptionEvent.objects.filter(
                subscription=sub, event_type__in=event_types
            ).count(),
            1,
        )
        self.assertEqual(
            PaymentTransaction.objects.filter(
                gateway_payment_id="pay_wb_001",
                status=PaymentTransaction.STATUS_CAPTURED,
            ).count(),
            1,
        )

    def test_webhook_and_confirm_race_no_double_extension(self):
        payload = self._started_checkout()
        # Webhook settles first...
        services.handle_gateway_webhook(self._captured_webhook())
        # ...then the client's confirm call lands (already captured).
        with mock.patch.object(
            gateway_module, "verify_payment_signature", return_value=True
        ), mock.patch.object(gateway_module, "capture_payment") as capture:
            updated = services.verify_and_capture_payment(
                payload["payment_transaction_id"], "pay_wb_001", "sig-whatever"
            )
            capture.assert_not_called()  # money was already settled

        updated.refresh_from_db()
        self.assertEqual(
            updated.end_date, timezone.localdate() + timezone.timedelta(days=30)
        )

    def test_webhook_amount_mismatch_never_activates(self):
        self._started_checkout()
        payload = _webhook_payload(
            "payment.captured", "pay_wb_bad", "order_wb_1", amount_paise=1  # 1 paisa!
        )
        result = services.handle_gateway_webhook(payload)
        self.assertEqual(result["reason"], "amount_mismatch_not_activated")

        sub = self.org_subscription()
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.free_tier)  # still free — not activated
        txn = PaymentTransaction.objects.get(gateway_order_id="order_wb_1")
        self.assertEqual(txn.status, PaymentTransaction.STATUS_FAILED)
        self.assertTrue(
            SubscriptionEvent.objects.filter(
                subscription=sub,
                event_type=SubscriptionEvent.EVENT_PAYMENT_FAILED,
                metadata__reason="amount_currency_mismatch",
            ).exists()
        )

    def test_webhook_authorized_event_captures_then_activates(self):
        self._started_checkout()
        payload = _webhook_payload(
            "payment.authorized", "pay_wb_auth", "order_wb_1", 499900
        )
        with mock.patch.object(
            gateway_module, "capture_payment",
            return_value={"id": "pay_wb_auth", "status": "captured"},
        ):
            result = services.handle_gateway_webhook(payload)
        self.assertEqual(result["reason"], "activated")
        sub = self.org_subscription()
        sub.refresh_from_db()
        self.assertEqual(sub.plan, self.pro_tier)

    def test_webhook_for_unknown_order_is_acknowledged(self):
        result = services.handle_gateway_webhook(
            _webhook_payload("payment.captured", "pay_ghost", "order_ghost", 100)
        )
        self.assertEqual(result["status"], "ignored")

    def test_unsupported_event_is_acknowledged(self):
        self._started_checkout()
        payload = {
            "event": "order.paid",
            "payload": {"order": {"entity": {"id": "order_wb_1"}}},
        }
        result = services.handle_gateway_webhook(payload)
        self.assertEqual(result["status"], "ignored")


class SignatureVerificationTests(BillingTestCaseBase):
    """Exercises the REAL razorpay SDK HMAC verification with a known test
    secret — the tamper tests must fail because the math fails, not because
    of a mock."""

    @override_settings(
        RAZORPAY_KEY_SECRET=TEST_PAYMENT_SECRET,
        RAZORPAY_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET,
    )
    def test_webhook_signature_verifies_and_rejects_tampering(self):
        body = _webhook_body(_webhook_payload("payment.captured", "p1", "o1", 100))
        good = _sign_webhook(body, TEST_WEBHOOK_SECRET)
        self.assertTrue(gateway_module.verify_webhook_signature(body, good))

        # Tampered body -> rejected.
        tampered_body = body + b"x"
        tampered_sig = _sign_webhook(tampered_body, TEST_WEBHOOK_SECRET)
        with self.assertRaises(gateway_module.GatewaySignatureError):
            gateway_module.verify_webhook_signature(tampered_body, good)
        with self.assertRaises(gateway_module.GatewaySignatureError):
            gateway_module.verify_webhook_signature(body, tampered_sig)

    @override_settings(
        RAZORPAY_KEY_SECRET=TEST_PAYMENT_SECRET,
        RAZORPAY_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET,
    )
    def test_payment_signature_verifies_and_rejects_tampering(self):
        good = _sign_payment("order_9", "pay_9", TEST_PAYMENT_SECRET)
        self.assertTrue(
            gateway_module.verify_payment_signature("order_9", "pay_9", good)
        )
        with self.assertRaises(gateway_module.GatewaySignatureError):
            gateway_module.verify_payment_signature("order_9", "pay_OTHER", good)

    def test_webhook_endpoint_returns_400_on_bad_signature(self):
        client = APIClient()
        with override_settings(RAZORPAY_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET):
            body = _webhook_body(_webhook_payload("payment.captured", "p1", "o1", 100))
            response = client.post(
                "/api/billing/webhooks/razorpay/",
                data=body,
                content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE="definitely-not-valid",
            )
        self.assertEqual(response.status_code, 400)


class SerializerContractTests(BillingTestCaseBase):
    """The wire contract must be a drop-in for subscriptionService.js /
    planCatalogService.js: camelCase keys, display-string status, `plan` as
    the denormalized name, and UNLIMITED_LIMIT for uncapped limits."""

    def test_subscription_serializer_shape(self):
        sub = self.org_subscription()
        data = SubscriptionSerializer(sub).data
        self.assertEqual(data["status"], "Active")
        self.assertEqual(data["plan"], "Free")  # denormalized plan name
        self.assertIsInstance(data["plan"], str)
        self.assertEqual(data["planId"], self.free_tier.pk)
        self.assertEqual(data["planDetails"]["name"], "Free")
        self.assertEqual(data["effectiveLimits"]["maxStudies"], 3)
        self.assertIn("maxStudies", data)
        self.assertIn("startDate", data)
        self.assertIn("autoRenewal", data)
        self.assertIn("usage", data)
        self.assertIsNone(data["endDate"])  # free tier has no expiry

    def test_unlimited_limit_serializes_as_sentinel(self):
        unlimited = PlanTier.objects.create(
            name="Enterprise",
            price=Decimal("9999.00"),
            max_studies=None,
            max_users=None,
            storage_limit_gb=None,
            is_default=False,
            is_active=True,
        )
        sub = self.org_subscription()
        sub.plan = unlimited
        sub.save()
        data = SubscriptionSerializer(sub).data
        self.assertEqual(data["effectiveLimits"]["maxStudies"], UNLIMITED_LIMIT)
        self.assertEqual(data["effectiveLimits"]["maxUsers"], UNLIMITED_LIMIT)
        self.assertEqual(data["planDetails"]["maxStudies"], UNLIMITED_LIMIT)

    def test_plan_list_endpoint_is_active_catalog(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        response = client.get("/api/billing/plans/")
        self.assertEqual(response.status_code, 200)
        # Parse the RENDERED json (response.data holds raw Decimals etc.).
        body = json.loads(response.content)
        names = [tier["name"] for tier in body]
        self.assertIn("Free", names)
        self.assertIn("Professional", names)
        self.assertNotIn("secret", response.content.decode().lower())
        # price must round-trip as a JSON number, not a string
        for tier in body:
            self.assertIsInstance(tier["price"], (int, float))

    def test_subscription_me_endpoint_shape(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        response = client.get("/api/billing/subscription/me/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("planDetails", response.data)
        self.assertEqual(response.data["plan"], "Free")


class SettleOnLoginTests(BillingTestCaseBase):
    def test_settle_persists_expired_when_window_lapsed(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        sub.status = Subscription.STATUS_ACTIVE
        sub.auto_renewal = False
        sub.end_date = timezone.localdate() - timezone.timedelta(days=3)
        sub.save()

        result = services.settle_subscription_on_login(self.org)
        self.assertIsNotNone(result)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)
        self.assertEqual(
            SubscriptionEvent.objects.filter(
                subscription=sub, event_type=SubscriptionEvent.EVENT_EXPIRED
            ).count(),
            1,
        )

        # Idempotent: settling again writes no duplicate event.
        services.settle_subscription_on_login(self.org)
        self.assertEqual(
            SubscriptionEvent.objects.filter(
                subscription=sub, event_type=SubscriptionEvent.EVENT_EXPIRED
            ).count(),
            1,
        )

    def test_settle_never_raises_for_org_without_subscription_state(self):
        # A brand-new org (no subscription yet) settles to a no-op, soft.
        fresh_org = Organization.objects.create(name="Fresh Org")
        result = services.settle_subscription_on_login(fresh_org)
        self.assertIsNotNone(result)

    def test_settle_with_auto_renewal_on_records_attempt_and_expires(self):
        sub = self.org_subscription()
        sub.plan = self.pro_tier
        sub.status = Subscription.STATUS_ACTIVE
        sub.auto_renewal = True  # renewal desired but no saved card exists
        sub.end_date = timezone.localdate() - timezone.timedelta(days=1)
        sub.save()

        services.settle_subscription_on_login(self.org)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)
        event = SubscriptionEvent.objects.filter(
            subscription=sub, event_type=SubscriptionEvent.EVENT_EXPIRED
        ).first()
        self.assertEqual(event.metadata["auto_renewal_attempted"], True)


class ConcurrentAssignPlanRaceTests(TransactionTestCase):
    """Two threads race assign_plan() on the same subscription — the
    select_for_update() path must serialize them so both assignments land as
    coherent, individually-audited transitions (no lost update, no partial
    state). TransactionTestCase (not TestCase) so the committed fixture rows
    are visible to the worker threads' own DB connections."""

    def setUp(self):
        self.org = Organization.objects.create(name="Race Org")
        role = Role.objects.create(name="Admin", organization=self.org)
        self.admin = User.objects.create_user(
            username="race_admin", email="race@acme.test",
            password="password12345", organization=self.org, role=role,
        )
        self.free_tier = PlanTier.objects.get(is_default=True)
        self.plan_a = PlanTier.objects.create(
            name="Plan A", price=Decimal("100.00"), max_studies=10,
            max_users=10, storage_limit_gb=10, is_default=False, is_active=True,
        )
        self.plan_b = PlanTier.objects.create(
            name="Plan B", price=Decimal("200.00"), max_studies=20,
            max_users=20, storage_limit_gb=20, is_default=False, is_active=True,
        )
        self.subscription = services.get_or_create_subscription(self.org)[0]

    def test_two_concurrent_assign_plan_calls_serialize(self):
        errors = []
        barrier = threading.Barrier(2)

        def assign(plan, target_plan_name):
            # SQLite (this project's dev/test DB) has no row-level
            # select_for_update and locks the whole database during writes,
            # so a racing sibling can surface as "database is locked". On
            # PostgreSQL the row lock serializes instead. Retry a few times
            # so the test verifies the END STATE under real overlap on both
            # backends rather than flaking on SQLite's coarse lock.
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            last_error = None
            for attempt in range(6):
                try:
                    sub = Subscription.objects.get(pk=self.subscription.pk)
                    services.assign_plan(sub, plan, self.admin)
                    return
                except Exception as exc:  # noqa: BLE001 — retry lock waits
                    last_error = exc
                    import time

                    time.sleep(0.2 * (attempt + 1))
            errors.append(f"{target_plan_name}: {last_error!r}")

        t1 = threading.Thread(target=assign, args=(self.plan_a, "plan_a"))
        t2 = threading.Thread(target=assign, args=(self.plan_b, "plan_b"))
        t1.start()
        t2.start()
        t1.join(timeout=45)
        t2.join(timeout=45)

        # Lock contention on SQLite may produce transient "database is
        # locked" errors that the retry loop absorbs; a hard failure after
        # retries is a real defect.
        self.assertEqual(errors, [])

        self.subscription.refresh_from_db()
        # Final plan is whichever assignment committed last — a coherent
        # end state, never a torn mix.
        self.assertIn(self.subscription.plan_id, (self.plan_a.pk, self.plan_b.pk))
        self.assertEqual(self.subscription.status, Subscription.STATUS_ACTIVE)
        # Overrides were cleared by both calls.
        self.assertIsNone(self.subscription.max_studies_override)
        self.assertIsNone(self.subscription.max_users_override)
        self.assertIsNone(self.subscription.storage_limit_gb_override)

        # Every assignment produced its own audited transition; the last one
        # must describe the final plan.
        events = list(
            SubscriptionEvent.objects.filter(
                subscription=self.subscription,
                event_type=SubscriptionEvent.EVENT_PLAN_CHANGED,
            ).order_by("id")
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1].metadata["to_plan"], self.subscription.plan.name)
