# accounts/views.py

from datetime import timedelta
from django.contrib.auth import login
from django.http import FileResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView as DRFAPIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import MethodNotAllowed, ValidationError

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .serializers import (
    UserListSerializer, RegisterSerializer, LoginSerializer, LoginMFASerializer,VerifyLoginOTPSerializer, ForgotPasswordSerializer, ResetPasswordSerializer, ChangePasswordSerializer,
    IntegritySerializer,
    DocumentUploadSerializer, UploadedDocumentSerializer,
    ProfilePhotoUploadSerializer, UploadFormSerializer, DeleteUploadFormSerializer, ViewUploadFormSerializer, AuditLogSerializer
)
from .services import (
    login_user, login_user_with_mfa, verify_login_otp, forgot_password_user,
    reset_password_user, change_password_user, upload_document, get_document_by_number, get_audit_logs_service, delete_document_by_number, upload_profile_photo, get_profile_photo, delete_profile_photo, report_compromised_token, create_audit_log, get_all_users_service, integrity_check_service, upload_form_service, delete_uploaded_form_service, get_uploaded_form_service, validate_api_endpoint_availability, validate_api_request_schema
)
from .models import User, UploadedDocument
from .audit import log_audit_event

# User = get_user_model()

OTP_STORAGE = {}
otp_storage = {}
otp_expiry = {}


# API VALIDATION CHANGE: All account APIs inherit this local APIView wrapper.
# It validates endpoint availability for correct method and required headers
# before the existing API logic runs.
class APIView(DRFAPIView):
    # API VALIDATION CHANGE: Default HTTP method behavior for account APIs.
    # POST/PUT/PATCH require a body; GET/DELETE should normally not send one.
    body_required_methods = {"POST", "PUT", "PATCH"}
    body_forbidden_methods = {"GET", "DELETE"}

    def initial(self, request, *args, **kwargs):
        allowed_content_types = [
            "application/json",
            "multipart/form-data",
            "application/x-www-form-urlencoded",
        ]

        if (
            "parser_classes" in self.__class__.__dict__ and
            MultiPartParser in self.parser_classes
        ):
            allowed_content_types = [
                "multipart/form-data",
                "application/x-www-form-urlencoded",
            ]

        requires_auth = not any(
            permission_class is AllowAny
            for permission_class in self.permission_classes
        )

        validation_errors, status_code = validate_api_endpoint_availability(
            request=request,
            allowed_methods=self.allowed_methods,
            requires_auth=requires_auth,
            allowed_content_types=allowed_content_types,
            body_required_methods=self.body_required_methods,
            body_forbidden_methods=self.body_forbidden_methods,
        )

        if validation_errors:
            if status_code == 405:
                raise MethodNotAllowed(
                    request.method,
                    detail=validation_errors.get("method"),
                )

            raise ValidationError(
                {
                    "message": "API validation failed",
                    "errors": validation_errors,
                }
            )

        return super().initial(request, *args, **kwargs)


class RegisterAPI(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(request_body=RegisterSerializer)
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        # API VALIDATION CHANGE: Return request-schema details on invalid input.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:
            return Response(schema_error, status=status_code)

        user = serializer.save()
        create_audit_log(
            user=user,
            action="REGISTER",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{user.username} registered into system",
            signature_meaning="User electronically signed registration"
        )
        log_audit_event("user_registered", user=user, request=request)
        return Response({"message": "User registered", "user_id": user.id}, status=201)


class UserListAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: UserListSerializer(many=True)})
    def get(self, request):
        if request.user.is_superuser:
            users = get_all_users_service()
        else:
            users = User.objects.filter(organization=request.user.organization)
            
        create_audit_log(
            user=request.user,
            action="VIEW_USERS",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} viewed users list",
            signature_meaning="User electronically signed for viewing users"
        )
        
        return Response(UserListSerializer(users, many=True).data)



