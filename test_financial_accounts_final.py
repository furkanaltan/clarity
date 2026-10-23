"""Regressions for the seven confirmed general-release cash account blockers."""
import ast
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
import report_engine
import prepare_multi_account_active_testers as old_rollout
import reconcile_financial_accounts as reconciliation
import rove_financial_accounts as accounts
from rove_expense_domain import begin_expense_write
from rove_score import canonical_liquid_cash
from rove_consumer_debt import ensure_consumer_debt_schema
import test_financial_accounts_sprint2 as sprint2
import test_financial_accounts_sprint3 as sprint3
import test_buffer_v1 as buffer_tests
from test_financial_accounts_sprint1 import create_legacy_db, add_user


class FinalCashAccountTests(unittest.TestCase):
    def fixture(self, cls=sprint3.Sprint3FinancialAccountTests):
        fixture = cls(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def cash(self, conn, user_id=1):
        return tuple(round(float(value), 2) for value in (
            conn.execute("SELECT current_cash FROM users WHERE user_id=?", (user_id,)).fetchone()[0],
            conn.execute("SELECT SUM(amount) FROM app_account_balances WHERE user_id=?", (user_id,)).fetchone()[0],
            conn.execute("SELECT SUM(balance) FROM app_financial_accounts WHERE user_id=? AND status='active'", (user_id,)).fetchone()[0],
        ))

    def fresh(self, giro=1000, *, pilot=False):
        fixture = self.fixture(sprint3.OnboardingAtomicityTests)
        with closing(fixture.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, pilot)
            conn.execute("DELETE FROM app_financial_account_roles WHERE user_id=1")
            conn.commit()
        payload = fixture.payload(giro=giro, tagesgeld=0, etf_savings=0)
        payload.update(cash_savings=0, contracts=[])
        response = fixture.submit(payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        return fixture

    def test_new_user_initializes_without_pilot_and_first_payment_and_income_work(self):
        f = self.fresh()
        with closing(f.connect()) as conn:
            giro = accounts.get_legacy_financial_account(conn, 1, "giro")
            self.assertEqual(accounts.roles_summary(conn, 1), dict.fromkeys(accounts.ACCOUNT_ROLES, giro["id"]))
            self.assertEqual(self.cash(conn), (1000, 1000, 1000))
            self.assertTrue(accounts.is_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1))
        with api.app.test_client() as client:
            headers = {"Origin": "https://getrove.de", "Authorization": "Bearer pilot-token"}
            expense = client.post("/v1/expenses", json={"amount": 10, "merchant": "Synthetic"}, headers=headers)
            income = client.post("/v1/income", json={"amount": 100, "request_id": "first-income"}, headers=headers)
            self.assertEqual(expense.status_code, 200, expense.get_json())
            self.assertEqual(income.status_code, 200, income.get_json())
        with closing(f.connect()) as conn:
            self.assertEqual(self.cash(conn), (1090, 1090, 1090))

    def test_zero_cash_has_payment_roles_and_first_wallet_supports_cash_payment(self):
        f = self.fresh(0)
        with api.app.test_client() as client:
            headers = {"Origin": "https://getrove.de", "Authorization": "Bearer pilot-token"}
            created = client.post("/v1/financial-accounts", json={"type": "wallet", "name": "Reisekasse", "balance": 50, "request_id": "wallet-once"}, headers=headers)
            self.assertEqual(created.status_code, 200, created.get_json())
            paid = client.post("/v1/expenses", json={"amount": 10, "merchant": "Synthetic", "paid_cash": True}, headers=headers)
            self.assertEqual(paid.status_code, 200, paid.get_json())
        with closing(f.connect()) as conn:
            wallet = accounts.get_legacy_financial_account(conn, 1, "bargeld")
            self.assertEqual(wallet["id"], created.get_json()["accountId"])
            self.assertEqual(self.cash(conn), (40, 40, 40))
            self.assertEqual(len(accounts.roles_summary(conn, 1)), 4)

    def test_legacy_conflict_never_adjusts_visible_giro_or_overwrites_cash(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.execute("UPDATE users SET current_cash=1350 WHERE user_id=1")
            conn.commit()
            before = reconciliation.user_checksum(conn, 1)
            with self.assertRaises(accounts.CashReconciliationRequired):
                state.get_app_cash_accounts(conn, 1, 1350)
            with self.assertRaises(accounts.CashReconciliationRequired):
                api.app_cash_accounts(conn, 1)
        failed = f.request("POST", "/v1/expenses", json={"amount": 10, "merchant": "Synthetic"})
        self.assertEqual(failed.status_code, 409, failed.get_json())
        with closing(f.connect()) as conn:
            self.assertEqual(before, reconciliation.user_checksum(conn, 1))
            self.assertIn("LEGACY_CURRENT_MISMATCH", reconciliation.inspect_user(conn, 1)["codes"])

    def test_dormant_financial_accounts_require_operator_decision(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
        self.assertEqual(f.request("POST", "/v1/expenses", json={"amount": 25}).status_code, 200)
        with closing(f.connect()) as conn:
            before = reconciliation.user_checksum(conn, 1)
            self.assertEqual(reconciliation.inspect_user(conn, 1)["status"], "REVIEW_REQUIRED")
            with self.assertRaisesRegex(ValueError, "operator_balance_decision_required"):
                reconciliation.prepare_user_plan(conn, 1)
            self.assertEqual(before, reconciliation.user_checksum(conn, 1))

    def test_canonical_conflict_blocks_create_without_receipt_or_balance_changes(self):
        f = self.fixture()
        with closing(f.connect()) as conn:
            conn.execute("UPDATE users SET current_cash=1350 WHERE user_id=1")
            conn.commit()
            before = reconciliation.user_checksum(conn, 1)
        failed = f.request("POST", "/v1/financial-accounts", json={"type": "checking", "name": "Synthetic", "balance": 10, "request_id": "conflict"})
        self.assertEqual(failed.status_code, 409, failed.get_json())
        with closing(f.connect()) as conn:
            self.assertEqual(before, reconciliation.user_checksum(conn, 1))

    def test_create_replay_changed_payload_and_other_user_are_isolated(self):
        f = self.fixture()
        payload = {"type": "checking", "name": "Synthetic Once", "balance": 100, "request_id": "same-id"}
        first = f.request("POST", "/v1/financial-accounts", json=payload)
        replay = f.request("POST", "/v1/financial-accounts", json=payload)
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(first.get_json()["accountId"], replay.get_json()["accountId"])
        conflict = f.request("POST", "/v1/financial-accounts", json={**payload, "balance": 101})
        self.assertEqual(conflict.status_code, 409, conflict.get_json())
        with closing(f.connect()) as conn:
            self.assertEqual(self.cash(conn), (1350, 1350, 1350))
            accounts.set_feature_enabled(conn, 2, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
            conn.commit()
        other = f.request("POST", "/v1/financial-accounts", token="other-token", json=payload)
        self.assertEqual(other.status_code, 200, other.get_json())
        self.assertNotEqual(first.get_json()["accountId"], other.get_json()["accountId"])

    def test_real_cookie_session_owns_create_and_foreign_account_ids_are_rejected(self):
        f = self.fixture(buffer_tests.BufferApiTests)
        with closing(f.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            accounts.mirror_financial_accounts_to_legacy(conn, 1)
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
            accounts.set_feature_enabled(conn, 2, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
            conn.commit()
        payload = {"type": "checking", "name": "Cookie Synthetic", "balance": 10, "request_id": "cookie-once"}
        with api.app.test_client() as client:
            denied = client.post("/v1/financial-accounts", json=payload, headers={"Origin": "https://getrove.de"})
            self.assertEqual(denied.status_code, 401)
            client.set_cookie(api.SESSION_COOKIE_NAME, f.sessions[1])
            stale = client.post("/v1/financial-accounts", json=payload, headers={"Origin": "https://getrove.de", "X-Rove-User-ID": "2"})
            self.assertEqual(stale.status_code, 409)
            created = client.post("/v1/financial-accounts", json=payload, headers={"Origin": "https://getrove.de", "X-Rove-User-ID": "1"})
            self.assertEqual(created.status_code, 200, created.get_json())
            client.set_cookie(api.SESSION_COOKIE_NAME, f.sessions[2])
            foreign = client.patch(f'/v1/financial-accounts/{created.get_json()["accountId"]}',
                json={"action": "set_balance", "balance": 999}, headers={"Origin": "https://getrove.de"})
            self.assertEqual(foreign.status_code, 404)

    def test_real_cookie_state_conflict_is_explicit_409_without_financial_mutation(self):
        f = self.fixture(buffer_tests.BufferApiTests)
        with closing(f.connect()) as conn:
            conn.execute("UPDATE users SET current_cash=4700 WHERE user_id=1")
            conn.commit()
            before = reconciliation.user_checksum(conn, 1)
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, f.sessions[1])
            response = client.get("/v1/state")
            self.assertEqual(response.status_code, 409, response.get_json())
            self.assertEqual(response.get_json(), {"ok": False, "error": "cash_reconciliation_required"})
        with closing(f.connect()) as conn:
            self.assertEqual(before, reconciliation.user_checksum(conn, 1))

    def test_parallel_create_replays_one_account(self):
        f = self.fixture()
        results = []
        def submit():
            response = f.request("POST", "/v1/financial-accounts", json={"type": "checking", "name": "Parallel", "balance": 100, "request_id": "parallel-once"})
            results.append((response.status_code, response.get_json()))
        threads = [threading.Thread(target=submit) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual([status for status, _ in results], [200] * 4, results)
        self.assertEqual(len({body["accountId"] for _, body in results}), 1)
        with closing(f.connect()) as conn:
            self.assertEqual(self.cash(conn), (1350, 1350, 1350))

    def test_create_missing_key_invalid_amount_and_rollback_do_not_add_accounts(self):
        f = self.fixture()
        payload = {"type": "checking", "name": "Synthetic", "balance": 100}
        self.assertEqual(f.request("POST", "/v1/financial-accounts", json=payload).status_code, 400)
        for value in (float("inf"), float("nan"), -float("inf")):
            self.assertEqual(f.request("POST", "/v1/financial-accounts", json={**payload, "balance": value, "request_id": "invalid"}).status_code, 400)
        with patch.object(api, "build_live_app_data", side_effect=RuntimeError("synthetic-failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic-failure"):
                f.request("POST", "/v1/financial-accounts", json={**payload, "request_id": "retry-after-rollback"})
        with closing(f.connect()) as conn:
            self.assertEqual(self.cash(conn), (1250, 1250, 1250))
        self.assertEqual(f.request("POST", "/v1/financial-accounts", json={**payload, "request_id": "retry-after-rollback"}).status_code, 200)

    def bot_helpers(self, fixture):
        tree = ast.parse(Path(api.__file__).with_name("bot.py").read_text())
        names = {"update_expense_amount", "_app_payment_movement", "_set_app_account_amount", "sync_app_paid_expense_amount", "reverse_app_paid_expense", "update_user_field"}
        body = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        @contextmanager
        def synthetic_db():
            with closing(fixture.connect()) as conn:
                with conn:
                    yield conn
        namespace = {"sqlite3": sqlite3, "get_db": synthetic_db, "begin_expense_write": begin_expense_write,
            "is_feature_enabled": accounts.is_feature_enabled, "FEATURE_MULTI_CASH_ACCOUNTS_V1": accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1,
            "adjust_financial_account_balance": accounts.adjust_financial_account_balance,
            "CashReconciliationRequired": accounts.CashReconciliationRequired, "ALLOWED_USER_FIELDS": {"current_cash"}}
        exec(compile(body, "bot.py", "exec"), namespace)
        return namespace

    def test_bot_edit_and_refund_use_original_account_after_role_change(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        expense = f.request("POST", "/v1/expenses", json={"amount": 25, "merchant": "Synthetic"})
        self.assertEqual(expense.status_code, 200, expense.get_json())
        with closing(f.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            alternate, _ = accounts.create_financial_account(conn, 1, "checking", "Anderes Giro", 100, request_id="bot-role-change")
            accounts.set_account_role(conn, 1, "expense", alternate)
            conn.commit()
        helpers = self.bot_helpers(f)
        helpers["update_expense_amount"](1, expense.get_json()["id"], 40)
        with closing(f.connect()) as conn:
            self.assertEqual(self.cash(conn), (1310, 1310, 1310))
            self.assertEqual(accounts.get_financial_account(conn, 1, alternate)["balance"], 100)
            conn.execute("BEGIN IMMEDIATE")
            self.assertEqual(helpers["reverse_app_paid_expense"](conn, 1, expense.get_json()["id"]), 40)
            conn.commit()
            self.assertEqual(self.cash(conn), (1350, 1350, 1350))
        with self.assertRaises(accounts.CashReconciliationRequired):
            helpers["update_user_field"](1, "current_cash", 999999)
        with patch.object(report_engine, "DB_NAME", str(f.db_path)):
            self.assertEqual(report_engine._report_cash_truth(1)["current_cash"], 1350)

    def test_old_bot_savings_confirmation_redirects_before_any_financial_write(self):
        f = self.fixture()
        tree = ast.parse(Path(api.__file__).with_name("bot.py").read_text())
        handler = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "handle_commands")
        handler.decorator_list = []
        helpers = self.bot_helpers(f)
        sent = []
        helpers.update({"get_command_token": lambda _text: "/investiert",
            "get_or_create_user": lambda _uid: {"onboarding_step": 0},
            "handle_admin_command": lambda *_args: False, "ensure_user_approved": lambda _msg: True,
            "STEP_NORMAL": 10, "bot": SimpleNamespace(send_message=lambda *args: sent.append(args))})
        with closing(f.connect()) as conn:
            before = reconciliation.user_checksum(conn, 1)
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "bot.py", "exec"), helpers)
        helpers["handle_commands"](SimpleNamespace(chat=SimpleNamespace(id=1), text="/investiert"))
        self.assertEqual(len(sent), 1)
        self.assertIn("Monatscheck", sent[0][1])
        with closing(f.connect()) as conn:
            self.assertEqual(before, reconciliation.user_checksum(conn, 1))

    def test_inventory_and_old_dry_run_leave_database_bytes_and_schema_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.db"
            create_legacy_db(path)
            add_user(path, 1, 100, balances={"giro": 100})
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = reconciliation.inventory(path)
            self.assertEqual(result["classes"], {"CONSISTENT_LEGACY": 1})
            self.assertNotIn("user_id", json.dumps(result))
            self.assertNotIn("current_cash", json.dumps(result))
            with patch.object(old_rollout, "active_app_tester_ids", return_value=[]), \
                 patch.object(old_rollout, "inspect_user", return_value={}):
                old_rollout.run(path, apply=False, limit=1, users=None)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaisesRegex(RuntimeError, "reviewed_user_transition_required"):
                old_rollout.run(path, apply=True, limit=1, users=[1])
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_single_user_plan_preserves_business_rows_is_idempotent_and_refuses_drift(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
            plan = reconciliation.prepare_user_plan(conn, 1)
            other = reconciliation.user_checksum(conn, 2)
            protected = reconciliation.user_checksum(conn, 1, protected_only=True)
            conn.execute("BEGIN IMMEDIATE")
            result = reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])
            conn.commit()
            self.assertTrue(result["protected_rows_unchanged"])
            self.assertEqual(protected, reconciliation.user_checksum(conn, 1, protected_only=True))
            self.assertEqual(other, reconciliation.user_checksum(conn, 2))
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])["idempotent_replay"])
            conn.rollback()
            conn.execute("UPDATE users SET income=3001 WHERE user_id=1")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "transition_already_completed_or_changed"):
                reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])
            conn.rollback()

    def test_stale_plan_and_conflicting_user_never_migrate(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
            plan = reconciliation.prepare_user_plan(conn, 1)
            conn.execute("UPDATE users SET income=1234 WHERE user_id=1")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "stale_transition_plan"):
                reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])
            conn.rollback()

    def test_reviewed_missing_roles_plan_preserves_existing_role_and_accounts(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            conn.execute("DELETE FROM app_financial_account_roles WHERE user_id=1 AND role!='income'")
            conn.commit()
            rows = [tuple(row) for row in conn.execute("SELECT * FROM app_financial_accounts ORDER BY id")]
            plan = reconciliation.prepare_user_plan(conn, 1)
            self.assertEqual(len(plan["roles_to_add"]), 3)
            conn.execute("BEGIN IMMEDIATE")
            reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])
            conn.commit()
            self.assertEqual(len(accounts.roles_summary(conn, 1)), 4)
            self.assertEqual(rows, [tuple(row) for row in conn.execute("SELECT * FROM app_financial_accounts ORDER BY id")])

    def test_initializer_never_overwrites_preexisting_cash(self):
        f = self.fixture()
        with closing(f.connect()) as conn:
            conn.execute("DELETE FROM app_financial_account_roles WHERE user_id=1")
            conn.execute("DELETE FROM app_financial_accounts WHERE user_id=1")
            conn.execute("DELETE FROM app_account_balances WHERE user_id=1")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(accounts.CashReconciliationRequired):
                accounts.ensure_initial_financial_accounts(conn, 1, {"giro": 1})
            conn.rollback()
            self.assertEqual(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0], 1250)

    def test_completed_model_cannot_be_switched_back_to_stale_legacy_paths(self):
        f = self.fresh()
        with closing(f.connect()) as conn:
            with self.assertRaisesRegex(ValueError, "canonical_cash_model_cannot_be_disabled"):
                accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            self.assertTrue(accounts.is_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1))

    def test_transition_failure_rolls_back_even_when_caller_catches_error(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
            plan = reconciliation.prepare_user_plan(conn, 1)
            before = reconciliation.user_checksum(conn, 1)
            conn.execute("BEGIN IMMEDIATE")
            with patch.object(reconciliation, "assert_cash_consistent", side_effect=RuntimeError("synthetic-check-failure")):
                with self.assertRaisesRegex(RuntimeError, "synthetic-check-failure"):
                    reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])
            conn.commit()
            self.assertEqual(before, reconciliation.user_checksum(conn, 1))

    def test_transition_preserves_actual_budget_wealth_chart_and_report_truth(self):
        f = self.fixture(sprint2.Sprint2AccountReferenceTests)
        with closing(f.connect()) as conn:
            accounts.set_feature_enabled(conn, 1, accounts.FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            state.ensure_app_properties_table(conn)
            ensure_consumer_debt_schema(conn)
            conn.execute("INSERT INTO app_properties(user_id,market_value,remaining_debt) VALUES(1,180000,190000)")
            conn.execute("INSERT INTO app_consumer_debts(user_id,name,debt_type,outstanding_balance,active) VALUES(1,'Synthetic','credit_card',500,1)")
            conn.commit()
            state.build_live_app_data(conn, 1, activate_due_savings=False)
            state.capture_monthly_financial_snapshot(conn, 1, "2026-09", 100)
            conn.commit()
            before = state.build_live_app_data(conn, 1, activate_due_savings=False)
            conn.commit()
            protected = reconciliation.user_checksum(conn, 1, protected_only=True)
            other = reconciliation.user_checksum(conn, 2)
            plan = reconciliation.prepare_user_plan(conn, 1)
            with patch.object(report_engine, "DB_NAME", str(f.db_path)):
                report_before = report_engine._report_cash_truth(1)
            conn.execute("BEGIN IMMEDIATE")
            reconciliation.apply_user_plan(conn, plan, operator_approved_checksum=plan["before_checksum"])
            conn.commit()
            after = state.build_live_app_data(conn, 1, activate_due_savings=False)
            with patch.object(report_engine, "DB_NAME", str(f.db_path)):
                report_after = report_engine._report_cash_truth(1)
            self.assertEqual(before["netWorth"], -8750)
            for key in ("netWorth", "sts", "budgets", "tx", "reports", "series", "histDates", "chartV2", "goals", "score"):
                self.assertEqual(before.get(key), after.get(key), key)
            self.assertEqual(report_before["current_cash"], report_after["current_cash"])
            self.assertEqual(protected, reconciliation.user_checksum(conn, 1, protected_only=True))
            self.assertEqual(other, reconciliation.user_checksum(conn, 2))

    def test_receipts_and_transition_markers_are_deleted_only_for_owner(self):
        f = self.fresh()
        with closing(f.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            accounts.create_financial_account(conn, 1, "wallet", "Synthetic", 20, request_id="delete-once")
            other = reconciliation.user_checksum(conn, 2)
            accounts.delete_financial_account_data(conn, 1)
            for table in ("app_financial_account_requests", "app_financial_account_migrations", "app_financial_accounts"):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE user_id=1').fetchone()[0], 0)
            self.assertEqual(other, reconciliation.user_checksum(conn, 2))
            conn.rollback()


if __name__ == "__main__":
    unittest.main()
