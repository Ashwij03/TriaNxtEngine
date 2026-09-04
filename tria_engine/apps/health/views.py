"""
Health and readiness endpoints for TriaNXT CTMS.

- /api/health/        → Liveness check (is the process alive?)
- /api/health/ready/  → Readiness check (is the app ready to serve traffic?)
"""
import logging
import time

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views import View

logger = logging.getLogger("tria_engine")


class HealthCheckView(View):
    """
    Liveness probe — returns 200 if the process is running.
    Does NOT check downstream dependencies.
    """

    def get(self, request):
        return JsonResponse({
            "status": "healthy",
            "service": "trianxt-ctms-engine",
            "timestamp": int(time.time()),
        })


class ReadinessCheckView(View):
    """
    Readiness probe — verifies database connectivity and cache
    availability before routing traffic to this instance.
    """

    def get(self, request):
        checks = {}
        overall = True

        # --- Database check ---
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            logger.error("Readiness check: database unreachable — %s", exc)
            checks["database"] = f"error: {type(exc).__name__}"
            overall = False

        # --- Cache check ---
        try:
            cache.set("_health_check_probe", "ok", 10)
            value = cache.get("_health_check_probe")
            checks["cache"] = "ok" if value == "ok" else "degraded"
        except Exception as exc:
            logger.warning("Readiness check: cache unavailable — %s", exc)
            checks["cache"] = f"unavailable: {type(exc).__name__}"
            # Cache is optional — not fatal

        status_code = 200 if overall else 503
        return JsonResponse({
            "status": "ready" if overall else "not_ready",
            "service": "trianxt-ctms-engine",
            "checks": checks,
            "timestamp": int(time.time()),
        }, status=status_code)
