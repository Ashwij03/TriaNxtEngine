import secrets
# import random
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.password_validation import password_changed, validate_password
from django.utils import timezone

from .audit import log_audit_event
from .models import PasswordResetToken, LoginOTP, UploadedDocument, AuditLog, UploadForm, UploadLog
from .upload_config import (
    get_document_max_size,
    get_document_allowed_extensions,
    get_profile_photo_max_size,
    get_profile_photo_allowed_extensions,
)
from .file_validators import (
    validate_file_size, validate_file_extension,
    validate_document, validate_profile_photo, validate_form_file
)

User = get_user_model()


# API VALIDATION CHANGE: Central endpoint availability validation for correct
# method and headers. URL correctness is confirmed by Django before a view is
# reached; this service validates the resolved endpoint request details.
def validate_api_endpoint_availability(
    *,
    request,
    allowed_methods,
    requires_auth=False,
    allowed_content_types=None,
    body_required_methods=None,
    body_forbidden_methods=None,
):
    from django.conf import settings
    from rest_framework.exceptions import ValidationError

    from .serializers import EndpointAvailabilitySerializer

    method = request.method.upper()
    allowed_methods = [
        allowed_method.upper()
        for allowed_method in allowed_methods
    ]
    # API VALIDATION CHANGE: Server-side HTTP method behavior rules for
    # GET, POST, PUT, PATCH, and DELETE.
    default_body_required_methods = {"POST", "PUT", "PATCH"}
    if body_required_methods is None:
        body_required_methods = default_body_required_methods

    body_required_methods = {
        method_name.upper()
        for method_name in body_required_methods
    }

    default_body_forbidden_methods = {"GET", "DELETE"}
    if body_forbidden_methods is None:
        body_forbidden_methods = default_body_forbidden_methods

    body_forbidden_methods = {
        method_name.upper()
        for method_name in body_forbidden_methods
    }
    content_length = request.META.get("CONTENT_LENGTH")
    has_request_body = bool(
        content_length and
        content_length != "0"
    )
    method_requires_body = method in body_required_methods
    method_allows_body = method not in body_forbidden_methods
    requires_auth_header = requires_auth and method != "OPTIONS"
    session_cookie_name = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
    request_user = getattr(request, "user", None)

    serializer = EndpointAvailabilitySerializer(
        data={
            "path": request.path,
            "method": method,
            "allowed_methods": allowed_methods,
            "supported_behavior_methods": [
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            ],
            "method_requires_body": method_requires_body,
            "has_request_body": has_request_body,
            "method_allows_body": method_allows_body,
            "content_type": getattr(request, "content_type", None),
            "requires_auth": requires_auth_header,
            "has_auth_header": bool(
                request.META.get("HTTP_AUTHORIZATION")
            ),
            "has_session_cookie": bool(
                request.COOKIES.get(session_cookie_name)
            ),
            "has_authenticated_user": bool(
                getattr(request_user, "is_authenticated", False)
            ),
            "has_resolver_match": bool(
                getattr(request, "resolver_match", None)
            ),
            "requires_body_header": method_requires_body,
            "allowed_content_types": allowed_content_types or [
                "application/json",
                "multipart/form-data",
                "application/x-www-form-urlencoded",
            ],
        }
    )

    try:
        serializer.is_valid(raise_exception=True)
    except ValidationError:
        status_code = 405 if method not in allowed_methods else 400
        return serializer.errors, status_code

    return None, None


# API VALIDATION CHANGE: Shared request-schema validation response for required
# fields, optional fields, and serializer data types.
def validate_api_request_schema(serializer):
    if serializer.is_valid():
        return None, None

    request_schema = {}
    if hasattr(serializer, "get_request_schema"):
        request_schema = serializer.get_request_schema()

    return {
        "message": "Request schema validation failed",
        "request_schema": request_schema,
        "errors": serializer.errors,
    }, 400


def _security_setting(name, default):
    security = getattr(settings, "TRIA_SECURITY", {})
    return security.get(name, default)


def _token_expires_in_minutes():
    return _security_setting("TOKEN_EXPIRY_MINUTES", 5)


def _password_max_age_days():
    return _security_setting("PASSWORD_MAX_AGE_DAYS", 90)


