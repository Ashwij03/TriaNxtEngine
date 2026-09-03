import logging
import time

from django.db import connections
from django.db.utils import OperationalError

logger = logging.getLogger(__name__)


class DatabaseRetryMiddleware:
    """Ensures the DB connection is alive before handling the request.

    Retries the connection health-check only -- never the request body --
    so a transient DB blip can't cause a non-idempotent write (POST/PUT/
    PATCH) to be applied twice. If the DB is still unreachable after the
    retries, the OperationalError propagates and Django's own exception
    handling surfaces a clean 503, rather than this middleware attempting
    to re-run get_response() itself.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.max_retries = 3

    def __call__(self, request):
        for attempt in range(self.max_retries):
            try:
                connections["default"].ensure_connection()
                break
            except OperationalError as e:
                logger.warning(
                    "DB connection check failed (attempt %s): %s", attempt + 1, e
                )
                if attempt == self.max_retries - 1:
                    logger.error("Database unreachable after retries")
                    raise
                time.sleep(0.5 * (attempt + 1))

        return self.get_response(request)
