import copy
import sqlite3
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from weasyprint import CSS, HTML

from report_story_v2 import build_report_story_v2
from report_html_renderer import _pdf_design_details, _pdf_monthly_recap, _render_hell_pages, build_html_document, plan_items, render_777_score
from rove_web_report_renderer import build_render_context, build_story_render_context, render_template as render_web_template
from rove_consumer_debt import net_worth_total
import rove_pdf_report_renderer
import report_engine
import rove_app_state
from rove_financial_accounts import ensure_financial_accounts_schema, set_feature_enabled
from test_report_story_v2 import standard_payload


ROOT = Path(__file__).resolve().parent
WEB_TEMPLATE = ROOT / "report_templates" / "rove_web_report.html"
PDF_TEMPLATE = ROOT / "report_templates" / "rove_pdf_report.html"


def render_template(template: str, data: dict) -> str:
    # Content assertions ignore currency's non-breaking presentation space.
    return render_web_template(template, data).replace("\u00a0", " ")


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
    truth["savings"] = {
        "actual_amount": 0.0, "confirmed": False,
        "automatic_etf_amount": 0.0, "source": "unconfirmed",
    }
    truth["investments"]["holdings"] = []
    truth["cash"].update({"current_cash": 9142.0, "account_total": 9142.0})
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
        "parts": {"total": 64, "score_version": 2, "factors": [
            {"key": "budget", "n": "Budget / Cashflow", "points": 6, "max": 20},
            {"key": "savings", "n": "Savings Rate", "points": 20, "max": 20},
            {"key": "liquidity", "n": "Liquidity", "points": 13, "max": 20},
            {"key": "debt", "n": "Debt Structure", "points": 15, "max": 30},
            {"key": "tracking", "n": "Tracking / Data Quality", "points": 10, "max": 10},
        ]},
    }
    data["report_story_v2"] = build_report_story_v2(data)
    return data


def july_review_payload() -> dict:
    data = july_truth_payload()
    truth = data["report_truth"]
    data["profile"].update({
        "current_investments": 20478.0,
        "property_equity": 10500.0,
        "property_remaining_debt": 80000.0,
        "total_consumer_debt": 1500.0,
    })
    truth["property"]["equity"] = 10500.0
    truth["wealth"].update({
        "total": 38620.0,
        "investments": 20478.0,
        "property_equity": 10500.0,
        "total_consumer_debt": 1500.0,
        "reconciles": False,
    })
    truth["wealth_period"] = {
        "opening_net_worth": 37200.0,
        "closing_net_worth": 38620.0,
        "change_amount": 1420.0,
        "change_percent": 3.8,
        "available": True,
        "closing_source": "live_canonical",
        "closing_label": "Stand aktuell",
    }
    truth["investment_period"] = {
        "amount": 20478.0, "available": True,
        "source": "live_canonical",
    }
    truth["buffer"] = {
        "available_cash": 9142.0, "monthly_basis": 4000.0,
        "basis_type": "fixed_costs", "covered_months": 9142.0 / 4000.0,
        "target_amount": 10000.0, "gap": 858.0,
        "previous_covered_months": 2.1, "source": "canonical_current",
    }
    truth["debts"] = {
        "consumer_current": 1500.0, "mortgage_current": 80000.0,
        "net_debt_reduction": 300.0, "has_relevant_debt": True,
    }
    truth["investments"]["contributions"] = {
        "net_contributions": 500.0, "events_count": 1,
        "recurring_in": 500.0, "one_time_in": 0.0, "out": 0.0, "by_asset": [],
    }
    truth["build"] = {
        "available": True,
        "components": [
            {"key": "cash_change", "label": "Cash-Veränderung", "amount": -100.0},
            {"key": "investment_contributions", "label": "Belegte Investmentbeiträge, netto", "amount": 500.0},
            {"key": "net_debt_reduction", "label": "Netto-Schuldenveränderung", "amount": 300.0},
        ],
        "market_movement": {"amount": None, "available": False},
    }
    data["report_story_v2"] = build_report_story_v2(data)
    return data


def july_final_sample_payload() -> dict:
    """Coherent presentation sample; keep incomplete fixtures for negative tests."""
    data = july_review_payload()
    truth = data["report_truth"]
    expenses = truth["expenses"]
    data["meta"]["tracked_days"] = 20
    expenses["tracked_days"] = 20
    expenses["merchants"] = [
        {"merchant": merchant, "category": category, "amount": amount,
         "transaction_count": count, "avg_transaction": average,
         "previous_amount": previous}
        for merchant, category, amount, count, average, previous in (
            ("Elektronikmarkt", "SHOPPING", 500.0, 1, 500.0, 0.0),
            ("Breuninger", "SHOPPING", 380.0, 1, 380.0, 0.0),
            ("REWE", "LEBENSMITTEL", 240.0, 4, 60.0, 210.0),
            ("Tankstelle", "MOBILITAET", 217.54, 3, 72.51, 170.0),
            ("Bistro", "RESTAURANTS", 193.0, 4, 48.25, 300.0),
            ("ALDI", "LEBENSMITTEL", 108.0, 4, 27.0, 90.0),
            ("Buchladen", "SHOPPING", 103.0, 2, 51.5, 200.0),
            ("Postfiliale", "SONSTIGES", 90.0, 3, 30.0, 280.0),
            ("Drogerie", "PFLEGE", 25.0, 1, 25.0, 150.0),
        )
    ]
    for category in expenses["categories"]:
        if category["category"] == "SHOPPING":
            category.update({"amount": 983.0, "avg_transaction": 245.75, "share": 52.95})
        elif category["category"] == "SONSTIGES":
            category.update({"amount": 90.0, "avg_transaction": 30.0, "share": 4.85})
    expenses["classification_totals"]["consumption"] = 1856.54
    expenses["largest_expense"].update({"created_at": "2026-07-18 14:30:00"})
    truth["budget"]["items"][0]["used"] = 983.0
    truth["savings"].update({
        "actual_amount": 500.0, "confirmed": True,
        "automatic_etf_amount": 500.0, "source": "month_close",
    })
    journey = data["pages"]["wealth_journey"]
    journey["monthly_execution"]["savings_confirmed"] = True
    journey["savings_progress"].update({"full_plan_confirmed": True, "full_plan_amount": 500.0})
    truth["buffer"].update({"monthly_basis": 1500.0, "covered_months": 9142.0 / 1500.0})
    data["report_story_v2"] = build_report_story_v2(data)
    return data


def legacy_archive_payload() -> dict:
    return {
        "meta": {"report_month": "2026-07", "tracked_days": 18},
        "profile": {
            "savings_plan": 500.0, "current_investments": 9000.0,
            "cash_reserve": 6000.0, "property_equity": 0.0,
            "net_worth": 15000.0, "total_consumer_debt": 0.0,
        },
        "pages": {
            "cover": {"freedom_step": 500.0, "development_percent": None},
            "financial_story": {"net_worth": 15000.0, "cash": 6000.0, "investments": 9000.0},
            "month": {"total_expenses": 900.0, "tracked_days": 18,
                      "strongest_category": None, "biggest_expense": None},
            "score": {"clarity_score": 72, "rank_name": "Controller", "parts": {}},
            "goal": {"description": "Dubai", "target_amount": 4000.0, "current_amount": 100.0,
                     "progress_percent": 2.5, "goal_monthly_rate": None},
            "money_map": {"categories": [], "insights": []},
            "wealth_journey": {
                "points": [{"month": "2026-06", "net_worth": 12000.0, "clarity_score": None}],
                "investment_summary": {"net_contributions": 500.0},
                "savings_progress": {},
            },
            "milestones": {"badges": []},
            "recap": {},
            "budget": {},
        },
    }


