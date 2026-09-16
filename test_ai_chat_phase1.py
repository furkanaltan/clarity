import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import report_ai_text


class AiChatPhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY, onboarding_step INTEGER DEFAULT 10,
                    income REAL DEFAULT 0, other_income REAL DEFAULT 0, fixed_costs REAL DEFAULT 0,
                    etf_savings REAL DEFAULT 0, cash_savings REAL DEFAULT 0, current_cash REAL DEFAULT 0,
                    current_investments REAL DEFAULT 0
                );
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE app_user_features (user_id INTEGER, feature_key TEXT, enabled INTEGER);
                CREATE TABLE expenses (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, category TEXT, created_at TEXT);
                CREATE TABLE portfolio_holdings (
                    id INTEGER PRIMARY KEY, user_id INTEGER, instrument_label TEXT, instrument_type TEXT,
                    quantity REAL, total_invested REAL, market_value REAL, valuation_enabled INTEGER,
                    price_symbol TEXT, quote_currency TEXT
                );
                CREATE TABLE investment_events (
                    id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, direction TEXT,
                    asset_type TEXT, asset_name TEXT, created_at TEXT
                );
                INSERT INTO users (user_id, income, fixed_costs, current_investments) VALUES (1, 3000, 800, 2100);
                INSERT INTO users (user_id, income, fixed_costs) VALUES (2, 2500, 500);
                INSERT INTO user_access VALUES (1, 'approved');
                INSERT INTO user_access VALUES (2, 'approved');
                INSERT INTO portfolio_holdings VALUES (1, 1, 'Test ETF', 'etf', 10, 1000, 1100, 1, 'TEST', 'EUR');
                INSERT INTO investment_events VALUES (1, 1, 1000, 'in', 'stock', 'X-Peng', CURRENT_TIMESTAMP);
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
        self.assertIn("X-Peng", seen[-1]["content"])
        self.assertIn('"manual_value": true', seen[-1]["content"])
        self.assertNotIn("two@example.test", seen[-1]["content"])

    def test_general_investment_knowledge_has_no_personal_portfolio_context(self):
        for question in ("Was ist ein ETF?", "Wo kann man Aktien kaufen?"):
            seen = []
            with patch.object(api, "ai_chat_provider", lambda messages: (seen.extend(messages) or ("Allgemeine Antwort.", 8, 4))):
                response = self.post(self.client_for(token=f"knowledge-{question}"), question)
            self.assertEqual(response.status_code, 200)
            prompt = seen[-1]["content"]
            self.assertIn('"personal_data": false', prompt)
            self.assertNotIn("Test ETF", prompt)
            self.assertNotIn("X-Peng", prompt)

    def test_investment_follow_up_gets_fresh_manual_positions(self):
        seen = []
        with patch.object(api, "ai_chat_provider", lambda messages: (seen.extend(messages) or ("Aktienanteil.", 8, 4))):
            response = self.post(self.client_for(), "Wie viel davon sind Aktien?")
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Peng", seen[-1]["content"])

    def test_off_topic_question_is_not_sent_to_provider(self):
        with patch.object(api, "ai_chat_provider", side_effect=AssertionError("provider must not run")):
            response = self.post(self.client_for(), "Wie baut man ein Flugzeug?")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["kind"], "ai")
        self.assertIn("Finanzen", response.get_json()["answer"])

    def test_system_prompt_requires_plain_text_without_markdown(self):
        self.assertIn("keine Sternchen", api.AI_CHAT_SYSTEM_PROMPT)
        self.assertIn("keine Markdown-Syntax", api.AI_CHAT_SYSTEM_PROMPT)

    def test_mentor_priority_questions_use_personal_v2_context(self):
        questions = (
            ("Was ist aktuell mein größter finanzieller Schwachpunkt?", "weakness"),
            ("Was soll ich als Nächstes verbessern?", "weakness"),
            ("Was ist mein wichtigster finanzieller Hebel?", "weakness"),
            ("Woran soll ich zuerst arbeiten?", "weakness"),
            ("Was muss ich diesen Monat priorisieren?", "action"),
            ("Was ist aktuell mein größter finanzieller Schwachpunkt und was soll ich konkret als Nächstes tun?", "combined"),
        )
        for index, (question, mode) in enumerate(questions):
            self.assertEqual(api.ai_chat_intent(question), "mentor_priority", question)
            self.assertEqual(api.ai_mentor_question_mode(question), mode, question)
            with patch.object(api, "ai_chat_provider", side_effect=AssertionError("deterministic mentor question must not call provider")):
                response = self.post(self.client_for(token=f"mentor-intent-{index}"), question)
            self.assertEqual(response.status_code, 200, response.get_json())
            payload = response.get_json()
            self.assertEqual(payload["kind"], "rove")
            self.assertTrue(payload["deterministic"])
            self.assertEqual(payload["intent"], "mentor_priority")
            self.assertTrue(payload["answer"])

    def test_next_financial_step_is_deterministic(self):
        question = "Was ist mein nächster finanzieller Schritt?"
        self.assertEqual(api.ai_mentor_question_mode(question), "action")
        with patch.object(api, "ai_chat_provider", side_effect=AssertionError("deterministic mentor question must not call provider")):
            response = self.post(self.client_for(token="mentor-next-step"), question)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["deterministic"])

    def test_attention_questions_use_deterministic_mentor_path(self):
        questions = (
            "Was braucht diesen Monat Aufmerksamkeit?",
            "Worauf soll ich diesen Monat achten?",
            "Was ist gerade wichtig?",
            "Was soll ich als Nächstes angehen?",
            "Wo habe ich aktuell Handlungsbedarf?",
            "Was ist diesen Monat auffällig?",
        )
        for index, question in enumerate(questions):
            self.assertEqual(api.ai_chat_intent(question), "mentor_priority", question)
            self.assertEqual(api.ai_mentor_question_mode(question), "action", question)
            with patch.object(api, "ai_chat_provider", side_effect=AssertionError("attention question must not call provider")):
                response = self.post(self.client_for(token=f"mentor-attention-{index}"), question)
            self.assertEqual(response.status_code, 200, response.get_json())
            payload = response.get_json()
            self.assertEqual(payload["kind"], "rove")
            self.assertTrue(payload["deterministic"])

    def test_attention_question_returns_explicit_fallback_without_candidate(self):
        with patch.object(api, "build_mentor_candidate", return_value=None), \
             patch.object(api, "ai_chat_provider", side_effect=AssertionError("attention question must not call provider")):
            response = self.post(self.client_for(token="mentor-attention-fallback"), "Was braucht diesen Monat Aufmerksamkeit?")
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["kind"], "rove")
        self.assertTrue(payload["deterministic"])
        self.assertIn("kein klarer priorisierter hebel", payload["answer"].casefold())

    def test_deterministic_weakness_answer_uses_score_v2_factor(self):
        answer = api._ai_deterministic_mentor_answer({
            "mentor_mode": "weakness",
            "mentor_weakest_factor": {
                "label": "Budget-Kontrolle", "points": 11, "max": 20,
            },
            "mentor_priority_item": None,
        })
        self.assertIn("Budget-Kontrolle", answer)
        self.assertIn("11/20", answer)

    def test_deterministic_combined_answer_keeps_weakness_before_action(self):
        answer = api._ai_deterministic_mentor_answer({
            "mentor_mode": "combined",
            "mentor_weakest_factor": {
                "label": "Budget-Kontrolle", "points": 11, "max": 20,
            },
            "mentor_priority_item": {
                "title": "Deinen Monatsreport prüfen",
                "message": "Erkenne die größten Abweichungen.",
                "action_label": "Report öffnen",
            },
        })
        self.assertLess(answer.index("Budget-Kontrolle"), answer.index("Monatsreport"))

    def test_mentor_candidate_is_authoritative_in_chat_context(self):
        candidate = {
            "id": "budget-overrun", "priority": 100, "type": "budget_overrun",
            "title": "Dein Budget braucht Aufmerksamkeit", "message": "Budget zuerst prüfen.",
            "action_label": "Budget prüfen", "deep_link": "analysis",
            "reason": "negative_or_overrun_budget",
        }
        score = {
            "score_version": 2, "total": 67,
            "factors": [
                {"key": "budget", "n": "Budget-Kontrolle", "points": 11, "max": 20},
                {"key": "savings", "n": "Sparrate", "points": 19, "max": 20},
                {"key": "liquidity", "n": "Notgroschen / Liquiditaet", "points": 15, "max": 20},
                {"key": "debt", "n": "Schuldenstruktur", "points": 22, "max": 30},
                {"key": "tracking", "n": "Tracking / Datenqualitaet", "points": 0, "max": 10},
            ],
        }
        with patch.object(api, "calculate_score", return_value=score), \
             patch.object(api, "build_mentor_candidate", return_value=candidate), \
             patch.object(api, "ai_chat_provider", side_effect=AssertionError("deterministic mentor question must not call provider")):
            response = self.post(self.client_for(token="mentor-authority"), "Was soll ich konkret als Nächstes tun?")
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["kind"], "rove")
        self.assertIn("Budget braucht Aufmerksamkeit", payload["answer"])
        self.assertIn("Budget prüfen", payload["answer"])
        self.assertTrue(payload["deterministic"])
        self.assertIn("autoritative", api.AI_CHAT_SYSTEM_PROMPT)

    def test_mentor_priority_without_candidate_has_explicit_fallback(self):
        with patch.object(api, "build_mentor_candidate", return_value=None), \
             patch.object(api, "ai_chat_provider", side_effect=AssertionError("deterministic mentor question must not call provider")):
            response = self.post(self.client_for(token="mentor-fallback"), "Was soll ich konkret als Nächstes tun?")
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["kind"], "rove")
        self.assertTrue(payload["deterministic"])
        self.assertIn("kein klarer priorisierter hebel", payload["answer"].casefold())

    def test_explanation_and_improvement_questions_use_mentor_context_with_provider(self):
        questions = (
            ("Warum ist Budget mein größter Hebel?", "explanation"),
            ("Warum ist Budget mein Schwachpunkt?", "explanation"),
            ("Wie kann ich meine Budgetkontrolle verbessern?", "improvement"),
        )
        for index, (question, mode) in enumerate(questions):
            seen = []

            def provider(messages):
                seen.extend(messages)
                return "Erklärung.", 8, 4

            self.assertEqual(api.ai_chat_intent(question), "mentor_priority", question)
            self.assertEqual(api.ai_mentor_question_mode(question), mode, question)
            with patch.object(api, "ai_chat_provider", provider):
                response = self.post(self.client_for(token=f"mentor-analysis-{index}"), question)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["kind"], "ai")
            prompt = seen[-1]["content"]
            self.assertIn('"context_type": "mentor_priority"', prompt)
            self.assertIn(f'"mentor_mode": "{mode}"', prompt)
            self.assertIn('"mentor_weakest_factor"', prompt)
            self.assertIn('"score_v2"', prompt)

    def test_budget_status_questions_do_not_match_mentor_analysis(self):
        for question in ("Wie läuft mein Budget?", "Welche Kategorie ist über Plan?"):
            self.assertIsNone(api.ai_mentor_question_mode(question), question)
            self.assertEqual(api.ai_chat_intent(question), "spending", question)

    def test_mentor_prompt_preserves_debt_status_and_mortgage_rules(self):
        self.assertIn('"unknown"', api.AI_CHAT_SYSTEM_PROMPT)
        self.assertIn("nicht Schuldenfreiheit", api.AI_CHAT_SYSTEM_PROMPT)
        self.assertIn("Hypothek ist nicht als problematische Konsumschuld", api.AI_CHAT_SYSTEM_PROMPT)

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
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_conversation_messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0], 6)
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
        with patch.object(api, "ai_chat_provider", self.provider("## **Wichtig** <script>alert(1)</script>")):
            response = self.post(self.client_for(), "Was ist TER?")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<", response.get_json()["answer"])
        self.assertNotIn("*", response.get_json()["answer"])
        self.assertNotIn("#", response.get_json()["answer"])
        with patch.object(api, "ai_chat_provider", side_effect=RuntimeError("ai_provider_unavailable")):
            failed = self.post(self.client_for(token="token-five"), "Was ist KGV?")
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("provider", failed.get_json()["answer"].casefold())

    def test_provider_runs_without_a_sqlite_write_lock_and_persists_afterward(self):
        def provider(_messages):
            with closing(sqlite3.connect(self.db_path, timeout=0.1)) as other:
                other.execute("BEGIN IMMEDIATE")
                other.execute("UPDATE users SET income = income WHERE user_id = 2")
                other.commit()
            return "Antwort ohne blockierte Datenbank.", 9, 4

        with patch.object(api, "ai_chat_provider", provider):
            response = self.post(self.client_for(), "Was ist TER?")
        self.assertEqual(response.status_code, 200, response.get_json())
        conversation_id = response.get_json()["conversation_id"]
        with closing(sqlite3.connect(self.db_path)) as conn:
            messages = conn.execute(
                "SELECT role FROM app_ai_conversation_messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
            self.assertEqual([row[0] for row in messages], ["user", "assistant"])

    def test_provider_failure_does_not_create_a_partial_conversation(self):
        with patch.object(api, "ai_chat_provider", side_effect=RuntimeError("ai_provider_unavailable")):
            response = self.post(self.client_for(), "Was ist KGV?")
        self.assertEqual(response.status_code, 503)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_conversations").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_conversation_messages").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_ai_usage").fetchone()[0], 1)

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

    def test_budget_context_contains_only_aggregate_budget_data(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "CREATE TABLE category_budgets (user_id INTEGER, active_month TEXT, category TEXT, monthly_limit REAL, source TEXT)"
            )
            conn.execute(
                "INSERT INTO category_budgets VALUES (1, ?, 'Mobilität', 250, 'manual')",
                (datetime.now().strftime("%Y-%m"),),
            )
            conn.commit()
            conn.row_factory = sqlite3.Row
            user = conn.execute("SELECT * FROM users WHERE user_id = 1").fetchone()
            intent, context = api.build_ai_chat_context(conn, 1, "Wie läuft mein Budget?")
        self.assertEqual(intent, "spending")
        self.assertEqual(context["context_type"], "spending_current_month")
        self.assertIn("budget", context)
        self.assertIn("Mobilität", str(context["budget"]))
        self.assertNotIn("Test ETF", str(context))
        self.assertNotIn("X-Peng", str(context))
        self.assertNotIn("one@example.test", str(context))

    def test_report_signals_omit_merchant_and_goal_description(self):
        signals = report_ai_text._build_signals({
            "meta": {"month_label": "September", "tracked_days": 4},
            "profile": {"income_total": 3000, "fixed_costs": 800, "savings_plan": 500, "savings_rate": 16.7, "net_worth": 40000},
            "pages": {
                "month": {
                    "total_expenses": 700, "remaining_budget": 150,
                    "strongest_category": {"category": "Mobilität", "total": 228},
                    "biggest_expense": {"merchant": "Private Händlerdaten", "amount": 228},
                },
                "score": {"clarity_score": 70, "parts": {"budget": 14}},
                "goal": {"description": "Private Zielbeschreibung", "target_amount": 10000, "progress_percent": 10, "current_amount": 1000},
                "financial_story": {"delta": 100},
                "wealth_journey": {"investment_summary": {"net_contributions": 250}, "monthly_execution": {}},
            },
        })
        self.assertIn("Mobilität", signals)
        self.assertNotIn("Private Händlerdaten", signals)
        self.assertNotIn("Private Zielbeschreibung", signals)

    def test_obvious_secret_is_rejected_before_provider_or_persistence(self):
        messages = (
            "Meine IBAN ist DE89370400440532013000",
            "Passwort: geheim-123",
            "api_key=sk-abcdefghijklmnopqrstuvwxyz",
            "token=ghp_abcdefghijklmnopqrstuvwxyz",
        )
        with patch.object(api, "ai_chat_provider", side_effect=AssertionError("secret must not reach provider")):
            for index, message in enumerate(messages):
                response = self.post(self.client_for(token=f"secret-{index}"), message)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"], "sensitive_input_not_accepted")
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertFalse(api._ai_table_exists(conn, "app_ai_conversation_messages"))

    def test_screenshot_payload_contains_only_image_and_extraction_prompt(self):
        seen = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{\\"transactions\\": []}"}}]}'

        def urlopen(request, timeout):
            seen.append((json.loads(request.data.decode("utf-8")), timeout))
            return Response()

        with patch.object(api, "OPENAI_API_KEY", "test-key"), patch.object(api.urllib.request, "urlopen", urlopen):
            api.request_screenshot_analysis(b"image-bytes", "image/png")
        body, timeout = seen[0]
        self.assertEqual(timeout, 40)
        content = body["messages"][0]["content"]
        self.assertEqual({item["type"] for item in content}, {"text", "image_url"})
        self.assertNotIn("current_cash", json.dumps(body))
        self.assertNotIn("one@example.test", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
