# tria_engine/urls.py

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
    openapi.Info(
        title="Tria Engine API",
        default_version="v1",
        description="API documentation for Tria Engine",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/accounts/", include("tria_engine.apps.accounts.urls")),
    re_path(r"^swagger(?P<format>\.json|\.yaml)$", schema_view.without_ui(cache_timeout=0), name="schema-json"),
    path("swagger/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
    path("redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
    # FIX: Swagger UI's "Django Login"/"Logout" buttons assume /accounts/login/
    # and /accounts/logout/ exist (Django's session-auth convention). This app
    # is JWT/API-based and has neither, so redirect both to the admin's
    # equivalent pages — they're the only session-login pages already wired up
    # with working templates.
    path("accounts/login/", RedirectView.as_view(pattern_name="admin:login", permanent=False)),
    path("accounts/logout/", RedirectView.as_view(pattern_name="admin:logout", permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)