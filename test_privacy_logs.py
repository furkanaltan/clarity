import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import rove_app_api
from rove_log_safety import safe_exception_summary


class PrivacyLogTests(unittest.TestCase):
    def test_exception_summary_drops_sensitive_message_and_keeps_provider_status(self):
        error = HTTPError(
            "https://provider.invalid",
            400,
            "provider rejected",
            {},
            io.BytesIO(b"email=furkan@example.com token=secret-financial-payload"),
        )

        summary = safe_exception_summary(error)

        self.assertEqual(summary, "HTTPError(status=400)")
        self.assertNotIn("furkan@example.com", summary)
        self.assertNotIn("secret-financial-payload", summary)
        error.close()

    def test_brevo_error_does_not_copy_provider_body_into_exception(self):
        error = HTTPError(
            "https://api.brevo.com",
            400,
            "bad request",
            {},
            io.BytesIO(b"recipient=furkan@example.com code=123456"),
        )

        with patch.object(rove_app_api, "BREVO_API_KEY", "test-key"), \
             patch.object(rove_app_api.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                rove_app_api.send_login_email("furkan@example.com", "123456")

        message = str(raised.exception)
        self.assertEqual(message, "brevo_status_400")
        self.assertNotIn("furkan@example.com", message)
        self.assertNotIn("123456", message)
        error.close()

    def test_safe_summary_drops_push_payloads(self):
        class PushError(Exception):
            response = type("Response", (), {"status_code": 503})()

        summary = safe_exception_summary(PushError("push token=secret body=private finance"))

        self.assertEqual(summary, "PushError(status=503)")
        self.assertNotIn("secret", summary)
        self.assertNotIn("private finance", summary)


if __name__ == "__main__":
    unittest.main()
