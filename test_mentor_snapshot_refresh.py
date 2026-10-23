from __future__ import annotations

import copy
import unittest
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import rove_app_api as api
import rove_behavior_patterns as patterns
import test_financial_insights as insight_fixtures
import test_profile_snapshot_invalidation as profile_fixtures
from rove_behavior_snapshot import (
    BEHAVIOR_CONTRACT_VERSION,
    BEHAVIOR_ENGINE_VERSION,
    SNAPSHOT_VERSION,
    process_pending_behavior_snapshots,
)


class MentorSnapshotRefreshTests(unittest.TestCase):
    """Authenticated app refreshes recover only their own stale coaching metadata."""

    def setUp(self):
        self.fixture = profile_fixtures.ProfileSnapshotInvalidationTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.update_snapshot(
            snapshot_version=SNAPSHOT_VERSION, engine_version=BEHAVIOR_ENGINE_VERSION,
            behavior_contract_version=BEHAVIOR_CONTRACT_VERSION,
        )
        self.fallback = {
            "id": "motivation", "type": "motivation", "priority": 10,
            "title": "Stabile Basis", "message": "Synthetischer Score-Fallback",
            "action_label": "Score ansehen", "deep_link": "score",
        }
        stack = self.enterContext(ExitStack())
        for name in (
            "ensure_market_tracking_schema", "revoke_legacy_state_links",
            "apply_due_scheduled_savings", "record_due_etf_plan", "hydrate_crypto_logos",
        ):
            stack.enter_context(patch.object(api, name))
        stack.enter_context(patch.object(api, "build_live_app_data", side_effect=lambda *_a, **_k: {
            "mentor_candidate": copy.deepcopy(self.fallback), "features": {},
        }))
        stack.enter_context(patch.object(api, "claim_coach_announcement", return_value=None))
        stack.enter_context(patch.object(api, "get_feature_announcements_for_user", return_value={}))

    def update_snapshot(self, **fields):
        with closing(self.fixture.connect()) as conn:
            conn.execute(
                "UPDATE app_behavior_snapshot SET " + ",".join(f"{key}=?" for key in fields)
                + " WHERE user_id=1", tuple(fields.values()),
            )
            conn.commit()

    def request(self, method="POST", uid=1):
        with api.app.test_client() as client:
            if uid is not None:
                client.set_cookie(api.SESSION_COOKIE_NAME, self.fixture.sessions[uid])
            return client.open("/v1/state", method=method,
                               headers={"Origin": "https://getrove.de"})

    def assert_queued(self):
        row = self.fixture.snapshot()
        self.assertEqual((row["status"], row["recompute_state"]), ("stale", "pending"))
        self.assertEqual(row["invalidation_version"], 1)
        self.assertEqual(row["metrics_invalidations_received"], 1)

    def expire(self):
        self.update_snapshot(generated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())

    def test_expired_snapshot_is_queued_once_without_recomputing_or_touching_other_user(self):
        self.expire()
        other = self.fixture.snapshot(2)
        profile = self.fixture.profile()
        with patch.object(patterns, "build_shadow_inspector", side_effect=AssertionError("inline engine")):
            response = self.request()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["mentor_candidate"], self.fallback)
            self.assertIsNone(response.get_json()["coach_v4_insight"])
            self.assert_queued()
            queued = self.fixture.snapshot()
            self.assertEqual(self.request().status_code, 200)
            self.assertEqual(self.fixture.snapshot(), queued)
        self.assertEqual(self.fixture.snapshot(2), other)
        self.assertEqual(self.fixture.profile(), profile)

    def test_old_uninvalidated_profile_change_is_recovered_even_without_stored_candidate(self):
        with closing(self.fixture.connect()) as conn:
            conn.execute("UPDATE users SET income=3200 WHERE user_id=1")
            conn.commit()
        self.assertIsNone(self.fixture.snapshot()["visible_coach_payload_json"])
        self.assertEqual(self.request().status_code, 200)
        self.assert_queued()

    def test_fresh_matching_snapshot_without_evidence_is_not_queued(self):
        before = self.fixture.snapshot()
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["coach_v4_insight"])
        self.assertEqual(response.get_json()["mentor_candidate"], self.fallback)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_existing_pending_and_running_jobs_are_not_invalidated_again(self):
        self.expire()
        for state in ("pending", "running"):
            with self.subTest(state=state):
                self.update_snapshot(recompute_state=state)
                before = self.fixture.snapshot()
                self.assertEqual(self.request().status_code, 200)
                self.assertEqual(self.fixture.snapshot(), before)

    def test_get_remains_read_only_even_when_snapshot_is_expired(self):
        self.expire()
        before = self.fixture.snapshot()
        self.assertEqual(self.request("GET").status_code, 200)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_unauthenticated_refresh_does_not_queue_anyone(self):
        self.expire()
        before = self.fixture.snapshot()
        self.assertEqual(self.request(uid=None).status_code, 401)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_queue_change_rolls_back_with_failed_state_transaction(self):
        self.expire()
        before = self.fixture.snapshot()
        with patch.object(api, "get_feature_announcements_for_user", side_effect=RuntimeError("test rollback")):
            with self.assertRaises(RuntimeError):
                self.request()
        self.assertEqual(self.fixture.snapshot(), before)

    def test_missing_table_never_creates_schema_on_refresh(self):
        with closing(self.fixture.connect()) as conn:
            conn.execute("DROP TABLE app_behavior_snapshot")
            conn.commit()
        self.assertEqual(self.request().status_code, 200)
        with closing(self.fixture.connect()) as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='app_behavior_snapshot'",
            ).fetchone())

    def test_version_mismatch_and_invalid_dates_are_queued_but_errors_are_not_retried_forever(self):
        for field, value in (
            ("engine_version", "old"),
            ("generated_at", "invalid"),
            ("generated_at", (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()),
        ):
            with self.subTest(field=field, value=value):
                self.update_snapshot(
                    status="ready", recompute_state="idle", invalidation_version=0,
                    metrics_invalidations_received=0, engine_version="coach-v4-shadow-1",
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
                self.update_snapshot(**{field: value})
                self.assertEqual(self.request().status_code, 200)
                self.assert_queued()
        self.update_snapshot(status="error", recompute_state="idle")
        before = self.fixture.snapshot()
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_missing_user_snapshot_is_queued_without_altering_other_user(self):
        other = self.fixture.snapshot(2)
        with closing(self.fixture.connect()) as conn:
            conn.execute("DELETE FROM app_behavior_snapshot WHERE user_id=1")
            conn.commit()
        self.assertEqual(self.request().status_code, 200)
        self.assert_queued()
        self.assertEqual(self.fixture.snapshot(2), other)

    def test_existing_worker_restores_valid_v4_ahead_of_score_without_changing_gate(self):
        self.expire()
        candidate = insight_fixtures.FinancialInsightContractTests.v4_candidate(
            category="restaurant", direction="improving",
        )
        with patch.dict("os.environ", {
            "ROVE_COACH_V4_VISIBLE_ENABLED": "1", "ROVE_COACH_V4_VISIBLE_MODE": "ON_GLOBAL",
        }):
            self.assertIsNone(self.request().get_json()["coach_v4_insight"])
            with closing(self.fixture.connect()) as conn:
                with patch.object(patterns, "build_shadow_inspector", return_value={
                    "insight_candidates": [candidate], "primary_coach_insight": candidate["insight_id"],
                }):
                    results = process_pending_behavior_snapshots(conn, limit=1)
                conn.commit()
            self.assertEqual([row["status"] for row in results], ["ready"])
            response = self.request().get_json()
            self.assertEqual(response["mentor_candidate"], self.fallback)
            self.assertIsNotNone(response["coach_v4_insight"])
            self.assertEqual(response["coach_v4_insight"]["insight_id"], "v4:" + candidate["insight_id"])
            before = self.fixture.snapshot()
            with patch.dict("os.environ", {"ROVE_COACH_V4_VISIBLE_MODE": "OFF"}):
                self.assertIsNone(self.request().get_json()["coach_v4_insight"])
            self.assertEqual(self.fixture.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