class LoginAPI(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(request_body=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        # API VALIDATION CHANGE: Validate required fields, optional fields,
        # and data types before login processing.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:
            return Response(schema_error, status=status_code)

        user, error = login_user(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
            request=request
        )
        
        if user is not None:

            create_audit_log(
                    user=user,
                    action="LOGIN",
                    ip_address=request.META.get('REMOTE_ADDR'),
                    description=f"{user.username} logged into system",
                    signature_meaning="User electronically signed for login"
                )
        else:
            create_audit_log(
               user=None,
               action="FAILED_LOGIN",
               ip_address=request.META.get('REMOTE_ADDR'),
               description=f"Failed login attempt for {serializer.validated_data['email']}",
               signature_meaning="Failed electronic signature attempt"
            )

            return Response({
                   "message": "Invalid Credentials"
            }, status=401)

        return Response(
            {
                "message": "Login successful",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "is_active": user.is_active,
                }
            },
            status=200
        )

class CheckSessionAPI(APIView):

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "username",
                openapi.IN_QUERY,
                description="Username",
                type=openapi.TYPE_STRING,
                required=True,
            )
        ]
    )
    def get(self, request):

        username = request.GET.get(
            "username"
        )

        if not username:

            return Response(
                {
                    "message": (
                        "username is required"
                    )
                },
                status=400
            )

        try:

            user = User.objects.get(
                username=username
            )

            if not user.last_activity:

                return Response(
                    {
                        "message": (
                            "No active session found"
                        )
                    },
                    status=401
                )

            inactive_time = (
                timezone.now() -
                user.last_activity
            )

            if inactive_time > timedelta(minutes=5):

                create_audit_log(
                    user=user,
                    action="SESSION_EXPIRED",
                    ip_address=request.META.get('REMOTE_ADDR'),
                    description=f"{user.username} session expired due to inactivity",
                    signature_meaning="Automatic session timeout recorded"
                )
                
                return Response(
                    {
                        "message": (
                            "Session expired. "
                            "Auto logoff successful."
                        )
                    },
                    status=401
                )

            user.last_activity = timezone.now()

            user.save(
                update_fields=["last_activity"]
            )

            create_audit_log(
                user=user,
                action="SESSION_ACTIVE",
                ip_address=request.META.get('REMOTE_ADDR'),
                description=f"{user.username} session checked and active",
                signature_meaning="Session activity verified"
            )

            return Response(
                {
                    "message": "Session active",

                    "last_activity": (
                        user.last_activity
                    ),
                },
                status=200
            )

        except User.DoesNotExist:

            return Response(
                {
                    "message": "User not found"
                },
                status=404
            )


