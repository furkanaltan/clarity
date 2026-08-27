from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from manage_financial_accounts_pilot import run as manage_pilot
from migrate_financial_account_references import run as migrate_references
from migrate_financial_accounts import run as migrate_sprint1
from rove_financial_accounts import (
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    ensure_financial_account_reference_schema,
    get_legacy_financial_account,
    set_account_role,
    set_feature_enabled,
)


def create_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                current_cash REAL DEFAULT 0,
                current_investments REAL DEFAULT 0,
                income REAL DEFAULT 0,
                other_income REAL DEFAULT 0,
                fixed_costs REAL DEFAULT 0,
                fixed_costs_details TEXT DEFAULT '{}',
                etf_savings REAL DEFAULT 0,
                cash_savings REAL DEFAULT 0
            );
            CREATE TABLE user_access (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE app_state_links (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                expires_at TEXT NOT NULL
            );
            CREATE TABLE app_account_balances (
                user_id INTEGER NOT NULL,
                account_key TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, account_key)
            );
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                category TEXT,
                merchant TEXT,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                expense_id INTEGER,
                label TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE investment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                direction TEXT,
                asset_type TEXT,
                asset_name TEXT,
                event_type TEXT,
                source TEXT,
                note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                scope TEXT,
                source TEXT,
                note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """INSERT INTO users
                   (user_id, current_cash, current_investments, income, fixed_costs,
                    etf_savings, cash_savings)
               VALUES (1, 1250, 500, 3000, 900, 20, 100)"""
        )
        conn.execute("INSERT INTO user_access VALUES (1, 'approved')")
        conn.execute(
            """INSERT INTO app_state_links(token,user_id,status,expires_at)
               VALUES ('pilot-token',1,'active','2099-01-01 00:00:00')"""
        )
        for key, amount in (("giro", 1000), ("tagesgeld", 200), ("bargeld", 50)):
            conn.execute(
                "INSERT INTO app_account_balances(user_id,account_key,amount) VALUES (1,?,?)",
                (key, amount),
            )
        conn.execute("INSERT INTO users(user_id,current_cash) VALUES (2, 80)")
        conn.execute("INSERT INTO user_access VALUES (2, 'approved')")
        conn.execute(
            """INSERT INTO app_state_links(token,user_id,status,expires_at)
               VALUES ('other-token',2,'active','2099-01-01 00:00:00')"""
        )
        conn.commit()
    result = migrate_sprint1(path, apply=True)
    if result["summary"]["blocked"]:
        raise RuntimeError(result)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        api.ensure_app_cash_movements_table(conn)
        ensure_financial_account_reference_schema(conn)
        set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
        conn.commit()


