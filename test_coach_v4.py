from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime

from rove_behavior_patterns import (
    build_behavior_insights,
    build_shadow_inspector,
    detect_behavior_patterns,
)


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
                occurred_at TEXT,
                label TEXT
            );
            CREATE TABLE category_budgets (
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                monthly_limit REAL NOT NULL,
                active_month TEXT NOT NULL
            );
            CREATE TABLE app_contracts (
                user_id INTEGER NOT NULL,
                contract_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                frequency TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, contract_id)
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

    def patterns_at(self, now):
        return detect_behavior_patterns(self.conn, 1, now=now)

    def insights(self):
        return build_behavior_insights(self.patterns())

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
            "(id,user_id,kind,amount,expense_id,created_at,label) VALUES (?,?,?,?,?,?,?)",
            (movement_id, 1, "income", amount, None, created_at, label),
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

    def add_contract(
        self,
        contract_id: str,
        name: str,
        amount: float,
        *,
        category: str = "Abos",
        frequency: str | None = "monthly",
        status: str = "active",
        active: int = 1,
    ) -> None:
        self.conn.execute(
            """INSERT INTO app_contracts
               (user_id, contract_id, name, category, amount, frequency, status, active,
                created_at, updated_at)
               VALUES (1,?,?,?,?,?,?,?,?,?)""",
            (
                contract_id,
                name,
                category,
                amount,
                frequency,
                status,
                active,
                "2026-09-01 09:00:00",
                "2026-09-01 09:00:00",
            ),
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
        for index, (day, amount) in enumerate(((6, 30), (13, 28), (20, 28)), 1):
            self.add_expense(index, "Lieferando", "Restaurants", amount, f"2026-09-{day:02d} 19:00:00")
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
        self.assertEqual(combined[0]["composite_type"], "behavior_driving_budget_pressure")
        self.assertEqual(combined[0]["pattern_kind"], "composite_behavior_pattern")

    def test_category_pressure_without_behavior_does_not_fuse(self):
        self.conn.execute(
            "INSERT INTO category_budgets VALUES (1,'Restaurants',100,'2026-09')"
        )
        self.add_expense(1, "Restaurant", "Restaurants", 160, "2026-09-15 19:00:00")
        self.assertFalse(any(
            pattern.get("composite_type") == "behavior_driving_budget_pressure"
            for pattern in self.patterns()
        ))

    def test_healthy_overall_budget_keeps_behavior_fusion_low_relevance(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.conn.execute(
            "INSERT INTO category_budgets VALUES (1,'Shopping',100,'2026-09')"
        )
        for index, day in enumerate((6, 13, 20, 27), 1):
            self.add_expense(index, "Amazon", "Shopping", 40, f"2026-09-{day:02d} 21:00:00")
        combined = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "behavior_driving_budget_pressure"
        ]
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["financial_relevance"], "low")
        self.assertFalse(combined[0]["eligible_for_coach"])

    def test_historical_slight_category_overrun_stays_low_when_overall_is_healthy(self):
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_budget("Restaurants", month, 100)
            self.add_financial_snapshot(month)
        self.add_month(1, "2026-06", (55, 55))
        self.add_month(3, "2026-07", (57.5, 57.5))
        self.add_month(5, "2026-08", (60, 60))
        combined = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "repeated_discretionary_budget_pressure"
        ]
        self.assertEqual(len(combined), 1)
        self.assertNotEqual(combined[0]["financial_relevance"], "high")
        self.assertFalse(combined[0]["eligible_for_coach"])
        self.assertEqual(
            combined[0]["observations"]["overall_pressure_months"],
            [],
        )

    def test_strong_category_and_repeated_overall_pressure_can_be_high(self):
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_budget("Restaurants", month, 100)
            self.add_financial_snapshot(month, income=500, fixed_costs=300)
        self.add_month(1, "2026-06", (150, 150))
        self.add_month(3, "2026-07", (160, 160))
        self.add_month(5, "2026-08", (170, 170))
        combined = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "repeated_discretionary_budget_pressure"
        ]
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["financial_relevance"], "high")
        self.assertTrue(combined[0]["eligible_for_coach"])

    def test_repeated_overspend_composite_requires_worsening_evidence(self):
        for month, amounts in (
            ("2026-06", (60, 60)),
            ("2026-07", (80, 80)),
            ("2026-08", (100, 100)),
        ):
            self.add_budget("Restaurants", month)
            self.add_month(int(month[-2:]) * 10, month, amounts)
        combined = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "repeated_discretionary_overspend"
        ]
        self.assertEqual(len(combined), 1)
        self.assertTrue(combined[0]["eligible_for_coach"])
        self.assertEqual(combined[0]["direction"], "worsening")
        self.assertFalse(combined[0]["conflicting_evidence"])

    def test_improving_category_creates_positive_composite(self):
        self.add_month(1, "2026-06", (90, 90))
        self.add_month(3, "2026-07", (70, 70))
        self.add_month(5, "2026-08", (45, 45))
        positive = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "category_spending_improvement_confirmed"
        ]
        self.assertEqual(len(positive), 1)
        self.assertEqual(positive[0]["direction"], "improving")
        self.assertEqual(positive[0]["pattern_strength"], "high")

    def test_budget_recovery_requires_completed_current_month(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (80, 80))
        self.add_month(7, "2026-09", (20, 20))
        positive = [
            pattern for pattern in self.patterns_at(datetime(2026, 9, 30, 23, 59, 59))
            if pattern.get("composite_type") == "budget_recovery"
        ]
        self.assertEqual(len(positive), 1)
        self.assertEqual(positive[0]["direction"], "improving")

    def test_budget_recovery_not_complete_at_last_day_noon(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (80, 80))
        self.add_month(7, "2026-09", (20, 20))
        self.assertFalse(any(
            pattern.get("composite_type") == "budget_recovery"
            for pattern in self.patterns()
        ))

    def test_budget_recovery_is_suppressed_when_current_budget_was_increased(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month, 100)
        self.add_budget("Restaurants", "2026-09", 200)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (80, 80))
        self.add_month(7, "2026-09", (75, 75))
        self.assertFalse(any(
            pattern.get("composite_type") == "budget_recovery"
            for pattern in self.patterns()
        ))

    def test_historical_worsening_and_current_recovery_are_conflicting(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month, 100)
        self.add_month(1, "2026-06", (75, 75))
        self.add_month(3, "2026-07", (90, 90))
        self.add_month(5, "2026-08", (110, 110))
        self.add_month(7, "2026-09", (40, 40))
        full_close = datetime(2026, 9, 30, 23, 59, 59)
        patterns = self.patterns_at(full_close)
        composites = [
            pattern for pattern in patterns
            if pattern.get("pattern_kind") == "composite_behavior_pattern"
            and pattern.get("category") == "Restaurants"
        ]
        self.assertTrue(any(pattern.get("composite_type") == "budget_recovery" for pattern in composites))
        self.assertTrue(any(
            pattern.get("composite_type") in {
                "repeated_discretionary_overspend",
                "repeated_discretionary_budget_pressure",
            }
            for pattern in composites
        ))
        recovery_id = next(
            pattern["pattern_id"]
            for pattern in composites
            if pattern.get("composite_type") == "budget_recovery"
        )
        historical = next(
            pattern for pattern in composites
            if pattern.get("composite_type") != "budget_recovery"
        )
        self.assertTrue(historical.get("superseded_by_pattern_id") == recovery_id)
        self.assertTrue(next(
            pattern["eligible_for_coach"]
            for pattern in composites
            if pattern.get("composite_type") == "budget_recovery"
        ))
        inspector = build_shadow_inspector(self.conn, 1, now=full_close)
        self.assertFalse(inspector["conflicting_evidence"])
        self.assertEqual(
            inspector["primary_composite"],
            next(
                pattern["pattern_id"]
                for pattern in composites
                if pattern.get("composite_type") == "budget_recovery"
            ),
        )
        self.assertFalse(any(
            pattern.get("composite_type") == "repeated_discretionary_overspend"
            and pattern.get("primary_composite")
            for pattern in composites
        ))

    def test_three_weak_months_do_not_create_high_composite(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month, 100)
        self.add_month(1, "2026-06", (52.5, 52.5))
        self.add_month(3, "2026-07", (55, 55))
        self.add_month(5, "2026-08", (57.5, 57.5))
        combined = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "repeated_discretionary_budget_pressure"
        ]
        self.assertEqual(len(combined), 1)
        self.assertNotEqual(combined[0]["financial_relevance"], "high")
        self.assertFalse(combined[0]["eligible_for_coach"])

    def test_positive_composite_is_suppressed_by_conflicting_current_worsening(self):
        self.add_month(1, "2026-06", (90, 90))
        self.add_month(3, "2026-07", (70, 70))
        self.add_month(5, "2026-08", (45, 45))
        self.add_month(7, "2026-09", (200, 200))
        self.assertTrue(self.of_type("category_spending_improving"))
        self.assertTrue(self.of_type("behavior_change_vs_personal_baseline"))
        self.assertFalse(any(
            pattern.get("composite_type") == "category_spending_improvement_confirmed"
            for pattern in self.patterns()
        ))

    def test_missing_history_does_not_create_positive_composite(self):
        self.add_month(1, "2026-06", (90, 90))
        self.add_month(3, "2026-08", (45, 45))
        self.assertFalse(any(
            pattern.get("pattern_kind") == "composite_behavior_pattern"
            and pattern.get("direction") == "improving"
            for pattern in self.patterns()
        ))

    def test_shadow_inspector_exposes_raw_and_composite_patterns(self):
        self.conn.execute(
            "INSERT INTO category_budgets VALUES (1,'Shopping',100,'2026-09')"
        )
        for index, day in enumerate((6, 13, 20, 27), 1):
            self.add_expense(index, "Amazon", "Shopping", 40, f"2026-09-{day:02d} 21:00:00")
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertTrue(inspector["composite_patterns"])
        self.assertTrue(any(
            pattern["pattern_type"] == "merchant_weekday_pattern"
            for pattern in inspector["patterns"]
        ))
        self.assertTrue(any(
            pattern["composite_type"] == "behavior_driving_budget_pressure"
            for pattern in inspector["composite_patterns"]
        ))
        self.assertFalse(inspector["coach_v3_affected"])

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

    def test_transfer_with_later_card_reference_stays_out_of_shadow_inspector(self):
        for index, day in enumerate((16, 17, 18), 1):
            self.add_expense(index, "Amazon", "Shopping", 500, f"2026-09-{day:02d} 21:00:00")
            self.conn.execute(
                "INSERT INTO app_cash_movements "
                "(id,user_id,kind,amount,expense_id,created_at) VALUES (?,?,?,?,?,?)",
                (index * 2 - 1, 1, "transfer", 500, index, f"2026-09-{day:02d} 21:00:00"),
            )
            self.conn.execute(
                "INSERT INTO app_cash_movements "
                "(id,user_id,kind,amount,expense_id,created_at) VALUES (?,?,?,?,?,?)",
                (index * 2, 1, "card", 500, index, f"2026-09-{day:02d} 21:01:00"),
            )
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertFalse(any(
            pattern["pattern_type"] in {
                "late_night_discretionary_spending",
                "merchant_weekday_pattern",
            }
            and pattern["eligible_for_coach"]
            for pattern in inspector["patterns"]
        ))
        self.assertIsNone(inspector["primary_coach_insight"])

    def test_investment_with_later_card_reference_stays_out_of_shadow_inspector(self):
        for index, day in enumerate((16, 17, 18), 1):
            self.add_expense(index, "Broker", "Shopping", 500, f"2026-09-{day:02d} 21:00:00")
            self.conn.execute(
                "INSERT INTO app_cash_movements "
                "(id,user_id,kind,amount,expense_id,created_at) VALUES (?,?,?,?,?,?)",
                (index * 2 - 1, 1, "investment", 500, index, f"2026-09-{day:02d} 21:00:00"),
            )
            self.conn.execute(
                "INSERT INTO app_cash_movements "
                "(id,user_id,kind,amount,expense_id,created_at) VALUES (?,?,?,?,?,?)",
                (index * 2, 1, "card", 500, index, f"2026-09-{day:02d} 21:01:00"),
            )
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertFalse(any(pattern["eligible_for_coach"] for pattern in inspector["patterns"]))

    def test_unknown_explicit_movement_kind_fails_closed(self):
        for index, day in enumerate((16, 17, 18), 1):
            self.add_expense(index, "Amazon", "Shopping", 500, f"2026-09-{day:02d} 21:00:00")
            self.conn.execute(
                "INSERT INTO app_cash_movements "
                "(id,user_id,kind,amount,expense_id,created_at) VALUES (?,?,?,?,?,?)",
                (index, 1, "mystery", 500, index, f"2026-09-{day:02d} 21:00:00"),
            )
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertFalse(any(pattern["eligible_for_coach"] for pattern in inspector["patterns"]))

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

    def test_suppressed_historical_composite_cannot_supersede_current_pressure(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month, 100)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_financial_snapshot(month)
            self.add_month(index * 10, month, (300,))
        self.add_expense(50, "Restaurant", "Restaurants", 220, "2026-09-15 12:00:00")
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        historical = next(
            pattern for pattern in inspector["composite_patterns"]
            if pattern.get("composite_type") in {
                "repeated_discretionary_overspend",
                "repeated_discretionary_budget_pressure",
            }
        )
        current = next(
            pattern for pattern in inspector["patterns"]
            if pattern["pattern_type"] == "category_budget_pressure"
        )
        self.assertFalse(historical["eligible_for_coach"])
        self.assertFalse(historical.get("primary_composite", False))
        self.assertTrue(current["eligible_for_coach"])
        self.assertIsNone(current.get("superseded_by_pattern_id"))
        self.assertEqual(
            inspector["primary_coach_insight"],
            next(
                insight["insight_id"]
                for insight in inspector["insight_candidates"]
                if insight["primary_coach_insight"]
                and insight["insight_type"] == "budget_attention"
                and insight["period_start"] == "2026-09-01"
            ),
        )

    def test_overlapping_same_category_composites_have_one_primary(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month, 100)
        self.add_month(1, "2026-06", (90, 90))
        self.add_month(3, "2026-07", (100, 100))
        self.add_month(5, "2026-08", (110, 110))
        for index, day in enumerate((1, 2, 3, 4), 7):
            self.add_expense(index, "Amazon", "Restaurants", 40, f"2026-09-{day:02d} 21:00:00")
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        composites = [
            pattern for pattern in inspector["composite_patterns"]
            if pattern.get("category") == "Restaurants"
        ]
        self.assertGreaterEqual(len(composites), 2)
        self.assertEqual(
            sum(pattern.get("primary_composite", False) for pattern in composites),
            1,
        )
        self.assertTrue(any(
            pattern.get("superseded_by_pattern_id")
            for pattern in composites
            if not pattern.get("primary_composite")
        ))

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
        suppressed = [
            insight for insight in inspector["insight_candidates"]
            if insight["insight_type"] == "post_income_pattern"
        ]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0]["suppression_reason"], "uncertain_event_time")
        self.assertFalse(suppressed[0]["coach_eligible"])
        self.assertFalse(suppressed[0]["report_eligible"])

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

    def test_similar_video_services_form_a_shadow_cluster(self):
        self.add_contract("c1", "Netflix", 15)
        self.add_contract("c2", "Disney+", 15)
        pattern = next(
            pattern for pattern in self.of_type("similar_recurring_services")
            if pattern["observations"]["cluster_type"] == "streaming_video"
        )
        self.assertEqual(pattern["observations"]["service_count"], 2)
        self.assertEqual(pattern["observations"]["monthly_normalized_total"], 30.0)
        self.assertEqual(pattern["financial_relevance"], "low")
        self.assertFalse(pattern["eligible_for_coach"])

    def test_duplicate_service_rows_count_once_and_expose_family_metadata(self):
        self.add_contract("n1", "Netflix", 15)
        self.add_contract("n2", "NETFLIX.COM", 15)
        self.add_contract("d1", "Disney+", 15)
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertEqual(pattern["observations"]["service_count"], 2)
        self.assertEqual(pattern["observations"]["monthly_normalized_total"], 30.0)
        netflix = next(
            service for service in pattern["observations"]["services"]
            if service["service_key"] == "streaming_video:netflix"
        )
        self.assertEqual(netflix["contract_row_count"], 2)
        self.assertEqual(netflix["contract_ids"], ["n1", "n2"])
        self.assertTrue(netflix["duplicate_ambiguous"])
        self.assertTrue(pattern["observations"]["duplicate_ambiguous"])

    def test_ambiguous_duplicate_amounts_are_not_summed(self):
        self.add_contract("n1", "Netflix", 15)
        self.add_contract("n2", "Netflix", 25)
        self.add_contract("d1", "Disney+", 15)
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertIsNone(pattern["observations"]["monthly_normalized_total"])
        self.assertEqual(pattern["financial_relevance"], "low")
        self.assertTrue(pattern["observations"]["duplicate_ambiguous"])

    def test_unverified_activity_keeps_shadow_evidence_ineligible(self):
        self.conn.execute("DROP TABLE app_contracts")
        self.conn.execute(
            """CREATE TABLE app_contracts (
                user_id INTEGER NOT NULL,
                contract_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                frequency TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, contract_id)
            )"""
        )
        self.conn.executemany(
            """INSERT INTO app_contracts
               (user_id, contract_id, name, category, amount, frequency, created_at, updated_at)
               VALUES (1, ?, ?, 'Abos', ?, 'monthly', '2026-09-01 09:00:00', '2026-09-01 09:00:00')""",
            (("n1", "Netflix", 50), ("d1", "Disney+", 50)),
        )
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertEqual(pattern["observations"]["activity_confidence"], "unknown")
        self.assertEqual(pattern["observations"]["data_quality"], "activity_unknown")
        self.assertFalse(pattern["eligible_for_coach"])
        self.assertFalse(pattern["eligible_for_report"])

    def test_explicit_sky_and_wow_products_are_classified_but_generic_names_are_not(self):
        self.add_contract("s1", "Sky Stream", 25)
        self.add_contract("s2", "Sky Cinema", 15)
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertEqual(pattern["observations"]["cluster_type"], "streaming_video")

        self.tearDown()
        self.setUp()
        self.add_contract("s1", "Sky", 25)
        self.add_contract("s2", "WOW", 15)
        self.assertFalse(self.of_type("similar_recurring_services"))

    def test_different_service_clusters_are_not_merged(self):
        self.add_contract("c1", "Netflix", 15)
        self.add_contract("c2", "Spotify", 10)
        self.assertFalse(self.of_type("similar_recurring_services"))

    def test_video_and_sport_services_keep_separate_clusters(self):
        for index, name in enumerate(("Netflix", "Disney+", "Paramount+"), 1):
            self.add_contract(f"v{index}", name, 20)
        self.add_contract("sport", "DAZN", 30)
        patterns = self.of_type("similar_recurring_services")
        self.assertEqual(
            {pattern["observations"]["cluster_type"] for pattern in patterns},
            {"streaming_video"},
        )

    def test_five_similar_services_have_high_pattern_strength(self):
        for index, name in enumerate(("Netflix", "Disney+", "Paramount+", "WOW TV", "RTL+"), 1):
            self.add_contract(f"c{index}", name, 20)
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertEqual(pattern["pattern_strength"], "high")
        self.assertEqual(pattern["financial_relevance"], "high")
        self.assertEqual(pattern["observations"]["monthly_normalized_total"], 100.0)

    def test_annual_service_is_normalized_only_with_explicit_frequency(self):
        self.add_contract("c1", "Netflix", 120, frequency="annual")
        self.add_contract("c2", "Disney+", 15, frequency="monthly")
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertEqual(pattern["observations"]["monthly_normalized_total"], 25.0)
        self.assertTrue(pattern["observations"]["frequency_complete"])

    def test_unknown_frequency_is_not_artificially_normalized(self):
        self.add_contract("c1", "Netflix", 120, frequency=None)
        self.add_contract("c2", "Disney+", 15, frequency=None)
        pattern = self.of_type("similar_recurring_services")[0]
        self.assertIsNone(pattern["observations"]["monthly_normalized_total"])
        self.assertEqual(pattern["observations"]["data_quality"], "frequency_unknown")
        self.assertEqual(pattern["financial_relevance"], "low")

    def test_inactive_contract_is_not_counted(self):
        self.add_contract("c1", "Netflix", 15)
        self.add_contract("c2", "Disney+", 15, status="cancelled")
        self.assertFalse(self.of_type("similar_recurring_services"))

    def test_prime_is_conservative_and_shopping_is_not_a_service(self):
        self.add_contract("prime", "Amazon Prime", 8.99)
        self.add_contract("video", "Prime Video", 8.99)
        self.add_expense(1, "Amazon", "Shopping", 80, "2026-09-10 12:00:00")
        self.assertFalse(self.of_type("similar_recurring_services"))

    def test_unknown_service_is_not_guessed(self):
        self.add_contract("c1", "Unknown Merchant X", 20)
        self.add_contract("c2", "Unknown Merchant Y", 20)
        self.assertFalse(self.of_type("similar_recurring_services"))

    def test_same_contract_stream_has_stable_single_evidence_object(self):
        self.add_contract("c1", "Netflix", 20)
        self.add_contract("c2", "Disney+", 20)
        first = self.of_type("similar_recurring_services")[0]
        second = self.of_type("similar_recurring_services")[0]
        self.assertEqual(first["pattern_id"], second["pattern_id"])
        self.assertEqual(first["observations"]["service_count"], 2)

    def test_shadow_inspector_exposes_similarity_metadata_without_visible_coach(self):
        self.add_contract("c1", "Netflix", 20)
        self.add_contract("c2", "Disney+", 20)
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        self.assertFalse(inspector["coach_v3_affected"])
        self.assertIn("similar_recurring_shadow", inspector)
        pattern = next(pattern for pattern in inspector["patterns"] if pattern["pattern_type"] == "similar_recurring_services")
        self.assertIn("services", pattern["observations"])
        self.assertIn("monthly_normalized_total", pattern["observations"])
        self.assertFalse(pattern["eligible_for_report"])

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

    def test_insight_candidate_contract_is_structured_and_traceable(self):
        self.add_budget("Restaurants", "2026-09", 100)
        self.add_expense(1, "Restaurant", "Restaurants", 160, "2026-09-05 12:00:00")
        insight = self.insights()[0]
        self.assertEqual(insight["candidate_type"], "behavior_insight_candidate")
        self.assertEqual(insight["insight_type"], "budget_attention")
        self.assertEqual(insight["source_pattern_ids"], [insight["primary_pattern_id"]])
        self.assertEqual(insight["evidence_metrics"]["amount_spent"], 160.0)
        self.assertEqual(insight["coach_timing_hint"], "immediate")
        self.assertEqual(insight["report_section_hint"], "monthly_attention")
        self.assertTrue(insight["coach_eligible"])
        self.assertFalse(insight["report_eligible"])

    def test_repeated_spending_insight_preserves_amount_and_period_evidence(self):
        pattern = self.of_type("merchant_weekday_pattern")
        self.assertFalse(pattern)
        for index, (day, amount) in enumerate(((6, 30), (13, 28), (20, 28)), 1):
            self.add_expense(index, "Lieferando", "Restaurants", amount, f"2026-09-{day:02d} 19:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "repeated_spending_pattern"
            and insight["coach_eligible"]
        )
        self.assertEqual(insight["merchant"], "Lieferando")
        self.assertEqual(insight["category"], "Restaurants")
        self.assertEqual(insight["evidence_metrics"]["amount_total"], 86.0)
        self.assertEqual(insight["evidence_metrics"]["occurrence_date_count"], 3)
        self.assertEqual(insight["evidence_metrics"]["weekday_opportunities"], 4)
        self.assertEqual(insight["period_start"], "2026-09-01")
        self.assertEqual(insight["period_end"], "2026-09-30")

    def test_behavior_budget_composite_preserves_both_evidence_sides(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.add_budget("Restaurants", "2026-09", 100)
        for index, (day, amount) in enumerate(((6, 50), (13, 50), (20, 50)), 1):
            self.add_expense(index, "Lieferando", "Restaurants", amount, f"2026-09-{day:02d} 19:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["evidence_summary"]["composite_type"] == "behavior_driving_budget_pressure"
        )
        metrics = insight["evidence_metrics"]
        self.assertEqual(metrics["behavior_amount_total"], 150.0)
        self.assertEqual(metrics["behavior_transaction_count"], 3)
        self.assertEqual(metrics["budget_amount_spent"], 150.0)
        self.assertEqual(metrics["budget_monthly_limit"], 100.0)
        self.assertEqual(metrics["budget_amount_over"], 50.0)

    def test_historical_budget_insight_preserves_monthly_budget_evidence(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month, 100)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (100, 100))
        self.add_month(5, "2026-08", (130, 130))
        insight = next(
            insight for insight in self.insights()
            if insight["primary_pattern_id"].startswith("shadow:repeated_discretionary_budget_pressure")
        )
        metrics = insight["evidence_metrics"]
        self.assertEqual(metrics["months_over_budget"], 3)
        self.assertEqual(len(metrics["historical_budget_evidence"]), 3)
        self.assertEqual(
            [row["monthly_limit"] for row in metrics["historical_budget_evidence"]],
            [100.0, 100.0, 100.0],
        )

    def test_salary_time_suppression_does_not_use_created_at(self):
        self.configure_budget_plan()
        self.add_income(1, created_at="2026-08-01 09:00:00")
        self.conn.execute("UPDATE app_cash_movements SET occurred_at=NULL")
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        insight = next(
            insight for insight in inspector["insight_candidates"]
            if insight["suppression_reason"] == "uncertain_event_time"
        )
        self.assertIsNone(insight["period_start"])
        self.assertIsNone(insight["period_end"])
        self.assertFalse(insight["coach_eligible"])

    def test_healthy_historical_budget_is_not_a_coach_insight(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month, 100)
            self.add_financial_snapshot(month)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08")):
            self.add_month(index * 2 + 1, month, (60, 60))
        insights = self.insights()
        self.assertTrue(any(
            insight["suppression_reason"] == "healthy_overall_budget"
            for insight in insights
        ))
        self.assertFalse(any(
            insight["insight_type"] == "budget_attention"
            and insight["coach_eligible"]
            for insight in insights
        ))

    def test_current_category_overrun_with_healthy_overall_budget_is_suppressed(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.add_budget("Sonstiges", "2026-09", 100)
        self.add_expense(1, "Sonstiges", "Sonstiges", 228, "2026-09-14 12:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "budget_attention"
        )
        self.assertEqual(insight["evidence_metrics"]["amount_spent"], 228.0)
        self.assertEqual(insight["evidence_metrics"]["monthly_limit"], 100.0)
        self.assertEqual(insight["evidence_metrics"]["amount_over"], 128.0)
        self.assertEqual(insight["evidence_metrics"]["overall_budget_status"], "healthy")
        self.assertEqual(insight["suppression_reason"], "healthy_overall_budget")
        self.assertFalse(insight["coach_eligible"])
        self.assertFalse(insight["primary_coach_insight"])

    def test_current_category_overrun_can_be_coach_eligible_when_total_is_under_pressure(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.add_budget("Sonstiges", "2026-09", 100)
        self.add_expense(1, "Sonstiges", "Sonstiges", 228, "2026-09-14 12:00:00")
        self.add_expense(2, "Weitere Ausgabe", "Sonstiges", 1900, "2026-09-15 12:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "budget_attention"
        )
        self.assertEqual(insight["evidence_metrics"]["overall_budget_status"], "under_pressure")
        self.assertTrue(insight["coach_eligible"])
        self.assertIsNone(insight["suppression_reason"])

    def test_small_category_overrun_with_healthy_total_is_not_coach_eligible(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.add_budget("Sonstiges", "2026-09", 100)
        self.add_expense(1, "Sonstiges", "Sonstiges", 111, "2026-09-14 12:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "budget_attention"
        )
        self.assertEqual(insight["financial_relevance"], "medium")
        self.assertEqual(insight["suppression_reason"], "healthy_overall_budget")
        self.assertFalse(insight["coach_eligible"])

    def test_unknown_overall_budget_status_fails_closed(self):
        self.conn.execute("UPDATE users SET income=NULL, fixed_costs=NULL")
        self.add_budget("Sonstiges", "2026-09", 100)
        self.add_expense(1, "Sonstiges", "Sonstiges", 228, "2026-09-14 12:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "budget_attention"
        )
        self.assertEqual(insight["evidence_metrics"]["overall_budget_status"], "unknown")
        self.assertEqual(insight["suppression_reason"], "overall_budget_status_unknown")
        self.assertFalse(insight["coach_eligible"])
        self.assertFalse(insight["primary_coach_insight"])

    def test_current_budget_uses_full_calendar_month_on_october_31(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.add_budget("Sonstiges", "2026-10", 100)
        self.add_expense(1, "Fruehe Ausgabe", "Sonstiges", 1900, "2026-10-01 09:00:00")
        self.add_expense(2, "Sonstiges", "Sonstiges", 228, "2026-10-15 12:00:00")
        inspector = build_shadow_inspector(
            self.conn,
            1,
            now=datetime(2026, 10, 31, 12, 0, 0),
        )
        insight = next(
            insight for insight in inspector["insight_candidates"]
            if insight["insight_type"] == "budget_attention"
            and insight["primary_pattern_id"].startswith("shadow:category_budget_pressure")
        )
        self.assertEqual(insight["evidence_metrics"]["amount_spent"], 2128.0)
        self.assertEqual(insight["evidence_metrics"]["overall_budget_status"], "under_pressure")
        self.assertTrue(insight["coach_eligible"])

    def test_current_budget_resets_at_november_first(self):
        self.configure_budget_plan(income=3000, fixed_costs=1000)
        self.add_budget("Sonstiges", "2026-11", 100)
        self.add_expense(1, "October", "Sonstiges", 1900, "2026-10-31 23:00:00")
        self.add_expense(2, "November", "Sonstiges", 10, "2026-11-01 09:00:00")
        inspector = build_shadow_inspector(
            self.conn,
            1,
            now=datetime(2026, 11, 1, 12, 0, 0),
        )
        self.assertFalse(any(
            insight["insight_type"] == "budget_attention"
            for insight in inspector["insight_candidates"]
        ))

    def test_historical_repeated_category_evidence_remains_report_separate(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Restaurants", month, 100)
            self.add_financial_snapshot(month)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08")):
            self.add_month(index * 2 + 1, month, (90, 90))
        insights = self.insights()
        historical = next(
            insight for insight in insights
            if insight["insight_type"] == "budget_attention"
            and insight["evidence_metrics"].get("months_over_budget") == 3
        )
        self.assertTrue(historical["report_eligible"])

    def test_repeated_spending_insight_has_weekend_timing_and_report_scope(self):
        for index, day in enumerate((6, 13, 20), 1):
            self.add_expense(index, "Lieferando", "Restaurants", 35, f"2026-09-{day:02d} 19:00:00")
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "repeated_spending_pattern"
            and insight["coach_eligible"]
        )
        self.assertEqual(insight["coach_timing_hint"], "weekend_prevention")
        self.assertEqual(insight["report_section_hint"], "spending_patterns")
        self.assertTrue(insight["report_eligible"])

    def test_historical_worsening_becomes_reportable_trend_insight(self):
        self._add_historical_months(((45, 45), (62.5, 62.5), (85, 85)))
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "spending_trend_worsening"
        )
        self.assertEqual(insight["direction"], "worsening")
        self.assertEqual(insight["report_section_hint"], "behavior_summary")
        self.assertTrue(insight["coach_eligible"])
        self.assertTrue(insight["report_eligible"])

    def test_historical_improvement_becomes_positive_progress_insight(self):
        self._add_historical_months(((90, 90), (70, 70), (45, 45)))
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "spending_trend_improving"
        )
        self.assertEqual(insight["direction"], "improving")
        self.assertEqual(insight["report_section_hint"], "positive_progress")
        self.assertTrue(insight["coach_eligible"])
        self.assertTrue(insight["report_eligible"])

    def test_post_income_insight_exposes_salary_window_hint(self):
        self._salary_cycle_fixture()
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "post_income_pattern"
            and insight["coach_eligible"]
        )
        self.assertEqual(insight["coach_timing_hint"], "post_salary_window")
        self.assertIn("salary_cycles", insight["evidence_metrics"])

    def test_subscription_insight_is_separate_from_other_insight_types(self):
        self.add_contract("c1", "Netflix", 25)
        self.add_contract("c2", "Disney+", 25)
        self.add_contract("c3", "Paramount+", 25)
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "subscription_cluster"
        )
        self.assertEqual(insight["category"], "streaming_video")
        self.assertEqual(insight["report_section_hint"], "recurring_costs")
        self.assertTrue(insight["coach_eligible"])
        self.assertFalse(insight["report_eligible"])

    def test_unverified_activity_creates_suppressed_subscription_insight(self):
        self.conn.execute("DROP TABLE app_contracts")
        self.conn.execute(
            """CREATE TABLE app_contracts (
                user_id INTEGER NOT NULL,
                contract_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                frequency TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, contract_id)
            )"""
        )
        self.conn.executemany(
            """INSERT INTO app_contracts
               (user_id, contract_id, name, category, amount, frequency,
                created_at, updated_at)
               VALUES (1, ?, ?, 'Abos', ?, 'monthly',
                       '2026-09-01 09:00:00', '2026-09-01 09:00:00')""",
            (("c1", "Netflix", 25), ("c2", "Disney+", 25), ("c3", "Paramount+", 25)),
        )
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "subscription_cluster"
        )
        self.assertEqual(insight["suppression_reason"], "uncertain_activity_status")
        self.assertFalse(insight["coach_eligible"])
        self.assertFalse(insight["report_eligible"])

    def test_single_outlier_insight_is_visible_but_not_eligible(self):
        self._add_historical_months(((40,), (42.5,), (310,)))
        insight = next(
            insight for insight in self.insights()
            if insight["insight_type"] == "spending_trend_worsening"
        )
        self.assertEqual(insight["suppression_reason"], "single_outlier")
        self.assertFalse(insight["coach_eligible"])
        self.assertFalse(insight["report_eligible"])

    def test_conflicting_directions_suppress_primary_insights(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month, 100)
        self.add_month(1, "2026-06", (60, 60))
        self.add_month(3, "2026-07", (80, 80))
        self.add_month(5, "2026-08", (100, 100))
        self.add_month(7, "2026-09", (40, 40))
        insights = self.insights()
        self.assertTrue(any(
            insight["suppression_reason"] == "conflicting_evidence"
            for insight in insights
        ))
        self.assertFalse(any(
            insight["primary_coach_insight"] or insight["primary_report_insight"]
            for insight in insights
            if insight["suppression_reason"] == "conflicting_evidence"
        ))

    def test_insight_arbitration_exposes_one_primary_per_channel_and_inspector_ids(self):
        self._add_historical_months(((60, 60), (80, 80), (100, 100)))
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        insights = inspector["insight_candidates"]
        coach_primaries = [i for i in insights if i["primary_coach_insight"]]
        report_primaries = [i for i in insights if i["primary_report_insight"]]
        self.assertLessEqual(len(coach_primaries), 1)
        self.assertLessEqual(len(report_primaries), 1)
        if coach_primaries:
            self.assertEqual(inspector["primary_coach_insight"], coach_primaries[0]["insight_id"])
        if report_primaries:
            self.assertEqual(inspector["primary_report_insight"], report_primaries[0]["insight_id"])
        self.assertFalse(inspector["coach_v3_affected"])

    def test_shared_salary_evidence_has_one_primary_per_channel(self):
        self._salary_cycle_fixture()
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        salary_insights = [
            insight for insight in inspector["insight_candidates"]
            if insight["insight_type"] == "post_income_pattern"
        ]
        self.assertGreaterEqual(len(salary_insights), 2)
        self.assertEqual(
            len([insight for insight in salary_insights if insight["primary_coach_insight"]]),
            1,
        )
        self.assertTrue(any(
            insight["suppression_reason"] == "superseded"
            for insight in salary_insights
        ))
        self.assertTrue(
            set(salary_insights[0]["source_ids"]) & set(salary_insights[1]["source_ids"])
        )

    def test_insight_arbitration_is_insert_order_independent(self):
        self._salary_cycle_fixture()
        patterns = self.patterns()
        normal = build_behavior_insights(patterns)
        reversed_order = build_behavior_insights(list(reversed(patterns)))
        normal_primary = [
            insight["insight_id"] for insight in normal
            if insight["primary_coach_insight"]
        ]
        reversed_primary = [
            insight["insight_id"] for insight in reversed_order
            if insight["primary_coach_insight"]
        ]
        self.assertEqual(normal_primary, reversed_primary)

    def test_recovery_keeps_current_period_and_historical_trace(self):
        for month in ("2026-06", "2026-07", "2026-08", "2026-09"):
            self.add_budget("Restaurants", month)
        self.add_month(1, "2026-06", (80, 80))
        self.add_month(3, "2026-07", (90, 90))
        self.add_month(5, "2026-08", (110, 110))
        self.add_month(7, "2026-09", (40, 40))
        full_close = datetime(2026, 9, 30, 23, 59, 59)
        inspector = build_shadow_inspector(self.conn, 1, now=full_close)
        recovery = next(
            pattern for pattern in inspector["composite_patterns"]
            if pattern.get("composite_type") == "budget_recovery"
        )
        self.assertEqual(recovery["period_start"], "2026-09-01")
        self.assertEqual(recovery["period_end"], "2026-09-30")
        self.assertEqual(recovery["observations"]["current_period_start"], "2026-09-01")
        self.assertEqual(recovery["observations"]["current_period_end"], "2026-09-30")
        self.assertEqual(
            recovery["observations"]["historical_pattern_id"],
            recovery["related_pattern_ids"][0],
        )
        recovery_insight = next(
            insight for insight in inspector["insight_candidates"]
            if insight["primary_pattern_id"] == recovery["pattern_id"]
        )
        self.assertTrue(recovery_insight["primary_coach_insight"])
        self.assertIn(
            recovery["observations"]["historical_pattern_id"],
            recovery_insight["source_pattern_ids"],
        )

    def test_mid_month_budget_creation_is_not_applied_to_earlier_history(self):
        self.conn.execute("ALTER TABLE category_budgets ADD COLUMN created_at TEXT")
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.conn.execute(
                "INSERT INTO category_budgets VALUES (1,?,?,?,?)",
                ("Restaurants", 100, month, f"{month}-20 09:00:00"),
            )
            self.add_month(index * 10, month, (80, 80))
        self.assertFalse(self.of_type("category_repeated_over_budget"))

    def test_budget_present_from_month_start_can_be_used_historically(self):
        self.conn.execute("ALTER TABLE category_budgets ADD COLUMN created_at TEXT")
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.conn.execute(
                "INSERT INTO category_budgets VALUES (1,?,?,?,?)",
                ("Restaurants", 100, month, f"{month}-01 00:00:00"),
            )
            self.add_month(index * 10, month, (80, 80))
        self.assertTrue(self.of_type("category_repeated_over_budget"))

    def test_hard_single_outlier_remains_suppressed_after_historical_fusion(self):
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_budget("Restaurants", month, 100)
            self.add_financial_snapshot(month, income=500, fixed_costs=300)
            self.add_month(index * 10, month, (300,))
        inspector = build_shadow_inspector(self.conn, 1, now=self.NOW)
        composite = next(
            pattern for pattern in inspector["composite_patterns"]
            if pattern.get("composite_type") == "repeated_discretionary_budget_pressure"
        )
        self.assertFalse(composite["eligible_for_coach"])
        self.assertEqual(
            composite["observations"]["hard_quality_exclusion"],
            "single_outlier",
        )
        insight = next(
            insight for insight in inspector["insight_candidates"]
            if insight["primary_pattern_id"] == composite["pattern_id"]
        )
        self.assertEqual(insight["suppression_reason"], "single_outlier")
        self.assertFalse(insight["coach_eligible"])

    def test_historical_composite_keeps_category_isolation(self):
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_budget("Restaurants", month, 100)
            self.add_financial_snapshot(month, income=500, fixed_costs=300)
            self.add_month(index * 10, month, (150, 150))
            self.add_expense(
                100 + int(month[-2:]),
                "Lidl",
                "Zubehoer",
                1,
                f"{month}-10 12:00:00",
            )
        composites = [
            pattern for pattern in self.patterns()
            if pattern.get("composite_type") == "repeated_discretionary_budget_pressure"
            and pattern.get("category") == "Restaurants"
        ]
        self.assertEqual(len(composites), 1)
        self.assertTrue(composites[0]["eligible_for_coach"])
        self.assertEqual(
            composites[0]["observations"]["category_kind"],
            "discretionary",
        )

    def test_weekday_outlier_does_not_become_behavioral_coaching(self):
        for expense_id, day, amount in ((1, 6, 1), (2, 13, 1), (3, 20, 500)):
            self.add_expense(
                expense_id, "Restaurant", "Restaurants", amount,
                f"2026-09-{day:02d} 19:00:00",
            )
        pattern = self.of_type("merchant_weekday_pattern")[0]
        self.assertTrue(pattern["observations"]["single_expense_dominated"])
        self.assertFalse(pattern["eligible_for_coach"])
        self.assertEqual(pattern["financial_relevance"], "low")

    def test_essential_merchant_classification_survives_historical_category_aggregation(self):
        for month in ("2026-06", "2026-07", "2026-08"):
            self.add_budget("Shopping", month, 100)
            self.add_expense(
                int(month[-2:]), "Lidl", "Shopping", 150,
                f"{month}-05 12:00:00",
            )
        self.assertFalse(any(
            pattern.get("category") == "Shopping"
            and pattern.get("eligible_for_coach")
            for pattern in self.patterns()
            if pattern["pattern_type"] in {
                "category_spending_worsening",
                "category_repeated_over_budget",
                "repeated_discretionary_budget_pressure",
            }
        ))

    def test_salary_insight_requires_reliable_expense_event_dates(self):
        self.configure_budget_plan()
        for index, month in enumerate(("2026-06", "2026-07", "2026-08"), 1):
            self.add_income(index, created_at=f"{month}-01 09:00:00")
            self.add_expense(
                100 + index, "Amazon", "Shopping", 200,
                f"{month}-02 12:00:00", transaction_at=None,
            )
        for index, day in enumerate((10, 11, 12), 1):
            self.add_expense(
                200 + index, "Restaurant", "Restaurants", 10,
                f"2026-05-{day:02d} 12:00:00",
            )
        self.assertFalse(any(
            pattern["pattern_type"].startswith("post_income_")
            and pattern["eligible_for_coach"]
            for pattern in self.patterns()
        ))

    def test_old_unreliable_salary_row_does_not_block_reliable_cycles(self):
        self._salary_cycle_fixture()
        self.conn.execute(
            "INSERT INTO app_cash_movements "
            "(id,user_id,kind,amount,expense_id,created_at,label,occurred_at) "
            "VALUES (99,1,'income',3000,NULL,'2026-05-01 09:00:00','Gehalt',NULL)"
        )
        self.assertTrue(self.of_type("post_income_discretionary_spike"))

    def _add_historical_months(self, values):
        for index, (month, amounts) in enumerate(
            zip(("2026-06", "2026-07", "2026-08"), values)
        ):
            self.add_month(index * 10 + 1, month, amounts)


if __name__ == "__main__":
    unittest.main()
