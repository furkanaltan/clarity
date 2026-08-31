import copy
import unittest
from pathlib import Path

from report_story_v2 import build_report_story_v2
from report_html_renderer import _render_hell_pages, build_html_document
from rove_web_report_renderer import build_render_context, build_story_render_context, render_template
from test_report_story_v2 import standard_payload


ROOT = Path(__file__).resolve().parent
WEB_TEMPLATE = ROOT / "report_templates" / "rove_web_report.html"
PDF_TEMPLATE = ROOT / "report_templates" / "rove_pdf_report.html"


def report_payload() -> dict:
    data = standard_payload()
    data["report_story_v2"] = build_report_story_v2(data)
    return data


def july_truth_payload() -> dict:
    data = copy.deepcopy(standard_payload())
    data["meta"]["tracked_days"] = 24
    data["profile"].update({"savings_plan": 1000.0, "current_investments": 20849.0,
                            "cash_reserve": 9142.0, "property_equity": 9000.0})
    truth = data["report_truth"]
    truth["expenses"].update({
        "total_consumption": 1856.54,
        "transaction_count": 23,
        "previous_total_consumption": 1400.0,
        "categories": [
            {"category": "SHOPPING", "amount": 733.0, "transaction_count": 4,
             "avg_transaction": 183.25, "share": 39.48, "previous_amount": 200.0,
             "previous_transaction_count": 2},
            {"category": "LEBENSMITTEL", "amount": 348.0, "transaction_count": 8,
             "avg_transaction": 43.5, "share": 18.74, "previous_amount": 300.0,
             "previous_transaction_count": 7},
            {"category": "SONSTIGES", "amount": 340.0, "transaction_count": 3,
             "avg_transaction": 113.33, "share": 18.31, "previous_amount": 280.0,
             "previous_transaction_count": 2},
            {"category": "MOBILITAET", "amount": 217.54, "transaction_count": 3,
             "avg_transaction": 72.51, "share": 11.72, "previous_amount": 170.0,
             "previous_transaction_count": 2},
            {"category": "RESTAURANTS", "amount": 193.0, "transaction_count": 4,
             "avg_transaction": 48.25, "share": 10.40, "previous_amount": 300.0,
             "previous_transaction_count": 5},
            {"category": "PFLEGE", "amount": 25.0, "transaction_count": 1,
             "avg_transaction": 25.0, "share": 1.35, "previous_amount": 150.0,
             "previous_transaction_count": 2},
        ],
        "merchants": [
            {"merchant": "Shopping", "category": "SONSTIGES", "amount": 733.0,
             "transaction_count": 4, "avg_transaction": 183.25, "previous_amount": 200.0},
            {"merchant": "Breuninger", "category": "SHOPPING", "amount": 380.0,
             "transaction_count": 1, "avg_transaction": 380.0, "previous_amount": 0.0},
            {"merchant": "REWE", "category": "LEBENSMITTEL", "amount": 240.0,
             "transaction_count": 4, "avg_transaction": 60.0, "previous_amount": 210.0},
            {"merchant": "Spotify", "category": "FREIZEIT", "amount": 120.0,
             "transaction_count": 2, "avg_transaction": 60.0, "previous_amount": 120.0},
        ],
        "largest_expense": {"merchant": "Elektronikmarkt", "category": "SHOPPING", "amount": 500.0},
    })
    truth["budget"] = {
        "has_budgets": True, "on_track": False,
        "items": [{"category": "SHOPPING", "limit": 200.0, "used": 733.0, "over": True}],
    }
    truth["investments"]["contributions"] = {
        "net_contributions": 0.0, "recurring_in": 0.0, "one_time_in": 0.0,
        "out": 0.0, "events_count": 0, "by_asset": [],
    }
    truth["investments"]["holdings"] = []
    truth["wealth"] = {
        "total": 38991.0, "cash": 9142.0, "investments": 20849.0,
        "property_equity": 9000.0, "allocation": [], "reconciles": True,
        "goals_included": False,
    }
    truth["goals"] = {
        "primary": {"id": "dubai", "name": "Dubai Urlaub", "target_amount": 4000.0,
                    "current_amount": 51.0, "is_primary": True},
        "goals": [],
    }
    truth["score"] = {
        "clarity_score": 64,
        "parts": {"total": 64, "factors": [
            {"key": "budget", "n": "Budget Control", "points": 6, "max": 25},
            {"key": "savings", "n": "Savings Execution", "points": 25, "max": 25},
            {"key": "consistency", "n": "Tracking Consistency", "points": 16, "max": 25},
            {"key": "structure", "n": "Financial Structure", "points": 25, "max": 25},
        ]},
    }
    data["report_story_v2"] = build_report_story_v2(data)
    return data


