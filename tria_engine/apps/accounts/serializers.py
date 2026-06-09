from rest_framework import serializers
from django.contrib.auth import get_user_model
from tria_engine.apps.organizations.models import Organization
from .models import  UploadedDocument, UploadForm, AuditLog

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
    # API VALIDATION CHANGE: Method behavior fields validate GET, POST, PUT,
    # PATCH, and DELETE request behavior from the server side.
    supported_behavior_methods = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    method_requires_body = serializers.BooleanField(default=False)
    has_request_body = serializers.BooleanField(default=False)
    method_allows_body = serializers.BooleanField(default=True)
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
        supported_behavior_methods = [
            supported_method.upper()
            for supported_method in data.get(
                "supported_behavior_methods",
                ["GET", "POST", "PUT", "PATCH", "DELETE"],
            )
        ]

        # API VALIDATION CHANGE: Block HTTP verbs outside the documented API
        # behavior set while still allowing infrastructure OPTIONS handling.
        if method not in supported_behavior_methods and method != "OPTIONS":
            errors["method_behavior"] = (
                f"{method} behavior is not supported. Supported API "
                "methods: GET, POST, PUT, PATCH, DELETE."
            )

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

        # API VALIDATION CHANGE: Validate request body behavior by method.
        # POST/PUT/PATCH endpoints that expect input must receive a body, while
        # GET and DELETE endpoints should use URL/query parameters instead.
        if data["method_requires_body"] and not data["has_request_body"]:
            errors["body"] = f"{method} request body is required for this endpoint."

        if not data["method_allows_body"] and data["has_request_body"]:
            errors["body"] = (
                f"{method} should not include a request body. Use URL or query "
                "parameters for this endpoint."
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


# API VALIDATION CHANGE: Shared request-schema helper for required fields,
# optional fields, and DRF data types used in validation error responses.
class RequestSchemaValidationMixin:
    def get_request_schema(self):
        required_fields = []
        optional_fields = []
        data_types = {}

        for field_name, field in self.fields.items():
            if field.read_only:
                continue

            data_types[field_name] = field.__class__.__name__

            if field.required and field.default is serializers.empty:
                required_fields.append(field_name)
            else:
                optional_fields.append(field_name)

        return {
            "required_fields": required_fields,
            "optional_fields": optional_fields,
            "data_types": data_types,
        }

    def validate_request_schema(self, data):
        errors = {}

        for field_name in self.get_request_schema()["required_fields"]:
            value = data.get(field_name)
            if value in [None, ""]:
                errors[field_name] = f"{field_name} is required"

        if errors:
            raise serializers.ValidationError(errors)

        return data
    
# API VALIDATION CHANGE:
# Pagination request validation
class PaginationSerializer(
    RequestSchemaValidationMixin,
    serializers.Serializer
):
    page_number = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1
    )

    page_size = serializers.IntegerField(
        required=False,
        default=10,
        min_value=1,
        max_value=100
    )

    def validate(self, data):
        self.validate_request_schema(data)
        return data 

# API VALIDATION CHANGE:
# Filtering validation
# Validates allowed filter fields and values.

class FilterSerializer(
    RequestSchemaValidationMixin,
    serializers.Serializer
):
    filter_by = serializers.CharField(
        required=False,
        allow_blank=True
    )

    filter_value = serializers.CharField(
        required=False,
        allow_blank=True
    )

    ALLOWED_FILTERS = [
        "document_number",
        "original_name",
        "content_type",
        "category"
    ]

    def validate(self, data):
        self.validate_request_schema(data)

        filter_by = data.get("filter_by")
        filter_value = data.get("filter_value")

        # API VALIDATION CHANGE:
        # Validate filter field
        if filter_by and filter_by not in self.ALLOWED_FILTERS:
            raise serializers.ValidationError({
                "filter_by": (
                    f"Invalid filter field. "
                    f"Allowed values: {', '.join(self.ALLOWED_FILTERS)}"
                )
            })

        # API VALIDATION CHANGE:
        # Empty filter value check
        if filter_by and not filter_value:
            raise serializers.ValidationError({
                "filter_value":
                "Filter value is required when filter_by is provided."
            })

        return data  


class AnyValueField(serializers.Field):
    def to_internal_value(self, data):
        return data

    def to_representation(self, value):
        return value


# API VALIDATION CHANGE: Serializer for validating API response schema
# structure, expected fields, and response data types.
class ResponseSchemaValidationSerializer(serializers.Serializer):
    response_data = AnyValueField(required=True)
    expected_schema = serializers.DictField(required=False)

    def _type_matches(self, value, expected_type):
        if expected_type == "any":
            return True
        if expected_type == "null":
            return value is None
        if expected_type == "str":
            return isinstance(value, str)
        if expected_type == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "bool":
            return isinstance(value, bool)
        if expected_type == "dict":
            return isinstance(value, dict)
        if expected_type == "list":
            return isinstance(value, list)
        return False

    def _validate_schema(self, value, schema, path="response"):
        errors = {}
        expected_type = schema.get("type", "any")

        if not self._type_matches(value, expected_type):
            errors[path] = (
                f"Expected {expected_type}, received "
                f"{type(value).__name__}."
            )
            return errors

        if expected_type == "dict":
            fields = schema.get("fields", {})
            required_fields = schema.get("required_fields", [])

            for field_name in required_fields:
                if field_name not in value:
                    errors[f"{path}.{field_name}"] = "Missing response field."

            for field_name, field_schema in fields.items():
                if field_name in value:
                    errors.update(
                        self._validate_schema(
                            value[field_name],
                            field_schema,
                            f"{path}.{field_name}",
                        )
                    )

        if expected_type == "list":
            item_schema = schema.get("item_schema")
            if item_schema:
                for index, item in enumerate(value):
                    errors.update(
                        self._validate_schema(
                            item,
                            item_schema,
                            f"{path}[{index}]",
                        )
                    )

        return errors

    def validate(self, data):
        expected_schema = data.get("expected_schema")
        if not expected_schema:
            return data

        errors = self._validate_schema(
            data["response_data"],
            expected_schema,
        )

        if errors:
            raise serializers.ValidationError(errors)

        return data