def _expose_otp_in_response():
    return _security_setting("EXPOSE_OTP_IN_RESPONSE", True)


def _generate_otp_code():
    return f"{secrets.randbelow(900000) + 100000}"


def _cleanup_active_login_otps(user):
    LoginOTP.objects.filter(
        user=user,
        is_used=False,
        expires_at__gt=timezone.now(),
    ).update(is_used=True)


def _cleanup_active_reset_tokens(user):
    PasswordResetToken.objects.filter(
        user=user,
        is_used=False,
        expires_at__gt=timezone.now(),
    ).update(is_used=True)


def _mark_all_unused_reset_tokens_used(user, exclude_id=None):
    qs = PasswordResetToken.objects.filter(user=user, is_used=False)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    qs.update(is_used=True)


def _mark_all_unused_login_otps_used(user, exclude_id=None):
    qs = LoginOTP.objects.filter(user=user, is_used=False)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    qs.update(is_used=True)


def _set_password_metadata(user, *, must_change_password=False):
    if hasattr(user, "password_changed_at"):
        user.password_changed_at = timezone.now()

    if hasattr(user, "must_change_password"):
        user.must_change_password = must_change_password


def _is_password_expired(user):
    if not hasattr(user, "password_changed_at"):
        return False

    changed_at = getattr(user, "password_changed_at", None)
    if not changed_at:
        return True

    return changed_at <= timezone.now() - timedelta(days=_password_max_age_days())


def _build_login_response(user, otp):
    data = {
        "mfa_required": True,
        "message": "OTP generated successfully",
        "email": user.email,
        "expires_at": otp.expires_at,
    }
    if _expose_otp_in_response():
        data["otp_code"] = otp.otp_code
    return data


# =========================================================
# User / Auth Services
# =========================================================

# FIX: Removed the unused extra parameters `username`, `email`, `password` from the
# function signature. The serializer calls this as create_user(validated_data) with a
# single dict argument, so those extra positional params caused a TypeError on every
# registration attempt. The uniqueness checks inside now correctly read from
# validated_data instead of the (now-removed) bare local variables.
def create_user(validated_data):

    if User.objects.filter(email=validated_data["email"]).exists():
        return {"error": "A user with this email already exists."}

    if User.objects.filter(username=validated_data["username"]).exists():
        return {"error": "This username is already taken."}

    user = User(
        username=validated_data["username"],
        email=validated_data["email"],
        first_name=validated_data.get("first_name", ""),
        last_name=validated_data.get("last_name", ""),
        organization=validated_data.get("organization"),
        role=validated_data.get("role"),
    )

    validate_password(validated_data["password"], user=user)
    user.set_password(validated_data["password"])
    _set_password_metadata(user)
    user.save()
    password_changed(validated_data["password"], user=user)
    return user


def get_all_users_service():
    return User.objects.all()


def login_user(email, password, request=None):
    user = authenticate(request=request, username=email, password=password)
    if user is None:
        log_audit_event("login_failed", request=request, status="failed", details={"email": email})
        return None, "Invalid credentials"

    if not user.is_active:
        log_audit_event("login_inactive_user", user=user, request=request, status="failed")
        return None, "User account is inactive"

    if getattr(user, "must_change_password", False):
        log_audit_event(
            "login_blocked_password_change_required",
            user=user,
            request=request,
            status="failed",
        )
        return None, "Password reset required due to a security event. Please reset your password."

    return user, None


def login_user_with_mfa(email, password, request=None):
    user, error = login_user(email=email, password=password, request=request)
    if error:
        return None, error

    _cleanup_active_login_otps(user)

    otp = LoginOTP.objects.create(
        user=user,
        otp_code=_generate_otp_code(),
        expires_at=timezone.now() + timedelta(minutes=_token_expires_in_minutes()),
    )

    log_audit_event(
        "otp_generated",
        user=user,
        request=request,
        details={"expires_at": str(otp.expires_at)},
    )

    return _build_login_response(user, otp), None


