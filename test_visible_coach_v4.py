from __future__ import annotations

import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from rove_behavior_snapshot import (
    BEHAVIOR_CONTRACT_VERSION,
    BEHAVIOR_ENGINE_VERSION,
    SNAPSHOT_VERSION,
    ensure_behavior_snapshot_table,
)
from rove_visible_coach_v4 import (
    get_visible_coach_v4,
    get_visible_pilot_metrics,
    reset_visible_pilot_metrics,
)


class VisibleCoachV4Tests(unittest.TestCase):
    NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        ensure_behavior_snapshot_table(self.conn)
        self.conn.commit()
        reset_visible_pilot_metrics()

    def tearDown(self) -> None:
        self.conn.close()

    def snapshot(
        self,
        payload,
        *,
        status="ready",
        snapshot_version=SNAPSHOT_VERSION,
        engine=BEHAVIOR_ENGINE_VERSION,
        contract=BEHAVIOR_CONTRACT_VERSION,
        recompute_state="idle",
    ):
        self.conn.execute(
            """INSERT INTO app_behavior_snapshot (
                user_id, snapshot_version, engine_version,
                behavior_contract_version, generated_at, source_watermark,
                status, visible_coach_payload_json, recompute_state, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, 'idle', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                snapshot_version=excluded.snapshot_version,
                engine_version=excluded.engine_version,
                behavior_contract_version=excluded.behavior_contract_version,
                generated_at=excluded.generated_at,
                status=excluded.status,
                visible_coach_payload_json=excluded.visible_coach_payload_json,
                recompute_state=excluded.recompute_state,
                updated_at=excluded.updated_at""",
            (
                7,
                snapshot_version,
                engine,
                contract,
                self.NOW.isoformat(),
                status,
                json.dumps(payload),
                self.NOW.isoformat(),
            ),
        )
        self.conn.execute(
            "UPDATE app_behavior_snapshot SET recompute_state=? WHERE user_id=7",
            (recompute_state,),
        )
        self.conn.commit()

    @staticmethod
    def budget_payload(**overrides):
        payload = {
            "visible_behavior_contract_version": 1,
            "insight_type": "budget_attention",
            "category_display": "Sonstiges",
            "confidence_class": "high",
            "overall_budget_status": "under_pressure",
            "amount_current": 228.0,
            "budget_amount": 100.0,
            "amount_over_budget": 128.0,
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def improvement_payload(**overrides):
        payload = {
            "visible_behavior_contract_version": 1,
            "insight_type": "spending_trend_improving",
            "category_display": "Restaurants",
            "confidence_class": "high",
            "direction": "improving",
            "monthly_amounts": [
                {"month": "2026-06", "amount": 180.0},
                {"month": "2026-07", "amount": 140.0},
                {"month": "2026-08", "amount": 95.0},
            ],
        }
        payload.update(overrides)
        return payload

    def call(self, payload, **env):
        with patch.dict(os.environ, {
            "ROVE_COACH_V4_VISIBLE_ENABLED": "1",
            "ROVE_COACH_V4_VISIBLE_USER_IDS": "7",
            **env,
        }, clear=False):
            return get_visible_coach_v4(self.conn, 7, now=self.NOW)

    def test_flag_off_is_default_deny(self):
        self.snapshot(self.budget_payload())
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_visible_coach_v4(self.conn, 7, now=self.NOW))
        self.assertEqual(get_visible_pilot_metrics()["delivered"], 0)
        self.assertEqual(get_visible_pilot_metrics()["skipped"], {"flag_off": 1})

    def test_allowlisted_ready_snapshot_returns_deterministic_budget_text(self):
        self.snapshot(self.budget_payload())
        result = self.call(self.budget_payload())
        self.assertEqual(result["insight_type"], "budget_attention")
        self.assertEqual(result["visible_behavior_contract_version"], 1)
        self.assertIn("128,00 €", result["message"])
        self.assertIn("Sonstiges", result["message"])
        self.assertNotIn("Teilbudget", result["message"])
        self.assertEqual(get_visible_pilot_metrics()["delivered"], 1)

    def test_non_allowlisted_user_is_not_visible(self):
        self.snapshot(self.budget_payload())
        with patch.dict(os.environ, {
            "ROVE_COACH_V4_VISIBLE_ENABLED": "1",
            "ROVE_COACH_V4_VISIBLE_USER_IDS": "999",
        }, clear=False):
            self.assertIsNone(get_visible_coach_v4(self.conn, 7, now=self.NOW))
        self.assertEqual(get_visible_pilot_metrics()["skipped"], {"not_allowlisted": 1})

    def test_stale_error_and_version_mismatch_fail_closed(self):
        for status, expected in (("stale", "stale"), ("error", "error")):
            reset_visible_pilot_metrics()
            self.snapshot(self.budget_payload(), status=status)
            self.assertIsNone(self.call(self.budget_payload()))
            self.assertEqual(get_visible_pilot_metrics()["skipped"], {expected: 1})
        reset_visible_pilot_metrics()
        self.snapshot(self.budget_payload(), snapshot_version=SNAPSHOT_VERSION + 1)
        self.assertIsNone(self.call(self.budget_payload()))
        self.assertEqual(get_visible_pilot_metrics()["skipped"], {"version_mismatch": 1})
        reset_visible_pilot_metrics()
        self.snapshot(self.budget_payload(), contract=BEHAVIOR_CONTRACT_VERSION + 1)
        self.assertIsNone(self.call(self.budget_payload()))
        self.assertEqual(get_visible_pilot_metrics()["skipped"], {"contract_mismatch": 1})

    def test_only_allowed_types_and_hard_suppressed_payloads_are_visible(self):
        self.snapshot(self.budget_payload(insight_type="spending_trend_worsening"))
        self.assertIsNone(self.call(self.budget_payload(insight_type="spending_trend_worsening")))
        self.snapshot(self.budget_payload(coach_suppression_reason="single_outlier"))
        self.assertIsNone(self.call(self.budget_payload(coach_suppression_reason="single_outlier")))
        self.assertEqual(get_visible_pilot_metrics()["delivered"], 0)

    def test_budget_requires_strong_overall_evidence(self):
        self.snapshot(self.budget_payload(overall_budget_status="healthy"))
        self.assertIsNone(self.call(self.budget_payload(overall_budget_status="healthy")))
        self.assertEqual(get_visible_pilot_metrics()["suppressed"], 1)

    def test_missing_visible_candidate_is_not_counted_as_suppressed(self):
        self.snapshot(None)
        self.assertIsNone(self.call(None))
        metrics = get_visible_pilot_metrics()
        self.assertEqual(metrics["suppressed"], 0)
        self.assertEqual(metrics["skipped"], {"no_visible_candidate": 1})

    def test_confirmed_improvement_is_rendered_without_internal_fields(self):
        self.snapshot(self.improvement_payload())
        result = self.call(self.improvement_payload())
        self.assertEqual(result["insight_type"], "spending_trend_improving")
        self.assertIn("gesunken", result["message"])
        self.assertNotIn("pattern", json.dumps(result).lower())
        self.assertNotIn("source", json.dumps(result).lower())
        self.assertNotIn("merchant", json.dumps(result).lower())

    def test_short_improvement_history_is_suppressed(self):
        payload = self.improvement_payload(
            monthly_amounts=[
                {"month": "2026-07", "amount": 140.0},
                {"month": "2026-08", "amount": 95.0},
            ]
        )
        self.snapshot(payload)
        self.assertIsNone(self.call(payload))
        self.assertEqual(get_visible_pilot_metrics()["suppressed"], 1)

    def test_visible_state_does_not_expose_ids_reasons_or_raw_labels(self):
        self.snapshot(self.budget_payload())
        result = self.call(self.budget_payload())
        serialized = json.dumps(result, ensure_ascii=False)
        for forbidden in ("pattern_id", "source_ids", "suppression_reason", "account_id", "merchant"):
            self.assertNotIn(forbidden, serialized)

    def test_metrics_are_bounded_and_snapshot_reads_do_not_count(self):
        self.snapshot(self.budget_payload())
        before = get_visible_pilot_metrics()
        from rove_behavior_snapshot import get_visible_behavior_snapshot

        self.assertIsNotNone(get_visible_behavior_snapshot(self.conn, 7, now=self.NOW))
        after = get_visible_pilot_metrics()
        self.assertEqual(before, after)
        self.assertNotIn("7", json.dumps(after))


if __name__ == "__main__":
    unittest.main()
