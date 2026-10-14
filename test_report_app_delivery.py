import json
import logging
import shutil
import sqlite3
import subprocess
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.test import Client
from werkzeug.wrappers import Response

import report_engine as engine
import report_html_renderer as pdf
import rove_app_api as api
import rove_app_state as state
import rove_report_worker as worker
import rove_web_report_renderer as web
import test_report_snapshot_v2 as snapshot_tests
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session


class ReportAppDeliveryTests(unittest.TestCase):
    def setUp(self):
        fixture = snapshot_tests.ReportSnapshotV2Tests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.database = Path(fixture.db)
        self.root = Path(fixture.tmp.name)
        self.reports = self.root / "reports"
        self.public = self.root / "web"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for obj, key, value in (
            (worker, "DB_PATH", self.database), (web, "DB_PATH", self.database),
            (web, "PUBLIC_REPORT_DIR", self.public),
            (web, "PUBLIC_REPORT_BASE_URL", "https://getrove.de/reports"),
            (engine, "REPORTS_DIR", self.reports), (api, "DB_PATH", self.database),
            (api, "REPORTS_DIR", self.reports), (api, "REPORTS_ARCHIVE_DIR", self.reports / "archive"),
            (api, "PUBLIC_REPORT_DIR", self.public), (api, "AUTH_SECRET", "synthetic-app-report-secret"),
            (state, "REPORTS_DIR", self.reports), (state, "REPORTS_ARCHIVE_DIR", self.reports / "archive"),
            (state, "PUBLIC_APP_API_BASE_URL", "https://localhost/app-api"),
        ):
            self.stack.enter_context(patch.object(obj, key, value))
        self.stack.enter_context(patch("report_ai_text.generate_ai_narratives", return_value={}))
        self.push = self.stack.enter_context(patch.object(engine, "send_report_push"))
        self.stack.enter_context(patch.object(worker, "random_report_time_for_today", return_value="2026-09-02 08:00:00"))
        self.stack.enter_context(patch.object(worker, "report_now", return_value=datetime(2026, 10, 14, 12)))
        with self.connection() as conn:
            conn.executescript("""
                ALTER TABLE users ADD COLUMN onboarding_step INTEGER DEFAULT 10;
                INSERT INTO users(user_id,current_cash) VALUES (2,2000);
                CREATE TABLE user_access(user_id INTEGER PRIMARY KEY,status TEXT);
                INSERT INTO user_access VALUES (1,'approved'),(2,'approved');
                CREATE TABLE report_jobs(
                    id INTEGER PRIMARY KEY,user_id INTEGER,report_month TEXT,scheduled_at TEXT,
                    status TEXT,attempts INTEGER,last_error TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id,report_month));
                DELETE FROM expenses;
                DELETE FROM app_cash_movements;
            """)
            conn.executemany(
                "INSERT INTO expenses(user_id,amount,category,merchant,description,created_at) VALUES (1,15,'Shopping','Synthetic Market','',?)",
                [(f"2026-08-{day:02d} 12:00:00",) for day in range(1, 21)],
            )
        for user in (1, 2):
            ensure_unlocked_test_session(self.database, user, f"delivery-session-{user}")
        worker.enqueue_month("2026-08")

    def connection(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def job(self):
        with self.connection() as conn:
            return dict(conn.execute("SELECT * FROM report_jobs WHERE user_id=1 AND report_month='2026-08'").fetchone())

    def app_reports(self, user=1):
        with self.connection() as conn:
            return [item for item in state._build_reports(conn, user) if item["month"] == "2026-08"]

    def fake_web(self, user, month, report_data):
        token = "synthetic-web-token"
        path = self.public / token / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html>Synthetic report</html>", encoding="utf-8")
        web.ensure_report_links_table()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO report_links(token,user_id,report_month,html_path,public_url,expires_at,status) VALUES (?,?,?,?,?,'2099-01-01','active')",
                (token, user, month, str(path), f"https://getrove.de/reports/{token}/"),
            )
        return {"token": token, "path": path, "url": f"https://getrove.de/reports/{token}/"}

    def fake_pdf(self, user, month, report_data):
        path = self.reports / f"rove_report_{user}_{month}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\nSynthetic test artifact\n")
        return path, 20

    def assert_not_ready(self, result, status="pending"):
        self.assertEqual(result["sent"], 0)
        self.assertEqual(self.job()["status"], status)
        self.assertEqual(self.app_reports(), [])
        self.push.assert_not_called()

    def test_success_requires_registered_web_and_pdf_before_ready(self):
        with patch.object(web, "build_web_report", side_effect=self.fake_web), \
             patch.object(engine, "build_pdf", side_effect=self.fake_pdf):
            result = worker.process_due_jobs()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(self.job()["status"], "sent")
        report = self.app_reports()[0]
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["pdfAvailable"])
        self.assertEqual(report["webUrl"], "https://localhost/app-api/v1/public-reports/synthetic-web-token/")
        self.assertEqual(self.app_reports(2), [])
        self.push.assert_called_once_with(1, "2026-08")

    def test_web_failure_retries_without_ready_or_leaking_payload(self):
        with patch.object(web, "build_web_report", side_effect=RuntimeError("SYNTHETIC_PRIVATE_PAYLOAD")), \
             patch.object(engine, "build_pdf") as build_pdf, \
             self.assertLogs(worker.logger, level="WARNING") as logs:
            result = worker.process_due_jobs()
        self.assert_not_ready(result)
        self.assertEqual(result["retry"], 1)
        self.assertEqual(self.job()["attempts"], 1)
        next_attempt = datetime(2026, 10, 14, 12) + timedelta(minutes=worker.REPORT_RETRY_DELAY_MINUTES)
        self.assertEqual(self.job()["scheduled_at"], next_attempt.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertNotIn("SYNTHETIC_PRIVATE_PAYLOAD", self.job()["last_error"] + "".join(logs.output))
        build_pdf.assert_not_called()

    def test_pdf_failure_retries_without_ready_even_with_web_artifact(self):
        with patch.object(web, "build_web_report", side_effect=self.fake_web), \
             patch.object(engine, "build_pdf", side_effect=RuntimeError("synthetic_pdf_failure")):
            result = worker.process_due_jobs()
        self.assert_not_ready(result)
        self.assertEqual(result["retry"], 1)
        self.assertTrue((self.public / "synthetic-web-token" / "index.html").is_file())

    def test_renderer_failure_respects_existing_terminal_retry_limit(self):
        with self.connection() as conn:
            conn.execute("UPDATE report_jobs SET attempts=? WHERE user_id=1", (worker.REPORT_MAX_ATTEMPTS - 1,))
        with patch.object(web, "build_web_report", side_effect=RuntimeError("synthetic_failure")):
            result = worker.process_due_jobs()
        self.assert_not_ready(result, "failed")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.job()["attempts"], worker.REPORT_MAX_ATTEMPTS)

    def test_missing_empty_and_unregistered_artifacts_cannot_complete(self):
        for defect in ("web_missing", "web_empty", "web_unregistered", "pdf_missing", "pdf_empty"):
            with self.subTest(defect=defect):
                with self.connection() as conn:
                    conn.execute("UPDATE report_jobs SET status='pending',attempts=0,scheduled_at='2026-09-02 08:00:00' WHERE user_id=1")
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='report_links'").fetchone():
                        conn.execute("DELETE FROM report_links")
                def build_pdf(user, month, report_data):
                    path, days = self.fake_pdf(user, month, report_data)
                    html = self.public / "synthetic-web-token" / "index.html"
                    if defect == "web_unregistered":
                        with self.connection() as conn:
                            conn.execute("DELETE FROM report_links")
                    elif defect.endswith("missing"):
                        (html if defect.startswith("web") else path).unlink()
                    else:
                        (html if defect.startswith("web") else path).write_bytes(b"")
                    return path, days
                with patch.object(web, "build_web_report", side_effect=self.fake_web), \
                     patch.object(engine, "build_pdf", side_effect=build_pdf):
                    self.assert_not_ready(worker.process_due_jobs())

    def test_foreign_user_month_and_pdf_paths_cannot_complete(self):
        for defect in ("user", "month", "pdf"):
            with self.subTest(defect=defect):
                with self.connection() as conn:
                    conn.execute("UPDATE report_jobs SET status='pending',attempts=0,scheduled_at='2026-09-02 08:00:00' WHERE user_id=1")
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='report_links'").fetchone():
                        conn.execute("DELETE FROM report_links")
                def build_pdf(user, month, report_data):
                    result = self.fake_pdf(2 if defect == "pdf" else user, month, report_data)
                    with self.connection() as conn:
                        if defect == "user":
                            conn.execute("UPDATE report_links SET user_id=2")
                        if defect == "month":
                            conn.execute("UPDATE report_links SET report_month='2026-07'")
                    return result
                with patch.object(web, "build_web_report", side_effect=self.fake_web), \
                     patch.object(engine, "build_pdf", side_effect=build_pdf):
                    self.assert_not_ready(worker.process_due_jobs())

    def test_historical_sent_without_complete_artifacts_is_not_ready(self):
        for defect in ("web_missing", "web_empty", "web_unregistered", "pdf_missing", "pdf_empty"):
            with self.subTest(defect=defect):
                with self.connection() as conn:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='report_links'").fetchone():
                        conn.execute("DELETE FROM report_links")
                    conn.execute("UPDATE report_jobs SET status='sent' WHERE user_id=1")
                web_result = self.fake_web(1, "2026-08", {})
                pdf_path, _ = self.fake_pdf(1, "2026-08", {})
                if defect == "web_unregistered":
                    with self.connection() as conn:
                        conn.execute("DELETE FROM report_links")
                elif defect.endswith("missing"):
                    (web_result["path"] if defect.startswith("web") else pdf_path).unlink()
                else:
                    (web_result["path"] if defect.startswith("web") else pdf_path).write_bytes(b"")
                self.assertEqual(self.app_reports(), [])
                self.assertEqual(self.job()["status"], "sent")

    def test_expired_web_does_not_remove_valid_pdf_archive(self):
        self.fake_web(1, "2026-08", {})
        self.fake_pdf(1, "2026-08", {})
        with self.connection() as conn:
            conn.execute("UPDATE report_jobs SET status='sent' WHERE user_id=1")
            conn.execute("UPDATE report_links SET status='expired',expires_at='2026-09-01'")
        (self.public / "synthetic-web-token" / "index.html").unlink()
        report = self.app_reports()[0]
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["pdfAvailable"])
        self.assertEqual(report["webUrl"], "")

    def test_default_web_origin_matches_existing_app_api_even_from_another_app_origin(self):
        self.fake_web(1, "2026-08", {})
        self.fake_pdf(1, "2026-08", {})
        with self.connection() as conn:
            conn.execute("UPDATE report_jobs SET status='sent' WHERE user_id=1")
        with patch.object(state, "PUBLIC_APP_API_BASE_URL", ""):
            url = self.app_reports()[0]["webUrl"]
        self.assertEqual(url, "https://getrove.de/app-api/v1/public-reports/synthetic-web-token/")
        self.assertIn('const PAIR_API_BASE_URL = "https://getrove.de/app-api";',
                      Path(__file__).with_name("frontend").joinpath("index.html").read_text())

    def test_real_renderers_share_snapshot_and_app_open_keeps_cookie_scope(self):
        with patch.object(web, "build_web_report", wraps=web.build_web_report) as web_spy, \
             patch.object(pdf, "build_pdf_report", wraps=pdf.build_pdf_report) as pdf_spy:
            result = worker.process_due_jobs()
        self.assertEqual(result["sent"], 1)
        self.assertIs(web_spy.call_args.kwargs["report_data"], pdf_spy.call_args.kwargs["report_data"])
        report = self.app_reports()[0]
        self.assertTrue(report["pdfAvailable"])
        self.assertTrue(report["webUrl"].startswith("https://localhost/app-api/v1/public-reports/"))
        source = Path(__file__).with_name("frontend").joinpath("index.html").read_text()
        start = source.index('document.getElementById("repdetailbody").addEventListener("click",e=>{')
        end = source.index("\n\nasync function openReportPdf", start)
        script = """
            let listener, opened;
            const DATA={reports:[REPORT]};
            const document={getElementById:()=>({addEventListener:(_,fn)=>listener=fn})};
            const window={open:(...args)=>opened=args};
            HANDLER
            listener({target:{closest:s=>s==='[data-report-web]'?{dataset:{reportWeb:'0'}}:null}});
            process.stdout.write(JSON.stringify(opened));
        """.replace("REPORT", json.dumps(report)).replace("HANDLER", source[start:end])
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required to exercise the actual app click handler")
        opened = json.loads(subprocess.check_output([node, "-e", script], text=True))
        self.assertEqual(opened, [report["webUrl"], "_blank", "noopener"])
        # Match the existing nginx prefix stripping and the real narrow cookie path.
        app = DispatcherMiddleware(Response(status=404), {"/app-api": api.app})
        for user, expected in ((1, 200), (2, 410), (None, 401)):
            client = Client(app, Response)
            if user:
                client.set_cookie(api.SESSION_COOKIE_NAME, f"delivery-session-{user}", path="/app-api/", secure=True)
            with client.get(opened[0], base_url="https://localhost") as response:
                self.assertEqual(response.status_code, expected)
            with client.get("/app-api/v1/reports/2026-08/pdf", base_url="https://localhost") as response:
                self.assertEqual(response.status_code, 200 if user == 1 else 404 if user == 2 else 401)
                if user == 1:
                    self.assertTrue(response.data.startswith(b"%PDF-"))
            if user == 1:
                self.assertEqual(client.get("/app-api/v1/reports/2026-07/pdf", base_url="https://localhost").status_code, 404)


if __name__ == "__main__":
    logging.getLogger("weasyprint").setLevel(logging.ERROR)
    unittest.main()
