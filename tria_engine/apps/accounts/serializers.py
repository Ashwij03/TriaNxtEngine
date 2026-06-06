from rest_framework import serializers
from django.contrib.auth import get_user_model
from tria_engine.apps.organizations.models import Organization
from .models import Patient, UploadedDocument, UploadForm, AuditLog

User = get_user_model()


# API VALIDATION CHANGE: Serializer used by the service layer to validate endpoint
# availability checks for URL resolution, HTTP method, and required headers.
class EndpointAvailabilitySerializer(serializers.Serializer):
    path = serializers.CharField(required=True)
    method = serializers.CharField(required=True)
    allowed_methods = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
    )
    content_type = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    requires_auth = serializers.BooleanField(default=False)
    has_auth_header = serializers.BooleanField(default=False)
    has_session_cookie = serializers.BooleanField(default=False)
    has_authenticated_user = serializers.BooleanField(default=False)
    has_resolver_match = serializers.BooleanField(default=True)
    requires_body_header = serializers.BooleanField(default=False)
    allowed_content_types = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    def validate(self, data):
        errors = {}
        method = data["method"].upper()
        allowed_methods = [
            allowed_method.upper()
            for allowed_method in data["allowed_methods"]
        ]

        if method not in allowed_methods:
            errors["method"] = (
                f"{method} method is not allowed for this endpoint. "
                f"Allowed methods: {', '.join(allowed_methods)}."
            )

        if not data["has_resolver_match"]:
            errors["url"] = "Requested URL does not match an available API endpoint."

        if data["requires_auth"] and not (
            data["has_auth_header"] or
            data["has_session_cookie"] or
            data["has_authenticated_user"]
        ):
            errors["headers"] = (
                "Authorization header or active session cookie is required "
                "for this endpoint."
            )

        content_type = (data.get("content_type") or "").lower()
        allowed_content_types = [
            allowed_type.lower()
            for allowed_type in data.get("allowed_content_types", [])
        ]

        if data["requires_body_header"]:
            if not content_type:
                errors["content_type"] = (
                    "Content-Type header is required for this endpoint."
                )
            elif allowed_content_types and not any(
                content_type.startswith(allowed_type)
                for allowed_type in allowed_content_types
            ):
                errors["content_type"] = (
                    "Invalid Content-Type header. Allowed values: "
                    f"{', '.join(allowed_content_types)}."
                )

        if errors:
            raise serializers.ValidationError(errors)

        return data


class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "organization", "role"]


class RegisterSerializer(serializers.ModelSerializer):
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        # FIX: Removed "password2" from fields — it was never declared as a serializer
        # field and does not exist on the User model, causing a runtime error on every
        # registration request. "confirm_password" (declared above) already covers the
        # confirmation check, so "password2" was a stale duplicate.
        fields = [
            "username",
            "email",
            "password",
            "confirm_password",
            "first_name",
            "last_name",
            "organization",
            "role",
        ]
        extra_kwargs = {
            "password": {"write_only": True}
        }

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate(self, data):
        if data["password"] != data["confirm_password"]:
            raise serializers.ValidationError("Passwords do not match")


        role = data.get("role")
        org = data.get("organization")

        if role and org and role.organization != org:
            raise serializers.ValidationError(
                "Role does not belong to selected organization"
            )

        return data

    def create(self, validated_data):
        from .services import create_user
    
        validated_data.pop("confirm_password")
        return create_user(validated_data)
    

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True)
    
    # def validate(self, data):
    #     if not data.get("email") and not data.get("username"):
    #         raise serializers.ValidationError("Either email or username is required")
    #     return data
    
    
class LoginMFASerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True)


class VerifyLoginOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp_code = serializers.CharField(max_length=6, required=True)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

