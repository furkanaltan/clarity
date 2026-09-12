import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_feature_announcements import (
    DEFAULT_FEATURE_ANNOUNCEMENTS,
    SAFE_DEEP_LINKS,
    ensure_feature_announcement_tables,
    get_feature_announcements_for_user,
    seed_default_feature_announcements,
)
from rove_financial_accounts import ensure_financial_account_reference_schema, set_feature_enabled


class FeatureAnnouncementSprintOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE users (user_id INTEGER PRIMARY KEY, onboarding_step INTEGER DEFAULT 10);
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE portfolio_holdings (id INTEGER PRIMARY KEY, user_id INTEGER, instrument_type TEXT);
                CREATE TABLE report_jobs (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT);
                CREATE TABLE app_user_features (
                    user_id INTEGER NOT NULL, feature_key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, feature_key)
                );
                INSERT INTO users VALUES (1, 10); INSERT INTO users VALUES (2, 10);
                INSERT INTO user_access VALUES (1, 'approved'); INSERT INTO user_access VALUES (2, 'approved');
            """)
            api.ensure_auth_tables(conn)
            ensure_financial_account_reference_schema(conn)
            ensure_feature_announcement_tables(conn)
            conn.execute("INSERT INTO app_accounts (email, user_id, verified_at, created_at, source) VALUES ('one@test', 1, CURRENT_TIMESTAMP, datetime('now', '-10 days'), 'app')")
            conn.execute("INSERT INTO app_accounts (email, user_id, verified_at, created_at, source) VALUES ('two@test', 2, CURRENT_TIMESTAMP, datetime('now', '-1 day'), 'app')")
            conn.commit()
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "announcement-test-secret-with-enough-entropy"),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def _insert(self, feature_id, **overrides):
        values = {
            "title": feature_id, "priority": "major", "published_at": "CURRENT_TIMESTAMP",
            "deep_link": "talk", "is_active": 1,
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join(value if value == "CURRENT_TIMESTAMP" else "?" for value in values.values())
        params = [feature_id] + [value for value in values.values() if value != "CURRENT_TIMESTAMP"]
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                f"INSERT INTO app_feature_announcements (feature_id, {columns}) VALUES (?, {placeholders})",
                params,
            )
            conn.commit()

    def _client(self, user_id=1, token="device-a", unlocked=True):
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_id = conn.execute("SELECT id FROM app_accounts WHERE user_id = ?", (user_id,)).fetchone()[0]
            session = conn.execute(
                "INSERT INTO app_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (api.keyed_hash(token), account_id, (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            if unlocked:
                conn.execute("INSERT INTO app_session_pins (session_id, pin_verifier, unlocked_at, last_activity_at) VALUES (?, 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (session.lastrowid,))
            conn.commit()
        client = api.app.test_client()
        client.set_cookie(api.SESSION_COOKIE_NAME, token, domain="localhost", path="/")
        return client

    def _for_user(self, user_id=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return get_feature_announcements_for_user(conn, user_id)

    def test_schema_is_additive_and_rerunnable(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            ensure_feature_announcement_tables(conn)
            ensure_feature_announcement_tables(conn)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"app_feature_announcements", "app_feature_announcement_state"} <= tables)

    def test_initial_release_is_explicit_idempotent_and_creates_no_user_state(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(seed_default_feature_announcements(conn), [
                item["feature_id"] for item in DEFAULT_FEATURE_ANNOUNCEMENTS
            ])
            first_published_at = dict(conn.execute(
                "SELECT feature_id, published_at FROM app_feature_announcements"
            ).fetchall())
            self.assertEqual(seed_default_feature_announcements(conn), [])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_feature_announcement_state"
            ).fetchone()[0], 0)
            self.assertEqual(dict(conn.execute(
                "SELECT feature_id, published_at FROM app_feature_announcements"
            ).fetchall()), first_published_at)
            conn.commit()
        self.assertEqual(
            {item["feature_id"] for item in self._for_user()["archive"]},
            {item["feature_id"] for item in DEFAULT_FEATURE_ANNOUNCEMENTS},
        )

    def test_global_definition_is_lazy_and_state_actions_are_idempotent(self):
        self._insert("rove_ai_v1")
        self.assertEqual(self._for_user()["unseen_count"], 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_feature_announcement_state").fetchone()[0], 0)
        client = self._client()
        for _ in range(2):
            self.assertEqual(client.post("/v1/feature-announcements/rove_ai_v1/opened").status_code, 200)
        self.assertEqual(client.post("/v1/feature-announcements/rove_ai_v1/dismissed").status_code, 200)
        self.assertEqual(client.post("/v1/feature-announcements/rove_ai_v1/completed").status_code, 200)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT seen_at, opened_at, dismissed_at, completed_at FROM app_feature_announcement_state WHERE user_id=1 AND feature_id='rove_ai_v1'").fetchone()
        self.assertTrue(all(row))

    def test_each_interaction_keeps_its_earliest_timestamp(self):
        self._insert("top_merchants_v1")
        client = self._client()
        for action, required in (("seen", "seen_at"), ("opened", "opened_at"), ("dismissed", "dismissed_at"), ("completed", "completed_at")):
            with self.subTest(action=action):
                response = client.post(f"/v1/feature-announcements/top_merchants_v1/{action}")
                self.assertEqual(response.status_code, 200)
                with closing(sqlite3.connect(self.db_path)) as conn:
                    self.assertIsNotNone(conn.execute(
                        f"SELECT {required} FROM app_feature_announcement_state WHERE user_id=1 AND feature_id='top_merchants_v1'"
                    ).fetchone()[0])

    def test_multiple_devices_same_user_produce_one_state_row(self):
        self._insert("rove_ai_v1")
        self.assertEqual(self._client(token="phone").post("/v1/feature-announcements/rove_ai_v1/seen").status_code, 200)
        self.assertEqual(self._client(token="tablet").post("/v1/feature-announcements/rove_ai_v1/opened").status_code, 200)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_feature_announcement_state WHERE user_id=1 AND feature_id='rove_ai_v1'").fetchone()[0], 1)

    def test_users_are_isolated_and_pin_and_auth_are_enforced(self):
        self._insert("crypto_tracking_v1")
        self.assertEqual(api.app.test_client().post("/v1/feature-announcements/crypto_tracking_v1/seen").status_code, 401)
        self.assertEqual(self._client(token="locked", unlocked=False).post("/v1/feature-announcements/crypto_tracking_v1/seen").status_code, 423)
        self.assertEqual(self._client(user_id=1).post("/v1/feature-announcements/crypto_tracking_v1/seen").status_code, 200)
        self.assertFalse(self._for_user(2)["archive"][0]["state"]["seen"])
        self.assertEqual(self._client(user_id=2, token="device-b").post("/v1/feature-announcements/not_real/seen").status_code, 404)

    def test_active_windows_new_accounts_flags_and_safe_links(self):
        self._insert("active_now")
        self._insert("future", active_from="2099-01-01 00:00:00")
        self._insert("expired", active_until="2000-01-01 00:00:00")
        self._insert("old", published_at="2000-01-01 00:00:00")
        self._insert("always", published_at="2000-01-01 00:00:00", always_relevant=1)
        self._insert("flagged", feature_flag="crypto_v1")
        self._insert("unsafe_link", deep_link="https://outside.example")
        first = self._for_user()
        ids = {item["feature_id"] for item in first["archive"]}
        self.assertEqual(ids, {"active_now", "always", "unsafe_link"})
        self.assertIsNone(next(item for item in first["archive"] if item["feature_id"] == "unsafe_link")["deep_link"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            set_feature_enabled(conn, 1, "crypto_v1", True)
            conn.commit()
        self.assertIn("flagged", {item["feature_id"] for item in self._for_user()["archive"]})

    def test_priority_constraint_and_invalid_feature_id_are_safe(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO app_feature_announcements (feature_id, title, priority) VALUES ('bad_priority', 'Bad', 'urgent')")
        self._insert("valid_one")
        response = self._client().post("/v1/feature-announcements/../../outside/seen")
        self.assertEqual(response.status_code, 404)

    def test_ninety_day_rule_and_usage_foundation_do_not_create_state_rows(self):
        self._insert("recent")
        self._insert("old_archive", published_at="2000-01-01 00:00:00", always_relevant=1)
        self._insert("rove_ai_v1")
        self._insert("crypto_tracking_v1")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE app_ai_conversation_messages (id INTEGER PRIMARY KEY, user_id INTEGER)")
            conn.execute("INSERT INTO app_ai_conversation_messages (user_id) VALUES (1)")
            conn.execute("INSERT INTO portfolio_holdings (user_id, instrument_type) VALUES (1, 'crypto')")
            conn.commit()
        result = self._for_user()
        self.assertEqual({item["feature_id"] for item in result["eligible"]}, {"recent"})
        archived = {item["feature_id"]: item for item in result["archive"]}
        self.assertTrue(archived["rove_ai_v1"]["state"]["completed"])
        self.assertTrue(archived["crypto_tracking_v1"]["state"]["completed"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_feature_announcement_state").fetchone()[0], 0)

    def test_report_usage_is_a_boolean_completion_signal(self):
        self._insert("report_v2")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("INSERT INTO report_jobs (user_id, status) VALUES (1, 'sent')")
            conn.commit()
        item = next(item for item in self._for_user()["archive"] if item["feature_id"] == "report_v2")
        self.assertTrue(item["state"]["completed"])
        self.assertNotIn("report_jobs", str(item))

    def test_state_rows_cascade_when_the_account_user_is_removed(self):
        self._insert("crypto_tracking_v1")
        self.assertEqual(self._client().post("/v1/feature-announcements/crypto_tracking_v1/seen").status_code, 200)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM users WHERE user_id = 1")
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_feature_announcement_state WHERE user_id=1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_feature_announcements WHERE feature_id='crypto_tracking_v1'").fetchone()[0], 1)

    def test_state_response_is_prepared_but_existing_ui_need_not_consume_it(self):
        self._insert("top_merchants_v1", priority="minor")
        with (
            patch.object(api, "apply_due_scheduled_savings"),
            patch.object(api, "record_due_etf_plan"),
            patch.object(api, "build_live_app_data", return_value={"assets": []}),
        ):
            response = self._client().get("/v1/state")
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()["feature_announcements"]
        self.assertEqual(payload["unseen_count"], 1)
        self.assertEqual(payload["eligible"][0]["feature_id"], "top_merchants_v1")
        self.assertTrue(SAFE_DEEP_LINKS)


if __name__ == "__main__":
    unittest.main()
