from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendSupportOfferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = SOURCE.read_text(encoding="utf-8")

    def test_three_existing_offers_have_distinct_visual_roles(self):
        self.assertIn('class="support-card access-offer"', self.frontend)
        self.assertIn(
            'class="support-card ${key === "reset" ? "reset-offer" : "guidance-offer"}"',
            self.frontend,
        )
        self.assertIn('key === "reset" ? "Starker Einstieg" : o.tag', self.frontend)

    def test_offer_routes_and_content_remain_present(self):
        self.assertIn('data-support-access', self.frontend)
        self.assertIn('data-support="${key}"', self.frontend)
        self.assertIn('function renderSupportDetail(key)', self.frontend)
        self.assertIn('function openAccess()', self.frontend)


if __name__ == "__main__":
    unittest.main()
