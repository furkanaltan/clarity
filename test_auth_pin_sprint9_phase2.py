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


def ensure_unlocked_test_session(db_path, user_id, raw_token, pin="1234"):
    """Create the current cookie-session/PIN fixture without weakening product auth."""
    with closing(sqlite3.connect(db_path)) as conn:
        api.ensure_auth_tables(conn)
        account = conn.execute(
            "SELECT id FROM app_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        if account:
            account_id = int(account[0])
        else:
            cursor = conn.execute(
                """INSERT INTO app_accounts (email, user_id, verified_at, source)
                   VALUES (?, ?, CURRENT_TIMESTAMP, 'app')""",
                (f"wave4-user-{user_id}@example.test", user_id),
            )
            account_id = int(cursor.lastrowid)

        token_hash = api.keyed_hash(raw_token)
        session = conn.execute(
            "SELECT id FROM app_sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if session:
            session_id = int(session[0])
        else:
            cursor = conn.execute(
                """INSERT INTO app_sessions (token_hash, account_id, expires_at)
                   VALUES (?, ?, ?)""",
                (
                    token_hash,
                    account_id,
                    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            session_id = int(cursor.lastrowid)

        verifier = api.PASSWORD_HASHER.hash(api.pin_secret_value(session_id, pin))
        conn.execute(
            """INSERT INTO app_session_pins
                   (session_id, pin_verifier, failed_attempts, locked_out_at,
                    unlocked_at, last_activity_at, updated_at)
               VALUES (?, ?, 0, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(session_id) DO UPDATE SET
                   pin_verifier = excluded.pin_verifier,
                   failed_attempts = 0,
                   locked_out_at = NULL,
                   unlocked_at = CURRENT_TIMESTAMP,
                   last_activity_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP""",
            (session_id, verifier),
        )
        conn.commit()
    return session_id


class AppPinAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    onboarding_step INTEGER DEFAULT 10
                );
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                INSERT INTO users VALUES (1, 10);
                INSERT INTO users VALUES (2, 10);
                INSERT INTO user_access VALUES (1, 'approved');
                INSERT INTO user_access VALUES (2, 'approved');
                """
            )
            api.ensure_auth_tables(conn)
            conn.execute(
                "INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES (?, 1, CURRENT_TIMESTAMP, 'app')",
                ("first@example.test",),
            )
            conn.execute(
                "INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES (?, 2, CURRENT_TIMESTAMP, 'app')",
                ("second@example.test",),
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
            patch.object(api, "AUTH_SECRET", "pin-test-secret-with-enough-entropy"),
            patch.object(api, "PASSWORD_HASHER", fast_hasher),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.PIN_ATTEMPT_BUCKETS.clear()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def account_id(self, user_id=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return int(conn.execute(
                "SELECT id FROM app_accounts WHERE user_id = ?", (user_id,)
            ).fetchone()[0])

    def issue_session(self, raw_token, user_id=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                "INSERT INTO app_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (
                    api.keyed_hash(raw_token),
                    self.account_id(user_id),
                    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def client_for(self, raw_token):
        client = api.app.test_client()
        client.set_cookie(api.SESSION_COOKIE_NAME, raw_token, domain="localhost", path="/")
        return client

    def setup_pin(self, client, pin="1234"):
        return client.post(
            "/v1/auth/pin/setup",
            json={"pin": pin, "pin_confirmation": pin},
        )

    def test_setup_accepts_four_digits_and_leading_zero_without_plaintext_storage(self):
        self.issue_session("device-a")
        with self.client_for("device-a") as client:
            response = self.setup_pin(client, "0007")
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(sqlite3.connect(self.db_path)) as conn:
            verifier = str(conn.execute("SELECT pin_verifier FROM app_session_pins").fetchone()[0])
        self.assertNotIn("0007", verifier)
        self.assertTrue(verifier.startswith("$argon2id$"))

    def test_setup_rejects_invalid_lengths_characters_and_mismatch(self):
        self.issue_session("device-a")
        with self.client_for("device-a") as client:
            for pin, confirmation in (("123", "123"), ("12345", "12345"), ("12a4", "12a4"), ("1234", "4321")):
                with self.subTest(pin=pin, confirmation=confirmation):
                    response = client.post(
                        "/v1/auth/pin/setup",
                        json={"pin": pin, "pin_confirmation": confirmation},
                    )
                    self.assertEqual(response.status_code, 400)

    def test_finance_api_is_centrally_locked_before_setup_and_after_explicit_lock(self):
        self.issue_session("device-a")
        with self.client_for("device-a") as client:
            locked = client.get("/v1/state")
            self.assertEqual(locked.status_code, 423)
            self.assertEqual(locked.get_json()["error"], "pin_locked")
            self.assertEqual(locked.get_json()["pin_status"], "setup_required")
            self.assertNotIn("assets", locked.get_json())
            self.assertEqual(self.setup_pin(client).status_code, 200)
            passed_gate = client.get("/v1/admin/overview")
            self.assertEqual(passed_gate.status_code, 403)
            self.assertEqual(client.post("/v1/auth/pin/lock", json={}).status_code, 200)
            relocked = client.get("/v1/admin/overview")
            self.assertEqual(relocked.status_code, 423)

    def test_third_wrong_attempt_requires_reauthentication_even_for_correct_pin(self):
        self.issue_session("device-a")
        with self.client_for("device-a") as client:
            self.assertEqual(self.setup_pin(client).status_code, 200)
            self.assertEqual(client.post("/v1/auth/pin/lock", json={}).status_code, 200)
            for attempt in range(1, 4):
                response = client.post("/v1/auth/pin/unlock", json={"pin": "9999"})
                self.assertEqual(response.status_code, 423 if attempt == 3 else 400)
            correct = client.post("/v1/auth/pin/unlock", json={"pin": "1234"})
        self.assertEqual(correct.status_code, 423)
        self.assertTrue(correct.get_json()["reauth_required"])

    def test_device_sessions_have_independent_pins_and_lockouts(self):
        self.issue_session("device-a")
        self.issue_session("device-b")
        device_a = self.client_for("device-a")
        device_b = self.client_for("device-b")
        self.assertEqual(self.setup_pin(device_a, "1234").status_code, 200)
        self.assertEqual(self.setup_pin(device_b, "9876").status_code, 200)
        device_a.post("/v1/auth/pin/lock", json={})
        for _ in range(3):
            device_a.post("/v1/auth/pin/unlock", json={"pin": "0000"})
        foreign_pin = device_b.post("/v1/auth/pin/lock", json={})
        self.assertEqual(foreign_pin.status_code, 200)
        self.assertEqual(device_b.post("/v1/auth/pin/unlock", json={"pin": "1234"}).status_code, 400)
        self.assertEqual(device_b.post("/v1/auth/pin/unlock", json={"pin": "9876"}).status_code, 200)
        self.assertEqual(device_b.get("/v1/admin/overview").status_code, 403)

    def test_two_minute_grace_is_server_enforced_and_reload_cannot_bypass_it(self):
        session_id = self.issue_session("device-a")
        with self.client_for("device-a") as client:
            self.assertEqual(self.setup_pin(client).status_code, 200)
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.execute(
                    "UPDATE app_session_pins SET last_activity_at = datetime('now', '-119 seconds') WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            # A reload/status request within the grace period remains unlocked.
            self.assertEqual(client.get("/v1/auth/pin/status").get_json()["pin_status"], "unlocked")
            within_grace = client.get("/v1/admin/overview")
            self.assertEqual(within_grace.status_code, 403)
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.execute(
                    "UPDATE app_session_pins SET last_activity_at = datetime('now', '-121 seconds') WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            first = client.get("/v1/admin/overview")
            second = client.get("/v1/admin/overview")
        self.assertEqual(first.status_code, 423)
        self.assertEqual(second.status_code, 423)
        self.assertEqual(second.get_json()["pin_status"], "locked")

    def test_active_session_activity_ping_prevents_relock(self):
        session_id = self.issue_session("device-a")
        with self.client_for("device-a") as client:
            self.assertEqual(self.setup_pin(client).status_code, 200)
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.execute(
                    "UPDATE app_session_pins SET last_activity_at = datetime('now', '-110 seconds') WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            self.assertEqual(client.post("/v1/auth/pin/activity", json={}).status_code, 200)
            # Two activity touches spanning more than two minutes keep active use unlocked.
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.execute(
                    "UPDATE app_session_pins SET last_activity_at = datetime('now', '-110 seconds') WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            self.assertEqual(client.post("/v1/auth/pin/activity", json={}).status_code, 200)
            self.assertEqual(client.get("/v1/admin/overview").status_code, 403)

    def test_new_user_onboarding_is_the_only_pre_pin_finance_write_exception(self):
        self.issue_session("new-user")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET onboarding_step = 0 WHERE user_id = 1")
            conn.commit()
        with self.client_for("new-user") as client:
            status = client.get("/v1/auth/pin/status")
            self.assertTrue(status.get_json()["onboarding_required"])
            with api.app.test_request_context(
                "/v1/onboarding",
                method="POST",
                headers={"Cookie": f"{api.SESSION_COOKIE_NAME}=new-user"},
            ):
                with api.db() as conn:
                    self.assertIsNotNone(api.session_user_from_cookie(conn))
                self.assertIsNone(api.enforce_session_pin())
            self.assertEqual(client.get("/v1/state").status_code, 423)

    def test_pin_change_requires_current_pin_and_replaces_it(self):
        self.issue_session("device-a")
        with self.client_for("device-a") as client:
            self.assertEqual(self.setup_pin(client, "1234").status_code, 200)
            denied = client.post("/v1/auth/pin/change", json={
                "current_pin": "9999",
                "new_pin": "4321",
                "new_pin_confirmation": "4321",
            })
            changed = client.post("/v1/auth/pin/change", json={
                "current_pin": "1234",
                "new_pin": "4321",
                "new_pin_confirmation": "4321",
            })
            client.post("/v1/auth/pin/lock", json={})
            old_pin = client.post("/v1/auth/pin/unlock", json={"pin": "1234"})
            new_pin = client.post("/v1/auth/pin/unlock", json={"pin": "4321"})
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(old_pin.status_code, 400)
        self.assertEqual(new_pin.status_code, 200)

    def test_password_recovery_replaces_device_pin_after_lockout(self):
        self.issue_session("device-a")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO app_credentials (account_id, password_hash) VALUES (?, ?)",
                (self.account_id(), api.PASSWORD_HASHER.hash("very-safe-password")),
            )
            conn.commit()
        with self.client_for("device-a") as client:
            self.assertEqual(self.setup_pin(client, "1234").status_code, 200)
            client.post("/v1/auth/pin/lock", json={})
            for _ in range(3):
                client.post("/v1/auth/pin/unlock", json={"pin": "9999"})
            denied = client.post("/v1/auth/pin/recover", json={
                "email": "first@example.test",
                "password": "wrong-password",
                "pin": "4321",
                "pin_confirmation": "4321",
            })
            recovered = client.post("/v1/auth/pin/recover", json={
                "email": "first@example.test",
                "password": "very-safe-password",
                "pin": "4321",
                "pin_confirmation": "4321",
            })
            client.post("/v1/auth/pin/lock", json={})
            old_pin = client.post("/v1/auth/pin/unlock", json={"pin": "1234"})
            new_pin = client.post("/v1/auth/pin/unlock", json={"pin": "4321"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(old_pin.status_code, 400)
        self.assertEqual(new_pin.status_code, 200)

    def test_logout_revokes_session_and_pin_cannot_restore_access(self):
        self.issue_session("device-a")
        with self.client_for("device-a") as client:
            self.assertEqual(self.setup_pin(client).status_code, 200)
            self.assertEqual(client.post("/v1/auth/logout", json={}).status_code, 200)
            self.assertEqual(client.post("/v1/auth/pin/unlock", json={"pin": "1234"}).status_code, 401)
            self.assertEqual(client.get("/v1/state").status_code, 401)

    def test_pin_security_state_is_not_exported_and_is_explicitly_deleted_with_sessions(self):
        exported = {table for _label, table in api.DATA_EXPORT_TABLES}
        self.assertNotIn("app_session_pins", exported)
        source = Path(api.__file__).read_text(encoding="utf-8")
        self.assertIn("DELETE FROM app_session_pins", source)


if __name__ == "__main__":
    unittest.main()
