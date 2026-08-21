import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import report_engine


class ReportSnapshotV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "clarity.db")
        self.db_patch = patch.object(report_engine, "DB_NAME", self.db)
        self.db_patch.start()
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE users (user_id INTEGER PRIMARY KEY, current_cash REAL);
                CREATE TABLE expenses (
                    id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL,
                    category TEXT, merchant TEXT, description TEXT, created_at TEXT
                );
                CREATE TABLE app_cash_movements (
                    id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT,
                    amount REAL, expense_id INTEGER
                );
                CREATE TABLE app_user_features (user_id INTEGER, feature_key TEXT, enabled INTEGER);
                CREATE TABLE app_financial_accounts (
                    id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT,
                    account_type TEXT, balance REAL, currency TEXT, status TEXT
                );
                CREATE TABLE app_goals (
                    user_id INTEGER, goal_id TEXT, name TEXT,
                    target_amount REAL, current_amount REAL, is_primary INTEGER
                );
                """
            )
            conn.execute("INSERT INTO users VALUES (1, 1000.0)")
            conn.executemany(
                "INSERT INTO expenses VALUES (?, 1, ?, ?, ?, '', '2026-08-10 12:00:00')",
                [(1, 20.0, "Restaurant", "Lidl"), (2, 300.0, "Transfer", "Umbuchung")],
            )
            conn.execute(
                "INSERT INTO expenses VALUES (3, 1, 55.0, 'Restaurant', 'Spaet', '', '2026-08-25 12:00:00')"
            )
            conn.execute("INSERT INTO app_cash_movements VALUES (1, 1, 'transfer', 300.0, 2)")
            conn.execute("INSERT INTO app_user_features VALUES (1, 'multi_cash_accounts_v1', 1)")
            conn.execute("INSERT INTO app_financial_accounts VALUES (1, 1, 'Giro', 'checking', 1000.0, 'EUR', 'active')")

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_transfers_are_not_consumption(self):
        rows = report_engine.get_report_expense_rows(1, "2026-08")
        self.assertEqual([row["classification"] for row in rows], ["consumption", "transfer", "consumption"])
        total, tracked_days, categories = report_engine.get_expense_stats(1, "2026-08")
        self.assertEqual(total, 75.0)
        self.assertEqual(tracked_days, 2)
        self.assertEqual(categories[0]["category"], "Restaurant")

    def test_open_month_window_uses_same_day_in_previous_month(self):
        window = report_engine.report_period_window("2026-08", date(2026, 8, 21))
        self.assertEqual(window["end"], "2026-08-21")
        self.assertEqual(window["comparison_mode"], "partial")
        self.assertEqual(report_engine.previous_period_end("2026-08", window["cutoff_day"]), "2026-07-21")

    def test_expenses_after_open_month_cutoff_are_excluded(self):
        rows = report_engine.get_report_expense_rows(1, "2026-08", "2026-08-21")
        self.assertEqual([row["id"] for row in rows], [1, 2])

    def test_multi_account_cash_drift_blocks_truth(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE app_financial_accounts SET balance = 999.0")
        with self.assertRaisesRegex(ValueError, "report_cash_invariant_failed"):
            report_engine._report_cash_truth(1)

    def test_goal_truth_preserves_text_goal_id(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO app_goals VALUES (1, 'g_UullDEEJIr', 'Dubai', 5000.0, 900.0, 1)"
            )

        truth = report_engine._report_goal_truth(1, "", 0.0, 0.0)

        self.assertEqual(truth["primary"]["id"], "g_UullDEEJIr")
        self.assertEqual(truth["goals"][0]["current_amount"], 900.0)

    def test_final_snapshot_is_reused(self):
        fake_data = {
            "meta": {"user_id": 1, "report_month": "2026-08", "tracked_days": 14},
            "profile": {},
            "pages": {"score": {}, "goal": {}, "wealth_journey": {"monthly_execution": {}}, "budget": {}},
        }
        fake_truth = {
            "cash": {"invariant_ok": True},
            "investments": {"market_movement": {"available": False}},
        }
        with patch.object(report_engine, "build_report_data", return_value=fake_data), \
             patch.object(report_engine, "_build_report_truth_layer", return_value=fake_truth), \
             patch.object(report_engine, "validate_report_snapshot", return_value={"ok": True}), \
             patch.object(report_engine, "MIN_TRACKING_DAYS", 0):
            first = report_engine.get_or_create_report_snapshot(1, "2026-08")
            changed = dict(fake_data)
            changed["meta"] = dict(fake_data["meta"], tracked_days=99)
            report_engine.build_report_data.return_value = changed
            second = report_engine.get_or_create_report_snapshot(1, "2026-08")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["data_hash"], second["data_hash"])
        self.assertEqual(json.dumps(first["data"], sort_keys=True), json.dumps(second["data"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
