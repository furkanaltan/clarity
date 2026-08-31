import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
import retire_legacy_app_state as retirement


class StateLinkSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        self.state_dir = Path(self.temp.name) / "app-state"
        self.state_dir.mkdir()
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
            conn.execute("INSERT INTO app_accounts (email,user_id,verified_at,source) VALUES ('one@example.test',1,CURRENT_TIMESTAMP,'app')")
            conn.execute("INSERT INTO app_accounts (email,user_id,verified_at,source) VALUES ('two@example.test',2,CURRENT_TIMESTAMP,'app')")
            conn.execute("UPDATE app_state_links SET status='active'")
            conn.execute("INSERT INTO app_state_links(token,user_id,expires_at,status) VALUES ('old-bearer',1,'2099-01-01','active')")
            conn.commit()
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "test-auth-secret"),
            patch.object(api, "ensure_market_tracking_schema", lambda _conn: None),
            patch.object(api, "apply_due_scheduled_savings", lambda *_args: None),
            patch.object(api, "record_due_etf_plan", lambda *_args: None),
            patch.object(api, "build_live_app_data", lambda _conn, uid: {"user_id": uid, "identity": {"name": f"User {uid}"}, "sts": {"available": 0}}),
            patch.object(state, "DB_PATH", self.db_path),
            patch.object(state, "PUBLIC_APP_STATE_DIR", self.state_dir),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def session(self, user_id, raw_token):
        with closing(sqlite3.connect(self.db_path)) as conn:
            account_id = conn.execute("SELECT id FROM app_accounts WHERE user_id=?", (user_id,)).fetchone()[0]
            conn.execute(
                "INSERT INTO app_sessions(token_hash,account_id,expires_at) VALUES (?,?,?)",
                (api.keyed_hash(raw_token), account_id, (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()

    def state_request(self, user_id=None, raw_token="session-one", bearer=None):
        with api.app.test_client() as client:
            if user_id is not None:
                client.set_cookie(api.SESSION_COOKIE_NAME, raw_token, domain="localhost", path="/")
            headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
            return client.get("/v1/state", headers=headers)

    def test_no_session_and_old_bearer_cannot_read_state(self):
        self.assertEqual(self.state_request().status_code, 401)
        self.assertEqual(self.state_request(bearer="old-bearer").status_code, 401)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM app_state_links WHERE token='old-bearer'").fetchone()[0],
                "revoked",
            )

    def test_cookie_session_returns_only_its_own_uncached_state(self):
        self.session(1, "session-one")
        self.session(2, "session-two")
        first = self.state_request(1, "session-one")
        second = self.state_request(2, "session-two")
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(first.get_json()["user_id"], 1)
        self.assertEqual(second.get_json()["user_id"], 2)
        self.assertNotIn("api", first.get_json())
        self.assertEqual(first.headers["Cache-Control"], "no-store")

    def test_logout_blocks_state_and_old_bearer(self):
        self.session(1, "session-one")
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, "session-one", domain="localhost", path="/")
            self.assertEqual(client.get("/v1/state").status_code, 200)
            self.assertEqual(client.post("/v1/auth/logout").status_code, 200)
            self.assertEqual(client.get("/v1/state").status_code, 401)
            self.assertEqual(client.get("/v1/state", headers={"Authorization": "Bearer old-bearer"}).status_code, 401)

    def test_public_state_writer_is_side_effect_free(self):
        stale = self.state_dir / "old-bearer.json"
        stale.write_text('{"api":{"token":"old-bearer"}}', encoding="utf-8")
        self.assertEqual(state.build_app_state(1), {"retired": True, "user_id": 1})
        self.assertTrue(stale.exists())
        self.assertEqual(list(self.state_dir.glob("*.json")), [stale])

    def test_retirement_inventory_is_read_only_and_apply_is_idempotent(self):
        stale = self.state_dir / "old-bearer.json"
        stale.write_text("legacy", encoding="utf-8")
        self.assertEqual(retirement.inventory(self.db_path, self.state_dir)["files"], 1)
        self.assertTrue(stale.exists())
        result = retirement.apply(self.db_path, self.state_dir)
        self.assertEqual(result["files_removed"], 1)
        self.assertFalse(stale.exists())
        self.assertEqual(retirement.inventory(self.db_path, self.state_dir)["files"], 0)

    def test_frontend_has_no_state_url_or_bearer_bootstrap(self):
        frontend = (Path(__file__).resolve().parent / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("app-state", frontend)
        self.assertNotIn("ROVE_API.token", frontend)
        self.assertNotIn('Authorization":`Bearer', frontend)


if __name__ == "__main__":
    unittest.main()
