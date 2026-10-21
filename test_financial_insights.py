from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from rove_financial_insights import (
    adapt_v3_mentor_candidate,
    adapt_v4_shadow_candidate,
    build_shadow_insight_path,
    build_v4_shadow_contracts,
    get_shadow_insight_metrics,
    record_shadow_insight_evaluation,
    reset_shadow_insight_metrics,
)


class FinancialInsightContractTests(unittest.TestCase):
    NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    WATERMARK = '[{"table":"expenses","count":3}]'

    @staticmethod
    def v3_candidate(**overrides):
        candidate = {
            "id": "budget-overrun",
            "priority": 100,
            "type": "budget_overrun",
            "title": "Budget prüfen",
            "message": "Bestehende V3-Aussage",
            "action_label": "Budget öffnen",
            "deep_link": "analysis",
            "reason": "negative_or_overrun_budget",
        }
        candidate.update(overrides)
        return candidate

    @staticmethod
    def v4_candidate(**overrides):
        insight = {
            "insight_id": "stable-insight-7",
            "insight_type": "spending_trend_improving",
            "coach_eligible": True,
            "primary_coach_insight": True,
            "coach_suppression_reason": None,
            "conflicting_evidence": False,
            "superseded_by_pattern_id": None,
            "pattern_strength": "high",
            "financial_relevance": "high",
            "confidence_class": "high",
            "period_start": "2026-07-01",
            "period_end": "2026-09-30",
            "source_pattern_ids": ["pattern-7"],
            "source_ids": ["raw-expense-1"],
            "evidence_summary": {
                "pattern_type": "category_spending_improving",
                "category": "restaurant",
                "merchant": "private merchant",
            },
            "evidence_metrics": {
                "months_compared": 3,
                "transaction_id": "transaction-7",
                "monthly_amounts": [
                    {"month": "2026-07", "amount": 120.0},
                    {"month": "2026-08", "amount": 90.0},
                    {"month": "2026-09", "amount": 70.0},
                ],
            },
        }
        insight.update(overrides)
        return insight

    def setUp(self):
        reset_shadow_insight_metrics()

    def test_v3_adapter_preserves_identity_priority_copy_and_input(self):
        source = self.v3_candidate()
        before = copy.deepcopy(source)
        result = adapt_v3_mentor_candidate(source, now=self.NOW)
        self.assertEqual(source, before)
        self.assertEqual(result["insight_id"], "v3:budget-overrun")
        self.assertEqual(result["insight_type"], "budget_attention")
        self.assertEqual(result["priority"], {
            "value": 100, "basis": "existing_v3_priority"
        })
        self.assertEqual(result["explanation"]["text"], "Bestehende V3-Aussage")

    def test_v4_contract_is_stable_bounded_and_excludes_raw_identifiers(self):
        source = self.v4_candidate()
        before = copy.deepcopy(source)
        first = adapt_v4_shadow_candidate(
            source, generated_at=self.NOW.isoformat(), source_watermark=self.WATERMARK
        )
        second = adapt_v4_shadow_candidate(
            source, generated_at=self.NOW.isoformat(), source_watermark=self.WATERMARK
        )
        self.assertEqual(source, before)
        self.assertEqual(first, second)
        self.assertEqual(first["visibility"], "shadow_only")
        self.assertEqual(first["period"]["start"], "2026-07-01")
        self.assertEqual(first["priority"]["basis"], "existing_v4_primary_coach_arbitration")
        self.assertIn("descriptive_only", first["claim_limits"])
        self.assertEqual(first["evidence"]["source_pattern_ids"], ["pattern-7"])
        rendered = str(first)
        self.assertNotIn("raw-expense-1", rendered)
        self.assertNotIn("transaction-7", rendered)
        self.assertNotIn("private merchant", rendered)

    def test_v4_rejects_missing_conflicting_suppressed_or_weak_evidence(self):
        cases = (
            {"coach_eligible": False},
            {"primary_coach_insight": False},
            {"conflicting_evidence": True},
            {"coach_suppression_reason": "single_outlier"},
            {"confidence_class": "unknown"},
            {"evidence_metrics": {}},
            {"source_pattern_ids": []},
            {"pattern_strength": "low"},
        )
        for change in cases:
            with self.subTest(change=change):
                insight = self.v4_candidate(**change)
                self.assertIsNone(adapt_v4_shadow_candidate(
                    insight,
                    generated_at=self.NOW.isoformat(),
                    source_watermark=self.WATERMARK,
                ))

    def test_post_income_requires_and_carries_correlation_limit(self):
        insight = self.v4_candidate(
            insight_type="post_income_pattern",
            evidence_metrics={"salary_cycles": 3},
        )
        self.assertIsNone(adapt_v4_shadow_candidate(
            insight,
            generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        ))
        insight["evidence_metrics"]["temporal_correlation_only"] = True
        contract = adapt_v4_shadow_candidate(
            insight,
            generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )
        self.assertIn("temporal_correlation_only", contract["claim_limits"])

    def test_one_primary_only_and_v3_budget_deduplication(self):
        generated = self.NOW.isoformat()
        v4_budget = self.v4_candidate(insight_type="budget_attention")
        contracts = build_v4_shadow_contracts(
            [v4_budget], generated_at=generated, source_watermark=self.WATERMARK
        )
        path = build_shadow_insight_path(
            self.v3_candidate(), contracts, snapshot_status="ready",
            profile_mode="legacy", now=self.NOW,
        )
        self.assertEqual(path["active_v3_candidate"]["priority"]["value"], 100)
        self.assertEqual(path["v4_candidates"], [])
        self.assertEqual(path["v4_deduplicated_against_v3"], 1)
        self.assertEqual(path["mode"], "shadow")
        self.assertEqual(path["profile_mode"], "legacy")

        two_primaries = [v4_budget, self.v4_candidate(insight_id="other")]
        self.assertEqual(build_v4_shadow_contracts(
            two_primaries, generated_at=generated, source_watermark=self.WATERMARK
        ), [])

    def test_stale_or_missing_snapshot_never_passes_v4_candidate(self):
        contract = build_v4_shadow_contracts(
            [self.v4_candidate()], generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )
        for status in ("snapshot_missing", "snapshot_stale", "source_watermark_mismatch"):
            path = build_shadow_insight_path(
                None, contract, snapshot_status=status, profile_mode="multi_account",
                now=self.NOW,
            )
            self.assertEqual(path["v4_candidates"], [])
            self.assertEqual(path["profile_mode"], "multi_account")

    def test_incomplete_period_and_inconsistent_dedupe_are_rejected(self):
        insight = self.v4_candidate(period_end=None)
        self.assertIsNone(adapt_v4_shadow_candidate(
            insight,
            generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        ))

        contract = build_v4_shadow_contracts(
            [self.v4_candidate()], generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )[0]
        contract["dedupe_key"] = "coach:unrelated_type"
        path = build_shadow_insight_path(
            None, [contract], snapshot_status="ready", profile_mode="legacy",
            now=self.NOW,
        )
        self.assertEqual(path["v4_candidates"], [])

    def test_shadow_metrics_are_aggregate_and_profile_scoped(self):
        path = build_shadow_insight_path(
            self.v3_candidate(), [], snapshot_status="no_candidate",
            profile_mode="multi_account", now=self.NOW,
        )
        record_shadow_insight_evaluation(path)
        metrics = get_shadow_insight_metrics()
        self.assertEqual(metrics["scope"], "process_local")
        self.assertEqual(metrics["counts"]["evaluations"], 1)
        self.assertEqual(metrics["counts"]["profile:multi_account"], 1)
        self.assertNotIn("user_id", str(metrics))


if __name__ == "__main__":
    unittest.main()