def verify_login_otp(email, otp_code, request=None):
    try:
        user = User.objects.select_related("organization", "role").get(email=email)
    except User.DoesNotExist:
        log_audit_event("otp_verify_failed", request=request, status="failed", details={"email": email})
        return None, "Invalid email"

    try:
        otp = LoginOTP.objects.get(user=user, otp_code=otp_code, is_used=False)
    except LoginOTP.DoesNotExist:
        log_audit_event("otp_verify_failed", user=user, request=request, status="failed")
        return None, "Invalid OTP"

    if otp.is_expired():
        otp.is_used = True
        otp.save(update_fields=["is_used"])
        log_audit_event("otp_expired", user=user, request=request, status="failed")
        return None, "OTP has expired"

    otp.is_used = True
    otp.save(update_fields=["is_used"])

    _mark_all_unused_login_otps_used(user, exclude_id=otp.id)
    log_audit_event("otp_verified", user=user, request=request)
    return user, None


# =========================================================
# Password Services
# =========================================================

def forgot_password_user(email, request=None):
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        log_audit_event(
            "forgot_password_requested_unknown_email",
            request=request,
            details={"email": email},
        )
        return None, "If an account with this email exists, a reset OTP has been generated."

    _cleanup_active_reset_tokens(user)

    reset_otp = PasswordResetToken.objects.create(
        user=user,
        otp_code=_generate_otp_code(),
        expires_at=timezone.now() + timedelta(minutes=_token_expires_in_minutes()),
    )

    log_audit_event("password_reset_otp_generated", user=user, request=request)

    result = {
        "message": "If an account with this email exists, a reset OTP has been generated.",
        "email": user.email,
        "expires_at": reset_otp.expires_at,
    }
    if _expose_otp_in_response():
        result["reset_otp"] = reset_otp.otp_code

    return result, None


def reset_password_user(email, otp_code, new_password, request=None):
    try:
        reset_otp = PasswordResetToken.objects.select_related("user").get(
            user__email=email,
            otp_code=otp_code,
            is_used=False,
        )
    except PasswordResetToken.DoesNotExist:
        log_audit_event(
            "password_reset_failed",
            request=request,
            status="failed",
            details={"email": email},
        )
        return None, "Invalid reset OTP"

    if reset_otp.is_expired():
        reset_otp.is_used = True
        reset_otp.save(update_fields=["is_used"])
        log_audit_event("password_reset_expired", user=reset_otp.user, request=request, status="failed")
        return None, "Reset OTP has expired"

    user = reset_otp.user
    validate_password(new_password, user=user)
    user.set_password(new_password)
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    _set_password_metadata(user)
    user.save()
    password_changed(new_password, user=user)

    reset_otp.is_used = True
    reset_otp.save(update_fields=["is_used"])

    _mark_all_unused_reset_tokens_used(user, exclude_id=reset_otp.id)
    _mark_all_unused_login_otps_used(user)
    log_audit_event("password_reset_success", user=user, request=request)
    return user, None


def change_password_user(user, current_password, new_password, request=None):
    if not user.check_password(current_password):
        if request:
            log_audit_event("change_password_failed", user=user, request=request, status="failed")
        return None, "Current password is incorrect"

    if current_password == new_password:
        return None, "New password must be different from current password"

    validate_password(new_password, user=user)

    user.set_password(new_password)
    user.must_change_password = False
    user.password_changed_at = timezone.now()
    _set_password_metadata(user)
    user.save()
    password_changed(new_password, user=user)

    _mark_all_unused_reset_tokens_used(user)
    _mark_all_unused_login_otps_used(user)

    if request:
        log_audit_event("change_password_success", user=user, request=request)

    return {"message": "Password changed successfully"}, None


def report_compromised_token(user, token_type, request=None):
    if token_type == "login_otp":
        LoginOTP.objects.filter(user=user, is_used=False).update(is_used=True)
    elif token_type == "password_reset":
        PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)

    user.must_change_password = True
    user.save(update_fields=["updated_at"])

    log_audit_event(
        "compromised_token_reported",
        user=user,
        request=request,
        status="warning",
        details={"token_type": token_type},
    )

    return {
        "message": "Compromised token handled successfully",
        "token_type": token_type,
        "action": "active tokens revoked and password change required",
    }, None


# =========================================================
# Integrity / Audit Services
# =========================================================


