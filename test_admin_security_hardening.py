import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from argon2 import PasswordHasher
from argon2.low_level import Type

import rove_app_api as api


class AdminSecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        self.original_admin_rate_limits = api.ADMIN_RATE_LIMITS.copy()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    onboarding_step INTEGER DEFAULT 10
                );
                CREATE TABLE user_access (
                    user_id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL
                );
                CREATE TABLE app_invitations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                INSERT INTO users VALUES (1, 10);
                INSERT INTO users VALUES (2, 10);
                INSERT INTO user_access VALUES (1, 'approved');
                INSERT INTO user_access VALUES (2, 'approved');
                """
            )
            api.ensure_auth_tables(conn)
            api.ensure_admin_tables(conn)
            for user_id, email in ((1, "admin@example.test"), (2, "member@example.test")):
                conn.execute(
                    """INSERT INTO app_accounts
                       (email, user_id, verified_at, source)
                       VALUES (?, ?, CURRENT_TIMESTAMP, 'app')""",
                    (email, user_id),
                )
            conn.commit()

        fast_hasher = PasswordHasher(
            time_cost=1,
            memory_cost=8192,
            parallelism=1,
            hash_len=16,
            salt_len=8,
            type=Type.ID,
        )
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "admin-security-test-secret"),
            patch.object(api, "ADMIN_USER_IDS", frozenset({1})),
            patch.object(api, "PASSWORD_HASHER", fast_hasher),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.ADMIN_RATE_BUCKETS.clear()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        api.ADMIN_RATE_BUCKETS.clear()
        api.ADMIN_RATE_LIMITS.clear()
        api.ADMIN_RATE_LIMITS.update(self.original_admin_rate_limits)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def _account_id(self, user_id):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return int(
                conn.execute(
                    "SELECT id FROM app_accounts WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            )

    def _session(self, user_id, raw_token, *, unlocked=True):
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                """INSERT INTO app_sessions (token_hash, account_id, expires_at)
                   VALUES (?, ?, ?)""",
                (
                    api.keyed_hash(raw_token),
                    self._account_id(user_id),
                    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            session_id = int(cursor.lastrowid)
            verifier = api.PASSWORD_HASHER.hash(api.pin_secret_value(session_id, "1234"))
            if unlocked:
                conn.execute(
                    """INSERT INTO app_session_pins
                       (session_id, pin_verifier, failed_attempts, locked_out_at,
                        unlocked_at, last_activity_at, updated_at)
                       VALUES (?, ?, 0, NULL, CURRENT_TIMESTAMP,
                               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                    (session_id, verifier),
                )
            else:
                conn.execute(
                    """INSERT INTO app_session_pins
                       (session_id, pin_verifier, failed_attempts, locked_out_at,
                        unlocked_at, last_activity_at, updated_at)
                       VALUES (?, ?, 0, NULL, NULL, NULL, CURRENT_TIMESTAMP)""",
                    (session_id, verifier),
                )
            conn.commit()
        return raw_token

    @staticmethod
    def _client(token):
        client = api.app.test_client()
        client.set_cookie(api.SESSION_COOKIE_NAME, token, domain="localhost", path="/")
        return client

    @staticmethod
    def _same_origin_headers(**extra):
        headers = {
            "Origin": "https://getrove.de",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
        }
        headers.update(extra)
        return headers

    def _audit_rows(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """SELECT action, success, failure_reason, target_user_id,
                          target_email, details, request_correlation
                     FROM app_admin_events ORDER BY id"""
            ).fetchall()

    def test_every_admin_endpoint_requires_a_session(self):
        probes = (
            ("GET", "/v1/admin/overview", None),
            ("GET", "/v1/admin/coach-patterns/1", None),
            ("GET", "/v1/admin/coach-snapshot-metrics", None),
            ("POST", "/v1/admin/invitations", {"email": "guest@example.test"}),
            ("DELETE", "/v1/admin/invitations/1", None),
            ("POST", "/v1/admin/access/2", {"action": "revoke"}),
        )
        with api.app.test_client() as client:
            for method, path, payload in probes:
                response = client.open(
                    path,
                    method=method,
                    json=payload,
                    headers=self._same_origin_headers(),
                )
                self.assertEqual(response.status_code, 401, (method, path, response.get_json()))

    def test_non_admin_and_locked_pin_are_denied(self):
        member = self._session(2, "member-token")
        with self._client(member) as client:
            self.assertEqual(client.get("/v1/admin/overview").status_code, 403)

        locked_admin = self._session(1, "locked-admin-token", unlocked=False)
        with self._client(locked_admin) as client:
            response = client.get("/v1/admin/overview")
        self.assertEqual(response.status_code, 423)
        self.assertEqual(response.get_json()["error"], "pin_locked")

    def test_admin_writes_fail_closed_without_trusted_browser_context(self):
        admin = self._session(1, "admin-origin-token")
        with self._client(admin) as client:
            missing_origin = client.post(
                "/v1/admin/invitations", json={"email": "missing@example.test"}
            )
            foreign_origin = client.post(
                "/v1/admin/invitations",
                json={"email": "foreign@example.test"},
                headers={"Origin": "https://attacker.invalid"},
            )
            suspicious_fetch = client.post(
                "/v1/admin/invitations",
                json={"email": "fetch@example.test"},
                headers={
                    "Origin": "https://getrove.de",
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "cors",
                },
            )
        self.assertEqual(missing_origin.status_code, 403)
        self.assertEqual(foreign_origin.status_code, 403)
        self.assertEqual(suspicious_fetch.status_code, 403)

    def test_same_origin_write_and_expected_failure_create_audits(self):
        admin = self._session(1, "admin-write-token")
        with self._client(admin) as client:
            invalid = client.post(
                "/v1/admin/invitations",
                json={"email": "", "days": 14},
                headers=self._same_origin_headers(),
            )
            success = client.post(
                "/v1/admin/invitations",
                json={"email": "new-user@example.test", "days": 14},
                headers=self._same_origin_headers(),
            )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(success.status_code, 200, success.get_json())

        rows = self._audit_rows()
        self.assertEqual([int(row["success"]) for row in rows], [0, 1])
        self.assertEqual(rows[0]["failure_reason"], "valid_email_required")
        self.assertEqual(rows[1]["action"], "invitation_created")
        self.assertTrue(rows[0]["request_correlation"])
        self.assertTrue(rows[1]["request_correlation"])
        serialized = " ".join(str(tuple(row)) for row in rows)
        self.assertNotIn("admin-write-token", serialized)
        self.assertNotIn("admin-security-test-secret", serialized)

    def test_admin_write_rate_limit_is_server_side(self):
        api.ADMIN_RATE_LIMITS["invitation_create"] = 1
        admin = self._session(1, "admin-rate-token")
        with self._client(admin) as client:
            first = client.post(
                "/v1/admin/invitations",
                json={"email": "first-rate@example.test"},
                headers=self._same_origin_headers(),
            )
            second = client.post(
                "/v1/admin/invitations",
                json={"email": "second-rate@example.test"},
                headers=self._same_origin_headers(),
            )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 429, second.get_json())
        self.assertGreaterEqual(int(second.headers["Retry-After"]), 1)
        self.assertEqual(self._audit_rows()[-1]["failure_reason"], "rate_limited")

    def test_coach_inspector_is_rate_limited_and_audited_without_evidence(self):
        api.ADMIN_RATE_LIMITS["coach_inspector_read"] = 1
        admin = self._session(1, "admin-inspector-token")
        with patch.object(api, "build_shadow_inspector", return_value={"patterns": []}):
            with self._client(admin) as client:
                first = client.get("/v1/admin/coach-patterns/2")
                second = client.get("/v1/admin/coach-patterns/2")
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 429, second.get_json())
        rows = self._audit_rows()
        self.assertEqual(rows[0]["action"], "coach_inspector_read")
        self.assertEqual(int(rows[0]["success"]), 1)
        self.assertEqual(rows[1]["failure_reason"], "rate_limited")
        self.assertEqual(rows[0]["details"], "{}")

    def test_audit_schema_contains_only_compact_security_fields(self):
        admin = self._session(1, "admin-schema-token")
        with self._client(admin) as client:
            response = client.post(
                "/v1/admin/invitations",
                json={"email": "schema@example.test"},
                headers=self._same_origin_headers(),
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(sqlite3.connect(self.db_path)) as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(app_admin_events)")
            }
        self.assertTrue(
            {"success", "failure_reason", "request_correlation"}.issubset(columns)
        )
        self.assertNotIn("evidence", columns)
        self.assertNotIn("session_token", columns)

    def test_legacy_admin_schema_is_upgraded_at_startup_not_request_time(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DROP TABLE app_admin_events")
            conn.execute(
                """CREATE TABLE app_admin_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    target_user_id INTEGER,
                    target_email TEXT DEFAULT '',
                    details TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                "INSERT INTO app_admin_events "
                "(admin_user_id, action, details) VALUES (1, 'legacy', 'kept')"
            )
            conn.commit()

        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertFalse(api.admin_schema_ready(conn))
            api.ensure_admin_tables(conn)
            conn.commit()
            self.assertTrue(api.admin_schema_ready(conn))
            row = conn.execute(
                "SELECT action, details, success, failure_reason, request_correlation "
                "FROM app_admin_events"
            ).fetchone()
            self.assertEqual(row, ("legacy", "kept", 1, "", ""))

    def test_admin_request_does_not_run_schema_ddl(self):
        admin = self._session(1, "admin-no-ddl-token")
        with patch.object(api, "ensure_admin_tables", side_effect=AssertionError("request DDL")):
            with patch.object(api, "build_shadow_inspector", return_value={"patterns": []}):
                with self._client(admin) as client:
                    response = client.get("/v1/admin/coach-patterns/2")
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_admin_schema_migration_is_idempotent_and_preserves_legacy_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "migration.db"
            with closing(sqlite3.connect(path)) as conn:
                self.assertFalse(api.admin_schema_ready(conn))
                api.ensure_admin_tables(conn)
                conn.commit()
                self.assertTrue(api.admin_schema_ready(conn))
                first_columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(app_admin_events)")
                }
            with closing(sqlite3.connect(path)) as conn:
                api.ensure_admin_tables(conn)
                conn.commit()
                second_columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(app_admin_events)")
                }
            self.assertEqual(first_columns, second_columns)

            path.unlink()
            with closing(sqlite3.connect(path)) as conn:
                conn.execute(
                    """CREATE TABLE app_admin_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        admin_user_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        target_user_id INTEGER,
                        target_email TEXT DEFAULT '',
                        details TEXT DEFAULT '',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        success INTEGER NOT NULL DEFAULT 1
                    )"""
                )
                conn.execute(
                    "INSERT INTO app_admin_events "
                    "(admin_user_id, action, target_email, details) "
                    "VALUES (202, 'access_revoke', 'deleted@example.test', 'private')"
                )
                conn.commit()

            with closing(sqlite3.connect(path)) as conn:
                self.assertFalse(api.admin_schema_ready(conn))
                api.ensure_admin_tables(conn)
                conn.commit()
                self.assertTrue(api.admin_schema_ready(conn))
                row = conn.execute(
                    "SELECT admin_user_id, action, target_email, details, success, "
                    "failure_reason, request_correlation FROM app_admin_events"
                ).fetchone()
                self.assertEqual(
                    row,
                    (202, "access_revoke", "deleted@example.test", "private", 1, "", ""),
                )
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
