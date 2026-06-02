# tria_engine/apps/organizations/serializers.py

from rest_framework import serializers
from .models import Organization, Role

class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name"]

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "organization"]