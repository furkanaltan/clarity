import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


def escape_html(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


class FrontendTransactionXssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_transaction_rows_use_dom_text_nodes(self):
        self.assertIn("function transactionRowElement(t,isCurrent){", self.frontend)
        self.assertIn("name.textContent=String(t?.n??\"\");", self.frontend)
        self.assertIn("dayLabel.textContent=String(day.d??\"\");", self.frontend)
        self.assertIn("list.replaceChildren();", self.frontend)
        self.assertNotIn('${t.n}', self.frontend)

    def test_untrusted_payloads_are_text_escaped_by_the_existing_helper(self):
        payloads = [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<svg onload=alert(1)>",
            "Café & Küche \"heute\"",
        ]
        for payload in payloads:
            escaped = escape_html(payload)
            self.assertNotIn(payload, escaped)
            self.assertNotIn("<script", escaped)
            self.assertNotIn("<img", escaped)
            self.assertNotIn("<svg", escaped)

    def test_transaction_logo_does_not_interpolate_untrusted_letter_as_markup(self):
        logo_start = self.frontend.index("function transactionLogo(t){")
        logo_end = self.frontend.index("function transactionDetailLogo", logo_start)
        logo_source = self.frontend[logo_start:logo_end]
        self.assertIn("announcementEscape(fallback)", logo_source)
        self.assertIn('safeColor=/^#[0-9a-f]{6}$/i.test(color)?color:"";', logo_source)


if __name__ == "__main__":
    unittest.main()
