from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime

from rove_app_state import (
    build_mentor_candidate,
    build_mentor_events,
    ensure_app_mentor_event_state_table,
    mark_mentor_event_seen,
)


class CoachV3EventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 16, 12, 0, 0)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO users(user_id) VALUES (1)")

    def tearDown(self) -> None:
        self.conn.close()

    @staticmethod
    def score(**overrides):
        result = {
            "value": 70,
            "debt": 30,
            "liquidity": 15,
            "liquidity_months": 3,
            "savings": 17,
            "savings_ratio": 0.20,
            "tracking_days_90": 20,
            "consumer_debt_total": 0,
        }
        result.update(overrides)
        return result

    def candidate(self, **overrides):
        values = {
            "score": self.score(),
            "budget_truth": {"free_month_remaining": 100, "category_remaining": 100},
            "monthly_actions": [],
            "goals": [{"t": "Reserve"}],
            "contracts": [],
            "reports": [],
            "income": 3000,
            "fixed_costs": 1000,
        }
        values.update(overrides)
        return build_mentor_candidate(**values)

    def test_new_budget_overrun_is_event_and_unchanged_overrun_is_not_repeated(self):
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": -20, "category_remaining": 100},
            now=self.now,
        )
        budget = next(event for event in events if event["event_type"] == "budget_overrun")
        self.assertTrue(budget["is_new"])
        self.assertEqual(budget["shortfall"], 20.0)
        self.assertEqual(self.candidate(events=events)["type"], "budget_overrun")

        mark_mentor_event_seen(self.conn, 1, budget["event_id"])
        repeated = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": -20, "category_remaining": 100},
            now=self.now,
        )
        self.assertFalse(next(event for event in repeated if event["event_type"] == "budget_overrun")["is_new"])

    def test_worsened_budget_overrun_reopens_same_stable_event(self):
        first = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": -20, "category_remaining": 100},
            now=self.now,
        )[0]
        mark_mentor_event_seen(self.conn, 1, first["event_id"])
        worsened = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": -21, "category_remaining": 100},
            now=self.now,
        )[0]
        self.assertEqual(worsened["event_id"], first["event_id"])
        self.assertTrue(worsened["is_new"])

    def test_new_consumer_debt_has_high_priority(self):
        self.conn.execute(
            """CREATE TABLE app_consumer_debt_events (
                id INTEGER PRIMARY KEY, user_id INTEGER, debt_id INTEGER,
                outstanding_balance REAL, active INTEGER, event_type TEXT, effective_at TEXT
            )"""
        )
        self.conn.execute(
            """INSERT INTO app_consumer_debt_events
               VALUES (1,1,9,15000,1,'created','2026-09-16 09:00:00')"""
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            now=self.now,
        )
        debt = next(event for event in events if event["event_type"] == "consumer_debt_new")
        self.assertEqual(debt["priority"], 105)
        result = self.candidate(
            score=self.score(consumer_debt_total=15000, debt=10), events=events
        )
        self.assertEqual(result["type"], "consumer_debt_new")
        self.assertIn("Hypothek", result["message"])

    def test_salary_event_comes_from_income_only(self):
        self.conn.execute(
            """CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, amount REAL,
                label TEXT, created_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO app_cash_movements VALUES (1,1,'income',3000,'Gehalt','2026-09-15 08:00:00')"
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            income=3000,
            now=self.now,
        )
        salary = next(event for event in events if event["event_type"] == "salary_received")
        self.assertEqual(salary["amount"], 3000.0)
        self.assertEqual(self.candidate(events=events, debt_status="none")["type"], "salary_received")

    def test_internal_transfer_creates_no_coach_event(self):
        self.conn.execute(
            """CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, amount REAL,
                label TEXT, created_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO app_cash_movements VALUES (1,1,'transfer',500,'Tagesgeld','2026-09-16 08:00:00')"
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            now=self.now,
        )
        self.assertFalse(any("transfer" in event["event_type"] for event in events))
        self.assertNotEqual(self.candidate(events=events)["type"], "consumer_debt_new")

    def test_new_contract_is_detected_once(self):
        self.conn.execute(
            """CREATE TABLE app_contracts (
                contract_id TEXT PRIMARY KEY, user_id INTEGER, name TEXT, category TEXT,
                amount REAL, created_at TEXT, updated_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO app_contracts VALUES ('c1',1,'Fitnessstudio','Abos',30,'2026-09-16 10:00:00','2026-09-16 10:00:00')"
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            now=self.now,
        )
        contract = next(event for event in events if event["event_type"] == "contract_changed")
        self.assertTrue(contract["is_new"])
        mark_mentor_event_seen(self.conn, 1, contract["event_id"])
        repeated = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            now=self.now,
        )
        self.assertFalse(next(event for event in repeated if event["event_type"] == "contract_changed")["is_new"])

    def test_existing_contract_without_change_has_no_candidate(self):
        result = self.candidate(
            contracts=[{"cat": "Verträge", "items": [{"n": "Fitnessstudio", "a": 30}]}],
            debt_status="none",
        )
        self.assertIsNone(result)

    def test_contract_candidate_requires_a_concrete_change_event(self):
        result = self.candidate(events=[{
            "event_id": "coach:contract_changed:test",
            "event_type": "contract_changed",
            "priority": 47,
            "is_new": True,
            "name": "Fitnessstudio",
        }], debt_status="none")
        self.assertEqual(result["type"], "contract_changed")
        self.assertIn("Fitnessstudio", result["message"])

    def test_savings_candidate_contains_measured_evidence(self):
        result = self.candidate(score=self.score(savings_ratio=0.05, savings=5))
        self.assertEqual(result["type"], "savings")
        self.assertIn("5,0 %", result["message"])

    def test_missing_tracking_data_does_not_create_tracking_recommendation(self):
        result = self.candidate(score={
            "value": 70,
            "debt": 30,
            "liquidity": 15,
            "liquidity_months": 3,
            "savings": 17,
            "savings_ratio": 0.20,
            "consumer_debt_total": 0,
        }, debt_status="none")
        self.assertIsNone(result)

    def test_missing_liquidity_data_does_not_create_liquidity_recommendation(self):
        result = self.candidate(score={
            "value": 70,
            "debt": 30,
            "savings": 17,
            "savings_ratio": 0.20,
            "consumer_debt_total": 0,
        }, debt_status="none")
        self.assertIsNone(result)

    def test_new_report_is_detected(self):
        self.conn.execute(
            """CREATE TABLE report_jobs (
                id INTEGER PRIMARY KEY, user_id INTEGER, report_month TEXT,
                status TEXT, created_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO report_jobs VALUES (1,1,'2026-08','sent','2026-09-15 07:00:00')"
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            now=self.now,
        )
        report = next(event for event in events if event["event_type"] == "report_ready")
        self.assertTrue(report["is_new"])
        self.assertEqual(self.candidate(events=events, debt_status="none")["type"], "report_ready")

    def test_opened_report_is_not_reintroduced_as_event(self):
        self.conn.execute(
            """CREATE TABLE report_jobs (
                id INTEGER PRIMARY KEY, user_id INTEGER, report_month TEXT,
                status TEXT, created_at TEXT, opened_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO report_jobs VALUES (1,1,'2026-08','sent','2026-09-15 07:00:00','2026-09-15 08:00:00')"
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            now=self.now,
        )
        self.assertFalse(any(event["event_type"] == "report_ready" for event in events))

    def test_critical_budget_event_displaces_positive_salary_event(self):
        self.conn.execute(
            """CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, amount REAL,
                label TEXT, created_at TEXT
            )"""
        )
        self.conn.execute(
            "INSERT INTO app_cash_movements VALUES (1,1,'income',3000,'Gehalt','2026-09-15 08:00:00')"
        )
        events = build_mentor_events(
            self.conn, 1,
            budget_truth={"free_month_remaining": -20, "category_remaining": 100},
            income=3000,
            now=self.now,
        )
        self.assertEqual(self.candidate(events=events)["type"], "budget_overrun")

    def test_event_metadata_is_additive_and_user_cascade_safe(self):
        ensure_app_mentor_event_state_table(self.conn)
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(app_mentor_event_state)")
        }
        self.assertTrue({"event_id", "occurred_at", "seen_at", "resolved_at", "expires_at"} <= columns)
        self.conn.execute(
            "INSERT INTO app_mentor_event_state(user_id,event_id,event_type,source_id,period_key,occurred_at) VALUES (1,'e','x','s','p','2026-09-16')"
        )
        self.conn.execute("DELETE FROM users WHERE user_id=1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_mentor_event_state").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
