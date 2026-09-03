# tria_engine/apps/billing/views.py
#
# DRF APIView endpoints for the billing app — style follows licensing/views.py
# (plain APIView classes, IsAuthenticated, swagger_auto_schema on every
# method) with monitoring/views.py's role helpers for org-scoped "Admin only"
# gating. No DB writes or business rules live here — everything funnels
# through billing/services.py.
#
# CSRF note: the gateway webhook endpoint is csrf_exempt and carries no
# session auth (the gateway authenticates via its signature header). The
# session-authenticated endpoints below intentionally keep DRF's normal
# SessionAuthentication CSRF enforcement, like licensing/views.py.

import json

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .gateway import GatewaySignatureError
from .models import PlanTier, PaymentTransaction
from .permissions import can_manage_plan_catalog, is_org_admin
from .serializers import (
    AutoRenewalSerializer,
    CheckoutRequestSerializer,
    CheckoutResponseSerializer,
    PaymentConfirmSerializer,
    PlanFormSerializer,
    PlanIdBodySerializer,
    PlanTierSerializer,
    SubscriptionSerializer,
)


def _error_response(exc):
    """Map billing service exceptions onto the HTTP status the operation
    deserves. The webhook view handles its own signature errors before the
    service layer is reached."""
    from .services import (
        BillingError,
        PaymentSignatureError,
        PermissionDeniedError,
        ServiceConflictError,
        SubscriptionLimitError,
    )

    if isinstance(exc, (SubscriptionLimitError, PermissionDeniedError)):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, PaymentSignatureError):
        code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, ServiceConflictError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, BillingError):
        code = status.HTTP_400_BAD_REQUEST
    else:
        return None
    return Response({"message": str(exc)}, status=code)


def _get_subscription_or_404(organization):
    """The (lazily created) billing Subscription for an org, or a 404-style
    response. Every org-scoped endpoint funnels through here so org
    resolution and subscription resolution live in one place."""
    try:
        subscription, _created = services.get_or_create_subscription(organization)
        return subscription, None
    except services.BillingError as exc:
        return None, Response({"message": str(exc)}, status=status.HTTP_404_NOT_FOUND)


# ===========================================================================
# Plan catalog — GET any role; POST/PUT/PATCH/DELETE Admin only
# ===========================================================================

class PlanTierListCreateAPI(APIView):
    """GET  /api/billing/plans/        — the active plan catalog. Any
         authenticated role can view it (MyLicense.js's upgrade screen).
    POST /api/billing/plans/        — Admin only: create a plan tier.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        tiers = PlanTier.objects.filter(is_active=True)
        serializer = PlanTierSerializer(tiers, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=PlanFormSerializer)
    def post(self, request):
        if not can_manage_plan_catalog(request.user):
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        serializer = PlanFormSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            tier = services.create_plan_tier(serializer.validated_data)
        except services.BillingError as exc:
            return _error_response(exc)

        # Post-write verification — mirrors the "...creation validation
        # failed" checks in organizations/views.py.
        if not PlanTier.objects.filter(pk=tier.pk).exists():
            return Response(
                {"message": "Plan tier creation validation failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            PlanTierSerializer(tier).data, status=status.HTTP_201_CREATED
        )


class PlanTierDetailAPI(APIView):
    """PUT/PATCH /api/billing/plans/<id>/ — Admin only: edit a plan tier.
    DELETE    /api/billing/plans/<id>/ — Admin only: SOFT-deactivate a plan
         tier. Blocked with 409 when it is the default, is in active use, or
         is the last active plan — mirroring SubscriptionManagement.js's
         "deleting the last plan / deleting an in-use plan throws" rules.
    """

    permission_classes = [IsAuthenticated]

    def _get_tier(self, request, pk):
        if not can_manage_plan_catalog(request.user):
            return None, Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        try:
            tier = PlanTier.objects.get(pk=pk)
        except PlanTier.DoesNotExist:
            return None, Response(
                {"message": "Plan not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return tier, None

    @swagger_auto_schema(request_body=PlanFormSerializer)
    def put(self, request, pk):
        tier, error = self._get_tier(request, pk)
        if error:
            return error

        serializer = PlanFormSerializer(instance=tier, data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = services.update_plan_tier(tier, serializer.validated_data)
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(PlanTierSerializer(updated).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=PlanFormSerializer)
    def patch(self, request, pk):
        tier, error = self._get_tier(request, pk)
        if error:
            return error

        serializer = PlanFormSerializer(instance=tier, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = services.update_plan_tier(tier, serializer.validated_data)
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(PlanTierSerializer(updated).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(responses={204: "Plan deactivated", 409: "Plan in use"})
    def delete(self, request, pk):
        tier, error = self._get_tier(request, pk)
        if error:
            return error

        try:
            services.deactivate_plan_tier(tier)
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Org subscription — read for every role, admin actions for org Admins
# ===========================================================================

class SubscriptionMeAPI(APIView):
    """GET /api/billing/subscription/me/ — the caller's own organization's
    subscription: status (recomputed), plan + planDetails, dates, overrides,
    effectiveLimits and live usage. Any authenticated role may read it — this
    is what MyLicense.js calls for every role."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = request.user.organization
        if not organization:
            return Response(
                {"message": "No organization assigned to this user"},
                status=status.HTTP_404_NOT_FOUND,
            )

        subscription, error = _get_subscription_or_404(organization)
        if error:
            return error

        # Settle first so a lapsed window is persisted (and audit-logged)
        # before the user sees it; fails soft by contract.
        services.settle_subscription_on_login(organization)

        subscription.refresh_from_db()
        return Response(
            SubscriptionSerializer(subscription).data, status=status.HTTP_200_OK
        )


