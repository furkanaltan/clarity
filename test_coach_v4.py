from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime

from rove_behavior_patterns import build_shadow_inspector, detect_behavior_patterns


class CoachV4ShadowPatternTests(unittest.TestCase):
    NOW = datetime(2026, 9, 30, 12, 0, 0)

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                income REAL DEFAULT 0,
                other_income REAL DEFAULT 0,
                fixed_costs REAL DEFAULT 0,
                etf_savings REAL DEFAULT 0,
                cash_savings REAL DEFAULT 0
            );
            INSERT INTO users (user_id) VALUES (1);
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT,
                merchant TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                transaction_at TEXT
            );
            CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                expense_id INTEGER,
                created_at TEXT NOT NULL,
                occurred_at TEXT
            );
            CREATE TABLE category_budgets (
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                monthly_limit REAL NOT NULL,
                active_month TEXT NOT NULL
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def add_expense(
        self,
        expense_id: int,
        merchant: str,
        category: str,
        amount: float,
        created_at: str,
        description: str = "",
        transaction_at: str | None | object = "__default__",
    ) -> None:
        if transaction_at == "__default__":
            transaction_at = created_at
        self.conn.execute(
            """INSERT INTO expenses
               (id,user_id,amount,category,merchant,description,created_at,transaction_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (expense_id, 1, amount, category, merchant, description, created_at, transaction_at),
        )

    def patterns(self):
        return detect_behavior_patterns(self.conn, 1, now=self.NOW)

    def of_type(self, pattern_type: str):
        return [p for p in self.patterns() if p["pattern_type"] == pattern_type]

    def add_budget(self, category: str, month: str, limit: float = 100) -> None:
        self.conn.execute(
            "INSERT INTO category_budgets VALUES (1,?,?,?)",
            (category, limit, month),
        )

    def add_income(
        self,
        movement_id: int,
        amount: float = 3000,
        created_at: str = "2026-06-01 09:00:00",
        label: str = "Gehalt",
    ) -> None:
        self.conn.execute(
            "INSERT INTO app_cash_movements "
            "(id,user_id,kind,amount,expense_id,created_at) VALUES (?,?,?,?,?,?)",
            (movement_id, 1, "income", amount, None, created_at),
        )
        self.conn.execute(
            "UPDATE app_cash_movements SET expense_id=NULL WHERE id=?",
            (movement_id,),
        )
        # The fixture schema mirrors the current production table, where the
        # income label is optional in older installations.
        try:
            self.conn.execute(
                "ALTER TABLE app_cash_movements ADD COLUMN label TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self.conn.execute(
            "UPDATE app_cash_movements SET label=? WHERE id=?",
            (label, movement_id),
        )
        self.conn.execute(
            "UPDATE app_cash_movements SET occurred_at=created_at WHERE id=?",
            (movement_id,),
        )

    def configure_budget_plan(
        self,
        *,
        income: float = 3000,
        fixed_costs: float = 1000,
        etf_savings: float = 0,
        cash_savings: float = 0,
    ) -> None:
        self.conn.execute(
            "UPDATE users SET income=?, fixed_costs=?, etf_savings=?, cash_savings=? WHERE user_id=1",
            (income, fixed_costs, etf_savings, cash_savings),
        )

    def add_financial_snapshot(
        self,
        month: str,
        *,
        income: float = 3000,
        fixed_costs: float = 1000,
        etf_savings: float = 0,
        cash_savings: float = 0,
    ) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS monthly_financial_snapshots (
                user_id INTEGER,
                report_month TEXT,
                income REAL,
                other_income REAL,
                fixed_costs REAL,
                etf_savings REAL,
                cash_savings REAL
            )"""
        )
        self.conn.execute(
            "INSERT INTO monthly_financial_snapshots VALUES (?,?,?,?,?,?,?)",
            (1, month, income, 0, fixed_costs, etf_savings, cash_savings),
        )

    def add_month(self, start_id: int, month: str, amounts: tuple[float, ...], category: str = "Restaurants") -> None:
        for offset, amount in enumerate(amounts):
            self.add_expense(
                start_id + offset,
                "Restaurant",
                category,
                amount,
                f"{month}-{offset + 5:02d} 12:00:00",
            )

    def test_eight_lidl_purchases_are_raw_only(self):
        for index in range(8):
            self.add_expense(index + 1, "Lidl", "Lebensmittel", 25, f"2026-09-{index + 1:02d} 12:00:00")
        patterns = self.of_type("merchant_frequency")
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["observations"]["transaction_count"], 8)
        self.assertFalse(patterns[0]["eligible_for_coach"])
        self.assertFalse(patterns[0]["eligible_for_report"])
        self.assertEqual(patterns[0]["financial_relevance"], "low")

    def test_food_budget_pressure_is_evidence_but_not_lidl_behavior_coaching(self):
        self.conn.execute(
            "INSERT INTO category_budgets VALUES (1,'Lebensmittel',350,'2026-09')"
        )
        for index in range(6):
            self.add_expense(index + 1, "Lidl", "Lebensmittel", 100, f"2026-09-{index + 1:02d} 12:00:00")
        pressure = self.of_type("category_budget_pressure")
        self.assertEqual(len(pressure), 1)
        self.assertTrue(pressure[0]["eligible_for_coach"])
        self.assertEqual(pressure[0]["observations"]["amount_spent"], 600.0)
        self.assertFalse(any(p["pattern_type"] == "behavior_plus_budget_pressure" for p in self.patterns()))

    def test_pharmacy_and_tankstation_have_no_behavioral_pattern(self):
        for index, merchant in enumerate(("Apotheke", "Apotheke", "Apotheke", "Shell", "Shell", "Shell"), 1):
            self.add_expense(index, merchant, "Gesundheit" if merchant == "Apotheke" else "Mobilität", 20, f"2026-09-{index:02d} 12:00:00")
        self.assertFalse(self.of_type("merchant_weekday_pattern"))
        self.assertFalse(self.of_type("late_night_discretionary_spending"))
        self.assertFalse(self.of_type("behavior_plus_budget_pressure"))
        self.assertFalse(any(p["eligible_for_coach"] for p in self.patterns()))

    def test_three_of_four_sundays_create_weekday_pattern(self):
        for index, day in enumerate((6, 13, 20), 1):
            self.add_expense(index, "Lieferando", "Restaurants", 35, f"2026-09-{day:02d} 19:00:00")
        patterns = self.of_type("merchant_weekday_pattern")
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["observations"]["transaction_count"], 3)
        self.assertEqual(patterns[0]["observations"]["weekday_opportunities"], 4)
        self.assertTrue(patterns[0]["eligible_for_coach"])

    def test_three_transactions_on_one_sunday_are_not_three_occurrences(self):
        for index in range(3):
            self.add_expense(index + 1, "Lieferando", "Restaurants", 35, "2026-09-06 19:00:00")
        self.assertFalse(self.of_type("merchant_weekday_pattern"))

    def test_one_of_four_sundays_is_not_a_pattern(self):
        self.add_expense(1, "Lieferando", "Restaurants", 35, "2026-09-06 19:00:00")
        self.assertFalse(self.of_type("merchant_weekday_pattern"))

    def test_repeated_late_night_amazon_is_detected(self):
        for index in range(5):
            self.add_expense(index + 1, "Amazon", "Shopping", 40, f"2026-09-{index + 1:02d} 21:00:00")
        patterns = self.of_type("late_night_discretionary_spending")
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["observations"]["transaction_count"], 5)
        self.assertTrue(patterns[0]["eligible_for_coach"])

    def test_created_at_only_is_not_reliable_late_night_evidence(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL,
                category TEXT, merchant TEXT, description TEXT, created_at TEXT
            );
            """
        )
        for index in range(5):
            conn.execute(
                "INSERT INTO expenses VALUES (?,?,?,?,?,?,?)",
                (index + 1, 1, 40, "Shopping", "Amazon", "", f"2026-09-{index + 1:02d} 21:00:00"),
            )
        patterns = detect_behavior_patterns(conn, 1, now=self.NOW)
        self.assertFalse(any(p["pattern_type"] == "late_night_discretionary_spending" for p in patterns))
        conn.close()

    def test_date_only_transaction_time_is_not_late_night_evidence(self):
        for index in range(5):
            self.add_expense(
                index + 1, "Amazon", "Shopping", 40,
                f"2026-09-{index + 1:02d} 21:00:00",
                transaction_at=f"2026-09-{index + 1:02d}",
            )
        self.assertFalse(self.of_type("late_night_discretionary_spending"))

    def test_single_late_night_purchase_is_not_a_pattern(self):
        self.add_expense(1, "Amazon", "Shopping", 40, "2026-09-16 21:00:00")
        self.assertFalse(self.of_type("late_night_discretionary_spending"))

    def test_behavior_and_budget_pressure_create_combined_pattern(self):
        self.conn.execute(
            "INSERT INTO category_budgets VALUES (1,'Shopping',100,'2026-09')"
        )
        for index in range(4):
            self.add_expense(index + 1, "Amazon", "Shopping", 40, f"2026-09-{index + 1:02d} 21:00:00")
        combined = self.of_type("behavior_plus_budget_pressure")
        self.assertEqual(len(combined), 1)
        self.assertTrue(combined[0]["eligible_for_coach"])
        self.assertTrue(combined[0]["eligible_for_report"])
        self.assertEqual(combined[0]["financial_relevance"], "high")
        eligible = [p for p in self.patterns() if p["eligible_for_coach"]]
        self.assertEqual([p["pattern_type"] for p in eligible], ["behavior_plus_budget_pressure"])
        self.assertEqual(len(combined[0]["related_pattern_ids"]), 2)

    def test_internal_transfer_is_not_consumption(self):
        self.conn.execute(
            "INSERT INTO app_cash_movements "
            "(id,user_id,kind,amount,expense_id,created_at) "
            "VALUES (1,1,'transfer',500,NULL,'2026-09-16 08:00:00')"
        )
        self.assertEqual(self.patterns(), [])

    def test_transfer_linked_to_an_expense_row_is_not_historical_consumption(self):
        self.add_expense(1, "Girokonto", "Transfer", 500, "2026-08-16 08:00:00")
        self.conn.execute(
            "INSERT INTO app_cash_movements "
            "(id,user_id,kind,amount,expense_id,created_at) "
            "VALUES (1,1,'transfer',500,1,'2026-08-16 08:00:00')"
        )
        self.assertEqual(self.patterns(), [])

    def test_investment_movement_is_not_consumption(self):
        self.conn.execute(
            """CREATE TABLE investment_events (
                id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL,
                direction TEXT, asset_type TEXT, asset_name TEXT, created_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO investment_events VALUES (1,1,1000,'in','etf','World ETF','2026-09-16 08:00:00')"
        )
        self.assertEqual(self.patterns(), [])

    def test_refund_and_storno_are_not_interpreted_as_consumption(self):
        self.add_expense(1, "Amazon", "Shopping", 40, "2026-09-16 21:00:00", "Erstattung")
        self.add_expense(2, "Amazon", "Shopping", 40, "2026-09-17 21:00:00", "Storno")
        self.assertEqual(self.patterns(), [])

    def test_negative_amount_is_not_interpreted_as_consumption(self):
        for index in range(3):
            self.add_expense(index + 1, "Amazon", "Shopping", -40, f"2026-09-{index + 1:02d} 21:00:00")
        self.assertEqual(self.patterns(), [])

    def test_insufficient_history_produces_no_pattern(self):
        self.add_expense(1, "Amazon", "Shopping", 40, "2026-09-16 21:00:00")
        self.add_expense(2, "Amazon", "Shopping", 40, "2026-09-17 12:00:00")
        self.assertFalse(self.of_type("late_night_discretionary_spending"))
        self.assertFalse(self.of_type("merchant_weekday_pattern"))

    def test_shadow_inspector_is_explicitly_non_coaching(self):
        self.add_expense(1, "Amazon", "Shopping", 40, "2026-09-16 21:00:00")
        payload = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertEqual(payload["mode"], "shadow")
        self.assertFalse(payload["coach_v3_affected"])
        self.assertIsInstance(payload["patterns"], list)

    def test_pattern_ids_are_stable(self):
        for index in range(3):
            self.add_expense(index + 1, "Amazon", "Shopping", 40, f"2026-09-{index + 1:02d} 21:00:00")
        first = self.of_type("late_night_discretionary_spending")[0]["pattern_id"]
        second = self.of_type("late_night_discretionary_spending")[0]["pattern_id"]
        self.assertEqual(first, second)

    def test_repeated_over_budget_requires_three_completed_months(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month)
            self.add_month(int(month[-2:]) * 10, month, (80, 80))
        patterns = [
            pattern for pattern in self.patterns()
            if pattern["pattern_type"] == "category_repeated_over_budget"
        ]
        self.assertEqual(len(patterns), 1)
        self.assertFalse(patterns[0]["eligible_for_coach"])
        self.assertEqual(patterns[0]["observations"]["months_over_budget"], 3)
        self.assertTrue(any(
            pattern["pattern_type"] == "repeated_discretionary_budget_pressure"
            and pattern["eligible_for_coach"]
            for pattern in self.patterns()
        ))

    def test_one_month_over_budget_is_not_repeated_history(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (40, 40))
        self.add_month(5, "2026-08", (40, 40))
        self.assertFalse(self.of_type("category_repeated_over_budget"))

    def test_three_of_four_months_over_budget_are_detected(self):
        for month in ("2026-05", "2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-05", (80, 80))
        self.add_month(3, "2026-06", (80, 80))
        self.add_month(5, "2026-07", (40, 40))
        self.add_month(7, "2026-08", (80, 80))
        pattern = self.of_type("category_repeated_over_budget")[0]
        self.assertEqual(pattern["observations"]["months_considered"], 4)
        self.assertEqual(pattern["observations"]["months_over_budget"], 3)

    def test_current_only_budget_never_backfills_historical_budget_truth(self):
        self.add_budget("Restaurants", "2026-09")
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (80, 80))
        self.assertFalse(self.of_type("category_repeated_over_budget"))

    def test_unversioned_mid_month_budget_change_is_not_reconstructed(self):
        self.add_budget("Restaurants", "2026-09", 100)
        self.add_budget("Restaurants", "2026-09", 200)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (80, 80))
        self.assertFalse(self.of_type("category_repeated_over_budget"))

    def test_category_spending_worsening_has_direction_and_evidence(self):
        self.add_month(1, "2026-06", (45, 45))
        self.add_month(3, "2026-07", (62.5, 62.5))
        self.add_month(5, "2026-08", (85, 85))
        pattern = self.of_type("category_spending_worsening")[0]
        self.assertTrue(pattern["eligible_for_coach"])
        self.assertEqual(pattern["direction"], "worsening")
        self.assertEqual(pattern["observations"]["absolute_change_eur"], 80.0)

    def test_single_event_outlier_is_not_coach_eligible_as_stable_worsening(self):
        self.add_month(1, "2026-06", (40, 40))
        self.add_month(3, "2026-07", (42.5, 42.5))
        self.add_month(5, "2026-08", (310,))
        pattern = self.of_type("category_spending_worsening")[0]
        self.assertTrue(pattern["observations"]["single_expense_dominated"])
        self.assertFalse(pattern["eligible_for_coach"])
        self.assertFalse(pattern["eligible_for_report"])

    def test_category_spending_improving_is_positive_shadow_evidence(self):
        self.add_month(1, "2026-06", (90, 90))
        self.add_month(3, "2026-07", (70, 70))
        self.add_month(5, "2026-08", (45, 45))
        pattern = self.of_type("category_spending_improving")[0]
        self.assertTrue(pattern["eligible_for_coach"])
        self.assertEqual(pattern["direction"], "improving")

    def test_missing_month_is_not_treated_as_zero_improvement(self):
        self.add_month(1, "2026-06", (90, 90))
        self.add_month(3, "2026-08", (45, 45))
        self.assertFalse(self.of_type("category_spending_improving"))

    def test_small_monthly_fluctuation_does_not_create_trend(self):
        self.add_month(1, "2026-06", (50, 50))
        self.add_month(3, "2026-07", (51.5, 51.5))
        self.add_month(5, "2026-08", (49, 49))
        self.assertFalse(self.of_type("category_spending_worsening"))
        self.assertFalse(self.of_type("category_spending_improving"))

    def test_essential_category_is_observed_but_not_behavioral_coaching(self):
        self.add_month(1, "2026-06", (50, 50), "Lebensmittel")
        self.add_month(3, "2026-07", (75, 75), "Lebensmittel")
        self.add_month(5, "2026-08", (100, 100), "Lebensmittel")
        pattern = self.of_type("category_spending_worsening")[0]
        self.assertFalse(pattern["eligible_for_coach"])
        self.assertFalse(pattern["eligible_for_report"])
        self.assertEqual(pattern["financial_relevance"], "low")

    def test_pharmacy_trend_is_not_behavioral_coaching(self):
        self.add_month(1, "2026-06", (50, 50), "Gesundheit")
        self.add_month(3, "2026-07", (75, 75), "Gesundheit")
        self.add_month(5, "2026-08", (100, 100), "Gesundheit")
        pattern = self.of_type("category_spending_worsening")[0]
        self.assertFalse(pattern["eligible_for_coach"])
        self.assertFalse(pattern["eligible_for_report"])

    def test_incomplete_current_month_uses_fair_elapsed_baseline(self):
        now = datetime(2026, 9, 15, 12, 0, 0)
        self.add_month(1, "2026-06", (50, 50))
        self.add_month(3, "2026-07", (50, 50))
        self.add_month(5, "2026-08", (50, 50))
        self.add_month(7, "2026-09", (50, 50))
        patterns = detect_behavior_patterns(self.conn, 1, now=now)
        self.assertFalse(any(
            pattern["pattern_type"] == "behavior_change_vs_personal_baseline"
            for pattern in patterns
        ))
        self.assertFalse(any(
            pattern.get("observations", {}).get("comparison_mode") == "full_month_scaled"
            for pattern in patterns
        ))

    def test_current_month_compares_the_same_day_segment(self):
        now = datetime(2026, 9, 15, 12, 0, 0)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08")):
            self.add_month(index * 2 + 1, month, (50, 50))
        self.add_month(7, "2026-09", (50, 50))
        patterns = detect_behavior_patterns(self.conn, 1, now=now)
        baseline = [
            pattern for pattern in patterns
            if pattern["pattern_type"] == "behavior_change_vs_personal_baseline"
        ]
        self.assertFalse(baseline)

    def test_february_segment_uses_valid_month_length(self):
        now = datetime(2026, 3, 31, 12, 0, 0)
        for index, month in enumerate(("2025-12", "2026-01", "2026-02")):
            self.add_month(index * 2 + 1, month, (50, 50))
        self.add_month(7, "2026-03", (50, 50))
        patterns = detect_behavior_patterns(self.conn, 1, now=now)
        self.assertFalse(any(
            pattern["pattern_type"] == "behavior_change_vs_personal_baseline"
            for pattern in patterns
        ))

    def test_thirty_one_day_current_month_clamps_shorter_baselines(self):
        now = datetime(2026, 5, 31, 12, 0, 0)
        rows = (
            (1, "2026-02", "2026-02-28 12:00:00"),
            (2, "2026-03", "2026-03-31 12:00:00"),
            (3, "2026-04", "2026-04-30 12:00:00"),
            (4, "2026-05", "2026-05-31 12:00:00"),
        )
        for expense_id, month, timestamp in rows:
            self.add_expense(expense_id, "Restaurant", "Restaurants", 50, timestamp)
        patterns = detect_behavior_patterns(self.conn, 1, now=now)
        self.assertFalse(any(
            pattern["pattern_type"] == "behavior_change_vs_personal_baseline"
            for pattern in patterns
        ))

    def test_personal_baseline_does_not_promote_single_expense_dominance(self):
        now = datetime(2026, 9, 30, 12, 0, 0)
        self.add_month(1, "2026-06", (50,))
        self.add_month(3, "2026-07", (50,))
        self.add_month(5, "2026-08", (50,))
        self.add_month(7, "2026-09", (250,))
        patterns = detect_behavior_patterns(self.conn, 1, now=now)
        baseline = [
            pattern for pattern in patterns
            if pattern["pattern_type"] == "behavior_change_vs_personal_baseline"
        ]
        self.assertEqual(len(baseline), 1)
        self.assertTrue(baseline[0]["observations"]["single_expense_dominated"])
        self.assertFalse(baseline[0]["eligible_for_coach"])

    def test_backfilled_transaction_uses_its_business_date_not_import_time(self):
        self.add_month(1, "2026-06", (45, 45))
        self.add_month(3, "2026-07", (62.5, 62.5))
        self.add_expense(
            5, "Restaurant", "Restaurants", 170,
            "2026-09-30 12:00:00",
            transaction_at="2026-08-05 12:00:00",
        )
        pattern = self.of_type("category_spending_worsening")[0]
        self.assertEqual(pattern["period_end"], "2026-08-31")
        self.assertNotEqual(pattern["last_seen_at"], "2026-09-30T12:00:00")

    def test_category_reclassification_has_one_current_canonical_history(self):
        self.add_month(1, "2026-06", (45, 45))
        self.add_month(3, "2026-07", (62.5, 62.5))
        self.add_month(5, "2026-08", (85, 85))
        self.conn.execute("UPDATE expenses SET category='Lebensmittel' WHERE id=1")
        patterns = self.patterns()
        restaurant_patterns = [
            pattern for pattern in patterns
            if pattern.get("category") == "Restaurants"
            and pattern["pattern_type"] == "category_spending_worsening"
        ]
        food_patterns = [
            pattern for pattern in patterns
            if pattern.get("category") == "Lebensmittel"
            and pattern["pattern_type"] == "category_spending_worsening"
        ]
        self.assertEqual(len(restaurant_patterns), 1)
        self.assertNotIn("1", restaurant_patterns[0]["source_ids"])
        self.assertEqual(len(food_patterns), 0)

    def test_missing_monthly_budgets_do_not_create_repeated_budget_pattern(self):
        self.add_budget("Restaurants", "2026-06")
        self.add_budget("Restaurants", "2026-08")
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (80, 80))
        self.assertFalse(self.of_type("category_repeated_over_budget"))

    def test_single_extreme_transaction_is_not_coach_eligible(self):
        for index, month in enumerate(("2026-06", "2026-07", "2026-08")):
            self.add_budget("Restaurants", month)
            self.add_month(index + 1, month, (200,))
        pattern = self.of_type("category_repeated_over_budget")[0]
        self.assertTrue(pattern["observations"]["single_expense_dominated"])
        self.assertFalse(pattern["eligible_for_coach"])

    def test_historical_combination_supersedes_components(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-06", (60, 60))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (100, 100))
        patterns = self.patterns()
        combined = [
            pattern for pattern in patterns
            if pattern["pattern_type"] == "repeated_discretionary_budget_pressure"
        ]
        self.assertEqual(len(combined), 1)
        self.assertTrue(combined[0]["eligible_for_coach"])
        self.assertTrue(combined[0]["eligible_for_report"])
        self.assertTrue(any(
            pattern["pattern_type"] == "category_repeated_over_budget"
            and pattern["superseded_by_pattern_id"] == combined[0]["pattern_id"]
            for pattern in patterns
        ))
        self.assertTrue(any(
            pattern["pattern_type"] == "category_spending_worsening"
            and pattern["superseded_by_pattern_id"] == combined[0]["pattern_id"]
            for pattern in patterns
        ))

    def test_current_budget_pressure_is_superseded_by_historical_combination(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-06", (60, 60))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (100, 100))
        self.add_month(7, "2026-09", (80, 80))
        patterns = self.patterns()
        combined = [
            pattern for pattern in patterns
            if pattern["pattern_type"] == "repeated_discretionary_budget_pressure"
        ]
        current_pressure = [
            pattern for pattern in patterns
            if pattern["pattern_type"] == "category_budget_pressure"
        ]
        self.assertEqual(len(combined), 1)
        self.assertEqual(len(current_pressure), 1)
        self.assertFalse(current_pressure[0]["eligible_for_coach"])
        self.assertIn(current_pressure[0]["pattern_id"], combined[0]["related_pattern_ids"])

    def _salary_cycle_fixture(self, *, post_amount: float = 200, baseline_amount: float = 10):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(
                100 + index,
                "Restaurant",
                "Restaurants",
                post_amount / 2,
                f"{month}-02 12:00:00",
            )
            self.add_expense(
                300 + index,
                "Restaurant",
                "Restaurants",
                post_amount / 2,
                f"{month}-03 12:00:00",
            )
        for index, day in enumerate((10, 11, 12), 1):
            self.add_expense(
                200 + index,
                "Restaurant",
                "Restaurants",
                baseline_amount,
                f"2026-05-{day:02d} 12:00:00",
            )

    def test_post_income_discretionary_spike_requires_three_salary_cycles(self):
        self._salary_cycle_fixture()
        patterns = self.of_type("post_income_discretionary_spike")
        self.assertEqual(len(patterns), 1)
        pattern = patterns[0]
        self.assertEqual(pattern["observations"]["salary_cycles"], 3)
        self.assertIn(pattern["observations"]["window_days"], (3, 7))
        self.assertEqual(pattern["observations"]["available_windows"], [3, 7])
        self.assertTrue(pattern["observations"]["temporal_correlation_only"])
        self.assertTrue(pattern["eligible_for_coach"])

    def test_unknown_credits_bonus_and_transfers_are_not_salary_cycles(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            label = "Gutschrift" if month == "2026-06" else "Bonus" if month == "2026-07" else "Umbuchung"
            self.add_income(index, created_at=f"{month}-01 09:00:00", label=label)
        self.add_income(9, created_at="2026-06-20 09:00:00", label="Gehalt")
        self.assertFalse(self.of_type("post_income_discretionary_spike"))
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertLess(inspector["post_income_shadow"]["cycles_detected"], 3)

    def test_special_payments_are_not_salary_cycles(self):
        self.configure_budget_plan()
        for label in ("13. Gehalt", "Weihnachtsgeld", "Weihnachtsgeld Gehalt", "Urlaubsgeld"):
            for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
                self.add_income(index, created_at=f"{month}-01 09:00:00", label=label)
            inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
            self.assertEqual(
                inspector["post_income_shadow"]["cycles_detected"],
                0,
                label,
            )
            self.conn.execute("DELETE FROM app_cash_movements")

    def test_parallel_income_sources_are_not_blindly_merged(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00", label="Gehalt")
            self.add_income(10 + index, created_at=f"{month}-15 09:00:00", label="Lohn Nebenjob")
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        selected_labels = [event["label"] for event in inspector["post_income_shadow"]["income_events"]]
        self.assertEqual(selected_labels, ["Gehalt", "Gehalt", "Gehalt"])

    def test_single_large_purchase_per_cycle_is_not_a_pattern(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(100 + index, "Amazon", "Shopping", 500, f"{month}-02 12:00:00")
        for index, day in enumerate((10, 11, 12), 1):
            self.add_expense(200 + index, "Restaurant", "Restaurants", 10, f"2026-05-{day:02d} 12:00:00")
        self.assertFalse(any(
            pattern["pattern_type"].startswith("post_income_")
            and pattern["eligible_for_coach"]
            for pattern in self.patterns()
        ))

    def test_multiple_discretionary_purchases_per_cycle_can_form_a_pattern(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(300 + index, "Amazon", "Shopping", 180, f"{month}-02 12:00:00")
            self.add_expense(400 + index, "Zalando", "Shopping", 180, f"{month}-03 12:00:00")
        for index, day in enumerate((10, 11, 12), 1):
            self.add_expense(500 + index, "Restaurant", "Restaurants", 10, f"2026-05-{day:02d} 12:00:00")
        self.assertTrue(self.of_type("post_income_discretionary_spike"))

    def test_single_cycle_outlier_does_not_form_stable_pattern(self):
        self.configure_budget_plan()
        for index, (month, amount) in enumerate(
            (("2026-06", 80), ("2026-07", 85), ("2026-08", 310)),
            1,
        ):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(600 + index, "Amazon", "Shopping", amount, f"{month}-02 12:00:00")
        for index, day in enumerate((10, 11, 12), 1):
            self.add_expense(700 + index, "Restaurant", "Restaurants", 10, f"2026-05-{day:02d} 12:00:00")
        self.assertFalse(self.of_type("post_income_discretionary_spike"))

    def test_missing_income_event_date_is_fail_closed(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
        self.conn.execute("UPDATE app_cash_movements SET occurred_at=NULL")
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertEqual(inspector["post_income_shadow"]["cycles_detected"], 0)

    def test_backfill_uses_event_date_not_import_time(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-20 09:00:00")
            self.conn.execute(
                "UPDATE app_cash_movements SET occurred_at=? WHERE id=?",
                (f"{month}-01 09:00:00", index),
            )
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertTrue(inspector["post_income_shadow"]["income_events"][0]["occurred_at"].startswith("2026-06-01"))

    def test_small_post_income_spikes_are_conservatively_suppressed(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(800 + index, "Amazon", "Shopping", 20, f"{month}-02 12:00:00")
        for index, day in enumerate((10, 11, 12), 1):
            self.add_expense(900 + index, "Restaurant", "Restaurants", 1, f"2026-05-{day:02d} 12:00:00")
        self.assertFalse(any(
            pattern["pattern_type"].startswith("post_income_")
            for pattern in self.patterns()
        ))

    def test_unlabeled_income_amount_is_not_promoted_to_salary(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00", label="")
        self.assertFalse(self.of_type("post_income_discretionary_spike"))

    def test_essentials_after_income_are_not_behavioral_coach_evidence(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(
                300 + index,
                "Lidl",
                "Lebensmittel",
                300,
                f"{month}-02 12:00:00",
            )
        self.assertFalse(any(
            pattern["pattern_type"].startswith("post_income_")
            and pattern["eligible_for_coach"]
            for pattern in self.patterns()
        ))

    def test_post_income_budget_pressure_requires_negative_total_budget(self):
        self._salary_cycle_fixture(post_amount=200)
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_financial_snapshot(month)
        self.add_budget("Restaurants", "2026-09", 50)
        self.assertFalse(self.of_type("post_income_budget_pressure"))
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_expense(
                400 + index,
                "Haushalt",
                "Sonstiges",
                2200,
                f"{month}-10 12:00:00",
            )
        pressure = self.of_type("post_income_budget_pressure")
        self.assertEqual(len(pressure), 1)
        self.assertEqual(pressure[0]["observations"]["budget_pressure_months"], ["2026-06", "2026-07", "2026-08"])
        self.assertFalse(self.of_type("post_income_discretionary_spike")[0]["eligible_for_coach"])

    def test_category_budget_overrun_alone_does_not_create_post_income_budget_pressure(self):
        self._salary_cycle_fixture()
        self.add_budget("Restaurants", "2026-06", 1)
        self.add_budget("Restaurants", "2026-07", 1)
        self.add_budget("Restaurants", "2026-08", 1)
        self.assertFalse(self.of_type("post_income_budget_pressure"))

    def test_incomplete_latest_salary_window_suppresses_pattern(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-07", "2026-08", "2026-09"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(500 + index, "Restaurant", "Restaurants", 200, f"{month}-02 12:00:00")
        self.assertFalse(self.of_type("post_income_discretionary_spike"))
        inspector = build_shadow_inspector(self.conn, 1, now=datetime(2026, 9, 5, 12, 0, 0))
        self.assertEqual(inspector["post_income_shadow"]["eligibility_reason"], "post_income_window_incomplete")

    def test_shadow_inspector_exposes_salary_metadata_without_affecting_coach_v3(self):
        self._salary_cycle_fixture()
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        shadow = inspector["post_income_shadow"]
        self.assertFalse(inspector["coach_v3_affected"])
        self.assertEqual(shadow["cycles_used"], 3)
        self.assertEqual(len(shadow["income_events"]), 3)
        self.assertIn("overall_budget_status", shadow)
        self.assertIn("baseline", shadow)

    def test_two_income_events_in_one_month_do_not_create_a_cycle(self):
        self.configure_budget_plan()
        for index, created_at in enumerate((
            "2026-06-01 09:00:00",
            "2026-06-15 09:00:00",
            "2026-07-01 09:00:00",
            "2026-08-01 09:00:00",
        ), 1):
            self.add_income(index, created_at=created_at)
        self.assertFalse(self.of_type("post_income_discretionary_spike"))


if __name__ == "__main__":
    unittest.main()
