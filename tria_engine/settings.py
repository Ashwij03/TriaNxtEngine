# settings.py

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------
# python-dotenv is declared in requirements/base.txt; load a project-root
# .env file (if present) before any os.environ reads below so secrets live
# in .env (git-ignored) rather than in this file. See .env.example.
try:
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
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")


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
    "tria_engine.apps.licensing.apps.LicensingConfig",
    "tria_engine.apps.monitoring.apps.MonitoringConfig",
    "tria_engine.apps.subscriptions.apps.SubscriptionsConfig",
    "tria_engine.apps.billing.apps.BillingConfig",
]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "tria_engine.middleware.DatabaseRetryMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
]

# Needed so the browser will actually send/receive the Django session
# cookie on cross-origin requests from the React dev server.
CORS_ALLOW_CREDENTIALS = True


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
# Security:
#   - DATABASE_URL must be supplied through the deployment environment or
#     secret manager and must never be committed to source control.
#   - conn_max_age=600 enables persistent connections for 10 minutes.
#   - connect_timeout limits the time spent attempting a DB connection.
#   - statement_timeout limits the execution time of individual PostgreSQL
#     queries.
#

DB_CONNECT_TIMEOUT = int(
    os.environ.get("DB_CONNECT_TIMEOUT_SECONDS", "5")
)

DB_STATEMENT_TIMEOUT_MS = int(
    os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000")
)


DATABASE_URL = os.environ.get("DATABASE_URL")


if ENV == "development" and not DATABASE_URL:
    # Local development fallback.
    DATABASES = {
        "default": dj_database_url.config(
            default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
            conn_max_age=600,
        )
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
            ssl_require=True,
        )
    }

    DATABASES["default"].setdefault("OPTIONS", {})

    # Limit the time spent attempting to establish a database connection.
    DATABASES["default"]["OPTIONS"]["connect_timeout"] = (
        DB_CONNECT_TIMEOUT
    )

    # Limit individual PostgreSQL query execution time.
    DATABASES["default"]["OPTIONS"]["options"] = (
        f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
    )

    # -----------------------------------------------------------------------
    # Transaction isolation
    # -----------------------------------------------------------------------
    #
    # PostgreSQL/Django uses READ COMMITTED by default.
    #
    # The application keeps READ COMMITTED as the default because the
    # referral-redemption race condition is handled locally with
    # select_for_update() and a database-level UniqueConstraint.
    #
    # A stronger isolation level can be selected through:
    #
    # DB_TRANSACTION_ISOLATION_LEVEL=REPEATABLE_READ
    #
    # or:
    #
    # DB_TRANSACTION_ISOLATION_LEVEL=SERIALIZABLE
    #

    try:
        import psycopg2.extensions

        _ISOLATION_LEVELS = {
            "READ_COMMITTED": (
                psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED
            ),
            "REPEATABLE_READ": (
                psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ
            ),
            "SERIALIZABLE": (
                psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE
            ),
        }

        _isolation_choice = os.environ.get(
            "DB_TRANSACTION_ISOLATION_LEVEL",
            "READ_COMMITTED",
        ).upper()

        if _isolation_choice not in _ISOLATION_LEVELS:
            raise ImproperlyConfigured(
                "Invalid DB_TRANSACTION_ISOLATION_LEVEL. "
                "Use READ_COMMITTED, REPEATABLE_READ, or SERIALIZABLE."
            )

        DATABASES["default"]["OPTIONS"]["isolation_level"] = (
            _ISOLATION_LEVELS[_isolation_choice]
        )

    except ImportError:
        # psycopg2 is required for PostgreSQL deployments.
        # SQLite development does not enter this branch when DATABASE_URL
        # is not configured.
        pass

    # -----------------------------------------------------------------------
    # Optional AWS RDS IAM database authentication
    # -----------------------------------------------------------------------
    #
    # Enable with:
    #
    # IAM_DB_AUTH_ENABLED=true
    #
    # The DB user must already have the required rds_iam role and the
    # application's AWS IAM role must have rds-db:connect permission.
    #

    if os.environ.get("IAM_DB_AUTH_ENABLED", "false").lower() == "true":

        DATABASES["default"]["ENGINE"] = (
            "tria_engine.db_backends.iam_postgres"
        )

        DATABASES["default"]["IAM_AUTH_REGION"] = os.environ.get(
            "AWS_REGION",
            "us-east-1",
        )

        # IAM authentication uses short-lived authentication tokens instead
        # of a static database password.
        DATABASES["default"].pop("PASSWORD", None)


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
    # "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    # "django.contrib.auth.hashers.ScryptPasswordHasher",
]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "tria_engine.apps.accounts.authentication.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]


