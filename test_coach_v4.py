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
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1);
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
                created_at TEXT NOT NULL
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
            "INSERT INTO app_cash_movements VALUES (1,1,'transfer',500,NULL,'2026-09-16 08:00:00')"
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


if __name__ == "__main__":
    unittest.main()
