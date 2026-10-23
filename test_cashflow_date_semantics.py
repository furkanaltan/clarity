from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import report_engine
import rove_app_state
from rove_app_state import _build_tx, _monthly_budget_truth
from rove_dates import (
    business_month_key,
    business_today,
    effective_business_date,
    parse_business_date,
)
from rove_expense_domain import classified_expenses
from rove_score import tracking_days_90


class CashflowDateSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "cashflow.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute(
            """CREATE TABLE expenses (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount REAL,
                category TEXT, merchant TEXT, description TEXT,
                created_at TEXT, account_id INTEGER
            )"""
        )

    def add_expense(self, *, created_at: str, description: str = "", booking_date: str | None = None):
        columns = ["user_id", "amount", "category", "merchant", "description", "created_at", "account_id"]
        values: list[object] = [1, 10.0, "SHOPPING", "Shop", description, created_at, None]
        if booking_date is not None:
            if "booking_date" not in {row[1] for row in self.conn.execute("PRAGMA table_info(expenses)")}:
                self.conn.execute("ALTER TABLE expenses ADD COLUMN booking_date TEXT")
            columns.append("booking_date")
            values.append(booking_date)
        placeholders = ",".join("?" for _ in values)
        cursor = self.conn.execute(
            f"INSERT INTO expenses ({','.join(columns)}) VALUES ({placeholders})", values
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def test_berlin_month_boundary_and_offsets(self):
        self.assertEqual(
            parse_business_date("2026-08-31T23:30:00+02:00"), date(2026, 8, 31)
        )
        self.assertEqual(
            parse_business_date("2026-08-31T22:30:00Z"), date(2026, 9, 1)
        )
        self.assertEqual(
            parse_business_date(datetime(2026, 8, 31, 21, 30, tzinfo=timezone.utc)),
            date(2026, 8, 31),
        )
        self.assertEqual(
            parse_business_date(datetime(2026, 8, 31, 22, 30, tzinfo=timezone.utc)),
            date(2026, 9, 1),
        )
        self.assertEqual(
            business_month_key(datetime(2026, 8, 31, 21, 30, tzinfo=timezone.utc)),
            "2026-08",
        )
        self.assertEqual(
            business_month_key(datetime(2026, 8, 31, 22, 30, tzinfo=timezone.utc)),
            "2026-09",
        )
        self.assertEqual(
            parse_business_date("2026-09-01T00:30:00+02:00"), date(2026, 9, 1)
        )
        self.assertEqual(
            business_today(datetime(2026, 8, 31, 22, 30, tzinfo=timezone.utc)),
            date(2026, 9, 1),
        )

    def test_date_only_value_is_not_timezone_shifted(self):
        self.assertEqual(parse_business_date("2026-08-31"), date(2026, 8, 31))
        screenshot_row = {
            "description": "Via Rov.E Screenshot · abc",
            "created_at": "2026-08-31 23:30:00",
        }
        self.assertEqual(effective_business_date(screenshot_row), date(2026, 8, 31))

    def test_booking_date_wins_over_created_import_timestamp(self):
        self.add_expense(
            created_at="2026-08-31T22:30:00Z", booking_date="2026-08-31"
        )
        august = classified_expenses(self.conn, 1, "2026-08")
        september = classified_expenses(self.conn, 1, "2026-09")
        self.assertEqual(len(august), 1)
        self.assertEqual(august[0]["effective_date"], "2026-08-31")
        self.assertEqual(september, [])

    def test_created_at_is_fallback_and_berlin_date_drives_expense_month(self):
        expense_id = self.add_expense(created_at="2026-08-31 22:30:00")
        self.assertEqual(classified_expenses(self.conn, 1, "2026-08"), [])
        september = classified_expenses(self.conn, 1, "2026-09")
        self.assertEqual([row["id"] for row in september], [expense_id])
        self.assertEqual(september[0]["effective_date"], "2026-09-01")

    def test_screenshot_calendar_date_is_stable_in_classified_expense_path(self):
        expense_id = self.add_expense(
            created_at="2026-08-31 23:30:00",
            description="Via Rov.E Screenshot · abc",
        )
        self.assertEqual(
            [row["id"] for row in classified_expenses(self.conn, 1, "2026-08")],
            [expense_id],
        )
        self.assertEqual(classified_expenses(self.conn, 1, "2026-09"), [])

    def test_budget_and_score_shared_expense_source_use_berlin_month(self):
        self.add_expense(created_at="2026-08-31T22:30:00Z")
        budget = _monthly_budget_truth(
            self.conn, 1, income=100.0, fixed_costs=0.0,
            savings=0.0, month_key="2026-09",
        )
        self.assertEqual(budget["variable_expenses"], 10.0)
        self.assertEqual(budget["free_month_remaining"], 90.0)

    def test_score_tracking_days_use_same_effective_berlin_date(self):
        self.add_expense(created_at="2026-08-31T22:30:00Z")
        self.assertEqual(tracking_days_90(self.conn, 1, date(2026, 9, 1)), 1)

    def test_cashflow_groups_expense_by_effective_berlin_date(self):
        self.add_expense(created_at="2026-08-31T22:30:00Z")
        self.conn.execute(
            """CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, amount REAL,
                expense_id INTEGER, label TEXT, source_account_id INTEGER,
                target_account_id INTEGER, created_at TEXT, classification TEXT
            )"""
        )
        self.conn.commit()
        with patch.object(rove_app_state, "list_financial_accounts", return_value=[]):
            tx = _build_tx(self.conn, 1, "2026-09")
        self.assertEqual(tx[0]["d"], "01.09.")
        self.assertEqual(tx[0]["items"][0]["date"], "2026-09-01")

    def test_cash_movements_prefer_existing_occurred_at_over_created_at(self):
        self.conn.execute(
            """CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, amount REAL,
                created_at TEXT, occurred_at TEXT
            )"""
        )
        self.conn.execute(
            """INSERT INTO app_cash_movements
               (id,user_id,kind,amount,created_at,occurred_at)
               VALUES (1,1,'withdrawal',20,'2026-08-01 12:00:00','2026-08-31T22:30:00Z')"""
        )
        self.conn.commit()
        august = rove_app_state._cash_movements_for_month(self.conn, 1, "2026-08")
        september = rove_app_state._cash_movements_for_month(self.conn, 1, "2026-09")
        self.assertEqual(august, [])
        self.assertEqual(len(september), 1)
        self.assertEqual(effective_business_date(september[0]), date(2026, 9, 1))

    def test_fixed_cost_classification_follows_linked_expense_booking_month(self):
        expense_id = self.add_expense(
            created_at="2026-08-31 12:00:00",
            description="Via Rov.E Screenshot · fixed",
        )
        self.conn.execute(
            """CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, amount REAL,
                expense_id INTEGER, created_at TEXT, classification TEXT
            )"""
        )
        self.conn.execute(
            """INSERT INTO app_cash_movements
               (id,user_id,kind,amount,expense_id,created_at,classification)
               VALUES (1,1,'card',10,?, '2026-09-01 00:30:00','fixed_cost')""",
            (expense_id,),
        )
        self.conn.commit()
        august = classified_expenses(self.conn, 1, "2026-08")
        self.assertEqual(len(august), 1)
        self.assertEqual(august[0]["classification"], "fixed_cost")
        self.assertEqual(sum(row["amount"] for row in august if row["classification"] == "fixed_cost"), 10)

    def test_report_expense_reader_uses_same_effective_month(self):
        self.add_expense(created_at="2026-08-31T22:30:00Z")
        with patch.object(report_engine, "DB_NAME", str(self.db_path)):
            september = report_engine.get_report_expense_rows(1, "2026-09")
            august = report_engine.get_report_expense_rows(1, "2026-08")
        self.assertEqual([row["effective_date"] for row in september], ["2026-09-01"])
        self.assertEqual(august, [])


if __name__ == "__main__":
    unittest.main()
