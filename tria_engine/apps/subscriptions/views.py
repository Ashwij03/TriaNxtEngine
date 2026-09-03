# tria_engine/apps/subscriptions/views.py
#
# Task 6b (backend) — Subscription & Plan Catalog API.
#
# APIView-only (no viewsets/routers, matching licensing/, organizations/,
# monitoring/), session-authenticated via the project's existing
# IsAuthenticated default (REST_FRAMEWORK SessionAuthentication in
# settings.py — no JWT), admin-only actions gated by IsAdminRole
# (permissions.py), and every error returned with the codebase-wide
# {"message": ...} key used throughout licensing/views.py.

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema

from . import services
from .models import Plan, Subscription
from .permissions import IsAdminRole
from .serializers import (
    PlanSerializer,
    SubscriptionSerializer,
    SubscriptionUsageSerializer,
)


def _subscription_for_request_user(request):
    """Fail-soft read helper shared by the /me/ and /usage/ endpoints:
    resolves the caller's Organization (via User.organization FK) to its
    Subscription row, returning None (→ 404 at the call site) when either
    is missing. Never raises."""
    organization = getattr(request.user, "organization", None)
    if organization is None:
        return None
    try:
        return services.get_subscription_for_organization(organization)
    except Subscription.DoesNotExist:
        return None


class PlanListCreateAPI(APIView):
    """GET  /api/subscriptions/plans/ — any authenticated user: the full
         plan catalog (MyLicense.js plan selector + SubscriptionManagement
         .js admin table).
    POST /api/subscriptions/plans/ — Admin only: add a catalog tier."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        plans = Plan.objects.all()
        serializer = PlanSerializer(plans, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=PlanSerializer)
    def post(self, request):
        if not IsAdminRole().has_permission(request, self):
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        serializer = PlanSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        plan = serializer.save()
        return Response(PlanSerializer(plan).data, status=status.HTTP_201_CREATED)


class PlanDetailAPI(APIView):
    """PUT    /api/subscriptions/plans/<id>/ — Admin only: edit a tier.
    DELETE /api/subscriptions/plans/<id>/ — Admin only: delete a tier
         (blocked with a clear 400 message if it's currently assigned to
         a Subscription — see services.delete_plan)."""

    permission_classes = [IsAdminRole]

    def _get_plan_or_none(self, pk):
        try:
            return Plan.objects.get(pk=pk)
        except Plan.DoesNotExist:
            return None

    @swagger_auto_schema(request_body=PlanSerializer)
    def put(self, request, pk):
        plan = self._get_plan_or_none(pk)
        if plan is None:
            return Response({"message": "Plan not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = PlanSerializer(plan, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        plan = serializer.save()
        return Response(PlanSerializer(plan).data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        plan = self._get_plan_or_none(pk)
        if plan is None:
            return Response({"message": "Plan not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            services.delete_plan(plan.pk)
        except services.SubscriptionLimitError as exc:
            return Response({"message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"message": "Plan deleted successfully"},
            status=status.HTTP_200_OK,
        )


class SubscriptionMeAPI(APIView):
    """GET /api/subscriptions/me/ — any authenticated user: the active
         plan + status + dates for the caller's Organization, mirroring
         getSubscription() in subscriptionService.js.
    PUT /api/subscriptions/me/ — Admin only: update status/dates/notes/
         auto-renewal/plan assignment for the caller's Organization."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscription = _subscription_for_request_user(request)
        if subscription is None:
            return Response(
                {"message": "No subscription found for your organization."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SubscriptionSerializer(subscription)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=SubscriptionSerializer)
    def put(self, request):
        if not IsAdminRole().has_permission(request, self):
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        subscription = _subscription_for_request_user(request)
        if subscription is None:
            return Response(
                {"message": "No subscription found for your organization."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SubscriptionSerializer(
            subscription, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        subscription = serializer.save()
        return Response(SubscriptionSerializer(subscription).data, status=status.HTTP_200_OK)


class SubscriptionUsageAPI(APIView):
    """GET /api/subscriptions/usage/ — any authenticated user: the three
    KPICard numbers (studies/users/storage used vs. limit) for the
    caller's Organization, shaped exactly like getSubscriptionUsage()'s
    return value on the frontend."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscription = _subscription_for_request_user(request)
        if subscription is None:
            return Response(
                {"message": "No subscription found for your organization."},
                status=status.HTTP_404_NOT_FOUND,
            )

        usage = services.get_usage(subscription.organization)
        serializer = SubscriptionUsageSerializer(usage)
        return Response(serializer.data, status=status.HTTP_200_OK)