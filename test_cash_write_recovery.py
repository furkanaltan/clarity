"""Transactional rejection and durable cash request replay regression tests."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_financial_accounts import set_feature_enabled, FEATURE_MULTI_CASH_ACCOUNTS_V1
from test_financial_accounts_sprint2 import create_db


class CashWriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.db"
        create_db(self.path)
        with sqlite3.connect(self.path) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
        self.addCleanup(patch.stopall)
        patch.object(api, "DB_PATH", self.path).start()
        self.identity = patch.object(api, "user_from_token", return_value=1).start()
        self.client = api.app.test_client()
        patch.dict(api.app.config, {"PROPAGATE_EXCEPTIONS": False}).start()

    def query(self, sql):
        with sqlite3.connect(self.path) as conn:
            return conn.execute(sql).fetchall()

    def post(self, route, payload):
        return self.client.post(route, json=payload)

    def receipts(self):
        if not self.query("SELECT 1 FROM sqlite_master WHERE name='app_cash_request_receipts'"):
            return []
        return self.query("SELECT * FROM app_cash_request_receipts")

    def test_insufficient_cash_has_no_persistent_effect(self):
        before = self.query("SELECT * FROM app_account_balances")
        response = self.post("/v1/expenses", {"amount": 100, "paid_cash": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.query("SELECT * FROM expenses"), [])
        self.assertEqual(self.query("SELECT * FROM app_cash_movements"), [])
        self.assertEqual(self.query("SELECT * FROM app_account_balances"), before)

    def test_domain_failure_after_insert_rolls_back(self):
        original = api.create_expense_for_user
        before = self.query("SELECT * FROM app_account_balances")
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError("injected_domain_failure")
        with patch.object(api, "create_expense_for_user", side_effect=fail):
            response = self.post("/v1/expenses", {"amount": 10})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.query("SELECT * FROM expenses"), [])
        self.assertEqual(self.query("SELECT * FROM app_cash_movements"), [])
        self.assertEqual(self.query("SELECT * FROM app_account_balances"), before)

    def test_valid_expense_changes_cash_once(self):
        response = self.post("/v1/expenses", {"amount": 10, "paid_cash": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.query("SELECT count(*) FROM expenses"), [(1,)])
        self.assertEqual(self.query("SELECT count(*) FROM app_cash_movements"), [(1,)])
        self.assertEqual(self.query("SELECT amount FROM app_account_balances WHERE account_key='bargeld' AND user_id=1"), [(40.0,)])

    def test_income_retry_conflict_and_user_isolation(self):
        payload = {"amount": 100, "label": "Synthetic", "request_id": "retry"}
        first = self.post("/v1/income", payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.post("/v1/income", payload).json, first.json)
        self.assertEqual(self.post("/v1/income", {**payload, "amount": 101}).status_code, 409)
        self.assertEqual(self.query("SELECT count(*) FROM app_cash_movements WHERE kind='income' AND user_id=1"), [(1,)])
        self.identity.return_value = 2
        self.assertEqual(self.post("/v1/income", payload).status_code, 200)
        self.assertEqual(self.query("SELECT count(*) FROM app_cash_movements WHERE kind='income' AND user_id=2"), [(1,)])

    def test_transfer_and_adjust_replay(self):
        for payload in (
            {"action": "transfer", "from": "bargeld", "to": "giro", "amount": 50, "request_id": "transfer"},
            {"action": "adjust", "account": "giro", "amount": 20, "request_id": "adjust"},
        ):
            with self.subTest(action=payload["action"]):
                first = self.post("/v1/accounts", payload)
                self.assertEqual(first.status_code, 200)
                balances = self.query("SELECT * FROM app_account_balances")
                self.assertEqual(self.post("/v1/accounts", payload).json, first.json)
                self.assertEqual(self.query("SELECT * FROM app_account_balances"), balances)
                self.assertEqual(self.post("/v1/accounts", {**payload, "amount": 51}).status_code, 409)

    def test_rejected_transfer_can_retry_after_correction(self):
        payload = {"action": "transfer", "from": "bargeld", "to": "giro", "amount": 100, "request_id": "rejected"}
        self.assertEqual(self.post("/v1/accounts", payload).status_code, 400)
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.post("/v1/accounts", {**payload, "amount": 10}).status_code, 200)

    def test_response_failure_rolls_back_income_and_receipt(self):
        with patch.object(api, "build_live_app_data", side_effect=RuntimeError("injected")):
            self.assertEqual(self.post("/v1/income", {"amount": 100, "request_id": "failure"}).status_code, 500)
        self.assertEqual(self.query("SELECT * FROM app_cash_movements"), [])
        self.assertEqual(self.receipts(), [])

    def test_concurrent_income_retry_is_one_write(self):
        payload = {"amount": 100, "label": "Concurrent", "request_id": "concurrent"}
        def send(_):
            with api.app.test_client() as client:
                response = client.post("/v1/income", json=payload)
                return response.status_code, response.json["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 200)
        self.assertEqual(self.query("SELECT count(*) FROM app_cash_movements WHERE kind='income'"), [(1,)])

    def test_multi_cash_replay_and_partial_failure(self):
        with sqlite3.connect(self.path) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
        payload = {"amount": 100, "request_id": "pilot"}
        first = self.post("/v1/income", payload)
        self.assertEqual(first.status_code, 200)
        balances = self.query("SELECT id,balance FROM app_financial_accounts")
        self.assertEqual(self.post("/v1/income", payload).json, first.json)
        self.assertEqual(self.query("SELECT id,balance FROM app_financial_accounts"), balances)
        with patch.object(api, "build_live_app_data", side_effect=RuntimeError("injected")):
            response = self.post("/v1/accounts", {"action": "adjust", "account": "giro", "amount": 20, "request_id": "failed-adjust"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.query("SELECT id,balance FROM app_financial_accounts"), balances)
        self.assertEqual(len(self.receipts()), 1)