class ReportRenderV2Tests(unittest.TestCase):
    def test_goal_forecast_uses_only_explicit_goal_rate(self):
        data = july_truth_payload()
        data["profile"]["savings_plan"] = 1000.0
        data["report_truth"]["goals"]["primary"].update({
            "target_amount": 4000.0,
            "current_amount": 0.0,
            "goal_monthly_rate": 50.0,
        })
        data["report_story_v2"] = build_report_story_v2(data)

        context = build_render_context(data)
        self.assertIn("80 Monaten", context["goal_honest_text"])
        self.assertEqual(context["plan_step3_impact"], "Sparziel 50 €")
        self.assertEqual(context["plan_step3_target"], "50 €")
        self.assertIn("Rate für Dubai Urlaub von 50 €", context["plan_step3_title"])
        self.assertIn("80 Monaten", render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data))
        self.assertEqual(len(_render_hell_pages(data)), 10)

        data["report_truth"]["goals"]["primary"]["goal_monthly_rate"] = None
        data["report_story_v2"] = build_report_story_v2(data)
        context_without_rate = build_render_context(data)
        self.assertNotIn("4 Monaten", context_without_rate["goal_honest_text"])
        self.assertNotIn("80 Monaten", context_without_rate["goal_honest_text"])
        self.assertEqual(context_without_rate["plan_step3_impact"], "Zielrate")
        self.assertEqual(context_without_rate["plan_step3_target"], "—")
        self.assertEqual(context_without_rate["plan_step3_title"], "Lege eine monatliche Rate für dein Ziel fest.")
        self.assertNotIn("1.000 €", context_without_rate["plan_step3_title"])
        self.assertNotIn("Sparziel 1.000 €", context_without_rate["plan_step3_impact"])
        no_rate_web = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        no_rate_pdf = "".join(_render_hell_pages(data))
        for rendered in (no_rate_web, no_rate_pdf):
            self.assertIn("Lege eine monatliche Rate für dein Ziel fest.", rendered)
            self.assertNotIn("geplante Sparrate von 1.000 €", rendered)
            self.assertNotIn("Sparziel 1.000 €", rendered)
        self.assertEqual(len(_render_hell_pages(data)), 10)

    def test_negative_confirmed_savings_stays_signed_in_web_and_pdf(self):
        data = july_truth_payload()
        data["report_truth"]["savings"].update({"actual_amount": -100.0, "confirmed": True})
        data["report_story_v2"] = build_report_story_v2(data)

        context = build_render_context(data)

        self.assertEqual(context["report"]["contribution_total"], "-100 €")
        self.assertEqual(context["freedom_step_text"], "-100 €")
        self.assertIn("-100 €", "".join(_render_hell_pages(data)))

    def test_monthly_financial_snapshot_freezes_inputs_and_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, income REAL, other_income REAL,
                fixed_costs REAL, etf_savings REAL, cash_savings REAL,
                current_cash REAL, current_investments REAL
            )"""
        )
        conn.execute(
            """INSERT INTO users VALUES (1, 4430, 0, 2105.32, 300, 700, 7000, 12000)"""
        )
        ensure_financial_accounts_schema(conn)
        set_feature_enabled(conn, 1, "multi_cash_accounts_v1", True)
        conn.executemany(
            """INSERT INTO app_financial_accounts
               (user_id, account_type, name, currency, balance, legacy_key, source, status)
               VALUES (1, ?, ?, 'EUR', ?, ?, 'manual', 'active')""",
            [("checking", "Girokonto", 2000, "giro"), ("savings", "Tagesgeld", 5000, "tagesgeld")],
        )
        conn.execute(
            """CREATE TABLE app_properties (
                user_id INTEGER PRIMARY KEY, market_value REAL, remaining_debt REAL,
                monthly_rate REAL, house_fee REAL, management_fee REAL
            )"""
        )
        conn.execute("INSERT INTO app_properties VALUES (1, 180000, 170000, 0, 0, 0)")
        conn.execute("CREATE TABLE monthly_snapshots (user_id INTEGER, month TEXT, clarity_score INTEGER)")
        conn.execute("INSERT INTO monthly_snapshots VALUES (1, '2026-08', 64)")

        first = rove_app_state.capture_monthly_financial_snapshot(conn, 1, "2026-08", 0)
        self.assertEqual(first["cash_total"], 7000.0)
        self.assertEqual(first["property_equity"], 10000.0)
        self.assertEqual(first["net_worth"], 29000.0)
        self.assertEqual(first["clarity_score"], 64)

        conn.execute("UPDATE users SET income = 9999, current_cash = 1, current_investments = 1")
        second = rove_app_state.capture_monthly_financial_snapshot(conn, 1, "2026-08", 999)
        self.assertEqual(first, second)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM monthly_financial_snapshots").fetchone()[0], 1
        )

    def test_historical_report_truth_without_snapshot_is_unavailable(self):
        wealth = report_engine._report_wealth_truth(
            {"current_investments": None, "property_equity": None},
            {"current_cash": None, "available": False},
            {"market_value": {"amount": None, "available": False}, "holdings": []},
        )
        self.assertIsNone(wealth["total"])
        self.assertFalse(wealth["available"])
        story = build_report_story_v2({
            "meta": {"report_month": "2026-08"},
            "profile": {"savings_plan": None},
            "pages": {"goal": {}, "wealth_journey": {}},
            "report_truth": {
                "income": {"amount": None},
                "fixed_costs": {"amount": None},
                "expenses": {"total_consumption": 0, "categories": [], "merchants": []},
                "cash": {"available": False, "current_cash": None},
                "investments": {"market_value": {"amount": None, "available": False}, "holdings": {}, "contributions": {}},
                "property": {"equity": None},
                "wealth": wealth,
                "savings": {"actual_amount": 0, "confirmed": False},
                "score": {"clarity_score": None, "parts": {"total": None}},
                "budget": {}, "goals": {},
            },
        })
        self.assertFalse(story["pages"]["page_6"]["available"])
        self.assertFalse(story["pages"]["page_8"]["available"])

    def test_property_equity_keeps_signed_value_and_net_worth(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE app_properties (user_id INTEGER, market_value REAL, remaining_debt REAL)")

        with patch.object(report_engine, "get_db", return_value=conn):
            conn.execute("INSERT INTO app_properties VALUES (1, 180000, 170000)")
            self.assertEqual(report_engine.get_app_property_equity(1), 10000.0)
            conn.execute("UPDATE app_properties SET market_value=250000, remaining_debt=280000 WHERE user_id=1")
            self.assertEqual(report_engine.get_app_property_equity(1), -30000.0)

        wealth = report_engine._report_wealth_truth(
            {"current_investments": 0.0, "property_equity": -30000.0},
            {"current_cash": 10000.0},
            {"holdings": []},
        )
        self.assertEqual(wealth["total"], -20000.0)
        self.assertTrue(wealth["allocation_excludes_negative_property_equity"])
        self.assertEqual([item["label"] for item in wealth["allocation"]], ["Cash"])

    def test_top_merchants_keep_unbroken_names_inside_mobile_card(self):
        template = WEB_TEMPLATE.read_text(encoding="utf-8")
        styles = re.search(r"<style>(.*?)</style>", template, re.DOTALL).group(1)
        merchant_rows = "".join(
            f'''<div class="report-merchant-row"><div class="report-merchant-rank">{rank}</div>
                <div style="min-width:0"><div class="report-merchant-name">{name}</div>
                <div class="report-merchant-meta">12 Ausgaben · Ø 102,88 €</div></div>
                <div class="report-merchant-amount">{amount}</div></div>'''
            for rank, name, amount in (
                (1, "Aldi", "-12,50 €"),
                (2, "Deutsche Bahn", "-915,33 €"),
                (3, "Restaurant Zum Goldenen Stern", "303,00 €"),
                (4, "SehrLangerHaendlernameOhneLeerzeichen123456789", "-1.249,99 €"),
                (5, "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789", "360,00 €"),
            )
        )
        html = f"<style>{styles}</style><div class=\"report-merchant-card\">{merchant_rows}</div>"

        for width in (320, 360, 375, 390, 430):
            document = HTML(string=html).render(
                stylesheets=[CSS(string=f"@page {{ size: {width}px 1600px; margin: 0; }}")]
            )
            self.assertEqual(len(document.pages), 1, width)
            for box in document.pages[0]._page_box.descendants():
                self.assertLessEqual(box.position_x + box.width, width + 0.1, width)

    def test_comparison_rows_keep_badges_and_deltas_aligned(self):
        template = WEB_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) minmax(92px, max-content) minmax(148px, max-content);",
            template,
        )
        self.assertIn('grid-template-areas:\n          "label amount"\n          "badge amount";', template)
        self.assertIn(".report-comparison-badge {", template)
        self.assertIn("width: max-content !important;", template)
        self.assertIn("width: 148px !important;", template)

        comparison_rows = "".join(
            f'''<div class="report-comparison-row"><div class="report-comparison-label">{label}</div>
                <div class="report-comparison-bar"><div class="report-comparison-badge">{badge}</div></div>
                <div class="report-comparison-amount">{amount}</div></div>'''
            for label, badge, amount in (
                ("Shopping", "+266,5 %", "+533 €"),
                ("Gesamtkonsum", "+32,6 %", "+456,54 €"),
                ("Breuninger", "Vergleich", "+380 €"),
                ("Sehr lange Ausgabenkategorie ohne kuerzeren Namen", "-96,4 %", "-1.249,99 €"),
                ("ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789", "-7,2 %", "-915,33 €"),
            )
        )
        layout_css = """
            * { box-sizing: border-box; }
            body { margin: 0; }
            .report-comparison-card { padding: 30px 20px; }
            .report-comparison-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(92px, max-content) minmax(148px, max-content); gap: 16px; align-items: center; }
            .report-comparison-label { min-width: 0; overflow-wrap: anywhere; font: 13px sans-serif; }
            .report-comparison-bar { width: auto; min-width: 0; height: auto; overflow: visible; }
            .report-comparison-badge { width: auto; min-width: 92px; height: 32px; padding: 0 12px; white-space: nowrap; font: 12px sans-serif; }
            .report-comparison-amount { width: 148px; min-width: 148px; text-align: right; white-space: nowrap; font: 24px sans-serif; }
        """
        mobile_css = """
            .report-comparison-row { grid-template-columns: minmax(0, 1fr) max-content; grid-template-areas: 'label amount' 'badge amount'; gap: 8px 12px; }
            .report-comparison-label { grid-area: label; }
            .report-comparison-bar { grid-area: badge; }
            .report-comparison-badge { width: max-content; min-width: 86px; }
            .report-comparison-amount { grid-area: amount; width: auto; min-width: 0; }
        """
        html = f"<style>{layout_css}</style><div class=\"report-comparison-card\">{comparison_rows}</div>"

        for width, css in ((1280, layout_css), (1440, layout_css), (320, layout_css + mobile_css),
                           (360, layout_css + mobile_css), (375, layout_css + mobile_css),
                           (390, layout_css + mobile_css), (430, layout_css + mobile_css)):
            document = HTML(string=html).render(stylesheets=[CSS(string=f"{css} @page {{ size: {width}px 1600px; margin: 0; }}")])
            for page in document.pages:
                for box in page._page_box.descendants():
                    element = getattr(box, "element", None)
                    classes = (element.get("class") or "").split() if element is not None else []
                    if any(name.startswith("report-comparison-") for name in classes):
                        self.assertLessEqual(box.position_x + box.width, width + 0.1, width)

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
        payload = report_payload()
        context = build_render_context(payload)
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), payload)
        self.assertEqual(html.count("<section data-screen-label="), 10)
        self.assertIn("Juli 2026", html)
        self.assertEqual(context["freedom_step_text"], "+800 €")
        self.assertEqual(context["freedom_step_subline"], "tatsächlich gespart")
        self.assertIn("+800 €", html)
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
        self.assertIn('data-ring-circumference="540.4"', html)
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

    def test_pdf_wealth_allocation_supports_six_asset_classes(self):
        data = report_payload()
        data["report_truth"]["wealth"]["allocation"] = [
            {"key": "cash:giro", "label": "Girokonto", "asset_class": "checking",
             "amount": 737.69, "share": 1.8},
            {"key": "cash:savings", "label": "Tagesgeld", "asset_class": "savings",
             "amount": 7118.0, "share": 17.6},
            {"key": "cash:wallet", "label": "Bargeld", "asset_class": "cash",
             "amount": 40.0, "share": 0.1},
            {"key": "investment:crypto", "label": "Krypto", "asset_class": "investment",
             "amount": 12090.41, "share": 29.9},
            {"key": "investment:etfs", "label": "ETFs", "asset_class": "investment",
             "amount": 8500.0, "share": 21.0},
            {"key": "investment:stocks", "label": "Aktien", "asset_class": "investment",
             "amount": 11905.9, "share": 29.6},
        ]
        data["report_story_v2"] = build_report_story_v2(data)

        html = build_html_document(_render_hell_pages(data))

        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", html)
        for label in ("Girokonto", "Tagesgeld", "Bargeld", "Krypto", "ETFs", "Aktien"):
            self.assertIn(label, html)

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
        self.assertIn("24 Tage erfasst", html)
        self.assertIn("Shopping", html)
        self.assertNotIn("Deine stärkste Kategorie war Shopping mit 733 €", html)
        self.assertNotIn("Zieltöpfe zeigen nur, wofür Geld reserviert ist", html)
        self.assertNotIn("Zieltöpfe sind Zweckbindungen", html)
        self.assertIn("533 € über deinem gesetzten Budget von 200 €", context["recap_attention_text"])
        self.assertEqual(context["freedom_step_text"], "Kein neuer Beitrag")
        self.assertIn("keine Veränderungen erfasst", context["build_summary_text"])
        self.assertIn("Spar-Teilscore liegt bei 20/20", context["rank_blurb"])
        self.assertEqual(context["goal_title_text"], "Dubai Urlaub")
        self.assertEqual(html.count("Dubai Urlaub"), 1)
        self.assertNotIn("Dein Ziel: Dubai Urlaub.", html)
        self.assertIn("Noch offen", html)
        self.assertIn("white-space: nowrap;", html)
        self.assertIn("Noch 1.009 €", context["milestone_headline"])
        self.assertIn("Shopping-Budget von 200 €", context["plan_step2_title"])
        self.assertEqual(context["plan_step3_title"], "Lege eine monatliche Rate für dein Ziel fest.")
        self.assertEqual(context["plan_step3_impact"], "Zielrate")
        shopping_comparison = next(
            row for row in context["comparison_rows"] if row["name"] == "Shopping"
        )
        self.assertEqual(shopping_comparison["amount_text"], "+533 €")
        self.assertIn('class="report-merchant-row"', html)
        self.assertIn('class="report-comparison-row"', html)
        self.assertIn('class="report-comparison-badge"', html)
        self.assertIn('grid-template-columns: minmax(0, 1fr) max-content;', html)
        self.assertIn('overflow-wrap: normal;', html)
        self.assertEqual(
            [item["name"] for item in context["money_map_categories"]],
            ["Shopping", "Lebensmittel", "Sonstiges", "Mobilität", "Restaurant", "Pflege"],
        )
        self.assertEqual(context["money_map_category_count"], 6)
        self.assertEqual(context["money_map_transaction_count"], 23)
        self.assertIn('data-screen-label="05 Vormonatsvergleich"', html)
        self.assertIn("Vergleichsbasis", html)
        self.assertEqual(html.count("Shopping lag 533 € über deinem gesetzten Budget."), 1)
        for category in context["money_map_categories"]:
            self.assertIn(category["name"], html)
            self.assertIn(category["amount_text"], html)
            self.assertIn(f'{category["pct_text"]} %', html)
        for phrase in (
            "Rov.E zeigt nur relevante Kategorien aus deinem Snapshot.",
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

    def test_v2_overview_shows_only_reliable_wealth_buffer_and_debt_truth_in_web_and_pdf(self):
        data = july_review_payload()

        context = build_render_context(data)
        web = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        pdf = "".join(_render_hell_pages(data))

        self.assertEqual(context["net_worth_amount"], "38.620 €")
        self.assertEqual(context["investment_overview_note"], "Die genaue Kursentwicklung lässt sich für diesen Monat noch nicht getrennt ausweisen.")
        self.assertEqual(context["investment_contribution_text"], "+500 € investiert")
        overview_web = web.split('data-screen-label="02 Überblick"', 1)[1].split('data-screen-label="03 ', 1)[0]
        overview_pdf = _render_hell_pages(data)[1]
        for overview in (overview_web, overview_pdf):
            self.assertIn("Die genaue Kursentwicklung lässt sich für diesen Monat noch nicht getrennt ausweisen.", overview)
            self.assertNotIn("Eine vollständige Trennung zwischen Einzahlungen und Marktwertentwicklung", overview)

        self.assertEqual(context["wealth_opening_amount"], "37.200 €")
        self.assertEqual(context["wealth_closing_amount"], "38.620 €")
        self.assertEqual(context["wealth_change_amount"], "+1.420 €")
        self.assertEqual(context["investment_end_amount"], "20.478 €")
        self.assertEqual(context["investment_end_amount"], context["investments_amount"])
        wealth = data["report_truth"]["wealth"]
        self.assertEqual(wealth["total"], net_worth_total(
            wealth["cash"], wealth["investments"], wealth["property_equity"],
            wealth["total_consumer_debt"],
        ))
        allocation = context["report"]["allocation"]
        self.assertAlmostEqual(sum(item["share_raw"] for item in allocation), 100.0)
        self.assertAlmostEqual(allocation[0]["share_raw"], 9142.0 / 40120.0 * 100)
        self.assertEqual(data["report_truth"]["buffer"]["available_cash"], data["report_truth"]["cash"]["current_cash"])
        self.assertAlmostEqual(data["report_truth"]["buffer"]["covered_months"], 9142.0 / 4000.0)
        self.assertEqual(data["report_truth"]["buffer"]["gap"], 10000.0 - 9142.0)
        self.assertEqual(context["buffer_cash_amount"], context["cash_amount"])
        self.assertEqual(context["consumer_debt_display"], "1.500 €")
        self.assertEqual(context["mortgage_debt_display"], "80.000 €")
        self.assertEqual(context["buffer_coverage_text"], "Deckt aktuell rund 2,3 Monate deiner hinterlegten monatlichen Fixkosten.")
        self.assertEqual(context["money_map_summary_text"], "1.856,54 € Konsum · 23 Buchungen · 6 Kategorien")
        self.assertEqual(context["money_map_total_amount"], "1.856,54 €")
        self.assertEqual(context["money_map_summary_meta"], "23 Buchungen · 6 Kategorien")
        for rendered in (web, pdf):
            self.assertIn("37.200 €", rendered)
            self.assertIn("38.620 €", rendered)
            self.assertIn("+1.420 €", rendered)
            self.assertIn("2,3 Monate", rendered)
            self.assertIn("Ziel 10.000 € · noch 858 €", rendered)
            self.assertIn("1.500 €", rendered)
            self.assertIn("Konsumschulden", rendered)
            self.assertIn("80.000 €", rendered)
            self.assertIn("Hypothek", rendered)
            self.assertIn("−100 €", rendered)
            self.assertIn("+500 € investiert", rendered)
            self.assertIn("Schulden reduziert", rendered)
            self.assertIn("300 € weniger als im Vormonat", rendered)
            self.assertIn("Datenqualität", rendered)
            self.assertIn("Zielrate", rendered)
            self.assertNotIn("Sparziel 1.000 €", rendered)
            self.assertNotIn("7.100 €", rendered)
            self.assertEqual(rendered.count("Die genaue Kursentwicklung lässt sich für diesen Monat noch nicht getrennt ausweisen."), 1)
            self.assertIn("1.856,54 € Konsum", rendered)
            self.assertIn("23 Buchungen · 6 Kategorien", rendered)
            self.assertNotIn("1.856,54 € Konsum · 23 Buchungen · 6 Kategorien", rendered)
            self.assertIn("Dein Nettovermögen ist diesen Monat um 1.420 € gestiegen.", rendered)
            self.assertNotIn("Belegte Investmentbeiträge, netto", rendered)
            self.assertNotIn("Aufbau · belegte Bestandteile", rendered)
            self.assertNotIn("700 € aufgebaut", rendered)
        self.assertIn("1 Buchung · Ø", pdf)
        self.assertNotIn("1 Buchungen", pdf)
        self.assertNotIn("Nettovermögen: 1.420 € mehr als im Vormonat", pdf)
        self.assertIn("Vermögensbild", pdf)
        self.assertIn("Belegte Vermögensbewegungen", pdf)
        self.assertIn("Nicht alle Veränderungen lassen sich für diesen Monat einzeln zuordnen.", web)
        self.assertIn("Nicht alle Veränderungen lassen sich für diesen Monat einzeln zuordnen.", pdf)
        self.assertIn("Aktuell", pdf)

    def test_incomplete_wealth_movement_is_disclosed_without_estimating_a_residual(self):
        data = july_review_payload()
        context = build_render_context(data)
        web = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        pdf = "".join(_render_hell_pages(data))

        self.assertTrue(context["wealth_movement_incomplete"])
        for rendered in (web, pdf):
            self.assertIn("Belegte Vermögensbewegungen", rendered)
            self.assertIn("Cash", rendered)
            self.assertIn("Investiert", rendered)
            self.assertIn("Schulden reduziert", rendered)
            self.assertIn(
                "Nicht alle Veränderungen lassen sich für diesen Monat einzeln zuordnen.",
                rendered,
            )
            self.assertNotIn("720 € Marktgewinn", rendered)
            self.assertNotIn("720 € aufgebaut", rendered)

        data["report_truth"]["wealth_period"]["change_amount"] = 700.0
        data["report_story_v2"] = build_report_story_v2(data)
        complete_context = build_render_context(data)
        complete_web = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        complete_pdf = "".join(_render_hell_pages(data))
        self.assertFalse(complete_context["wealth_movement_incomplete"])
        self.assertNotIn("Nicht alle Veränderungen lassen sich für diesen Monat einzeln zuordnen.", complete_web)
        self.assertNotIn("Nicht alle Veränderungen lassen sich für diesen Monat einzeln zuordnen.", complete_pdf)

    def test_mortgage_is_visually_connected_to_available_property_equity(self):
        data = july_review_payload()
        data["report_truth"]["property"] = {
            "market_value": 90500.0,
            "remaining_debt": 80000.0,
            "equity": 10500.0,
        }
        data["profile"].update({
            "property_market_value": 90500.0,
            "property_remaining_debt": 80000.0,
        })
        data["report_story_v2"] = build_report_story_v2(data)

        context = build_render_context(data)
        web = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        pdf = "".join(_render_hell_pages(data))

        self.assertTrue(context["mortgage_property_context_available"])
        self.assertIn("Immobilienwert 90.500 € · Eigenkapital 10.500 €", web)
        self.assertIn("Immobilien-Eigenkapital", pdf)
        self.assertIn("Marktwert 90.500 € · Restschuld 80.000 €", pdf)
        self.assertEqual(data["report_truth"]["wealth"]["total"], 38620.0)
        self.assertIn("Nächste Etappe", pdf)
        self.assertIn("40.000 €", pdf)

    def test_debt_and_buffer_blocks_are_omitted_without_source_values(self):
        data = july_truth_payload()
        data["report_truth"].pop("buffer", None)
        data["report_truth"].pop("debts", None)
        data["report_story_v2"] = build_report_story_v2(data)

        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)

        self.assertNotIn("Dein Puffer ·", html)
        self.assertNotIn("Schulden · aktueller Stand", html)

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

    def test_score_deltas_render_as_points_for_both_signs(self):
        for current, previous, expected in ((80, 70, "+10 Punkte"), (66, 70, "−4 Punkte")):
            with self.subTest(expected=expected):
                data = july_truth_payload()
                data["report_truth"]["score"]["clarity_score"] = current
                data["report_truth"]["score"]["parts"]["total"] = current
                data["report_truth"]["previous_month"]["snapshot"]["clarity_score"] = previous
                data["report_truth"]["expenses"].update({
                    "previous_total_consumption": 1856.54, "categories": [], "merchants": [],
                })
                data["report_story_v2"] = build_report_story_v2(data)

                score_row = next(
                    row for row in build_render_context(data)["comparison_rows"]
                    if row["name"] == "Rov.E Score"
                )
                self.assertEqual(score_row["amount_text"], expected)

    def test_missing_historical_score_stays_unavailable_in_web_and_pdf_fallback(self):
        data = july_truth_payload()
        data["report_truth"]["score"].update({"clarity_score": None, "parts": {"total": None, "factors": []}})
        data["report_truth"]["previous_month"]["snapshot"]["clarity_score"] = None
        data["report_story_v2"] = build_report_story_v2(data)

        context = build_render_context(data)
        html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        self.assertFalse(context["score_available"])
        self.assertEqual(context["score_span"], "—")
        self.assertEqual(context["rank_name"], "Nicht verfügbar")
        self.assertEqual(context["rank_band_text"], "")
        self.assertIn("Score nicht verfügbar.", html)
        self.assertNotIn("0 von 100", html)
        self.assertNotIn("Was „Rookie“ bedeutet", html)

        pdf_canvas = unittest.mock.MagicMock()
        with patch.object(rove_pdf_report_renderer, "begin_page"), \
             patch.object(rove_pdf_report_renderer, "draw_card") as draw_card, \
             patch.object(rove_pdf_report_renderer, "draw_ring"), \
             patch.object(rove_pdf_report_renderer, "end_page"), \
             patch.object(rove_pdf_report_renderer, "font", return_value="Helvetica"):
            rove_pdf_report_renderer.draw_score(pdf_canvas, {
                "pages": {"score": {"clarity_score": None, "rank_name": None, "parts": {"factors": []}}}
            })
        self.assertIn("—", [call.args[2] for call in pdf_canvas.drawCentredString.call_args_list])
        self.assertIn("NICHT VERFÜGBAR", [call.args[2] for call in pdf_canvas.drawCentredString.call_args_list])
        self.assertEqual(draw_card.call_args.args[5], "Score-Einordnung nicht verfügbar")

        legacy = legacy_archive_payload()
        legacy["pages"]["score"].update({
            "clarity_score": None, "rank_name": None, "proof_days": None,
            "days_to_unlock": None, "parts": {"factors": []},
        })
        legacy_score_html = render_777_score("", legacy)
        self.assertIn("Score nicht verfügbar.", legacy_score_html)
        self.assertIn("Kein gespeicherter historischer Rang", legacy_score_html)
        self.assertNotIn("Rookie", legacy_score_html)
        self.assertEqual(plan_items(legacy)[0][2], "Score nicht verfügbar")

    def test_negative_net_worth_keeps_full_milestone_distance(self):
        data = july_truth_payload()
        data["report_truth"]["wealth"]["total"] = -15000.0
        data["report_story_v2"] = build_report_story_v2(data)

        context = build_render_context(data)

        self.assertEqual(context["milestone_to"], "5.000 €")
        self.assertEqual(context["milestone_headline"], "Noch 20.000 € bis zum nächsten Meilenstein.")

    def test_legacy_archive_preview_discloses_older_wealth_basis_only_when_present(self):
        legacy_context = build_render_context(legacy_archive_payload())
        legacy_html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), legacy_archive_payload())
        self.assertEqual(
            legacy_context["legacy_wealth_basis_note"],
            "Älterer Monatswert · basiert auf der damals verfügbaren Datenbasis.",
        )
        self.assertIn("Älterer Monatswert · basiert auf der damals verfügbaren Datenbasis.", legacy_html)

        modern = july_truth_payload()
        modern["pages"]["wealth_journey"]["points"] = [
            {"month": "2026-06", "net_worth": 12000.0}
        ]
        modern["report_story_v2"] = build_report_story_v2(modern)
        modern_context = build_render_context(modern)
        modern_html = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), modern)
        self.assertEqual(modern_context["legacy_wealth_basis_note"], "")
        self.assertNotIn("Älterer Monatswert · basiert auf der damals verfügbaren Datenbasis.", modern_html)

    def test_wealth_history_keeps_missing_historical_score_unavailable(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE monthly_snapshots (id INTEGER PRIMARY KEY, user_id INTEGER, month TEXT, net_worth REAL, clarity_score INTEGER)"
        )
        conn.execute("INSERT INTO monthly_snapshots (user_id, month, net_worth, clarity_score) VALUES (1, '2026-06', 12000, NULL)")

        with patch.object(report_engine, "get_db", return_value=conn):
            points = report_engine.get_wealth_history(1, "2026-07")

        self.assertIsNone(points[0]["clarity_score"])

    def test_pdf_keeps_design_shell_and_uses_v2_truth_fields(self):
        data = july_truth_payload()
        pages = _render_hell_pages(data)
        html = build_html_document(pages)

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
        self.assertIn("38.991 €", pages[1])
        self.assertIn("38.991 €", pages[7])
        self.assertIn("9.142 €", html)
        self.assertIn("20.849 €", html)
        self.assertIn("9.000 €", html)
        self.assertIn("Controller", html)
        self.assertNotIn("Budget / Cashflow", html)
        self.assertIn(">6<span", html)
        self.assertIn(">/20</span>", html)
        self.assertIn("Sparquote", html)
        self.assertIn("Budget & Cashflow", html)
        self.assertIn("Schuldenstruktur", html)
        self.assertIn("Datenqualität", html)
        self.assertNotIn("Savings Rate", html)
        self.assertIn(">20<span", html)
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


class PdfMonthlyDetailsTests(unittest.TestCase):
    def test_largest_single_expense_uses_engine_fact_not_merchant_total(self):
        data = july_truth_payload()
        rows = [
            {"classification": "consumption", "amount": 500.25,
             "merchant": "Elektronikmarkt", "category": "SHOPPING", "created_at": "2026-07-18 14:00:00"},
            {"classification": "transfer", "amount": 9000.0,
             "merchant": "Eigenes Konto", "category": "TRANSFER", "created_at": "2026-07-19 14:00:00"},
        ]
        with patch.object(report_engine, "get_report_expense_rows", return_value=rows):
            expense = report_engine.get_biggest_expense(1, "2026-07")
        data["pages"]["month"] = {"biggest_expense": expense}
        data["report_truth"]["expenses"]["largest_expense"] = {
            key: expense[key] for key in ("amount", "merchant", "category")
        }
        before = copy.deepcopy(data)

        context = build_render_context(data)
        page = _render_hell_pages(data)[4]

        self.assertEqual(context["pdf_largest_expense"]["amount"], "500,25 €")
        self.assertEqual(context["pdf_largest_expense"]["detail"], "Elektronikmarkt")
        self.assertEqual(context["biggest_name"], "Breuninger")
        self.assertIn("Größte Einzelausgabe", page)
        self.assertIn("500,25 €", page)
        self.assertNotIn("18.07.2026", page)
        self.assertEqual(data, before)
        data["report_truth"]["expenses"]["largest_expense"]["created_at"] = expense["created_at"]
        self.assertEqual(build_render_context(data)["pdf_largest_expense"]["detail"], "Elektronikmarkt · 18.07.2026")

    def test_missing_or_invalid_single_expense_does_not_invent_zero(self):
        for expense in (None, {}, {"amount": None}, {"amount": 0}, {"amount": float("nan")}):
            with self.subTest(expense=expense):
                data = july_truth_payload()
                data["report_truth"]["expenses"]["largest_expense"] = expense
                context = build_render_context(data)
                page = _render_hell_pages(data)[4]
                self.assertFalse(context["pdf_largest_expense"]["available"])
                block = page.split('data-report-block="largest-expense"', 1)[1]
                self.assertIn("Keine verlässliche Einzelbuchung verfügbar.", block)
                self.assertNotIn("0 €", block)

    def test_single_expense_omits_unreliable_merchant_and_mismatched_date(self):
        data = july_truth_payload()
        data["report_truth"]["expenses"]["largest_expense"] = {
            "amount": 500.0, "merchant": "Shopping", "category": "SHOPPING",
        }
        data["pages"]["month"] = {"biggest_expense": {
            "amount": 380.0, "merchant": "Breuninger", "category": "SHOPPING",
            "created_at": "2026-07-18",
        }}
        self.assertEqual(build_render_context(data)["pdf_largest_expense"]["detail"], "")
        for date in ("invalid", "2026-08-18"):
            data["report_truth"]["expenses"]["largest_expense"]["created_at"] = date
            self.assertEqual(build_render_context(data)["pdf_largest_expense"]["detail"], "")

    def test_single_expense_escapes_counterparty_text(self):
        data = july_truth_payload()
        data["report_truth"]["expenses"]["largest_expense"]["merchant"] = '<script>alert("test")</script>'
        block = _render_hell_pages(data)[4].split('data-report-block="largest-expense"', 1)[1]
        self.assertNotIn("<script>", block)
        self.assertIn("&lt;script&gt;", block)

    def test_cashflow_reuses_frozen_value_and_preserves_financial_truth(self):
        data = report_payload()
        data["report_story_v2"]["pages"]["page_2"]["primary_metric"]["value"] = 123.45
        before = copy.deepcopy(data)
        context = build_render_context(data)
        page = _render_hell_pages(data)[1]

        self.assertEqual(context["pdf_cashflow"]["income"], "4.000 €")
        self.assertEqual(context["pdf_cashflow"]["fixed_costs"], "1.500 €")
        self.assertEqual(context["pdf_cashflow"]["consumption"], "900 €")
        self.assertEqual(context["pdf_cashflow"]["net"], "123,45 €")
        for text in ("Einnahmen", "Ausgaben", "Konsum 900 €", "Fixkosten 1.500 €",
                     "Verbleibend nach Sparen", "123,45 €", "bestätigter Sparleistung (800 €)"):
            self.assertIn(text, page)
        self.assertNotIn("Netto-Geldfluss", page)
        self.assertEqual(data, before)

    def test_cashflow_missing_inputs_are_not_replaced_with_zero(self):
        for section, key in (("income", "amount"), ("fixed_costs", "amount"),
                             ("expenses", "total_consumption"), ("savings", "actual_amount")):
            for missing in (True, False):
                with self.subTest(section=section, missing=missing):
                    data = report_payload()
                    if missing:
                        del data["report_truth"][section][key]
                    else:
                        data["report_truth"][section][key] = None
                    summary = build_render_context(data)["pdf_cashflow"]
                    self.assertEqual(summary["net"], "—")
                    self.assertIn("nicht verfügbar", summary["note"])
                    if section != "savings":
                        field = {"income": "income", "fixed_costs": "fixed_costs", "expenses": "consumption"}[section]
                        self.assertEqual(summary[field], "—")
        data = report_payload()
        data["report_truth"]["income"]["source"] = "unavailable"
        self.assertEqual(build_render_context(data)["pdf_cashflow"]["income"], "—")
        self.assertEqual(build_render_context(data)["pdf_cashflow"]["net"], "—")

    def test_cashflow_unconfirmed_inputs_suppress_net_value(self):
        for section in ("income", "fixed_costs", "savings"):
            with self.subTest(section=section):
                data = report_payload()
                data["report_truth"][section]["confirmed"] = False
                summary = build_render_context(data)["pdf_cashflow"]
                self.assertEqual(summary["net"], "—")
                self.assertIn("nicht vollständig bestätigt", summary["note"])
                if section != "savings":
                    self.assertEqual(summary["income_status" if section == "income" else "fixed_status"], "Noch nicht bestätigt")

    def test_cashflow_zero_and_negative_values_keep_existing_semantics(self):
        data = report_payload()
        for section, key in (("fixed_costs", "amount"), ("expenses", "total_consumption"), ("savings", "actual_amount")):
            data["report_truth"][section][key] = 0.0
        data["report_story_v2"] = build_report_story_v2(data)
        summary = build_render_context(data)["pdf_cashflow"]
        self.assertEqual(summary["net"], "4.000 €")
        self.assertEqual(summary["fixed_costs"], "0 €")
        self.assertEqual(summary["consumption"], "0 €")
        data = report_payload()
        data["report_truth"]["expenses"]["total_consumption"] = 2000.0
        data["report_story_v2"] = build_report_story_v2(data)
        self.assertEqual(build_render_context(data)["pdf_cashflow"]["net"], "-300 €")

    def test_cashflow_does_not_treat_transfers_or_investments_as_consumption(self):
        data = report_payload()
        expected = build_render_context(data)["pdf_cashflow"]
        data["report_truth"]["expenses"]["classification_totals"].update({
            "transfer": 90000.0, "investment_contribution": 70000.0,
        })
        data["report_story_v2"] = build_report_story_v2(data)
        self.assertEqual(build_render_context(data)["pdf_cashflow"], expected)

    def test_new_blocks_fit_ten_pages_without_footer_overlap(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                data = july_review_payload()
                if missing:
                    data["report_truth"]["income"]["amount"] = None
                    data["report_truth"]["expenses"]["largest_expense"] = None
                else:
                    data["report_truth"]["savings"].update({"confirmed": True, "actual_amount": 800.0})
                    data["report_truth"]["expenses"]["largest_expense"].update({
                        "merchant": "SehrLangerHaendlernameOhneLeerzeichen123456789" * 40,
                        "created_at": "2026-07-18",
                    })
                    detail = build_render_context(data)["pdf_largest_expense"]["detail"]
                    self.assertIn("… · 18.07.2026", detail)
                    self.assertLessEqual(len(detail), 93)
                data["report_story_v2"] = build_report_story_v2(data)
                document = HTML(string=build_html_document(_render_hell_pages(data))).render()
                self.assertEqual(len(document.pages), 10)
                for page_index, name in ((1, "monthly-cashflow"), (4, "largest-expense")):
                    boxes = list(document.pages[page_index]._page_box.descendants())
                    block = next(box for box in boxes if box.element is not None and box.element.get("data-report-block") == name)
                    footer = next(box for box in boxes if box.element is not None and box.element.get("data-report-footer") == str(page_index + 1))
                    self.assertLessEqual(block.border_box_y() + block.border_height(), footer.border_box_y() - 8)
                    for box in block.descendants():
                        self.assertLessEqual(box.position_x + box.width, 760.1)


class PdfFinalSampleTests(unittest.TestCase):
    def test_sample_merchant_and_category_aggregates_reconcile(self):
        data = july_final_sample_payload()
        expenses = data["report_truth"]["expenses"]
        merchants = expenses["merchants"]
        categories = expenses["categories"]
        self.assertEqual(round(sum(item["amount"] for item in merchants), 2), 1856.54)
        self.assertEqual(round(sum(item["amount"] for item in categories), 2), 1856.54)
        self.assertEqual(expenses["classification_totals"]["consumption"], 1856.54)
        self.assertEqual(sum(item["transaction_count"] for item in merchants), 23)
        self.assertEqual(sum(item["transaction_count"] for item in categories), 23)
        for category in categories:
            matching = [item for item in merchants if item["category"] == category["category"]]
            self.assertEqual(round(sum(item["amount"] for item in matching), 2), category["amount"])
            self.assertEqual(sum(item["transaction_count"] for item in matching), category["transaction_count"])
            self.assertEqual(round(category["amount"] / category["transaction_count"], 2), category["avg_transaction"])
            self.assertEqual(round(category["amount"] / 1856.54 * 100, 2), category["share"])
        for merchant in merchants:
            self.assertEqual(round(merchant["amount"] / merchant["transaction_count"], 2), merchant["avg_transaction"])
        shopping = next(item for item in categories if item["category"] == "SHOPPING")
        self.assertEqual(data["report_truth"]["budget"]["items"][0]["used"], shopping["amount"])
        self.assertEqual(shopping["amount"], 983.0)
        self.assertLessEqual(data["meta"]["tracked_days"], expenses["transaction_count"])

    def test_sample_largest_expense_is_in_the_actual_rendered_ranking(self):
        data = july_final_sample_payload()
        before = copy.deepcopy(data)
        context = build_render_context(data)
        self.assertEqual([item["name"] for item in context["merchant_rows"]],
                         ["Elektronikmarkt", "Breuninger", "REWE"])
        self.assertEqual([item["amount"] for item in context["merchant_rows"]], ["500 €", "380 €", "240 €"])
        self.assertEqual(context["pdf_largest_expense"]["amount"], "500 €")
        page = _render_hell_pages(data)[4]
        self.assertEqual(page.count("Elektronikmarkt"), 2)
        self.assertIn("18.07.2026", page)
        self.assertEqual(data, before)

    def test_confirmed_sample_displays_the_existing_remaining_amount(self):
        data = july_final_sample_payload()
        before = copy.deepcopy(data)
        truth = data["report_truth"]
        self.assertTrue(all(truth[key]["confirmed"] for key in ("income", "fixed_costs", "savings")))
        self.assertEqual(truth["savings"]["actual_amount"], 500.0)
        self.assertEqual(truth["investments"]["contributions"]["net_contributions"], 500.0)
        self.assertEqual(data["pages"]["wealth_journey"]["savings_progress"]["full_plan_amount"], 500.0)
        self.assertEqual(data["report_story_v2"]["pages"]["page_2"]["primary_metric"]["value"], 143.46)
        context = build_render_context(data)
        self.assertEqual(context["pdf_cashflow"]["net"], "143,46 €")
        self.assertEqual(truth["buffer"]["monthly_basis"], truth["fixed_costs"]["amount"])
        self.assertAlmostEqual(truth["buffer"]["covered_months"], 9142.0 / 1500.0)
        self.assertEqual(context["buffer_months_amount"], "6,1 Monate")
        page = _render_hell_pages(data)[1]
        self.assertIn("Verbleibend nach Sparen", page)
        self.assertIn("143,46 €", page)
        self.assertIn("Einnahmen abzüglich Fixkosten, Konsum und bestätigter Sparleistung (500 €). Kein Kontosaldo.", page)
        self.assertNotIn("Netto-Geldfluss", page)
        self.assertEqual(data, before)

    def test_label_change_keeps_missing_or_unconfirmed_savings_unavailable(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                data = july_final_sample_payload()
                if missing:
                    data["report_truth"]["savings"]["actual_amount"] = None
                else:
                    data["report_truth"]["savings"]["confirmed"] = False
                data["report_story_v2"] = build_report_story_v2(data)
                context = build_render_context(data)
                self.assertEqual(context["pdf_cashflow"]["net"], "—")
                page = _render_hell_pages(data)[1]
                self.assertIn("Verbleibend nach Sparen", page)
                self.assertIn("Verbleibender Betrag nicht verfügbar", page)
                self.assertNotIn("Netto-Geldfluss", page)

    def test_final_sample_fits_ten_pages_with_unchanged_milestones(self):
        data = july_final_sample_payload()
        pages = _render_hell_pages(data)
        self.assertIn("38.620 €", pages[7])
        self.assertIn("72,4 %", pages[7])
        self.assertIn("1.380 €", pages[7])
        document = HTML(string=build_html_document(pages)).render()
        self.assertEqual(len(document.pages), 10)
        for page_index, names in ((1, ("monthly-cashflow",)), (4, ("largest-expense",)), (7, ("milestone-summary",))):
            boxes = list(document.pages[page_index]._page_box.descendants())
            footer = next(box for box in boxes if box.element is not None and box.element.get("data-report-footer") == str(page_index + 1))
            for name in names:
                block = next(box for box in boxes if box.element is not None and box.element.get("data-report-block") == name)
                self.assertLessEqual(block.border_box_y() + block.border_height(), footer.border_box_y() - 8)
                for box in block.descendants():
                    self.assertLessEqual(box.position_x + box.width, 760.1)
                    self.assertLessEqual(box.position_y + box.height, block.border_box_y() + block.border_height() + 0.1)


class PdfDesignPolishTests(unittest.TestCase):
    def test_expense_metadata_uses_only_the_selected_snapshot_fact(self):
        data = july_truth_payload()
        data["report_truth"]["expenses"]["largest_expense"].update({
            "merchant": "Elektronikmarkt", "category": "MOBILITAET",
            "created_at": "2026-07-18 14:30:00",
        })
        data["pages"]["month"] = {"biggest_expense": {
            "amount": 9000, "merchant": "Anderer Händler", "created_at": "2026-07-01",
        }}
        before = copy.deepcopy(data)
        context = build_render_context(data)
        details = _pdf_design_details(data, context)["pdf_largest_expense"]
        self.assertEqual(details["amount"], context["pdf_largest_expense"]["amount"])
        self.assertEqual(details["merchant"], "Elektronikmarkt")
        self.assertEqual(details["category"], "Mobilität")
        self.assertEqual(details["date"], "18.07.2026")
        self.assertIn('data-report-value="largest-expense-amount"', _render_hell_pages(data)[4])
        self.assertEqual(data, before)
        self.assertEqual(build_render_context(data), context)

    def test_missing_or_invalid_expense_metadata_is_omitted(self):
        for category, date in ((None, None), ("", "bad-date"), (None, "2026-08-18")):
            with self.subTest(category=category, date=date):
                data = july_truth_payload()
                data["report_truth"]["expenses"]["largest_expense"].update({
                    "category": category, "created_at": date,
                })
                details = _pdf_design_details(data, build_render_context(data))["pdf_largest_expense"]
                self.assertEqual(details["category"], "")
                self.assertEqual(details["date"], "")
                page = _render_hell_pages(data)[4]
                self.assertNotIn('data-report-value="largest-expense-category"', page)
                self.assertNotIn('data-report-value="largest-expense-date"', page)

    def test_unavailable_expense_does_not_publish_metadata(self):
        data = july_truth_payload()
        data["report_truth"]["expenses"]["largest_expense"].update({
            "amount": None, "merchant": "Elektronikmarkt", "created_at": "2026-07-18",
        })
        details = _pdf_design_details(data, build_render_context(data))["pdf_largest_expense"]
        self.assertFalse(details["available"])
        self.assertEqual((details["merchant"], details["category"], details["date"]), ("", "", ""))

    def test_legacy_expense_metadata_stays_on_its_original_fact(self):
        data = legacy_archive_payload()
        data["pages"]["month"]["biggest_expense"] = {
            "amount": 42, "merchant": "Beispielmarkt", "category": "LEBENSMITTEL",
            "created_at": "2026-07-03",
        }
        details = _pdf_design_details(data, build_render_context(data))["pdf_largest_expense"]
        self.assertEqual((details["amount"], details["merchant"], details["date"]),
                         ("42 €", "Beispielmarkt", "03.07.2026"))

    def test_milestone_progress_reuses_the_existing_band(self):
        for total, lower, upper, remaining, pct in (
            (12500, "10.000 €", "15.000 €", "2.500 €", 50),
            (15000, "15.000 €", "20.000 €", "5.000 €", 0),
            (0, "0 €", "5.000 €", "5.000 €", 0),
            (-15000, "0 €", "5.000 €", "20.000 €", 0),
        ):
            with self.subTest(total=total):
                data = july_truth_payload()
                data["report_truth"]["wealth"]["total"] = total
                data["report_story_v2"] = build_report_story_v2(data)
                before = copy.deepcopy(data)
                context = build_render_context(data)
                details = _pdf_design_details(data, context)["pdf_milestones"]
                self.assertTrue(details["progress_available"])
                self.assertEqual(details["progress_raw"], context["milestone_pct_raw"])
                self.assertEqual(details["progress_raw"], pct)
                self.assertEqual((context["milestone_from"], context["milestone_to"], context["milestone_remaining"]),
                                 (lower, upper, remaining))
                page = _render_hell_pages(data)[7]
                for text in ("Fortschritt in dieser Etappe", lower, upper, remaining):
                    self.assertIn(text, page)
                self.assertEqual(data, before)

    def test_unknown_wealth_or_invalid_progress_suppresses_the_bar(self):
        data = july_truth_payload()
        for progress in (None, True, -1, 101, float("nan"), float("inf"), "50"):
            with self.subTest(progress=progress):
                context = build_render_context(data)
                context["milestone_pct_raw"] = progress
                self.assertFalse(_pdf_design_details(data, context)["pdf_milestones"]["progress_available"])
        context = build_render_context(data)
        context["wealth_available"] = False
        self.assertFalse(_pdf_design_details(data, context)["pdf_milestones"]["progress_available"])
        with patch("rove_web_report_renderer.build_render_context", return_value=context):
            self.assertNotIn('data-report-block="milestone-progress"', _render_hell_pages(data)[7])

    def test_pdf_badges_localize_known_labels_and_safe_dates_without_mutation(self):
        data = july_truth_payload()
        data["pages"]["milestones"] = {"badges": [
            {"key": "ten_k_club", "label": "Fuenfstellige Freiheit", "earned_at": "2026-07-20T14:00:00+02:00"},
            {"key": "streak_7", "label": "Erste Woche", "earned_at": "bad-date"},
            {"key": "custom", "label": '<script>alert("test")</script>', "earned_at": None},
        ]}
        before = copy.deepcopy(data)
        context = build_render_context(data)
        details = _pdf_design_details(data, context)["pdf_milestones"]
        self.assertEqual(len(details["badges"]), len(context["badges"]))
        self.assertEqual(details["badges"][0], {"label": "Fünfstellige Freiheit", "date": "20.07.2026"})
        self.assertEqual(details["badges"][1]["date"], "")
        self.assertEqual(details["badges"][2]["date"], "")
        page = _render_hell_pages(data)[7]
        self.assertIn("Fünfstellige Freiheit", page)
        self.assertIn("20.07.2026", page)
        self.assertNotIn("Fuenfstellige", page)
        self.assertNotIn("bad-date", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertEqual(data, before)
        self.assertEqual(build_render_context(data), context)

    def test_pdf_metadata_does_not_change_web_report_or_financial_context(self):
        data = july_review_payload()
        before = copy.deepcopy(data)
        context = build_render_context(data)
        template = WEB_TEMPLATE.read_text(encoding="utf-8")
        web_before = render_template(template, data)
        _render_hell_pages(data)
        self.assertEqual(render_template(template, data), web_before)
        self.assertEqual(build_render_context(data), context)
        self.assertEqual(data, before)

    def test_polished_blocks_fit_with_long_labels_and_four_badges(self):
        data = july_review_payload()
        data["report_truth"]["expenses"]["largest_expense"].update({
            "merchant": "SehrLangerHaendlernameOhneLeerzeichen" * 4,
            "created_at": "2026-07-18",
        })
        data["pages"]["milestones"] = {"badges": [
            {"key": key, "label": "Ignored legacy label", "earned_at": "2026-07-20"}
            for key in ("ten_k_club", "first_investment", "thousand_club", "emergency_fund")
        ]}
        document = HTML(string=build_html_document(_render_hell_pages(data))).render()
        self.assertEqual(len(document.pages), 10)
        for page_index, names in ((4, ("largest-expense",)), (7, ("milestone-summary", "milestone-badges"))):
            boxes = list(document.pages[page_index]._page_box.descendants())
            footer = next(box for box in boxes if box.element is not None and box.element.get("data-report-footer") == str(page_index + 1))
            for name in names:
                block = next(box for box in boxes if box.element is not None and box.element.get("data-report-block") == name)
                self.assertLessEqual(block.border_box_y() + block.border_height(), footer.border_box_y() - 8)
                for box in block.descendants():
                    self.assertLessEqual(box.position_x + box.width, 760.1)
                    self.assertLessEqual(box.position_y + box.height, block.border_box_y() + block.border_height() + 0.1)
        page5_boxes = list(document.pages[4]._page_box.descendants())
        amount = next(box for box in page5_boxes if box.element is not None and box.element.get("data-report-value") == "largest-expense-amount")
        self.assertEqual(amount.style["font_size"], 44)


class PdfMonthlyRecapTests(unittest.TestCase):
    def recap(self, data):
        data["report_story_v2"] = build_report_story_v2(data)
        return _pdf_monthly_recap(data, build_render_context(data))

    def neutral_payload(self):
        data = standard_payload()
        truth = data["report_truth"]
        data["profile"]["savings_plan"] = 0.0
        truth["savings"].update({"confirmed": False, "actual_amount": 0.0})
        truth["budget"] = {"has_budgets": False, "on_track": False, "items": []}
        truth["investments"]["contributions"].update({"net_contributions": 0.0, "recurring_in": 0.0})
        for item in truth["expenses"]["categories"]:
            item["previous_transaction_count"] = item["transaction_count"]
            item["transaction_count_delta"] = 0
        truth["expenses"]["merchants"] = []
        return data

    def test_three_sections_reuse_ranked_facts_without_claiming_causality(self):
        data = july_final_sample_payload()
        recap = self.recap(data)
        self.assertIn("1.420 €", " ".join(recap["good"]["facts"]))
        self.assertIn("Monatsanfang bis Stand aktuell", recap["good"]["facts"][0])
        self.assertIn("bestätigte Sparleistung beträgt 500 €", recap["good"]["facts"][1])
        self.assertEqual(recap["attention"]["title"], "Sparziel und flexible Ausgaben")
        self.assertIn("Dir fehlten 500 €", recap["attention"]["facts"][0])
        self.assertIn("Shopping lag 783 €", recap["attention"]["facts"][1])
        self.assertIn("keine nachgewiesene Ursache", recap["lever"]["facts"][0])
        page = _render_hell_pages(data)[8]
        for heading in ("Das lief gut", "Das fällt auf", "Dein nächster Hebel"):
            self.assertIn(heading, page)
        for step in data["report_story_v2"]["next_month_engine"]["steps"]:
            self.assertNotIn(step["text"], " ".join(recap["lever"]["facts"]))

    def test_unconfirmed_savings_never_become_a_verified_gap_or_goal_completion(self):
        data = july_truth_payload()
        recap = self.recap(data)
        self.assertEqual(recap["attention"]["title"], "Abweichung vom gesetzten Budget")
        self.assertNotIn("Dir fehlten", str(recap))
        self.assertNotIn("Sparlücke", str(recap))
        data = report_payload()
        data["report_truth"]["savings"]["confirmed"] = False
        recap = self.recap(data)
        self.assertNotIn("geplante Sparrate erreicht", str(recap))
        self.assertIn("wiederkehrende Beiträge investiert", str(recap))

    def test_negative_wealth_and_savings_are_not_presented_as_positive_growth(self):
        data = self.neutral_payload()
        data["report_truth"]["wealth_period"] = {
            "available": True, "opening_net_worth": 15100.0,
            "closing_net_worth": 15000.0, "change_amount": -100.0,
        }
        data["report_truth"]["savings"].update({"confirmed": True, "actual_amount": -200.0})
        recap = self.recap(data)
        self.assertIn("noch nicht belegt", recap["good"]["title"])
        self.assertNotIn("gesunken", str(recap["good"]))
        self.assertNotIn("gestiegen", str(recap["good"]))
        self.assertIn("gesunken", recap["attention"]["title"])
        self.assertIn("−100 €", recap["attention"]["facts"][0])
        self.assertIn("nicht seine Ursache", recap["lever"]["facts"][0])

    def test_merchant_concentration_is_not_an_automatic_cutback_recommendation(self):
        data = self.neutral_payload()
        data["report_truth"]["expenses"]["merchants"] = [{
            "merchant": "Rewe", "category": "LEBENSMITTEL", "amount": 360.0,
            "transaction_count": 5, "avg_transaction": 72.0,
        }]
        recap = self.recap(data)
        self.assertIn("Rewe machte mit 5 Buchungen 40,0 %", recap["attention"]["facts"][0])
        self.assertIn("noch kein Sparpotenzial", recap["lever"]["title"])
        self.assertIn("keine Kürzungsempfehlung", recap["lever"]["facts"][0])

    def test_necessary_expense_frequency_has_a_neutral_lever(self):
        data = self.neutral_payload()
        data["report_truth"]["expenses"]["categories"][0]["previous_transaction_count"] = 4
        recap = self.recap(data)
        self.assertEqual(recap["attention"]["title"], "Veränderte Buchungshäufigkeit")
        self.assertIn("Lebensmittel", str(recap["attention"]))
        self.assertIn("keine pauschale Kürzungsempfehlung", recap["lever"]["facts"][0])

    def test_necessary_budget_overrun_keeps_the_existing_budget_fact(self):
        data = self.neutral_payload()
        data["report_truth"]["budget"] = {
            "has_budgets": True, "on_track": False,
            "items": [{"category": "LEBENSMITTEL", "limit": 500.0, "used": 600.0, "over": True}],
        }
        recap = self.recap(data)
        self.assertIn("Lebensmittel lag 100 €", recap["attention"]["facts"][0])
        self.assertIn("Notwendiger Bedarf", recap["lever"]["facts"][0])
        self.assertNotIn("Sparlücke", str(recap))

    def test_reached_savings_goal_does_not_create_conflicting_savings_pressure(self):
        data = july_final_sample_payload()
        data["profile"]["savings_plan"] = 500.0
        recap = self.recap(data)
        self.assertIn("kein zusätzlicher Sparzwang", recap["lever"]["facts"][0])
        self.assertNotIn("Sparlücke", str(recap))

    def test_completed_goal_and_confirmed_budget_success_use_existing_candidates(self):
        data = self.neutral_payload()
        data["report_truth"]["goals"]["primary"]["current_amount"] = 5000.0
        recap = self.recap(data)
        self.assertIn("Dein Ziel Dubai ist vollständig gefüllt", str(recap["good"]))
        recap = self.recap(report_payload())
        self.assertIn("Budgets eingehalten", str(recap["good"]))
        self.assertIn("geplante Sparrate erreicht", str(recap["good"]))

    def test_missing_previous_month_does_not_invent_a_monthly_comparison(self):
        data = july_final_sample_payload()
        truth = data["report_truth"]
        truth["previous_month"] = {}
        for key in ("previous_total_consumption", "previous_transaction_count"):
            truth["expenses"].pop(key, None)
        for item in truth["expenses"]["categories"]:
            item["previous_amount"] = 0.0
            item["previous_transaction_count"] = 0
        recap = self.recap(data)
        self.assertNotIn("Vormonat", str(recap))
        self.assertIn("Budgetabweichung", str(recap["lever"]))

    def test_empty_and_legacy_snapshots_get_honest_fallbacks(self):
        for data in (self.neutral_payload(), legacy_archive_payload(), {}):
            with self.subTest(legacy=not data.get("report_truth")):
                if data.get("report_truth"):
                    recap = self.recap(data)
                else:
                    recap = _pdf_monthly_recap(data, {})
                self.assertIn("noch nicht belegt", recap["good"]["title"])
                self.assertIn("keine Bestätigung", recap["attention"]["facts"][0])
                self.assertIn("kein konkreter Verbesserungshebel", recap["lever"]["facts"][0])

    def test_labels_are_escaped_and_missing_or_nonfinite_positive_values_are_not_claimed(self):
        data = self.neutral_payload()
        data["report_truth"]["goals"]["primary"].update({
            "name": "<script>Beispiel</script>" + "x" * 100,
            "current_amount": 5000.0,
        })
        recap = self.recap(data)
        self.assertNotIn("<script>", str(recap))
        self.assertIn("&lt;script&gt;", str(recap))
        self.assertIn("…", str(recap))
        for value in (None, float("nan"), float("inf"), True, 0):
            with self.subTest(value=value):
                snapshot = {"report_truth": {"wealth_period": {"available": True, "change_amount": value},
                                             "savings": {"confirmed": True, "actual_amount": value}}}
                recap = _pdf_monthly_recap(snapshot, {})
                self.assertIn("noch nicht belegt", recap["good"]["title"])
                self.assertNotIn("gestiegen", str(recap))

    def test_render_is_non_mutating_and_page_ten_and_web_are_unchanged(self):
        data = july_final_sample_payload()
        before = copy.deepcopy(data)
        context = build_render_context(data)
        context_before = copy.deepcopy(context)
        web = render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data)
        baseline_pages = _render_hell_pages(data)
        _pdf_monthly_recap(data, context)
        with patch("report_html_renderer._pdf_monthly_recap", return_value={
            key: {"title": "Andere Darstellung", "facts": ["Nur Seite neun"]}
            for key in ("good", "attention", "lever")
        }):
            changed_pages = _render_hell_pages(data)
        self.assertNotEqual(baseline_pages[8], changed_pages[8])
        for index in range(10):
            if index != 8:
                self.assertEqual(baseline_pages[index], changed_pages[index])
        self.assertEqual(render_template(WEB_TEMPLATE.read_text(encoding="utf-8"), data), web)
        self.assertEqual(context, context_before)
        self.assertEqual(data, before)

    def test_recap_cards_fit_ten_pages_without_footer_overlap(self):
        long_labels = self.neutral_payload()
        long_labels["report_truth"]["goals"]["primary"].update({"name": "Sehr langes Sparziel " * 20, "current_amount": 5000.0})
        long_labels["report_truth"]["expenses"]["merchants"] = [{
            "merchant": "SehrLangerHaendlername" * 20, "category": "LEBENSMITTEL",
            "amount": 360.0, "transaction_count": 5, "avg_transaction": 72.0,
        }]
        for data in (july_final_sample_payload(), self.neutral_payload(), long_labels):
            data["report_story_v2"] = build_report_story_v2(data)
            pages = _render_hell_pages(july_final_sample_payload())
            # Stress only the changed page; legacy goal-page typography is not
            # part of this PDF recap change.
            pages[8] = _render_hell_pages(data)[8]
            document = HTML(string=build_html_document(pages)).render()
            self.assertEqual(len(document.pages), 10)
            boxes = list(document.pages[8]._page_box.descendants())
            footer = next(box for box in boxes if box.element is not None and box.element.get("data-report-footer") == "9")
            for name in ("good", "attention", "lever"):
                card = next(box for box in boxes if box.element is not None and box.element.get("data-report-block") == "recap-" + name)
                self.assertLessEqual(card.border_box_y() + card.border_height(), footer.border_box_y() - 8)
                for box in card.descendants():
                    self.assertLessEqual(box.position_x + box.width, 760.1)
                    self.assertLessEqual(box.position_y + box.height, card.border_box_y() + card.border_height() + 0.1)


if __name__ == "__main__":
    unittest.main()