# ---------------------------------------------------------------------------
# Django REST Framework
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
}


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
    os.environ.get(
        "DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE",
        "2621440",
    )
)

DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get(
        "DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE",
        "10485760",
    )
)

DATA_UPLOAD_MAX_NUMBER_FILES = int(
    os.environ.get(
        "DJANGO_DATA_UPLOAD_MAX_NUMBER_FILES",
        "20",
    )
)


# ---------------------------------------------------------------------------
# Billing / payments
# ---------------------------------------------------------------------------

# Razorpay credentials come from the environment (or .env).
# Only KEY_ID is exposed to the frontend checkout widget.
# KEY_SECRET / WEBHOOK_SECRET stay server-side.
#
# Production must export real values or Django refuses to boot.

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET")


if not RAZORPAY_KEY_ID:
    if ENV == "development":
        RAZORPAY_KEY_ID = "rzp_test_dev_only_key_id"

        RAZORPAY_KEY_SECRET = (
            RAZORPAY_KEY_SECRET
            or "dev-only-key-secret-not-for-production"
        )

        RAZORPAY_WEBHOOK_SECRET = (
            RAZORPAY_WEBHOOK_SECRET
            or "dev-only-webhook-secret"
        )

    else:
        raise ImproperlyConfigured(
            "RAZORPAY_KEY_ID is required"
        )


if not RAZORPAY_KEY_SECRET and ENV != "development":
    raise ImproperlyConfigured(
        "RAZORPAY_KEY_SECRET is required"
    )


if not RAZORPAY_WEBHOOK_SECRET and ENV != "development":
    raise ImproperlyConfigured(
        "RAZORPAY_WEBHOOK_SECRET is required"
    )


# Length of a paid period when a payment clears.
# Product decision: monthly cycle.
BILLING_DEFAULT_PERIOD_DAYS = int(
    os.environ.get(
        "BILLING_DEFAULT_PERIOD_DAYS",
        "30",
    )
)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"


# In development, HTTPS may not be configured.
# Production/UAT should enable these through environment-specific settings
# or deployment configuration.
SECURE_SSL_REDIRECT = ENV != "development"

SESSION_COOKIE_SECURE = ENV != "development"

CSRF_COOKIE_SECURE = ENV != "development"

SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_HTTPONLY = False


CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:9000",
    "http://127.0.0.1:8000",
]


if ENV != "development":
    SECURE_HSTS_SECONDS = int(
        os.environ.get(
            "SECURE_HSTS_SECONDS",
            "31536000",
        )
    )

    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

    SECURE_HSTS_PRELOAD = True

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

else:
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_PROXY_SSL_HEADER = None


# SESSION_INACTIVE_TIMEOUT = 5


# ---------------------------------------------------------------------------
# Logging / PII & PHI redaction
# ---------------------------------------------------------------------------
#
# The redaction filter strips known PII/PHI field values such as password,
# token, SSN, DOB, diagnosis, etc. from log records before they are emitted.
#
# Applied at the handler level so future handlers cannot accidentally bypass
# the redaction policy if they reuse this configured handler.
#

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "filters": {
        "pii_redaction": {
            "()": "tria_engine.logging_filters.PIIRedactionFilter",
        },
    },

    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": [
                "pii_redaction",
            ],
            "formatter": "verbose",
        },
    },

    "root": {
        "handlers": [
            "console",
        ],
        "level": os.environ.get(
            "DJANGO_LOG_LEVEL",
            "INFO",
        ),
    },

    "loggers": {
        "django": {
            "handlers": [
                "console",
            ],
            "level": os.environ.get(
                "DJANGO_LOG_LEVEL",
                "INFO",
            ),
            "propagate": False,
        },
    },
}