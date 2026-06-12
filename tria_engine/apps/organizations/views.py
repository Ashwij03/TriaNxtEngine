from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from .models import Organization, Role
from .serializers import OrganizationSerializer, RoleSerializer


class OrganizationListCreateAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.is_superuser:
            organizations = Organization.objects.all()
        else:
            organizations = Organization.objects.filter(id=request.user.organization_id)
        serializer = OrganizationSerializer(organizations, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=OrganizationSerializer)
    def post(self, request):
        if not request.user.is_superuser:
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        serializer = OrganizationSerializer(data=request.data)
        # if serializer.is_valid():
        #     serializer.save()
        #     return Response(serializer.data, status=status.HTTP_201_CREATED)
        if serializer.is_valid():

            organization = serializer.save()

            # =====================================================
            # DATABASE VALIDATION CHANGE:
            # Verify organization inserted correctly.
            # =====================================================

            if not Organization.objects.filter(
                id=organization.id
            ).exists():

                return Response(
                    {
                        "message": "Organization creation validation failed"
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            return Response(
                OrganizationSerializer(
                    organization
                ).data,
                status=status.HTTP_201_CREATED
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

# role list and create API view 
class RoleListCreateAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.is_superuser:
            roles = Role.objects.select_related("organization").all()
        else:
            roles = Role.objects.select_related("organization").filter(organization=request.user.organization)
        serializer = RoleSerializer(roles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=RoleSerializer)
    def post(self, request):
        if not request.user.is_superuser:
            return Response({"message": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        serializer = RoleSerializer(data=request.data)
        # if serializer.is_valid():
        #     serializer.save()
        #     return Response(serializer.data, status=status.HTTP_201_CREATED)
        if serializer.is_valid():
            role = serializer.save()
            

           # =====================================================
           # DATABASE VALIDATION CHANGE:
           # Verify role inserted correctly.
           # =====================================================
            if not Role.objects.filter(
                id=role.id
            ).exists():
                return Response(
                   {
                       "message": "Role creation validation failed"
                   },
                   status=status.HTTP_500_INTERNAL_SERVER_ERROR
               )
            return Response(
                RoleSerializer(
                    role
                ).data,
                status=status.HTTP_201_CREATED
            ) 
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)