class _OrgAdminActionAPI(APIView):
    """Shared plumbing for the org-admin subscription actions below: resolves
    the caller's org subscription and rejects non-Admins / cross-org callers
    BEFORE any service work. "Admin only" = Admin role of the SAME org the
    subscription belongs to (superusers bypass the role check)."""

    permission_classes = [IsAuthenticated]

    def resolve_subscription(self, request):
        organization = request.user.organization
        if not organization:
            return None, Response(
                {"message": "No organization assigned to this user"},
                status=status.HTTP_404_NOT_FOUND,
            )
        subscription, error = _get_subscription_or_404(organization)
        if error:
            return subscription, error
        if not is_org_admin(request.user, organization):
            return None, Response(
                {"message": "Only an Admin of this organization can do that."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return subscription, None

    def _resolve_plan(self, plan_id):
        try:
            tier = PlanTier.objects.get(pk=plan_id, is_active=True)
        except PlanTier.DoesNotExist:
            return None
        return tier


class SubscriptionCheckoutAPI(_OrgAdminActionAPI):
    """POST /api/billing/subscription/checkout/ — Admin only. Body
    {planId}: start a gateway order for switching to `planId`. Returns the
    gateway order id, amount, currency, publishable key and local
    paymentTransactionId for the frontend checkout modal."""

    @swagger_auto_schema(
        request_body=CheckoutRequestSerializer,
        responses={200: CheckoutResponseSerializer()},
    )
    def post(self, request):
        subscription, error = self.resolve_subscription(request)
        if error:
            return error

        body = CheckoutRequestSerializer(data=request.data)
        if not body.is_valid():
            return Response(body.errors, status=status.HTTP_400_BAD_REQUEST)

        tier = self._resolve_plan(body.validated_data["planId"])
        if tier is None:
            return Response(
                {"message": "Plan not found or no longer active."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            checkout = services.initiate_checkout(subscription, tier)
        except services.BillingError as exc:
            return _error_response(exc)

        serializer = CheckoutResponseSerializer(data=checkout)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SubscriptionConfirmAPI(_OrgAdminActionAPI):
    """POST /api/billing/subscription/confirm/ — Admin only. Body
    {paymentTransactionId, gatewayPaymentId, gatewaySignature}. The
    signature is verified SERVER-SIDE (HMAC over order|payment with the API
    secret); nothing activates on a mismatch. This call is the fast-path UX
    optimization — the gateway webhook independently settles the payment
    even if this call never arrives."""

    @swagger_auto_schema(request_body=PaymentConfirmSerializer)
    def post(self, request):
        subscription, error = self.resolve_subscription(request)
        if error:
            return error

        body = PaymentConfirmSerializer(data=request.data)
        if not body.is_valid():
            return Response(body.errors, status=status.HTTP_400_BAD_REQUEST)

        # The transaction must belong to THIS org's subscription — never
        # confirm someone else's payment against our subscription.
        if not PaymentTransaction.objects.filter(
            pk=body.validated_data["paymentTransactionId"],
            subscription=subscription,
        ).exists():
            return Response(
                {"message": "Payment transaction not found for this organization."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            updated = services.verify_and_capture_payment(
                body.validated_data["paymentTransactionId"],
                body.validated_data["gatewayPaymentId"],
                body.validated_data["gatewaySignature"],
            )
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(
            SubscriptionSerializer(updated).data, status=status.HTTP_200_OK
        )


class SubscriptionAssignAPI(_OrgAdminActionAPI):
    """POST /api/billing/subscription/assign/ — Admin only. Body {planId}:
    switch the org's plan with NO payment (downgrade, internally comped
    upgrade). Clears per-org override fields exactly like the frontend's
    handleAssignPlan() so effective limits fall back to the new tier."""

    @swagger_auto_schema(request_body=PlanIdBodySerializer)
    def post(self, request):
        subscription, error = self.resolve_subscription(request)
        if error:
            return error

        body = PlanIdBodySerializer(data=request.data)
        if not body.is_valid():
            return Response(body.errors, status=status.HTTP_400_BAD_REQUEST)

        tier = self._resolve_plan(body.validated_data["planId"])
        if tier is None:
            return Response(
                {"message": "Plan not found or no longer active."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            updated = services.assign_plan(subscription, tier, request.user)
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(
            SubscriptionSerializer(updated).data, status=status.HTTP_200_OK
        )


class SubscriptionCancelAPI(_OrgAdminActionAPI):
    """POST /api/billing/subscription/cancel/ — Admin only. Cancels at the
    end of the paid period (auto-renewal off); a never-activated PENDING
    row is cancelled outright."""

    @swagger_auto_schema(responses={200: SubscriptionSerializer()})
    def post(self, request):
        subscription, error = self.resolve_subscription(request)
        if error:
            return error

        try:
            updated = services.cancel_subscription(subscription, request.user)
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(
            SubscriptionSerializer(updated).data, status=status.HTTP_200_OK
        )


class SubscriptionAutoRenewalAPI(_OrgAdminActionAPI):
    """PATCH /api/billing/subscription/auto-renewal/ — Admin only. Body
    {enabled}: turn auto-renewal on/off."""

    @swagger_auto_schema(request_body=AutoRenewalSerializer)
    def patch(self, request):
        subscription, error = self.resolve_subscription(request)
        if error:
            return error

        body = AutoRenewalSerializer(data=request.data)
        if not body.is_valid():
            return Response(body.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = services.toggle_auto_renewal(
                subscription, body.validated_data["enabled"], request.user
            )
        except services.BillingError as exc:
            return _error_response(exc)
        return Response(
            SubscriptionSerializer(updated).data, status=status.HTTP_200_OK
        )


# ===========================================================================
# Gateway webhook — NO auth; the gateway authenticates via signature header
# ===========================================================================

@method_decorator(csrf_exempt, name="dispatch")
class GatewayWebhookAPI(APIView):
    """POST /api/billing/webhooks/<gateway>/ — called directly by the
    payment gateway; deliberately has NO session/session auth and is
    csrf_exempt. Authenticity comes from the X-Razorpay-Signature header,
    verified over the RAW request body against RAZORPAY_WEBHOOK_SECRET.

    Returns 200 quickly for anything handled (gateways retry on non-2xx)
    and is idempotent: duplicate deliveries of the same payment are no-ops
    (see services.handle_gateway_webhook). A failed signature returns 400
    and never touches the ledger."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, gateway_name):
        if gateway_name != "razorpay":
            return Response(
                {"message": "Unsupported gateway."},
                status=status.HTTP_404_NOT_FOUND,
            )

        signature_header = request.META.get("HTTP_X_RAZORPAY_SIGNATURE", "")
        try:
            from . import gateway as gateway_adapter

            gateway_adapter.verify_webhook_signature(request.body, signature_header)
        except GatewaySignatureError:
            return Response(
                {"message": "Webhook signature verification failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response(
                {"message": "Invalid webhook payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = services.handle_gateway_webhook(payload)
        return Response(result, status=status.HTTP_200_OK)