class LoginMFAAPI(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(request_body=LoginMFASerializer)
    def post(self, request):
        serializer = LoginMFASerializer(data=request.data)
        # API VALIDATION CHANGE: Validate request schema before MFA login.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:
            create_audit_log(
                user=None,
                action="FAILED_MFA_LOGIN",
                ip_address=request.META.get('REMOTE_ADDR'),
                description="Failed MFA login attempt",
                signature_meaning="Failed MFA electronic signature"
            )
            return Response(schema_error, status=status_code)

        result, error = login_user_with_mfa(**serializer.validated_data, request=request)
        if error:
            create_audit_log(
                user=None,
                action="FAILED_MFA_LOGIN",
                ip_address=request.META.get('REMOTE_ADDR'),
                description="Failed MFA login attempt",
                signature_meaning="Failed MFA electronic signature"
            )
            return Response({"message": error}, status=401)

        create_audit_log(
            user=request.user if request.user.is_authenticated else None,
            action="MFA_LOGIN_INITIATED",
            ip_address=request.META.get('REMOTE_ADDR'),
            description="MFA login initiated",
            signature_meaning="MFA authentication initiated"
        )
        
        return Response(result, status=200)


class VerifyLoginOTPAPI(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(request_body=VerifyLoginOTPSerializer)
    def post(self, request):
        serializer = VerifyLoginOTPSerializer(data=request.data)
        # API VALIDATION CHANGE: Validate request schema before OTP verification.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:
            return Response(schema_error, status=status_code)

        user, error = verify_login_otp(
            email=serializer.validated_data["email"],
            otp_code=serializer.validated_data["otp_code"],
            request=request,
        )
        if error:
            create_audit_log(
                user=None,
                action="FAILED_OTP_VERIFICATION",
                ip_address=request.META.get('REMOTE_ADDR'),
                description="Invalid OTP verification attempt",
                signature_meaning="Failed OTP verification signature"
            )
            return Response({"message": error}, status=400)

        if user is None:
            return Response({"message": "Invalid user"}, status=400)

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        
        create_audit_log(
            user=user,
            action="MFA_VERIFIED",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{user.username} verified MFA OTP successfully",
            signature_meaning="User electronically signed MFA verification"
        )
        return Response({"message": "MFA login successful", "user_id": user.id}, status=200)


class ForgotPasswordAPI(APIView):

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        request_body=ForgotPasswordSerializer
    )
    def post(self, request):

        serializer = ForgotPasswordSerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate forgot-password request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        result, error = forgot_password_user(
            email=serializer.validated_data["email"],
            request=request,
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=404
            )

        user = User.objects.filter(email=serializer.validated_data["email"]).first()

        if user:

            create_audit_log(
                user=user,
                action="FORGOT_PASSWORD",
                ip_address=request.META.get("REMOTE_ADDR"),
                description=(
                    f"{user.username} requested forgot password"
                ),
                signature_meaning=(
                    "User electronically signed forgot password request"
                )
            )

        return Response(
            {
                "message": "Password reset instructions sent successfully",
                "data": result,
            },
            status=200
        )


class ResetPasswordAPI(APIView):

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        request_body=ResetPasswordSerializer
    )
    def post(self, request):

        serializer = ResetPasswordSerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate reset-password request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        user, error = reset_password_user(
            email=serializer.validated_data["email"],
            otp_code=serializer.validated_data["otp_code"],
            new_password=serializer.validated_data["new_password"],
            request=request,
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=400
            )

        if user:

            create_audit_log(
                user=user,
                action="RESET_PASSWORD",
                ip_address=request.META.get("REMOTE_ADDR"),
                description="User password reset successfully",
                signature_meaning=(
                    "User electronically signed for password reset"
                )
            )

        return Response(
            {
                "message": "Password reset successful"
            },
            status=200
        )


class ChangePasswordAPI(APIView):

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=ChangePasswordSerializer
    )
    def post(self, request):

        serializer = ChangePasswordSerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate change-password request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        try:

            email = serializer.validated_data["email"]

            user = User.objects.get(email=email)

            if (
                request.user != user and
                not request.user.is_superuser
            ):

                return Response(
                    {
                        "message": "You don't have permission"
                    },
                    status=403
                )

            result, error = change_password_user(
                user=user,
                current_password=serializer.validated_data[
                    "current_password"
                ],
                new_password=serializer.validated_data[
                    "new_password"
                ],
                request=request,
            )

            if error:

                create_audit_log(
                    user=user,
                    action="FAILED_PASSWORD_CHANGE",
                    ip_address=request.META.get(
                        "REMOTE_ADDR"
                    ),
                    description=(
                        f"Failed password change attempt "
                        f"for {user.username}"
                    ),
                    signature_meaning=(
                        "Failed electronic signature "
                        "for password change"
                    )
                )

                return Response(
                    {
                        "message": error
                    },
                    status=400
                )

            create_audit_log(
                user=user,
                action="PASSWORD_CHANGE",
                ip_address=request.META.get(
                    "REMOTE_ADDR"
                ),
                description=(
                    f"{user.username} changed password"
                ),
                signature_meaning=(
                    "User electronically signed "
                    "for password change"
                )
            )

            return Response(
                {
                    "message": "Password changed successfully",
                    "data": result,
                },
                status=200
            )

        except User.DoesNotExist:

            return Response(
                {
                    "message": (
                        "User not found with this email"
                    )
                },
                status=400
            )

        except Exception as e:

            return Response(
                {
                    "message": f"Error: {str(e)}"
                },
                status=500
            )


