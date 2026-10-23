from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_behavior_snapshot import (
    compute_behavior_source_watermark,
    ensure_behavior_snapshot_table,
    invalidate_behavior_snapshot,
)
from test_financial_accounts_sprint2 import create_db


class ProfileSnapshotInvalidationTests(unittest.TestCase):
    """Real profile requests, cookie sessions and SQLite transactions on synthetic data."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "profile.db"
        for patcher in (
            patch.object(api, "DB_PATH", self.path),
            patch.object(api, "AUTH_SECRET", "profile-invalidation-test-only"),
            patch.dict(api.app.config, TESTING=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        create_db(self.path)
        self.sessions = {}
        with closing(self.connect()) as conn:
            api.ensure_auth_tables(conn)
            for uid in (1, 2):
                account = conn.execute(
                    "INSERT INTO app_accounts(email,user_id,verified_at,source) "
                    "VALUES (?,?,CURRENT_TIMESTAMP,'app')",
                    (f"profile{uid}@example.test", uid),
                ).lastrowid
                self.sessions[uid], _ = api.issue_session(conn, account)
                session_id = conn.execute(
                    "SELECT id FROM app_sessions WHERE account_id=?", (account,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO app_session_pins "
                    "(session_id,pin_verifier,unlocked_at,last_activity_at) "
                    "VALUES (?,'test-only',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (session_id,),
                )
            api.build_live_app_data(conn, 1)
            ensure_behavior_snapshot_table(conn)
            for uid in (1, 2):
                invalidate_behavior_snapshot(conn, uid, "fixture")
                conn.execute(
                    "UPDATE app_behavior_snapshot SET status='ready', recompute_state='idle', "
                    "stale_reason=NULL, invalidation_version=0, metrics_invalidations_received=0, "
                    "source_watermark=?, generated_at=? WHERE user_id=?",
                    (compute_behavior_source_watermark(conn, uid),
                     datetime.now(timezone.utc).isoformat(), uid),
                )
            conn.commit()
        api.prepare_runtime_schema()
        spy = patch.object(api, "invalidate_behavior_snapshot_safely",
                           wraps=api.invalidate_behavior_snapshot_safely)
        self.invalidate = spy.start()
        self.addCleanup(spy.stop)

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def request(self, payload, uid=1):
        with api.app.test_client() as client:
            if uid is not None:
                client.set_cookie(api.SESSION_COOKIE_NAME, self.sessions[uid])
            return client.post("/v1/profile", json=payload,
                               headers={"Origin": "https://getrove.de"})

    def save(self, payload):
        response = self.request(payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["ok"])
        return response.get_json()

    def profile(self, uid=1):
        with closing(self.connect()) as conn:
            return dict(conn.execute(
                "SELECT income,other_income,etf_savings,cash_savings FROM users WHERE user_id=?",
                (uid,),
            ).fetchone())

    def snapshot(self, uid=1):
        with closing(self.connect()) as conn:
            return dict(conn.execute(
                "SELECT * FROM app_behavior_snapshot WHERE user_id=?", (uid,),
            ).fetchone())

    def assert_queued_once(self, reason="income_plan_changed"):
        self.assertEqual(self.invalidate.call_count, 1)
        self.assertEqual(self.invalidate.call_args.args[1:], (1, reason))
        state = self.snapshot()
        self.assertEqual((state["status"], state["recompute_state"]), ("stale", "pending"))
        self.assertEqual(state["stale_reason"], reason)
        self.assertEqual(state["invalidation_version"], 1)
        self.assertEqual(state["metrics_invalidations_received"], 1)

    def confirm_savings(self):
        with closing(self.connect()) as conn:
            conn.execute(
                "INSERT INTO app_monthly_plan_status(user_id,month_key,savings_status) "
                "VALUES (1,?,'confirmed')", (datetime.now().strftime("%Y-%m"),),
            )
            conn.commit()

    def test_changed_income_queues_once_and_preserves_other_user(self):
        other_profile, other_snapshot = self.profile(2), self.snapshot(2)
        self.save({"income": 3200})
        self.assertEqual(self.profile()["income"], 3200)
        self.assert_queued_once()
        self.assertEqual(self.profile(2), other_profile)
        self.assertEqual(self.snapshot(2), other_snapshot)

    def test_changed_other_income_queues_once(self):
        self.save({"other_income": 150})
        self.assertEqual(self.profile()["other_income"], 150)
        self.assert_queued_once()

    def test_both_incomes_changed_queue_once(self):
        self.save({"income": 3200, "other_income": 150})
        self.assertEqual(self.profile()["income"], 3200)
        self.assertEqual(self.profile()["other_income"], 150)
        self.assert_queued_once()

    def test_income_reduction_to_zero_queues_once(self):
        self.save({"income": 0})
        self.assertEqual(self.profile()["income"], 0)
        self.assert_queued_once()

    def test_identical_and_equivalently_rounded_incomes_do_not_invalidate(self):
        before = self.snapshot()
        for payload in ({"income": 3000}, {"other_income": 0},
                        {"income": 3000.0, "other_income": 0.0},
                        {"income": "3000.004", "other_income": "0.004"}):
            with self.subTest(payload=payload):
                self.save(payload)
                self.invalidate.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_identical_retry_does_not_queue_twice(self):
        payload = {"income": 3200, "other_income": 150, "etf_savings": 35, "cash_savings": 110}
        self.save(payload)
        before = self.snapshot()
        self.save(payload)
        self.assert_queued_once()
        self.assertEqual(self.snapshot(), before)

    def test_null_to_zero_is_a_persisted_change_but_its_retry_is_not(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE users SET income=NULL,other_income=NULL,etf_savings=NULL WHERE user_id=1")
            conn.commit()
        payload = {"income": 0, "other_income": 0, "etf_savings": 0}
        self.save(payload)
        self.save(payload)
        self.assertEqual(self.profile(), {
            "income": 0, "other_income": 0, "etf_savings": 0, "cash_savings": 100,
        })
        self.assert_queued_once()

    def test_existing_pending_and_running_queue_semantics_are_preserved(self):
        for index, queue_state in enumerate(("pending", "running")):
            with self.subTest(queue_state=queue_state):
                self.invalidate.reset_mock()
                with closing(self.connect()) as conn:
                    conn.execute(
                        "UPDATE app_behavior_snapshot SET recompute_state=?, "
                        "invalidation_version=4,metrics_invalidations_received=4 WHERE user_id=1",
                        (queue_state,),
                    )
                    conn.commit()
                before = self.snapshot()
                self.save({"income": self.profile()["income"]})
                self.invalidate.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                self.save({"income": 3200 + index * 100})
                self.assertEqual(self.invalidate.call_count, 1)
                state = self.snapshot()
                self.assertEqual(state["recompute_state"], queue_state)
                self.assertEqual(state["invalidation_version"], 5)
                self.assertEqual(state["metrics_invalidations_received"], 5)

    def test_combined_income_and_immediate_savings_invalidate_once(self):
        self.save({"income": 3200, "other_income": 150, "etf_savings": 35, "cash_savings": 110})
        self.assertEqual(self.profile(), {
            "income": 3200, "other_income": 150, "etf_savings": 35, "cash_savings": 110,
        })
        self.assert_queued_once()

    def test_changed_income_and_unchanged_savings_invalidate_once(self):
        self.save({"income": 3200, "etf_savings": 20, "cash_savings": 100})
        self.assert_queued_once()

    def test_unchanged_income_and_changed_savings_invalidate_once(self):
        self.save({"income": 3000, "other_income": 0, "etf_savings": 35})
        self.assert_queued_once("savings_plan_changed")

    def test_changed_savings_without_income_preserve_existing_invalidation(self):
        self.save({"cash_savings": 110})
        self.assertEqual(self.profile()["cash_savings"], 110)
        self.assert_queued_once("savings_plan_changed")

    def test_unchanged_income_and_savings_do_not_invalidate(self):
        before = self.snapshot()
        self.save({"income": 3000, "other_income": 0, "etf_savings": 20, "cash_savings": 100})
        self.invalidate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_unchanged_savings_without_income_do_not_invalidate(self):
        before = self.snapshot()
        self.save({"etf_savings": 20, "cash_savings": 100})
        self.invalidate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_changed_income_with_deferred_savings_invalidates_income_only(self):
        self.confirm_savings()
        result = self.save({"income": 3200, "etf_savings": 35})
        next_month = (datetime.now().replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
        self.assertEqual(result["scheduledSavings"], {"effectiveMonth": next_month, "etf": 35, "cash": 100})
        self.assertEqual(self.profile()["etf_savings"], 20)
        self.assert_queued_once()

    def test_unchanged_income_with_deferred_savings_does_not_invalidate(self):
        self.confirm_savings()
        before = self.snapshot()
        self.save({"income": 3000, "etf_savings": 35})
        self.assertEqual(self.profile()["etf_savings"], 20)
        self.invalidate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_profile_and_invalidation_are_uncommitted_until_response_is_built(self):
        before_profile, before_snapshot = self.profile(), self.snapshot()
        build = api.build_live_app_data

        def observe(conn, uid):
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT income FROM users WHERE user_id=1").fetchone()[0], 3200)
            self.assertEqual(conn.execute(
                "SELECT recompute_state FROM app_behavior_snapshot WHERE user_id=1",
            ).fetchone()[0], "pending")
            self.assertEqual(self.profile(), before_profile)
            self.assertEqual(self.snapshot(), before_snapshot)
            return build(conn, uid)

        with patch.object(api, "build_live_app_data", side_effect=observe):
            self.save({"income": 3200, "etf_savings": 35})
        self.assert_queued_once()

    def test_late_exception_rolls_back_profile_and_snapshot_together(self):
        before_profile, before_snapshot = self.profile(), self.snapshot()
        with patch.object(api, "build_live_app_data", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.request({"income": 3200, "other_income": 150, "etf_savings": 35})
        self.assertEqual(self.invalidate.call_count, 1)
        self.assertEqual(self.profile(), before_profile)
        self.assertEqual(self.snapshot(), before_snapshot)

    def test_income_only_exception_rolls_back_profile_and_snapshot_together(self):
        before_profile, before_snapshot = self.profile(), self.snapshot()
        with patch.object(api, "build_live_app_data", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.request({"other_income": 150})
        self.assertEqual(self.invalidate.call_count, 1)
        self.assertEqual(self.profile(), before_profile)
        self.assertEqual(self.snapshot(), before_snapshot)

    def test_sql_failure_after_income_invalidation_rolls_back_both(self):
        before_profile, before_snapshot = self.profile(), self.snapshot()
        with closing(self.connect()) as conn:
            conn.execute("CREATE TRIGGER reject_savings BEFORE UPDATE OF etf_savings ON users "
                         "BEGIN SELECT RAISE(ABORT, 'synthetic savings failure'); END")
            conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic savings failure"):
            self.request({"income": 3200, "etf_savings": 35})
        self.assertEqual(self.invalidate.call_count, 1)
        self.assertEqual(self.profile(), before_profile)
        self.assertEqual(self.snapshot(), before_snapshot)

    def test_invalid_income_changes_neither_profile_nor_snapshot(self):
        before_profile, before_snapshot = self.profile(), self.snapshot()
        for payload in ({"income": -1}, {"income": "invalid"},
                        {"income": 3200, "other_income": -1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request(payload).status_code, 400)
                self.invalidate.assert_not_called()
                self.assertEqual(self.profile(), before_profile)
                self.assertEqual(self.snapshot(), before_snapshot)

    def test_existing_early_return_commit_also_queues_stored_income(self):
        # The existing endpoint commits earlier writes on a later validation return.
        response = self.request({"income": 3200, "etf_plan": {"execution_day": 99}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.profile()["income"], 3200)
        self.assert_queued_once()

    def test_unrelated_profile_update_does_not_invalidate(self):
        before = self.snapshot()
        self.save({"name": "Synthetic Profile"})
        self.invalidate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_missing_snapshot_table_preserves_existing_safe_helper_behavior(self):
        with closing(self.connect()) as conn:
            conn.execute("DROP TABLE app_behavior_snapshot")
            conn.commit()
        self.save({"income": 3200})
        self.assertEqual(self.profile()["income"], 3200)
        self.assertEqual(self.invalidate.call_count, 1)
        with closing(self.connect()) as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='app_behavior_snapshot'",
            ).fetchone())

    def test_unauthenticated_request_changes_nothing(self):
        before_profile, before_snapshot = self.profile(), self.snapshot()
        self.assertEqual(self.request({"income": 3200}, uid=None).status_code, 401)
        self.invalidate.assert_not_called()
        self.assertEqual(self.profile(), before_profile)
        self.assertEqual(self.snapshot(), before_snapshot)


if __name__ == "__main__":
    unittest.main()
