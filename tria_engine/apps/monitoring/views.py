# tria_engine/apps/monitoring/views.py

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tria_engine.apps.organizations.models import Organization

from .models import MonitoringAccessRequest
from .permissions import (
    CanDecideMonitoringRequest,
    CanSubmitMonitoringRequest,
    is_monitoring_approver,
)
from .serializers import (
    MonitoringAccessDecisionSerializer,
    MonitoringAccessRequestSerializer,
)


def _role_label(user):
    role = getattr(user, "role", None)
    return role.name if role else ""


@method_decorator(csrf_exempt, name='dispatch')
class MonitoringAccessRequestListCreateAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.is_superuser or is_monitoring_approver(user):
            # Admins/Site Staff see requests aimed at their own site; a
            # superuser (no organization) sees every request.
            if user.is_superuser:
                requests_qs = MonitoringAccessRequest.objects.select_related(
                    "requested_by", "site", "approved_by"
                ).all()
            else:
                requests_qs = MonitoringAccessRequest.objects.select_related(
                    "requested_by", "site", "approved_by"
                ).filter(site_id=user.organization_id)
        else:
            # Requesters only ever see their own requests.
            requests_qs = MonitoringAccessRequest.objects.select_related(
                "requested_by", "site", "approved_by"
            ).filter(requested_by=user)

        status_filter = request.query_params.get("status")
        if status_filter:
            requests_qs = requests_qs.filter(status=status_filter)

        if not requests_qs.exists():
            return Response(
                {"message": "No monitoring access requests found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MonitoringAccessRequestSerializer(requests_qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        request_body=MonitoringAccessRequestSerializer,
        permission_classes=[CanSubmitMonitoringRequest],
    )
    def post(self, request):
        if not CanSubmitMonitoringRequest().has_permission(request, self):
            return Response(
                {"message": "Only CRA, Sponsor, or CRO users can request monitoring access."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MonitoringAccessRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        access_request = serializer.save(
            requested_by=request.user,
            requester_role_label=_role_label(request.user),
            status=MonitoringAccessRequest.STATUS_PENDING,
        )

        if not MonitoringAccessRequest.objects.filter(id=access_request.id).exists():
            return Response(
                {"message": "Monitoring access request creation validation failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            MonitoringAccessRequestSerializer(access_request).data,
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_exempt, name='dispatch')
class MonitoringAccessRequestDecisionAPI(APIView):
    permission_classes = [CanDecideMonitoringRequest]

    def _get_request_for_user(self, request, pk):
        access_request = get_object_or_404(MonitoringAccessRequest, pk=pk)
        user = request.user
        if not user.is_superuser and access_request.site_id != user.organization_id:
            return None
        return access_request

    @swagger_auto_schema(request_body=MonitoringAccessDecisionSerializer)
    def put(self, request, pk, action):
        access_request = self._get_request_for_user(request, pk)
        if access_request is None:
            return Response(
                {"message": "Monitoring access request not found for your site."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if access_request.status != MonitoringAccessRequest.STATUS_PENDING and action != "revoke":
            return Response(
                {"message": f"Request is already {access_request.status}."},
                status=status.HTTP_409_CONFLICT,
            )

        note_serializer = MonitoringAccessDecisionSerializer(data=request.data)
        note_serializer.is_valid(raise_exception=False)
        note = note_serializer.validated_data.get("note", "") if note_serializer.is_valid() else ""

        if action == "approve":
            access_request.approve(approved_by=request.user, note=note)
        elif action == "reject":
            access_request.reject(approved_by=request.user, note=note)
        elif action == "revoke":
            if access_request.status != MonitoringAccessRequest.STATUS_APPROVED:
                return Response(
                    {"message": "Only an approved request can be revoked."},
                    status=status.HTTP_409_CONFLICT,
                )
            access_request.revoke(approved_by=request.user, note=note)
        else:
            return Response(
                {"message": "Unknown action. Use approve, reject, or revoke."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            MonitoringAccessRequestSerializer(access_request).data,
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name='dispatch')
class MonitoringAccessCheckAPI(APIView):
    """Used by the frontend to decide whether to render a site's data in
    read-only ("monitoring view") mode for the current user, right now."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        site_id = request.query_params.get("site")
        if not site_id:
            return Response({"message": "site query param is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not Organization.objects.filter(id=site_id).exists():
            return Response({"message": "Site not found"}, status=status.HTTP_404_NOT_FOUND)

        has_access = MonitoringAccessRequest.has_active_view_access(
            user=request.user, site_id=site_id, on_date=timezone.localdate()
        )
        return Response(
            {"site": int(site_id), "view_only_access": has_access},
            status=status.HTTP_200_OK,
        )
