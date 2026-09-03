# settings.py

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

ENV = os.environ.get("DJANGO_ENV", "development")
DEBUG = ENV == "development"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if ENV == "development":
        SECRET_KEY = "dev-only-not-for-production-change-me-immediately"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")

# DEBUG follows ENV -- previously hardcoded to True here, which silently
# overrode the correct ENV-based value set above and meant production would
# have run with DEBUG=True (stack traces, SQL, and settings values exposed
# to any 500 response) regardless of DJANGO_ENV. Removed.

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
    "tria_engine.middleware.DatabaseRetryMiddleware",
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

# DATABASE_URL is expected to be injected as an env var by the deployment
# platform (ECS task def / K8s secret) -- the value itself comes from AWS
# Secrets Manager via the ctms/* read-only IAM policy set up in Section 4,
# never committed here or read directly from Secrets Manager in-process.
#
# - conn_max_age=600: persistent connections for 10 min; keep this in sync
#   with pool sizing so (replicas * gunicorn workers * conns) stays well
#   under the RDS instance's max_connections.
# - ssl_require=True: enforces TLS client-side, matching `ssl=1` set at the
#   RDS parameter-group level in Section 1 -- belt and suspenders.
# - connect_timeout: bounds how long a single connection ATTEMPT can hang
#   (TCP-level, before auth even happens). Without this the only backstop
#   is the OS TCP timeout (often 60-130s). Combined with the retry
#   middleware's up to 3 attempts, an unreachable DB could otherwise tie
#   up a sync Gunicorn worker for several minutes on one request.
# - statement_timeout: bounds how long an individual QUERY can run once
#   connected, so a runaway/locked query can't hold a connection (and a
#   worker) hostage indefinitely. Set via the `options` connection
#   parameter, which psycopg2 passes through as a Postgres session GUC.
DB_CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT_SECONDS", 5))
DB_STATEMENT_TIMEOUT_MS = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", 30000))

if ENV == "development":
    DATABASES = {
        "default": dj_database_url.config(
            default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
            conn_max_age=600,
        )
    }
else:
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        raise ImproperlyConfigured("DATABASE_URL is required outside development")
    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"]["connect_timeout"] = DB_CONNECT_TIMEOUT
    DATABASES["default"]["OPTIONS"]["options"] = (
        f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS}"
    )

    # Transaction isolation level -- explicit decision, not a silent
    # default. READ COMMITTED (Postgres/Django's own default) is kept
    # deliberately: the referral-redemption race condition (Section 6) is
    # already handled correctly via select_for_update() row locks plus a
    # DB-level UniqueConstraint, which is the standard Django pattern for
    # race safety and doesn't need a stricter global isolation level.
    # Bumping every connection to REPEATABLE READ or SERIALIZABLE would
    # add serialization-failure handling (retry-on-conflict) across every
    # view in the app for a problem that's already solved locally where
    # it actually occurs. Overridable via env var if a specific workload
    # (e.g. a reporting/export view) later needs stronger guarantees.
    import psycopg2.extensions

    _ISOLATION_LEVELS = {
        "READ_COMMITTED": psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED,
        "REPEATABLE_READ": psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ,
        "SERIALIZABLE": psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE,
    }
    _isolation_choice = os.environ.get(
        "DB_TRANSACTION_ISOLATION_LEVEL", "READ_COMMITTED"
    ).upper()
    DATABASES["default"]["OPTIONS"]["isolation_level"] = _ISOLATION_LEVELS[
        _isolation_choice
    ]

    # IAM_DB_AUTH_ENABLED opts a workload into RDS IAM auth instead of a
    # static password from Secrets Manager, per the Security Tasks in
    # Section 2 ("prefer IAM auth where the workload supports it"). The
    # DB user in DATABASE_URL must already be granted the rds_iam role and
    # the app's IAM role needs rds-db:connect for that user -- this only
    # swaps how the password is obtained per-connection, not the account
    # setup itself.
    if os.environ.get("IAM_DB_AUTH_ENABLED", "false").lower() == "true":
        DATABASES["default"]["ENGINE"] = "tria_engine.db_backends.iam_postgres"
        DATABASES["default"]["IAM_AUTH_REGION"] = os.environ.get(
            "AWS_REGION", "us-east-1"
        )
        # the token generated per-connection replaces this; anything here
        # would be stale within 15 minutes and is never used once the
        # custom backend is active.
        DATABASES["default"].pop("PASSWORD", None)


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

# REST_FRAMEWORK = {
#     "DEFAULT_AUTHENTICATION_CLASSES": [
#         "rest_framework.authentication.SessionAuthentication",
#     ],
#     "DEFAULT_PERMISSION_CLASSES": [
#         "rest_framework.permissions.IsAuthenticated",
#     ],
#     "DEFAULT_RENDERER_CLASSES": [
#         "rest_framework.renderers.JSONRenderer",
#     ],
# }

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

# Logging -- the redact filter strips known PII/PHI field values (password,
# token, ssn, dob, diagnosis, etc.) from every log record before it's
# emitted, per Section 2's Security Task. Applied to every handler so
# there's no accidental bypass route (e.g. a handler added later that
# forgets to attach the filter).
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
            "filters": ["pii_redaction"],
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}