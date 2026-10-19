from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api


class OnboardingDraftApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "synthetic.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE users(user_id INTEGER PRIMARY KEY, onboarding_step INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE user_access(user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                INSERT INTO users VALUES (1, 0);
                INSERT INTO users VALUES (2, 0);
                INSERT INTO user_access VALUES (1, 'app_only');
                INSERT INTO user_access VALUES (2, 'app_only');
            """)
            api.ensure_auth_tables(conn)
            for user_id in (1, 2):
                conn.execute(
                    "INSERT INTO app_accounts(email,user_id,verified_at,source) VALUES(?,?,CURRENT_TIMESTAMP,'app')",
                    (f"draft-{user_id}@example.test", user_id),
                )
            conn.commit()
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "onboarding-draft-test-secret-with-entropy"),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)
        self.tokens = {1: "synthetic-session-one", 2: "synthetic-session-two"}
        with closing(sqlite3.connect(self.db_path)) as conn:
            for user_id, raw_token in self.tokens.items():
                account_id = conn.execute(
                    "SELECT id FROM app_accounts WHERE user_id=?", (user_id,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO app_sessions(token_hash,account_id,expires_at) VALUES(?,?,?)",
                    (api.keyed_hash(raw_token), account_id,
                     (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
                )
            conn.commit()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def client(self, user_id):
        client = api.app.test_client()
        if user_id is not None:
            client.set_cookie(api.SESSION_COOKIE_NAME, self.tokens[user_id], domain="localhost", path="/")
        return client

    @staticmethod
    def draft(**changes):
        value = {
            "name": "Synthetic",
            "wantsApi": False,
            "income": 4200,
            "otherIncome": None,
            "otherIncomeLabel": "",
            "payday": 15,
            "contracts": {"miete": 1100},
            "customFix": [{"n": "Kita", "a": 250}],
            "wealth": {"giro": 5000},
            "etfRate": 300,
            "cashRate": 100,
            "etfExecutionDay": 1,
            "etfSource": "giro",
            "etfMode": "auto",
            "goalName": "Reise",
            "goalTar": 2500,
            "goalCur": 500,
            "autoFinType": None,
            "fixExpanded": False,
        }
        value.update(changes)
        return value

    def test_confirmed_pre_pin_draft_resumes_for_its_owner_only(self):
        with self.client(1) as owner:
            saved = owner.post("/v1/onboarding/draft", json={"step": 5, "draft": self.draft()})
            self.assertEqual(saved.status_code, 200, saved.get_json())
        with self.client(1) as resumed_client, self.client(2) as other, self.client(None) as anonymous:
            resumed = resumed_client.get("/v1/onboarding/draft")
            self.assertEqual(resumed.status_code, 200, resumed.get_json())
            self.assertEqual(resumed.get_json()["step"], 5)
            self.assertEqual(resumed.get_json()["draft"]["wealth"], {"giro": 5000.0})
            self.assertIsNone(other.get("/v1/onboarding/draft").get_json()["draft"])
            self.assertEqual(anonymous.get("/v1/onboarding/draft").status_code, 401)

    def test_existing_locked_pin_still_blocks_draft_access(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            api.ensure_session_pin_table(conn)
            session_id = conn.execute(
                "SELECT id FROM app_sessions WHERE token_hash=?",
                (api.keyed_hash(self.tokens[1]),),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO app_session_pins(session_id,pin_verifier) VALUES(?, 'synthetic-verifier')",
                (session_id,),
            )
            conn.commit()
        with self.client(1) as client:
            response = client.post("/v1/onboarding/draft", json={"step": 2, "draft": self.draft()})
        self.assertEqual(response.status_code, 423)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertFalse(api.table_exists(conn, "app_onboarding_drafts"))

    def test_invalid_and_oversized_drafts_are_rejected_without_saving(self):
        with self.client(1) as client:
            invalid = client.post("/v1/onboarding/draft", json={"step": 10, "draft": self.draft()})
            invalid_enum = client.post(
                "/v1/onboarding/draft",
                json={"step": 3, "draft": {**self.draft(), "etfMode": []}},
            )
            oversized = client.post(
                "/v1/onboarding/draft",
                json={"step": 3, "draft": {**self.draft(), "name": "x" * 40_000}},
            )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid_enum.status_code, 400)
        self.assertEqual(oversized.status_code, 413)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertFalse(api.table_exists(conn, "app_onboarding_drafts"))

    def test_completed_account_cannot_read_or_write_old_draft(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET onboarding_step=10 WHERE user_id=1")
            conn.commit()
        with patch.object(api, "pin_state", return_value="unlocked"):
            with self.client(1) as client:
                read = client.get("/v1/onboarding/draft")
                write = client.post("/v1/onboarding/draft", json={"step": 3, "draft": self.draft()})
        self.assertEqual(read.status_code, 409)
        self.assertEqual(write.status_code, 409)


if __name__ == "__main__":
    unittest.main()
