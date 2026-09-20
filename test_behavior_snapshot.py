from __future__ import annotations

import json
import sqlite3
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import rove_behavior_patterns as behavior_patterns
from migrate_behavior_snapshot import result_is_valid, run as run_snapshot_migration
from rove_behavior_snapshot import (
    BEHAVIOR_CONTRACT_VERSION,
    BEHAVIOR_ENGINE_VERSION,
    SNAPSHOT_RECOMPUTE_RUNNING,
    SNAPSHOT_STATUS_ERROR,
    SNAPSHOT_STATUS_READY,
    SNAPSHOT_STATUS_STALE,
    compute_behavior_source_watermark,
    delete_behavior_snapshot,
    ensure_behavior_snapshot_table,
    get_behavior_snapshot_metrics,
    get_behavior_snapshot_status,
    get_visible_behavior_snapshot,
    invalidate_behavior_snapshot,
    process_pending_behavior_snapshots,
    recompute_behavior_snapshot,
)


class BehaviorSnapshotTests(unittest.TestCase):
    NOW = datetime(2026, 9, 18, 12, 0, 0)

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1);
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL,
                created_at TEXT, transaction_at TEXT
            );
            CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT,
                amount REAL, created_at TEXT, occurred_at TEXT
            );
            CREATE TABLE category_budgets (
                user_id INTEGER, category TEXT, monthly_limit REAL,
                active_month TEXT
            );
            CREATE TABLE app_contracts (
                user_id INTEGER, contract_id TEXT, amount REAL,
                created_at TEXT, updated_at TEXT
            );
            """
        )
        ensure_behavior_snapshot_table(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @staticmethod
    def inspector(*_args, **_kwargs):
        return {
            "insight_candidates": [{
                "insight_id": "budget_attention:test",
                "primary_coach_insight": True,
                "primary_report_insight": False,
            }]
        }

    @staticmethod
    def visible(_insight):
        return {
            "visible_behavior_contract_version": behavior_patterns.VISIBLE_BEHAVIOR_CONTRACT_VERSION,
            "insight_type": "budget_attention",
            "category_display": "Sonstiges",
        }

    def recompute(self):
        with patch.object(behavior_patterns, "build_shadow_inspector", self.inspector), \
             patch.object(behavior_patterns, "build_visible_behavior_insight", self.visible):
            return recompute_behavior_snapshot(self.conn, 1, now=self.NOW)

    def test_invalidation_is_async_and_read_never_recomputes(self):
        self.assertTrue(invalidate_behavior_snapshot(self.conn, 1, "expense_changed"))
        self.assertIsNone(get_visible_behavior_snapshot(self.conn, 1, now=self.NOW))
        status = get_behavior_snapshot_status(self.conn, 1, now=self.NOW)
        self.assertEqual(status["status"], SNAPSHOT_STATUS_STALE)
        self.assertEqual(status["recompute_state"], "pending")

    def test_metrics_count_invalidations_and_coalescing_without_recompute(self):
        invalidate_behavior_snapshot(self.conn, 1, "expense_changed")
        invalidate_behavior_snapshot(self.conn, 1, "budget_changed")
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        metrics = get_behavior_snapshot_metrics(self.conn, now=self.NOW)
        self.conn.set_trace_callback(None)

        self.assertEqual(metrics["pending_users"], 1)
        self.assertEqual(metrics["invalidations_received"], 2)
        self.assertEqual(metrics["invalidations_coalesced"], 1)
        self.assertEqual(metrics["recomputes_avoided_by_coalescing"], 1)
        self.assertEqual(metrics["sql_query_count"], 1)
        self.assertEqual(len(statements), 1)
        self.assertLess(len(json.dumps(metrics)), 4096)

    def test_suppression_metrics_separate_eligibility_and_reasons(self):
        def inspector(*_args, **_kwargs):
            return {
                "patterns": [{"pattern_id": "pattern-1"}],
                "insight_candidates": [
                    {
                        "insight_id": "budget-1",
                        "insight_type": "budget_attention",
                        "coach_eligible": False,
                        "report_eligible": True,
                        "coach_suppression_reason": "healthy_overall_budget",
                        "report_suppression_reason": None,
                        "primary_coach_insight": False,
                        "primary_report_insight": True,
                    },
                    {
                        "insight_id": "subscription-1",
                        "insight_type": "subscription_cluster",
                        "coach_eligible": True,
                        "report_eligible": False,
                        "coach_suppression_reason": None,
                        "report_suppression_reason": "uncertain_activity_status",
                        "primary_coach_insight": False,
                        "primary_report_insight": False,
                    },
                    {
                        "insight_id": "trend-1",
                        "insight_type": "spending_trend_improving",
                        "coach_eligible": True,
                        "report_eligible": True,
                        "coach_suppression_reason": None,
                        "report_suppression_reason": None,
                        "primary_coach_insight": True,
                        "primary_report_insight": True,
                    },
                ],
            }

        with patch.object(behavior_patterns, "build_shadow_inspector", inspector), \
             patch.object(behavior_patterns, "build_visible_behavior_insight", self.visible):
            self.assertEqual(
                recompute_behavior_snapshot(self.conn, 1, now=self.NOW)["status"],
                SNAPSHOT_STATUS_READY,
            )
        metrics = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(metrics["patterns_detected"], 1)
        self.assertEqual(metrics["insight_candidates"], 3)
        self.assertEqual(metrics["coach_eligible"], 2)
        self.assertEqual(metrics["coach_suppressed"], 1)
        self.assertEqual(metrics["report_eligible"], 2)
        self.assertEqual(metrics["report_suppressed"], 1)
        self.assertEqual(metrics["primary_coach_candidates"], 1)
        self.assertEqual(metrics["primary_report_candidates"], 2)
        self.assertEqual(metrics["coach_eligible_not_primary"], 1)
        self.assertEqual(metrics["users_with_eligible_coach"], 1)
        self.assertEqual(metrics["users_no_eligible_coach"], 0)
        self.assertEqual(
            metrics["suppression_reasons"]["coach"]["healthy_overall_budget"],
            1,
        )
        self.assertEqual(
            metrics["suppression_reasons"]["report"]["uncertain_activity_status"],
            1,
        )
        self.assertEqual(metrics["insight_types"]["budget_attention"], 1)

    def test_no_evidence_is_not_counted_as_suppressed_insight(self):
        def inspector(*_args, **_kwargs):
            return {"patterns": [], "insight_candidates": []}

        with patch.object(behavior_patterns, "build_shadow_inspector", inspector):
            self.assertEqual(
                recompute_behavior_snapshot(self.conn, 1, now=self.NOW)["status"],
                SNAPSHOT_STATUS_READY,
            )
        metrics = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(metrics["no_evidence_users"], 1)
        self.assertEqual(metrics["users_no_eligible_coach"], 1)
        self.assertEqual(metrics["coach_suppressed"], 0)
        self.assertEqual(metrics["insight_candidates"], 0)

    def test_pattern_without_insight_is_separate_from_no_evidence(self):
        def inspector(*_args, **_kwargs):
            return {
                "patterns": [{"pattern_id": "observed-only"}],
                "insight_candidates": [],
            }

        with patch.object(behavior_patterns, "build_shadow_inspector", inspector):
            self.assertEqual(
                recompute_behavior_snapshot(self.conn, 1, now=self.NOW)["status"],
                SNAPSHOT_STATUS_READY,
            )
        metrics = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(metrics["patterns_detected_no_insight"], 1)
        self.assertEqual(metrics["no_evidence_users"], 0)
        self.assertEqual(metrics["coach_suppressed"], 0)

    def test_superseded_candidate_is_counted_without_changing_eligibility(self):
        def inspector(*_args, **_kwargs):
            return {
                "patterns": [{"pattern_id": "p1"}, {"pattern_id": "p2"}],
                "insight_candidates": [
                    {
                        "insight_id": "winner",
                        "insight_type": "budget_attention",
                        "coach_eligible": True,
                        "report_eligible": False,
                        "coach_suppression_reason": None,
                        "report_suppression_reason": None,
                        "primary_coach_insight": True,
                        "primary_report_insight": False,
                    },
                    {
                        "insight_id": "superseded",
                        "insight_type": "spending_trend_worsening",
                        "coach_eligible": False,
                        "report_eligible": False,
                        "coach_suppression_reason": "superseded",
                        "report_suppression_reason": None,
                        "primary_coach_insight": False,
                        "primary_report_insight": False,
                    },
                ],
            }

        with patch.object(behavior_patterns, "build_shadow_inspector", inspector), \
             patch.object(behavior_patterns, "build_visible_behavior_insight", self.visible):
            self.assertEqual(
                recompute_behavior_snapshot(self.conn, 1, now=self.NOW)["status"],
                SNAPSHOT_STATUS_READY,
            )
        metrics = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(metrics["coach_eligible"], 1)
        self.assertEqual(metrics["coach_suppressed"], 1)
        self.assertEqual(metrics["primary_coach_candidates"], 1)
        self.assertEqual(metrics["suppression_reasons"]["coach"]["superseded"], 1)

    def test_suppression_metrics_are_bounded_and_pii_free(self):
        def inspector(*_args, **_kwargs):
            return {
                "patterns": [{"pattern_id": "private-pattern"}],
                "insight_candidates": [{
                    "insight_id": "private-insight",
                    "insight_type": "budget_attention",
                    "merchant": "Private Merchant",
                    "source_ids": ["secret-source-id"],
                    "coach_eligible": False,
                    "report_eligible": False,
                    "coach_suppression_reason": "insufficient_history",
                    "report_suppression_reason": "insufficient_history",
                }],
            }

        with patch.object(behavior_patterns, "build_shadow_inspector", inspector):
            recompute_behavior_snapshot(self.conn, 1, now=self.NOW)
        metrics = get_behavior_snapshot_metrics(self.conn)
        serialized = json.dumps(metrics, sort_keys=True)
        self.assertNotIn("Private Merchant", serialized)
        self.assertNotIn("secret-source-id", serialized)
        self.assertNotIn("private-insight", serialized)
        self.assertNotIn("private-pattern", serialized)
        self.assertLessEqual(len(metrics["suppression_reasons"]), 2)

    def test_metrics_read_does_not_double_count_or_change_arbitration(self):
        self.assertEqual(self.recompute()["status"], SNAPSHOT_STATUS_READY)
        before = get_behavior_snapshot_metrics(self.conn)
        first = get_behavior_snapshot_metrics(self.conn)
        second = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(first, second)
        self.assertEqual(
            first["primary_coach_candidates"],
            before["primary_coach_candidates"],
        )
        self.assertEqual(
            first["recomputes_completed"],
            before["recomputes_completed"],
        )
        self.assertEqual(first["recomputes_completed"], 1)

    def test_metrics_cover_success_duration_and_failure(self):
        self.assertEqual(self.recompute()["status"], SNAPSHOT_STATUS_READY)
        metrics = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(metrics["recomputes_started"], 1)
        self.assertEqual(metrics["recomputes_completed"], 1)
        self.assertEqual(metrics["recomputes_failed"], 0)
        self.assertIsNotNone(metrics["average_recompute_duration_ms"])
        self.assertIsNotNone(metrics["max_recompute_duration_ms"])

        invalidate_behavior_snapshot(self.conn, 1, "expense_changed")
        with patch.object(behavior_patterns, "build_shadow_inspector", side_effect=RuntimeError("private")):
            self.assertEqual(
                recompute_behavior_snapshot(self.conn, 1, now=self.NOW)["status"],
                SNAPSHOT_STATUS_ERROR,
            )
        metrics = get_behavior_snapshot_metrics(self.conn)
        self.assertEqual(metrics["recomputes_failed"], 1)

    def test_metrics_are_aggregated_across_users(self):
        self.conn.execute("INSERT INTO users VALUES (2)")
        invalidate_behavior_snapshot(self.conn, 1, "expense_changed")
        invalidate_behavior_snapshot(self.conn, 2, "expense_changed")
        metrics = get_behavior_snapshot_metrics(self.conn, now=self.NOW)
        self.assertEqual(metrics["pending_users"], 2)
        self.assertEqual(metrics["invalidations_received"], 2)
        self.assertGreaterEqual(metrics["oldest_pending_age_seconds"], 0)
        self.assertGreaterEqual(metrics["average_pending_age_seconds"], 0)

    def test_one_hundred_invalidations_coalesce_to_one_pending_user(self):
        for index in range(100):
            invalidate_behavior_snapshot(self.conn, 1, f"change_{index}")
        metrics = get_behavior_snapshot_metrics(self.conn, now=self.NOW)
        self.assertEqual(metrics["pending_users"], 1)
        self.assertEqual(metrics["invalidations_received"], 100)
        self.assertEqual(metrics["invalidations_coalesced"], 99)
        self.assertEqual(metrics["recomputes_avoided_by_coalescing"], 99)
        self.assertEqual(metrics["recomputes_started"], 0)

    def test_recompute_publishes_one_versioned_visible_payload(self):
        result = self.recompute()
        self.assertEqual(result["status"], SNAPSHOT_STATUS_READY)
        payload = get_visible_behavior_snapshot(self.conn, 1)
        self.assertEqual(payload["insight_type"], "budget_attention")
        status = get_behavior_snapshot_status(self.conn, 1)
        self.assertEqual(status["status"], SNAPSHOT_STATUS_READY)
        self.assertEqual(status["behavior_contract_version"], 1)

    def test_source_change_during_recompute_cannot_publish_old_watermark(self):
        def mutating_inspector(*_args, **_kwargs):
            self.conn.execute(
                "INSERT INTO expenses VALUES (1, 1, 12, '2026-09-18', '2026-09-18')"
            )
            return self.inspector()

        with patch.object(behavior_patterns, "build_shadow_inspector", mutating_inspector), \
             patch.object(behavior_patterns, "build_visible_behavior_insight", self.visible):
            result = recompute_behavior_snapshot(self.conn, 1, now=self.NOW)
        self.assertEqual(result["status"], SNAPSHOT_STATUS_STALE)
        status = get_behavior_snapshot_status(self.conn, 1)
        self.assertEqual(status["stale_reason"], "source_changed_during_recompute")
        self.assertEqual(status["recompute_state"], "pending")
        self.assertIsNone(get_visible_behavior_snapshot(self.conn, 1))

    def test_invalidation_during_failed_recompute_keeps_follow_up_pending(self):
        invalidate_behavior_snapshot(self.conn, 1, "expense_changed")

        def invalidate_then_fail(*_args, **_kwargs):
            invalidate_behavior_snapshot(self.conn, 1, "budget_changed")
            raise RuntimeError("private source detail")

        with patch.object(behavior_patterns, "build_shadow_inspector", invalidate_then_fail):
            result = recompute_behavior_snapshot(self.conn, 1, now=self.NOW)
        self.assertEqual(result["status"], "stale")
        status = get_behavior_snapshot_status(self.conn, 1)
        self.assertEqual(status["stale_reason"], "source_changed_during_recompute")
        self.assertEqual(status["recompute_state"], "pending")
        self.assertIsNone(get_visible_behavior_snapshot(self.conn, 1))

    def test_error_does_not_replace_previous_payload_or_log_exception_text(self):
        self.recompute()
        before = self.conn.execute(
            "SELECT visible_coach_payload_json FROM app_behavior_snapshot WHERE user_id=1"
        ).fetchone()[0]
        invalidate_behavior_snapshot(self.conn, 1, "budget_changed")
        with patch.object(behavior_patterns, "build_shadow_inspector", side_effect=RuntimeError("secret prompt")):
            result = recompute_behavior_snapshot(self.conn, 1, now=self.NOW)
        self.assertEqual(result["status"], SNAPSHOT_STATUS_ERROR)
        row = self.conn.execute(
            "SELECT status, visible_coach_payload_json, last_error_class FROM app_behavior_snapshot WHERE user_id=1"
        ).fetchone()
        self.assertEqual(row["status"], SNAPSHOT_STATUS_ERROR)
        self.assertEqual(row["visible_coach_payload_json"], before)
        self.assertEqual(row["last_error_class"], "RuntimeError")
        self.assertNotIn("secret prompt", json.dumps(dict(row)))
        self.assertIsNone(get_visible_behavior_snapshot(self.conn, 1))

    def test_running_claim_coalesces_second_recompute(self):
        invalidate_behavior_snapshot(self.conn, 1, "already_running_test")
        self.conn.execute(
            "UPDATE app_behavior_snapshot SET recompute_state=? WHERE user_id=1",
            (SNAPSHOT_RECOMPUTE_RUNNING,),
        )
        invalidate_behavior_snapshot(self.conn, 1, "running_change")
        metrics = get_behavior_snapshot_metrics(self.conn, now=self.NOW)
        self.assertEqual(metrics["invalidations_coalesced"], 1)
        self.assertEqual(metrics["recomputes_avoided_by_coalescing"], 0)
        self.assertEqual(self.recompute()["status"], "already_running")

    def test_pending_processor_is_bounded_and_deduplicated(self):
        invalidate_behavior_snapshot(self.conn, 1, "contract_changed")
        with patch.object(behavior_patterns, "build_shadow_inspector", self.inspector), \
             patch.object(behavior_patterns, "build_visible_behavior_insight", self.visible):
            results = process_pending_behavior_snapshots(self.conn, limit=1, now=self.NOW)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], SNAPSHOT_STATUS_READY)

    def test_ttl_and_version_mismatch_are_fail_closed(self):
        self.recompute()
        old = (datetime.now() - timedelta(days=2)).isoformat()
        self.conn.execute(
            "UPDATE app_behavior_snapshot SET generated_at=? WHERE user_id=1", (old,)
        )
        self.assertIsNone(get_visible_behavior_snapshot(self.conn, 1))
        self.recompute()
        self.conn.execute(
            "UPDATE app_behavior_snapshot SET engine_version='old' WHERE user_id=1"
        )
        self.assertIsNone(get_visible_behavior_snapshot(self.conn, 1))

    def test_read_path_is_snapshot_lookup_only(self):
        self.recompute()
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        started = time.perf_counter()
        with patch.object(behavior_patterns, "detect_behavior_patterns", side_effect=AssertionError("recompute on read")):
            payload = get_visible_behavior_snapshot(self.conn, 1)
        elapsed = time.perf_counter() - started
        self.conn.set_trace_callback(None)
        self.assertIsNotNone(payload)
        self.assertLessEqual(len(statements), 2)
        self.assertTrue(all("FROM expenses" not in statement for statement in statements))
        self.assertTrue(all("FROM app_contracts" not in statement for statement in statements))
        self.assertLess(len(json.dumps(payload)), 4096)
        self.assertLess(elapsed, 0.1)

    def test_migration_is_safe_on_fresh_and_existing_databases(self):
        fresh = sqlite3.connect(":memory:")
        try:
            ensure_behavior_snapshot_table(fresh)
            ensure_behavior_snapshot_table(fresh)
            self.assertTrue(fresh.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_behavior_snapshot'"
            ).fetchone())
            self.assertTrue(fresh.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_app_behavior_snapshot_queue'"
            ).fetchone())
        finally:
            fresh.close()

        before = [row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        ensure_behavior_snapshot_table(self.conn)
        after = [row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        self.assertEqual([name for name in before if name != "app_behavior_snapshot"],
                         [name for name in after if name != "app_behavior_snapshot"])

    def test_canonical_migration_is_dry_run_idempotent_and_preserves_schema(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "migration.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE users (user_id INTEGER PRIMARY KEY);
                    CREATE TABLE expenses (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL);
                    INSERT INTO users VALUES (1);
                    INSERT INTO expenses VALUES (1, 1, 42.0);
                    """
                )
                before_tables = conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                before_expense = conn.execute(
                    "SELECT * FROM expenses"
                ).fetchall()

            dry = run_snapshot_migration(db_path, apply=False)
            self.assertFalse(dry["table_before"])
            self.assertFalse(dry["table_after"])
            self.assertFalse(dry["changed"])
            self.assertTrue(result_is_valid(dry, apply=False))

            first = run_snapshot_migration(db_path, apply=True)
            second = run_snapshot_migration(db_path, apply=True)
            self.assertTrue(first["changed"])
            self.assertTrue(first["table_after"])
            self.assertTrue(first["queue_index_after"])
            self.assertTrue(first["metrics_after"])
            self.assertEqual(first["integrity_check"], "ok")
            self.assertEqual(first["foreign_key_errors"], 0)
            self.assertTrue(result_is_valid(first, apply=True))
            self.assertFalse(second["changed"])
            self.assertEqual(second["backup"], "")

            with sqlite3.connect(db_path) as conn:
                after_tables = conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                after_expense = conn.execute(
                    "SELECT * FROM expenses"
                ).fetchall()
                self.assertTrue(conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='index' "
                    "AND name='idx_app_behavior_snapshot_queue'"
                ).fetchone())

            self.assertEqual([row for row in before_tables if row[0] != "app_behavior_snapshot"],
                             [row for row in after_tables if row[0] != "app_behavior_snapshot"])
            self.assertEqual(before_expense, after_expense)

    def test_canonical_migration_upgrades_existing_snapshot_metrics(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy-snapshot.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE users (user_id INTEGER PRIMARY KEY);
                    INSERT INTO users VALUES (1);
                    CREATE TABLE app_behavior_snapshot (
                        user_id INTEGER PRIMARY KEY,
                        snapshot_version INTEGER NOT NULL,
                        engine_version TEXT NOT NULL,
                        behavior_contract_version INTEGER NOT NULL,
                        generated_at TEXT,
                        source_watermark TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL CHECK(status IN ('ready', 'stale', 'error')),
                        stale_reason TEXT,
                        primary_coach_insight_id TEXT,
                        primary_report_insight_id TEXT,
                        visible_coach_payload_json TEXT,
                        recompute_state TEXT NOT NULL DEFAULT 'idle'
                            CHECK(recompute_state IN ('idle', 'pending', 'running')),
                        invalidation_version INTEGER NOT NULL DEFAULT 0,
                        last_error_class TEXT,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                    );
                    CREATE INDEX idx_app_behavior_snapshot_queue
                        ON app_behavior_snapshot(recompute_state, updated_at);
                    """
                )

            result = run_snapshot_migration(db_path, apply=True)
            self.assertTrue(result["changed"])
            self.assertTrue(result["metrics_after"])
            self.assertTrue(result_is_valid(result, apply=True))

            with sqlite3.connect(db_path) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(app_behavior_snapshot)")
                }
            self.assertIn("metrics_recomputes_started", columns)
            self.assertIn("metrics_max_recompute_duration_ms", columns)
            self.assertIn("metrics_suppression_reasons_json", columns)
            self.assertIn("metrics_insight_types_json", columns)

    def test_delete_cleanup_removes_snapshot(self):
        self.recompute()
        invalidate_behavior_snapshot(self.conn, 1, "expense_changed")
        delete_behavior_snapshot(self.conn, 1)
        self.assertIsNone(get_behavior_snapshot_status(self.conn, 1).get("generated_at"))
        self.assertEqual(process_pending_behavior_snapshots(self.conn), [])

    def test_watermark_contains_metadata_only(self):
        watermark = compute_behavior_source_watermark(self.conn, 1)
        self.assertNotIn("secret", watermark)
        self.assertIn('"table":"expenses"', watermark)


if __name__ == "__main__":
    unittest.main()
