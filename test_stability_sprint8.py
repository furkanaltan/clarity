import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE users (user_id INTEGER PRIMARY KEY, onboarding_step INTEGER DEFAULT 10);
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                INSERT INTO users VALUES (1, 10);
                INSERT INTO users VALUES (2, 10);
                INSERT INTO user_access VALUES (1, 'approved');
                INSERT INTO user_access VALUES (2, 'approved');
                """
            )
            api.ensure_auth_tables(conn)
            conn.execute(
                "INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES (?, ?, CURRENT_TIMESTAMP, 'telegram')",
                ("legacy@example.test", 1),
            )
            conn.execute(
                "INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES (?, ?, CURRENT_TIMESTAMP, 'app')",
                ("other@example.test", 2),
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
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def issue_session(self, raw_token="session-token"):
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_id = conn.execute("SELECT id FROM app_accounts WHERE user_id = 1").fetchone()[0]
            conn.execute(
                "INSERT INTO app_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (api.keyed_hash(raw_token), account_id, (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        return raw_token

    def test_logout_revokes_session_is_idempotent_and_blocks_auth_me(self):
        raw_token = self.issue_session()
        with api.app.test_client() as client:
            # Flask tests the API below /v1; production nginx exposes that path below /app-api/.
            client.set_cookie(api.SESSION_COOKIE_NAME, raw_token, domain="localhost", path="/")
            first = client.post("/v1/auth/logout")
            second = client.post("/v1/auth/logout")
            blocked = client.get("/v1/auth/me")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(blocked.status_code, 401)
        with closing(sqlite3.connect(self.db_path)) as conn:
            revoked_at = conn.execute("SELECT revoked_at FROM app_sessions").fetchone()[0]
        self.assertIsNotNone(revoked_at)

    def test_verified_legacy_web_identity_logs_in_without_telegram_pairing(self):
        with patch.object(api.secrets, "randbelow", return_value=123456), patch.object(api, "send_login_email") as send:
            with api.app.test_client() as client:
                requested = client.post("/v1/auth/request-code", json={"email": "legacy@example.test"})
                verified = client.post("/v1/auth/verify-code", json={"email": "legacy@example.test", "code": "123456"})
        self.assertEqual(requested.status_code, 200, requested.get_json())
        self.assertFalse(requested.get_json()["needsPairing"])
        send.assert_called_once()
        self.assertEqual(verified.status_code, 200, verified.get_json())
        self.assertEqual(verified.get_json()["state_url"], "https://state.example.test/1.json")

    def test_verified_email_stays_bound_to_its_own_user(self):
        with patch.object(api.secrets, "randbelow", return_value=654321), patch.object(api, "send_login_email"):
            with api.app.test_client() as client:
                client.post("/v1/auth/request-code", json={"email": "other@example.test"})
                verified = client.post("/v1/auth/verify-code", json={"email": "other@example.test", "code": "654321"})
        self.assertEqual(verified.status_code, 200, verified.get_json())
        self.assertEqual(verified.get_json()["state_url"], "https://state.example.test/2.json")

    def test_logout_then_second_user_login_uses_only_second_user_state(self):
        raw_token = self.issue_session()
        with patch.object(api.secrets, "randbelow", return_value=222222), patch.object(api, "send_login_email"):
            with api.app.test_client() as client:
                client.set_cookie(api.SESSION_COOKIE_NAME, raw_token, domain="localhost", path="/")
                self.assertEqual(client.post("/v1/auth/logout").status_code, 200)
                self.assertEqual(
                    client.post("/v1/auth/request-code", json={"email": "other@example.test"}).status_code,
                    200,
                )
                second_user = client.post(
                    "/v1/auth/verify-code", json={"email": "other@example.test", "code": "222222"}
                )
        self.assertEqual(second_user.status_code, 200, second_user.get_json())
        self.assertEqual(second_user.get_json()["state_url"], "https://state.example.test/2.json")

    def test_unknown_email_cannot_claim_a_legacy_user(self):
        with api.app.test_client() as client:
            response = client.post("/v1/auth/request-code", json={"email": "unknown@example.test"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "pairing_code_required")


if __name__ == "__main__":
    unittest.main()
