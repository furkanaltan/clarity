from __future__ import annotations

import copy
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from rove_financial_insights import (
    adapt_v3_mentor_candidate,
    adapt_v4_shadow_candidate,
    build_shadow_insight_path,
    build_v4_shadow_contracts,
    get_shadow_insight_metrics,
    record_shadow_insight_evaluation,
    reset_shadow_insight_metrics,
)
from rove_coach_v4_delivery import (
    answer_v4_question,
    build_visible_mentor_insight,
    resolve_v4_transaction_examples,
    v4_question_mode,
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
        self.assertEqual(first["evidence"]["metrics"]["source_transaction_count"], 1)
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

    def test_visible_mentor_join_keeps_shared_id_and_fails_closed_on_v3_or_fact_mismatch(self):
        contract = adapt_v4_shadow_candidate(
            self.v4_candidate(), generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )
        visible = {
            "insight_type": "spending_trend_improving",
            "category_display": "Restaurants",
            "period_start": "2026-07-01", "period_end": "2026-09-30",
            "title": "Deine Ausgaben entwickeln sich positiv",
            "message": "Bei Restaurants sind deine Ausgaben gesunken.",
            "monthly_amounts": [
                {"month": "2026-07", "amount": 120.0},
                {"month": "2026-08", "amount": 90.0},
                {"month": "2026-09", "amount": 70.0},
            ],
        }
        result = build_visible_mentor_insight(None, [contract], visible)
        self.assertEqual(result["insight_id"], contract["insight_id"])
        self.assertEqual(result["evidence"]["metrics"]["monthly_amounts"], visible["monthly_amounts"])
        self.assertNotIn("private merchant", str(result))
        self.assertIsNone(build_visible_mentor_insight(self.v3_candidate(), [contract], visible))
        visible["monthly_amounts"][0]["amount"] = 999
        self.assertIsNone(build_visible_mentor_insight(None, [contract], visible))

    @staticmethod
    def visible_v4():
        return {
            "insight_type": "spending_trend_improving",
            "category_display": "Restaurants",
            "period_start": "2026-07-01", "period_end": "2026-09-30",
            "title": "Deine Ausgaben entwickeln sich positiv",
            "message": "Bei Restaurants sind deine Ausgaben gesunken.",
            "monthly_amounts": [
                {"month": "2026-07", "amount": 120.0},
                {"month": "2026-08", "amount": 90.0},
                {"month": "2026-09", "amount": 70.0},
            ],
        }

    def test_score_fallback_yields_to_valid_v4_in_legacy_and_multi_account_profiles(self):
        from rove_app_state import build_mentor_candidate

        fallback = build_mentor_candidate(
            score={"value": 81, "tracking_days_90": 8},
            budget_truth={"free_month_remaining": 100, "category_remaining": 100},
            monthly_actions=[], goals=[], contracts=[], reports=[],
            income=3000, fixed_costs=1000, debt_status="none",
        )
        before = copy.deepcopy(fallback)
        self.assertEqual((fallback["id"], fallback["type"], fallback["priority"]),
                         ("motivation", "motivation", 10))
        contract = adapt_v4_shadow_candidate(
            self.v4_candidate(), generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )
        for profile_mode in ("legacy", "multi_account"):
            with self.subTest(profile_mode=profile_mode):
                path = build_shadow_insight_path(
                    fallback, [contract], snapshot_status="ready",
                    profile_mode=profile_mode, now=self.NOW,
                )
                joined = build_visible_mentor_insight(
                    fallback, path["v4_candidates"], self.visible_v4()
                )
                self.assertIsNotNone(joined)
                self.assertEqual(joined, build_visible_mentor_insight(
                    None, [contract], self.visible_v4()
                ))
        self.assertEqual(fallback, before)

    def test_concrete_and_unknown_v3_candidates_keep_priority_over_v4(self):
        contract = adapt_v4_shadow_candidate(
            self.v4_candidate(), generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )
        candidates = [
            self.v3_candidate(type=kind, priority=priority)
            for kind, priority in (
                ("budget_overrun", 100), ("consumer_debt", 95),
                ("liquidity", 90), ("monthly_action", 50), ("report", 30),
                ("unknown", 1),
            )
        ]
        candidates.extend([
            self.v3_candidate(id="motivation", type="motivation", priority=50),
            self.v3_candidate(id="concrete", type="motivation", priority=10),
        ])
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertIsNone(build_visible_mentor_insight(
                    candidate, [contract], self.visible_v4()
                ))

    def test_score_fallback_never_bypasses_missing_stale_or_conflicting_v4_evidence(self):
        fallback = self.v3_candidate(id="motivation", type="motivation", priority=10)
        contract = adapt_v4_shadow_candidate(
            self.v4_candidate(), generated_at=self.NOW.isoformat(),
            source_watermark=self.WATERMARK,
        )
        for status in ("snapshot_missing", "snapshot_stale", "source_watermark_mismatch"):
            with self.subTest(status=status):
                path = build_shadow_insight_path(
                    fallback, [contract], snapshot_status=status,
                    profile_mode="multi_account", now=self.NOW,
                )
                self.assertIsNone(build_visible_mentor_insight(
                    fallback, path["v4_candidates"], self.visible_v4()
                ))
        self.assertIsNone(build_visible_mentor_insight(fallback, [], self.visible_v4()))
        self.assertIsNone(build_visible_mentor_insight(fallback, [contract], None))
        conflicting = self.visible_v4()
        conflicting["monthly_amounts"][0]["amount"] = 999
        self.assertIsNone(build_visible_mentor_insight(fallback, [contract], conflicting))
        missing = copy.deepcopy(contract)
        missing["evidence"] = {}
        self.assertIsNone(build_visible_mentor_insight(fallback, [missing], self.visible_v4()))

    def test_context_questions_are_deterministic_and_claim_limited(self):
        self.assertEqual(v4_question_mode("Warum sagst du mir das?"), "why")
        self.assertEqual(v4_question_mode("Welche Ausgaben meinst du?"), "transactions")
        self.assertEqual(v4_question_mode("Was kann ich konkret ändern?"), "action")
        self.assertIsNone(v4_question_mode("Wie viel Bargeld habe ich?"))
        insight = {
            "insight_type": "spending_trend_improving",
            "facts": {
                "category": "Restaurants",
                "monthly_amounts": [
                    {"month": "2026-07", "amount": 120},
                    {"month": "2026-08", "amount": 90},
                    {"month": "2026-09", "amount": 70},
                ],
                "transaction_count": 8,
            },
        }
        answer = answer_v4_question("why", insight)
        self.assertIn("120,00 €", answer)
        self.assertIn("70,00 €", answer)
        self.assertIn("8 Buchungen", answer)
        self.assertIn("Ursache lässt sich daraus nicht ableiten", answer)
        answer = answer_v4_question("transactions", insight, {
            "count": 8,
            "examples": [{"date": "2026-09-04", "label": "Café", "amount": 4.5}],
        })
        self.assertIn("8 Buchungen", answer)
        self.assertIn("4,50 €", answer)

    def test_transaction_examples_resolve_exact_user_scoped_detector_sources(self):
        raw = self.v4_candidate(
            source_ids=["1", "2"],
            evidence_metrics={
                "months_compared": 3,
                "monthly_amounts": [
                    {"month": "2026-07", "amount": 120.0},
                    {"month": "2026-08", "amount": 90.0},
                    {"month": "2026-09", "amount": 70.0},
                ],
            },
        )
        contract = adapt_v4_shadow_candidate(
            raw, generated_at=self.NOW.isoformat(), source_watermark=self.WATERMARK,
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE expenses (
            id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, category TEXT,
            merchant TEXT, transaction_at TEXT, created_at TEXT
        )""")
        conn.executemany(
            "INSERT INTO expenses VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (1, 7, 4.5, "Essen", "Café", "2026-08-03 12:00:00", "2026-08-03 12:01:00"),
                (2, 7, 20.0, "Essen", "Markt", "2026-09-04 10:00:00", "2026-09-04 10:01:00"),
                (3, 8, 900.0, "Essen", "Fremdes Konto", "2026-09-04 10:00:00", "2026-09-04 10:01:00"),
            ),
        )
        conn.commit()
        inspector = {"insight_candidates": [raw]}
        with patch("rove_coach_v4_delivery.compute_behavior_source_watermark", return_value=self.WATERMARK), \
             patch("rove_coach_v4_delivery.build_shadow_inspector", return_value=inspector):
            result = resolve_v4_transaction_examples(conn, 7, contract)
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["label"] for item in result["examples"]], ["Café", "Markt"])
        self.assertNotIn("Fremdes Konto", str(result))
        conn.close()

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
