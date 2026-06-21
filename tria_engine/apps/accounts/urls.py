# accounts/urls.py

from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from .views import (
    UserListAPI,
    RegisterAPI,
    LoginAPI,
    LoginMFAAPI,
    VerifyLoginOTPAPI,
    ForgotPasswordAPI,
    ResetPasswordAPI,
    ChangePasswordAPI,
    CompromisedTokenReportAPI,
    DocumentListAPI,
    DocumentUploadAPI,
    DocumentDownloadAPI,
    DocumentDeleteAPI,
    ProfilePhotoUploadAPI,
    ProfilePhotoViewAPI,
    ProfilePhotoDeleteAPI,
    CheckSessionAPI,
    IntegrityCheckAPI,
    UploadFormAPI,
    DeleteUploadFormAPI,
    ViewUploadFormAPI,
    AuditLogsAPI
)

urlpatterns = [
    
    path(
        "users/",
        UserListAPI.as_view(),
        name="user-list-api"
    ),


    path(
        "register/",
        RegisterAPI.as_view(),
        name="register-api"
    ),

    path(
        "login/",
        LoginAPI.as_view(),
        name="login-api"
    ),
    
    path(
        "login-mfa/",
        LoginMFAAPI.as_view(),
        name="login-mfa-api"
    ),

   
    path(
        "verify-otp/",
        VerifyLoginOTPAPI.as_view(),
        name="verify-otp-api"
    ),


    path(
        "forgot-password/",
        ForgotPasswordAPI.as_view(),
        name="forgot-password-api"
    ),


    path(
        "reset-password/",
        ResetPasswordAPI.as_view(),
        name="reset-password-api"
    ),

    path(
        "change-password/",
        ChangePasswordAPI.as_view(),
        name="change-password-api"
    ),

    path(
        "report-compromised-token/",
        CompromisedTokenReportAPI.as_view(),
        name="report-compromised-token-api"
    ),
    path(
        "documents/",
        DocumentListAPI.as_view(),
        name="document-list-api"
    ),

    path(
        "documents/upload/",
        DocumentUploadAPI.as_view(),
        name="document-upload-api"
    ),

    path(
        "documents/download/",
        DocumentDownloadAPI.as_view(),
        name="document-download-api"
    ),

    path(
        "documents/delete/",
        DocumentDeleteAPI.as_view(),
        name="document-delete-api"
    ),


    path(
        "profile-photo/upload/",
        ProfilePhotoUploadAPI.as_view(),
        name="profile-photo-upload-api"
    ),

    path(
        "profile-photo/view/",
        ProfilePhotoViewAPI.as_view(),
        name="profile-photo-view-api"
    ),

    path(
        "profile-photo/delete/",
        ProfilePhotoDeleteAPI.as_view(),
        name="profile-photo-delete-api"
    ),
    
    path(
        "check-session/",
        CheckSessionAPI.as_view(),
        name="check-session-api"
    ),

    path(
        "integrity-check/",
        IntegrityCheckAPI.as_view(),
        name="integrity-check-api"
    ),

    path(
        "upload-form/",
        UploadFormAPI.as_view(),
        name="upload-form-api"
    ),


    path(
        "upload-form/delete/",
        DeleteUploadFormAPI.as_view(),
        name="delete-upload-form"
    ),
    

    path(
        "upload-form/view/",
        ViewUploadFormAPI.as_view(),
        name="view-upload-form"
    ),

    path(
        "audit-logs/",
        AuditLogsAPI.as_view(),
        name="audit-logs-api"
    ),

]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)