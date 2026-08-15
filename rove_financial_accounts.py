"""Additives Cash-account foundation for Rov.E multi-account migration.

This module deliberately does not run schema setup at import time. Sprint 1 is
activated only by the explicit migration command; existing API money paths keep
using ``app_account_balances`` and ``users.current_cash`` unchanged.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping


FEATURE_MULTI_CASH_ACCOUNTS_V1 = "multi_cash_accounts_v1"
ACCOUNT_TYPES = ("checking", "savings", "wallet")
ACCOUNT_ROLES = ("expense", "income", "fixed_cost", "screenshot")
LEGACY_ACCOUNT_META = {
    "giro": ("checking", "Girokonto"),
    "tagesgeld": ("savings", "Tagesgeld"),
    "bargeld": ("wallet", "Bargeld"),
}
TYPE_TO_LEGACY_KEY = {account_type: key for key, (account_type, _name) in LEGACY_ACCOUNT_META.items()}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def ensure_financial_accounts_schema(conn: sqlite3.Connection) -> None:
    """Create only the additive Sprint-1 tables and validate their shape."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_financial_accounts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            account_type TEXT NOT NULL CHECK(account_type IN ('checking', 'savings', 'wallet')),
            name         TEXT NOT NULL,
            currency     TEXT NOT NULL DEFAULT 'EUR' CHECK(currency = 'EUR'),
            balance      REAL NOT NULL DEFAULT 0.0,
            legacy_key   TEXT CHECK(legacy_key IS NULL OR legacy_key IN ('giro', 'tagesgeld', 'bargeld')),
            source       TEXT NOT NULL DEFAULT 'legacy' CHECK(source IN ('legacy', 'manual')),
            status       TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
            created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            archived_at  DATETIME,
            UNIQUE (user_id, id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_app_financial_accounts_legacy
               ON app_financial_accounts(user_id, legacy_key)
             WHERE legacy_key IS NOT NULL"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_app_financial_accounts_user_status
               ON app_financial_accounts(user_id, status, id)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_financial_account_roles (
            user_id    INTEGER NOT NULL,
            role       TEXT NOT NULL CHECK(role IN ('expense', 'income', 'fixed_cost', 'screenshot')),
            account_id INTEGER NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, role),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, account_id)
                REFERENCES app_financial_accounts(user_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_app_financial_account_roles_account
               ON app_financial_account_roles(user_id, account_id)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_user_features (
            user_id    INTEGER NOT NULL,
            feature_key TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, feature_key),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    required = {
        "app_financial_accounts": {
            "id", "user_id", "account_type", "name", "currency", "balance",
            "legacy_key", "source", "status", "created_at", "updated_at", "archived_at",
        },
        "app_financial_account_roles": {"user_id", "role", "account_id", "updated_at"},
        "app_user_features": {"user_id", "feature_key", "enabled", "updated_at"},
    }
    for table, expected_columns in required.items():
        actual = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
        missing = expected_columns - actual
        if missing:
            raise RuntimeError(f"invalid_{table}_schema_missing:{','.join(sorted(missing))}")


def is_feature_enabled(conn: sqlite3.Connection, user_id: int, feature_key: str) -> bool:
    if not table_exists(conn, "app_user_features"):
        return False
    row = conn.execute(
        """SELECT enabled FROM app_user_features
             WHERE user_id = ? AND feature_key = ?""",
        (user_id, feature_key),
    ).fetchone()
    return bool(row and int(row[0] or 0) == 1)


def set_feature_enabled(
    conn: sqlite3.Connection, user_id: int, feature_key: str, enabled: bool
) -> None:
    """Persist a feature flag only for an existing user."""
    if not conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
        raise LookupError("user_not_found")
    conn.execute(
        """INSERT INTO app_user_features (user_id, feature_key, enabled, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, feature_key) DO UPDATE SET
               enabled = excluded.enabled,
               updated_at = CURRENT_TIMESTAMP""",
        (user_id, feature_key, 1 if enabled else 0),
    )


def list_financial_accounts(
    conn: sqlite3.Connection, user_id: int, *, include_archived: bool = False
) -> list[sqlite3.Row]:
    if not table_exists(conn, "app_financial_accounts"):
        return []
    status_sql = "" if include_archived else " AND status = 'active'"
    return conn.execute(
        f"""SELECT id, user_id, account_type, name, currency, balance, legacy_key,
                    source, status, created_at, updated_at, archived_at
               FROM app_financial_accounts
              WHERE user_id = ?{status_sql}
              ORDER BY id""",
        (user_id,),
    ).fetchall()


def get_financial_account(
    conn: sqlite3.Connection, user_id: int, account_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT id, user_id, account_type, name, currency, balance, legacy_key,
                  source, status, created_at, updated_at, archived_at
             FROM app_financial_accounts
            WHERE id = ? AND user_id = ?""",
        (account_id, user_id),
    ).fetchone()


def get_legacy_financial_account(
    conn: sqlite3.Connection, user_id: int, legacy_key: str
) -> sqlite3.Row | None:
    if legacy_key not in LEGACY_ACCOUNT_META:
        raise ValueError("invalid_legacy_key")
    return conn.execute(
        """SELECT id, user_id, account_type, name, currency, balance, legacy_key,
                  source, status, created_at, updated_at, archived_at
             FROM app_financial_accounts
            WHERE user_id = ? AND legacy_key = ?""",
        (user_id, legacy_key),
    ).fetchone()


def resolve_account_role(
    conn: sqlite3.Connection, user_id: int, role: str
) -> sqlite3.Row | None:
    if role not in ACCOUNT_ROLES:
        raise ValueError("invalid_account_role")
    return conn.execute(
        """SELECT a.id, a.user_id, a.account_type, a.name, a.currency, a.balance,
                  a.legacy_key, a.source, a.status, a.created_at, a.updated_at, a.archived_at
             FROM app_financial_account_roles r
             JOIN app_financial_accounts a
               ON a.id = r.account_id AND a.user_id = r.user_id
            WHERE r.user_id = ? AND r.role = ?""",
        (user_id, role),
    ).fetchone()


def set_account_role(
    conn: sqlite3.Connection, user_id: int, role: str, account_id: int
) -> None:
    if role not in ACCOUNT_ROLES:
        raise ValueError("invalid_account_role")
    account = get_financial_account(conn, user_id, account_id)
    if not account or str(account["status"]) != "active":
        raise LookupError("financial_account_not_found")
    conn.execute(
        """INSERT INTO app_financial_account_roles (user_id, role, account_id, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, role) DO UPDATE SET
               account_id = excluded.account_id,
               updated_at = CURRENT_TIMESTAMP""",
        (user_id, role, account_id),
    )


def financial_accounts_total(conn: sqlite3.Connection, user_id: int) -> float:
    row = conn.execute(
        """SELECT COALESCE(SUM(balance), 0.0)
             FROM app_financial_accounts WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    return round(float(row[0] or 0.0), 2)


def legacy_balances_from_financial_accounts(
    conn: sqlite3.Connection, user_id: int
) -> dict[str, float]:
    """Aggregate account types back to the three still-active legacy keys."""
    balances = {key: 0.0 for key in LEGACY_ACCOUNT_META}
    rows = conn.execute(
        """SELECT account_type, COALESCE(SUM(balance), 0.0) AS amount
             FROM app_financial_accounts
            WHERE user_id = ?
            GROUP BY account_type""",
        (user_id,),
    ).fetchall()
    for row in rows:
        key = TYPE_TO_LEGACY_KEY.get(str(row["account_type"]))
        if not key:
            raise ValueError("unsupported_cash_account_type")
        balances[key] = round(float(row["amount"] or 0.0), 2)
    return balances


def mirror_financial_accounts_to_legacy(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    """Mirror new balances atomically inside the caller's open transaction.

    The caller owns commit/rollback. This function is intentionally unused by
    existing endpoints while the feature flag remains off.
    """
    if not conn.in_transaction:
        raise RuntimeError("financial_account_write_requires_transaction")
    balances = legacy_balances_from_financial_accounts(conn, user_id)
    for key, amount in balances.items():
        conn.execute(
            """INSERT INTO app_account_balances (user_id, account_key, amount, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, account_key) DO UPDATE SET
                   amount = excluded.amount,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, key, amount),
        )
    total = round(sum(balances.values()), 2)
    cur = conn.execute(
        "UPDATE users SET current_cash = ? WHERE user_id = ?", (total, user_id)
    )
    if cur.rowcount != 1:
        raise LookupError("user_not_found")
    return balances


def update_financial_account_balance(
    conn: sqlite3.Connection, user_id: int, account_id: int, balance: float
) -> dict[str, float]:
    """User-bound dual-write primitive for a later feature-flagged endpoint."""
    if not conn.in_transaction:
        raise RuntimeError("financial_account_write_requires_transaction")
    cur = conn.execute(
        """UPDATE app_financial_accounts
              SET balance = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND status = 'active'""",
        (round(float(balance), 2), account_id, user_id),
    )
    if cur.rowcount != 1:
        raise LookupError("financial_account_not_found")
    return mirror_financial_accounts_to_legacy(conn, user_id)


def roles_summary(conn: sqlite3.Connection, user_id: int) -> Mapping[str, int]:
    if not table_exists(conn, "app_financial_account_roles"):
        return {}
    return {
        str(row["role"]): int(row["account_id"])
        for row in conn.execute(
            """SELECT role, account_id FROM app_financial_account_roles
                 WHERE user_id = ? ORDER BY role""",
            (user_id,),
        )
    }


def delete_financial_account_data(conn: sqlite3.Connection, user_id: int) -> None:
    """Delete Sprint-1 data in FK-safe order for exactly one user."""
    for table in (
        "app_financial_account_roles",
        "app_financial_accounts",
        "app_user_features",
    ):
        if table_exists(conn, table):
            conn.execute(f'DELETE FROM "{table}" WHERE user_id = ?', (user_id,))
