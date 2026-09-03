import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendPinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def function_body(self, name):
        match = re.search(
            rf"(?:async )?function {re.escape(name)}\([^)]*\)\{{(?P<body>.*?)\n\}}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(match, name)
        return match.group("body")

    def test_pin_status_is_checked_before_financial_state(self):
        bootstrap = self.function_body("bootstrapAuthenticatedApp")
        self.assertLess(bootstrap.index("fetchPinStatus()"), bootstrap.index("loadBridgeState()"))
        boot = re.search(
            r'if\(APP_MODE==="bridge"\)\{(?P<body>.*?)\n\s*\} else if\(APP_MODE==="profile"\)',
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(boot)
        self.assertIn("bootstrapAuthenticatedApp()", boot.group("body"))

    def test_financial_app_is_hidden_until_pin_and_lock_screen_has_no_navigation(self):
        self.assertIn('if(APP_MODE==="bridge") document.getElementById("app")?.setAttribute("hidden","");', self.frontend)
        lock_screen = self.function_body("showPinScreen")
        self.assertIn('document.getElementById("app")?.setAttribute("hidden","")', lock_screen)
        self.assertNotIn("tabbar", lock_screen)
        self.assertNotIn("DATA.", lock_screen)

    def test_pin_screen_is_fullscreen_and_uses_numeric_keypad(self):
        self.assertIn("#onboard.pin-mode", self.frontend)
        self.assertIn('onboard.classList.add("pin-mode")', self.frontend)
        self.assertIn('data-pin-key="0"', self.frontend)
        self.assertIn('data-pin-key="delete"', self.frontend)
        self.assertIn("function pinPadPress(key)", self.frontend)
        self.assertIn("function pinEntryComplete(id)", self.frontend)
        self.assertIn("setTimeout(submitPinUnlock,80)", self.frontend)
        self.assertIn('PIN_PAD_TARGET="pinSetupConfirm"', self.frontend)
        self.assertNotIn('pinSetupConfirm")?.focus()', self.frontend)
        self.assertIn('Gib zum Einloggen deine PIN ein.', self.frontend)

    def test_pin_is_never_persisted_in_browser_storage_or_cookie(self):
        pin_block = self.frontend[
            self.frontend.index("async function pinRequest"):self.frontend.index("async function loadBridgeState")
        ]
        self.assertNotIn("localStorage", pin_block)
        self.assertNotIn("sessionStorage", pin_block)
        self.assertNotIn("document.cookie", pin_block)
        self.assertNotIn("Authorization", pin_block)

    def test_mobile_pin_fields_are_exactly_four_numeric_characters(self):
        self.assertIn('inputmode="numeric"', self.frontend)
        self.assertIn('pattern="[0-9]*"', self.frontend)
        self.assertIn('maxlength="4"', self.frontend)
        self.assertIn('e.target.value.replace(/\\D/g,"").slice(0,4)', self.frontend)

    def test_pin_input_does_not_trigger_password_autofill(self):
        pin_field = self.function_body("pinField")
        self.assertIn('name="app_pin"', pin_field)
        self.assertIn('type="text"', pin_field)
        self.assertIn('autocomplete="off"', pin_field)
        self.assertIn('autocorrect="off"', pin_field)
        self.assertIn('autocapitalize="off"', pin_field)
        self.assertIn('spellcheck="false"', pin_field)
        self.assertNotIn('type="password"', pin_field)
        self.assertNotIn('current-password', pin_field)
        self.assertNotIn('one-time-code', pin_field)
        self.assertNotIn('-webkit-text-security', self.frontend)
        self.assertIn('pin-entry${withPad?"":" pin-entry-compact"}', pin_field)
        self.assertIn('id="${id}Dots"', pin_field)
        self.assertIn('readonly aria-readonly="true" tabindex="-1"', pin_field)
        self.assertIn('.pin-entry .pin-pad-input{pointer-events:none}', self.frontend)

    def test_onboarding_finishes_at_pin_setup_not_home(self):
        finish = self.function_body("finishOnboarding")
        marker = 'SERVER_ONBOARDING_REQUIRED=false;'
        tail = finish[finish.index(marker):]
        self.assertIn('showPinScreen("setup")', tail)
        self.assertNotIn("location.href=location.pathname", tail)

    def test_two_minute_inactivity_relocks_and_active_use_touches_server(self):
        self.assertIn("const PIN_INACTIVITY_MS=2*60*1000;", self.frontend)
        self.assertIn('pinRequest("/v1/auth/pin/activity"', self.frontend)
        self.assertIn('pinRequest("/v1/auth/pin/lock"', self.frontend)
        self.assertIn("Date.now()-PIN_LAST_LOCAL_ACTIVITY>=PIN_INACTIVITY_MS", self.frontend)

    def test_background_uses_server_checked_grace_without_a_finance_flash(self):
        self.assertIn("async function resumePinAfterBackground()", self.frontend)
        self.assertNotIn("function lockPinOnExit()", self.frontend)
        self.assertNotIn('window.addEventListener("pagehide",lockPinOnExit)', self.frontend)
        visibility_start = self.frontend.rindex('document.addEventListener("visibilitychange"')
        visibility = self.frontend[visibility_start:self.frontend.index('setInterval(()=>', visibility_start)]
        self.assertIn('document.getElementById("app")?.setAttribute("hidden","")', visibility)
        self.assertIn("resumePinAfterBackground();", visibility)
        resume = self.function_body("resumePinAfterBackground")
        self.assertLess(resume.index('setAttribute("hidden","")'), resume.index("fetchPinStatus()"))
        self.assertLess(resume.index("fetchPinStatus()"), resume.index('removeAttribute("hidden")'))

    def test_recovery_change_logout_and_locked_event_are_wired(self):
        for endpoint in (
            "/v1/auth/pin/setup",
            "/v1/auth/pin/unlock",
            "/v1/auth/pin/recover",
            "/v1/auth/pin/change",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, self.frontend)
        self.assertIn('data-pin="forgot"', self.frontend)
        self.assertIn('data-pin="logout"', self.frontend)
        self.assertIn('new CustomEvent("rove:api-locked")', self.frontend)
        self.assertIn('action("pin-change","App-PIN ändern"', self.frontend)

    def test_server_lockout_enters_login_then_normal_pin_setup(self):
        unlock = self.function_body("submitPinUnlock")
        self.assertIn('res.status===423||data.reauth_required', unlock)
        self.assertIn('showPinReauthenticationLogin()', unlock)
        self.assertNotIn('showPinScreen("reauth_required")', unlock)
        self.assertNotIn('location.reload', unlock)

        transition = self.function_body("showPinReauthenticationLogin")
        self.assertIn('clearPinEntry("pinUnlock")', transition)
        self.assertIn('showAppConnect()', transition)
        self.assertIn('PIN_SESSION_STATE="reauth_required"', transition)

        login = self.function_body("showAppConnect")
        self.assertIn('onboard.classList.remove("pin-mode")', login)
        self.assertIn('body.classList.remove("pin-lock-layout","pin-setup-layout")', login)

        password_login = self.function_body("passwordLogin")
        self.assertIn('continueWithSession(!!data.onboarding_required)', password_login)
        pin_status = self.function_body("fetchPinStatus")
        self.assertIn('data.pin_status==="setup_required"?"setup":data.pin_status', pin_status)

    def test_pin_recovery_screen_has_defined_shared_header(self):
        screen = self.function_body("showPinScreen")
        definition = 'const common=`<div class="ob-glow"></div><img class="ob-logo" src="logo.png" alt="Rov.E">`;'
        self.assertIn(definition, screen)
        self.assertLess(screen.index(definition), screen.index('${common}'))

    def test_no_retired_bearer_or_state_link_bypass_returns(self):
        for forbidden in ("ROVE_API.token", "Authorization: Bearer", "app-state", "state_url", "?state="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.frontend)


if __name__ == "__main__":
    unittest.main()
