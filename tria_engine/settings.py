# settings.py — Production-ready Django settings for TriaNXT CTMS
# Updated for Task 3: UI ↔ Engine Integration (Prod Scale)

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV = os.environ.get("DJANGO_ENV", "development")
DEBUG = ENV == "development"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if ENV == "development":
        SECRET_KEY = "dev-only-not-for-production-change-me-immediately"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

CTMS_ENCRYPTION_KEY = os.environ.get("CTMS_ENCRYPTION_KEY")
if not CTMS_ENCRYPTION_KEY:
    if ENV != "development":
        raise ImproperlyConfigured("CTMS_ENCRYPTION_KEY is required in production")

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
    "tria_engine.apps.health.apps.HealthConfig",
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
# URL Configuration
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
# Database — configurable via environment
# ---------------------------------------------------------------------------
DB_ENGINE = os.environ.get("DB_ENGINE", "django.db.backends.sqlite3")

if DB_ENGINE == "django.db.backends.sqlite3":
    DATABASES = {
        "default": {
            "ENGINE": DB_ENGINE,
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": DB_ENGINE,
            "NAME": os.environ.get("DB_NAME", "trianxt_ctms"),
            "USER": os.environ.get("DB_USER", "trianxt"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "OPTIONS": {},
        }
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

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTHENTICATION_BACKENDS = [
    "tria_engine.apps.accounts.authentication.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

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

# ---------------------------------------------------------------------------
# Security Settings — environment-aware
# ---------------------------------------------------------------------------
# SECURE_SSL_REDIRECT: overridable via env (set to 'false' in Docker since SSL is terminated at nginx)
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
    SECURE_HSTS_SECONDS = 31536000
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

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = os.environ.get(
    "CSRF_TRUSTED_ORIGINS",
    "http://127.0.0.1:9000,http://127.0.0.1:8000,http://localhost:3000"
).split(",")

# Session settings
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", 3600))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging — structured production-friendly logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO" if ENV == "production" else "DEBUG")
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
    },
    "handlers": {
        "console": {
            "level": LOG_LEVEL,
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "file": {
            "level": LOG_LEVEL,
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "trianxt.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
        "error_file": {
            "level": "ERROR",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "trianxt_errors.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
        "security_file": {
            "level": "WARNING",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "trianxt_security.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": True,
        },
        "django.request": {
            "handlers": ["console", "error_file"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["security_file"],
            "level": "WARNING",
            "propagate": False,
        },
        "tria_engine": {
            "handlers": ["console", "file", "error_file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": LOG_LEVEL,
    },
}

# ---------------------------------------------------------------------------
# Gunicorn settings
# ---------------------------------------------------------------------------
GUNICORN_WORKERS = int(os.environ.get("GUNICORN_WORKERS", "3"))
GUNICORN_TIMEOUT = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
GUNICORN_BIND = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")