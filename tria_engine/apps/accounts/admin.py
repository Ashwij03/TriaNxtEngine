# apps/accounts/admin.py

from django.contrib import admin
from .models import User, PasswordResetToken, LoginOTP, Patient, MedicalRecord, UploadedDocument, UploadForm


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "username", "email", "organization", "role", "is_active", "is_superuser", "created_at")
    search_fields = ("username", "email", "first_name", "last_name")
    list_filter = ("is_active", "is_superuser", "organization", "role")
    ordering = ("-id",)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "otp_code", "is_used", "expires_at", "created_at")
    search_fields = ("user__email", "otp_code")
    list_filter = ("is_used", "created_at", "expires_at")
    ordering = ("-id",)


@admin.register(LoginOTP)
class LoginOTPAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "otp_code", "is_used", "expires_at", "created_at")
    search_fields = ("user__email", "otp_code")
    list_filter = ("is_used", "created_at", "expires_at")
    ordering = ("-id",)


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("id", "patient_id", "status", "site", "created_at", "updated_at")
    search_fields = ("patient_id",)
    list_filter = ("status", "site", "created_at")
    ordering = ("-id",)


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "patient", "created_at", "updated_at")
    search_fields = ("patient__patient_id", "diagnosis")
    list_filter = ("created_at", "updated_at")
    ordering = ("-id",)


@admin.register(UploadedDocument)
class UploadedDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_name",
        "category",
        "organization",
        "uploaded_by",
        "file_size",
        "created_at",
    )
    search_fields = ("original_name", "uploaded_by__email", "organization__name")
    list_filter = ("category", "organization", "created_at")
    ordering = ("-id",)
    readonly_fields = ("original_name", "content_type", "file_size", "created_at", "updated_at")
    

@admin.register(UploadForm)
class UploadFormAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "user",
        "form_type",
        "file_name",
        "created_at",
    )

    search_fields = (
        "user__username",
        "form_type",
        "file_name",
    )

    list_filter = (
        "form_type",
        "created_at",
    )

    readonly_fields = (
        "created_at",
    )