# FIX: Replaced `user_id` (IntegerField) with `email` (EmailField). The `ResetPasswordAPI`
# view now passes `email` (obtained from the forgot-password response) to
# `reset_password_user()`, which looks up the token via the user's email. This removes
# the need for the caller to track an internal user_id and makes the API consistent
# with every other password-related endpoint that identifies users by email.
class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp_code = serializers.CharField(max_length=6, required=True)
    new_password = serializers.CharField(write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    # username = serializers.CharField(required=False)
    current_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError("New passwords do not match")
        
        if data["current_password"] == data["new_password"]:
            raise serializers.ValidationError("New password must be different from current password")
        
        return data
    

class PatientSerializer(serializers.ModelSerializer):
    site = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
    first_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    date_of_birth = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    medical_record_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Patient
        fields = [
            "id", "patient_id", "status", "site",
            "first_name", "last_name", "date_of_birth",
            "email", "phone", "address", "medical_record_number",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "patient_id", "created_at", "updated_at"]

    def validate_site(self, value):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser:
            if not user.organization_id or value.id != user.organization_id:
                raise serializers.ValidationError("You can only assign patients to your own organization")
        return value

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        pii = instance.get_pii()
        ret["first_name"] = pii.get("first_name")
        ret["last_name"] = pii.get("last_name")
        ret["date_of_birth"] = pii.get("date_of_birth")
        ret["email"] = pii.get("email")
        ret["phone"] = pii.get("phone")
        ret["address"] = pii.get("address")
        ret["medical_record_number"] = pii.get("medical_record_number")
        return ret

    def create(self, validated_data):
        first_name = validated_data.pop("first_name", None)
        last_name = validated_data.pop("last_name", None)
        date_of_birth = validated_data.pop("date_of_birth", None)
        email = validated_data.pop("email", None)
        phone = validated_data.pop("phone", None)
        address = validated_data.pop("address", None)
        medical_record_number = validated_data.pop("medical_record_number", None)

        patient = Patient.objects.create(**validated_data)
        patient.set_pii(
            first_name=first_name,
            last_name=last_name,
            dob=date_of_birth,
            email=email,
            phone=phone,
            address=address,
            mrn=medical_record_number,
        )
        patient.save()
        return patient

    def update(self, instance, validated_data):
        first_name = validated_data.pop("first_name", None)
        last_name = validated_data.pop("last_name", None)
        date_of_birth = validated_data.pop("date_of_birth", None)
        email = validated_data.pop("email", None)
        phone = validated_data.pop("phone", None)
        address = validated_data.pop("address", None)
        medical_record_number = validated_data.pop("medical_record_number", None)

        instance.status = validated_data.get("status", instance.status)
        instance.site = validated_data.get("site", instance.site)

        instance.set_pii(
            first_name=first_name,
            last_name=last_name,
            dob=date_of_birth,
            email=email,
            phone=phone,
            address=address,
            mrn=medical_record_number,
        )
        instance.save()
        return instance
    
    
class DocumentUploadSerializer(serializers.Serializer):

    user_id = serializers.IntegerField()

    uploaded_by = serializers.EmailField(
        required=True
    )

    file = serializers.FileField()

    category = serializers.ChoiceField(
        choices=UploadedDocument.CATEGORY_CHOICES,
        required=False,
        default="general",
    )


class UploadedDocumentSerializer(serializers.ModelSerializer):
    organization = serializers.SerializerMethodField()
    uploaded_by = serializers.CharField(source="uploaded_by.email", read_only=True)

    class Meta:
        model = UploadedDocument
        fields = [
            "document_number",
            "original_name",
            "content_type",
            "file_size",
            "category",
            "organization",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_organization(self, obj):
        if obj.organization:
            return obj.organization.name
        return None


class UploadFormSerializer(serializers.ModelSerializer):

    user_id = serializers.IntegerField(
        required=True
    )

    class Meta:

        model = UploadForm

        fields = [
            "user_id",
            "form_type",
            "file",
        ]


class DeleteUploadFormSerializer(
    serializers.Serializer
):

    user_id = serializers.IntegerField(
        required=True
    )

    form_id = serializers.IntegerField(
        required=True
    )


class ViewUploadFormSerializer(
    serializers.Serializer
):

    form_id = serializers.IntegerField(
        required=True
    )


class IntegritySerializer(serializers.Serializer):
    message = serializers.CharField()


class ProfilePhotoUploadSerializer(serializers.Serializer):
    photo = serializers.FileField()
    email = serializers.EmailField(required=True)
    user_id = serializers.IntegerField()

    def validate_photo(self, value):
        import os

        ext = os.path.splitext(value.name)[1].lower()
        allowed_extensions = [".png", ".jpg", ".jpeg"]

        if ext not in allowed_extensions:
            raise serializers.ValidationError(
                "Only PNG, JPG, and JPEG image files are allowed. PDF and DOC files are not supported."
            )

        return value
    

class AuditLogSerializer(serializers.ModelSerializer):

    user = serializers.SerializerMethodField()

    timestamp = serializers.SerializerMethodField()

    signature_token = serializers.SerializerMethodField()

    class Meta:

        model = AuditLog

        fields = [
            "id",
            "user",
            "action",
            "ip_address",
            "description",
            "timestamp",
            "signature_token",
            "signature_meaning",
        ]

        read_only_fields = fields

    def get_user(self, obj):

        if obj.user:

            return obj.user.username

        return "Unknown User"

    def get_timestamp(self, obj):

        return obj.created_at.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def get_signature_token(self, obj):

        return str(
            obj.signature_token
        )
