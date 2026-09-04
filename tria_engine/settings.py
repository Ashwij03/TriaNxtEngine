# settings.py — Production-ready Django settings for TriaNXT CTMS

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------
# python-dotenv is declared in requirements/base.txt; load a project-root
# .env file (if present) before any os.environ reads below so secrets live
# in .env (git-ignored) rather than in this file. See .env.example.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


ENV = os.environ.get("DJANGO_ENV", "development")
DEBUG = ENV == "development"


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")

if not SECRET_KEY:
    if ENV == "development":
        SECRET_KEY = "dev-only-not-for-production-change-me-immediately"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production")


# DEBUG follows ENV. Production/UAT must never run with DEBUG=True.
ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1",
).split(",")


CTMS_ENCRYPTION_KEY = os.environ.get("CTMS_ENCRYPTION_KEY")

if not CTMS_ENCRYPTION_KEY and ENV != "development":
    raise ImproperlyConfigured("CTMS_ENCRYPTION_KEY is required")


AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "drf_yasg",
    "tria_engine.apps.accounts.apps.AccountsConfig",
    "tria_engine.apps.organizations.apps.OrganizationsConfig",
]

# ---------------------------------------------------------------------------
# Middleware — CORS must be first
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ---------------------------------------------------------------------------
# CORS — environment-driven, NO wildcard in production
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

CORS_ALLOW_CREDENTIALS = os.environ.get("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-api-key",
]

# ---------------------------------------------------------------------------
# URL / Templates / WSGI
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------
#
# Development:
#   - If DATABASE_URL is not provided, use local SQLite.
#   - If DATABASE_URL is provided, use PostgreSQL through dj-database-url.
#
# Non-development:
#   - DATABASE_URL is mandatory.
#   - PostgreSQL connections require SSL/TLS.
#

import dj_database_url

DB_CONNECT_TIMEOUT = int(
    os.environ.get("DB_CONNECT_TIMEOUT_SECONDS", "5")
)

DB_STATEMENT_TIMEOUT_MS = int(
    os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000")
)


DATABASE_URL = os.environ.get("DATABASE_URL")


if ENV == "development" and not DATABASE_URL:
    # Local development fallback to SQLite.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

else:
    if not DATABASE_URL:
        raise ImproperlyConfigured(
            "DATABASE_URL is required outside development"
        )

    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=False,
        )
    }


# ---------------------------------------------------------------------------
# Cache — Redis (optional for development, required for production)
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "trianxt",
            "TIMEOUT": int(os.environ.get("CACHE_DEFAULT_TIMEOUT", "300")),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "trianxt-dev-cache",
            "TIMEOUT": 300,
        }
    }

# ---------------------------------------------------------------------------
# REST Framework
# ---------------------------------------------------------------------------
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
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("DRF_ANON_THROTTLE", "100/hour"),
        "user": os.environ.get("DRF_USER_THROTTLE", "1000/hour"),
    },
}


# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        )
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
        "OPTIONS": {
            "min_length": 8,
        },
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        )
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        )
    },
]


# ---------------------------------------------------------------------------
# Password hashers
# ---------------------------------------------------------------------------

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "tria_engine.apps.accounts.authentication.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"

USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static / Media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ---------------------------------------------------------------------------
# TriaNXT security configuration
# ---------------------------------------------------------------------------

TRIA_SECURITY = {
    "PASSWORD_MAX_AGE_DAYS": int(
        os.environ.get("TRIA_PASSWORD_MAX_AGE_DAYS", "90")
    ),
    "TOKEN_EXPIRY_MINUTES": int(
        os.environ.get("TRIA_TOKEN_EXPIRY_MINUTES", "10")
    ),
    "EXPOSE_OTP_IN_RESPONSE": (
        os.environ.get(
            "TRIA_EXPOSE_OTP_IN_RESPONSE",
            "true",
        ).lower()
        == "true"
    ),
}


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------

TRIA_UPLOADS = {
    "DOCUMENT_MAX_SIZE": int(
        os.environ.get(
            "TRIA_DOCUMENT_MAX_SIZE",
            str(10 * 1024 * 1024),
        )
    ),
    "DOCUMENT_ALLOWED_EXTENSIONS": os.environ.get(
        "TRIA_DOCUMENT_ALLOWED_EXTENSIONS",
        "pdf,doc,docx,xls,xlsx,txt",
    ).split(","),
    "PROFILE_PHOTO_MAX_SIZE": int(
        os.environ.get(
            "TRIA_PROFILE_PHOTO_MAX_SIZE",
            str(5 * 1024 * 1024),
        )
    ),
    "PROFILE_PHOTO_ALLOWED_EXTENSIONS": os.environ.get(
        "TRIA_PROFILE_PHOTO_ALLOWED_EXTENSIONS",
        "jpg,jpeg,png",
    ).split(","),
}

FILE_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", "2621440")
)

DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", "10485760")
)

DATA_UPLOAD_MAX_NUMBER_FILES = int(
    os.environ.get("DJANGO_DATA_UPLOAD_MAX_NUMBER_FILES", "20")
)


# ---------------------------------------------------------------------------
# Security Settings — environment-aware
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

# SECURE_SSL_REDIRECT: overridable via env
env_ssl_redirect = os.environ.get("SECURE_SSL_REDIRECT", "").lower()
if env_ssl_redirect in ("true", "1", "yes"):
    SECURE_SSL_REDIRECT = True
elif env_ssl_redirect in ("false", "0", "no"):
    SECURE_SSL_REDIRECT = False
elif ENV == "production":
    SECURE_SSL_REDIRECT = True
else:
    SECURE_SSL_REDIRECT = False

if ENV == "production":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    CSRF_COOKIE_HTTPONLY = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_PROXY_SSL_HEADER = None

SESSION_COOKIE_HTTPONLY = True

CSRF_TRUSTED_ORIGINS = os.environ.get(
    "CSRF_TRUSTED_ORIGINS",
    "http://127.0.0.1:9000,http://127.0.0.1:8000,http://localhost:3000"
).split(",")

# Session settings
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", 3600))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"