def create_audit_log(
    user,
    action,
    ip_address=None,
    description=None,
    signature_token=None,
    signature_meaning=None
):

    audit_log = AuditLog.objects.create(

        user=user,

        action=action,

        ip_address=ip_address,

        description=(
            description
            if description
            else "Audit log recorded"
        ),

        signature_meaning=(
            signature_meaning
            if signature_meaning
            else (
                "Electronic signature recorded"
            )
        ),
    )

    return audit_log


def get_audit_logs_service():

    logs = AuditLog.objects.all().order_by(
        "-created_at"
    )

    return logs


def integrity_check_service(message):

    hash_value = hashlib.sha256(
        message.encode()
    ).hexdigest()

    return {
        "message": (
            "Integrity check completed successfully"
        ),

        "original_message": message,

        "sha256_hash": hash_value,
    }


# =========================================================
# Document Services
# =========================================================


def upload_document(*, user_id, uploaded_file, uploaded_by, category="general", organization=None, request=None):

    validate_file_size(
        uploaded_file,
        get_document_max_size()
    )

    validate_file_extension(
        uploaded_file,
        get_document_allowed_extensions()
    )

    validate_document(
        uploaded_file
    )

    final_organization = uploaded_by.organization

    if organization is not None:

        final_organization = organization

    if (
        not final_organization and
        not uploaded_by.is_superuser
    ):

        return None, (
            "Organization is required "
            "for document upload"
        )

    document = UploadedDocument.objects.create(
        uploaded_by=uploaded_by,
        organization=final_organization,
        file=uploaded_file,
        original_name=uploaded_file.name,
        content_type=getattr(
            uploaded_file,
            "content_type",
            None
        ),
        file_size=uploaded_file.size,
        category=category,
    )

    log_audit_event(
        "document_uploaded",
        user=uploaded_by,
        request=request,
        details={
            "user_id": user_id,
            "document_number": (
                document.document_number
            ),
            "file_name": (
                document.original_name
            ),
        },
    )

    return document, None



def get_document_by_number(*, document_number, user, request=None):
    try:
        document = UploadedDocument.objects.select_related("organization", "uploaded_by").get(
            document_number=document_number
        )
    except UploadedDocument.DoesNotExist:
        log_audit_event(
            "document_access_failed",
            user=user,
            request=request,
            status="failed",
            details={"document_number": document_number},
        )
        return None, "Document not found"

    if not user.is_superuser and document.organization_id != user.organization_id:
        log_audit_event(
            "document_access_forbidden",
            user=user,
            request=request,
            status="failed",
            details={"document_number": document_number},
        )
        return None, "You do not have permission to access this document"

    return document, None


def delete_document_by_number(*, user_id, document_number, user, request=None):

    try:

        document = (
            UploadedDocument.objects
            .select_related(
                "organization",
                "uploaded_by"
            )
            .get(
                document_number=document_number
            )
        )

    except UploadedDocument.DoesNotExist:

        log_audit_event(
            "document_delete_failed",
            user=user,
            request=request,
            status="failed",
            details={
                "user_id": user_id,
                "document_number": (
                    document_number
                ),
                "file_name": (
                    document.original_name
                ),
            },
        )

        return None, "Document not found"

    if (
        not user.is_superuser and
        document.organization_id !=
        user.organization_id
    ):

        log_audit_event(
            "document_delete_forbidden",
            user=user,
            request=request,
            status="failed",
            details={
                "user_id": user_id,
                "document_number": (
                    document_number
                ),
                "file_name": (
                    document.original_name
                ),
            },
        )

        return None, (
            "You do not have permission "
            "to delete this document"
        )

    file_name = document.original_name

    document.delete()

    log_audit_event(
        "document_deleted",
        user=user,
        request=request,
        details={
            "user_id": user_id,
            "document_number": (
                document_number
            ),
            "file_name": file_name,
        },
    )

    return {
        "message": (
            "Document deleted successfully"
        )
    }, None


# =========================================================
# Profile Photo Services
# =========================================================


def upload_profile_photo(*, user, uploaded_file, request=None):
    validate_file_size(uploaded_file, get_profile_photo_max_size())
    validate_file_extension(uploaded_file, get_profile_photo_allowed_extensions())
    validate_profile_photo(uploaded_file)

    if user.profile_photo:
        user.profile_photo.delete(save=False)

    user.profile_photo = uploaded_file
    user.save(update_fields=["profile_photo", "updated_at"])

    log_audit_event(
        "profile_photo_uploaded",
        user=user,
        request=request,
        details={"file_name": uploaded_file.name},
    )
    return user, None


