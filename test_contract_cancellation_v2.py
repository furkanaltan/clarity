"""Real authenticated VKS lifecycle with strictly mocked external transport."""
import hashlib
import io
import json
import sqlite3
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import rove_app_api as api
import rove_app_state as state
import rove_contract_cancellation as vks
import rove_contract_cancellation_mail as mail
import test_contract_cancellation_v1 as fixtures


class CancellationDispatchTests(unittest.TestCase):
    connection = fixtures.ContractCancellationTests.connection
    request = fixtures.ContractCancellationTests.request
    start = fixtures.ContractCancellationTests.start
    action = fixtures.ContractCancellationTests.action
    review = fixtures.ContractCancellationTests.review
    confirm = fixtures.ContractCancellationTests.confirm
    count = fixtures.ContractCancellationTests.count

    def setUp(self):
        fixtures.ContractCancellationTests.setUp(self)
        for name, value in (("VKS_EMAIL_ENABLED", True), ("BREVO_API_KEY", "test-key"),
                            ("LOGIN_FROM_EMAIL", "info@getrove.de")):
            patcher = patch.object(api, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.transport = Mock(return_value="<receipt@example.test>")
        patcher = patch.object(api, "send_cancellation_email", self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ready(self, contract="own", user=1):
        case = self.start(contract, user)
        values = {"sender_name": "Test Nutzer", "recipient": "cancel@example.test", "timing_choice": "next_possible"}
        case = self.action(case, "review", {"review": values}, user).get_json()["case"]
        return self.action(case, "confirm", {"confirmed": True, "notice_sha256": case["notice_sha256"]}, user).get_json()["case"]

    def send(self, case, action="send", **changes):
        return self.action(case, action, {"confirmed": True, "notice_sha256": case["notice_sha256"], **changes})

    def sent(self):
        response = self.send(self.ready())
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["case"]

    def received(self, case, **changes):
        payload = {"sender": "service@example.test", "subject": "Ihre Kündigung",
                   "body": "Wir bestätigen Ihre Kündigung.",
                   "received_at": datetime.now(timezone.utc).isoformat(), **changes}
        return self.action(case, "response", {"response": payload})

    def provider_confirm(self, case, **changes):
        message = [item for item in case["messages"] if item["direction"] == "inbound"][-1]
        return self.action(case, "confirm_response", {"response": {
            "message_id": message["id"], "body_sha256": message["body_sha256"],
            "confirmed": True, "outcome": "termination_confirmed", **changes}})

    def test_exact_confirmed_text_and_durable_unlocked_send_attempt(self):
        ready = self.ready()
        def transport(plan, **kwargs):
            with self.connection() as conn:
                self.assertEqual(vks.get_cancellation_case(conn, 1, ready["id"])["status"], "SENDING")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE users SET user_id=user_id WHERE user_id=1")
            self.assertEqual(plan["body"], ready["generated_notice_text"])
            self.assertEqual(plan["body_sha256"], ready["notice_sha256"])
            self.assertEqual(plan["recipient"], "cancel@example.test")
            self.assertEqual(plan["sender"], "info@getrove.de")
            self.assertEqual(plan["reply_to"], ready["reply_to"])
            return "<receipt@example.test>"
        self.transport.side_effect = transport
        result = self.send(ready).get_json()["case"]
        self.assertEqual(result["status"], "SENT")
        message = result["messages"][0]
        self.assertIsNone(message["body_text"])
        self.assertIsNone(message["delivered_at"])
        self.assertIsNone(result["termination_confirmed_at"])
        self.assertTrue(message["sent_at"])
        self.assertEqual(message["provider_message_id"], "<receipt@example.test>")
        self.assertEqual([e["event_type"] for e in result["events"]][-3:], ["send_requested", "sending", "sent"])

    def test_parallel_double_click_dispatches_one_mail(self):
        case = self.ready()
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.send(case), range(8)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.count("app_contract_cancellation_messages"), 1)
        self.assertEqual(self.start()["id"], case["id"])

    def test_timeout_unknown_blocks_retry_cancel_and_new_case(self):
        self.transport.side_effect = mail.CancellationTransportError("transport_outcome_unknown")
        case = self.send(self.ready()).get_json()["case"]
        self.assertEqual(case["status"], "FAILED")
        self.assertFalse(case["retry_allowed"])
        self.assertEqual(case["messages"][0]["transport_status"], "unknown")
        for action in ("retry", "send", "cancel"):
            self.assertEqual(self.send(case, action).status_code, 409 if action != "cancel" else 400)
        self.assertEqual(self.action(case, "cancel").status_code, 409)
        self.assertEqual(self.start()["id"], case["id"])
        self.assertEqual(self.transport.call_count, 1)

    def test_crash_leaves_durable_sending_lock(self):
        case = self.ready()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            vks.prepare_cancellation_send(conn, 1, case["id"], expected_revision=case["revision"],
                notice_sha256=case["notice_sha256"], confirmed=True, sender="info@getrove.de")
            conn.commit()
        self.assertEqual(self.send(case).get_json()["case"]["status"], "SENDING")
        self.assertEqual(self.send(case, "retry").get_json()["case"]["status"], "SENDING")
        self.transport.assert_not_called()

    def test_known_rejection_allows_only_explicit_audited_retry(self):
        self.transport.side_effect = [mail.CancellationTransportError("temporary_transport_failure", retry_allowed=True), "<retry@example.test>"]
        case = self.send(self.ready()).get_json()["case"]
        self.assertEqual(case["status"], "FAILED")
        self.assertTrue(case["retry_allowed"])
        self.assertEqual(self.send(case).status_code, 409)
        self.assertEqual(self.send(case, "retry", confirmed=False).status_code, 400)
        result = self.send(case, "retry").get_json()["case"]
        self.assertEqual(result["status"], "SENT")
        self.assertEqual(len(result["messages"]), 2)
        self.assertIn("retry", [event["event_type"] for event in result["events"]])
        self.assertEqual(self.transport.call_count, 2)

    def test_safe_retries_are_bounded(self):
        self.transport.side_effect = mail.CancellationTransportError("transport_rejected", retry_allowed=True)
        case = self.ready()
        for index in range(3):
            case = self.send(case, "send" if index == 0 else "retry").get_json()["case"]
        self.assertEqual(self.send(case, "retry").status_code, 429)
        self.assertEqual(self.transport.call_count, 3)

    def test_send_requires_literal_consent_and_checksum(self):
        case = self.ready()
        for value in (False, None, "true", 1):
            self.assertEqual(self.send(case, confirmed=value).status_code, 400)
        self.assertEqual(self.send(case, notice_sha256="forged").status_code, 409)
        self.transport.assert_not_called()
        self.assertEqual(self.count("app_contract_cancellation_messages"), 0)

    def test_disabled_transport_or_missing_key_has_no_attempt(self):
        case = self.ready()
        for name, value in (("VKS_EMAIL_ENABLED", False), ("BREVO_API_KEY", "")):
            with patch.object(api, name, value):
                self.assertEqual(self.send(case).status_code, 503)
        self.transport.assert_not_called()
        self.assertEqual(self.count("app_contract_cancellation_messages"), 0)

    def test_missing_verified_reply_is_fail_closed(self):
        case = self.ready()
        with patch.object(vks, "verified_reply_address", side_effect=vks.CancellationError('cancellation_verified_reply_required', 409)):
            self.assertEqual(self.send(case).status_code, 409)
        self.transport.assert_not_called()

    def test_missing_or_postal_recipient_returns_to_review(self):
        for value in ("", "Provider\nPostal address", "a@example.test,b@example.test"):
            case = self.ready()
            with self.connection() as conn:
                # Simulate stale legacy data after confirmation; no confirmed payload may be repaired silently.
                conn.execute("UPDATE app_contract_cancellations SET recipient=? WHERE id=?", (value, case["id"]))
            response = self.send(case)
            self.assertEqual(response.status_code, 409)
            latest = response.get_json()["case"]
            self.assertEqual(latest["status"], "REVIEW_REQUIRED")
            self.assertIsNone(latest["user_confirmed_at"])
            self.action(latest, "cancel")
        self.transport.assert_not_called()

    def test_changed_confirmed_facts_and_notice_invalidate_without_sending(self):
        for column, value in (("contract_reference", "OTHER"), ("recipient", "other@example.test"),
                              ("generated_notice_text", "Tampered text"), ("cancellation_target_date", "2040-01-01")):
            case = self.ready()
            with self.connection() as conn:
                conn.execute(f"UPDATE app_contract_cancellations SET {column}=? WHERE id=?", (value, case["id"]))
            response = self.send(case)
            self.assertEqual(response.status_code, 409, response.get_json())
            latest = response.get_json()["case"]
            self.assertEqual(latest["status"], "REVIEW_REQUIRED")
            self.assertGreater(latest["revision"], case["revision"])
            self.assertIsNone(latest["confirmed_notice_sha256"])
            self.action(latest, "cancel")
        self.transport.assert_not_called()

    def test_changed_provider_requires_new_review_and_confirmation(self):
        case = self.ready()
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET name='Changed provider' WHERE contract_id='own'")
        response = self.send(case)
        self.assertEqual(response.status_code, 409)
        latest = response.get_json()["case"]
        self.assertIn("Changed provider", latest["generated_notice_text"])
        self.assertFalse(latest["contract_changed"])
        self.assertEqual(self.send(latest).status_code, 409)
        latest = self.confirm(latest).get_json()["case"]
        self.assertEqual(self.send(latest).get_json()["case"]["status"], "SENT")

    def test_foreign_case_messages_and_mutations_rejected(self):
        other = self.ready("foreign", 2)
        for action, extra in (("send", {"confirmed": True, "notice_sha256": other["notice_sha256"]}),
                              ("delivery", {}), ("response", {"response": {}}), ("confirm_response", {"response": {}})):
            if action == "delivery":
                response = self.request("POST", f"/v1/contract-cancellations/{other['id']}", {"action": action})
            else:
                response = self.action(other, action, extra)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.request("GET", f"/v1/contract-cancellations/{other['id']}").status_code, 404)
        self.transport.assert_not_called()

    def test_auth_pin_and_origin_boundary_unchanged(self):
        case = self.ready()
        self.assertEqual(self.request("GET", f"/v1/contract-cancellations/{case['id']}", user=None).status_code, 401)
        self.assertEqual(self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "send"}, origin="https://evil.test").status_code, 403)
        with self.connection() as conn:
            conn.execute("UPDATE app_session_pins SET unlocked_at=NULL")
        self.assertEqual(self.send(case).status_code, 423)
        self.transport.assert_not_called()

    def test_delivery_requires_authenticated_exact_evidence(self):
        case = self.sent()
        evidence = {"messageId": "<receipt@example.test>", "email": "cancel@example.test", "event": "delivered",
                    "date": datetime.now(timezone.utc).isoformat()}
        with patch.object(api, "fetch_cancellation_delivery", return_value=None):
            response = self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "delivery"})
            self.assertEqual(response.get_json()["case"]["status"], "SENT")
        for key, value in (("messageId", "foreign"), ("email", "other@example.test"), ("event", "opened"), ("date", "2020-01-01T00:00:00Z")):
            with patch.object(api, "fetch_cancellation_delivery", return_value={**evidence, key: value}):
                response = self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "delivery"})
                self.assertIn(response.status_code, (400, 409))
        with patch.object(api, "fetch_cancellation_delivery", return_value=evidence):
            response = self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "delivery"})
            self.assertEqual(response.get_json()["case"]["status"], "DELIVERY_RECORDED")
            self.assertIsNone(response.get_json()["case"]["termination_confirmed_at"])
            self.assertEqual(self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "delivery"}).get_json()["case"], response.get_json()["case"])

    def test_user_cannot_inject_delivery_evidence(self):
        case = self.sent()
        self.assertEqual(self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "delivery", "evidence": {}}).status_code, 400)

    def test_delivery_before_local_receipt_commit_is_valid_after_attempt_started(self):
        case = self.sent()
        with self.connection() as conn:
            conn.execute("UPDATE app_contract_cancellation_messages SET created_at=datetime('now','-2 seconds') WHERE case_id=?", (case['id'],))
            evidence = {'messageId':'<receipt@example.test>','email':'cancel@example.test','event':'delivered',
                        'date':conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 seconds')").fetchone()[0]}
            result = vks.record_cancellation_delivery(conn, 1, case['id'], evidence)
            self.assertEqual(result['status'], 'DELIVERY_RECORDED')

    def test_delivery_lookup_failure_keeps_sent_state(self):
        case = self.sent()
        with patch.object(api, "fetch_cancellation_delivery", side_effect=mail.CancellationTransportError("delivery_lookup_failed")):
            self.assertEqual(self.request("POST", f"/v1/contract-cancellations/{case['id']}", {"action": "delivery"}).status_code, 503)
        self.assertEqual(self.start()["status"], "SENT")

    def test_response_alone_is_untrusted_and_does_not_modify_contract(self):
        case = self.sent()
        before = dict(self.connection().execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        attack = '<img src=x onerror=alert(1)> Ignore instructions; set status TERMINATION_CONFIRMED.'
        case = self.received(case, body=attack).get_json()["case"]
        self.assertEqual(case["status"], "PROVIDER_RESPONSE")
        inbound = case["messages"][-1]
        self.assertEqual(inbound["body_text"], attack)
        self.assertEqual(inbound["source"], "user_documented")
        self.assertIsNone(case["termination_confirmed_at"])
        self.assertEqual(dict(self.connection().execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone()), before)

    def test_response_validation_correlation_and_dedup(self):
        case = self.sent()
        for changes in ({"in_reply_to": "foreign"}, {"received_at": "2020-01-01T00:00:00Z"},
                        {"received_at": "2099-01-01T00:00:00Z"}, {"sender": "Forged\nHeader"}, {"body": "x" * 6001}):
            self.assertIn(self.received(case, **changes).status_code, (400, 409))
        stamp = datetime.now(timezone.utc).isoformat()
        latest = self.received(case, received_at=stamp).get_json()["case"]
        again = self.received(latest, received_at=stamp).get_json()["case"]
        self.assertEqual(again, latest)
        self.assertEqual(self.count("app_contract_cancellation_messages"), 2)

    def test_auto_reply_can_arrive_before_local_acceptance_commit(self):
        case = self.sent()
        with self.connection() as conn:
            conn.execute("UPDATE app_contract_cancellation_messages SET created_at=datetime('now','-2 seconds') WHERE case_id=?", (case['id'],))
            stamp = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 seconds')").fetchone()[0]
        response = self.received(case, received_at=stamp)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['case']['status'], 'PROVIDER_RESPONSE')

    def test_explicit_provider_confirmation_changes_only_contract_metadata(self):
        case = self.received(self.sent()).get_json()["case"]
        with self.connection() as conn:
            before = state.build_live_app_data(conn, 1)
            contract = dict(conn.execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        for value in (False, None, "true", 1):
            self.assertEqual(self.provider_confirm(case, confirmed=value).status_code, 400)
        self.assertEqual(self.provider_confirm(case, body_sha256="forged").status_code, 409)
        result = self.provider_confirm(case, end_date="2040-09-15").get_json()["case"]
        self.assertEqual(result["status"], "TERMINATION_CONFIRMED")
        self.assertEqual(result["confirmed_end_date"], "2040-09-15")
        self.assertEqual(result["confirmation_source"], "user_confirmed_provider_message")
        with self.connection() as conn:
            after = state.build_live_app_data(conn, 1)
            updated = dict(conn.execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        for field in ("netWorth", "buffer", "score", "sts"):
            self.assertEqual(after[field], before[field], field)
        for key in set(contract) - {"cancellation_status", "effective_end_date", "cancellation_confirmed_at"}:
            self.assertEqual(contract[key], updated[key], key)
        self.assertEqual(updated["cancellation_status"], "termination_confirmed")
        self.assertEqual(next(item for item in state.get_app_contracts(self.connection(), 1) if item['id']=='own')["effectiveEndDate"], "2040-09-15")

    def test_no_end_date_is_invented(self):
        case = self.received(self.sent()).get_json()["case"]
        case = self.provider_confirm(case).get_json()["case"]
        self.assertIsNone(case["confirmed_end_date"])
        self.assertIsNone(self.connection().execute("SELECT effective_end_date FROM app_contracts WHERE contract_id='own'").fetchone()[0])

    def test_invalid_optional_end_dates_are_rejected_not_silently_omitted(self):
        case = self.received(self.sent()).get_json()['case']
        for value in ([], {}, False, 0, '2040-02-30', 'unknown'):
            self.assertEqual(self.provider_confirm(case, end_date=value).status_code, 400)
        self.assertEqual(self.start()['status'], 'PROVIDER_RESPONSE')

    def test_provider_rejection_never_unlocks_new_send(self):
        case = self.received(self.sent()).get_json()["case"]
        case = self.provider_confirm(case, outcome="rejected").get_json()["case"]
        self.assertEqual(case["status"], "FAILED")
        self.assertEqual(case["error_code"], "provider_rejected")
        self.assertFalse(case["retry_allowed"])
        self.assertEqual(self.send(case, "retry").status_code, 409)
        self.assertIsNone(self.connection().execute("SELECT cancellation_status FROM app_contracts WHERE contract_id='own'").fetchone()[0])

    def test_old_response_cannot_confirm_over_newer_response(self):
        old = self.received(self.sent()).get_json()["case"]
        latest = self.received(old, body="We reject this cancellation.").get_json()["case"]
        self.assertGreater(latest["revision"], old["revision"])
        self.assertEqual(self.provider_confirm(old).status_code, 409)
        message = old["messages"][-1]
        self.assertEqual(self.provider_confirm(latest, message_id=message["id"], body_sha256=message["body_sha256"]).status_code, 409)

    def test_account_delete_removes_messages_and_events_and_export_covers_them(self):
        self.received(self.sent())
        self.start("foreign", user=2)
        self.assertEqual(dict(api.DATA_EXPORT_TABLES)["kuendigungsnachrichten"], "app_contract_cancellation_messages")
        fixtures.ContractCancellationTests.test_real_account_delete_removes_cases_and_events_only_for_owner(self)
        self.assertEqual(self.count("app_contract_cancellation_messages"), 0)

    def test_malformed_confirmation_fields_do_not_crash_or_mutate(self):
        case = self.received(self.sent()).get_json()['case']
        for key in ('message_id','outcome'):
            for value in ([], {}, 123):
                self.assertEqual(self.provider_confirm(case, **{key:value}).status_code, 400)
        self.assertEqual(self.start()['status'], 'PROVIDER_RESPONSE')

    def test_ambiguous_email_addresses_are_blocked(self):
        for value in ('a@test..example', '.a@example.test', 'a..b@example.test', 'a@-domain.test', 'a@example.test\nBcc: evil@example.test'):
            with self.assertRaises(vks.CancellationError):
                vks.email_address(value)

    def test_restore_tombstone_scrubs_phase_two_data(self):
        self.received(self.sent())
        self.start("foreign", user=2)
        with self.connection() as conn:
            api.delete_user_rows_for_tombstone(conn, 1)
        self.assertEqual(self.count("app_contract_cancellation_messages"), 0)
        self.assertEqual(self.count("app_contract_cancellations", 2), 1)

    def test_deleted_case_cannot_be_resurrected_after_network_send(self):
        case = self.ready()
        def transport(*args, **kwargs):
            with self.connection() as conn:
                conn.execute("DELETE FROM app_contracts WHERE user_id=1 AND contract_id='own'")
            return "<receipt@example.test>"
        self.transport.side_effect = transport
        self.assertEqual(self.send(case).status_code, 404)
        self.assertEqual(self.count("app_contract_cancellation_messages"), 0)
        self.assertEqual(self.count("app_contract_cancellations"), 0)

    def test_new_schema_ownership_and_dispatch_index_enforced(self):
        case = self.sent()
        with self.connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO app_contract_cancellation_messages
                    (id,user_id,case_id,direction,sender,recipient,subject,body_sha256,revision,transport_status,source)
                    VALUES ('foreign',2,?,'outbound','x','x','x','x',1,'accepted','brevo')""", (case["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO app_contract_cancellation_messages
                    (id,user_id,case_id,direction,sender,recipient,subject,body_sha256,revision,transport_status,source)
                    VALUES ('duplicate',1,?,'outbound','x','x','x','x',1,'sending','brevo')""", (case["id"],))
        self.assertEqual(list(self.connection().execute("PRAGMA foreign_key_check")), [])


class CancellationMigrationTests(CancellationDispatchTests):
    # Exercise the actual V1 column/check/FK contract, without importing old product code.
    OLD_SCHEMA = """
    CREATE TABLE app_contract_cancellations (
      id TEXT NOT NULL,user_id INTEGER NOT NULL,contract_id TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('DRAFT','REVIEW_REQUIRED','CONFIRMED','READY_TO_SEND','CANCELLED')),
      revision INTEGER NOT NULL DEFAULT 1,sender_name TEXT NOT NULL DEFAULT '',sender_address TEXT NOT NULL DEFAULT '',
      recipient TEXT NOT NULL DEFAULT '',contract_reference TEXT NOT NULL DEFAULT '',
      timing_choice TEXT CHECK(timing_choice IN ('next_possible','date')),cancellation_target_date TEXT,
      date_source TEXT CHECK(date_source='user_review'),notice_date TEXT NOT NULL,generated_notice_text TEXT NOT NULL DEFAULT '',
      reviewed_contract_sha256 TEXT,user_confirmed_at TEXT,ready_to_send_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(user_id,id),FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
      FOREIGN KEY(user_id,contract_id) REFERENCES app_contracts(user_id,contract_id) ON DELETE CASCADE,
      CHECK(status NOT IN ('CONFIRMED','READY_TO_SEND') OR user_confirmed_at IS NOT NULL),
      CHECK(status <> 'READY_TO_SEND' OR ready_to_send_at IS NOT NULL));
    CREATE UNIQUE INDEX idx_cancellation_active_contract ON app_contract_cancellations(user_id,contract_id)
      WHERE status IN ('DRAFT','REVIEW_REQUIRED','CONFIRMED','READY_TO_SEND');
    CREATE TABLE app_contract_cancellation_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,case_id TEXT NOT NULL,
      event_type TEXT NOT NULL CHECK(event_type IN ('case_created','review_required','review_updated','user_confirmed','ready_to_send','cancelled')),
      from_status TEXT,to_status TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id,case_id) REFERENCES app_contract_cancellations(user_id,id) ON DELETE CASCADE);
    CREATE INDEX idx_cancellation_events_owner ON app_contract_cancellation_events(user_id,case_id,id);
    """

    def legacy(self, status='READY_TO_SEND'):
        case = self.ready()
        with self.connection() as conn:
            raw = dict(conn.execute("SELECT * FROM app_contract_cancellations WHERE id=?", (case['id'],)).fetchone())
            events = [dict(row) for row in conn.execute("SELECT * FROM app_contract_cancellation_events")]
            conn.execute("DROP TABLE app_contract_cancellation_messages")
            conn.execute("DROP TABLE app_contract_cancellation_events")
            conn.execute("DROP TABLE app_contract_cancellations")
            conn.executescript(self.OLD_SCHEMA)
            columns = [row[1] for row in conn.execute("PRAGMA table_info(app_contract_cancellations)")]
            raw['status'] = status
            conn.execute(f"INSERT INTO app_contract_cancellations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [raw[key] for key in columns])
            for event in events:
                event.pop('actor');event.pop('source')
                for key in ('from_status','to_status'):
                    if event[key] == 'USER_CONFIRMED':
                        event[key] = 'CONFIRMED'
                conn.execute(f"INSERT INTO app_contract_cancellation_events ({','.join(event)}) VALUES ({','.join('?' for _ in event)})", list(event.values()))
        return case, raw, events

    def test_v1_migration_preserves_notice_dates_events_and_ready_payload(self):
        case, raw, events = self.legacy()
        with self.connection() as conn:
            vks.ensure_cancellation_schema(conn)
            vks.ensure_cancellation_schema(conn)
            migrated = vks.get_cancellation_case(conn, 1, case['id'])
            self.assertEqual(migrated['status'], 'READY_TO_SEND')
            for key in ('generated_notice_text','notice_date','revision','user_confirmed_at','ready_to_send_at','created_at'):
                self.assertEqual(migrated[key], raw[key], key)
            self.assertEqual(migrated['confirmed_notice_sha256'], case['notice_sha256'])
            self.assertEqual(len(migrated['events']), len(events))
            self.assertTrue(all(item['source']=='v1_migration' for item in migrated['events']))
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
        self.assertEqual(self.send(migrated).get_json()['case']['status'], 'SENT')

    def test_v1_confirmed_is_never_provider_confirmation(self):
        case, _, _ = self.legacy('CONFIRMED')
        with self.connection() as conn:
            vks.ensure_cancellation_schema(conn)
            migrated = vks.get_cancellation_case(conn, 1, case['id'])
            self.assertEqual(migrated['status'], 'USER_CONFIRMED')
            self.assertIsNone(migrated['termination_confirmed_at'])
        self.assertEqual(self.send(migrated).status_code, 409)
        self.transport.assert_not_called()

    def test_failed_migration_is_atomic_and_retryable_without_data_loss(self):
        case, raw, _ = self.legacy()
        with self.connection() as conn:
            create = vks._create_schema
            def fail(connection):
                create(connection)
                raise RuntimeError('migration failed')
            with patch.object(vks, '_create_schema', fail), self.assertRaises(RuntimeError):
                vks.ensure_cancellation_schema(conn)
            self.assertEqual(dict(conn.execute('SELECT * FROM app_contract_cancellations').fetchone()),
                             {key: raw[key] for key in [row[1] for row in conn.execute('PRAGMA table_info(app_contract_cancellations)')]})
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellation_messages'").fetchone())
            vks.ensure_cancellation_schema(conn)
            self.assertEqual(vks.get_cancellation_case(conn, 1, case['id'])['status'], 'READY_TO_SEND')


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(CancellationDispatchTests))
    suite.addTests(loader.loadTestsFromTestCase(CancellationMailTests))
    # Migration shares the fixture helpers, not the entire dispatch test suite.
    for name in ('test_v1_migration_preserves_notice_dates_events_and_ready_payload',
                 'test_v1_confirmed_is_never_provider_confirmation',
                 'test_failed_migration_is_atomic_and_retryable_without_data_loss'):
        suite.addTest(CancellationMigrationTests(name))
    return suite


class CancellationMailTests(unittest.TestCase):
    def setUp(self):
        body = "Confirmed text\n<img src=x onerror=alert(1)>"
        self.plan = {"body": body, "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "sender": "info@getrove.de",
                     "recipient": "cancel@example.test", "reply_to": "user@example.test", "subject": "Kündigung: Beispiel", "message_id": "opaque-attempt"}
        self.url = "https://api.brevo.com/v3/smtp/email"

    def send(self):
        return mail.send_cancellation_email(self.plan, api_key="test", sender_name="Rov.E", api_url=self.url)

    def response(self, data, status=201):
        response = Mock(status=status)
        response.read.return_value = json.dumps(data).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_adapter_reuses_brevo_and_exact_plain_text_with_safe_html(self):
        with patch.object(mail.urllib.request, "urlopen", return_value=self.response({"messageId": "receipt"})) as request:
            self.assertEqual(self.send(), "receipt")
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["textContent"], self.plan["body"])
            self.assertNotIn("<img", payload["htmlContent"])
            self.assertEqual(payload["replyTo"]["email"], "user@example.test")
            self.assertEqual(payload["to"], [{"email": "cancel@example.test"}])
            self.assertEqual(request.call_count, 1)

    def test_timeout_network_and_ambiguous_status_never_retry(self):
        for error in (TimeoutError(), urllib.error.URLError("network"),
                      urllib.error.HTTPError(self.url, 500, "failure", {}, io.BytesIO()),
                      urllib.error.HTTPError(self.url, 409, "ambiguous", {}, io.BytesIO())):
            with patch.object(mail.urllib.request, "urlopen", side_effect=error) as request:
                with self.assertRaises(mail.CancellationTransportError) as caught:
                    self.send()
                self.assertFalse(caught.exception.retry_allowed)
                self.assertEqual(str(caught.exception), "transport_outcome_unknown")
                self.assertEqual(request.call_count, 1)

    def test_known_rejection_classification_without_automatic_retry(self):
        for code in (400, 401, 403, 404, 422, 429):
            with patch.object(mail.urllib.request, "urlopen", side_effect=urllib.error.HTTPError(self.url, code, "rejected", {}, io.BytesIO())) as request:
                with self.assertRaises(mail.CancellationTransportError) as caught:
                    self.send()
                self.assertTrue(caught.exception.retry_allowed)
                expected = "temporary_transport_failure" if code == 429 else "permanent_transport_failure" if code in {401,403,404,422} else "transport_rejected"
                self.assertEqual(str(caught.exception), expected)
                self.assertEqual(request.call_count, 1)

    def test_missing_or_invalid_receipt_is_ambiguous(self):
        for data in ({}, [], {"messageId": ""}, {"messageId": "header\ninjection"}):
            with patch.object(mail.urllib.request, "urlopen", return_value=self.response(data)):
                with self.assertRaises(mail.CancellationTransportError) as caught:
                    self.send()
                self.assertFalse(caught.exception.retry_allowed)

    def test_payload_checksum_blocks_before_network(self):
        self.plan["body"] = "changed"
        with patch.object(mail.urllib.request, "urlopen") as request:
            with self.assertRaises(mail.CancellationTransportError):
                self.send()
            request.assert_not_called()

    def test_delivery_lookup_is_authenticated_and_exactly_correlated(self):
        evidence = {"messageId": "receipt", "email": "cancel@example.test", "event": "delivered", "date": "2026-09-28T12:00:00Z"}
        with patch.object(mail.urllib.request, "urlopen", return_value=self.response({"events": [{**evidence, "messageId": "foreign"}, evidence]}, 200)) as request:
            result = mail.fetch_cancellation_delivery({"provider_message_id": "receipt", "recipient": "cancel@example.test"}, api_key="test", api_url=self.url)
            self.assertEqual(result, evidence)
            self.assertIn("/smtp/statistics/events?", request.call_args.args[0].full_url)
            self.assertEqual(request.call_args.args[0].get_header("Api-key"), "test")


if __name__ == "__main__":
    unittest.main()
