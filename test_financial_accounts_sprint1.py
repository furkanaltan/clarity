from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from migrate_financial_accounts import run
from rove_app_api import DATA_EXPORT_TABLES, export_table_rows
from rove_financial_accounts import (
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    delete_financial_account_data,
    ensure_financial_accounts_schema,
    financial_accounts_total,
    get_financial_account,
    is_feature_enabled,
    list_financial_accounts,
    resolve_account_role,
    set_account_role,
    set_feature_enabled,
    update_financial_account_balance,
)


def create_legacy_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                current_cash REAL DEFAULT 0,
                current_investments REAL DEFAULT 0
            );
            CREATE TABLE user_access (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
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
                amount REAL
            );
            CREATE TABLE app_cash_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                amount REAL
            );
            CREATE TABLE app_properties (
                user_id INTEGER PRIMARY KEY,
                market_value REAL,
                remaining_debt REAL
            );
            """
        )
        conn.commit()


def add_user(
    path: Path,
    user_id: int,
    current_cash: float,
    *,
    status: str = "approved",
    balances: dict[str, float] | None = None,
    investments: float = 0,
) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "INSERT INTO users(user_id, current_cash, current_investments) VALUES (?, ?, ?)",
            (user_id, current_cash, investments),
        )
        conn.execute("INSERT INTO user_access(user_id, status) VALUES (?, ?)", (user_id, status))
        for key, amount in (balances or {}).items():
            conn.execute(
                "INSERT INTO app_account_balances(user_id, account_key, amount) VALUES (?, ?, ?)",
                (user_id, key, amount),
            )
        conn.execute("INSERT INTO expenses(user_id, amount) VALUES (?, 12.34)", (user_id,))
        conn.execute(
            "INSERT INTO app_cash_movements(user_id, kind, amount) VALUES (?, 'card', 12.34)",
            (user_id,),
        )
        conn.commit()


class FinancialAccountsSprint1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "clarity.db"
        self.connections: list[sqlite3.Connection] = []
        create_legacy_db(self.db_path)

    def tearDown(self) -> None:
        for conn in self.connections:
            conn.close()
        self.temp_dir.cleanup()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self.connections.append(conn)
        return conn

    def test_dry_run_is_read_only_and_reports_fallback(self) -> None:
        add_user(self.db_path, 1, 1250, status="app_only")
        result = run(self.db_path, apply=False)
        self.assertEqual(result["summary"], {"total": 1, "ready_or_success": 1, "blocked": 0})
        self.assertEqual(result["users"][0]["planned_accounts"], 1)
        with self.connect() as conn:
            self.assertFalse(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='app_financial_accounts'"
            ).fetchone())

    def test_apply_bootstraps_all_legacy_types_and_is_idempotent(self) -> None:
        add_user(
            self.db_path,
            1,
            5100,
            balances={"giro": 0, "tagesgeld": 5000, "bargeld": 100},
            investments=9000,
        )
        first = run(self.db_path, apply=True)
        second = run(self.db_path, apply=True)
        self.assertEqual(first["summary"]["blocked"], 0)
        self.assertEqual(second["summary"]["blocked"], 0)
        self.assertTrue(Path(first["backup"]).is_file())
        with self.connect() as conn:
            accounts = list_financial_accounts(conn, 1)
            self.assertEqual(len(accounts), 3)
            self.assertEqual(financial_accounts_total(conn, 1), 5100)
            self.assertEqual(
                {row["legacy_key"] for row in accounts}, {"giro", "tagesgeld", "bargeld"}
            )
            self.assertEqual(
                {role: int(resolve_account_role(conn, 1, role)["id"]) for role in (
                    "expense", "income", "fixed_cost", "screenshot"
                )},
                {role: int(resolve_account_role(conn, 1, "expense")["id"]) for role in (
                    "expense", "income", "fixed_cost", "screenshot"
                )},
            )
            self.assertFalse(is_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1))
            self.assertEqual(conn.execute("SELECT current_investments FROM users WHERE user_id=1").fetchone()[0], 9000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expenses WHERE user_id=1").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_cash_movements WHERE user_id=1").fetchone()[0], 1)

    def test_negative_giro_and_zero_accounts_are_preserved(self) -> None:
        add_user(
            self.db_path,
            1,
            400,
            balances={"giro": -100, "tagesgeld": 500, "bargeld": 0},
        )
        result = run(self.db_path, apply=True)
        self.assertEqual(result["summary"]["blocked"], 0)
        with self.connect() as conn:
            values = {row["legacy_key"]: row["balance"] for row in list_financial_accounts(conn, 1)}
            self.assertEqual(values, {"giro": -100, "tagesgeld": 500, "bargeld": 0})

    def test_negative_current_cash_fallback_is_preserved_exactly(self) -> None:
        add_user(self.db_path, 1, -125, status="app_only")
        result = run(self.db_path, apply=True)
        self.assertEqual(result["summary"]["blocked"], 0)
        with self.connect() as conn:
            account = list_financial_accounts(conn, 1)[0]
            self.assertEqual(account["legacy_key"], "giro")
            self.assertEqual(account["balance"], -125)

    def test_mismatch_blocks_only_affected_user(self) -> None:
        add_user(self.db_path, 1, 1000, balances={"giro": 900})
        add_user(self.db_path, 2, 200, status="app_only", balances={"giro": 200})
        result = run(self.db_path, apply=True)
        self.assertEqual(result["summary"]["blocked"], 1)
        with self.connect() as conn:
            self.assertEqual(len(list_financial_accounts(conn, 1)), 0)
            self.assertEqual(len(list_financial_accounts(conn, 2)), 1)

    def test_dry_run_detects_drift_in_existing_new_accounts(self) -> None:
        add_user(self.db_path, 1, 100)
        run(self.db_path, apply=True)
        with self.connect() as conn:
            conn.execute(
                "UPDATE app_financial_accounts SET balance = 99 WHERE user_id = 1"
            )
        result = run(self.db_path, apply=False)
        self.assertEqual(result["summary"]["blocked"], 1)
        self.assertEqual(
            result["users"][0]["reason"],
            "financial_account_sum_differs_from_current_cash",
        )

    def test_user_bound_helpers_reject_foreign_account(self) -> None:
        add_user(self.db_path, 1, 100)
        add_user(self.db_path, 2, 200)
        run(self.db_path, apply=True)
        with self.connect() as conn:
            account_1 = list_financial_accounts(conn, 1)[0]
            account_2 = list_financial_accounts(conn, 2)[0]
            self.assertIsNone(get_financial_account(conn, 1, int(account_2["id"])))
            with self.assertRaises(LookupError):
                set_account_role(conn, 1, "expense", int(account_2["id"]))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """UPDATE app_financial_account_roles SET account_id = ?
                        WHERE user_id = 1 AND role = 'income'""",
                    (int(account_2["id"]),),
                )
            conn.rollback()
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(LookupError):
                update_financial_account_balance(conn, 1, int(account_2["id"]), 999)
            conn.rollback()
            self.assertEqual(get_financial_account(conn, 1, int(account_1["id"]))["balance"], 100)

    def test_dual_write_primitive_updates_new_and_legacy_atomically(self) -> None:
        add_user(self.db_path, 1, 100)
        run(self.db_path, apply=True)
        with self.connect() as conn:
            account_id = int(list_financial_accounts(conn, 1)[0]["id"])
            conn.execute("BEGIN IMMEDIATE")
            update_financial_account_balance(conn, 1, account_id, 75.25)
            conn.commit()
            self.assertEqual(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0], 75.25)
            self.assertEqual(conn.execute(
                "SELECT amount FROM app_account_balances WHERE user_id=1 AND account_key='giro'"
            ).fetchone()[0], 75.25)

    def test_export_and_user_scoped_deletion(self) -> None:
        add_user(self.db_path, 1, 100)
        add_user(self.db_path, 2, 200)
        run(self.db_path, apply=True)
        export_names = dict(DATA_EXPORT_TABLES)
        self.assertEqual(export_names["financial_accounts"], "app_financial_accounts")
        self.assertEqual(export_names["financial_account_roles"], "app_financial_account_roles")
        with self.connect() as conn:
            columns, rows = export_table_rows(conn, "app_financial_accounts", 1)
            self.assertIn("balance", columns)
            self.assertEqual(len(rows), 1)
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
            delete_financial_account_data(conn, 1)
            conn.commit()
            self.assertEqual(len(list_financial_accounts(conn, 1)), 0)
            self.assertEqual(len(list_financial_accounts(conn, 2)), 1)
            self.assertFalse(is_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1))
            self.assertFalse(is_feature_enabled(conn, 2, FEATURE_MULTI_CASH_ACCOUNTS_V1))


if __name__ == "__main__":
    unittest.main()
