# apps/organizations/models.py

from django.db import models
from django.conf import settings


class Organization(models.Model):
    name = models.CharField(max_length=255)
    
    def __str__(self):
        return self.name


class Role(models.Model):
    name = models.CharField(max_length=100)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="roles"
    )
    

    def __str__(self):
        return f"{self.name} ({self.organization.name})"