class CompromisedTokenReportAPI(APIView):
    permission_classes = [IsAuthenticated]
    # API VALIDATION CHANGE: This POST endpoint receives token_type in the query
    # string, so it does not require a request body.
    body_required_methods = set()

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "token_type",
                openapi.IN_QUERY,
                description="Compromised token type",
                type=openapi.TYPE_STRING,
                required=True,
                enum=["login_otp", "password_reset"],
            )
        ]
    )
    def post(self, request):
        token_type = request.query_params.get("token_type")
        if not token_type:
            return Response({"message": "token_type is required"}, status=400)

        result, error = report_compromised_token(
            user=request.user,
            token_type=token_type,
            request=request,
        )
        if error:
            return Response({"message": error}, status=400)

        create_audit_log(
            user=request.user,
            action="COMPROMISED_TOKEN_REPORTED",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} reported compromised token",
            signature_meaning="User electronically signed compromised token report"
        )
        
        return Response(result, status=200)


class IntegrityCheckAPI(APIView):

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=IntegritySerializer
    )
    def post(self, request):

        serializer = IntegritySerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate integrity-check request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        response = integrity_check_service(
            serializer.validated_data[
                "message"
            ]
        )

        log_audit_event(
            "integrity_check_completed",
            user=request.user,
            request=request,
            status="success",
        )

        create_audit_log(
            user=request.user,
            action="INTEGRITY_CHECK",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} performed integrity check",
            signature_meaning="Integrity verification electronically signed"
        )
        
        return Response(
            response,
            status=200
        )
class DocumentListAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: UploadedDocumentSerializer(many=True)})
    def get(self, request):
        if request.user.is_superuser:
            documents = UploadedDocument.objects.select_related(
                "organization",
                "uploaded_by",
            ).all().order_by("document_number")
        else:
            documents = UploadedDocument.objects.select_related(
                "organization",
                "uploaded_by",
            ).filter(
                organization=request.user.organization
            ).order_by("document_number")
            
        create_audit_log(
            user=request.user,
            action="VIEW_DOCUMENTS",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} viewed document list",
            signature_meaning="Document viewing electronically signed"
        )
        
        return Response(UploadedDocumentSerializer(documents, many=True).data, status=200)


class DocumentUploadAPI(APIView):

    permission_classes = [IsAuthenticated]

    parser_classes = [
        MultiPartParser,
        FormParser,
    ]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "user_id",
                openapi.IN_FORM,
                description="User ID",
                type=openapi.TYPE_INTEGER,
                required=True,
            ),
            openapi.Parameter(
                "uploaded_by",
                openapi.IN_FORM,
                description="Email of the user uploading the document",
                type=openapi.TYPE_STRING,
                required=True,
            ),
            openapi.Parameter(
                "file",
                openapi.IN_FORM,
                description="Document file",
                type=openapi.TYPE_FILE,
                required=True,
            ),
            openapi.Parameter(
                "category",
                openapi.IN_FORM,
                description="Document category",
                type=openapi.TYPE_STRING,
                required=False,
                enum=[
                    choice[0]
                    for choice in (
                        UploadedDocument
                        .CATEGORY_CHOICES
                    )
                ],
            ),
        ],
        responses={
            201: UploadedDocumentSerializer
        },
    )
    def post(self, request):

        serializer = DocumentUploadSerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate document-upload request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        user_id = serializer.validated_data[
            "user_id"
        ]
        

        uploaded_file = serializer.validated_data[
            "file"
        ]

        category = serializer.validated_data.get(
            "category",
            "general"
        )

        document, error = upload_document(
            user_id=user_id,
            uploaded_file=uploaded_file,
            uploaded_by=request.user,
            category=category,
            organization=request.user.organization,
            request=request,
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=400
            )
        
        create_audit_log(
            user=request.user,
            action="UPLOAD_DOCUMENT",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} uploaded document {document.document_number}",
            signature_meaning="Document upload electronically signed"
        )

        return Response(
            {
                "message": (
                    "Document uploaded "
                    "successfully"
                ),
                "data": (
                    UploadedDocumentSerializer(
                        document
                    ).data
                ),
            },
            status=201,
        )
