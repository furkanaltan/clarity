"""Scenario regressions through the real V4 engine, snapshot and delivery path."""

from __future__ import annotations

import hashlib
import copy
import json
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_coach_v4 as fixtures
from rove_behavior_patterns import build_shadow_inspector, build_visible_behavior_insight
from rove_behavior_snapshot import (
    ensure_behavior_snapshot_table,
    get_shadow_insight_contracts,
    recompute_behavior_snapshot,
)
from rove_coach_v4_delivery import (
    answer_v4_question,
    build_visible_mentor_insight,
    resolve_v4_transaction_examples,
    v4_question_mode,
)
from rove_visible_coach_v4 import get_visible_coach_v4


class CoachingQualityScenarios(unittest.TestCase):
    def setUp(self):
        self.profile = fixtures.CoachV4ShadowPatternTests(methodName="runTest")
        self.profile.setUp()
        self.addCleanup(self.profile.tearDown)
        self.conn = self.profile.conn
        self.now = self.profile.NOW.replace(tzinfo=timezone.utc)

    def financial_fingerprint(self):
        rows = {
            table: [tuple(row) for row in self.conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in ("users", "expenses", "app_cash_movements", "category_budgets", "app_contracts")
        }
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()

    def gated_visible(self, *, now=None):
        with patch.dict(os.environ, {
            "ROVE_COACH_V4_VISIBLE_ENABLED": "1",
            "ROVE_COACH_V4_VISIBLE_USER_IDS": "1",
        }):
            return get_visible_coach_v4(self.conn, 1, now=now or self.now)

    def pipeline(self, v3=None, now=None):
        moment = now or self.now
        engine_now = moment.replace(tzinfo=None)
        self.conn.commit()
        before = self.financial_fingerprint()
        inspector = build_shadow_inspector(self.conn, 1, now=engine_now)
        ensure_behavior_snapshot_table(self.conn)
        # Freeze the snapshot clock as well as the detector's business date.
        with patch("rove_behavior_snapshot._now_text", return_value=moment.isoformat()):
            outcome = recompute_behavior_snapshot(self.conn, 1, now=moment)
        self.assertEqual(outcome["status"], "ready", outcome)
        contracts, status = get_shadow_insight_contracts(self.conn, 1, now=moment)
        visible = self.gated_visible(now=moment)
        joined = build_visible_mentor_insight(v3, contracts, visible)
        self.assertEqual(self.financial_fingerprint(), before)
        return inspector, contracts, status, visible, joined

    def add_current_pressure(self, category="Restaurants"):
        self.profile.configure_budget_plan(income=1500, fixed_costs=1200)
        self.profile.add_budget(category, "2026-09", 100)
        for index in range(1, 5):
            self.profile.add_expense(
                100 + index, f"Ausgabe {index}", category, 90,
                f"2026-09-{index + 1:02d} 12:00:00",
            )

    def add_improvement(self):
        self.profile._add_historical_months(((90, 90), (70, 70), (45, 45)))

    def assert_v3_precedence(self, now=None):
        _inspector, _contracts, _status, visible, joined = self.pipeline(
            v3={"type": "budget_overrun", "priority": 900}, now=now
        )
        self.assertIsNotNone(visible)
        self.assertIsNone(joined)

    def test_delivery_weekday_pattern_has_exact_dates_count_and_amount(self):
        for index, (day, amount) in enumerate(((7, 30), (14, 28), (21, 28)), 1):
            self.profile.add_expense(index, "Lieferando", "Restaurants", amount,
                                     f"2026-09-{day:02d} 19:00:00")
        inspector, contracts, status, visible, joined = self.pipeline()
        selected = next(item for item in inspector["insight_candidates"] if item["primary_coach_insight"])
        self.assertEqual(selected["insight_type"], "repeated_spending_pattern")
        self.assertEqual(selected["evidence_metrics"]["weekday"], 0)
        self.assertEqual(selected["evidence_metrics"]["occurrence_date_count"], 3)
        self.assertEqual(selected["evidence_metrics"]["amount_total"], 86)
        self.assertEqual((selected["period_start"], selected["period_end"]), ("2026-09-01", "2026-09-30"))
        self.assertEqual(status, "ready")
        self.assertEqual(contracts[0]["insight_id"], "v4:" + selected["insight_id"])
        self.assertIsNotNone(visible)
        self.assertIsNotNone(joined)
        self.assertEqual(joined["insight_id"], contracts[0]["insight_id"])
        self.assertEqual(joined["facts"]["transaction_count"], 3)
        self.assertEqual(joined["facts"]["merchant"], "Lieferando")
        self.assertEqual(joined["facts"]["amount_current"], 86)
        self.assertIn("3 von 4 Montagen", visible["message"])
        answer = answer_v4_question("why", joined)
        self.assertIn("86,00 €", answer)
        self.assertIn("Lieferando", answer)
        examples = resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW)
        self.assertEqual(examples["count"], 3)
        self.assertEqual(answer_v4_question("transactions", joined, examples).count("Lieferando"), 3)
        self.assert_v3_precedence()
        self.profile.conn.execute("UPDATE expenses SET amount=31 WHERE id=1")
        self.assertIsNone(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW))
        self.assertEqual(inspector["primary_coach_insight"], self.pipeline()[0]["primary_coach_insight"])

    def test_one_day_with_three_orders_is_not_a_weekday_habit(self):
        for index in range(1, 4):
            self.profile.add_expense(index, "Lieferando", "Restaurants", 30, "2026-09-07 19:00:00")
        inspector, *_ = self.pipeline()
        self.assertFalse(any(item["pattern_type"] == "merchant_weekday_pattern" for item in inspector["patterns"]))

    def test_increasing_category_preserves_all_three_months(self):
        self.profile._add_historical_months(((45, 45), (70, 70), (100, 100)))
        inspector, contracts, status, visible, joined = self.pipeline()
        selected = next(item for item in inspector["insight_candidates"] if item["primary_coach_insight"])
        self.assertEqual(selected["insight_type"], "spending_trend_worsening")
        values = selected["evidence_metrics"]["monthly_amounts"]
        self.assertEqual([(item["month"], item["amount"]) for item in values],
                         [("2026-06", 90), ("2026-07", 140), ("2026-08", 200)])
        self.assertEqual(selected["evidence_metrics"]["absolute_change_eur"], 110)
        self.assertEqual(len(selected["source_ids"]), 6)
        self.assertEqual(status, "ready")
        self.assertEqual(len(contracts), 1)
        self.assertIsNotNone(visible)
        self.assertIsNotNone(joined)
        self.assertEqual(joined["facts"]["monthly_amounts"],
                         [{"month": row["month"], "amount": row["amount"]} for row in values])
        self.assertEqual(joined["facts"]["transaction_count"], 6)
        answer = answer_v4_question("why", joined)
        for fact in ("90,00 €", "140,00 €", "200,00 €", "6 Buchungen"):
            self.assertIn(fact, answer)
        self.assertIn("Ursache", answer)
        self.assert_v3_precedence()
        inconsistent = copy.deepcopy(selected)
        inconsistent["evidence_metrics"]["absolute_change_eur"] = 111
        self.assertIsNone(build_visible_behavior_insight(inconsistent))
        self.profile.conn.execute("UPDATE expenses SET amount=999 WHERE id=1")
        self.assertIsNone(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW))

    def test_post_salary_pattern_needs_three_reliable_cycles_and_has_no_causal_claim(self):
        self.profile._salary_cycle_fixture()
        inspector, contracts, status, visible, _ = self.pipeline()
        selected = next(item for item in inspector["insight_candidates"] if item["primary_coach_insight"])
        self.assertEqual(selected["insight_type"], "post_income_pattern")
        metrics = selected["evidence_metrics"]
        self.assertEqual(metrics["cycles_used"], 3)
        self.assertEqual(metrics["post_income_average_eur"], 200)
        self.assertTrue(metrics["temporal_correlation_only"])
        self.assertEqual(status, "ready")
        self.assertIn("no_causal_claims", contracts[0]["claim_limits"])
        self.assertIn("temporal_correlation_only", contracts[0]["claim_limits"])
        self.assertIsNotNone(visible)
        self.assertEqual(visible["insight_type"], "post_income_pattern")
        _, _, _, _, joined = self.pipeline()
        self.assertIsNotNone(joined)
        answer = answer_v4_question("why", joined)
        for fact in ("3 Gehaltszyklen", "200,00 €", "40,00 €", "160,00 €", "6 Buchungen"):
            self.assertIn(fact, answer)
        self.assertIn("kein Beleg für eine Ursache", answer)
        examples = resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW)
        self.assertEqual(examples["count"], 6)
        self.assert_v3_precedence()
        inconsistent = copy.deepcopy(selected)
        inconsistent["evidence_metrics"]["absolute_delta_eur"] = 161
        self.assertIsNone(build_visible_behavior_insight(inconsistent))
        self.profile.conn.execute("UPDATE expenses SET amount=201 WHERE id=101")
        self.assertIsNone(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW))
        self.conn.execute("DELETE FROM app_cash_movements WHERE id=3")
        two_cycles = build_shadow_inspector(self.conn, 1, now=self.profile.NOW)
        self.assertFalse(any(item["insight_type"] == "post_income_pattern" and item["coach_eligible"]
                             for item in two_cycles["insight_candidates"]))

    def test_recurring_services_do_not_sum_duplicate_contract_rows(self):
        for key, name in (("c1", "Netflix"), ("c2", "NETFLIX.COM"),
                          ("c3", "Disney+"), ("c4", "Paramount+")):
            self.profile.add_contract(key, name, 25)
        inspector, contracts, _, visible, joined = self.pipeline()
        pattern = next(item for item in inspector["patterns"] if item["pattern_type"] == "similar_recurring_services")
        self.assertEqual(pattern["observations"]["service_count"], 3)
        self.assertEqual(pattern["observations"]["monthly_normalized_total"], 75)
        self.assertTrue(pattern["observations"]["duplicate_ambiguous"])
        self.assertEqual(visible["insight_type"], "subscription_cluster")
        self.assertEqual(joined["facts"]["service_count"], 3)
        self.assertEqual(joined["facts"]["monthly_normalized_total"], 75)
        answer = answer_v4_question("why", joined)
        self.assertIn("75,00 €", answer)
        self.assertIn("belegt weder", answer)
        self.assertIn("kündigt nichts", answer_v4_question("action", joined))
        self.assertEqual(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW)["count"], 4)
        self.assertEqual(v4_question_mode("Welche Verträge meinst du?"), "transactions")
        self.assert_v3_precedence()
        self.conn.execute("UPDATE app_contracts SET amount=35 WHERE contract_id='c2'")
        self.assertIsNone(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW))
        ambiguous = build_shadow_inspector(self.conn, 1, now=self.profile.NOW)
        pattern = next(item for item in ambiguous["patterns"] if item["pattern_type"] == "similar_recurring_services")
        self.assertIsNone(pattern["observations"]["monthly_normalized_total"])
        self.assertFalse(pattern["eligible_for_coach"])

    def test_budget_recovery_requires_closed_month_and_explains_historical_rows(self):
        self.profile.configure_budget_plan(income=1500, fixed_costs=1200)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08")):
            self.profile.add_budget("Restaurants", month, 100)
            self.profile.add_month(index * 10 + 1, month, (80, 80))
        self.profile.add_budget("Restaurants", "2026-09", 100)
        self.profile.add_expense(100, "Restaurant", "Restaurants", 20, "2026-09-05 12:00:00")
        self.profile.add_expense(101, "Restaurant", "Restaurants", 20, "2026-09-10 12:00:00")

        before_close = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        inspector, contracts, _, visible, _ = self.pipeline(now=before_close)
        self.assertFalse(any(item["insight_type"] == "budget_recovery" and item["coach_eligible"]
                             for item in inspector["insight_candidates"]))
        self.assertEqual(contracts, [])
        self.assertIsNone(visible)

        closed_month = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
        inspector, contracts, status, visible, joined = self.pipeline(now=closed_month)
        selected = next(item for item in inspector["insight_candidates"]
                        if item["insight_type"] == "budget_recovery" and item["primary_coach_insight"])
        self.assertEqual(status, "ready")
        self.assertEqual(len(contracts), 1)
        self.assertEqual(visible["insight_type"], "budget_recovery")
        self.assertEqual(joined["insight_id"], contracts[0]["insight_id"])
        self.assertEqual(joined["facts"]["amount_current"], 40)
        self.assertEqual(joined["facts"]["budget_amount"], 100)
        self.assertEqual(joined["facts"]["months_over_budget"], 3)
        self.assertEqual(len(joined["facts"]["historical_budget_evidence"]), 3)
        why = answer_v4_question("why", joined)
        for fact in ("40,00 €", "100,00 €", "Juni 2026", "Juli 2026", "August 2026", "Ursache"):
            self.assertIn(fact, why)
        examples = resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=closed_month)
        self.assertEqual(examples["count"], 2)
        self.assertIn("2 Buchungen", answer_v4_question("transactions", joined, examples))
        self.assertIn("beibehalten", answer_v4_question("action", joined))
        self.assert_v3_precedence(now=closed_month)
        self.profile.conn.execute("UPDATE expenses SET amount=90 WHERE id=100")
        self.assertIsNone(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=closed_month))

    def test_budget_recovery_is_suppressed_after_material_budget_change(self):
        self.profile.configure_budget_plan(income=1500, fixed_costs=1200)
        for index, month in enumerate(("2026-06", "2026-07", "2026-08")):
            self.profile.add_budget("Restaurants", month, 100)
            self.profile.add_month(index * 10 + 1, month, (80, 80))
        self.profile.add_budget("Restaurants", "2026-09", 200)
        self.profile.add_expense(100, "Restaurant", "Restaurants", 20, "2026-09-05 12:00:00")
        self.profile.add_expense(101, "Restaurant", "Restaurants", 20, "2026-09-10 12:00:00")
        closed_month = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
        inspector, contracts, _, visible, _ = self.pipeline(now=closed_month)
        self.assertFalse(any(item["insight_type"] == "budget_recovery" and item["coach_eligible"]
                             for item in inspector["insight_candidates"]))
        self.assertEqual(contracts, [])
        self.assertIsNone(visible)

    def test_current_budget_pressure_keeps_complete_shared_evidence(self):
        self.add_current_pressure()
        inspector, contracts, status, visible, joined = self.pipeline()
        self.assertEqual(status, "ready")
        self.assertIsNotNone(visible, "real engine payload must survive the existing visible gate")
        self.assertIsNotNone(joined, "validated budget status must survive the DTO join")
        self.assertEqual(joined["insight_id"], "v4:" + inspector["primary_coach_insight"])
        self.assertEqual(joined["facts"]["amount_current"], 360)
        self.assertEqual(joined["facts"]["budget_amount"], 100)
        self.assertEqual(joined["facts"]["amount_over_budget"], 260)
        self.assertEqual(joined["facts"]["transaction_count"], 4)
        examples = resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW)
        self.assertEqual(examples["count"], 4)
        self.assertTrue(all(item["amount"] == 90 for item in examples["examples"]))
        answer = answer_v4_question("why", joined)
        for fact in ("360,00 €", "100,00 €", "260,00 €", "4 Buchungen"):
            self.assertIn(fact, answer)
        self.assertIn("01.09.2026 bis 30.09.2026", answer)

    def test_strong_positive_history_is_visible_and_explained_without_causality(self):
        self.add_improvement()
        inspector, contracts, status, visible, joined = self.pipeline()
        selected = next(item for item in inspector["insight_candidates"] if item["primary_coach_insight"])
        self.assertEqual(selected["pattern_strength"], "high")
        self.assertEqual(selected["confidence_class"], "medium")
        self.assertEqual(status, "ready")
        self.assertIsNotNone(visible, "low urgency must not hide a strongly evidenced positive trend")
        self.assertIsNotNone(joined)
        self.assertEqual(joined["confidence"], "medium")
        self.assertEqual(joined["facts"]["transaction_count"], 6)
        answer = answer_v4_question("why", joined)
        for fact in ("180,00 €", "90,00 €", "6 Buchungen"):
            self.assertIn(fact, answer)
        self.assertIn("Juni 2026", answer)
        self.assertIn("August 2026", answer)
        self.assertIn("Ursache", answer)
        examples = resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW)
        self.assertEqual(examples["count"], 6)
        self.assertEqual(len(examples["examples"]), 5)
        detailed = answer_v4_question("transactions", joined, examples)
        self.assertIn("6 Buchungen", detailed)
        self.assertIn("05.06.2026", detailed)

    def test_medium_strength_or_missing_history_does_not_pass_positive_gate(self):
        self.profile._add_historical_months(((90, 90), (80, 80), (60, 60)))
        _, _, _, visible, joined = self.pipeline()
        self.assertIsNone(visible)
        self.assertIsNone(joined)
        self.conn.execute("DELETE FROM expenses WHERE transaction_at LIKE '2026-07%'")
        incomplete, contracts, _, visible, _ = self.pipeline()
        self.assertFalse(any(item["insight_type"] == "spending_trend_improving" for item in incomplete["insight_candidates"]))
        self.assertEqual(contracts, [])
        self.assertIsNone(visible)

    def test_sparse_profile_does_not_produce_behavioral_coaching(self):
        self.profile.add_expense(1, "Lieferando", "Restaurants", 30, "2026-09-07 19:00:00")
        inspector, contracts, _, visible, joined = self.pipeline()
        self.assertEqual(inspector["insight_candidates"], [])
        self.assertEqual(contracts, [])
        self.assertIsNone(visible)
        self.assertIsNone(joined)

    def test_conflicting_directions_are_suppressed(self):
        for month, amounts in zip(("2026-06", "2026-07", "2026-08", "2026-09"),
                                  ((60, 60), (80, 80), (100, 100), (40, 40))):
            self.profile.add_budget("Restaurants", month)
            self.profile.add_month(int(month[-2:]) * 10, month, amounts)
        inspector, contracts, _, visible, _ = self.pipeline()
        self.assertTrue(any(item["coach_suppression_reason"] == "conflicting_evidence"
                            for item in inspector["insight_candidates"]))
        self.assertFalse(any(item["primary_coach_insight"] for item in inspector["insight_candidates"]))
        self.assertEqual(contracts, [])
        self.assertIsNone(visible)

    def test_stale_or_changed_evidence_cannot_be_delivered_or_resolved(self):
        self.add_improvement()
        _, contracts, status, visible, _ = self.pipeline()
        self.assertEqual(status, "ready")
        stale_time = self.now + timedelta(days=2)
        self.assertEqual(get_shadow_insight_contracts(self.conn, 1, now=stale_time)[0], [])
        self.assertIsNone(self.gated_visible(now=stale_time))
        self.conn.execute("UPDATE expenses SET amount=999 WHERE id=1")
        self.assertEqual(get_shadow_insight_contracts(self.conn, 1, now=self.now)[0], [])
        self.assertIsNone(build_visible_mentor_insight(None, [], visible))
        self.assertIsNone(resolve_v4_transaction_examples(self.conn, 1, contracts[0], now=self.profile.NOW))

    def test_visible_v4_gate_remains_off_without_server_configuration(self):
        self.add_current_pressure()
        self.pipeline()
        with patch.dict(os.environ, {
            "ROVE_COACH_V4_VISIBLE_ENABLED": "",
            "ROVE_COACH_V4_VISIBLE_USER_IDS": "",
        }):
            self.assertIsNone(get_visible_coach_v4(self.conn, 1, now=self.now))

    def test_inconsistent_legacy_snapshot_totals_are_rejected(self):
        self.add_current_pressure()
        self.pipeline()
        payload = json.loads(self.conn.execute(
            "SELECT visible_coach_payload_json FROM app_behavior_snapshot WHERE user_id=1"
        ).fetchone()[0])
        payload.pop("coach_timing_hint", None)
        payload["amount_over_budget"] = 999
        self.conn.execute("UPDATE app_behavior_snapshot SET visible_coach_payload_json=? WHERE user_id=1",
                          (json.dumps(payload),))
        self.assertIsNone(self.gated_visible())

    def test_existing_engine_winner_survives_multiple_independent_insights(self):
        self.add_improvement()
        self.add_current_pressure(category="Shopping")
        inspector, contracts, status, visible, joined = self.pipeline()
        primaries = [item for item in inspector["insight_candidates"] if item["primary_coach_insight"]]
        self.assertGreaterEqual(len(primaries), 2)
        selected = next(item for item in primaries if item["insight_id"] == inspector["primary_coach_insight"])
        self.assertEqual(selected["insight_type"], "budget_attention")
        self.assertEqual(status, "ready")
        self.assertEqual(len(contracts), 1)
        self.assertEqual(contracts[0]["insight_id"], "v4:" + selected["insight_id"])
        self.assertEqual(visible["insight_type"], selected["insight_type"])
        self.assertEqual(joined["insight_id"], contracts[0]["insight_id"])
        self.assertIsNone(build_visible_mentor_insight({"type": "budget_overrun", "priority": 100}, contracts, visible))

    def test_real_chat_api_uses_current_user_scoped_engine_evidence(self):
        import test_ai_chat_phase1 as api_fixtures
        import rove_app_api as api

        fixture = api_fixtures.AiChatPhaseOneTests(methodName="runTest")
        fixture.setUp()
        try:
            now = datetime.now()
            with closing(sqlite3.connect(fixture.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                expense_id = 1
                for offset, amount in zip((3, 2, 1), (90, 70, 45)):
                    year, month = divmod(now.year * 12 + now.month - 1 - offset, 12)
                    for day in (5, 6):
                        occurred = f"{year:04d}-{month + 1:02d}-{day:02d} 12:00:00"
                        conn.execute("""INSERT INTO expenses
                            (id,user_id,amount,category,merchant,transaction_date,created_at)
                            VALUES (?,1,?,'Restaurants','Restaurant',?,?)""",
                                     (expense_id, amount, occurred, occurred))
                        expense_id += 1
                conn.execute("""INSERT INTO expenses
                    (id,user_id,amount,category,merchant,transaction_date,created_at)
                    VALUES (999,2,999,'Restaurants','Fremdes Konto',?,?)""", (occurred, occurred))
                self.assertEqual(recompute_behavior_snapshot(conn, 1, now=now)["status"], "ready")
                contracts, status = get_shadow_insight_contracts(conn, 1)
                self.assertEqual(status, "ready")
                insight_id = contracts[0]["insight_id"]
                conn.commit()
            client = fixture.client_for(token="quality-real-evidence")
            other = fixture.client_for(user_id=2, token="quality-other-user")
            with patch.dict(os.environ, {"ROVE_COACH_V4_VISIBLE_ENABLED": "1",
                                         "ROVE_COACH_V4_VISIBLE_USER_IDS": "1"}), \
                 patch.object(api, "ai_chat_provider", side_effect=AssertionError("evidence must not reach AI")):
                for question in ("Warum sagst du mir das?", "Welche Ausgaben meinst du?",
                                 "Was kann ich konkret ändern?"):
                    response = fixture.post(client, question, insight_id=insight_id)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    payload = response.get_json()
                    self.assertTrue(payload["deterministic"])
                    self.assertEqual(payload["insight_id"], insight_id)
                    self.assertNotIn("Fremdes Konto", payload["answer"])
                    self.assertNotIn("999,00", payload["answer"])
                    if question.startswith("Welche"):
                        self.assertIn("6 Buchungen", payload["answer"])
                        self.assertIn("45,00 €", payload["answer"])
                self.assertEqual(fixture.post(other, "Warum sagst du mir das?", insight_id=insight_id).status_code, 409)
                with patch.dict(os.environ, {"ROVE_COACH_V4_VISIBLE_ENABLED": "0"}):
                    self.assertEqual(fixture.post(client, "Warum sagst du mir das?", insight_id=insight_id).status_code, 409)
                with closing(sqlite3.connect(fixture.db_path)) as conn:
                    conn.execute("UPDATE app_behavior_snapshot SET status='stale' WHERE user_id=1")
                    conn.commit()
                self.assertEqual(fixture.post(client, "Warum sagst du mir das?", insight_id=insight_id).status_code, 409)
        finally:
            fixture.tearDown()

    def test_real_chat_api_explains_subscription_contracts_without_ai(self):
        import test_ai_chat_phase1 as api_fixtures
        import rove_app_api as api

        fixture = api_fixtures.AiChatPhaseOneTests(methodName="runTest")
        fixture.setUp()
        try:
            now = datetime.now(timezone.utc)
            with closing(sqlite3.connect(fixture.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("""CREATE TABLE app_contracts (
                    user_id INTEGER NOT NULL, contract_id TEXT NOT NULL, name TEXT NOT NULL,
                    category TEXT NOT NULL, amount REAL NOT NULL, frequency TEXT,
                    status TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT, updated_at TEXT,
                    PRIMARY KEY(user_id, contract_id)
                )""")
                for contract_id, name in (
                    ("n1", "Netflix"), ("n2", "NETFLIX.COM"),
                    ("d1", "Disney+"), ("p1", "Paramount+"),
                ):
                    conn.execute(
                        "INSERT INTO app_contracts VALUES (1,?,?, 'Abos',25,'monthly','active',1,?,?)",
                        (contract_id, name, now.isoformat(), now.isoformat()),
                    )
                self.assertEqual(recompute_behavior_snapshot(conn, 1, now=now)["status"], "ready")
                contracts, status = get_shadow_insight_contracts(conn, 1, now=now)
                self.assertEqual(status, "ready")
                self.assertEqual(contracts[0]["insight_type"], "subscription_cluster")
                insight_id = contracts[0]["insight_id"]
                conn.commit()
            client = fixture.client_for(token="quality-subscription")
            with patch.dict(os.environ, {"ROVE_COACH_V4_VISIBLE_ENABLED": "1",
                                         "ROVE_COACH_V4_VISIBLE_USER_IDS": "1"}), \
                 patch.object(api, "ai_chat_provider", side_effect=AssertionError("must remain deterministic")):
                why = fixture.post(client, "Warum sagst du mir das?", insight_id=insight_id)
                self.assertEqual(why.status_code, 200, why.get_json())
                self.assertTrue(why.get_json()["deterministic"])
                self.assertIn("3 aktive Services", why.get_json()["answer"])
                self.assertIn("75,00 €", why.get_json()["answer"])
                self.assertIn("belegt weder", why.get_json()["answer"])
                which = fixture.post(client, "Welche Verträge meinst du?", insight_id=insight_id)
                self.assertEqual(which.status_code, 200, which.get_json())
                self.assertIn("4 aktive Vertragszeilen", which.get_json()["answer"])
                self.assertIn("NETFLIX.COM", which.get_json()["answer"])
                self.assertIn("25,00 € pro Monat", which.get_json()["answer"])
                action = fixture.post(client, "Was kann ich konkret ändern?", insight_id=insight_id)
                self.assertEqual(action.status_code, 200, action.get_json())
                self.assertIn("kündigt nichts", action.get_json()["answer"])
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
