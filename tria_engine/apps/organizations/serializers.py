# tria_engine/apps/organizations/serializers.py

from rest_framework import serializers
from .models import Organization, Role

class OrganizationSerializer(serializers.ModelSerializer):
    # =====================================================
    # DATABASE VALIDATION CHANGE:
    # Unique Constraint Validation
    # Prevent duplicate organization names.
    # =====================================================
    def validate_name(self, value):

        if Organization.objects.filter(
            name__iexact=value
        ).exclude(
            pk=self.instance.pk if self.instance else None
        ).exists():

            raise serializers.ValidationError(
                "Organization with this name already exists."
            )

        return value
    
    class Meta:
        model = Organization
        fields = ["id", "name"]
        
        
class RoleSerializer(serializers.ModelSerializer):

    # =====================================================
    # DATABASE VALIDATION CHANGE:
    # Referential Integrity Validation
    # Verify referenced organization exists.
    # =====================================================
    def validate_organization(self, value):

        if not Organization.objects.filter(
            id=value.id
        ).exists():

            raise serializers.ValidationError(
                "Referenced organization does not exist."
            )

        return value

    # =====================================================
    # DATABASE VALIDATION CHANGE:
    # Unique Constraint Validation
    # Prevent duplicate role names
    # within the same organization.
    # =====================================================
    def validate(self, attrs):
        
        organization = attrs.get("organization")
        
        if not organization and self.instance:
            organization = self.instance.organization
        
        role_name = attrs.get("name")
        
        if not role_name and self.instance:
            role_name = self.instance.name
        

        # organization = attrs.get(
        #     "organization",
        #     self.instance.organization if self.instance else None
        # )

        # role_name = attrs.get(
        #     "name",
        #     self.instance.name if self.instance else None
        # )

        if Role.objects.filter(
            organization=organization,
            name__iexact=role_name
        ).exclude(
            pk=self.instance.pk if self.instance else None
        ).exists():

            raise serializers.ValidationError(
                {
                    "name":
                    "Role already exists in this organization."
                }
            )

        return attrs

    class Meta:
        model = Role
        fields = ["id", "name", "organization"]       

