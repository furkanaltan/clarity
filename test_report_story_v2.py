import copy
import unittest

from report_story_v2 import (
    CANDIDATE_TYPES,
    REPORT_STORY_VERSION,
    build_report_story_v2,
    get_report_wealth,
    story_from_snapshot_data,
)


def standard_payload() -> dict:
    return {
        "meta": {"report_month": "2026-07", "month_label": "Juli 2026", "tracked_days": 20},
        "profile": {"income_total": 4000.0, "fixed_costs": 1500.0, "savings_plan": 800.0,
                    "current_investments": 9000.0, "cash_reserve": 6000.0, "property_equity": 0.0},
        "pages": {
            "wealth_journey": {
                "monthly_execution": {"income_confirmed": True, "fixed_costs_confirmed": True, "savings_confirmed": True},
                "savings_progress": {"full_plan_confirmed": True, "full_plan_amount": 800.0},
            }
        },
        "report_truth": {
            "income": {"amount": 4000.0, "confirmed": True, "source": "confirmed_month"},
            "fixed_costs": {"amount": 1500.0, "confirmed": True, "source": "confirmed_month"},
            "expenses": {
                "total_consumption": 900.0,
                "transaction_count": 14,
                "previous_total_consumption": 680.0,
                "previous_transaction_count": 10,
                "classification_totals": {"consumption": 900.0, "transfer": 300.0},
                "categories": [
                    {"category": "LEBENSMITTEL", "amount": 600.0, "transaction_count": 8,
                     "avg_transaction": 75.0, "share": 66.67, "previous_amount": 500.0,
                     "delta": 100.0, "previous_transaction_count": 7, "transaction_count_delta": 1},
                    {"category": "RESTAURANTS", "amount": 300.0, "transaction_count": 6,
                     "avg_transaction": 50.0, "share": 33.33, "previous_amount": 180.0,
                     "delta": 120.0, "previous_transaction_count": 3, "transaction_count_delta": 3},
                ],
                "merchants": [
                    {"merchant": "Rewe", "category": "LEBENSMITTEL", "amount": 360.0,
                     "transaction_count": 5, "avg_transaction": 72.0, "previous_amount": 300.0,
                     "delta": 60.0, "previous_transaction_count": 4, "transaction_count_delta": 1},
                    {"merchant": "Restaurant A", "category": "RESTAURANTS", "amount": 210.0,
                     "transaction_count": 4, "avg_transaction": 52.5, "previous_amount": 90.0,
                     "delta": 120.0, "previous_transaction_count": 2, "transaction_count_delta": 2},
                ],
            },
            "budget": {
                "has_budgets": True, "on_track": True, "total_limit": 1000.0, "total_used": 900.0,
                "items": [
                    {"category": "LEBENSMITTEL", "limit": 650.0, "used": 600.0, "over": False},
                    {"category": "RESTAURANTS", "limit": 350.0, "used": 300.0, "over": False},
                ],
            },
            "cash": {"current_cash": 6000.0, "account_total": 6000.0, "invariant_ok": True, "accounts": []},
            "investments": {
                "contributions": {
                    "net_contributions": 800.0, "recurring_in": 800.0, "one_time_in": 0.0,
                    "out": 0.0, "events_count": 2,
                    "by_asset": [{"asset_type": "etf", "total": 800.0}],
                },
                "market_movement": {"amount": None, "available": False},
                "holdings": [
                    {"id": 7, "instrument_label": "S&P 500", "instrument_type": "etf",
                     "contribution": 800.0, "contribution_data_available": True,
                     "market_value": 9000.0, "valuation_enabled": True},
                ],
            },
            "property": {"equity": 0.0, "source": "app_properties"},
            "wealth": {
                "total": 15000.0, "cash": 6000.0, "investments": 9000.0, "property_equity": 0.0,
                "allocation": [
                    {"key": "cash", "label": "Cash", "asset_class": "cash", "amount": 6000.0, "share": 40.0},
                    {"key": "investment:etfs", "label": "ETFs", "asset_class": "investment", "amount": 9000.0, "share": 60.0},
                ],
                "reconciles": True, "goals_included": False,
            },
            "goals": {
                "primary": {"id": "primary", "name": "Dubai", "target_amount": 5000.0,
                            "current_amount": 1000.0, "is_primary": True},
                "goals": [
                    {"id": "primary", "name": "Dubai", "target_amount": 5000.0,
                     "current_amount": 1000.0, "is_primary": True},
                    {"id": "g_house", "name": "Haus", "target_amount": 100000.0,
                     "current_amount": 500.0, "is_primary": False},
                ],
            },
            "score": {
                "clarity_score": 72,
                "parts": {
                    "total": 72,
                    "factors": [
                        {"key": "budget", "n": "Budget-Kontrolle", "points": 20, "max": 25},
                        {"key": "savings", "n": "Sparrate", "points": 25, "max": 25},
                        {"key": "consistency", "n": "Tracking-Konstanz", "points": 12, "max": 25},
                        {"key": "structure", "n": "Finanzielle Struktur", "points": 15, "max": 25},
                    ],
                },
            },
            "previous_month": {"report_month": "2026-06", "snapshot": {"month": "2026-06", "clarity_score": 69}},
        },
    }


