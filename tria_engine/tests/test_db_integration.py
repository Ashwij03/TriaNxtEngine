"""
Project-level DB integration tests for Task 2 (Integrate DB with Existing
Code). These validate the pieces added for cloud DB integration:
  - the DB connection is actually reachable through the configured DATABASES
  - DatabaseRetryMiddleware retries the connection check (not the request
    body) on a transient OperationalError, then lets the request through
  - DatabaseRetryMiddleware re-raises after exhausting retries, so Django's
    own exception handling can surface a clean 503 instead of silently
    swallowing a real outage
  - the pooling/SSL settings actually landed in DATABASES as configured

Run with: python manage.py test tria_engine.tests.test_db_integration
"""
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.test import TestCase, RequestFactory

from tria_engine.middleware import DatabaseRetryMiddleware


class DatabaseConnectivityTests(TestCase):
    """Sanity checks that the configured connection actually works."""

    def test_default_connection_is_reachable(self):
        # ensure_connection() raises OperationalError if it can't connect --
        # if this passes, DATABASE_URL/DATABASES is wired correctly for
        # whatever DB the test is being run against.
        connections["default"].ensure_connection()
        self.assertTrue(connections["default"].is_usable())

    def test_can_run_a_query(self):
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        self.assertEqual(result[0], 1)


class DatabaseConfigTests(TestCase):
    """Confirms the pooling/SSL settings from Task 2 actually landed."""

    def test_conn_max_age_is_set(self):
        # Persistent connections should be enabled -- a value of 0 means
        # every request opens a fresh connection, defeating the point of
        # pooling under load.
        self.assertGreater(settings.DATABASES["default"].get("CONN_MAX_AGE", 0), 0)

    def test_ssl_required_outside_development(self):
        if settings.ENV == "development":
            self.skipTest("SSL is only enforced outside development")
        options = settings.DATABASES["default"].get("OPTIONS", {})
        # dj_database_url's ssl_require=True sets OPTIONS={'sslmode': 'require'}
        self.assertEqual(options.get("sslmode"), "require")


class DatabaseRetryMiddlewareTests(TestCase):
    """Exercises the retry-the-connection-not-the-request behavior."""

    def setUp(self):
        self.factory = RequestFactory()
        self.get_response = MagicMock(return_value=HttpResponse("ok"))
        self.middleware = DatabaseRetryMiddleware(self.get_response)
        # keep tests fast -- don't actually sleep between retries
        self.sleep_patcher = patch("tria_engine.middleware.time.sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    @patch("tria_engine.middleware.connections")
    def test_succeeds_immediately_when_db_is_healthy(self, mock_connections):
        request = self.factory.get("/")
        response = self.middleware(request)

        mock_connections["default"].ensure_connection.assert_called_once()
        self.get_response.assert_called_once_with(request)
        self.assertEqual(response.status_code, 200)

    @patch("tria_engine.middleware.connections")
    def test_retries_connection_check_then_succeeds(self, mock_connections):
        # fail twice, succeed on the third attempt
        mock_connections["default"].ensure_connection.side_effect = [
            OperationalError("connection reset"),
            OperationalError("connection reset"),
            None,
        ]
        request = self.factory.post("/")
        response = self.middleware(request)

        self.assertEqual(
            mock_connections["default"].ensure_connection.call_count, 3
        )
        # the request body must only ever be forwarded once, regardless of
        # how many connection retries happened -- this is the whole point
        # of the fix (no duplicate POST/PUT/PATCH writes)
        self.get_response.assert_called_once_with(request)
        self.assertEqual(response.status_code, 200)

    @patch("tria_engine.middleware.connections")
    def test_reraises_after_exhausting_retries(self, mock_connections):
        mock_connections["default"].ensure_connection.side_effect = OperationalError(
            "db unreachable"
        )
        request = self.factory.post("/")

        with self.assertRaises(OperationalError):
            self.middleware(request)

        self.assertEqual(
            mock_connections["default"].ensure_connection.call_count,
            self.middleware.max_retries,
        )
        # get_response must never be called -- a genuinely down DB should
        # never let a write request through
        self.get_response.assert_not_called()
