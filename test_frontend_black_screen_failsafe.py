import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendBlackScreenFailsafeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_startup_auth_and_pin_requests_have_bounded_timeouts(self):
        self.assertIn("const STARTUP_AUTH_TIMEOUT_MS=8000;", self.frontend)
        self.assertIn("function requestWithTimeout(url,options={},timeoutMs=STARTUP_AUTH_TIMEOUT_MS)", self.frontend)
        self.assertIn("requestWithTimeout(`${PAIR_API_BASE_URL}/v1/auth/me`", self.frontend)
        self.assertIn('pinRequest("/v1/auth/pin/status",{timeoutMs:STARTUP_AUTH_TIMEOUT_MS})', self.frontend)

    def test_pin_timeout_option_is_not_forwarded_to_fetch(self):
        pin = re.search(
            r"function pinRequest\(path,options=\{\}\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(pin)
        body = pin.group("body")
        self.assertIn("const {timeoutMs, ...requestOptions}=options;", body)
        self.assertIn("requestOptions.headers", body)
        self.assertNotIn("timeoutMs:timeoutMs", body)

    def test_network_or_timeout_failure_exposes_auth_surface(self):
        restore = self.frontend.split("async function restoreEmailSession", 1)[1].split("\n}", 1)[0]
        self.assertIn("catch(e){ return {valid:false,definitive:false}; }", restore)
        pin_status = self.frontend.split("async function fetchPinStatus", 1)[1].split("\n}", 1)[0]
        self.assertIn('catch(_){ return {state:"login"}; }', pin_status)
        self.assertIn("const showStartupFallback=(message=", self.frontend)
        self.assertIn("showAppConnect();", self.frontend)
        self.assertIn("if(onboard) onboard.hidden=false;", self.frontend)
        self.assertIn("STARTUP_AUTH_UNCERTAIN=true;", self.frontend)

    def test_successful_boot_has_no_fallback_race(self):
        startup = self.frontend.split("if(APP_MODE===\"bridge\")", 1)[1].split(
            '} else if(APP_MODE==="profile")', 1
        )[0]
        self.assertIn('if(state==="ready") return true;', startup)
        self.assertIn('if(state==="login") showStartupFallback(', startup)
        self.assertIn(".catch(()=>{ showStartupFallback(); return null; });", startup)
        self.assertNotIn("Promise.race", startup)
        self.assertNotIn("setTimeout(r,4000)", startup)

    def test_splash_removal_has_no_hidden_app_and_hidden_onboarding_state(self):
        remove = self.frontend.split("const removeSplash=", 1)[1].split("};   // startet", 1)[0]
        self.assertIn("if(app?.hidden && ob?.hidden) showStartupFallback();", remove)

    def test_empty_optional_state_is_still_normalized(self):
        self.assertIn("DATA.assets = DATA.features.multi_cash_accounts_v1", self.frontend)
        self.assertIn("Array.isArray(b.goals) ? b.goals : []", self.frontend)
        self.assertIn("Array.isArray(b.vertraege) ? b.vertraege : []", self.frontend)


if __name__ == "__main__":
    unittest.main()
