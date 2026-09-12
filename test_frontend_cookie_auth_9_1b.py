import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


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

    def test_public_code_requests_use_a_neutral_acknowledgement(self):
        neutral = "Prüfe jetzt deine E-Mail und gib den Code ein. Falls kein Code ankommt, prüfe Spam oder versuche es später erneut."
        for name in ("requestNewAccountCode", "requestEmailLoginCode"):
            with self.subTest(name=name):
                match = re.search(rf"async function {name}\(\)\{{(?P<body>.*?)\n\}}", self.frontend, re.DOTALL)
                self.assertIsNotNone(match)
                body = match.group("body")
                self.assertIn(neutral, body)
                self.assertNotIn("account_required", body)
                self.assertNotIn("account_already_exists", body)
                self.assertNotIn("Code gesendet an ${email}.", body)

    def test_auth_flow_restarts_server_bootstrap_without_hash_reload(self):
        transition = re.search(
            r"async function continueWithSession\(\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(transition)
        body = transition.group("body")
        self.assertIn("bootstrapAuthenticatedApp()", body)
        self.assertIn('state==="onboarding"', body)
        self.assertIn("showServerOnboarding()", body)
        self.assertNotIn("location.reload()", body)

    def test_authenticated_bootstrap_prioritizes_missing_password(self):
        pin_status = re.search(
            r"async function fetchPinStatus\(\)(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(pin_status)
        body = pin_status.group("body")
        self.assertIn("data.password_setup_required", body)
        self.assertIn('showPasswordSetup(true)', body)
        self.assertIn('return {state:"password_setup"};', body)
        self.assertLess(body.index("data.password_setup_required"), body.index("data.pin_status"))
        self.assertLess(body.index("data.pin_status"), body.index('state:"onboarding"'))

    def test_server_session_wins_over_local_profile_before_bootstrap(self):
        self.assertIn("let APP_MODE = (()=>", self.frontend)
        self.assertIn('return "bridge";', self.frontend)
        resolver = re.search(
            r"async function resolveAppMode\(\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(resolver)
        body = resolver.group("body")
        self.assertIn("const session=await restoreEmailSession();", body)
        self.assertIn('if(session.valid)', body)
        self.assertIn('APP_MODE="bridge";', body)
        self.assertLess(body.index("session.valid"), body.index("readProfile()"))

    def test_server_session_wins_over_demo_hash_and_storage(self):
        resolver = re.search(
            r"async function resolveAppMode\(\)(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(resolver)
        body = resolver.group("body")
        self.assertLess(body.index("if(session.valid)"), body.index('APP_MODE="bridge"'))
        self.assertIn('sessionStorage.removeItem("rove-demo")', body)
        self.assertIn("if(!session.definitive)", body)
        self.assertLess(body.index("if(!session.definitive)"), body.index('APP_MODE==="mock"'))

    def test_app_stays_hidden_until_mode_resolution(self):
        self.assertIn('document.getElementById("app")?.setAttribute("hidden","");', self.frontend)
        startup = self.frontend.split('(async function(){', 1)[1].split('// ===================== ONBOARDING', 1)[0]
        self.assertLess(startup.index("await resolveAppMode();"), startup.index('removeAttribute("hidden")'))

    def test_state_bootstrap_uses_server_user_id_for_bridge_storage(self):
        self.assertIn("const serverUserId=Number(b.user_id);", self.frontend)
        self.assertIn("if(!Number.isSafeInteger(serverUserId)||serverUserId<1)", self.frontend)
        self.assertIn("BRIDGE_USER_ID = serverUserId;", self.frontend)
        self.assertIn("restoreBridgeLocal(readBridgeLocal(), BRIDGE_USER_ID);", self.frontend)

    def test_local_profile_is_only_used_after_definitive_unauthenticated_response(self):
        session = re.search(
            r"async function restoreEmailSession\(\)(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(session)
        body = session.group("body")
        self.assertIn("{valid:!!(res.ok && data.ok), definitive:res.status===401||res.ok}", body)
        self.assertIn("{valid:false,definitive:false}", body)
        resolver = re.search(
            r"async function resolveAppMode\(\)(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIn('APP_MODE=session.definitive && readProfile() ? "profile" : "bridge";', resolver.group("body"))

    def test_startup_resolves_mode_before_local_load_or_server_bootstrap(self):
        startup = self.frontend.split('(async function(){', 1)[1].split('// ===================== ONBOARDING', 1)[0]
        self.assertLess(startup.index("await resolveAppMode();"), startup.index("installProfileAutosave();"))
        self.assertLess(startup.index("await resolveAppMode();"), startup.index("loadProfileState();"))
        self.assertLess(startup.index("await resolveAppMode();"), startup.index("bootstrapAuthenticatedApp()"))
        self.assertIn('document.getElementById("app")?.removeAttribute("hidden");', startup)

    def test_server_bootstrap_never_uses_the_local_profile_renderer(self):
        startup = self.frontend.split('(async function(){', 1)[1].split('// ===================== ONBOARDING', 1)[0]
        bridge = startup.split('if(APP_MODE==="bridge"){', 1)[1].split('} else if(APP_MODE==="profile")', 1)[0]
        self.assertNotIn("loadProfileState()", bridge)
        self.assertIn("bootstrapAuthenticatedApp()", bridge)
        self.assertIn("restoreBridgeLocal(readBridgeLocal(), BRIDGE_USER_ID);", self.frontend)

    def test_password_setup_continues_through_bootstrap(self):
        setup = re.search(r"async function completePasswordSetup\(\)(?P<body>.*?)\n\}", self.frontend, re.DOTALL)
        self.assertIsNotNone(setup)
        body = setup.group("body")
        self.assertIn("continueWithSession();", body)
        self.assertNotIn("location.href", body)

    def test_password_actions_have_in_flight_guards_and_feedback(self):
        self.assertIn("const AUTH_ACTIONS_IN_FLIGHT = new Set();", self.frontend)
        for name, key, busy_label in (
            ("passwordLogin", "password-login", "Anmeldung läuft ..."),
            ("completePasswordSetup", "password-setup", "Passwort wird gespeichert ..."),
            ("requestPasswordReset", "password-reset-request", "Code wird gesendet ..."),
            ("confirmPasswordReset", "password-reset-confirm", "Passwort wird gespeichert ..."),
        ):
            with self.subTest(name=name):
                match = re.search(rf"async function {name}\(\)(?P<body>.*?)\n\}}", self.frontend, re.DOTALL)
                self.assertIsNotNone(match)
                body = match.group("body")
                self.assertIn(f'const action="{key}";', body)
                self.assertIn("if(!beginAuthAction(action,", body)
                self.assertIn(busy_label, body)
                self.assertIn("endAuthAction(action,", body)

    def test_login_errors_remain_neutral_and_distinguish_network_or_rate_limit(self):
        login = re.search(r"async function passwordLogin\(\)(?P<body>.*?)\n\}", self.frontend, re.DOTALL)
        self.assertIsNotNone(login)
        body = login.group("body")
        self.assertIn("Anmeldung nicht möglich. Prüfe deine Angaben und versuche es erneut.", body)
        self.assertIn("Rov.E konnte die Anfrage gerade nicht abschließen. Bitte versuche es erneut.", body)
        self.assertIn("Zu viele Versuche. Warte kurz und versuche es später erneut.", self.frontend)
        self.assertIn('data?.error==="too_many_login_attempts"', self.frontend)
        self.assertNotIn("E-Mail-Adresse oder Passwort stimmen nicht.", body)

        verify = re.search(r"async function verifyEmailLoginCode\(\)(?P<body>.*?)\n\}", self.frontend, re.DOTALL)
        self.assertIsNotNone(verify)
        self.assertIn("Rov.E konnte die Anfrage gerade nicht abschließen. Bitte versuche es erneut.", verify.group("body"))

    def test_password_setup_reports_success_before_continuing(self):
        setup = re.search(r"async function completePasswordSetup\(\)(?P<body>.*?)\n\}", self.frontend, re.DOTALL)
        self.assertIsNotNone(setup)
        body = setup.group("body")
        self.assertIn("Passwort gespeichert. Wir bringen dich weiter ...", body)
        self.assertLess(body.index("endAuthAction(action"), body.index("continueWithSession("))

    def test_auth_surface_hides_financial_app_and_tabbar(self):
        self.assertIn('body.auth-flow-active #app,body.auth-flow-active .tabbar', self.frontend)
        self.assertIn('function setAuthSurface(active)', self.frontend)
        self.assertIn('if(active) document.getElementById("app")?.setAttribute("hidden","");', self.frontend)
        for name in ("showAppConnect", "showNewAccountRegistration", "showPasswordSetup", "showPasswordReset"):
            with self.subTest(name=name):
                body = self.function_body(name)
                self.assertIn("setAuthSurface(true);", body)
        bootstrap = self.function_body("loadBridgeState")
        self.assertLess(bootstrap.index("setAuthSurface(false);"), bootstrap.index('removeAttribute("hidden")'))

    def test_auth_inputs_request_native_mobile_keyboard_semantics(self):
        for field, attrs in (
            ("loginEmail", ('type="email"', 'inputmode="email"', 'autocapitalize="none"', 'autocorrect="off"', 'spellcheck="false"')),
            ("loginPassword", ('type="password"', 'autocomplete="current-password"', 'autocorrect="off"', 'spellcheck="false"')),
            ("registerEmail", ('type="email"', 'inputmode="email"', 'autocapitalize="none"', 'autocorrect="off"', 'spellcheck="false"')),
            ("resetEmail", ('type="email"', 'inputmode="email"', 'autocapitalize="none"', 'autocorrect="off"', 'spellcheck="false"')),
            ("resetCode", ('type="text"', 'inputmode="numeric"', 'autocomplete="one-time-code"')),
        ):
            with self.subTest(field=field):
                field_markup = re.search(rf'<input id="{field}"[^>]+>', self.frontend)
                self.assertIsNotNone(field_markup)
                for attr in attrs:
                    self.assertIn(attr, field_markup.group(0))

    def function_body(self, name):
        match = re.search(
            rf"(?:async )?function {re.escape(name)}\([^)]*\)\{{(?P<body>.*?)\n\}}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(match, name)
        return match.group("body")


if __name__ == "__main__":
    unittest.main()
