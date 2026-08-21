import copy
import unittest
from pathlib import Path

from report_story_v2 import build_report_story_v2
from rove_web_report_renderer import build_render_context, render_template
from test_report_story_v2 import standard_payload


ROOT = Path(__file__).resolve().parent
WEB_TEMPLATE = ROOT / "report_templates" / "rove_web_report.html"


def report_payload() -> dict:
    data = standard_payload()
    data["report_story_v2"] = build_report_story_v2(data)
    return data


class ReportRenderV2Tests(unittest.TestCase):
    def test_shared_context_is_snapshot_story_only(self):
        context = build_render_context(report_payload())
        self.assertEqual(list(context), ["report"])
        self.assertEqual(context["report"]["page_count"], 10)
        self.assertEqual(context["report"]["wealth_total"], "15.000 €")
        self.assertEqual(context["report"]["facts"][-1]["value"], "20,0 %")

    def test_web_renders_exactly_ten_pages_with_localized_copy(self):
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), report_payload())
        self.assertEqual(html.count('<section class="page"'), 10)
        self.assertIn("Juli 2026", html)
        self.assertIn("Dein wiederkehrender Beitrag von 800 €", html)
        self.assertIn('data-screen-label="05 Was hat sich verändert"', html)
        self.assertIn('data-screen-label="09 Rov.E Insight"', html)
        self.assertNotIn("800.00 EUR", html)
        self.assertNotIn("Plan für Juli", html)

    def test_web_uses_legacy_visual_language_without_developer_copy(self):
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), report_payload())
        self.assertIn('family=Newsreader', html)
        self.assertIn('family=Hanken+Grotesk', html)
        self.assertIn('@keyframes clarity-hero-in', html)
        self.assertIn('@keyframes clarity-drift', html)
        self.assertIn('class="glass money-overview reveal"', html)
        self.assertIn('class="glass ranking-card reveal"', html)
        for phrase in (
            "Keine erfundene Marktperformance",
            "dokumentierte Investmentbeiträge",
            "Truth Layer",
            "deterministisch",
            "Market Movement nicht verfügbar",
        ):
            self.assertNotIn(phrase, html)

    def test_empty_investments_and_goals_degrade_without_fake_values(self):
        data = copy.deepcopy(standard_payload())
        data["profile"]["current_investments"] = 0.0
        data["report_truth"]["investments"] = {
            "contributions": {"net_contributions": 0.0, "recurring_in": 0.0, "by_asset": []},
            "market_movement": {"amount": None, "available": False},
            "holdings": [],
        }
        data["report_truth"]["goals"] = {"primary": None, "goals": []}
        data["report_story_v2"] = build_report_story_v2(data)
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        self.assertNotIn("keine Sparleistung erfunden", html)
        self.assertNotIn("keine erfundene Marktperformance", html)
        self.assertIn("Keine Investmentbeiträge in diesem Monat", html)
        self.assertNotIn("Noch kein primäres Ziel ausgewählt", html)
        self.assertNotIn("Beitrag: 0 €", html)


if __name__ == "__main__":
    unittest.main()