# class for document download and deletion based on document number with proper permissions and audit logging

class DocumentDownloadAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "document_number",
                openapi.IN_QUERY,
                description="Document number",
                type=openapi.TYPE_INTEGER,
                required=True,
            )
        ]
    )
    def get(self, request):
        document_number = request.query_params.get("document_number")
        if not document_number:
            return Response({"message": "document_number is required"}, status=400)

        document, error = get_document_by_number(
            document_number=document_number,
            user=request.user,
            request=request,
        )
        if error:
            return Response({"message": error}, status=404)

        return FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_name,
        )


class DocumentDeleteAPI(APIView):

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="User ID",
                type=openapi.TYPE_INTEGER,
                required=True,
            ),
            
            openapi.Parameter(
                "document_number",
                openapi.IN_QUERY,
                description="Document number",
                type=openapi.TYPE_INTEGER,
                required=True,
            )
        ]
    )
    def delete(self, request):

        user_id = request.query_params.get(
            "user_id"
        )

        document_number = request.query_params.get(
            "document_number"
        )

        if not user_id:

            return Response(
                {
                    "message": (
                        "user_id is required"
                    )
                },
                status=400
            )

        if not document_number:

            return Response(
                {
                    "message": (
                        "document_number is required"
                    )
                },
                status=400
            )

        result, error = delete_document_by_number(
            user_id=user_id,
            document_number=document_number,
            user=request.user,
            request=request,
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=404
            )
        
        create_audit_log(
            user=request.user,
            action="DELETE_DOCUMENT",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} deleted document {document_number}",
            signature_meaning="Document deletion electronically signed"
        )

        return Response(
            result,
            status=200
        )


class ProfilePhotoUploadAPI(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "photo",
                openapi.IN_FORM,
                description="Profile photo file",
                type=openapi.TYPE_FILE,
                required=True,
            ),
            
            openapi.Parameter(
                "email",
                openapi.IN_FORM,
                description="User's email",
                type=openapi.TYPE_STRING,
                required=True,
            ),
            
            openapi.Parameter(
                "user_id",
                openapi.IN_FORM,
                description="User ID",
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
        ]
    )
    def post(self, request):

        serializer = ProfilePhotoUploadSerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate profile-photo request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        uploaded_file = serializer.validated_data[
            "photo"
        ]

        user, error = upload_profile_photo(
            user=request.user,
            uploaded_file=uploaded_file,
            request=request,
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=400
            )
        
        create_audit_log(
            user=request.user,
            action="PROFILE_PHOTO_UPLOAD",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} uploaded profile photo",
            signature_meaning="Profile photo upload electronically signed"
        )

        return Response(
            {
                "message": (
                    "Profile photo uploaded successfully"
                ),
                "profile_photo": (
                    user.profile_photo.url
                    if user.profile_photo
                    else None
                ),
            },
            status=200,
        )


class ProfilePhotoViewAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        file, error = get_profile_photo(
            user=request.user
        )
    
        if error:
        
            return Response(
                {
                    "message": error
                },
                status=404
            )
        
        create_audit_log(
            user=request.user,
            action="VIEW_PROFILE_PHOTO",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} viewed profile photo",
            signature_meaning="Profile photo viewed electronically"
        )
    
        return Response(
            {
                "name": (
                    file["name"]
                ),

                "url": (
                    file["url"]
                ),
            },
            status=200,
        )


class ProfilePhotoDeleteAPI(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        result, error = delete_profile_photo(
            user=request.user,
            request=request,
        )
        if error:
            return Response({"message": error}, status=404)
        
        create_audit_log(
            user=request.user,
            action="DELETE_PROFILE_PHOTO",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} deleted profile photo",
            signature_meaning="Profile photo deletion electronically signed"
        )

        return Response(result, status=200)


