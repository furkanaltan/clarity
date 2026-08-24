import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api


class PasswordAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE users (user_id INTEGER PRIMARY KEY, onboarding_step INTEGER DEFAULT 10);
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                INSERT INTO users VALUES (1, 10);
                INSERT INTO users VALUES (2, 10);
                INSERT INTO user_access VALUES (1, 'approved');
                INSERT INTO user_access VALUES (2, 'approved');
            """)
            api.ensure_auth_tables(conn)
            conn.execute(
                "INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES (?, ?, CURRENT_TIMESTAMP, 'app')",
                ("first@example.test", 1),
            )
            conn.execute(
                "INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES (?, ?, CURRENT_TIMESTAMP, 'app')",
                ("second@example.test", 2),
            )
            conn.commit()
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "test-auth-secret"),
            patch.object(api, "PUBLIC_APP_STATE_BASE_URL", "https://state.example.test"),
            patch.object(api, "create_state_url_for_user", lambda _conn, user_id: f"https://state.example.test/{user_id}.json"),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.AUTH_BUCKETS.clear()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def account_id(self, user_id=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return int(conn.execute("SELECT id FROM app_accounts WHERE user_id = ?", (user_id,)).fetchone()[0])

    def issue_session(self, user_id=1, raw_token="session-token"):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO app_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (api.keyed_hash(raw_token), self.account_id(user_id), (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        return raw_token

    @staticmethod
    def password_payload(password="very-safe-password"):
        return {"password": password, "password_confirmation": password}

    def setup_password(self, raw_token="session-token"):
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, raw_token, domain="localhost", path="/")
            return client.post("/v1/auth/password/setup", json=self.password_payload())

    def test_existing_code_login_can_set_password_without_changing_user(self):
        raw_token = self.issue_session()
        response = self.setup_password(raw_token)
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(sqlite3.connect(self.db_path)) as conn:
            credential = conn.execute("SELECT password_hash FROM app_credentials WHERE account_id = ?", (self.account_id(),)).fetchone()
            user_id = conn.execute("SELECT user_id FROM app_accounts WHERE id = ?", (self.account_id(),)).fetchone()[0]
        self.assertTrue(api.PASSWORD_HASHER.verify(credential[0], "very-safe-password"))
        self.assertEqual(user_id, 1)

    def test_password_login_and_neutral_failures(self):
        self.issue_session()
        self.assertEqual(self.setup_password().status_code, 200)
        with api.app.test_client() as client:
            bad_email = client.post("/v1/auth/password/login", json={"email": "unknown@example.test", "password": "very-safe-password"})
            bad_password = client.post("/v1/auth/password/login", json={"email": "first@example.test", "password": "wrong-password"})
            success = client.post("/v1/auth/password/login", json={"email": "first@example.test", "password": "very-safe-password"})
        self.assertEqual((bad_email.status_code, bad_email.get_json()["error"]), (401, "invalid_credentials"))
        self.assertEqual((bad_password.status_code, bad_password.get_json()["error"]), (401, "invalid_credentials"))
        self.assertEqual(success.status_code, 200, success.get_json())
        self.assertEqual(success.get_json()["state_url"], "https://state.example.test/1.json")

    def test_reset_uses_one_time_hmac_code_and_revokes_old_sessions(self):
        self.issue_session(raw_token="old-session")
        self.assertEqual(self.setup_password("old-session").status_code, 200)
        with patch.object(api.secrets, "randbelow", return_value=123456), patch.object(api, "send_password_reset_email") as send:
            with api.app.test_client() as client:
                requested = client.post("/v1/auth/password/reset/request", json={"email": "first@example.test"})
                confirmed = client.post("/v1/auth/password/reset/confirm", json={
                    "email": "first@example.test", "code": "123456", **self.password_payload("new-safe-password"),
                })
        self.assertEqual(requested.status_code, 200)
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        send.assert_called_once()
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_sessions WHERE revoked_at IS NULL").fetchone()[0], 1)
            self.assertIsNotNone(conn.execute("SELECT consumed_at FROM app_password_reset_codes").fetchone()[0])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_password_reset_codes WHERE code_hash = '123456'").fetchone()[0], 0)

    def test_change_password_requires_current_password_and_reissues_session(self):
        self.issue_session(raw_token="first-session")
        self.assertEqual(self.setup_password("first-session").status_code, 200)
        self.issue_session(raw_token="other-device")
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, "first-session", domain="localhost", path="/")
            denied = client.post("/v1/auth/password/change", json={"current_password": "bad", **self.password_payload("changed-password")})
            changed = client.post("/v1/auth/password/change", json={"current_password": "very-safe-password", **self.password_payload("changed-password")})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(changed.status_code, 200, changed.get_json())
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_sessions WHERE revoked_at IS NULL").fetchone()[0], 1)

    def test_rate_limit_buckets_are_separate(self):
        with api.app.test_request_context("/v1/auth/password/login"):
            for _ in range(api.AUTH_ATTEMPT_LIMIT):
                self.assertTrue(api.auth_attempt_allowed("password_login", "first@example.test"))
            self.assertFalse(api.auth_attempt_allowed("password_login", "first@example.test"))
            self.assertTrue(api.auth_attempt_allowed("password_reset_request", "first@example.test"))

    def test_invalid_password_policy_and_missing_auth_secret_fail_closed(self):
        self.issue_session()
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, "session-token", domain="localhost", path="/")
            short = client.post("/v1/auth/password/setup", json=self.password_payload("short"))
        self.assertEqual(short.status_code, 400)
        with patch.object(api, "AUTH_SECRET", ""):
            with self.assertRaisesRegex(RuntimeError, "app_auth_not_configured"):
                api.keyed_hash("anything")

    def test_export_allowlist_excludes_authentication_tables(self):
        exported = {table for _label, table in api.DATA_EXPORT_TABLES}
        self.assertFalse({"app_credentials", "app_password_reset_codes", "app_sessions", "app_login_codes"} & exported)


if __name__ == "__main__":
    unittest.main()