class ReportStoryV2Tests(unittest.TestCase):
    def test_legacy_snapshot_wealth_is_derived_without_mutating_payload(self):
        data = standard_payload()
        data["report_truth"].pop("wealth")
        before = copy.deepcopy(data)

        wealth = get_report_wealth(data)

        self.assertTrue(wealth["available"])
        self.assertEqual(wealth["total"], 15000.0)
        self.assertEqual(data, before)

    def test_incomplete_legacy_snapshot_never_uses_live_data(self):
        data = standard_payload()
        data["report_truth"].pop("wealth")
        data["profile"].pop("current_investments")

        wealth = get_report_wealth(data)

        self.assertFalse(wealth["available"])
        self.assertIsNone(wealth["total"])

    def test_partial_comparison_does_not_compare_full_month_score(self):
        data = standard_payload()
        data["report_truth"]["previous_month"]["comparison_mode"] = "partial"

        story = build_report_story_v2(data)

        self.assertTrue(story["quality"]["previous_month_available"])
        changes = story["pages"]["page_5"]["supporting_metrics"]
        self.assertNotIn("score", {change["type"] for change in changes})

    def test_pre_truth_snapshot_keeps_its_embedded_story(self):
        data = {"report_story_v2": build_report_story_v2(standard_payload())}

        story = story_from_snapshot_data(data)

        self.assertEqual(story["story_version"], REPORT_STORY_VERSION)

    def test_standard_story_has_exactly_ten_distinct_pages(self):
        story = build_report_story_v2(standard_payload())
        self.assertEqual(story["story_version"], REPORT_STORY_VERSION)
        self.assertEqual(story["page_count"], 10)
        self.assertEqual(list(story["pages"]), [f"page_{number}" for number in range(1, 11)])
        self.assertTrue(story["quality"]["unique_primary_metrics"])
        for number, page in enumerate(story["pages"].values(), start=1):
            self.assertEqual(page["page_number"], number)
            self.assertTrue(page["question"])
            self.assertIn("semantic_key", page["primary_metric"])
            self.assertIn("type", page["visual"])
            self.assertIn("available", page)

    def test_savings_reached_and_restaurants_up_is_not_negative(self):
        story = build_report_story_v2(standard_payload())
        selected = story["insight_engine"]["selected"]
        self.assertEqual(selected["type"], "positive_budget_and_saving")
        self.assertEqual(selected["suggested_tone"], "positive")

    def test_savings_gap_connected_to_flexible_delta(self):
        data = standard_payload()
        data["report_truth"]["investments"]["contributions"]["net_contributions"] = 730.0
        data["report_truth"]["investments"]["contributions"]["recurring_in"] = 730.0
        data["report_truth"]["budget"]["has_budgets"] = False
        data["report_truth"]["budget"]["on_track"] = None
        story = build_report_story_v2(data)
        selected = story["insight_engine"]["selected"]
        self.assertEqual(selected["type"], "savings_goal_gap_vs_discretionary_delta")
        self.assertEqual(selected["supporting_metrics"]["savings_gap"], 70.0)
        self.assertTrue(selected["safe_to_coach"])

    def test_food_increase_remains_neutral(self):
        data = standard_payload()
        data["profile"]["savings_plan"] = 0.0
        data["report_truth"]["investments"]["contributions"] = {
            "net_contributions": 0.0, "recurring_in": 0.0, "by_asset": []
        }
        data["report_truth"]["expenses"]["categories"] = [
            {"category": "LEBENSMITTEL", "amount": 700.0, "transaction_count": 10,
             "avg_transaction": 70.0, "share": 100.0, "previous_amount": 400.0,
             "previous_transaction_count": 5}
        ]
        data["report_truth"]["expenses"]["merchants"] = []
        story = build_report_story_v2(data)
        frequency = next(item for item in story["insight_engine"]["candidates"] if item["type"] == "category_frequency_change")
        self.assertEqual(frequency["suggested_tone"], "neutral")
        self.assertFalse(frequency["safe_to_coach"])

    def test_necessary_budget_overrun_does_not_create_lifestyle_candidate(self):
        data = standard_payload()
        data["report_truth"]["budget"]["on_track"] = False
        data["report_truth"]["budget"]["items"][0].update({"limit": 500.0, "used": 600.0, "over": True})
        data["report_truth"]["budget"]["items"] = data["report_truth"]["budget"]["items"][:1]
        story = build_report_story_v2(data)
        types = {item["type"] for item in story["insight_engine"]["candidates"]}
        self.assertNotIn("budget_overrun_vs_flexible_category", types)

    def test_discretionary_budget_overrun_can_create_candidate(self):
        data = standard_payload()
        data["report_truth"]["budget"]["on_track"] = False
        data["report_truth"]["budget"]["items"][1].update({"limit": 200.0, "used": 300.0, "over": True})
        data["report_truth"]["investments"]["contributions"]["net_contributions"] = 600.0
        data["report_truth"]["investments"]["contributions"]["recurring_in"] = 600.0
        story = build_report_story_v2(data)
        types = {item["type"] for item in story["insight_engine"]["candidates"]}
        self.assertIn("budget_overrun_vs_flexible_category", types)

    def test_no_anomaly_uses_stable_month(self):
        data = standard_payload()
        data["profile"]["savings_plan"] = 0.0
        data["report_truth"]["budget"] = {"has_budgets": False, "items": [], "on_track": None}
        data["report_truth"]["investments"]["contributions"] = {
            "net_contributions": 0.0, "recurring_in": 0.0, "by_asset": []
        }
        data["report_truth"]["expenses"]["previous_total_consumption"] = 900.0
        for item in data["report_truth"]["expenses"]["categories"]:
            item["previous_amount"] = item["amount"]
            item["previous_transaction_count"] = item["transaction_count"]
        data["report_truth"]["expenses"]["merchants"] = []
        story = build_report_story_v2(data)
        self.assertEqual(story["insight_engine"]["selected"]["type"], "stable_month")

    def test_contribution_never_becomes_market_performance(self):
        story = build_report_story_v2(standard_payload())
        page = story["pages"]["page_7"]
        self.assertEqual(page["primary_metric"]["value"], 800.0)
        self.assertIn("keine belastbare Marktbewegung", page["text"])
        self.assertNotIn("Kursanstieg", page["text"])

    def test_without_investments_page_seven_degrades(self):
        data = standard_payload()
        data["profile"]["current_investments"] = 0.0
        data["report_truth"]["investments"] = {
            "contributions": {"net_contributions": 0.0, "recurring_in": 0.0, "by_asset": []},
            "market_movement": {"amount": None, "available": False}, "holdings": [],
        }
        story = build_report_story_v2(data)
        self.assertFalse(story["pages"]["page_7"]["available"])
        self.assertIn("keine Sparleistung erfunden", story["pages"]["page_7"]["empty_state"])

    def test_without_goals_no_random_primary_is_selected(self):
        data = standard_payload()
        data["report_truth"]["goals"] = {"primary": None, "goals": []}
        story = build_report_story_v2(data)
        goal_metric = story["pages"]["page_8"]["supporting_metrics"][2]
        self.assertIsNone(goal_metric["value"])

    def test_without_previous_month_page_five_degrades(self):
        data = standard_payload()
        data["report_truth"]["previous_month"] = {"report_month": "2026-06", "snapshot": None}
        data["report_truth"]["expenses"]["previous_total_consumption"] = 0.0
        data["report_truth"]["expenses"]["previous_transaction_count"] = 0
        for item in data["report_truth"]["expenses"]["categories"]:
            item["previous_amount"] = 0.0
            item["previous_transaction_count"] = 0
        story = build_report_story_v2(data)
        self.assertFalse(story["pages"]["page_5"]["available"])
        self.assertEqual(story["pages"]["page_5"]["empty_state"], "Noch kein vollständiger Vormonat zum Vergleichen.")

    def test_without_merchants_page_four_degrades(self):
        data = standard_payload()
        data["report_truth"]["expenses"]["merchants"] = []
        story = build_report_story_v2(data)
        self.assertFalse(story["pages"]["page_4"]["available"])
        self.assertTrue(story["pages"]["page_3"]["available"])

    def test_few_expenses_still_builds_complete_story(self):
        data = standard_payload()
        data["report_truth"]["expenses"]["total_consumption"] = 12.0
        data["report_truth"]["expenses"]["transaction_count"] = 1
        data["report_truth"]["expenses"]["categories"] = [
            {"category": "SONSTIGES", "amount": 12.0, "transaction_count": 1,
             "avg_transaction": 12.0, "share": 100.0, "previous_amount": 0.0}
        ]
        data["report_truth"]["expenses"]["merchants"] = []
        story = build_report_story_v2(data)
        self.assertEqual(len(story["pages"]), 10)

    def test_multi_asset_wealth_reconciles_without_goals(self):
        data = standard_payload()
        data["report_truth"]["wealth"] = {
            "total": 200000.0,
            "allocation": [
                {"label": "Girokonto", "amount": 5000.0, "share": 2.5},
                {"label": "Tagesgeld", "amount": 15000.0, "share": 7.5},
                {"label": "ETFs", "amount": 50000.0, "share": 25.0},
                {"label": "Aktien", "amount": 30000.0, "share": 15.0},
                {"label": "Immobilien-Eigenkapital", "amount": 100000.0, "share": 50.0},
            ],
            "reconciles": True, "goals_included": False,
        }
        story = build_report_story_v2(data)
        page = story["pages"]["page_6"]
        self.assertEqual(sum(item["amount"] for item in page["supporting_metrics"]), 200000.0)
        self.assertNotIn("Dubai", [item.get("label") for item in page["supporting_metrics"]])

    def test_candidate_catalog_and_next_steps_are_bounded(self):
        story = build_report_story_v2(standard_payload())
        self.assertEqual(tuple(story["insight_engine"]["candidate_types"]), CANDIDATE_TYPES)
        self.assertLessEqual(len(story["next_month_engine"]["steps"]), 3)
        self.assertTrue(story["ai_contract"]["fallback_complete"])

    def test_flow_keeps_transfer_and_goal_out_of_consumption_and_wealth(self):
        story = build_report_story_v2(standard_payload())
        flow = {item["key"]: item["value"] for item in story["pages"]["page_2"]["supporting_metrics"]}
        self.assertEqual(flow["consumption"], 900.0)
        self.assertEqual(flow["invested"], 800.0)
        self.assertEqual(story["pages"]["page_6"]["primary_metric"]["value"], 15000.0)


if __name__ == "__main__":
    unittest.main()
