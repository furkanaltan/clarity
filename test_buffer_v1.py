from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import build_buffer_data, build_live_app_data, ensure_buffer_target_column
from rove_score import calculate_score
from rove_financial_accounts import FEATURE_MULTI_CASH_ACCOUNTS_V1, set_feature_enabled
from test_financial_accounts_sprint2 import create_db
from test_score_v2 import make_connection, score, user


class BufferBasisTests(unittest.TestCase):
    def test_cash_coverage_and_personal_gap(self):
        with closing(make_connection()) as conn:
            result = build_buffer_data(conn, 1, user(
                current_cash=4600, fixed_costs=2000, buffer_target_amount=6000,
            ))
        self.assertEqual(result, {
            "available_cash": 4600, "monthly_basis": 2000, "basis_type": "fixed_costs",
            "covered_months": 2.3, "target_amount": 6000, "target_months": 3.0, "gap": 1400,
        })

    def test_cash_above_target_has_no_gap(self):
        with closing(make_connection()) as conn:
            result = build_buffer_data(conn, 1, user(current_cash=7000, buffer_target_amount=6000))
        self.assertEqual(result["gap"], 0)

    def test_missing_basis_preserves_cash_and_target_without_months(self):
        with closing(make_connection()) as conn:
            for basis in (None, 0, -1):
                with self.subTest(basis=basis):
                    result = build_buffer_data(conn, 1, user(
                        current_cash=4600, fixed_costs=basis, buffer_target_amount=6000,
                    ))
                    self.assertEqual(result["available_cash"], 4600)
                    self.assertIsNone(result["covered_months"])
                    self.assertIsNone(result["target_months"])
                    self.assertEqual(result["gap"], 1400)

    def test_missing_target_does_not_invent_goal_or_gap(self):
        with closing(make_connection()) as conn:
            result = build_buffer_data(conn, 1, user(current_cash=4600, fixed_costs=2000))
        self.assertIsNone(result["target_amount"])
        self.assertIsNone(result["target_months"])
        self.assertIsNone(result["gap"])

    def test_non_cash_assets_and_normal_goals_do_not_enter_buffer_or_change_score(self):
        with closing(make_connection()) as conn:
            profile = user(current_cash=4600, fixed_costs=2000)
            profile.update(current_investments=90000, crypto=12000, valuables=5000,
                           property_value=500000, goal_amount=6000, goal_description="Notgroschen")
            baseline = score(conn, profile)
            profile["buffer_target_amount"] = 6000
            result = build_buffer_data(conn, 1, profile)
            after = score(conn, profile)
        self.assertEqual(result["available_cash"], 4600)
        self.assertEqual(after, baseline)

    def test_negative_cash_is_not_replaced_by_positive_goal_or_assets(self):
        with closing(make_connection()) as conn:
            result = build_buffer_data(conn, 1, user(
                current_cash=-100, fixed_costs=1000, buffer_target_amount=6000,
            ))
        self.assertEqual(result["available_cash"], -100)
        self.assertEqual(result["covered_months"], -0.1)
        self.assertEqual(result["gap"], 6100)

    def test_migration_is_additive_nullable_idempotent_and_preserves_data(self):
        for populated in (False, True):
            with self.subTest(populated=populated), closing(sqlite3.connect(":memory:")) as conn:
                conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, current_cash REAL)")
                if populated:
                    conn.execute("INSERT INTO users VALUES (1, 4600)")
                ensure_buffer_target_column(conn)
                if populated:
                    self.assertEqual(conn.execute("SELECT * FROM users").fetchall(), [(1, 4600, None)])
                    conn.execute("UPDATE users SET buffer_target_amount=6000 WHERE user_id=1")
                trace = []
                conn.set_trace_callback(trace.append)
                ensure_buffer_target_column(conn)
                self.assertFalse(any("ALTER" in statement.upper() for statement in trace))
                if populated:
                    self.assertEqual(conn.execute("SELECT buffer_target_amount FROM users").fetchone()[0], 6000)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


class BufferApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        create_db(self.path)
        for patcher in (patch.object(api, "DB_PATH", self.path),
                        patch.object(api, "AUTH_SECRET", "buffer-test-only-secret")):
            patcher.start()
            self.addCleanup(patcher.stop)
        api.app.config.update(TESTING=True)
        self.sessions = {}
        with closing(self.connect()) as conn:
            ensure_buffer_target_column(conn)
            api.ensure_auth_tables(conn)
            conn.execute("UPDATE users SET current_cash=4600, fixed_costs=2000 WHERE user_id=1")
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            for uid in (1, 2):
                account = conn.execute(
                    "INSERT INTO app_accounts (email, user_id, verified_at, source) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP, 'app')", (f"buffer{uid}@example.test", uid),
                ).lastrowid
                token, _ = api.issue_session(conn, account)
                self.sessions[uid] = token
                sid = conn.execute("SELECT id FROM app_sessions WHERE account_id=?", (account,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO app_session_pins (session_id, pin_verifier, unlocked_at, last_activity_at) "
                    "VALUES (?, 'unused-test-verifier', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (sid,),
                )
            api.ensure_app_goals_table(conn)
            conn.execute("INSERT INTO app_goals(user_id,goal_id,name,target_amount,current_amount) "
                         "VALUES (1,'saved-goal','Notgroschen',2000,300)")
            build_live_app_data(conn, 1)
            conn.commit()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def request(self, payload, *, uid=1, origin="https://getrove.de"):
        with api.app.test_client() as client:
            if uid is not None:
                client.set_cookie(api.SESSION_COOKIE_NAME, self.sessions[uid])
            return client.post("/v1/profile", json=payload, headers={"Origin": origin})

    def financial_truth(self):
        with closing(self.connect()) as conn:
            tables = ("app_account_balances", "app_financial_accounts", "expenses", "investment_events",
                      "app_cash_movements", "app_goals", "app_properties")
            snapshot = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
            snapshot["users"] = [tuple(row) for row in conn.execute(
                "SELECT user_id,current_cash,current_investments,fixed_costs FROM users ORDER BY user_id")]
            profile = conn.execute("SELECT * FROM users WHERE user_id=1").fetchone()
            snapshot["score"] = calculate_score(conn, 1, profile)
            return snapshot

    def test_save_edit_remove_returns_server_object_and_preserves_financial_truth(self):
        before = self.financial_truth()
        for target, gap in ((6000, 1400), (4000, 0), (None, None)):
            response = self.request({"buffer_target_amount": target, "user_id": 2})
            self.assertEqual(response.status_code, 200, response.get_json())
            result = response.get_json()["buffer"]
            self.assertEqual(result["target_amount"], target)
            self.assertEqual(result["gap"], gap)
            self.assertEqual(result["covered_months"], 2.3)
            with closing(self.connect()) as conn:
                self.assertEqual(build_live_app_data(conn, 1)["buffer"], result)
                self.assertIsNone(build_live_app_data(conn, 2)["buffer"]["target_amount"])
            self.assertEqual(self.financial_truth(), before)

    def test_invalid_amounts_do_not_mutate_target(self):
        self.assertEqual(self.request({"buffer_target_amount": 6000}).status_code, 200)
        for target in (0, -1, 0.001, 1000001, 10**400, True, "6000", [], {}, float("nan"), float("inf")):
            with self.subTest(target=target):
                response = self.request({"buffer_target_amount": target})
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(response.get_json()["error"], "valid_buffer_target_required")
        with closing(self.connect()) as conn:
            self.assertEqual(build_live_app_data(conn, 1)["buffer"]["target_amount"], 6000)

    def test_rejected_profile_payload_does_not_save_buffer_target(self):
        response = self.request({"buffer_target_amount": 6000, "payday": 32})
        self.assertEqual(response.status_code, 400)
        with closing(self.connect()) as conn:
            self.assertIsNone(build_live_app_data(conn, 1)["buffer"]["target_amount"])

    def test_authenticated_state_includes_persisted_buffer(self):
        saved = self.request({"buffer_target_amount": 6000}).get_json()["buffer"]
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, self.sessions[1])
            response = client.get("/v1/state")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["buffer"], saved)

    def test_auth_pin_and_origin_protect_write(self):
        self.assertEqual(self.request({"buffer_target_amount": 6000}, uid=None).status_code, 401)
        self.assertEqual(self.request({"buffer_target_amount": 6000}, origin="https://foreign.test").status_code, 403)
        with closing(self.connect()) as conn:
            conn.execute("UPDATE app_session_pins SET unlocked_at=NULL,last_activity_at=NULL")
            conn.commit()
        self.assertEqual(self.request({"buffer_target_amount": 6000}).status_code, 423)

    def test_active_accounts_are_the_only_cash_source_with_no_double_count(self):
        with closing(self.connect()) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
            conn.execute("UPDATE users SET current_cash=99999,current_investments=120000 WHERE user_id=1")
            profile = conn.execute("SELECT * FROM users WHERE user_id=1").fetchone()
            expected = conn.execute("SELECT SUM(balance) FROM app_financial_accounts WHERE user_id=1 AND status='active'").fetchone()[0]
            result = build_buffer_data(conn, 1, profile)
            self.assertEqual(result["available_cash"], expected)
            self.assertEqual(result["covered_months"], calculate_score(conn, 1, profile)["liquidity_months"])
            conn.execute("UPDATE app_financial_accounts SET status='archived' WHERE user_id=1")
            self.assertEqual(build_buffer_data(conn, 1, profile)["available_cash"], 0)

    def test_legacy_missing_and_zero_cash_follow_score_source(self):
        with closing(self.connect()) as conn:
            for value, expected in ((None, 1250), (0, 0), (4600, 4600)):
                conn.execute("UPDATE users SET current_cash=? WHERE user_id=1", (value,))
                profile = conn.execute("SELECT * FROM users WHERE user_id=1").fetchone()
                result = build_buffer_data(conn, 1, profile)
                self.assertEqual(result["available_cash"], expected)
                self.assertEqual(result["covered_months"], calculate_score(conn, 1, profile)["liquidity_months"])

    def test_request_does_not_apply_missing_migration(self):
        with closing(self.connect()) as conn:
            conn.execute("ALTER TABLE users DROP COLUMN buffer_target_amount")
            conn.commit()
        response = self.request({"buffer_target_amount": 6000})
        self.assertEqual(response.status_code, 503)
        with closing(self.connect()) as conn:
            self.assertNotIn("buffer_target_amount", {row[1] for row in conn.execute("PRAGMA table_info(users)")})


if __name__ == "__main__":
    unittest.main()
