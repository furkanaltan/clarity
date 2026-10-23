from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import get_app_goals
from test_financial_accounts_sprint2 import create_db


class GoalUnassignApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_db(self.db_path)
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "user_from_token", lambda _conn, token: 1 if token == "test-token" else None),
            patch.object(api, "build_live_app_data", self.build_live_data),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)
        with closing(self.connect()) as conn:
            api.ensure_app_goals_table(conn)
            conn.execute("ALTER TABLE users ADD COLUMN goal_description TEXT DEFAULT ''")
            conn.execute("ALTER TABLE users ADD COLUMN goal_amount REAL DEFAULT 0")
            api.ensure_app_primary_goal_progress_table(conn)
            conn.execute(
                "INSERT INTO app_goals (user_id, goal_id, name, target_amount, current_amount) "
                "VALUES (1, 'goal-1', 'Dubai', 1000, 500)"
            )
            conn.commit()
        self.initial_financial_truth = self.financial_truth()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def build_live_data(self, conn, user_id):
        user = conn.execute(
            "SELECT goal_description, goal_amount FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        goals = []
        if user and str(user["goal_description"] or "").strip():
            progress = conn.execute(
                "SELECT current_amount, goal_monthly_rate FROM app_primary_goal_progress WHERE user_id=?",
                (user_id,),
            ).fetchone()
            target = float(user["goal_amount"] or 0)
            goals.append({
                "id": "primary",
                "t": str(user["goal_description"]),
                "tar": round(target, 2) or 1,
                "cur": round(max(0.0, float(progress["current_amount"] or 0)), 2) if progress else 0,
                "rate": round(float(progress["goal_monthly_rate"]), 2)
                if progress and progress["goal_monthly_rate"] else None,
                "source": "bot",
            })
        return {"goals": goals + get_app_goals(conn, user_id)}

    def request(self, payload):
        with api.app.test_client() as client:
            return client.post(
                "/v1/goals",
                json=payload,
                headers={
                    "Authorization": "Bearer test-token",
                    "Origin": "https://getrove.de",
                },
            )

    def current_amount(self, goal_id="goal-1"):
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT current_amount FROM app_goals WHERE user_id=1 AND goal_id=?",
                (goal_id,),
            ).fetchone()
            return None if row is None else float(row[0])

    def financial_truth(self):
        with closing(self.connect()) as conn:
            return (
                float(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0]),
                int(conn.execute("SELECT COUNT(*) FROM expenses WHERE user_id=1").fetchone()[0]),
                int(conn.execute("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1").fetchone()[0]),
                int(conn.execute("SELECT COUNT(*) FROM investment_events WHERE user_id=1").fetchone()[0]),
            )

    def assert_financial_truth_unchanged(self):
        self.assertEqual(self.financial_truth(), self.initial_financial_truth)

    def test_unassign_reduces_and_returns_confirmed_server_state(self):
        response = self.request({"action": "unassign", "goal_id": "goal-1", "amount": 100})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.current_amount(), 400)
        self.assertEqual(response.get_json()["goals"][0]["cur"], 400)
        self.assert_financial_truth_unchanged()

    def test_unassign_exact_current_amount_reaches_zero(self):
        response = self.request({"action": "unassign", "goal_id": "goal-1", "amount": 500})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.current_amount(), 0)
        self.assertEqual(response.get_json()["goals"][0]["cur"], 0)
        self.assert_financial_truth_unchanged()

    def test_primary_goal_uses_the_same_unassign_contract(self):
        with closing(self.connect()) as conn:
            conn.execute(
                "UPDATE users SET goal_description='Primary', goal_amount=1000 WHERE user_id=1"
            )
            conn.execute(
                "INSERT INTO app_primary_goal_progress (user_id, current_amount) VALUES (1, 500)"
            )
            conn.commit()

        response = self.request({"action": "unassign", "goal_id": "primary", "amount": 100})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["goals"][0]["cur"], 400)
        with closing(self.connect()) as conn:
            self.assertEqual(float(conn.execute(
                "SELECT current_amount FROM app_primary_goal_progress WHERE user_id=1"
            ).fetchone()[0]), 400)
        self.assert_financial_truth_unchanged()

    def test_unassign_over_current_amount_is_rejected_without_mutation(self):
        response = self.request({"action": "unassign", "goal_id": "goal-1", "amount": 600})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {
            "ok": False,
            "error": "goal_unassign_exceeds_current",
            "current_amount": 500,
        })
        self.assertEqual(self.current_amount(), 500)
        self.assert_financial_truth_unchanged()

    def test_unassign_rejects_zero_and_negative_amounts(self):
        for amount in (0, -1):
            with self.subTest(amount=amount):
                response = self.request({"action": "unassign", "goal_id": "goal-1", "amount": amount})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"], "valid_goal_amount_required")
                self.assertEqual(self.current_amount(), 500)
        self.assert_financial_truth_unchanged()

    def test_unknown_goal_keeps_existing_not_found_behavior(self):
        response = self.request({"action": "unassign", "goal_id": "missing", "amount": 10})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "goal_not_found")
        self.assert_financial_truth_unchanged()

    def test_assign_and_target_actions_keep_existing_semantics_and_return_state(self):
        assigned = self.request({"action": "assign", "goal_id": "goal-1", "amount": 100})
        self.assertEqual(assigned.status_code, 200, assigned.get_json())
        self.assertEqual(assigned.get_json()["goals"][0]["cur"], 600)

        targeted = self.request({"action": "set_target", "goal_id": "goal-1", "target": 1500})
        self.assertEqual(targeted.status_code, 200, targeted.get_json())
        self.assertEqual(targeted.get_json()["goals"][0]["cur"], 600)
        self.assertEqual(targeted.get_json()["goals"][0]["tar"], 1500)
        self.assert_financial_truth_unchanged()


if __name__ == "__main__":
    unittest.main()
