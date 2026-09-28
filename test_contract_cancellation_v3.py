"""VKS follow-up, private evidence file and restricted operator mail gates."""
import hashlib
import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
import rove_contract_cancellation as vks
import rove_contract_cancellation_pdf as pdf
import rove_report_worker as worker
from test_contract_cancellation_v2 import CancellationDispatchTests


class CancellationFinalizationTests(CancellationDispatchTests):
    def old_sent(self, days=15):
        case = self.sent()
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        with self.connection() as conn:
            conn.execute("UPDATE app_contract_cancellation_messages SET sent_at=?,created_at=? WHERE case_id=?",
                         (stamp, stamp, case['id']))
            case = vks.get_cancellation_case(conn, 1, case['id'])
        return case

    def maintain(self):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count = vks.refresh_cancellation_reminders(conn)
            conn.commit()
        return count

    def test_reminder_14_day_boundary_and_no_invented_send_date(self):
        case = self.sent()
        stamp = datetime.fromisoformat(case['messages'][0]['sent_at']).replace(tzinfo=timezone.utc)
        self.assertFalse(vks.cancellation_reminder(case, now=stamp + timedelta(days=14, seconds=-1))['due'])
        self.assertTrue(vks.cancellation_reminder(case, now=stamp + timedelta(days=14))['due'])
        self.assertFalse(vks.cancellation_reminder({**case, 'status': 'READY_TO_SEND', 'messages': []})['due'])

    def test_maintenance_marks_due_once_and_never_dispatches(self):
        case = self.old_sent()
        self.assertEqual(self.maintain(), 1)
        self.assertEqual(self.maintain(), 0)
        latest = self.start()
        self.assertEqual(latest['status'], 'FOLLOW_UP_DUE')
        self.assertGreater(latest['revision'], case['revision'])
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(sum(event['event_type'] == 'follow_up_due' for event in latest['events']), 1)

    def test_closed_failed_and_any_documented_response_stop_reminders(self):
        case = self.received(self.old_sent()).get_json()['case']
        self.assertEqual(self.maintain(), 0)
        self.assertFalse(vks.cancellation_reminder(case)['due'])
        confirmed = self.provider_confirm(case).get_json()['case']
        self.assertEqual(self.maintain(), 0)
        self.assertFalse(vks.cancellation_reminder(confirmed)['due'])
        for status in ('FAILED', 'CANCELLED', 'MANUAL_REVIEW_REQUIRED'):
            self.assertFalse(vks.cancellation_reminder({**case, 'status': status})['due'])

    def test_no_reminder_for_unknown_or_rejected_transport(self):
        from rove_contract_cancellation_mail import CancellationTransportError
        self.transport.side_effect = CancellationTransportError('transport_outcome_unknown')
        case = self.send(self.ready()).get_json()['case']
        self.assertEqual(self.maintain(), 0)
        self.assertIsNone(case['reminder']['due_at'])

    def test_followup_is_bound_to_original_and_cannot_send_again(self):
        case = self.old_sent()
        original = case['generated_notice_text'], case['confirmed_notice_sha256'], case['recipient']
        with self.connection() as conn:
            conn.execute("UPDATE app_contracts SET name='Renamed contract' WHERE contract_id='own'")
        prepared = self.action(case, 'followup').get_json()['case']
        self.assertEqual(prepared['status'], 'FOLLOW_UP_PREPARED')
        followup = prepared['followups'][0]
        self.assertIn('Beispielanbieter', followup['body_text'])
        self.assertNotIn('Renamed contract', followup['body_text'])
        self.assertIn('zum nächstmöglichen Zeitpunkt', followup['body_text'])
        self.assertIn(datetime.fromisoformat(case['messages'][0]['sent_at']).strftime('%d.%m.%Y'), followup['body_text'])
        self.assertEqual(followup['body_sha256'], hashlib.sha256(followup['body_text'].encode()).hexdigest())
        self.assertEqual((prepared['generated_notice_text'], prepared['confirmed_notice_sha256'], prepared['recipient']), original)
        self.assertEqual(self.action(prepared, 'followup').get_json()['case'], prepared)
        self.assertEqual(self.send(prepared).status_code, 200)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.maintain(), 0)

    def test_followup_explicit_original_date_and_reference_are_preserved(self):
        case = self.ready()
        self.action(case, 'cancel')
        case = self.review(self.start(), recipient='cancel@example.test', timing_choice='date',
                           cancellation_target_date='2040-09-15', contract_reference='REF-123')
        case = self.confirm(case).get_json()['case']
        case = self.send(case).get_json()['case']
        stamp = (datetime.now(timezone.utc) - timedelta(days=15)).strftime('%Y-%m-%d %H:%M:%S')
        with self.connection() as conn:
            conn.execute('UPDATE app_contract_cancellation_messages SET sent_at=?,created_at=? WHERE case_id=?', (stamp,stamp,case['id']))
        case = self.action(case, 'followup').get_json()['case']
        self.assertIn('zum 15.09.2040', case['followups'][0]['body_text'])
        self.assertIn('REF-123', case['followups'][0]['body_text'])

    def test_followup_not_before_due_or_after_answer_or_manual_review_due(self):
        case = self.sent()
        self.assertEqual(self.action(case, 'followup').status_code, 409)
        case = self.received(case).get_json()['case']
        self.assertEqual(self.action(case, 'followup').status_code, 409)
        with self.connection() as conn:
            case = dict(conn.execute('SELECT * FROM app_contract_cancellations').fetchone())
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM app_contract_cancellation_followups').fetchone()[0], 0)

    def test_28_days_marks_manual_review_not_provider_default(self):
        case = self.old_sent(29)
        self.assertEqual(self.action(case, 'followup').status_code, 409)
        self.assertEqual(self.maintain(), 1)
        case = self.start()
        self.assertEqual(case['status'], 'MANUAL_REVIEW_REQUIRED')
        self.assertFalse(case['reminder']['due'])
        self.assertIsNone(case['termination_confirmed_at'])
        self.assertEqual(self.transport.call_count, 1)
        response = self.received(case).get_json()['case']
        self.assertEqual(response['status'], 'PROVIDER_RESPONSE')
        self.assertEqual(self.provider_confirm(response).get_json()['case']['status'], 'TERMINATION_CONFIRMED')

    def test_manual_review_on_unclear_response_does_not_change_contract(self):
        case = self.received(self.sent(), body='Bitte senden Sie uns weitere Angaben.').get_json()['case']
        before = dict(self.connection().execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone())
        latest = self.action(case, 'manual_review').get_json()['case']
        self.assertEqual(latest['status'], 'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(dict(self.connection().execute("SELECT * FROM app_contracts WHERE contract_id='own'").fetchone()), before)
        self.assertEqual(self.provider_confirm(latest).get_json()['case']['status'], 'TERMINATION_CONFIRMED')

    def test_manual_review_never_interrupts_pending_transport(self):
        case = self.ready()
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            vks.prepare_cancellation_send(conn, 1, case['id'], expected_revision=case['revision'],
                                          notice_sha256=case['notice_sha256'], confirmed=True, sender='info@getrove.de')
        self.assertEqual(self.action(case, 'manual_review').status_code, 409)
        self.assertEqual(self.start()['status'], 'SENDING')

    def test_maintenance_is_bounded_and_tolerates_pre_vks_schema(self):
        self.old_sent()
        with self.connection() as conn:
            self.assertEqual(vks.refresh_cancellation_reminders(conn, limit=0), 0)
        with sqlite3.connect(':memory:') as conn:
            self.assertEqual(vks.refresh_cancellation_reminders(conn), 0)

    def test_existing_daily_maintenance_runs_the_reminder_without_mail(self):
        self.old_sent()
        with patch.object(worker, 'DB_PATH', self.path), patch.object(worker, 'cleanup_auth_artifacts', return_value={}), \
             patch.object(worker.account_delete_cleanup, 'retry_paths', return_value=0), \
             patch.object(worker, 'report_renderer_module') as renderer, patch.object(worker, 'report_engine_module') as engine:
            renderer.return_value.cleanup_expired_reports.return_value = 0
            engine.return_value.archive_old_reports.return_value = 0
            self.assertEqual(worker.maintain_archives()['vks_reminders_updated'], 1)
        self.assertEqual(self.transport.call_count, 1)

    def test_private_case_file_is_complete_deterministic_and_has_no_owner_internals(self):
        case = self.action(self.old_sent(), 'followup').get_json()['case']
        case = self.received(case).get_json()['case']
        case = self.provider_confirm(case, end_date='2040-09-15').get_json()['case']
        endpoint = f"/v1/contract-cancellations/{case['id']}/file"
        result = self.request('GET', endpoint).get_json()['case_file']
        self.assertEqual(result, self.request('GET', endpoint).get_json()['case_file'])
        self.assertEqual(result['status_label'], 'Kündigung bestätigt')
        self.assertTrue(result['notice_integrity'])
        self.assertEqual(result['notice_text'], case['generated_notice_text'])
        self.assertEqual(len(result['messages']), 2)
        self.assertEqual(len(result['followups']), 1)
        self.assertTrue(result['timeline'])
        self.assertNotIn('user_id', result)
        self.assertNotIn('confirmed_payload_sha256', result)

    def test_pdf_is_private_deterministic_and_contains_only_documented_facts(self):
        case = self.received(self.sent(), body='Noch kein Enddatum genannt.').get_json()['case']
        case = self.provider_confirm(case).get_json()['case']
        endpoint = f"/v1/contract-cancellations/{case['id']}/pdf"
        from reportlab.platypus import Paragraph
        with patch('reportlab.platypus.Paragraph', wraps=Paragraph) as paragraphs:
            response = self.request('GET', endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertEqual(response.data, self.request('GET', endpoint).data)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('attachment', response.headers['Content-Disposition'])
        text = '\n'.join(call.args[0] for call in paragraphs.call_args_list)
        for phrase in ('Kündigungsakte', 'Kündigung bestätigt', 'Nicht mitgeteilt', 'Noch kein Enddatum genannt.', 'Transport-ID'):
            self.assertIn(phrase, text)
        self.assertNotIn('TERMINATION_CONFIRMED', text)
        self.assertNotIn('2030-', text)

    def test_case_file_prefers_event_timestamps_and_falls_back_to_storage_time(self):
        case = self.received(self.sent()).get_json()['case']
        outbound = case['messages'][0]
        inbound = case['messages'][1]
        with self.connection() as conn:
            conn.execute("UPDATE app_contract_cancellation_messages SET sent_at=?,created_at=? WHERE id=?",
                         ('2026-08-15T11:00:00+00:00', '2026-09-28 20:00:00', outbound['id']))
            conn.execute("UPDATE app_contract_cancellation_messages SET received_at=?,created_at=? WHERE id=?",
                         ('2026-08-15T12:30:00+00:00', '2026-09-27 20:00:00', inbound['id']))
            file = vks.build_cancellation_file(conn, 1, case['id'])
        sent_event = next(event for event in file['timeline'] if event['event_type'] == 'sent')
        response_event = next(event for event in file['timeline'] if event['event_type'] == 'response_received')
        self.assertEqual(sent_event['occurred_at'], '2026-08-15T11:00:00+00:00')
        self.assertEqual(response_event['occurred_at'], '2026-08-15T12:30:00+00:00')
        self.assertLess(file['timeline'].index(sent_event), file['timeline'].index(response_event))
        with self.connection() as conn:
            conn.execute("UPDATE app_contract_cancellation_messages SET sent_at=NULL WHERE id=?", (outbound['id'],))
            fallback = vks.build_cancellation_file(conn, 1, case['id'])
        sent_fallback = next(event for event in fallback['timeline'] if event['event_type'] == 'sent')
        self.assertEqual(sent_fallback['occurred_at'], '2026-09-28 20:00:00')

    def test_pdf_dates_use_german_format_and_berlin_time(self):
        self.assertEqual(pdf.format_date('2040-09-15'), '15.09.2040')
        self.assertEqual(pdf.format_date(None), 'Nicht mitgeteilt')
        self.assertEqual(pdf.format_timestamp('2026-09-28T12:00:00Z'), '28.09.2026, 14:00 Uhr')
        self.assertEqual(pdf.format_timestamp('2026-01-15 12:00:00'), '15.01.2026, 13:00 Uhr')

    def test_pdf_reply_to_is_included_only_when_transport_stored_it(self):
        case = self.sent()
        with self.connection() as conn:
            message = conn.execute("SELECT * FROM app_contract_cancellation_messages WHERE case_id=?", (case['id'],)).fetchone()
            case_file = vks.build_cancellation_file(conn, 1, case['id'])
        from reportlab.platypus import Paragraph
        if message['reply_to']:
            with patch('reportlab.platypus.Paragraph', wraps=Paragraph) as paragraphs:
                pdf.render_cancellation_pdf(case_file)
            self.assertIn(f"Reply-To: {message['reply_to']}",
                          '\n'.join(call.args[0] for call in paragraphs.call_args_list))
        case_file['messages'][0]['reply_to'] = None
        with patch('reportlab.platypus.Paragraph', wraps=Paragraph) as paragraphs:
            pdf.render_cancellation_pdf(case_file)
        rendered = '\n'.join(call.args[0] for call in paragraphs.call_args_list)
        self.assertNotIn('Reply-To:', rendered)
        self.assertIn('Text-Prüfsumme (SHA-256):', rendered)
        self.assertIn('Erstellt am:', rendered)
        self.assertIn(f"Vorgangs-ID: {case['id']}", rendered)

    def test_pdf_preserves_german_umlauts_in_provider_contract_notice_and_reply(self):
        case = self.received(self.sent(), body='Wir bestätigen die Kündigung für Ölstraße 12. Grüße, Müller.') \
            .get_json()['case']
        with self.connection() as conn:
            case_file = vks.build_cancellation_file(conn, 1, case['id'])
            case_file['provider'] = 'ÄÖÜ Anbieter GmbH'
            case_file['contract'] = 'Schöne Straße – Ökostrom'
            case_file['notice_text'] = 'Kündigung für ä ö ü Ä Ö Ü ß.'
            case_file['messages'][1]['body_text'] = 'Antwort: ä ö ü Ä Ö Ü ß.'
            from reportlab.platypus import Paragraph
            with patch('reportlab.platypus.Paragraph', wraps=Paragraph) as paragraphs:
                payload = pdf.render_cancellation_pdf(case_file)
        self.assertTrue(payload.startswith(b'%PDF-'))
        rendered = '\n'.join(call.args[0] for call in paragraphs.call_args_list)
        for phrase in ('ÄÖÜ Anbieter GmbH', 'Schöne Straße – Ökostrom',
                       'Kündigung für ä ö ü Ä Ö Ü ß.', 'Antwort: ä ö ü Ä Ö Ü ß.'):
            self.assertIn(phrase, rendered)

    def test_pdf_handles_long_untrusted_response_without_fetching_resources(self):
        case = self.received(self.sent(), body='<img src="https://evil.test/x">\n' + 'A' * 5700).get_json()['case']
        with self.connection() as conn:
            payload = pdf.render_cancellation_pdf(vks.build_cancellation_file(conn, 1, case['id']))
        self.assertTrue(payload.startswith(b'%PDF-'))

    def test_file_pdf_history_user_isolation_and_pin(self):
        case = self.sent()
        for suffix in ('file', 'pdf'):
            endpoint = f"/v1/contract-cancellations/{case['id']}/{suffix}"
            self.assertEqual(self.request('GET', endpoint, user=2).status_code, 404)
            self.assertEqual(self.request('GET', endpoint, user=None).status_code, 401)
        with self.connection() as conn:
            conn.execute('UPDATE app_session_pins SET unlocked_at=NULL')
        self.assertEqual(self.request('GET', f"/v1/contract-cancellations/{case['id']}/pdf").status_code, 423)

    def test_account_delete_and_tombstone_remove_followups_and_disable_download(self):
        case = self.action(self.old_sent(), 'followup').get_json()['case']
        with self.connection() as conn:
            api.delete_user_rows_for_tombstone(conn, 1)
        self.assertEqual(self.count('app_contract_cancellation_followups'), 0)
        self.assertEqual(self.count('app_contract_cancellation_messages'), 0)
        self.assertEqual(self.request('GET', f"/v1/contract-cancellations/{case['id']}/pdf").status_code, 401)

    def test_deletion_during_pdf_render_does_not_return_artifact(self):
        case = self.sent()
        def deleted(case_file):
            with self.connection() as conn:
                conn.execute("DELETE FROM app_contracts WHERE contract_id='own' AND user_id=1")
            return b'%PDF-no-release'
        with patch.object(pdf, 'render_cancellation_pdf', side_effect=deleted):
            self.assertEqual(self.request('GET', f"/v1/contract-cancellations/{case['id']}/pdf").status_code, 404)

    def test_contract_end_display_is_server_derived_and_never_changes_plan(self):
        case = self.received(self.sent()).get_json()['case']
        with self.connection() as conn:
            before = state.build_live_app_data(conn, 1)
        case = self.provider_confirm(case, end_date='2020-01-01').get_json()['case']
        with self.connection() as conn:
            after = state.build_live_app_data(conn, 1)
            contract = next(item for item in state.get_app_contracts(conn, 1) if item['id'] == 'own')
        self.assertTrue(case['contract_ended'])
        self.assertTrue(contract['cancellationEnded'])
        for field in ('netWorth', 'buffer', 'score', 'sts'):
            self.assertEqual(before[field], after[field], field)
        self.assertEqual(contract['a'], 50)

    def test_live_mail_needs_separate_operator_approval(self):
        case = self.ready()
        with patch.object(api, 'VKS_LIVE_APPROVED', False):
            self.assertEqual(self.send(case).get_json()['error'], 'cancellation_live_not_approved')
        self.transport.assert_not_called()
        self.assertEqual(self.count('app_contract_cancellation_messages'), 0)

    def test_internal_e2e_only_exact_test_user_contract_and_recipient_can_send(self):
        with patch.object(api, 'VKS_MAIL_MODE', 'test'), patch.object(api, 'VKS_TEST_USER_ID', '1'), \
             patch.object(api, 'VKS_TEST_CONTRACT_ID', 'own'), patch.object(api, 'VKS_TEST_RECIPIENT', 'cancel@example.test'):
            case = self.ready()
            self.assertEqual(self.send(case).get_json()['error'], 'cancellation_test_target_required')
            self.action(case, 'cancel')
            with self.connection() as conn:
                conn.execute("UPDATE app_contracts SET name='[VKS TEST] Interner Testvertrag' WHERE contract_id='own'")
            case = self.ready()
            with patch.object(api, 'VKS_TEST_RECIPIENT', 'other@example.test'):
                self.assertEqual(self.send(case).status_code, 503)
            with patch.object(api, 'VKS_TEST_USER_ID', '2'):
                self.assertEqual(self.send(case).status_code, 503)
            with patch.object(api, 'VKS_TEST_CONTRACT_ID', 'foreign'):
                self.assertEqual(self.send(case).status_code, 503)
            self.assertEqual(self.send(case).get_json()['case']['status'], 'SENT')
        self.assertEqual(self.transport.call_count, 1)

    def test_unconfigured_test_or_unknown_mode_never_dispatches(self):
        case = self.ready()
        with patch.object(api, 'VKS_MAIL_MODE', 'test'), patch.object(api, 'VKS_TEST_RECIPIENT', ''):
            self.assertEqual(self.send(case).get_json()['error'], 'cancellation_test_not_configured')
        with patch.object(api, 'VKS_MAIL_MODE', 'invalid'):
            self.assertEqual(self.send(case).get_json()['error'], 'cancellation_mail_mode_invalid')
        self.transport.assert_not_called()

    def test_real_account_delete_removes_prepared_followup(self):
        case = self.action(self.old_sent(), 'followup').get_json()['case']
        from test_contract_cancellation_v1 import ContractCancellationTests
        ContractCancellationTests.test_real_account_delete_removes_cases_and_events_only_for_owner(self)
        self.assertEqual(self.count('app_contract_cancellation_followups'), 0)
        self.assertEqual(self.count('app_contract_cancellation_messages'), 0)
        self.assertEqual(dict(api.DATA_EXPORT_TABLES)['kuendigungsnachfragen'], 'app_contract_cancellation_followups')

    def test_stale_revision_does_not_prepare_or_confirm_over_reminder(self):
        case = self.old_sent()
        self.maintain()
        self.assertEqual(self.action(case, 'followup').status_code, 409)
        self.assertEqual(self.count('app_contract_cancellation_followups'), 0)

    def test_missing_original_evidence_blocks_followup_with_clear_manual_action(self):
        case = self.old_sent()
        with self.connection() as conn:
            conn.execute('UPDATE app_contract_cancellations SET confirmed_provider_name=NULL WHERE id=?', (case['id'],))
        response = self.action(case, 'followup')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error'], 'cancellation_manual_review_required')
        self.assertEqual(self.count('app_contract_cancellation_followups'), 0)

    def downgrade_fixture_to_v2(self):
        with self.connection() as conn:
            tables = ('app_contract_cancellations', 'app_contract_cancellation_events', 'app_contract_cancellation_messages')
            schemas, records = {}, {}
            for table in tables:
                schemas[table] = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
                records[table] = [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            schemas[tables[0]] = re.sub(r",\s*'(?:FOLLOW_UP_DUE|FOLLOW_UP_PREPARED|MANUAL_REVIEW_REQUIRED)'", '', schemas[tables[0]])
            schemas[tables[0]] = schemas[tables[0]].replace('confirmed_provider_name TEXT,', '')
            schemas[tables[1]] = schemas[tables[1]].replace(", 'follow_up_due'", '')  # SQL may contain no whitespace.
            schemas[tables[1]] = re.sub(r",\s*'(?:follow_up_due|follow_up_prepared|manual_review_required)'", '', schemas[tables[1]])
            conn.execute('DROP TABLE app_contract_cancellation_followups')
            for table in reversed(tables):
                conn.execute(f'DROP TABLE {table}')
            for table in tables:
                conn.execute(schemas[table])
                for row in records[table]:
                    if table == tables[0]:
                        row.pop('confirmed_provider_name')
                    conn.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
            conn.commit()
        return records

    def test_v2_migration_preserves_dispatch_lock_messages_events_and_no_new_send(self):
        case = self.received(self.sent()).get_json()['case']
        records = self.downgrade_fixture_to_v2()
        with self.connection() as conn:
            vks.ensure_cancellation_schema(conn)
            vks.ensure_cancellation_schema(conn)
            latest = vks.get_cancellation_case(conn, 1, case['id'])
            self.assertEqual(latest['messages'], case['messages'])
            self.assertEqual(latest['events'], case['events'])
            self.assertEqual(latest['confirmed_provider_name'], 'Beispielanbieter')
            self.assertEqual(latest['confirmed_notice_sha256'], case['confirmed_notice_sha256'])
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
        self.assertEqual(self.send(latest).status_code, 200)
        self.assertEqual(self.transport.call_count, 1)

    def test_v2_migration_failure_restores_original_checks_and_evidence(self):
        self.sent()
        before = self.downgrade_fixture_to_v2()
        original_create = vks._create_schema
        def fail(conn):
            original_create(conn)
            raise RuntimeError('Controlled migration failure')
        with self.connection() as conn:
            with patch.object(vks, '_create_schema', side_effect=fail), self.assertRaises(RuntimeError):
                vks.ensure_cancellation_schema(conn)
            for table, records in before.items():
                self.assertEqual([dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')], records)
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellation_followups'").fetchone())
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
            vks.ensure_cancellation_schema(conn)

    def test_reminder_maintenance_and_prepare_parallel_paths_have_one_record(self):
        case = self.old_sent()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.action(case, 'followup'), range(4)))
        self.assertEqual(sum(response.status_code == 200 for response in responses), 1)
        self.assertEqual(self.count('app_contract_cancellation_followups'), 1)
        self.assertEqual(self.transport.call_count, 1)


def load_tests(loader, tests, pattern):
    # Reuse V2 fixtures/helpers, but run only Phase-3-specific tests here.
    return unittest.TestSuite(CancellationFinalizationTests(name) for name in
                             sorted(CancellationFinalizationTests.__dict__) if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
