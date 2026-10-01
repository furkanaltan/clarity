from __future__ import annotations

import ast
import http.client
import io
import json
import logging
import os
import runpy
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import gunicorn
import rove_app_api as api
from rove_wsgi_logging import RuntimeLogFilter, configure_runtime_logging
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session
from test_financial_accounts_sprint2 import create_db


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "deploy/gunicorn.conf.py"
UNIT = ROOT / "deploy/systemd/rove-app-api.service"
AUTH_SECRET = "isolated-runtime-auth-secret"
MARKER = "RUNTIME_SECRET_MUST_NOT_APPEAR"


class WsgiConfigurationTests(unittest.TestCase):
    def test_conservative_worker_and_proxy_configuration(self):
        with patch.dict(os.environ, {"ROVE_APP_API_PORT": "5057"}):
            config = runpy.run_path(str(CONFIG))
        expected = {
            "bind": "127.0.0.1:5057", "workers": 1, "threads": 4,
            "worker_class": "gthread", "worker_connections": 64,
            "preload_app": False, "reload": False, "daemon": False,
            "max_requests": 0, "timeout": 120, "graceful_timeout": 90,
            "control_socket_disable": True, "forwarded_allow_ips": "127.0.0.1",
        }
        for name, value in expected.items():
            self.assertEqual(config[name], value, name)

    def test_existing_port_setting_is_preserved(self):
        with patch.dict(os.environ, {"ROVE_APP_API_PORT": "15057"}):
            self.assertEqual(runpy.run_path(str(CONFIG))["bind"], "127.0.0.1:15057")

    def test_unit_preserves_environment_and_leaves_time_to_drain(self):
        unit = UNIT.read_text()
        self.assertIn("python -m gunicorn --config /root/clarity/deploy/gunicorn.conf.py rove_app_wsgi:app", unit)
        self.assertEqual([line for line in unit.splitlines() if line.startswith("EnvironmentFile=")], [
            "EnvironmentFile=/root/clarity/.env",
            "EnvironmentFile=/root/clarity/.rove-app-api.env",
            "EnvironmentFile=-/root/clarity/.rove-leeway.env",
            "EnvironmentFile=-/root/clarity/.rove-market-data.env",
        ])
        for value in ("Restart=always", "RestartSec=5", "KillMode=mixed", "TimeoutStopSec=105",
                      "RuntimeDirectory=rove-app-api", "RuntimeDirectoryMode=0700",
                      "StandardOutput=journal", "StandardError=journal"):
            self.assertIn(value, unit)
        self.assertNotIn("ExecReload=", unit)

    def test_wsgi_and_development_entry_use_same_startup_preparation(self):
        with patch.object(api, "prepare_runtime_schema") as prepare, patch.object(api.app, "run") as run, \
                patch.object(api, "DB_PATH", ROOT / "rove_app_api.py"), \
                patch("threading.Thread.start") as thread_start:
            namespace = runpy.run_module("rove_app_wsgi")
        self.assertIs(namespace["app"], api.app)
        prepare.assert_called_once_with()
        run.assert_not_called()
        thread_start.assert_not_called()
        source = ast.parse((ROOT / "rove_app_api.py").read_text())
        main = source.body[-1]
        self.assertIsInstance(main, ast.If)
        self.assertEqual(main.body[0].value.func.id, "prepare_runtime_schema")

    def test_schema_preparation_is_idempotent_and_preserves_users(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.db"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, current_cash REAL)")
                conn.execute("INSERT INTO users VALUES (1, 123.45)")
            with patch.object(api, "DB_PATH", database):
                api.prepare_runtime_schema()
                api.prepare_runtime_schema()
            with sqlite3.connect(database) as conn:
                self.assertEqual(conn.execute("SELECT current_cash FROM users").fetchone()[0], 123.45)
                self.assertTrue(api.admin_schema_ready(conn))
                self.assertIn("buffer_target_amount", [row[1] for row in conn.execute("PRAGMA table_info(users)")])
                self.assertTrue(conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellations'").fetchone())

    def test_missing_database_fails_closed_without_creating_one(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing.db"
            with patch.object(api, "DB_PATH", database), patch.object(api, "prepare_runtime_schema") as prepare:
                with self.assertRaisesRegex(RuntimeError, "API startup failed .*FileNotFoundError"):
                    runpy.run_module("rove_app_wsgi")
            self.assertFalse(database.exists())
            prepare.assert_not_called()

    def test_startup_failure_does_not_expose_exception_payload(self):
        with patch.object(api, "DB_PATH", ROOT / "rove_app_api.py"), \
                patch.object(api, "prepare_runtime_schema", side_effect=ValueError(MARKER)):
            with self.assertRaises(RuntimeError) as caught:
                runpy.run_module("rove_app_wsgi")
        self.assertNotIn(MARKER, str(caught.exception))

    def test_access_log_omits_request_identifiers_and_secrets(self):
        config = runpy.run_path(str(CONFIG))
        self.assertEqual(config["accesslog"], "-")
        self.assertEqual(config["errorlog"], "-")
        self.assertEqual(config["loglevel"], "info")
        self.assertEqual(config["access_log_format"], "%(t)s pid=%(p)s method=%(m)s status=%(s)s duration=%(L)s")

    def test_exception_filter_preserves_frames_without_path_source_or_payload(self):
        try:
            raise RuntimeError(MARKER)
        except RuntimeError:
            record = logging.LogRecord("rove_app_api", logging.ERROR, __file__, 1,
                                       "Exception on /private/%s", (MARKER,), sys.exc_info())
        RuntimeLogFilter().filter(record)
        self.assertEqual(record.getMessage(), "Runtime exception (RuntimeError)")
        self.assertIsNone(record.exc_info)
        formatted = logging.Formatter().format(record)
        self.assertNotIn(MARKER, formatted)
        self.assertNotIn(str(ROOT), formatted)
        self.assertIn("Traceback (most recent call last):", formatted)
        self.assertIn('File "test_wsgi_runtime.py", line ', formatted)
        self.assertIn("[details redacted]", formatted)

    def test_module_logging_is_redacted_without_duplicate_handlers(self):
        root = logging.getLogger()
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        with patch.object(root, "handlers", [handler]), patch.object(root, "level", logging.WARNING):
            configure_runtime_logging()
            configure_runtime_logging()
            self.assertEqual(root.handlers, [handler])
            self.assertEqual(len(handler.filters), 1)
            try:
                raise ValueError(MARKER)
            except ValueError:
                logging.getLogger("runtime.synthetic.module").exception("Private path %s", MARKER)
        log = output.getvalue()
        self.assertNotIn(MARKER, log)
        self.assertEqual(log.count("Runtime exception (ValueError)"), 1)
        self.assertIn("Traceback (most recent call last):", log)

    def test_invalid_http_filter_omits_header_values(self):
        record = logging.LogRecord("gunicorn.error", logging.WARNING, __file__, 1,
                                   "Invalid request from ip=127.0.0.1: " + MARKER, (), None)
        RuntimeLogFilter().filter(record)
        self.assertEqual(record.getMessage(), "Invalid HTTP request rejected")

    def test_dependency_is_release_and_source_hash_pinned(self):
        self.assertEqual(gunicorn.__version__, "26.2.2")
        requirement = (ROOT / "requirements/wsgi.txt").read_text()
        self.assertIn("/refs/tags/26.2.2#sha256=fab2c19817acf7ad61d405584feef928d4231a17374ae8ffa92b82ee43deed7c", requirement)
        self.assertIn("-r wsgi.txt", (ROOT / "requirements/api.txt").read_text())


class GunicornHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.database = cls.directory / "test.db"
        cls.log = cls.directory / "gunicorn.log"
        cls.public = cls.directory / "public"
        cls.public.mkdir()
        cls.reports = cls.directory / "reports"
        cls.reports.mkdir()
        create_db(cls.database)
        with sqlite3.connect(cls.database) as conn:
            conn.execute("ALTER TABLE users ADD COLUMN onboarding_step INTEGER DEFAULT 10")
        with patch.object(api, "AUTH_SECRET", AUTH_SECRET):
            for user in (1, 2):
                ensure_unlocked_test_session(cls.database, user, f"runtime-session-{user}")
        with closing(sqlite3.connect(cls.database)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            api.ensure_push_preferences_table(conn)
            api.ensure_cancellation_schema(conn)
            api.ensure_market_tracking_schema(conn)
            api.ensure_app_etf_savings_plan_table(conn)
            api.ensure_app_etf_position_plans_table(conn)
            api.ensure_investment_contribution_schema(conn)
            for user in (1, 2):
                api.build_live_app_data(conn, user, activate_due_savings=False)
                api.get_visible_coach_v4(conn, user)
                api.get_feature_announcements_for_user(conn, user)
            conn.execute("INSERT INTO app_credentials(account_id,password_hash) SELECT id,? FROM app_accounts WHERE user_id=1",
                         (api.PASSWORD_HASHER.hash("runtime-only-password"),))
            conn.execute("CREATE TABLE report_jobs (user_id INTEGER, report_month TEXT, status TEXT)")
            conn.execute("INSERT INTO report_jobs VALUES (1,'2026-09','sent')")
            conn.execute("CREATE TABLE report_links (token TEXT PRIMARY KEY,user_id INTEGER,html_path TEXT,status TEXT,expires_at TEXT)")
            public_report = cls.public / "synthetic-report"
            public_report.mkdir()
            html = public_report / "index.html"
            html.write_text("<html><body>Synthetic report</body></html>")
            conn.execute("INSERT INTO report_links VALUES (?,1,?,'active','2099-01-01')", (MARKER, str(html)))
            conn.execute("INSERT INTO app_contracts(user_id,contract_id,detail_key,name,category,amount,cancelable) VALUES (1,'runtime-test','runtime-test','[VKS TEST] Runtime','Abos',12,1)")
            conn.commit()
        from reportlab.pdfgen.canvas import Canvas
        pdf = Canvas(str(cls.reports / "rove_report_1_2026-09.pdf"))
        pdf.drawString(40, 800, "Synthetic runtime report")
        pdf.save()
        # Loaded only by this isolated test process, never by the production unit.
        fixture = '''import os, time
import rove_app_api as api
from rove_app_wsgi import app
api.send_login_email = lambda *args, **kwargs: None
api.secrets.randbelow = lambda size: 135790
@app.get('/__runtime_test__/pid')
def worker_pid():
    return {'pid': os.getpid()}
@app.get('/__runtime_test__/slow')
def slow_request():
    time.sleep(1)
    return {'ok': True}
@app.get('/__runtime_test__/pin-rate')
def pin_rate():
    return {'allowed': api.pin_rate_allowed(999000001), 'pid': os.getpid()}
@app.get('/__runtime_test__/failure')
def failure():
    raise RuntimeError('RUNTIME_SECRET_MUST_NOT_APPEAR')
'''
        (cls.directory / "runtime_fixture.py").write_text(fixture)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.process = None
        cls.addClassCleanup(cls.stop_server)
        cls.start_server()

    @classmethod
    def start_server(cls):
        env = os.environ.copy()
        env.update({
            "CLARITY_DB_NAME": str(cls.database), "ROVE_APP_AUTH_SECRET": AUTH_SECRET,
            "CLARITY_REPORTS_DIR": str(cls.reports), "ROVE_REPORT_PUBLIC_DIR": str(cls.public),
            "ROVE_APP_ALLOWED_ORIGINS": "https://getrove.de", "ROVE_VKS_EMAIL_ENABLED": "0",
            "ROVE_VKS_LIVE_APPROVED": "0", "ROVE_VKS_MAIL_MODE": "test",
            "ROVE_VKS_BREVO_WEBHOOK_TOKEN": "runtime-webhook-token", "BREVO_API_KEY": "",
            "OPENAI_API_KEY": "", "ROVE_VAPID_PRIVATE": "", "GUNICORN_CMD_ARGS": "",
        })
        with cls.log.open("ab") as output:
            cls.process = subprocess.Popen([
                sys.executable, "-m", "gunicorn", "--config", str(CONFIG),
                "--bind", f"127.0.0.1:{cls.port}", "--worker-tmp-dir", str(cls.directory),
                "--pythonpath", str(cls.directory), "runtime_fixture:app",
            ], cwd=ROOT, env=env, stdout=output, stderr=output, start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise AssertionError("Gunicorn failed to start: " + cls.log.read_text())
            try:
                if cls.request("GET", "/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(.05)
        raise AssertionError("Gunicorn startup timed out: " + cls.log.read_text())

    @classmethod
    def stop_server(cls):
        if cls.process and cls.process.poll() is None:
            cls.process.send_signal(signal.SIGTERM)
            try:
                cls.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(cls.process.pid, signal.SIGKILL)
                cls.process.wait(timeout=5)

    @classmethod
    def request(cls, method, path, data=None, user=None, headers=None):
        request_headers = {"Origin": "https://getrove.de", **(headers or {})}
        if user:
            request_headers["Cookie"] = f"{api.SESSION_COOKIE_NAME}=runtime-session-{user}"
        body = None if data is None else json.dumps(data)
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=8)
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    @classmethod
    def financial_rows(cls):
        with sqlite3.connect(cls.database) as conn:
            return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                    for table in ("users", "expenses", "app_financial_accounts", "app_cash_movements", "investment_events")}

    def test_health_minimal_and_untrusted_cors_remains_blocked(self):
        status, body, headers = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "service": "rove-app-api"})
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://getrove.de")
        foreign = self.request("GET", "/health", headers={"Origin": "https://foreign.invalid"})
        self.assertNotIn("Access-Control-Allow-Origin", foreign[2])

    def test_parallel_reads_are_isolated_and_do_not_change_financial_rows(self):
        before = self.financial_rows()
        routes = [("/v1/state", 1), ("/v1/state", 2), ("/v1/transactions", 1),
                  ("/v1/contract-cancellations?contract_id=runtime-test", 1), ("/v1/push/preferences", 1),
                  ("/v1/auth/me", 2)] * 4
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda item: self.request("GET", item[0], user=item[1]), routes))
        self.assertEqual(self.financial_rows(), before)
        self.assertEqual([item[0] for item in results], [200] * len(routes),
                         [(route, result[0]) for route, result in zip(routes, results) if result[0] != 200])
        for (path, user), (status, body, _) in zip(routes, results):
            self.assertEqual(status, 200, (path, body))
            if path == "/v1/state":
                self.assertEqual(json.loads(body)["user_id"], user)
        self.assertNotIn("database is locked", self.log.read_text().lower())

    def test_state_is_isolated_and_financially_read_only_without_overlap(self):
        before = self.financial_rows()
        for user in (1, 2):
            status, body, _ = self.request("GET", "/v1/state", user=user)
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)["user_id"], user)
        self.assertEqual(self.financial_rows(), before)

    def test_parallel_state_gate_three_independent_workers(self):
        workers = set()
        for run in range(1, 4):
            self.stop_server()
            self.start_server()
            worker = json.loads(self.request("GET", "/__runtime_test__/pid")[1])["pid"]
            self.assertNotIn(worker, workers)
            workers.add(worker)
            before = self.financial_rows()
            log_offset = self.log.stat().st_size
            with ThreadPoolExecutor(max_workers=24) as pool:
                results = list(pool.map(lambda uid: self.request("GET", "/v1/state", user=uid), [1, 2] * 12))
            self.assertEqual([result[0] for result in results], [200] * 24,
                             [(status, body) for status, body, _ in results if status != 200])
            for uid, (_, body, _) in zip([1, 2] * 12, results):
                self.assertEqual(json.loads(body)["user_id"], uid)
            self.assertEqual(self.financial_rows(), before)
            log = self.log.read_bytes()[log_offset:].decode()
            self.assertNotIn("OperationalError", log)
            self.assertNotIn("database is locked", log.lower())
            print(f"STATE_PARALLEL_RUN_{run}=24/24 HTTP_200; LOCK_ERRORS=0")

    def test_password_login_works_over_wsgi(self):
        status, body, headers = self.request("POST", "/v1/auth/password/login", {
            "email": "wave4-user-1@example.test", "password": "runtime-only-password"})
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["ok"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])

    def test_process_local_pin_rate_limit_is_shared_by_threads(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request("GET", "/__runtime_test__/pin-rate"), range(24)))
        self.assertEqual([result[0] for result in results], [200] * 24)
        payloads = [json.loads(result[1]) for result in results]
        self.assertEqual(len({payload["pid"] for payload in payloads}), 1)
        self.assertEqual(sum(payload["allowed"] for payload in payloads), api.PIN_RATE_LIMIT)

    def test_login_code_is_consumed_exactly_once_over_parallel_requests(self):
        email = "wave4-user-1@example.test"
        requested = self.request("POST", "/v1/auth/request-code", {"email": email})
        self.assertEqual(requested[0], 200)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request("POST", "/v1/auth/verify-code", {
                "email": email, "code": "135790"}), range(2)))
        self.assertEqual(sorted(item[0] for item in results), [200, 401])

    def test_report_html_and_pdf_are_served_through_existing_gates(self):
        html = self.request("GET", f"/v1/public-reports/{MARKER}/")
        self.assertEqual(html[0], 200, html[1])
        self.assertIn(b"Synthetic report", html[1])
        pdf = self.request("GET", "/v1/reports/2026-09/pdf", user=1)
        self.assertEqual(pdf[0], 200, pdf[1])
        self.assertTrue(pdf[1].startswith(b"%PDF-"))
        self.assertEqual(self.request("GET", "/v1/reports/2026-09/pdf", user=2)[0], 404)

    def test_vks_case_private_pdf_and_mail_off_remain_intact(self):
        started = self.request("POST", "/v1/contract-cancellations", {"contract_id": "runtime-test"}, user=1)
        self.assertEqual(started[0], 200, started[1])
        case = json.loads(started[1])["case"]
        pdf = self.request("GET", f"/v1/contract-cancellations/{case['id']}/pdf", user=1)
        self.assertEqual(pdf[0], 200, pdf[1])
        self.assertTrue(pdf[1].startswith(b"%PDF-"))
        self.assertEqual(self.request("GET", f"/v1/contract-cancellations/{case['id']}/pdf", user=2)[0], 404)
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_contract_cancellation_messages WHERE direction='outbound'").fetchone()[0], 0)

    def test_webhook_auth_and_unknown_event_are_safe_over_wsgi(self):
        payload = {"event": "delivered", "message-id": "runtime-unknown-message", "email": "nobody@example.test", "ts_event": 1790738400}
        before = self.financial_rows()
        self.assertEqual(self.request("POST", "/webhooks/brevo/vks", payload)[0], 401)
        for _ in range(2):
            status, body, _ = self.request("POST", "/webhooks/brevo/vks", payload,
                                          headers={"Authorization": "Bearer runtime-webhook-token"})
            self.assertEqual(status, 202, body)
            self.assertEqual(json.loads(body)["result"], "unmatched")
        self.assertEqual(self.financial_rows(), before)

    def test_settings_preferences_save_and_read_without_transport(self):
        saved = self.request("POST", "/v1/push/preferences", {"trackingReminder": False, "timezone": "Europe/Berlin"}, user=1)
        self.assertEqual(saved[0], 200, saved[1])
        read = self.request("GET", "/v1/push/preferences", user=1)
        self.assertFalse(json.loads(read[1])["trackingReminder"])

    def test_logs_hide_paths_query_headers_and_exception_payloads(self):
        self.request("GET", "/health?token=" + MARKER, headers={"Authorization": "Bearer " + MARKER})
        failure = self.request("GET", "/__runtime_test__/failure?token=" + MARKER)
        self.assertEqual(failure[0], 500)
        self.assertNotIn(MARKER.encode(), failure[1])
        time.sleep(.1)
        log = self.log.read_text()
        self.assertNotIn(MARKER, log)
        self.assertIn("Traceback (most recent call last)", log)
        self.assertIn('File "runtime_fixture.py", line ', log)
        self.assertNotIn("raise RuntimeError(", log)
        self.assertNotIn("development server", log.lower())
        self.assertIn("Runtime exception (RuntimeError)", log)
        self.assertIn("method=GET status=", log)

    def test_z_graceful_shutdown_drains_request_and_restart_has_no_orphans(self):
        old_worker = json.loads(self.request("GET", "/__runtime_test__/pid")[1])["pid"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.request, "GET", "/__runtime_test__/slow")
            time.sleep(.2)
            self.process.send_signal(signal.SIGTERM)
            self.assertEqual(pending.result(timeout=5)[0], 200)
        self.assertEqual(self.process.wait(timeout=8), 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(old_worker, 0)
        self.start_server()
        new_worker = json.loads(self.request("GET", "/__runtime_test__/pid")[1])["pid"]
        self.assertNotEqual(new_worker, old_worker)
        self.assertEqual(self.request("GET", "/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
