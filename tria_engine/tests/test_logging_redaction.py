import logging

from django.test import SimpleTestCase

from tria_engine.logging_filters import PIIRedactionFilter


class PIIRedactionFilterTests(SimpleTestCase):
    def setUp(self):
        self.logger = logging.getLogger("pii-redaction-test")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.records = []

        class _Capture(logging.Handler):
            def emit(_self, record):
                self.records.append(record.getMessage())

        handler = _Capture()
        handler.addFilter(PIIRedactionFilter())
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)

    def test_redacts_percent_style_password_arg(self):
        self.logger.info("login failed for %s password=%s", "noel", "hunter2")
        self.assertNotIn("hunter2", self.records[0])
        self.assertIn("***REDACTED***", self.records[0])
        self.assertIn("noel", self.records[0])  # non-sensitive arg preserved

    def test_redacts_quoted_json_style_fields(self):
        self.logger.info('payload: {"ssn": "123-45-6789", "user": "noel"}')
        self.assertNotIn("123-45-6789", self.records[0])
        self.assertIn("noel", self.records[0])

    def test_redacts_authorization_bearer_token(self):
        self.logger.info("Authorization: Bearer abc.def.ghi")
        self.assertNotIn("abc.def.ghi", self.records[0])

    def test_does_not_touch_non_sensitive_fields(self):
        self.logger.info("order_id=4471 status=shipped")
        self.assertIn("order_id=4471", self.records[0])
        self.assertIn("status=shipped", self.records[0])

    def test_percent_placeholder_in_sensitive_position_does_not_crash(self):
        # regression test: previously, redacting the raw msg before args
        # substitution left a dangling %s with no value to consume,
        # raising TypeError during logging instead of emitting a record.
        self.logger.info("token=%s issued", "abc.def.ghi")
        self.assertNotIn("abc.def.ghi", self.records[0])
        self.assertIn("issued", self.records[0])

    def test_dict_style_args_are_redacted(self):
        self.logger.warning("%(user)s login, dob=%(dob)s", {"user": "noel", "dob": "1990-01-01"})
        self.assertNotIn("1990-01-01", self.records[0])
        self.assertIn("noel", self.records[0])