class UploadFormAPI(APIView):

    permission_classes = [IsAuthenticated]

    parser_classes = [
        MultiPartParser,
        FormParser,
    ]

    @swagger_auto_schema(
        request_body=UploadFormSerializer
    )
    def post(self, request):

        serializer = UploadFormSerializer(
            data=request.data
        )

        # API VALIDATION CHANGE: Validate upload-form request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        user_id = serializer.validated_data[
            "user_id"
        ]

        form_type = serializer.validated_data[
            "form_type"
        ]

        uploaded_file = serializer.validated_data[
            "file"
        ]

        form = upload_form_service(
            user_id,
            request.user,
            uploaded_file,
            form_type
        )
        
        create_audit_log(
            user=request.user,
            action="UPLOAD_FORM",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} uploaded {form_type} form",
            signature_meaning="Form upload electronically signed"
        )

        return Response(
            {
                "message": (
                    f"{form_type} uploaded successfully"
                ),
                "form_id": form.id,
                "file": form.file.url,
            },
            status=201
        )


class DeleteUploadFormAPI(APIView):

    permission_classes = [IsAuthenticated]
    # API VALIDATION CHANGE: This DELETE endpoint uses request.data because the
    # existing serializer validates user_id and form_id from the request body.
    body_required_methods = {"DELETE"}
    body_forbidden_methods = {"GET"}

    @swagger_auto_schema(
        request_body=DeleteUploadFormSerializer
    )
    def delete(self, request):

        serializer = (
            DeleteUploadFormSerializer(
                data=request.data
            )
        )

        # API VALIDATION CHANGE: Validate delete-upload-form request schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        user_id = serializer.validated_data[
            "user_id"
        ]

        form_id = serializer.validated_data[
            "form_id"
        ]

        result, error = (
            delete_uploaded_form_service(
                user_id,
                form_id,
                request.user,
                request,
            )
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=400
            )

        create_audit_log(
            user=request.user,
            action="FORM_DELETE",
            ip_address=request.META.get(
                'REMOTE_ADDR'
            ),
            description=(
                f"{request.user.username} "
                f"deleted uploaded form"
            ),
            signature_meaning=(
                "Form deletion "
                "electronically signed"
            )
        )

        return Response(
            result,
            status=200
        )

class ViewUploadFormAPI(APIView):

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "form_id",
                openapi.IN_QUERY,
                description="Form ID",
                type=openapi.TYPE_INTEGER,
                required=True,
            )
        ]
    )
    def get(self, request):

        serializer = ViewUploadFormSerializer(
            data=request.GET
        )

        # API VALIDATION CHANGE: Validate view-upload-form query schema.
        schema_error, status_code = validate_api_request_schema(serializer)
        if schema_error:

            return Response(
                schema_error,
                status=status_code
            )

        form_id = serializer.validated_data[
            "form_id"
        ]

        result, error = (
            get_uploaded_form_service(
                form_id,
                request.user,
                request,
            )
        )

        if error:

            return Response(
                {
                    "message": error
                },
                status=404
            )

        create_audit_log(
            user=request.user,
            action="FORM_VIEW",
            ip_address=request.META.get(
                'REMOTE_ADDR'
            ),
            description=(
                f"{request.user.username} "
                f"viewed uploaded form"
            ),
            signature_meaning=(
                "Form viewing "
                "electronically signed"
            )
        )

        return Response(
            result,
            status=200
        )


class AuditLogsAPI(APIView):

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: AuditLogSerializer(
                many=True
            )
        }
    )
    def get(self, request):

        logs = get_audit_logs_service()

        create_audit_log(
            user=request.user,
            action="LOGIN",
            ip_address=request.META.get(
                "REMOTE_ADDR"
            ),
            description=(
                "Viewed audit logs"
            ),
            signature_meaning=(
                "Electronic signature recorded"
            )
        )

        serializer = AuditLogSerializer(
            logs,
            many=True
        )
        
        create_audit_log(
            user=request.user,
            action="VIEW_AUDIT_LOGS",
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f"{request.user.username} viewed audit logs",
            signature_meaning="Audit logs viewed electronically"
        )

        return Response(
            serializer.data,
            status=200
        )