class ReportRenderV2Tests(unittest.TestCase):
    def test_shared_context_is_snapshot_story_only(self):
        context = build_render_context(report_payload())
        self.assertIn("report", context)
        self.assertEqual(context["report"]["page_count"], 10)
        self.assertEqual(context["report"]["wealth_total"], "15.000 €")
        self.assertEqual(context["report"]["facts"][-1]["value"], "20,0 %")
        self.assertEqual(context["score_value"], 72)
        self.assertEqual(context["goal_desc"], "Dubai")

    def test_legacy_snapshot_uses_snapshot_only_wealth_fallback(self):
        data = standard_payload()
        data["report_truth"].pop("wealth")
        data["report_story_v2"] = build_report_story_v2(data)

        context = build_render_context(data)

        self.assertIn('data-count="15000"', context["net_worth_span"])
        self.assertEqual(context["report"]["wealth_total"], "15.000 €")

    def test_pre_truth_snapshot_uses_neutral_presentation_context(self):
        context = build_story_render_context({"meta": {"report_month": "2026-07"}, "pages": {}})

        self.assertEqual(context["month_label"], "Juli 2026")
        self.assertEqual(context["wealth_total"], "—")
        self.assertIn("ursprünglichen Fassung", context["pages"]["page_3"]["text"])

    def test_pre_truth_snapshot_maps_only_frozen_legacy_values(self):
        data = {
            "meta": {"report_month": "2026-07", "tracked_days": 24},
            "profile": {
                "income_total": 3500.0,
                "fixed_costs": 1200.0,
                "cash_reserve": 9167.0,
                "current_investments": 22299.0,
                "property_equity": 9000.0,
                "net_worth": 40466.0,
            },
            "pages": {
                "cover": {"freedom_step": 750.0},
                "financial_story": {
                    "cash": 9167.0,
                    "investments": 22299.0,
                    "net_worth": 40466.0,
                },
                "month": {"total_expenses": 2610.0},
                "wealth_journey": {"investment_summary": {"net_contributions": 750.0}},
                "goal": {
                    "description": "Dubai",
                    "target_amount": 4000.0,
                    "current_amount": 51.0,
                    "progress_percent": 1.275,
                },
                "score": {"clarity_score": 64},
                "money_map": {
                    "categories": [{"category": "restaurant", "total": 210.0}],
                    "insights": ["Restaurant war deine größte flexible Kategorie."],
                },
                "recap": {},
            },
        }

        context = build_story_render_context(data)

        self.assertEqual(context["wealth_total"], "40.466 €")
        self.assertEqual(context["consumption_total"], "2.610 €")
        self.assertEqual(context["contribution_total"], "750 €")
        self.assertEqual(context["income_total"], "3.500 €")
        self.assertEqual(context["fixed_costs_total"], "1.200 €")
        self.assertEqual(context["goal"]["current"], "51 €")
        self.assertEqual(context["categories"][0]["name"], "Restaurant")

    def test_goal_current_and_net_worth_are_distinct(self):
        context = build_render_context(report_payload())
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), report_payload())

        self.assertEqual(context["goal_current_amount"], "1.000 €")
        self.assertEqual(context["net_worth_amount"], "15.000 €")
        self.assertIn("Aktueller Stand</div><div", html)
        self.assertIn(">1.000 €</div>", html)

    def test_web_renders_exactly_ten_pages_with_localized_copy(self):
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), report_payload())
        self.assertEqual(html.count("<section data-screen-label="), 10)
        self.assertIn("Juli 2026", html)
        self.assertIn("800 € investiert oder zurückgelegt", html)
        self.assertRegex(html, r'data-screen-label="02 (Überblick|Dein Geldfluss)"')
        self.assertIn('data-screen-label="03 Deine Kategorien"', html)
        self.assertIn('data-screen-label="04 Händler und Ausgabenmuster"', html)
        self.assertIn("Rov.E", html)
        self.assertNotIn("800.00 EUR", html)

    def test_web_uses_legacy_visual_language_without_developer_copy(self):
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), report_payload())
        self.assertIn('family=Newsreader', html)
        self.assertIn('family=Hanken+Grotesk', html)
        self.assertIn('@keyframes clarity-hero-in', html)
        self.assertIn('@keyframes clarity-drift', html)
        self.assertIn('@keyframes roveRingPulse', html)
        self.assertIn('data-ring=', html)
        self.assertIn('data-count=', html)
        self.assertIn('data-rove-assistant', html)
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
        self.assertNotIn("Noch kein primäres Ziel ausgewählt", html)
        self.assertNotIn("Beitrag: 0 €", html)

    def test_july_truth_pass_uses_merchant_budget_and_zero_guards(self):
        data = july_truth_payload()
        context = build_render_context(data)
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)

        self.assertEqual(context["biggest_name"], "Breuninger")
        self.assertEqual(context["biggest_amount"], "380 €")
        self.assertNotEqual(context["biggest_name"], data["report_truth"]["expenses"]["largest_expense"]["merchant"])
        self.assertEqual([item["name"] for item in context["merchant_rows"]], ["Breuninger", "REWE", "Spotify"])
        self.assertEqual(context["merchant_rows"][1]["count_text"], "4 Ausgaben")
        self.assertEqual(context["merchant_rows"][1]["average"], "60 €")
        self.assertEqual(context["merchant_rows"][1]["category"], "Lebensmittel")
        self.assertNotIn(" · Sonstiges", html)
        self.assertIn(">24 Tagen</span> aktiv getrackt", html)
        self.assertIn(">Shopping</span> mit 733 €.", html)
        self.assertIn(
            "Zieltöpfe zeigen nur, wofür Geld reserviert ist. Sie erhöhen dein Vermögen nicht zusätzlich.",
            html,
        )
        self.assertNotIn("Zieltöpfe sind Zweckbindungen", html)
        self.assertIn("533 € über deinem gesetzten Budget von 200 €", context["recap_attention_text"])
        self.assertEqual(context["freedom_step_text"], "Kein neuer Beitrag")
        self.assertIn("kein neuer Investment- oder Sparbeitrag", context["build_summary_text"])
        self.assertIn("Spar-Teilscore liegt bei 25/25", context["rank_blurb"])
        self.assertEqual(context["goal_title_text"], "Dein Ziel: Dubai Urlaub.")
        self.assertEqual(html.count("Dubai Urlaub"), 2)
        self.assertNotIn("Dein Ziel: Dubai Urlaub.", html)
        self.assertIn(">Noch</div>", html)
        self.assertIn(context["goal_remaining_amount"], html)
        self.assertIn("white-space: nowrap;", html)
        self.assertIn("Noch 1.009 €", context["milestone_headline"])
        self.assertIn("Shopping-Budget von 200 €", context["plan_step2_title"])
        self.assertIn("geplante Sparrate von 1.000 €", context["plan_step3_title"])
        self.assertEqual(context["comparison_rows"][0]["name"], "Shopping")
        self.assertEqual(context["comparison_rows"][0]["amount_text"], "+533 €")
        self.assertEqual(
            [item["name"] for item in context["money_map_categories"]],
            ["Shopping", "Lebensmittel", "Sonstiges", "Mobilität", "Restaurant", "Pflege"],
        )
        self.assertEqual(context["money_map_category_count"], 6)
        self.assertEqual(context["money_map_transaction_count"], 23)
        self.assertIn('data-screen-label="05 Money Map"', html)
        self.assertIn(">Vormonatsvergleich</div>", html)
        self.assertEqual(html.count("Shopping lag 533 € über deinem gesetzten Budget von 200 €."), 1)
        for category in context["money_map_categories"]:
            self.assertIn(category["name"], html)
            self.assertIn(category["amount_text"], html)
            self.assertIn(f'{category["pct_text"]} %', html)
        self.assertIn("Rov.E zeigt nur relevante Kategorien aus deinem Snapshot.", html)
        for phrase in (
            "dokumentierter Beitrag",
            "Konsum und Marktbewegung",
            "Die Rechnung verwendet",
            "wird nicht hochgerechnet",
        ):
            self.assertNotIn(phrase, html)
        for forbidden in (
            "unter 600", "+366 €/Monat", "4.398 €/Jahr", "wichtiger Baustein",
            "Budget stimmt", "Sparen läuft", "Das allein hebt dich", "wieder in den Griff",
            "dringend",
        ):
            self.assertNotIn(forbidden, html)

    def test_money_map_stays_complete_without_merchant_data(self):
        with_merchants = july_truth_payload()
        without_merchants = copy.deepcopy(with_merchants)
        without_merchants["report_truth"]["expenses"]["merchants"] = []
        without_merchants["report_story_v2"] = build_report_story_v2(without_merchants)

        complete_map = build_render_context(with_merchants)["money_map_categories"]
        map_without_merchants = build_render_context(without_merchants)["money_map_categories"]

        self.assertEqual(complete_map, map_without_merchants)
        self.assertEqual(len(map_without_merchants), 6)

    def test_comparison_percent_is_derived_from_frozen_values_when_missing(self):
        data = july_truth_payload()
        change = data["report_story_v2"]["pages"]["page_5"]["supporting_metrics"][0]
        change["delta_percent"] = None

        comparison = build_render_context(data)["comparison_rows"][0]

        self.assertNotEqual(comparison["pct_label"], "Vergleich")
        self.assertTrue(comparison["pct_label"].endswith(" %"))

    def test_pdf_keeps_design_shell_and_uses_v2_truth_fields(self):
        data = july_truth_payload()
        html = build_html_document(_render_hell_pages(data))

        self.assertEqual(html.count('data-screen-label="'), 10)
        self.assertIn("#EAF3FB", html)
        self.assertIn("#F7F6F3", html)
        self.assertIn("#155681", html)
        self.assertNotIn("#42b992", html.lower())
        self.assertNotIn("#3d8b5b", html.lower())
        self.assertIn("Shopping", html)
        self.assertIn("733 €", html)
        self.assertIn("39,5 %", html)
        self.assertIn("Lebensmittel", html)
        self.assertIn("348 €", html)
        self.assertIn("18,7 %", html)
        self.assertIn("533 € über deinem gesetzten Budget von 200 €", html)
        self.assertIn("Breuninger", html)
        self.assertIn("REWE", html)
        self.assertIn("Spotify", html)
        self.assertIn("+533 €", html)
        self.assertIn("+266,5 %", html)
        self.assertIn("Kein neuer Beitrag", html)
        self.assertEqual(html.count("38.991 €"), 1)
        self.assertIn("9.142 €", html)
        self.assertIn("20.849 €", html)
        self.assertIn("9.000 €", html)
        self.assertIn("Controller", html)
        self.assertIn("Budget Control", html)
        self.assertIn(">6<span", html)
        self.assertIn(">/25</span>", html)
        self.assertIn("Savings Execution", html)
        self.assertIn(">25<span", html)
        self.assertIn("Dubai Urlaub", html)
        self.assertIn("51 €", html)
        self.assertIn("3.949 €", html)
        self.assertNotIn("38.991 €</div><div style=\"font-size:14px;color:#75757B;margin-top:9px;line-height:1.35;\">von 4.000 €", html)
        self.assertEqual(html.count("Dubai Urlaub"), 1)
        for phrase in (
            "Dokumentierte Investmentbeiträge",
            "keine erfundene Marktperformance",
            "Truth Layer",
            "Snapshot",
            "deterministisch",
            "Market Movement",
            "+366 €/Monat",
            "+4.398 €/Jahr",
            "wichtiger Baustein",
            "Sparen läuft",
            "Das allein hebt dich",
            "dringend",
            "Bei deiner geplanten Sparrate",
            "rund 4 Monaten",
        ):
            self.assertNotIn(phrase, html)


if __name__ == "__main__":
    unittest.main()