class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "organization", "role"]


class RegisterSerializer(RequestSchemaValidationMixin, serializers.ModelSerializer):
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

    username = serializers.CharField(
        min_length=3,
        max_length=30
    )

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        max_length=50
    )

    confirm_password = serializers.CharField(
        write_only=True,
        min_length=8,
        max_length=50
    )

    first_name = serializers.CharField(
        required=True,
        min_length=2,
        max_length=50
    )

    last_name = serializers.CharField(
        required=True,
        min_length=2,
        max_length=50
    )
    organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.all(),
        required=True
    )
    role = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=True
    )

    # API VALIDATION CHANGE:
    # Duplicate email detected.
    # API should return HTTP 409 Conflict.
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate(self, data):
        # API VALIDATION CHANGE: Validate request schema before business rules.
        self.validate_request_schema(data)

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
    

class LoginSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    email = serializers.EmailField(
    max_length=100
)

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        max_length=50
    )
    
    
class LoginMFASerializer(RequestSchemaValidationMixin, serializers.Serializer):
    email = serializers.EmailField(
    max_length=100
)

    password = serializers.CharField(
        min_length=8,
        max_length=50,
        write_only=True
    )


class VerifyLoginOTPSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp_code = serializers.CharField(
        max_length=6,
        required=True
    )
    def validate_otp_code(self, value):

        if not value.isdigit():
            raise serializers.ValidationError(
                "OTP must contain only numbers"
            )

        otp_int = int(value)

        if otp_int < 100000 or otp_int > 999999:
            raise serializers.ValidationError(
                "OTP must be between 100000 and 999999"
            )

        return value


class ForgotPasswordSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    email = serializers.EmailField()

# FIX: Replaced `user_id` (IntegerField) with `email` (EmailField). The `ResetPasswordAPI`
# view now passes `email` (obtained from the forgot-password response) to
# `reset_password_user()`, which looks up the token via the user's email. This removes
# the need for the caller to track an internal user_id and makes the API consistent
# with every other password-related endpoint that identifies users by email.
class ResetPasswordSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    otp_code = serializers.CharField(
    min_length=6,
    max_length=6
)

    new_password = serializers.CharField(
        min_length=8,
        max_length=50,
        write_only=True
    )

    def validate(self, data):
            # API VALIDATION CHANGE: Validate actual reset-password schema fields.
            self.validate_request_schema(data)
            return data
    
    def validate_otp_code(self, value):

        if not value.isdigit():
            raise serializers.ValidationError(
                "OTP must contain only numbers"
            )

        otp_int = int(value)

        if otp_int < 100000 or otp_int > 999999:
            raise serializers.ValidationError(
                "OTP must be between 100000 and 999999"
            )

        return value


class ChangePasswordSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    email = serializers.EmailField(required=True)
    # username = serializers.CharField(required=False)
    current_password = serializers.CharField(
    min_length=8,
    max_length=50,
    write_only=True
)

new_password = serializers.CharField(
    min_length=8,
    max_length=50,
    write_only=True
)

confirm_password = serializers.CharField(
    min_length=8,
    max_length=50,
    write_only=True
)
def validate(self, data):
        # API VALIDATION CHANGE: Validate request schema before password rules.
        self.validate_request_schema(data)

        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError(
                "New passwords do not match"
            )

        if data["current_password"] == data["new_password"]:
            raise serializers.ValidationError(
                "New password must be different from current password"
            )

        return data

class DocumentUploadSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    
    def validate(self, data):
        # API VALIDATION CHANGE: Validate required upload fields and data types.
        self.validate_request_schema(data)
        return data

    user_id = serializers.IntegerField(
        min_value=1,
        max_value=99999999
    )

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


class UploadFormSerializer(RequestSchemaValidationMixin, serializers.ModelSerializer):

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

    def validate(self, data):
        # API VALIDATION CHANGE: Validate only fields declared by UploadFormSerializer.
        self.validate_request_schema(data)
        return data


class DeleteUploadFormSerializer(
    RequestSchemaValidationMixin,
    serializers.Serializer
):

    user_id = serializers.IntegerField(
    min_value=1,
    max_value=99999999
)

    form_id = serializers.IntegerField(
    min_value=1,
    max_value=99999999
)


class ViewUploadFormSerializer(
    RequestSchemaValidationMixin,
    serializers.Serializer
):

    form_id = serializers.IntegerField(
    min_value=1,
    max_value=99999999
)


class IntegritySerializer(RequestSchemaValidationMixin, serializers.Serializer):
    message = serializers.CharField()


class ProfilePhotoUploadSerializer(RequestSchemaValidationMixin, serializers.Serializer):
    photo = serializers.FileField()
    email = serializers.EmailField(required=True, max_length=100)
    
    user_id = serializers.IntegerField(min_value=1,
         max_value=99999999)

    def validate(self, data):
        # API VALIDATION CHANGE: Validate profile-photo request schema.
        self.validate_request_schema(data)
        return data

    def validate_photo(self, value):
        import os
        ext = os.path.splitext(value.name)[1].lower()
        allowed_extensions = [".png", ".jpg", ".jpeg"]

        if ext not in allowed_extensions:
            raise serializers.ValidationError(
                "Only PNG, JPG, and JPEG image files are allowed. PDF, DOC and TXT files are not supported."
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