def get_profile_photo(*, user, request=None):
    if not user.profile_photo:
        log_audit_event(
            "profile_photo_view_failed",
            user=user,
            request=request,
            status="failed",
        )
        return None, "Profile photo not found"

    log_audit_event("profile_photo_viewed", user=user, request=request)
    return {
        "name": user.profile_photo.name.split("/")[-1],
        "url": request.build_absolute_uri(user.profile_photo.url) if request else user.profile_photo.url,
    }, None


def delete_profile_photo(*, user, request=None):
    if not user.profile_photo:
        return None, "Profile photo not found"

    old_file_name = user.profile_photo.name
    user.profile_photo.delete(save=False)
    user.profile_photo = None
    user.save(update_fields=["profile_photo", "updated_at"])

    log_audit_event(
        "profile_photo_deleted",
        user=user,
        request=request,
        details={"file_name": old_file_name},
    )
    return {"message": "Profile photo deleted successfully"}, None


# =========================================================
# Upload Form Services
# =========================================================

def upload_form_service(
    user_id,
    user,
    uploaded_file,
    form_type
):

    validate_form_file(
        uploaded_file
    )

    form = UploadForm.objects.create(
        user=user,
        form_type=form_type,
        file=uploaded_file,
    )

    UploadLog.objects.create(
        user=user,
        file_name=uploaded_file.name,
        action="FORM_UPLOAD",
    )

    log_audit_event(
        "form_uploaded",
        user=user,
        status="success",
        details={
            "user_id": user_id,
            "form_type": form_type,
            "file": uploaded_file.name,
        }
    )

    return form


def delete_uploaded_form_service(
    user_id,
    form_id,
    user,
    request=None
):

    try:

        form = UploadForm.objects.get(
            id=form_id
        )

    except UploadForm.DoesNotExist:

        log_audit_event(
            "form_delete_failed",
            user=user,
            request=request,
            status="failed",
            details={
                "user_id": user_id,
                "form_id": form_id,
            }
        )

        return None, "Form not found"

    if form.user != user and not user.is_superuser:

        log_audit_event(
            "form_delete_forbidden",
            user=user,
            request=request,
            status="failed",
            details={
                "user_id": user_id,
                "form_id": form_id,
            }
        )

        return (
            None,
            "You do not have permission "
            "to delete this form"
        )

    form_type = form.form_type

    file_name = form.file_name

    form.delete()

    UploadLog.objects.create(
        user=user,
        action="FORM_DELETE",
    )

    log_audit_event(
        "form_deleted",
        user=user,
        request=request,
        status="success",
        details={
            "user_id": user_id,
            "form_id": form_id,
            "form_type": form_type,
            "file_name": file_name,
        }
    )

    return {
        "message": (
            f"{form_type} deleted successfully"
        )
    }, None
    

def get_uploaded_form_service(
    form_id,
    user,
    request=None
):

    try:

        form = UploadForm.objects.get(
            id=form_id
        )

    except UploadForm.DoesNotExist:

        log_audit_event(
            "form_view_failed",
            user=user,
            request=request,
            status="failed",
            details={
                "form_id": form_id,
            }
        )

        return None, "Form not found"

    if form.user != user and not user.is_superuser:

        log_audit_event(
            "form_view_forbidden",
            user=user,
            request=request,
            status="failed",
            details={
                "form_id": form_id,
            }
        )

        return (
            None,
            "You do not have permission "
            "to view this form"
        )

    log_audit_event(
        "form_viewed",
        user=user,
        request=request,
        status="success",
        details={
            "form_id": form_id,
            "form_type": form.form_type,
            "file_name": form.file_name,
        }
    )

    return {
        "form_id": form.id,
        "form_type": form.form_type,
        "file_name": form.file_name,
        "file_url": (
            request.build_absolute_uri(
                form.file.url
            )
            if request
            else form.file.url
        ),
        "uploaded_at": (
            form.created_at.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        ),
    }, None
