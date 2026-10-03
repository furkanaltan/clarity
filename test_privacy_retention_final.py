import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import report_engine
import report_html_renderer
import rove_account_delete_cleanup as cleanup
import rove_app_api as api
import rove_report_worker as worker
import rove_web_report_renderer as web_renderer


class PrivacyRetentionFinalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "clarity.db"

    def tearDown(self):
        self.tmp.cleanup()

    def create_users(self, *user_ids):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
            conn.executemany("INSERT INTO users VALUES (?)", [(uid,) for uid in user_ids])

    def test_account_delete_removes_only_owned_generated_html_and_preview(self):
        generated = self.root / "generated"
        generated.mkdir()
        html_a = generated / "clarity_report_1_2026-08.html"
        html_b = generated / "clarity_report_2_2026-08.html"
        unrelated = generated / "template-preview.html"
        html_a.write_text("<!-- rove-preview-owner:1 -->A", encoding="utf-8")
        html_b.write_text("<!-- rove-preview-owner:2 -->B", encoding="utf-8")
        unrelated.write_text("shared template", encoding="utf-8")
        preview = generated / "latest_preview.html"
        preview.write_text("<!-- rove-preview-owner:2 -->B", encoding="utf-8")

        state = self.root / "state"; state.mkdir()
        public = self.root / "public"; public.mkdir()
        reports = self.root / "reports"; reports.mkdir()
        archive = reports / "archive"; archive.mkdir()
        with patch.object(api, "PUBLIC_APP_STATE_DIR", state), \
             patch.object(api, "PUBLIC_REPORT_DIR", public), \
             patch.object(api, "REPORTS_DIR", reports), \
             patch.object(api, "REPORTS_ARCHIVE_DIR", archive), \
             patch.object(api, "GENERATED_REPORT_DIR", generated):
            errors = api.remove_deleted_account_files(1, [], [])

        self.assertEqual(errors, [])
        self.assertFalse(html_a.exists())
        self.assertTrue(html_b.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(preview.read_text(encoding="utf-8"), "<!-- rove-preview-owner:2 -->B")

        preview.write_text("<!-- rove-preview-owner:1 -->A", encoding="utf-8")
        with patch.object(api, "PUBLIC_APP_STATE_DIR", state), \
             patch.object(api, "PUBLIC_REPORT_DIR", public), \
             patch.object(api, "REPORTS_DIR", reports), \
             patch.object(api, "REPORTS_ARCHIVE_DIR", archive), \
             patch.object(api, "GENERATED_REPORT_DIR", generated):
            api.remove_deleted_account_files(1, [], [])
        self.assertFalse(preview.exists())
        self.assertTrue(html_b.exists())

    def test_legacy_preview_hash_is_bound_to_matching_user_artifact(self):
        generated = self.root / "generated"
        generated.mkdir()
        html_a = generated / "clarity_report_1_2026-08.html"
        html_a.write_text("legacy user A", encoding="utf-8")
        html_b = generated / "clarity_report_2_2026-08.html"
        html_b.write_text("legacy user B", encoding="utf-8")
        preview = generated / "latest_preview.html"
        preview.write_text("legacy user A", encoding="utf-8")

        owned, ownership = cleanup.generated_report_paths(generated, 1)
        canonical_preview = preview.resolve()
        self.assertIn(canonical_preview, owned)
        self.assertEqual(ownership[canonical_preview][0], 1)
        self.assertIsNotNone(ownership[canonical_preview][1])

        # A legacy renderer may replace the shared preview after ownership was read.
        preview.write_text("legacy user B", encoding="utf-8")
        owner, expected_hash = ownership[canonical_preview]
        self.assertIsNone(cleanup.remove_path(
            canonical_preview,
            (generated,),
            expected_owner_user_id=owner,
            expected_sha256=expected_hash,
        ))
        self.assertEqual(preview.read_text(encoding="utf-8"), "legacy user B")

        # The delayed cleanup queue must retain the original ownership hash too.
        preview.write_text("legacy user A", encoding="utf-8")
        _owned, queued_ownership = cleanup.generated_report_paths(generated, 1)
        queued_owner, queued_hash = queued_ownership[canonical_preview]
        preview.write_text("legacy user B", encoding="utf-8")
        queue_db = self.root / "cleanup.db"
        with sqlite3.connect(queue_db) as conn:
            cleanup.queue_paths_in_conn(
                conn,
                (generated,),
                [canonical_preview],
                conditional_owner_by_path={canonical_preview: (queued_owner, queued_hash)},
            )
        self.assertEqual(cleanup.retry_paths(queue_db, (generated,)), 1)
        self.assertEqual(preview.read_text(encoding="utf-8"), "legacy user B")

    def test_ambiguous_identical_legacy_previews_are_not_claimed(self):
        generated = self.root / "generated"
        generated.mkdir()
        (generated / "clarity_report_1_2026-08.html").write_text("same", encoding="utf-8")
        (generated / "clarity_report_2_2026-08.html").write_text("same", encoding="utf-8")
        preview = generated / "latest_preview.html"
        preview.write_text("same", encoding="utf-8")

        owned, ownership = cleanup.generated_report_paths(generated, 1)
        canonical_preview = preview.resolve()
        self.assertNotIn(canonical_preview, owned)
        self.assertNotIn(canonical_preview, ownership)

    def test_web_report_after_delete_publishes_no_file_or_link(self):
        with sqlite3.connect(self.db) as conn:
            conn.executescript("CREATE TABLE users (user_id INTEGER PRIMARY KEY); INSERT INTO users VALUES (1);")
        public = self.root / "public"
        template = self.root / "report.html"
        template.write_text("<html></html>", encoding="utf-8")

        def render_then_delete(_template, _report_data):
            with sqlite3.connect(self.db) as conn:
                conn.execute("DELETE FROM users WHERE user_id=1")
            return "<html>private</html>"

        with patch.object(web_renderer, "DB_PATH", self.db), \
             patch.object(web_renderer, "TEMPLATE_PATH", template), \
             patch.object(web_renderer, "PUBLIC_REPORT_DIR", public), \
             patch.object(web_renderer, "render_template", side_effect=render_then_delete):
            with self.assertRaises(report_engine.ReportSkipped):
                web_renderer.build_web_report(1, "2026-08", {"meta": {}})

        self.assertFalse(public.exists())
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_links").fetchone()[0], 0)

    def test_pdf_renderer_finishing_after_delete_cannot_publish_final_pdf(self):
        self.create_users(1)
        reports = self.root / "reports"
        final_pdf = reports / "rove_report_1_2026-08.pdf"

        def render_then_delete(_user_id, _month, temporary_pdf, report_data=None):
            Path(temporary_pdf).write_bytes(b"temporary pdf")
            with sqlite3.connect(self.db) as conn:
                conn.execute("DELETE FROM users WHERE user_id=1")
            return Path(temporary_pdf)

        report_data = {"meta": {"tracked_days": 14}}
        with patch.object(report_engine, "DB_NAME", str(self.db)), \
             patch.object(report_engine, "REPORTS_DIR", reports), \
             patch.object(report_html_renderer, "build_pdf_report", side_effect=render_then_delete):
            with self.assertRaises(report_engine.ReportSkipped):
                report_engine.build_pdf(1, "2026-08", report_data=report_data)

        self.assertFalse(final_pdf.exists())

    def test_generated_html_after_delete_leaves_no_final_file(self):
        self.create_users(1)
        generated = self.root / "generated"

        def render_then_delete(_data):
            with sqlite3.connect(self.db) as conn:
                conn.execute("DELETE FROM users WHERE user_id=1")
            return ["<main>private</main>"]

        with patch.object(report_engine, "DB_NAME", str(self.db)), \
             patch.object(report_html_renderer, "GENERATED_DIR", generated), \
             patch.object(report_html_renderer, "_render_hell_pages", side_effect=render_then_delete), \
             patch.object(report_html_renderer, "build_html_document", return_value="<html><head></head></html>"):
            with self.assertRaises(report_engine.ReportSkipped):
                report_html_renderer.build_html_report(1, "2026-08", report_data={"meta": {}})

        self.assertFalse(generated.exists())

    def test_snapshot_finalize_after_delete_creates_no_orphan_row(self):
        self.create_users(1)
        with patch.object(report_engine, "DB_NAME", str(self.db)):
            report_engine.ensure_report_snapshots_v2_table()

        fake_data = {"meta": {"tracked_days": 14}, "ai_narratives": {}}

        def build_then_delete(_user_id, _month):
            with sqlite3.connect(self.db) as conn:
                conn.execute("DELETE FROM users WHERE user_id=1")
            return dict(fake_data)

        with patch.object(report_engine, "DB_NAME", str(self.db)), \
             patch.object(report_engine, "build_report_data", side_effect=build_then_delete), \
             patch.object(report_engine, "_build_report_truth_layer", return_value={}), \
             patch.object(report_engine, "validate_report_snapshot", return_value={"ok": True}), \
             patch.object(report_engine, "MIN_TRACKING_DAYS", 0), \
             patch("report_story_v2.build_report_story_v2", return_value={}):
            with self.assertRaises(report_engine.ReportSkipped):
                report_engine.get_or_create_report_snapshot(1, "2026-08")

        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_snapshots_v2").fetchone()[0], 0)

    def test_scheduled_maintenance_expires_old_ai_state_and_receipts_idempotently(self):
        self.create_users(1)
        state = self.root / "state"; state.mkdir()
        recent_file = state / "recent-token.json"; recent_file.write_text("recent")
        old_file = state / "old-token.json"; old_file.write_text("old")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        old = (now - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db) as conn:
            conn.executescript(
                """
                CREATE TABLE app_state_links (token TEXT PRIMARY KEY, status TEXT, created_at TEXT, expires_at TEXT);
                CREATE TABLE app_ai_conversations (conversation_id TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT);
                CREATE TABLE app_ai_conversation_messages (id INTEGER PRIMARY KEY, conversation_id TEXT, message TEXT);
                CREATE TABLE app_ai_usage (id INTEGER PRIMARY KEY, user_id INTEGER, created_at TEXT);
                CREATE TABLE app_cash_request_receipts (
                    user_id INTEGER NOT NULL, request_id TEXT NOT NULL, operation TEXT NOT NULL,
                    payload TEXT NOT NULL, response TEXT, created_at TEXT, expired_at TEXT,
                    PRIMARY KEY(user_id, request_id)
                );
                """
            )
            conn.executemany(
                "INSERT INTO app_state_links VALUES (?, 'revoked', ?, ?)",
                [("old-token", old, old), ("recent-token", recent, recent)],
            )
            conn.executemany(
                "INSERT INTO app_ai_conversations VALUES (?, 1, ?)",
                [("old", old), ("recent", (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))],
            )
            conn.executemany(
                "INSERT INTO app_ai_conversation_messages(conversation_id, message) VALUES (?, ?)",
                [("old", "private old"), ("recent", "private recent")],
            )
            conn.executemany(
                "INSERT INTO app_ai_usage(user_id, created_at) VALUES (1, ?)",
                [(old,), (recent,)],
            )
            conn.execute(
                "INSERT INTO app_cash_request_receipts(user_id, request_id, operation, payload, response, created_at) "
                "VALUES (1, 'old-request', 'income', '{\"amount\":50}', '{\"balance\":500}', ?)",
                (old,),
            )
            conn.execute(
                "INSERT INTO app_cash_request_receipts(user_id, request_id, operation, payload, response, created_at) "
                "VALUES (1, 'recent-request', 'income', '{\"amount\":20}', '{\"balance\":520}', ?)",
                (recent,),
            )

        public = self.root / "public"; public.mkdir()
        reports = self.root / "reports"; reports.mkdir()
        archive = reports / "archive"; archive.mkdir()
        roots = (state, public, reports, archive, self.root / "generated")
        with patch.object(worker, "DB_PATH", self.db), \
             patch.object(worker, "account_delete_cleanup_roots", return_value=roots), \
             patch.object(worker, "report_renderer_module", return_value=SimpleNamespace(cleanup_expired_reports=lambda: 0)), \
             patch.object(worker, "report_engine_module", return_value=SimpleNamespace(archive_old_reports=lambda: 0)):
            result = worker.maintain_archives()

        self.assertFalse(old_file.exists())
        self.assertTrue(recent_file.exists())
        self.assertGreater(result["auth_cleanup"]["ai_conversations"], 0)
        self.assertEqual(result["auth_cleanup"]["ai_messages"], 1)
        self.assertEqual(result["auth_cleanup"]["ai_usage"], 1)
        self.assertEqual(result["auth_cleanup"]["cash_receipts_redacted"], 1)
        with sqlite3.connect(self.db) as conn:
            old_receipt = conn.execute(
                "SELECT operation, payload, response, expired_at FROM app_cash_request_receipts WHERE request_id='old-request'"
            ).fetchone()
            recent_receipt = conn.execute(
                "SELECT operation, payload, response, expired_at FROM app_cash_request_receipts WHERE request_id='recent-request'"
            ).fetchone()
            self.assertEqual(old_receipt[:3], ("", "", None))
            self.assertIsNotNone(old_receipt[3])
            self.assertEqual(recent_receipt, ("income", '{"amount":20}', '{"balance":520}', None))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_delete_file_cleanup WHERE completed_at IS NOT NULL").fetchone()[0], 1)

        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            with api.app.test_request_context("/"):
                response, status = api.cash_request_replay(
                    conn, 1, "old-request", "income", {"amount": 50}
                )
        self.assertEqual(status, 409)
        self.assertEqual(response.get_json()["error"], "cash_request_expired")

        # Both scheduled cleanup and the receipt redaction are safe to repeat.
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(cleanup.cleanup_cash_request_receipts(conn, now=now), 0)

    def test_legacy_receipt_without_timestamp_gets_full_retention_from_migration(self):
        self.create_users(1)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE app_cash_request_receipts (
                    user_id INTEGER NOT NULL, request_id TEXT NOT NULL,
                    operation TEXT NOT NULL, payload TEXT NOT NULL, response TEXT,
                    PRIMARY KEY(user_id, request_id)
                )"""
            )
            conn.execute(
                "INSERT INTO app_cash_request_receipts VALUES (1, 'legacy', 'income', '{\"amount\":70}', '{\"ok\":true}')"
            )
            cleanup.ensure_cash_request_receipts_schema(conn)
            created_at = conn.execute(
                "SELECT created_at FROM app_cash_request_receipts WHERE request_id='legacy'"
            ).fetchone()[0]
            migration_time = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            self.assertEqual(
                cleanup.cleanup_cash_request_receipts(conn, now=migration_time + timedelta(days=29)),
                0,
            )
            row = conn.execute(
                "SELECT operation, payload, response, expired_at FROM app_cash_request_receipts WHERE request_id='legacy'"
            ).fetchone()

        self.assertEqual(row, ("income", '{"amount":70}', '{"ok":true}', None))


if __name__ == "__main__":
    unittest.main()
