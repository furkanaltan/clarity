import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class AdminStepUpFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "frontend/index.html").read_text()

    def test_fresh_step_up_response_opens_existing_pin_surface(self):
        self.assertIn("function handleApiSessionStatus(status,body=null)", self.html)
        self.assertIn('body?.error==="fresh_step_up_required"', self.html)
        self.assertIn('new CustomEvent("rove:api-step-up-required")', self.html)
        self.assertIn('showPinScreen("step_up_required")', self.html)

    def test_api_fetch_inspects_423_body_without_consuming_response(self):
        self.assertIn(
            "response.clone().json().catch(()=>null)",
            self.html,
        )
        self.assertIn("handleApiSessionStatus(response.status,statusBody)", self.html)

    def test_step_up_surface_uses_pin_unlock_field(self):
        marker = 'mode==="step_up_required"'
        start = self.html.index(marker)
        end = self.html.index('}else{', start)
        section = self.html[start:end]
        self.assertIn('pinField("pinUnlock"', section)
        self.assertIn("sensible Aktion", section)


if __name__ == "__main__":
    unittest.main()
