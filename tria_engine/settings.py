# settings.py

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

ENV = os.environ.get("DJANGO_ENV", "development")
DEBUG = ENV == "development"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if ENV == "development":
        SECRET_KEY = "dev-only-not-for-production-change-me-immediately"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")

DEBUG = True

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

CTMS_ENCRYPTION_KEY = os.environ.get("CTMS_ENCRYPTION_KEY")
if not CTMS_ENCRYPTION_KEY:
    if ENV != "development":
        raise ImproperlyConfigured("CTMS_ENCRYPTION_KEY is required")

AUTH_USER_MODEL = "accounts.User"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    'corsheaders',
    "drf_yasg",
    "tria_engine.apps.accounts.apps.AccountsConfig",
    "tria_engine.apps.organizations.apps.OrganizationsConfig",
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
]

ROOT_URLCONF = "tria_engine.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "tria_engine.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    # "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    # "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTHENTICATION_BACKENDS = [
    "tria_engine.apps.accounts.authentication.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

TRIA_SECURITY = {
    "PASSWORD_MAX_AGE_DAYS": int(os.environ.get("TRIA_PASSWORD_MAX_AGE_DAYS", 90)),
    "TOKEN_EXPIRY_MINUTES": int(os.environ.get("TRIA_TOKEN_EXPIRY_MINUTES", 10)),
    "EXPOSE_OTP_IN_RESPONSE": os.environ.get("TRIA_EXPOSE_OTP_IN_RESPONSE", "true").lower() == "true",
}



TRIA_UPLOADS = {
    "DOCUMENT_MAX_SIZE": int(os.environ.get("TRIA_DOCUMENT_MAX_SIZE", 10 * 1024 * 1024)),
    "DOCUMENT_ALLOWED_EXTENSIONS": os.environ.get(
        "TRIA_DOCUMENT_ALLOWED_EXTENSIONS",
        "pdf,doc,docx,xls,xlsx,txt"
    ).split(","),
    "PROFILE_PHOTO_MAX_SIZE": int(os.environ.get("TRIA_PROFILE_PHOTO_MAX_SIZE", 5 * 1024 * 1024)),
    "PROFILE_PHOTO_ALLOWED_EXTENSIONS": os.environ.get(
        "TRIA_PROFILE_PHOTO_ALLOWED_EXTENSIONS",
        "jpg,jpeg,png"
    ).split(","),
}

FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", 2621440))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", 10485760))
DATA_UPLOAD_MAX_NUMBER_FILES = int(os.environ.get("DJANGO_DATA_UPLOAD_MAX_NUMBER_FILES", 20))

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
CSRF_COOKIE_HTTPONLY = False
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:9000", "http://127.0.0.1:8000"]   
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_PROXY_SSL_HEADER = None
# SESSION_INACTIVE_TIMEOUT = 5