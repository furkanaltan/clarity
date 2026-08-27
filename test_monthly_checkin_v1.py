from __future__ import annotations

import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

import rove_app_state as state
from rove_score import savings_confirmed


class MonthlyCheckinTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, income REAL DEFAULT 2000,
                other_income REAL DEFAULT 0, payday INTEGER DEFAULT 15
            );
            CREATE TABLE investment_events (
                id INTEGER PRIMARY KEY, user_id INTEGER, holding_id INTEGER,
                amount REAL, direction TEXT, asset_type TEXT, source TEXT,
                event_type TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE portfolio_holdings (
                id INTEGER PRIMARY KEY, user_id INTEGER, instrument_label TEXT,
                instrument_type TEXT
            );
            INSERT INTO users (user_id) VALUES (1);
            INSERT INTO portfolio_holdings VALUES (10, 1, 'ETF Eins', 'etf');
            INSERT INTO portfolio_holdings VALUES (20, 1, 'ETF Zwei', 'etf');
        """)
        state.ensure_app_etf_position_plans_table(self.conn)
        state.ensure_app_month_close_table(self.conn)
        self.conn.execute(
            "INSERT INTO app_month_close_enrollment (user_id, starts_month) VALUES (1, '2026-02')"
        )
        self.conn.execute("""INSERT INTO app_etf_position_plans
            (user_id, holding_id, monthly_amount, execution_day, source_account, mode, active, start_month)
            VALUES (1, 10, 100, 15, 'giro', 'confirm', 1, '2026-02')""")
        self.conn.execute("""INSERT INTO app_etf_position_plans
            (user_id, holding_id, monthly_amount, execution_day, source_account, mode, active, start_month)
            VALUES (1, 20, 100, 30, 'giro', 'confirm', 1, '2026-02')""")

    def tearDown(self):
        self.conn.close()

    def actions(self, today: date):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return today
        user = dict(self.conn.execute("SELECT * FROM users WHERE user_id=1").fetchone())
        with patch.object(state, "date", FixedDate):
            return state.get_monthly_checkin_actions(self.conn, 1, user)

    def test_future_actions_do_not_count_as_due(self):
        actions = self.actions(date(2026, 3, 1))
        self.assertEqual([action["kind"] for action in actions], ["month_close"])

    def test_month_close_never_offers_the_running_august_month(self):
        for today in (date(2026, 8, 27), date(2026, 8, 30), date(2026, 8, 31)):
            with self.subTest(today=today):
                close_months = [action["month"] for action in self.actions(today) if action["kind"] == "month_close"]
                self.assertNotIn("2026-08", close_months)
                self.assertEqual(len(close_months), len(set(close_months)))

    def test_first_feature_read_does_not_create_historical_close_debt(self):
        self.conn.execute("DELETE FROM app_month_close_enrollment WHERE user_id = 1")
        august_actions = self.actions(date(2026, 8, 27))
        self.assertNotIn("month_close", [action["kind"] for action in august_actions])

        september_months = [
            action["month"]
            for action in self.actions(date(2026, 9, 1))
            if action["kind"] == "month_close"
        ]
        self.assertEqual(september_months, ["2026-08"])

    def test_first_day_of_following_month_offers_only_august(self):
        close_months = [action["month"] for action in self.actions(date(2026, 9, 1)) if action["kind"] == "month_close"]
        self.assertEqual(close_months, ["2026-08"])

    def test_oldest_recent_open_month_is_the_only_catch_up_prompt(self):
        self.conn.execute("""INSERT INTO investment_events
            (user_id, amount, direction, asset_type, source, created_at)
            VALUES (1, 100, 'in', 'etf', 'app_etf_plan', '2026-07-15')""")
        close_months = [action["month"] for action in self.actions(date(2026, 9, 1)) if action["kind"] == "month_close"]
        self.assertEqual(close_months, ["2026-07"])

    def test_february_waits_until_march_in_both_calendar_variants(self):
        self.conn.execute(
            "UPDATE app_month_close_enrollment SET starts_month = '2024-01' WHERE user_id = 1"
        )
        for today in (date(2025, 2, 28), date(2024, 2, 29)):
            with self.subTest(today=today):
                self.assertNotIn(
                    "2025-02" if today.year == 2025 else "2024-02",
                    [action["month"] for action in self.actions(today) if action["kind"] == "month_close"],
                )
        self.assertEqual(
            [action["month"] for action in self.actions(date(2025, 3, 1)) if action["kind"] == "month_close"],
            ["2025-02"],
        )

    def test_income_and_first_etf_are_due_on_their_days(self):
        actions = self.actions(date(2026, 3, 15))
        self.assertEqual({action["id"] for action in actions}, {"income", "etf_plan:10", "month_close:2026-02"})

    def test_second_etf_remains_future_until_its_own_day(self):
        actions = self.actions(date(2026, 3, 15))
        self.assertNotIn("etf_plan:20", {action["id"] for action in actions})
        self.assertIn("etf_plan:20", {action["id"] for action in self.actions(date(2026, 3, 30))})

    def test_day_31_uses_february_month_end(self):
        self.conn.execute("UPDATE users SET payday=31 WHERE user_id=1")
        actions = self.actions(date(2026, 2, 28))
        self.assertIn("income", {action["id"] for action in actions})

    def test_executed_etf_and_closed_month_disappear(self):
        self.conn.execute("""INSERT INTO investment_events
            (user_id, holding_id, amount, direction, asset_type, source, event_type, created_at)
            VALUES (1, 10, 100, 'in', 'etf', 'app_etf_plan', 'recurring_plan', '2026-03-15')""")
        state.ensure_app_month_close_table(self.conn)
        self.conn.execute("INSERT INTO app_month_closures (user_id, month_key, actual_savings) VALUES (1, '2026-02', 50)")
        actions = self.actions(date(2026, 3, 15))
        self.assertNotIn("etf_plan:10", {action["id"] for action in actions})
        self.assertNotIn("month_close:2026-02", {action["id"] for action in actions})

    def test_closed_previous_month_does_not_fall_through_to_running_month(self):
        state.ensure_app_month_close_table(self.conn)
        self.conn.execute("INSERT INTO app_month_closures (user_id, month_key, actual_savings) VALUES (1, '2026-07', 50)")
        close_months = [action["month"] for action in self.actions(date(2026, 8, 27)) if action["kind"] == "month_close"]
        self.assertEqual(close_months, [])

    def test_only_month_close_confirms_savings_for_score(self):
        self.conn.execute("""INSERT INTO investment_events
            (user_id, amount, direction, asset_type, source, created_at)
            VALUES (1, 100, 'in', 'etf', 'app_etf_plan', '2026-02-15')""")
        self.assertFalse(savings_confirmed(self.conn, 1, "2026-02"))
        state.ensure_app_month_close_table(self.conn)
        self.conn.execute("INSERT INTO app_month_closures (user_id, month_key, actual_savings) VALUES (1, '2026-02', 100)")
        self.assertTrue(savings_confirmed(self.conn, 1, "2026-02"))

    def test_month_close_is_idempotent(self):
        state.ensure_app_month_close_table(self.conn)
        self.conn.execute("INSERT OR IGNORE INTO app_month_closures (user_id, month_key, actual_savings) VALUES (1, '2026-02', 100)")
        self.conn.execute("INSERT OR IGNORE INTO app_month_closures (user_id, month_key, actual_savings) VALUES (1, '2026-02', 200)")
        row = self.conn.execute("SELECT actual_savings FROM app_month_closures WHERE user_id=1 AND month_key='2026-02'").fetchone()
        self.assertEqual(row["actual_savings"], 100)


if __name__ == "__main__":
    unittest.main()
