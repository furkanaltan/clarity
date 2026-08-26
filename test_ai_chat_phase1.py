import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api


class AiChatPhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY, onboarding_step INTEGER DEFAULT 10,
                    income REAL DEFAULT 0, other_income REAL DEFAULT 0, fixed_costs REAL DEFAULT 0,
                    etf_savings REAL DEFAULT 0, cash_savings REAL DEFAULT 0, current_cash REAL DEFAULT 0
                );
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE expenses (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, category TEXT, created_at TEXT);
                CREATE TABLE portfolio_holdings (
                    id INTEGER PRIMARY KEY, user_id INTEGER, instrument_label TEXT, instrument_type TEXT,
                    quantity REAL, total_invested REAL, market_value REAL, valuation_enabled INTEGER,
                    price_symbol TEXT, quote_currency TEXT
                );
                INSERT INTO users (user_id, income, fixed_costs) VALUES (1, 3000, 800);
                INSERT INTO users (user_id, income, fixed_costs) VALUES (2, 2500, 500);
                INSERT INTO user_access VALUES (1, 'approved');
                INSERT INTO user_access VALUES (2, 'approved');
                INSERT INTO portfolio_holdings VALUES (1, 1, 'Test ETF', 'etf', 10, 1000, 1100, 1, 'TEST', 'EUR');
            """)
            api.ensure_auth_tables(conn)
            conn.execute("INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES ('one@example.test', 1, CURRENT_TIMESTAMP, 'app')")
            conn.execute("INSERT INTO app_accounts (email, user_id, verified_at, source) VALUES ('two@example.test', 2, CURRENT_TIMESTAMP, 'app')")
            conn.commit()
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "ai-chat-test-secret-with-enough-entropy"),
            patch.object(api, "OPENAI_API_KEY", "test-key"),
        ]
        for patcher in self.patchers:
            patcher.start()
        api._ai_chat_attempts.clear()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def _account_id(self, user_id):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute("SELECT id FROM app_accounts WHERE user_id = ?", (user_id,)).fetchone()[0]

    def client_for(self, user_id=1, token="token-one", unlocked=True):
        with closing(sqlite3.connect(self.db_path)) as conn:
            session = conn.execute(
                "INSERT INTO app_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (api.keyed_hash(token), self._account_id(user_id), (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            if unlocked:
                conn.execute(
                    "INSERT INTO app_session_pins (session_id, pin_verifier, unlocked_at, last_activity_at) VALUES (?, 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (session.lastrowid,),
                )
            conn.commit()
        client = api.app.test_client()
        client.set_cookie(api.SESSION_COOKIE_NAME, token, domain="localhost", path="/")
        return client

    @staticmethod
    def provider(answer="Klare Antwort."):
        return lambda _messages: (answer, 12, 7)

    def post(self, client, message, **extra):
        return client.post("/v1/ai/chat", json={"message": message, **extra})

    def test_unauthenticated_and_pin_locked_are_blocked(self):
        self.assertEqual(self.post(api.app.test_client(), "Was ist TER?").status_code, 401)
        self.assertEqual(self.post(self.client_for(token="locked", unlocked=False), "Was ist TER?").status_code, 423)

    def test_general_question_uses_no_personal_context_or_secret(self):
        seen = []
        def provider(messages):
            seen.extend(messages)
            return "TER sind laufende Fondskosten.", 11, 6
        with patch.object(api, "ai_chat_provider", provider):
            response = self.post(self.client_for(), "Was ist TER?")
        self.assertEqual(response.status_code, 200, response.get_json())
        prompt = seen[-1]["content"]
        self.assertIn('"personal_data": false', prompt)
        self.assertNotIn("one@example.test", prompt)
        self.assertNotIn("test-key", prompt)

    def test_investment_context_is_user_scoped(self):
        seen = []
        def provider(messages):
            seen.extend(messages)
            return "Dein Portfolio enthält eine Position.", 10, 5
        with patch.object(api, "ai_chat_provider", provider):
            response = self.post(self.client_for(), "Wie ist mein Portfolio aufgebaut?")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Test ETF", seen[-1]["content"])
        self.assertNotIn("two@example.test", seen[-1]["content"])

    def test_action_is_never_sent_to_ai_or_written_as_finance_data(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            before = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        with patch.object(api, "ai_chat_provider", side_effect=AssertionError("provider must not run")):
            response = self.post(self.client_for(), "Buche 25 Euro Restaurant")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["kind"], "rove")
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], before)

    def test_conversation_is_bounded_user_scoped_and_expires(self):
        with patch.object(api, "ai_chat_provider", self.provider("Erste Antwort.")):
            first = self.post(self.client_for(), "Was ist TER?")
        conversation_id = first.get_json()["conversation_id"]
        second_client = self.client_for(user_id=2, token="token-two")
        self.assertEqual(self.post(second_client, "Und was bedeutet das?", conversation_id=conversation_id).status_code, 403)
        with patch.object(api, "ai_chat_provider", self.provider("Folgeantwort.")):
            response = self.post(self.client_for(token="token-three"), "Und wie wirkt sich das aus?", conversation_id=conversation_id)
        self.assertEqual(response.status_code, 200)
        with patch.object(api, "ai_chat_provider", self.provider("Weitere Antwort.")):
            for index in range(6):
                response = self.post(self.client_for(token=f"token-history-{index}"), f"Folgefrage {index}", conversation_id=conversation_id)
                self.assertEqual(response.status_code, 200)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_conversation_messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0], 12)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE app_ai_conversations SET expires_at = datetime('now', '-1 hour') WHERE conversation_id = ?", (conversation_id,))
            conn.commit()
        self.assertEqual(self.post(self.client_for(token="token-four"), "Noch eine Frage", conversation_id=conversation_id).status_code, 403)

    def test_fresh_context_is_rebuilt_after_a_follow_up(self):
        with patch.object(api, "ai_chat_provider", self.provider("Erste Antwort.")):
            first = self.post(self.client_for(), "Wie ist mein Portfolio aufgebaut?")
        conversation_id = first.get_json()["conversation_id"]
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE portfolio_holdings SET market_value = 2200 WHERE user_id = 1")
            conn.commit()
        seen = []
        def provider(messages):
            seen.extend(messages)
            return "Aktualisierte Antwort.", 10, 5
        with patch.object(api, "ai_chat_provider", provider):
            response = self.post(self.client_for(token="token-fresh"), "Wie ist mein Portfolio aufgebaut?", conversation_id=conversation_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("2200.0", seen[-1]["content"])

    def test_output_is_plain_text_and_provider_failure_is_neutral(self):
        with patch.object(api, "ai_chat_provider", self.provider("<script>alert(1)</script>")):
            response = self.post(self.client_for(), "Was ist TER?")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<", response.get_json()["answer"])
        with patch.object(api, "ai_chat_provider", side_effect=RuntimeError("ai_provider_unavailable")):
            failed = self.post(self.client_for(token="token-five"), "Was ist KGV?")
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("provider", failed.get_json()["answer"].casefold())

    def test_rate_limit_and_revoked_session_are_blocked(self):
        with patch.object(api, "AI_CHAT_RATE_LIMIT", 1), patch.object(api, "ai_chat_provider", self.provider()):
            client = self.client_for()
            self.assertEqual(self.post(client, "Was ist TER?").status_code, 200)
            self.assertEqual(self.post(client, "Was ist KGV?").status_code, 429)
        client = self.client_for(token="revoked")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE app_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ?", (api.keyed_hash("revoked"),))
            conn.commit()
        self.assertEqual(self.post(client, "Was ist TER?").status_code, 401)


if __name__ == "__main__":
    unittest.main()
