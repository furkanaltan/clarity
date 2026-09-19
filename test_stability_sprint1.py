from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import apply_due_scheduled_savings, build_live_app_data, get_app_cash_accounts
from rove_financial_accounts import FEATURE_MULTI_CASH_ACCOUNTS_V1, set_feature_enabled
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session
from test_financial_accounts_sprint2 import create_db


class StabilitySprint1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_db(self.db_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def set_multi_cash(self, enabled: bool) -> None:
        with closing(self.connect()) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, enabled)
            conn.commit()

    def state(self):
        with closing(self.connect()) as conn:
            return build_live_app_data(conn, 1)

    def test_multi_account_home_cash_uses_active_account_sum(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE app_financial_accounts SET balance = CASE id WHEN 2 THEN 1000 WHEN 3 THEN 500 ELSE 0 END WHERE user_id=1")
            conn.commit()
        self.assertEqual(self.state()["sts"]["konto"], 1500)

    def test_multi_account_drift_is_not_repaired(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE app_financial_accounts SET balance = CASE id WHEN 2 THEN 1000 WHEN 3 THEN 500 ELSE 0 END WHERE user_id=1")
            conn.execute("UPDATE users SET current_cash=1400 WHERE user_id=1")
            conn.commit()
        state = self.state()
        self.assertEqual(state["sts"]["konto"], 1500)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0], 1400)

    def test_feature_off_keeps_legacy_current_cash(self):
        self.set_multi_cash(False)
        with closing(self.connect()) as conn:
            conn.execute("UPDATE users SET current_cash=1400 WHERE user_id=1")
            conn.commit()
        self.assertEqual(self.state()["sts"]["konto"], 1400)

    def test_negative_legacy_cash_is_preserved(self):
        self.set_multi_cash(False)
        with closing(self.connect()) as conn:
            conn.execute("UPDATE users SET current_cash=-250 WHERE user_id=1")
            conn.execute("UPDATE app_account_balances SET amount=0 WHERE user_id=1")
            conn.execute("UPDATE app_account_balances SET amount=-250 WHERE user_id=1 AND account_key='giro'")
            conn.commit()
        self.assertEqual(self.state()["sts"]["konto"], -250)

    def test_negative_multi_account_cash_is_preserved(self):
        with closing(self.connect()) as conn:
            conn.execute("UPDATE app_financial_accounts SET balance = CASE id WHEN 2 THEN -500 WHEN 3 THEN 200 ELSE 0 END WHERE user_id=1")
            conn.commit()
        self.assertEqual(self.state()["sts"]["konto"], -300)

    def test_unavailable_cash_is_not_coerced_to_zero(self):
        self.set_multi_cash(False)
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM app_account_balances WHERE user_id=1")
            conn.execute("UPDATE users SET current_cash=NULL WHERE user_id=1")
            conn.commit()
        state = self.state()
        self.assertIsNone(state["sts"]["konto"])
        self.assertIsNone(state["netWorth"])

    def test_cash_account_fallback_preserves_negative_and_none(self):
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM app_account_balances WHERE user_id=1")
            negative, _ = get_app_cash_accounts(conn, 1, -250)
            unavailable, _ = get_app_cash_accounts(conn, 1, None)
        self.assertEqual(negative["giro"], -250)
        self.assertIsNone(unavailable["giro"])

    def test_parallel_transactions_materialize_scheduled_savings_once(self):
        with closing(self.connect()) as conn:
            apply_due_scheduled_savings(conn, 1)
            conn.execute(
                "INSERT INTO app_scheduled_savings(user_id,effective_month,etf_savings,cash_savings) VALUES (1,?,?,?)",
                (date.today().strftime("%Y-%m"), 300, 100),
            )
            conn.commit()

        statuses = []
        raw_token = "parallel-session"

        def request_transactions():
            with api.app.test_client() as client:
                client.set_cookie(api.SESSION_COOKIE_NAME, raw_token, domain="localhost", path="/")
                response = client.get("/v1/transactions", headers={"Origin": "https://getrove.de"})
                statuses.append(response.status_code)

        with patch.object(api, "DB_PATH", self.db_path), patch.object(
            api, "AUTH_SECRET", "parallel-transaction-test-secret"
        ):
            ensure_unlocked_test_session(self.db_path, 1, raw_token)
            threads = [threading.Thread(target=request_transactions) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(statuses), [200, 200])
        with closing(self.connect()) as conn:
            user = conn.execute("SELECT etf_savings,cash_savings FROM users WHERE user_id=1").fetchone()
            pending = conn.execute("SELECT COUNT(*) FROM app_scheduled_savings WHERE user_id=1").fetchone()[0]
        self.assertEqual((user["etf_savings"], user["cash_savings"]), (300, 100))
        self.assertEqual(pending, 0)


if __name__ == "__main__":
    unittest.main()
