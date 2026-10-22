from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import (
    acknowledge_coach_insight_display,
    ensure_app_coach_insight_memory_table,
    get_opened_coach_insight_context,
    mark_coach_insight_opened,
    reserve_coach_insight_display,
)


def claim_coach_insight_shown(conn, user_id, insight, contract):
    """Test helper for the former atomic shown claim: reserve and then acknowledge."""
    result = reserve_coach_insight_display(conn, user_id, insight, contract, "a" * 32)
    return result.get("status") == "reserved" and acknowledge_coach_insight_display(
        conn, user_id, insight["insight_id"], insight["evidence_fingerprint"], "a" * 32
    )


class CoachInsightMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript("""
                CREATE TABLE users(user_id INTEGER PRIMARY KEY);
                INSERT INTO users(user_id) VALUES (1), (2);
            """)
            ensure_app_coach_insight_memory_table(conn)
            conn.commit()

    @staticmethod
    def insight(insight_id="v4:synthetic", fingerprint="a" * 64, **facts):
        return {
            "insight_id": insight_id,
            "dedupe_key": "coach:budget_attention",
            "evidence_fingerprint": fingerprint,
            "insight_type": "budget_attention",
            "title": "Budget im Blick",
            "message": "Dein Budget ist unter Druck.",
            "period": {"start": "2026-10-01", "end": "2026-10-21"},
            "facts": {
                "category": "Essen",
                "period": {"start": "2026-10-01", "end": "2026-10-21"},
                "amount_current": 120,
                "budget_amount": 100,
                "amount_over_budget": 20,
                **facts,
            },
            "evidence": {"metrics": {"transaction_count": 3}},
            "claim_limits": ["descriptive_only"],
            "confidence": "high",
        }

    @staticmethod
    def contract(insight):
        return {
            "insight_id": insight["insight_id"],
            "dedupe_key": insight["dedupe_key"],
            "insight_type": insight["insight_type"],
            "period": insight["period"],
            "evidence": insight["evidence"],
            "claim_limits": insight["claim_limits"],
        }

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def test_reload_new_day_and_second_device_do_not_reclaim_same_evidence(self):
        insight = self.insight()
        with self.connection() as phone, self.connection() as desktop:
            self.assertTrue(claim_coach_insight_shown(phone, 1, insight, self.contract(insight)))
            phone.commit()
            # A second connection models another device; date is not part of the key.
            self.assertFalse(claim_coach_insight_shown(desktop, 1, insight, self.contract(insight)))
            desktop.commit()
            self.assertEqual(
                phone.execute("SELECT COUNT(*) FROM app_coach_insight_memory WHERE user_id=1").fetchone()[0],
                1,
            )

    def test_transport_failure_leaves_only_expiring_reservation(self):
        insight = self.insight()
        with self.connection() as phone, self.connection() as desktop:
            first = reserve_coach_insight_display(phone, 1, insight, self.contract(insight), "1" * 32)
            self.assertEqual(first["status"], "reserved")
            phone.commit()  # Claim reached SQLite, but the client never rendered it.
            other = reserve_coach_insight_display(desktop, 1, insight, self.contract(insight), "2" * 32)
            self.assertEqual(other["status"], "reserved_elsewhere")
            desktop.commit()
            state = phone.execute(
                "SELECT COUNT(*) FROM app_coach_insight_memory WHERE user_id=1"
            ).fetchone()[0]
            self.assertEqual(state, 0)
            self.assertEqual(phone.execute(
                "SELECT COUNT(*) FROM app_coach_insight_display_reservations WHERE user_id=1"
            ).fetchone()[0], 1)
            self.assertFalse(mark_coach_insight_opened(
                phone, 1, insight["insight_id"], insight["evidence_fingerprint"]
            ))
            self.assertIsNone(get_opened_coach_insight_context(
                phone, 1, insight["insight_id"], insight["evidence_fingerprint"]
            ))
            phone.execute(
                "UPDATE app_coach_insight_display_reservations SET expires_at=datetime('now','-1 second') WHERE user_id=1"
            )
            phone.commit()
            retry = reserve_coach_insight_display(desktop, 1, insight, self.contract(insight), "2" * 32)
            self.assertEqual(retry["status"], "reserved")
            self.assertTrue(acknowledge_coach_insight_display(
                desktop, 1, insight["insight_id"], insight["evidence_fingerprint"], "2" * 32
            ))

    def test_claim_response_loss_retries_same_reservation_token(self):
        insight = self.insight()
        with self.connection() as conn:
            token = "3" * 32
            first = reserve_coach_insight_display(conn, 1, insight, self.contract(insight), token)
            conn.commit()  # Simulate server commit followed by a dropped response.
            retry = reserve_coach_insight_display(conn, 1, insight, self.contract(insight), token)
            self.assertEqual(first["status"], "reserved")
            self.assertEqual(retry["status"], "reserved")
            self.assertTrue(acknowledge_coach_insight_display(
                conn, 1, insight["insight_id"], insight["evidence_fingerprint"], token
            ))
            self.assertTrue(acknowledge_coach_insight_display(
                conn, 1, insight["insight_id"], insight["evidence_fingerprint"], token
            ))

    def test_expired_or_stale_reservation_cannot_acknowledge(self):
        insight = self.insight()
        with self.connection() as conn:
            reserve_coach_insight_display(conn, 1, insight, self.contract(insight), "4" * 32)
            conn.execute(
                "UPDATE app_coach_insight_display_reservations SET expires_at=datetime('now','-1 second') WHERE user_id=1"
            )
            conn.commit()
            self.assertFalse(acknowledge_coach_insight_display(
                conn, 1, insight["insight_id"], insight["evidence_fingerprint"], "4" * 32
            ))
            retry = reserve_coach_insight_display(conn, 1, insight, self.contract(insight), "5" * 32)
            self.assertEqual(retry["status"], "reserved")
            self.assertFalse(acknowledge_coach_insight_display(
                conn, 1, insight["insight_id"], insight["evidence_fingerprint"], "4" * 32
            ))

    def test_failed_escalation_reservation_preserves_last_confirmed_display(self):
        first = self.insight()
        escalated = self.insight(
            insight_id="v4:synthetic-escalated", fingerprint="c" * 64,
            amount_current=170, amount_over_budget=70,
        )
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, first, self.contract(first)))
            self._age_shown_record(conn, 2)
            reserve = reserve_coach_insight_display(
                conn, 1, escalated, self.contract(escalated), "d" * 32
            )
            self.assertEqual(reserve["status"], "reserved")
            saved = conn.execute(
                "SELECT insight_id,evidence_fingerprint,shown_at FROM app_coach_insight_memory WHERE user_id=1"
            ).fetchone()
            self.assertEqual(saved["insight_id"], first["insight_id"])
            self.assertEqual(saved["evidence_fingerprint"], first["evidence_fingerprint"])
            conn.execute(
                "UPDATE app_coach_insight_display_reservations SET expires_at=datetime('now','-1 second') WHERE user_id=1"
            )
            conn.commit()
            retry = reserve_coach_insight_display(
                conn, 1, escalated, self.contract(escalated), "e" * 32
            )
            self.assertEqual(retry["status"], "reserved")
            still_saved = conn.execute(
                "SELECT evidence_fingerprint FROM app_coach_insight_memory WHERE user_id=1"
            ).fetchone()[0]
            self.assertEqual(still_saved, first["evidence_fingerprint"])

    def test_reservation_survives_connection_restart_but_acknowledged_display_suppresses_reload(self):
        insight = self.insight()
        token = "6" * 32
        with self.connection() as conn:
            result = reserve_coach_insight_display(conn, 1, insight, self.contract(insight), token)
            self.assertEqual(result["status"], "reserved")
        with self.connection() as restarted:
            self.assertEqual(
                reserve_coach_insight_display(restarted, 1, insight, self.contract(insight), "7" * 32)["status"],
                "reserved_elsewhere",
            )
            self.assertTrue(acknowledge_coach_insight_display(
                restarted, 1, insight["insight_id"], insight["evidence_fingerprint"], token
            ))
        with self.connection() as reload_conn:
            self.assertEqual(
                reserve_coach_insight_display(reload_conn, 1, insight, self.contract(insight), "8" * 32)["status"],
                "suppressed",
            )

    def test_changed_evidence_retriggers_and_keeps_last_opened_context(self):
        first = self.insight()
        revised = self.insight(
            insight_id="v4:synthetic-updated", fingerprint="b" * 64,
            amount_current=150, amount_over_budget=25,
        )
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, first, self.contract(first)))
            self.assertTrue(mark_coach_insight_opened(conn, 1, first["insight_id"], first["evidence_fingerprint"]))
            self._age_shown_record(conn, 8)
            self.assertTrue(claim_coach_insight_shown(conn, 1, revised, self.contract(revised)))
            context = get_opened_coach_insight_context(
                conn, 1, first["insight_id"], first["evidence_fingerprint"]
            )
            self.assertEqual(context["insight"]["facts"]["amount_current"], 120)
            self.assertEqual(
                conn.execute(
                    "SELECT opened_at FROM app_coach_insight_memory WHERE user_id=1 AND insight_id=?",
                    (revised["insight_id"],),
                ).fetchone()[0],
                None,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM app_coach_insight_memory WHERE user_id=1").fetchone()[0],
                2,
            )

    def test_period_or_source_id_churn_alone_does_not_retrigger(self):
        first = self.insight()
        first["evidence"]["source_pattern_ids"] = ["pattern-old"]
        shifted = self.insight(insight_id="v4:synthetic-next-day", fingerprint="b" * 64)
        shifted["period"] = {"start": "2026-10-02", "end": "2026-10-22"}
        shifted["evidence"]["source_pattern_ids"] = ["pattern-new"]
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, first, self.contract(first)))
            self.assertFalse(claim_coach_insight_shown(conn, 1, shifted, self.contract(shifted)))

    def test_material_change_waits_for_cooldown_but_large_warning_escalation_does_not(self):
        first = self.insight()
        revised = self.insight(
            insight_id="v4:synthetic-updated", fingerprint="b" * 64,
            amount_current=150, amount_over_budget=25,
        )
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, first, self.contract(first)))
            self._age_shown_record(conn, 2)
            self.assertFalse(claim_coach_insight_shown(conn, 1, revised, self.contract(revised)))
            revised["facts"]["amount_current"] = 170
            revised["facts"]["amount_over_budget"] = 70
            revised["insight_id"] = "v4:synthetic-escalated"
            revised["evidence_fingerprint"] = "c" * 64
            self.assertTrue(claim_coach_insight_shown(conn, 1, revised, self.contract(revised)))

    def test_positive_retrigger_is_dosed_for_thirty_days(self):
        first = self.insight()
        first["insight_type"] = "budget_recovery"
        next_version = self.insight(
            insight_id="v4:positive-next-period", fingerprint="b" * 64, amount_current=80
        )
        next_version["insight_type"] = "budget_recovery"
        next_version["dedupe_key"] = "coach:budget_recovery"
        first["dedupe_key"] = "coach:budget_recovery"
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, first, self.contract(first)))
            self._age_shown_record(conn, 8)
            self.assertFalse(claim_coach_insight_shown(conn, 1, next_version, self.contract(next_version)))
            self._age_shown_record(conn, 31)
            self.assertTrue(claim_coach_insight_shown(conn, 1, next_version, self.contract(next_version)))

    def test_identical_budget_warning_can_repeat_after_thirty_days(self):
        insight = self.insight()
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, insight, self.contract(insight)))
            self._age_shown_record(conn, 31)
            self.assertTrue(claim_coach_insight_shown(conn, 1, insight, self.contract(insight)))

    def test_active_budget_warning_repeats_after_thirty_days_when_period_rolls(self):
        first = self.insight()
        next_period = self.insight(
            insight_id="v4:synthetic-next-month", fingerprint="b" * 64,
        )
        next_period["period"] = {"start": "2026-11-01", "end": "2026-11-21"}
        next_period["facts"]["period"] = next_period["period"]
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, first, self.contract(first)))
            self._age_shown_record(conn, 31)
            self.assertTrue(
                claim_coach_insight_shown(conn, 1, next_period, self.contract(next_period))
            )

    def test_fingerprint_is_independent_of_snapshot_generation_time(self):
        from rove_coach_v4_delivery import coach_insight_evidence_fingerprint

        one = self.insight()
        two = {**one, "generated_at": (datetime.now() + timedelta(days=1)).isoformat()}
        self.assertEqual(coach_insight_evidence_fingerprint(one), coach_insight_evidence_fingerprint(two))
        two["facts"] = {**one["facts"], "amount_over_budget": 21}
        self.assertNotEqual(coach_insight_evidence_fingerprint(one), coach_insight_evidence_fingerprint(two))

    def test_concurrent_device_claim_has_exactly_one_winner(self):
        insight = self.insight()
        barrier = threading.Barrier(2)
        tokens = iter(("1" * 32, "2" * 32))
        token_lock = threading.Lock()

        def claim():
            with token_lock:
                token = next(tokens)
            with self.connection() as conn:
                barrier.wait(timeout=5)
                conn.execute("BEGIN IMMEDIATE")
                result = reserve_coach_insight_display(conn, 1, insight, self.contract(insight), token)
                conn.commit()
                return result["status"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: claim(), range(2)))
        self.assertEqual(sorted(outcomes), ["reserved", "reserved_elsewhere"])

    def test_users_are_isolated_and_delete_cascades_memory(self):
        insight = self.insight()
        pending = self.insight(insight_id="v4:pending", fingerprint="b" * 64)
        pending["insight_type"] = "subscription_cluster"
        pending["dedupe_key"] = "coach:subscription_cluster"
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, insight, self.contract(insight)))
            self.assertTrue(claim_coach_insight_shown(conn, 2, insight, self.contract(insight)))
            self.assertEqual(reserve_coach_insight_display(
                conn, 1, pending, self.contract(pending), "1" * 32
            )["status"], "reserved")
            self.assertEqual(reserve_coach_insight_display(
                conn, 2, pending, self.contract(pending), "2" * 32
            )["status"], "reserved")
            mark_coach_insight_opened(conn, 1, insight["insight_id"], insight["evidence_fingerprint"])
            self.assertIsNone(get_opened_coach_insight_context(conn, 2))
            conn.execute("DELETE FROM users WHERE user_id=1")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM app_coach_insight_memory WHERE user_id=1").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM app_coach_insight_memory WHERE user_id=2").fetchone()[0],
                1,
            )
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_coach_insight_display_reservations WHERE user_id=1"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_coach_insight_display_reservations WHERE user_id=2"
            ).fetchone()[0], 1)

    def test_missing_table_does_not_self_migrate_on_claim(self):
        with self.connection() as conn:
            conn.execute("DROP TABLE app_coach_insight_memory")
            insight = self.insight()
            with self.assertRaises(sqlite3.OperationalError):
                claim_coach_insight_shown(conn, 1, insight, self.contract(insight))
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_coach_insight_memory'"
            ).fetchone()
            self.assertIsNone(exists)

    def test_existing_memory_schema_is_extended_additively(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DROP TABLE app_coach_insight_memory")
            conn.executescript("""
                CREATE TABLE app_coach_insight_memory (
                    user_id INTEGER NOT NULL, insight_id TEXT NOT NULL,
                    evidence_fingerprint TEXT NOT NULL, insight_type TEXT NOT NULL,
                    period_start TEXT NOT NULL, period_end TEXT NOT NULL,
                    context_json TEXT NOT NULL, shown_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    opened_at TEXT, last_opened_fingerprint TEXT,
                    last_opened_context_json TEXT, last_opened_at TEXT,
                    PRIMARY KEY(user_id, insight_id),
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                INSERT INTO app_coach_insight_memory(
                    user_id,insight_id,evidence_fingerprint,insight_type,period_start,period_end,
                    context_json,last_opened_context_json,last_opened_fingerprint
                ) VALUES (1,'v4:legacy','a','budget_attention','2026-10-01','2026-10-21','{}','{}','a');
            """)
            ensure_app_coach_insight_memory_table(conn)
            row = conn.execute(
                "SELECT dedupe_key,last_opened_insight_id FROM app_coach_insight_memory"
            ).fetchone()
            self.assertEqual(row, ("coach:budget_attention", "v4:legacy"))
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_coach_insight_display_reservations'"
            ).fetchone())
            conn.commit()

    def test_api_claim_rechecks_v3_winner_and_persists_once(self):
        self._prepare_api_db()
        insight = self.insight()
        contract = self.contract(insight)
        client = self._client()
        patches = (
            patch.object(api, "build_live_app_data", return_value={"mentor_candidate": None, "features": {}}),
            patch.object(api, "get_shadow_insight_contracts", return_value=([contract], "ready")),
            patch.object(api, "build_shadow_insight_path", return_value={"v4_candidates": [contract]}),
            patch.object(api, "get_visible_coach_v4", return_value={"insight_type": "budget_attention"}),
            patch.object(api, "build_visible_mentor_insight", return_value=insight),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            payload = {"insight_id": insight["insight_id"], "evidence_fingerprint": insight["evidence_fingerprint"]}
            payload["reservation_token"] = "9" * 32
            first = client.post("/v1/coach/insights/claim", json=payload)
            second = client.post("/v1/coach/insights/claim", json=payload)
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(first.get_json()["status"], "reserved")
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(second.get_json()["status"], "reserved")
        acknowledged = client.post("/v1/coach/insights/acknowledged", json=payload)
        self.assertEqual(acknowledged.status_code, 200, acknowledged.get_json())
        self.assertTrue(acknowledged.get_json()["acknowledged"])
        duplicate_acknowledgement = client.post("/v1/coach/insights/acknowledged", json=payload)
        self.assertTrue(duplicate_acknowledgement.get_json()["acknowledged"])
        opened = client.post("/v1/coach/insights/opened", json=payload)
        self.assertEqual(opened.status_code, 200, opened.get_json())
        self.assertTrue(opened.get_json()["opened"])
        with self.connection() as conn:
            saved = get_opened_coach_insight_context(conn, 1, insight["insight_id"], insight["evidence_fingerprint"])
        self.assertIsNotNone(saved)

        v3_patches = (
            patch.object(api, "build_live_app_data", return_value={"mentor_candidate": {"id": "critical", "priority": 110}, "features": {}}),
            patch.object(api, "get_shadow_insight_contracts", return_value=([contract], "ready")),
            patch.object(api, "build_shadow_insight_path", return_value={"v4_candidates": []}),
            patch.object(api, "get_visible_coach_v4", return_value={"insight_type": "budget_attention"}),
            patch.object(api, "build_visible_mentor_insight", return_value=None),
        )
        with v3_patches[0], v3_patches[1], v3_patches[2], v3_patches[3], v3_patches[4]:
            blocked = client.post("/v1/coach/insights/claim", json=payload)
        self.assertEqual(blocked.status_code, 409)

    def test_api_score_fallback_claim_uses_real_join_and_preserves_dedupe_and_v3_recheck(self):
        self._prepare_api_db()
        client = self._client()
        template = self.insight()
        contract = self.contract(template)
        contract["confidence"] = "high"
        contract["evidence"] = {"metrics": {
            "amount_spent": 120, "monthly_limit": 100, "amount_over": 20,
        }}
        visible = {
            "insight_type": "budget_attention", "confidence_class": "high",
            "category_display": "Essen", "period_start": "2026-10-01",
            "period_end": "2026-10-21", "amount_current": 120,
            "budget_amount": 100, "amount_over_budget": 20,
            "overall_budget_status": "under_pressure", "title": "Budget im Blick",
            "message": "Dein Budget ist unter Druck.",
        }
        state = {"mentor_candidate": {
            "id": "motivation", "type": "motivation", "priority": 10,
        }, "features": {}}
        from rove_coach_v4_delivery import build_visible_mentor_insight
        insight = build_visible_mentor_insight(None, [contract], visible)
        self.assertIsNotNone(insight)
        payload = {
            "insight_id": insight["insight_id"],
            "evidence_fingerprint": insight["evidence_fingerprint"],
            "reservation_token": "8" * 32,
        }
        with (
            patch.object(api, "build_live_app_data", return_value=state),
            patch.object(api, "get_shadow_insight_contracts", return_value=([contract], "ready")),
            patch.object(api, "build_shadow_insight_path", return_value={"v4_candidates": [contract]}),
            patch.object(api, "get_visible_coach_v4", return_value=visible),
        ):
            claim = client.post("/v1/coach/insights/claim", json=payload)
            self.assertEqual(claim.status_code, 200, claim.get_json())
            self.assertEqual(claim.get_json()["status"], "reserved")
            for _ in range(2):
                ack = client.post("/v1/coach/insights/acknowledged", json=payload)
                self.assertEqual(ack.status_code, 200, ack.get_json())
                self.assertTrue(ack.get_json()["acknowledged"])
            opened = client.post("/v1/coach/insights/opened", json=payload)
            self.assertTrue(opened.get_json()["opened"])
            repeated = client.post("/v1/coach/insights/claim", json=payload)
            self.assertEqual(repeated.get_json()["status"], "suppressed")
            state["mentor_candidate"] = {"id": "budget-overrun", "type": "budget_overrun", "priority": 100}
            blocked = client.post("/v1/coach/insights/claim", json=payload)
            self.assertEqual(blocked.status_code, 409, blocked.get_json())
        with self.connection() as conn:
            self.assertIsNotNone(get_opened_coach_insight_context(conn, 1))
            self.assertIsNone(get_opened_coach_insight_context(conn, 2))

    def test_acknowledgement_requires_matching_user_and_live_reservation(self):
        self._prepare_api_db()
        insight = self.insight()
        contract = self.contract(insight)
        with self.connection() as conn:
            reserved = reserve_coach_insight_display(conn, 1, insight, contract, "c" * 32)
            self.assertEqual(reserved["status"], "reserved")
            self.assertFalse(acknowledge_coach_insight_display(
                conn, 2, insight["insight_id"], insight["evidence_fingerprint"], "c" * 32
            ))
        client = self._client()
        payload = {
            "insight_id": insight["insight_id"],
            "evidence_fingerprint": insight["evidence_fingerprint"],
            "reservation_token": "c" * 32,
        }
        ack = client.post("/v1/coach/insights/acknowledged", json=payload)
        self.assertEqual(ack.status_code, 200, ack.get_json())
        with self.connection() as conn:
            self.assertTrue(mark_coach_insight_opened(
                conn, 1, insight["insight_id"], insight["evidence_fingerprint"]
            ))

    def test_opened_context_survives_chat_reload_without_ai_fallback(self):
        self._prepare_api_db()
        insight = self.insight()
        contract = self.contract(insight)
        with self.connection() as conn:
            self.assertTrue(claim_coach_insight_shown(conn, 1, insight, contract))
            self.assertTrue(mark_coach_insight_opened(conn, 1, insight["insight_id"], insight["evidence_fingerprint"]))
        client = self._client()
        with patch.object(api, "get_shadow_insight_contracts", return_value=([], "snapshot_stale")), \
             patch.object(api, "get_visible_coach_v4", return_value=None), \
             patch.object(api, "ai_chat_provider", side_effect=AssertionError("opened insights stay deterministic")):
            response = client.post("/v1/ai/chat", json={"message": "Warum sagst du mir das?"})
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["deterministic"])
        self.assertEqual(payload["insight_id"], insight["insight_id"])
        self.assertIn("120,00 €", payload["answer"])
        self.assertIn("20,00 €", payload["answer"])

    def test_missing_memory_table_returns_503_without_recreating_schema(self):
        self._prepare_api_db()
        with self.connection() as conn:
            conn.execute("DROP TABLE app_coach_insight_memory")
        insight = self.insight()
        contract = self.contract(insight)
        client = self._client()
        with patch.object(api, "build_live_app_data", return_value={"mentor_candidate": None, "features": {}}), \
             patch.object(api, "get_shadow_insight_contracts", return_value=([contract], "ready")), \
             patch.object(api, "build_shadow_insight_path", return_value={"v4_candidates": [contract]}), \
             patch.object(api, "get_visible_coach_v4", return_value={"insight_type": "budget_attention"}), \
             patch.object(api, "build_visible_mentor_insight", return_value=insight):
            response = client.post("/v1/coach/insights/claim", json={
                "insight_id": insight["insight_id"],
                "evidence_fingerprint": insight["evidence_fingerprint"],
                "reservation_token": "b" * 32,
            })
        self.assertEqual(response.status_code, 503)
        with self.connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_coach_insight_memory'"
            ).fetchone())

    def test_acknowledgement_missing_table_returns_503(self):
        self._prepare_api_db()
        with self.connection() as conn:
            conn.execute("DROP TABLE app_coach_insight_memory")
        response = self._client().post("/v1/coach/insights/acknowledged", json={
            "insight_id": "v4:synthetic",
            "evidence_fingerprint": "a" * 64,
            "reservation_token": "d" * 32,
        })
        self.assertEqual(response.status_code, 503, response.get_json())
        with self.connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_coach_insight_memory'"
            ).fetchone())

    def test_missing_reservation_table_fails_closed_without_schema_recreation(self):
        self._prepare_api_db()
        with self.connection() as conn:
            conn.execute("DROP TABLE app_coach_insight_display_reservations")
        insight = self.insight()
        contract = self.contract(insight)
        with patch.object(api, "build_live_app_data", return_value={"mentor_candidate": None, "features": {}}), \
             patch.object(api, "get_shadow_insight_contracts", return_value=([contract], "ready")), \
             patch.object(api, "build_shadow_insight_path", return_value={"v4_candidates": [contract]}), \
             patch.object(api, "get_visible_coach_v4", return_value={"insight_type": "budget_attention"}), \
             patch.object(api, "build_visible_mentor_insight", return_value=insight):
            response = self._client().post("/v1/coach/insights/claim", json={
                "insight_id": insight["insight_id"],
                "evidence_fingerprint": insight["evidence_fingerprint"],
                "reservation_token": "e" * 32,
            })
        self.assertEqual(response.status_code, 503, response.get_json())
        with self.connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_coach_insight_display_reservations'"
            ).fetchone())

    def test_missing_opened_context_table_fails_closed_for_coach_question(self):
        self._prepare_api_db()
        with self.connection() as conn:
            conn.execute("DROP TABLE app_coach_insight_memory")
        client = self._client()
        with patch.object(api, "ai_chat_provider", side_effect=AssertionError("must not use ungrounded AI")):
            response = client.post("/v1/ai/chat", json={"message": "Warum sagst du mir das?"})
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertNotIn("app_coach_insight_memory", response.get_json().get("answer", ""))

    def _age_shown_record(self, conn, days):
        shown_at = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE app_coach_insight_memory SET shown_at=? WHERE user_id=1", (shown_at,))

    def _prepare_api_db(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS user_access(user_id INTEGER PRIMARY KEY, status TEXT)")
            api.ensure_auth_tables(conn)
            ensure_app_coach_insight_memory_table(conn)
            conn.execute("INSERT OR IGNORE INTO user_access VALUES (1,'approved')")
            conn.execute(
                "INSERT OR IGNORE INTO app_accounts(email,user_id,verified_at,source) VALUES ('one@test.invalid',1,CURRENT_TIMESTAMP,'app')"
            )
            conn.commit()
        self.patches = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "coach-memory-test-secret-with-sufficient-entropy"),
        ]
        for item in self.patches:
            item.start()
        api.app.config.update(TESTING=True)

    def _client(self):
        token = "coach-memory-session"
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_id = conn.execute("SELECT id FROM app_accounts WHERE user_id=1").fetchone()[0]
            session = conn.execute(
                "INSERT INTO app_sessions(token_hash,account_id,expires_at) VALUES (?,?,?)",
                (api.keyed_hash(token), account_id, (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.execute(
                "INSERT INTO app_session_pins(session_id,pin_verifier,unlocked_at,last_activity_at) VALUES (?,'test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (session.lastrowid,),
            )
            conn.commit()
        client = api.app.test_client()
        client.set_cookie(api.SESSION_COOKIE_NAME, token, domain="localhost", path="/")
        return client

    def tearDown(self):
        for item in reversed(getattr(self, "patches", [])):
            item.stop()
        self.temp.cleanup()


if __name__ == "__main__":
    unittest.main()
