import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent.parent / "rove-app" / "index.html"


class FrontendCookieAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_no_client_bearer_or_static_state_reference_remains(self):
        forbidden = (
            "ROVE_API.token",
            "Authorization: Bearer",
            'Authorization":`Bearer',
            "app-state",
            "BRIDGE_STATE",
            "state_url",
            "?state=",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, self.frontend)

    def test_cookie_api_wrapper_is_the_only_authenticated_feature_transport(self):
        wrapper = re.search(
            r"async function apiFetch\(path,options=\{\}\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(wrapper)
        body = wrapper.group("body")
        self.assertIn('credentials:"include"', body)
        self.assertIn('headers.delete("Authorization")', body)
        self.assertIn("handleApiSessionStatus(response.status)", body)

        direct_api_fetches = re.findall(
            r"fetch\(`\$\{ROVE_API\.baseUrl\}", self.frontend
        )
        self.assertEqual(len(direct_api_fetches), 1, "Only apiFetch may call the API base URL directly")

    def test_cookie_bootstrap_marks_session_authenticated_without_token(self):
        self.assertIn(
            "ROVE_API = {baseUrl:PAIR_API_BASE_URL,authenticated:true};",
            self.frontend,
        )
        self.assertIn(
            'fetch(`${PAIR_API_BASE_URL}/v1/state`,{credentials:"include",cache:"no-store"',
            self.frontend,
        )
        self.assertIn("return !!(ROVE_API?.baseUrl && ROVE_API.authenticated);", self.frontend)

    def test_feature_matrix_uses_cookie_wrapper(self):
        paths = (
            "/v1/accounts",
            "/v1/transactions",
            "/v1/budgets",
            "/v1/contracts",
            "/v1/investments",
            "/v1/goals",
            "/v1/reports/",
            "/v1/profile",
            "/v1/push/preferences",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertRegex(
                    self.frontend,
                    rf"apiFetch\((?:`|\"){re.escape(path)}",
                )

    def test_session_expiry_hides_financial_app_and_returns_to_login(self):
        self.assertIn("if(status!==401 || API_SESSION_FAILURE_HANDLED) return;", self.frontend)
        self.assertIn("ROVE_API=null;", self.frontend)
        self.assertIn("BRIDGE_USER_ID=null;", self.frontend)
        self.assertIn("if(app) app.hidden=true;", self.frontend)
        self.assertIn("showAppConnect();", self.frontend)

    def test_future_pin_lock_is_central_without_pin_ui(self):
        self.assertIn('if(status===423){', self.frontend)
        self.assertIn('new CustomEvent("rove:api-locked")', self.frontend)
        self.assertNotIn("PIN_LOCKED", self.frontend)

    def test_logout_and_user_switch_clear_authenticated_transport(self):
        logout = re.search(
            r"async function logoutCurrentUser\(\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(logout)
        body = logout.group("body")
        self.assertIn('/v1/auth/logout', body)
        self.assertIn('credentials:"include"', body)
        self.assertIn("ROVE_API=null;", body)
        self.assertIn("BRIDGE_USER_ID=null;", body)
        self.assertIn("location.replace(location.pathname);", body)


if __name__ == "__main__":
    unittest.main()