class Sprint2AccountReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_db(self.db_path)
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "user_from_token", lambda _conn, token: {"pilot-token": 1, "other-token": 2}.get(token)),
            patch.object(api, "build_live_app_data", lambda _conn, _uid: {"sts": {"available": 0}, "budgets": []}),
            patch.object(api, "award_tracking_points", lambda *_a, **_k: {"awarded": 0}),
            patch.object(api, "reverse_tracking_points_for_deleted_expense", lambda *_a, **_k: False),
            patch.object(api, "category_rule_for_merchant", lambda *_a, **_k: None),
            patch.object(api, "apply_due_scheduled_savings", lambda *_a, **_k: None),
        ]
        for item in self.patchers:
            item.start()
        api.app.config.update(TESTING=True)

    def tearDown(self) -> None:
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def request(self, method: str, path: str, *, token: str = "pilot-token", json=None):
        with api.app.test_client() as client:
            return client.open(
                path,
                method=method,
                json=json,
                headers={"Origin": "https://getrove.de", "Authorization": f"Bearer {token}"},
            )

    def account(self, key: str, user_id: int = 1):
        with closing(self.connect()) as conn:
            return dict(get_legacy_financial_account(conn, user_id, key))

    def legacy(self, key: str) -> float:
        with closing(self.connect()) as conn:
            return float(conn.execute(
                "SELECT amount FROM app_account_balances WHERE user_id=1 AND account_key=?",
                (key,),
            ).fetchone()[0])

    def test_card_uses_role_and_delete_refunds_original_account_after_role_change(self) -> None:
        response = self.request("POST", "/v1/expenses", json={
            "amount": 25, "merchant": "Test", "category": "Sonstiges",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        expense_id = int(response.get_json()["id"])
        with closing(self.connect()) as conn:
            expense = conn.execute("SELECT account_id FROM expenses WHERE id=?", (expense_id,)).fetchone()
            movement = conn.execute(
                "SELECT source_account_id FROM app_cash_movements WHERE expense_id=?", (expense_id,)
            ).fetchone()
            giro = get_legacy_financial_account(conn, 1, "giro")
            tagesgeld = get_legacy_financial_account(conn, 1, "tagesgeld")
            self.assertEqual(int(expense[0]), int(giro["id"]))
            self.assertEqual(int(movement[0]), int(giro["id"]))
            set_account_role(conn, 1, "expense", int(tagesgeld["id"]))
            conn.commit()
        self.assertEqual(self.account("giro")["balance"], 975)
        self.assertEqual(self.legacy("giro"), 975)

        deleted = self.request("DELETE", f"/v1/expenses/{expense_id}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(self.account("giro")["balance"], 1000)
        self.assertEqual(self.account("tagesgeld")["balance"], 200)
        self.assertEqual(self.legacy("giro"), 1000)

    def test_cash_payment_requires_funds_and_refunds_wallet(self) -> None:
        denied = self.request("POST", "/v1/expenses", json={
            "amount": 60, "merchant": "Bar", "category": "Sonstiges", "paid_cash": True,
        })
        self.assertEqual(denied.status_code, 400)
        booked = self.request("POST", "/v1/expenses", json={
            "amount": 20, "merchant": "Bar", "category": "Sonstiges", "paid_cash": True,
        })
        self.assertEqual(booked.status_code, 200, booked.get_json())
        self.assertEqual(self.account("bargeld")["balance"], 30)
        self.request("DELETE", f"/v1/expenses/{booked.get_json()['id']}")
        self.assertEqual(self.account("bargeld")["balance"], 50)

    def test_income_and_flag_rollback_delete_use_saved_target(self) -> None:
        booked = self.request("POST", "/v1/income", json={"amount": 75, "label": "Bonus"})
        self.assertEqual(booked.status_code, 200, booked.get_json())
        movement_id = int(booked.get_json()["id"])
        with closing(self.connect()) as conn:
            target_id = conn.execute(
                "SELECT target_account_id FROM app_cash_movements WHERE id=?", (movement_id,)
            ).fetchone()[0]
            self.assertIsNotNone(target_id)
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
        deleted = self.request("DELETE", f"/v1/cash-movements/{movement_id}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(self.account("giro")["balance"], 1000)
        self.assertEqual(self.legacy("giro"), 1000)

    def test_flag_off_legacy_write_is_not_lost_when_old_pilot_expense_is_deleted(self) -> None:
        pilot_expense = self.request("POST", "/v1/expenses", json={
            "amount": 25, "merchant": "Pilot", "category": "Sonstiges",
        })
        self.assertEqual(pilot_expense.status_code, 200, pilot_expense.get_json())
        with closing(self.connect()) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
        legacy_expense = self.request("POST", "/v1/expenses", json={
            "amount": 10, "merchant": "Legacy danach", "category": "Sonstiges",
        })
        self.assertEqual(legacy_expense.status_code, 200, legacy_expense.get_json())
        self.assertEqual(self.legacy("giro"), 965)
        self.assertEqual(self.account("giro")["balance"], 975)

        deleted = self.request("DELETE", f"/v1/expenses/{pilot_expense.get_json()['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(self.legacy("giro"), 990)
        self.assertEqual(self.account("giro")["balance"], 990)

    def test_withdrawal_has_source_target_and_reverses(self) -> None:
        booked = self.request("POST", "/v1/accounts", json={
            "action": "transfer", "from": "giro", "to": "bargeld",
            "amount": 30, "log": "withdrawal",
        })
        self.assertEqual(booked.status_code, 200, booked.get_json())
        with closing(self.connect()) as conn:
            movement = conn.execute(
                """SELECT id, source_account_id, target_account_id FROM app_cash_movements
                     WHERE kind='withdrawal'"""
            ).fetchone()
            self.assertIsNotNone(movement["source_account_id"])
            self.assertIsNotNone(movement["target_account_id"])
            movement_id = int(movement["id"])
        self.assertEqual(self.account("giro")["balance"], 970)
        self.assertEqual(self.account("bargeld")["balance"], 80)
        deleted = self.request("DELETE", f"/v1/cash-movements/{movement_id}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(self.account("giro")["balance"], 1000)
        self.assertEqual(self.account("bargeld")["balance"], 50)

    def test_screenshot_batch_uses_screenshot_role_and_remains_idempotent(self) -> None:
        payload = {"transactions": [
            {"amount": 12.5, "merchant": "Lidl", "category": "Lebensmittel", "importKey": "a" * 32},
            {"amount": 7.5, "merchant": "Aldi", "category": "Lebensmittel", "importKey": "b" * 32},
        ]}
        first = self.request("POST", "/v1/import/screenshot/commit", json=payload)
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self.request("POST", "/v1/import/screenshot/commit", json=payload)
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(len(second.get_json()["inserted"]), 0)
        self.assertEqual(self.account("giro")["balance"], 980)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """SELECT e.account_id, m.source_account_id FROM expenses e
                     JOIN app_cash_movements m ON m.expense_id=e.id ORDER BY e.id"""
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row[0] and row[0] == row[1] for row in rows))

    def test_monthly_income_and_fixed_costs_are_symmetric(self) -> None:
        income = self.request("POST", "/v1/monthly-plan", json={"action": "confirm_income"})
        self.assertEqual(income.status_code, 200, income.get_json())
        fixed = self.request("POST", "/v1/monthly-plan", json={"action": "confirm_fixed_costs"})
        self.assertEqual(fixed.status_code, 200, fixed.get_json())
        self.assertEqual(self.account("giro")["balance"], 3100)
        self.request("POST", "/v1/monthly-plan", json={"action": "reopen_fixed_costs"})
        self.request("POST", "/v1/monthly-plan", json={"action": "reopen_income"})
        self.assertEqual(self.account("giro")["balance"], 1000)
        self.assertEqual(self.legacy("giro"), 1000)

    def test_monthly_savings_moves_cash_without_changing_net_worth(self) -> None:
        before = self.account("giro")["balance"] + self.account("tagesgeld")["balance"] + 500
        booked = self.request("POST", "/v1/monthly-plan", json={"action": "confirm_savings"})
        self.assertEqual(booked.status_code, 200, booked.get_json())
        self.assertEqual(self.account("giro")["balance"], 880)
        self.assertEqual(self.account("tagesgeld")["balance"], 300)
        with closing(self.connect()) as conn:
            investments = float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0])
        self.assertEqual(self.account("giro")["balance"] + self.account("tagesgeld")["balance"] + investments, before)
        reopened = self.request("POST", "/v1/monthly-plan", json={"action": "reopen_savings"})
        self.assertEqual(reopened.status_code, 200, reopened.get_json())
        self.assertEqual(self.account("giro")["balance"], 1000)
        self.assertEqual(self.account("tagesgeld")["balance"], 200)

    def test_etf_source_string_is_bound_to_stable_account_id_and_not_double_booked(self) -> None:
        configured = self.request("POST", "/v1/profile", json={
            "etf_plan": {
                "execution_day": 31,
                "source_account": "giro",
                "mode": "confirm",
                "active": True,
            }
        })
        self.assertEqual(configured.status_code, 200, configured.get_json())
        first = self.request("POST", "/v1/etf-plan", json={"action": "execute"})
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self.request("POST", "/v1/etf-plan", json={"action": "execute"})
        self.assertEqual(second.status_code, 200, second.get_json())
        with closing(self.connect()) as conn:
            plan = conn.execute(
                "SELECT source_account, source_account_id FROM app_etf_savings_plan WHERE user_id=1"
            ).fetchone()
            events = int(conn.execute(
                "SELECT COUNT(*) FROM investment_events WHERE user_id=1 AND source='app_etf_plan'"
            ).fetchone()[0])
            investments = float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0])
            giro = get_legacy_financial_account(conn, 1, "giro")
            self.assertEqual(plan["source_account"], "giro")
            self.assertEqual(int(plan["source_account_id"]), int(giro["id"]))
        self.assertEqual(events, 1)
        self.assertEqual(investments, 520)
        self.assertEqual(self.account("giro")["balance"], 980)
        self.assertEqual(self.legacy("giro"), 980)

    def test_foreign_account_cannot_be_assigned(self) -> None:
        with closing(self.connect()) as conn:
            foreign = get_legacy_financial_account(conn, 2, "giro")
            with self.assertRaises(LookupError):
                set_account_role(conn, 1, "expense", int(foreign["id"]))

    def test_flag_off_preserves_legacy_path_and_leaves_refs_null(self) -> None:
        with closing(self.connect()) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
        response = self.request("POST", "/v1/expenses", json={
            "amount": 10, "merchant": "Legacy", "category": "Sonstiges",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            row = conn.execute(
                """SELECT e.account_id, m.source_account_id FROM expenses e
                     JOIN app_cash_movements m ON m.expense_id=e.id"""
            ).fetchone()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])
        self.assertEqual(self.legacy("giro"), 990)
        self.assertEqual(self.account("giro")["balance"], 1000)

    def test_parallel_expenses_do_not_lose_updates(self) -> None:
        statuses: list[int] = []

        def book() -> None:
            response = self.request("POST", "/v1/expenses", json={
                "amount": 30, "merchant": "Parallel", "category": "Sonstiges",
            })
            statuses.append(response.status_code)

        threads = [threading.Thread(target=book) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(statuses), [200, 200])
        self.assertEqual(self.account("giro")["balance"], 940)
        self.assertEqual(self.legacy("giro"), 940)
        with closing(self.connect()) as conn:
            self.assertEqual(float(conn.execute(
                "SELECT current_cash FROM users WHERE user_id=1"
            ).fetchone()[0]), 1190)

    def test_parallel_expense_and_income_preserve_both_updates(self) -> None:
        statuses: list[int] = []

        def expense() -> None:
            statuses.append(self.request("POST", "/v1/expenses", json={
                "amount": 30, "merchant": "Parallel", "category": "Sonstiges",
            }).status_code)

        def income() -> None:
            statuses.append(self.request("POST", "/v1/income", json={
                "amount": 45, "label": "Parallel",
            }).status_code)

        threads = [threading.Thread(target=expense), threading.Thread(target=income)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(statuses), [200, 200])
        self.assertEqual(self.account("giro")["balance"], 1015)
        self.assertEqual(self.legacy("giro"), 1015)

    def test_parallel_identical_screenshot_batches_remain_idempotent(self) -> None:
        payload = {"transactions": [{
            "amount": 12.5,
            "merchant": "Parallel Import",
            "category": "Lebensmittel",
            "importKey": "c" * 32,
        }]}
        inserted_counts: list[int] = []

        def commit() -> None:
            response = self.request("POST", "/v1/import/screenshot/commit", json=payload)
            self.assertEqual(response.status_code, 200, response.get_json())
            inserted_counts.append(len(response.get_json()["inserted"]))

        threads = [threading.Thread(target=commit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(inserted_counts), [0, 1])
        self.assertEqual(self.account("giro")["balance"], 987.5)
        self.assertEqual(self.legacy("giro"), 987.5)

    def test_manual_expense_request_id_is_user_bound_and_idempotent(self) -> None:
        payload = {
            "amount": 30,
            "merchant": "Lidl",
            "category": "Lebensmittel",
            "request_id": "manual-request-1",
        }
        first = self.request("POST", "/v1/expenses", json=payload)
        replay = self.request("POST", "/v1/expenses", json={**payload, "amount": 999})
        second = self.request("POST", "/v1/expenses", json={**payload, "request_id": "manual-request-2"})

        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()["idempotent_replay"])
        self.assertEqual(replay.get_json()["id"], first.get_json()["id"])
        self.assertEqual(second.status_code, 200, second.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expenses WHERE user_id=1").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1 AND kind='card'").fetchone()[0], 2)
        self.assertEqual(self.account("giro")["balance"], 940)

    def test_transaction_state_exposes_real_merchant_not_category_fallback(self) -> None:
        from rove_app_state import _build_tx

        with closing(self.connect()) as conn:
            conn.execute(
                "INSERT INTO expenses (user_id, amount, category, merchant, description) VALUES (1, 5, 'LEBENSMITTEL', NULL, NULL)"
            )
            conn.execute(
                "INSERT INTO expenses (user_id, amount, category, merchant, description) VALUES (1, 7, 'LEBENSMITTEL', 'Hochzeit', NULL)"
            )
            conn.commit()
            items = [item for group in _build_tx(conn, 1) for item in group["items"]]

        merchants = {item["merchant"] for item in items}
        self.assertIn("", merchants)
        self.assertIn("Hochzeit", merchants)
        self.assertNotIn("Lebensmittel", merchants)

    def test_category_write_is_atomic_and_user_bound(self) -> None:
        created = self.request("POST", "/v1/expenses", json={
            "amount": 12, "merchant": "Hochzeit", "category": "Sonstiges",
        })
        expense_id = created.get_json()["id"]
        changed = self.request("POST", f"/v1/expenses/{expense_id}/category", json={
            "category": "Shopping",
        })
        self.assertEqual(changed.status_code, 200, changed.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(
                conn.execute("SELECT category FROM expenses WHERE id=?", (expense_id,)).fetchone()[0],
                "SHOPPING",
            )

    def test_budget_and_contract_writes_remain_consistent_under_parallel_updates(self) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """CREATE TABLE category_budgets (
                   user_id INTEGER, category TEXT, monthly_limit REAL,
                   source TEXT, active_month TEXT,
                   UNIQUE(user_id, category, active_month)
                )"""
            )
            conn.commit()

        budget_statuses: list[int] = []

        def update_budget(limit: int) -> None:
            budget_statuses.append(self.request("POST", "/v1/budgets", json={
                "budgets": [{"category": "Shopping", "limit": limit}],
            }).status_code)

        threads = [threading.Thread(target=update_budget, args=(limit,)) for limit in (200, 250)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(budget_statuses), [200, 200])

        contract_statuses: list[int] = []

        def create_contract(name: str) -> None:
            contract_statuses.append(self.request("POST", "/v1/contracts", json={
                "action": "create", "name": name, "category": "Abos", "amount": 10,
            }).status_code)

        threads = [threading.Thread(target=create_contract, args=(name,)) for name in ("Abo A", "Abo B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(contract_statuses), [200, 200])
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM category_budgets").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_contracts WHERE user_id=1").fetchone()[0], 2)

    def test_reference_migration_and_pilot_control_are_safe_and_reversible(self) -> None:
        first = migrate_references(self.db_path, apply=True)
        second = migrate_references(self.db_path, apply=True)
        self.assertTrue(first["backup"])
        self.assertTrue(second["backup"])
        self.assertEqual(first["after"], second["after"])

        disabled = manage_pilot(self.db_path, 1, "disable")
        self.assertFalse(disabled["feature_enabled_after"])
        enabled = manage_pilot(self.db_path, 1, "enable")
        self.assertTrue(enabled["feature_enabled_after"])
        self.assertEqual(enabled["active_pilots_after"], 1)
        self.assertTrue(all(enabled["checks"].values()))
        self.assertTrue(all(enabled["financial_state_unchanged"].values()))

        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
