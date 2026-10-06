from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
import rove_contract_cancellation as vks
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session
from test_financial_accounts_sprint2 import create_db


class ContractCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        create_db(self.path)
        for name, value in (("DB_PATH", self.path), ("AUTH_SECRET", "vks-test-secret")):
            patcher = patch.object(api, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        api.app.config.update(TESTING=True)
        for user in (1, 2):
            ensure_unlocked_test_session(self.path, user, f"session-{user}")
        with self.connection() as conn:
            vks.ensure_cancellation_schema(conn)
            conn.executemany("""INSERT INTO app_contracts
                (user_id,contract_id,detail_key,name,category,amount,cancelable) VALUES (?,?,?,?,?,?,?)""", [
                (1, "own", "own", "Beispielanbieter", "Abos", 50, 1),
                (2, "foreign", "foreign", "Anderer Anbieter", "Abos", 60, 1),
                (1, "noncancelable", "loan", "Kredit", "Kredite", 100, 0),
            ])
            conn.commit()

    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(conn.close)
        return conn

    def request(self, method, path, payload=None, user=1, origin="https://getrove.de"):
        with api.app.test_client() as client:
            if user:
                client.set_cookie(api.SESSION_COOKIE_NAME, f"session-{user}")
            return client.open(path, method=method, json=payload, headers={"Origin": origin})

    def start(self, contract="own", user=1):
        response = self.request("POST", "/v1/contract-cancellations", {"contract_id": contract}, user)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["case"]

    def action(self, case, action, extra=None, user=1):
        return self.request("POST", f"/v1/contract-cancellations/{case['id']}",
                            {"action": action, "revision": case["revision"], **(extra or {})}, user)

    def review(self, case, **changes):
        values = {"sender_name": "Test Nutzer", "sender_address": "Musterweg 1\n12345 Musterort",
                  "recipient": "Beispielanbieter\nKundenservice, Musterweg 2", "contract_reference": "REF-123",
                  "timing_choice": "next_possible", **changes}
        response = self.action(case, "review", {"review": values})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["case"]

    def confirm(self, case, **changes):
        return self.action(case, "confirm", {"confirmed": True, "notice_sha256": case["notice_sha256"], **changes})

    def count(self, table, user=1):
        return self.connection().execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (user,)).fetchone()[0]

    def test_owned_contract_starts_review_and_records_draft_transition(self):
        case = self.start()
        self.assertEqual(case["status"], "REVIEW_REQUIRED")
        self.assertEqual(case["missing_fields"], ["sender_name", "recipient", "timing_choice"])
        self.assertEqual([(e["event_type"], e["to_status"]) for e in case["events"]],
                         [("case_created", "DRAFT"), ("review_required", "REVIEW_REQUIRED")])
        self.assertIsNone(case["cancellation_target_date"])
        self.assertIsNone(case["user_confirmed_at"])

    def test_foreign_contract_and_forged_user_are_rejected(self):
        response = self.request("POST", "/v1/contract-cancellations", {"contract_id": "foreign"})
        self.assertEqual(response.status_code, 404)
        response = self.request("POST", "/v1/contract-cancellations", {"contract_id": "foreign", "user_id": 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count("app_contract_cancellations"), 0)

    def test_duplicate_start_and_parallel_clicks_have_one_active_case(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            cases = list(pool.map(lambda _: self.start(), range(4)))
        self.assertEqual(len({case["id"] for case in cases}), 1)
        self.assertEqual(self.count("app_contract_cancellations"), 1)
        self.assertEqual(self.count("app_contract_cancellation_events"), 2)

    def test_database_index_enforces_one_open_case_even_without_adapter(self):
        self.start()
        with self.connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO app_contract_cancellations (id,user_id,contract_id,status,notice_date)
                    VALUES ('duplicate',1,'own','DRAFT','2026-09-28')""")

    def test_non_cancelable_and_nonexistent_contract_fail_closed(self):
        for contract, code in (("noncancelable", 409), ("missing", 404)):
            response = self.request("POST", "/v1/contract-cancellations", {"contract_id": contract})
            self.assertEqual(response.status_code, code)

    def test_notice_is_deterministic_and_debit_day_never_becomes_deadline(self):
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET debit_day='15.' WHERE contract_id='own'")
        case = self.review(self.start())
        conn = self.connection()
        raw = dict(conn.execute("SELECT * FROM app_contract_cancellations WHERE id=?", (case["id"],)).fetchone())
        contract = dict(conn.execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        notice = vks.generate_notice(raw, contract)
        self.assertEqual(notice, case["generated_notice_text"])
        self.assertIn("hiermit kündige ich meinen Vertrag bei Beispielanbieter zum nächstmöglichen Zeitpunkt.", notice)
        self.assertEqual(notice.count("Beispielanbieter"), 1)
        self.assertNotIn(case["recipient"], notice)
        self.assertIn("Bitte bestätigen Sie mir die Kündigung sowie den Beendigungszeitpunkt schriftlich.", notice)
        self.assertIsNone(case["cancellation_target_date"])
        self.assertIn("REF-123", notice)

    def test_notice_uses_reviewed_unicode_facts_and_sender_address(self):
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET name=? WHERE contract_id='own'", ("Müller & Söhne",))
        case = self.review(self.start(), sender_name="Jörg Öztürk",
                           sender_address="Straße 7\n12345 Köln", recipient="kontakt@example.test",
                           contract_reference="KÜ-ß-42")
        notice = case["generated_notice_text"]
        self.assertIn("Jörg Öztürk\nStraße 7\n12345 Köln", notice)
        self.assertIn("Vertrags-/Kundennummer: KÜ-ß-42", notice)
        self.assertIn("bei Müller & Söhne", notice)
        self.assertEqual(notice.count("Müller & Söhne"), 1)
        self.assertNotIn("kontakt@example.test", notice)

    def test_explicit_document_date_is_preserved_with_user_provenance(self):
        case = self.review(self.start(), timing_choice="date", cancellation_target_date="2040-09-15")
        self.assertIn("zum 15.09.2040", case["generated_notice_text"])
        self.assertEqual(case["date_source"], "user_review")
        self.assertEqual(case["cancellation_target_date"], "2040-09-15")

    def test_missing_invalid_or_past_date_is_rejected_without_mutation(self):
        case = self.start()
        for value in (None, "2026-02-30", "2020-01-01", "20400915", 5):
            response = self.action(case, "review", {"review": {"timing_choice": "date", "cancellation_target_date": value}})
            self.assertEqual(response.status_code, 400, value)
        self.assertEqual(self.count("app_contract_cancellation_events"), 2)

    def test_switching_to_next_possible_removes_the_explicit_date(self):
        case = self.review(self.start(), timing_choice="date", cancellation_target_date="2040-09-15")
        response = self.action(case, "review", {"review": {"timing_choice": "next_possible"}})
        case = response.get_json()["case"]
        self.assertIsNone(case["cancellation_target_date"])
        self.assertIsNone(case["date_source"])
        self.assertNotIn("15.09.2040", case["generated_notice_text"])

    def test_missing_sender_or_recipient_or_timing_prevents_ready(self):
        for field in ("sender_name", "recipient", "timing_choice"):
            case = self.review(self.start(), **{field: None})
            response = self.confirm(case)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["error"], "cancellation_review_incomplete")
        self.assertEqual(self.count("app_contract_cancellation_events"), 5)

    def test_confirm_requires_literal_true_and_reviewed_text_hash(self):
        case = self.review(self.start())
        for confirmed in (None, False, 1, "true"):
            response = self.confirm(case, confirmed=confirmed)
            self.assertEqual(response.status_code, 400)
        response = self.confirm(case, notice_sha256="forged")
        self.assertEqual(response.status_code, 409)

    def test_confirmation_reaches_ready_atomically_and_repeat_is_idempotent(self):
        case = self.review(self.start())
        result = self.confirm(case)
        self.assertEqual(result.status_code, 200, result.get_json())
        ready = result.get_json()["case"]
        self.assertEqual(ready["status"], "READY_TO_SEND")
        self.assertTrue(ready["user_confirmed_at"])
        self.assertTrue(ready["ready_to_send_at"])
        self.assertEqual([e["event_type"] for e in ready["events"]][-2:], ["user_confirmed", "ready_to_send"])
        self.assertEqual(self.confirm(case).get_json()["case"], ready)

    def test_confirmation_failure_rolls_back_intermediate_state_and_history(self):
        case = self.review(self.start())
        with self.connection() as conn:
            conn.execute("""CREATE TRIGGER reject_ready BEFORE UPDATE OF status ON app_contract_cancellations
                WHEN NEW.status='READY_TO_SEND' BEGIN SELECT RAISE(ABORT,'test rollback'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.confirm(case)
        stored = self.request("GET", f"/v1/contract-cancellations/{case['id']}").get_json()["case"]
        self.assertEqual(stored["status"], "REVIEW_REQUIRED")
        self.assertIsNone(stored["user_confirmed_at"])
        self.assertEqual(len(stored["events"]), 3)

    def test_stale_revision_cannot_confirm_or_overwrite_newer_review(self):
        old = self.review(self.start())
        latest = self.review(old, contract_reference="NEW")
        self.assertGreater(latest["revision"], old["revision"])
        self.assertEqual(self.confirm(old).status_code, 409)
        self.assertEqual(self.action(old, "review", {"review": {"recipient": "old"}}).status_code, 409)

    def test_contract_identity_change_invalidates_confirmation(self):
        case = self.review(self.start())
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET name='Neuer Anbieter' WHERE contract_id='own'")
        self.assertEqual(self.confirm(case).get_json()["error"], "cancellation_contract_changed")
        latest = self.review(case)
        self.assertIn("Neuer Anbieter", latest["generated_notice_text"])
        self.assertEqual(self.confirm(latest).status_code, 200)

    def test_terminal_cases_cannot_be_edited_or_set_to_send(self):
        case = self.confirm(self.review(self.start())).get_json()["case"]
        self.assertEqual(self.action(case, "review", {"review": {"recipient": "new"}}).status_code, 409)
        for action in ("SENT", "READY_TO_SEND", "DRAFT"):
            self.assertEqual(self.action(case, action).status_code, 400)

    def test_cancel_only_cancels_preparation_and_allows_fresh_case(self):
        case = self.start()
        cancelled = self.action(case, "cancel").get_json()["case"]
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(cancelled["events"][-1]["event_type"], "cancelled")
        self.assertEqual(self.action(cancelled, "cancel").get_json()["case"], cancelled)
        self.assertEqual(self.confirm(cancelled).status_code, 409)
        self.assertNotEqual(self.start()["id"], case["id"])

    def test_foreign_case_cannot_be_read_reviewed_confirmed_cancelled_or_listed(self):
        foreign = self.start("foreign", user=2)
        self.assertEqual(self.request("GET", f"/v1/contract-cancellations/{foreign['id']}").status_code, 404)
        for action in ("review", "confirm", "cancel"):
            self.assertEqual(self.action(foreign, action).status_code, 404)
        self.assertEqual(self.request("GET", "/v1/contract-cancellations?contract_id=foreign").status_code, 404)
        self.assertEqual(self.count("app_contract_cancellations", 2), 1)

    def test_missing_session_and_untrusted_origin_are_blocked(self):
        self.assertEqual(self.request("POST", "/v1/contract-cancellations", {"contract_id": "own"}, user=None).status_code, 401)
        self.assertEqual(self.request("POST", "/v1/contract-cancellations", {"contract_id": "own"}, origin="https://foreign.test").status_code, 403)

    def test_existing_pin_guard_also_blocks_case_access(self):
        with self.connection() as conn:
            conn.execute("UPDATE app_session_pins SET unlocked_at=NULL")
        self.assertEqual(self.request("POST", "/v1/contract-cancellations", {"contract_id": "own"}).get_json()["error"], "pin_locked")

    def test_normalized_telegram_contract_allowed_and_ambiguous_legacy_blocked(self):
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET source='telegram_legacy',legacy_ref='telegram_legacy:abos:netflix' WHERE contract_id='own'")
        case = self.start()
        self.assertEqual(case["contract_source"], "telegram_legacy")
        self.assertEqual(case["status"], "REVIEW_REQUIRED")
        self.assertEqual(self.request("POST", "/v1/contract-cancellations", {"contract_id": "telegram_legacy:unknown"}).status_code, 404)
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET legacy_ref=NULL WHERE contract_id='own'")
        response = self.request("POST", "/v1/contract-cancellations", {"contract_id": "own"})
        self.assertEqual(response.get_json()["error"], "contract_ownership_unverified")

    def test_ready_does_not_write_contract_financial_state_or_send_mail(self):
        conn = self.connection()
        before = dict(conn.execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        financial_before = state.build_live_app_data(conn, 1)
        conn.commit()
        with patch.object(api.urllib.request, "urlopen", side_effect=AssertionError("no network allowed")):
            result = self.confirm(self.review(self.start()))
        self.assertEqual(result.status_code, 200)
        after = dict(conn.execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        self.assertEqual(after, before)
        financial_after = state.build_live_app_data(conn, 1)
        for field in ("netWorth", "buffer", "score", "vertraege"):
            self.assertEqual(financial_after[field], financial_before[field])

    def test_schema_is_additive_and_idempotent_and_fk_ownership_enforced(self):
        with self.connection() as conn:
            before = list(conn.execute("SELECT * FROM app_contracts"))
            vks.ensure_cancellation_schema(conn)
            vks.ensure_cancellation_schema(conn)
            self.assertEqual(list(conn.execute("SELECT * FROM app_contracts")), before)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO app_contract_cancellations (id,user_id,contract_id,status,notice_date)
                    VALUES ('wrong-owner',1,'foreign','DRAFT','2026-09-28')""")

    def test_schema_is_not_created_by_requests(self):
        with self.connection() as conn:
            conn.execute("DROP TABLE app_contract_cancellation_events")
            conn.execute("DROP TABLE app_contract_cancellations")
        response = self.request("POST", "/v1/contract-cancellations", {"contract_id": "own"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.connection().execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellations'").fetchone())

    def test_real_account_delete_removes_cases_and_events_only_for_owner(self):
        self.start()
        other = self.start("foreign", user=2)
        code = "123456"
        with self.connection() as conn:
            conn.execute("""INSERT INTO app_account_delete_codes (user_id,code_hash,expires_at)
                VALUES (1,?,'2099-01-01 00:00:00')""", (api.keyed_hash(f"delete:1:{code}"),))
        with patch.object(api.account_delete_cleanup, "record_delete_tombstone"), \
             patch.object(api, "account_delete_cleanup_paths", return_value=[]), \
             patch.object(api.account_delete_cleanup, "generated_report_paths", return_value=([], {})), \
             patch.object(api, "remove_deleted_account_files", return_value=[]), \
             patch.object(api, "retry_account_delete_file_cleanup"):
            response = self.request("DELETE", "/v1/account", {"confirmation": "LOESCHEN", "code": code})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.count("app_contract_cancellations"), 0)
        self.assertEqual(self.count("app_contract_cancellation_events"), 0)
        self.assertEqual(self.count("app_contract_cancellations", 2), 1)
        self.assertEqual(self.request("GET", f"/v1/contract-cancellations/{other['id']}", user=2).status_code, 200)

    def test_tombstone_restore_scrubs_cases_and_history_and_export_is_user_scoped(self):
        self.start()
        self.start("foreign", user=2)
        exports = dict(api.DATA_EXPORT_TABLES)
        self.assertEqual(exports["kuendigungsfaelle"], "app_contract_cancellations")
        self.assertEqual(exports["kuendigungshistorie"], "app_contract_cancellation_events")
        with self.connection() as conn:
            api.delete_user_rows_for_tombstone(conn, 1)
        self.assertEqual(self.count("app_contract_cancellations"), 0)
        self.assertEqual(self.count("app_contract_cancellation_events"), 0)
        self.assertEqual(self.count("app_contract_cancellation_events", 2), 2)

    def test_malformed_payload_and_mass_assignment_do_not_change_cases(self):
        case = self.start()
        for payload in (["bad"], {"status": "READY_TO_SEND"}, {"sender_name": "x", "user_id": 2}):
            response = self.request("POST", f"/v1/contract-cancellations/{case['id']}", payload)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.action(case, "review", {"review": {"status": "READY_TO_SEND"}}).status_code, 400)
        self.assertEqual(self.count("app_contract_cancellation_events"), 2)


if __name__ == "__main__":
    unittest.main()
