from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import _build_tx, _monthly_budget_truth
from rove_behavior_patterns import _load_salary_income_events
from rove_financial_accounts import FEATURE_MULTI_CASH_ACCOUNTS_V1, set_feature_enabled
from test_financial_accounts_sprint2 import create_db


class CashflowTruthFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_db(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
        self.patches = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "user_from_token", lambda _conn, token: 1 if token == "pilot-token" else None),
            patch.object(api, "build_live_app_data", lambda *_args: {"sts": {"available": 0}, "budgets": []}),
            patch.object(api, "award_tracking_points", lambda *_args, **_kwargs: {"awarded": 0}),
            patch.object(api, "reverse_tracking_points_for_deleted_expense", lambda *_args: False),
            patch.object(api, "category_rule_for_merchant", lambda *_args: None),
            patch.object(api, "apply_due_scheduled_savings", lambda *_args: None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        api.app.config.update(TESTING=True)

    def request(self, method: str, path: str, payload: dict):
        with api.app.test_client() as client:
            return client.open(
                path,
                method=method,
                json=payload,
                headers={"Origin": "https://getrove.de", "Authorization": "Bearer pilot-token"},
            )

    def query(self, sql: str, params: tuple = ()):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(sql, params).fetchall()

    def set_cash(self, value: float | None, *, remove_account_rows: bool = False) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET current_cash=? WHERE user_id=1", (value,))
            if remove_account_rows:
                conn.execute("DELETE FROM app_account_balances WHERE user_id=1")
            conn.commit()

    def giro(self) -> float:
        rows = self.query(
            "SELECT amount FROM app_account_balances WHERE user_id=1 AND account_key='giro'"
        )
        if rows:
            return float(rows[0][0])
        return float(self.query("SELECT current_cash FROM users WHERE user_id=1")[0][0])

    def fixed_expense(
        self, amount: float = 50, *, import_key: str | None = None, request_id: str | None = None
    ):
        if import_key:
            return self.request("POST", "/v1/import/screenshot/commit", {"transactions": [{
                "amount": amount, "merchant": "Miete", "category": "Sonstiges",
                "importKey": import_key, "fixed_cost": True,
            }]})
        return self.request("POST", "/v1/expenses", {
            "amount": amount, "merchant": "Miete", "category": "Sonstiges",
            "fixed_cost": True, "request_id": request_id,
        })

    def test_legacy_expense_keeps_negative_cash_before_debit(self):
        self.set_cash(-500, remove_account_rows=True)
        response = self.request("POST", "/v1/expenses", {"amount": 10, "merchant": "Shop"})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.giro(), -510)
        self.assertEqual(self.query("SELECT current_cash FROM users WHERE user_id=1"), [(-510.0,)])

    def test_tagged_manual_fixed_expense_is_excluded_and_monthly_check_does_not_charge_again(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET fixed_costs=50 WHERE user_id=1")
            conn.commit()
        response = self.fixed_expense()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.giro(), 950)
        month = datetime.now().strftime("%Y-%m")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            truth = _monthly_budget_truth(conn, 1, income=3000, fixed_costs=50, savings=0, month_key=month)
        self.assertEqual(truth["variable_expenses"], 0)
        confirmed = self.request("POST", "/v1/monthly-plan", {"action": "confirm_fixed_costs"})
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        self.assertEqual(self.giro(), 950)
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND kind='fixed'"), [(0,)])

    def test_tagged_expense_after_monthly_confirmation_reconciles_one_cash_debit(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET fixed_costs=50 WHERE user_id=1")
            conn.commit()
        confirmed = self.request("POST", "/v1/monthly-plan", {"action": "confirm_fixed_costs"})
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        self.assertEqual(self.giro(), 950)
        response = self.fixed_expense()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.giro(), 950)
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND kind='fixed'"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND classification='fixed_cost'"), [(1,)])

    def test_screenshot_fixed_expense_uses_same_month_plan_classification(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET fixed_costs=50 WHERE user_id=1")
            conn.commit()
        imported = self.fixed_expense(import_key="a" * 32)
        self.assertEqual(imported.status_code, 200, imported.get_json())
        self.assertEqual(self.giro(), 950)

    def test_idempotent_fixed_expense_retry_does_not_reconcile_plan_twice(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET fixed_costs=100 WHERE user_id=1")
            conn.commit()
        confirmed = self.request("POST", "/v1/monthly-plan", {"action": "confirm_fixed_costs"})
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        payload = {"amount": 30, "merchant": "Miete", "category": "Sonstiges", "fixed_cost": True, "request_id": "fixed-idempotent"}
        first = self.request("POST", "/v1/expenses", payload)
        retry = self.request("POST", "/v1/expenses", payload)
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(retry.status_code, 200, retry.get_json())
        self.assertTrue(retry.get_json()["idempotent_replay"])
        self.assertEqual(self.giro(), 900)
        self.assertEqual(self.query("SELECT amount FROM app_cash_movements WHERE user_id=1 AND kind='fixed'"), [(70.0,)])

    def test_deleting_reconciled_fixed_expense_restores_confirmed_plan_remainder(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE users SET fixed_costs=50 WHERE user_id=1")
            conn.commit()
        self.assertEqual(self.request("POST", "/v1/monthly-plan", {"action": "confirm_fixed_costs"}).status_code, 200)
        created = self.fixed_expense()
        self.assertEqual(created.status_code, 200, created.get_json())
        expense_id = created.get_json()["id"]
        self.assertEqual(self.giro(), 950)
        deleted = self.request("DELETE", f"/v1/expenses/{expense_id}", {})
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(self.giro(), 950)
        self.assertEqual(self.query("SELECT amount FROM app_cash_movements WHERE user_id=1 AND kind='fixed'"), [(50.0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND kind='card'"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND classification='fixed_cost'"), [(0,)])
        confirmed = self.request("POST", "/v1/monthly-plan", {"action": "confirm_fixed_costs"})
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        self.assertEqual(self.giro(), 950)

    def test_unmarked_expense_remains_variable_and_reduces_free_month(self):
        response = self.request("POST", "/v1/expenses", {"amount": 50, "merchant": "Shop"})
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            truth = _monthly_budget_truth(
                conn, 1, income=3000, fixed_costs=50, savings=0,
                month_key=datetime.now().strftime("%Y-%m"),
            )
        self.assertEqual(truth["variable_expenses"], 50)
        self.assertEqual(truth["free_month_remaining"], 2900)

    def test_refund_is_separate_from_salary_cash_and_variable_expenses(self):
        salary = self.request("POST", "/v1/income", {
            "amount": 3000, "label": "Gehalt", "request_id": "salary-1",
        })
        refund = self.request("POST", "/v1/income", {
            "amount": 50, "label": "Amazon Gutschrift", "movement_type": "refund",
            "request_id": "refund-1",
        })
        self.assertEqual(salary.status_code, 200, salary.get_json())
        self.assertEqual(refund.status_code, 200, refund.get_json())
        self.assertEqual(refund.get_json()["movement_type"], "refund")
        self.assertEqual(self.giro(), 4050)
        self.assertEqual(self.query("SELECT SUM(amount) FROM app_cash_movements WHERE user_id=1 AND kind='income'"), [(3000.0,)])
        self.assertEqual(self.query("SELECT SUM(amount) FROM app_cash_movements WHERE user_id=1 AND kind='refund'"), [(50.0,)])
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("ALTER TABLE app_cash_movements ADD COLUMN occurred_at TEXT")
            conn.execute(
                "UPDATE app_cash_movements SET occurred_at=? WHERE user_id=1",
                (datetime.now().isoformat(timespec="seconds"),),
            )
            conn.commit()
            truth = _monthly_budget_truth(
                conn, 1, income=3000, fixed_costs=50, savings=0,
                month_key=datetime.now().strftime("%Y-%m"),
            )
            tx = [item for day in _build_tx(conn, 1, datetime.now().strftime("%Y-%m")) for item in day["items"]]
            salary_events = _load_salary_income_events(conn, 1, now=datetime.now())
        self.assertEqual(truth["variable_expenses"], 0)
        self.assertEqual(sum(item["amount"] for item in salary_events), 3000)
        refund_rows = [item for item in tx if item.get("classification") == "refund"]
        self.assertEqual(len(refund_rows), 1)
        self.assertEqual((refund_rows[0]["cat"], refund_rows[0]["a"]), ("Gutschrift", 50.0))

    def test_transfer_remains_neutral_to_expenses_income_and_cash_total(self):
        before = sum(float(row[0]) for row in self.query(
            "SELECT amount FROM app_account_balances WHERE user_id=1"
        ))
        response = self.request("POST", "/v1/accounts", {
            "action": "transfer", "from": "bargeld", "to": "giro", "amount": 20,
            "request_id": "neutral-transfer",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        after = sum(float(row[0]) for row in self.query(
            "SELECT amount FROM app_account_balances WHERE user_id=1"
        ))
        self.assertEqual(after, before)
        self.assertEqual(self.query("SELECT COUNT(*) FROM expenses WHERE user_id=1"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND kind='income'"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND kind='transfer'"), [(0,)])


if __name__ == "__main__":
    unittest.main()
