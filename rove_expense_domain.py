"""Shared atomic expense write for the app and Telegram bot."""

from __future__ import annotations

import sqlite3

from rove_app_state import (
    ACCOUNT_META,
    ensure_app_account_balances_table,
    ensure_app_cash_movements_table,
)
from rove_financial_accounts import (
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    adjust_financial_account_balance,
    ensure_financial_account_reference_schema,
    get_legacy_financial_account,
    is_feature_enabled,
    require_account_role,
    require_financial_account,
)
from rove_score import award_tracking_points


def begin_expense_write(conn: sqlite3.Connection) -> None:
    """Serialize the read-modify-write sequence before reading any balance."""
    conn.execute("BEGIN IMMEDIATE")


def ensure_expense_request_id_schema(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(expenses)")}
    if "request_id" not in columns:
        conn.execute("ALTER TABLE expenses ADD COLUMN request_id TEXT")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_user_request_id
           ON expenses(user_id, request_id)
           WHERE request_id IS NOT NULL AND TRIM(request_id) <> ''"""
    )


def _legacy_balances(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    ensure_app_account_balances_table(conn)
    balances = {key: 0.0 for key in ACCOUNT_META}
    rows = conn.execute(
        "SELECT account_key, amount FROM app_account_balances WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    if rows:
        for row in rows:
            key = str(row["account_key"])
            if key in balances:
                value = float(row["amount"] or 0)
                balances[key] = round(value if key == "giro" else max(0.0, value), 2)
        return balances
    user = conn.execute("SELECT current_cash FROM users WHERE user_id = ?", (user_id,)).fetchone()
    balances["giro"] = round(max(0.0, float(user["current_cash"] or 0)), 2) if user else 0.0
    return balances


def _save_legacy_balances(conn: sqlite3.Connection, user_id: int, balances: dict[str, float]) -> None:
    for key in ACCOUNT_META:
        value = float(balances.get(key, 0.0))
        if key != "giro":
            value = max(0.0, value)
        conn.execute(
            """INSERT INTO app_account_balances (user_id, account_key, amount, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, account_key)
               DO UPDATE SET amount = excluded.amount, updated_at = CURRENT_TIMESTAMP""",
            (user_id, key, round(value, 2)),
        )
    conn.execute(
        "UPDATE users SET current_cash = ? WHERE user_id = ?",
        (round(sum(balances.values()), 2), user_id),
    )


def create_expense_for_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    amount: float,
    category: str,
    merchant: str,
    description: str,
    request_id: str | None = None,
    paid_cash: bool = False,
) -> dict:
    """Create one expense and its cash effect in the open write transaction."""
    if not conn.in_transaction:
        raise RuntimeError("expense_write_requires_transaction")
    amount = round(abs(float(amount or 0)), 2)
    if amount <= 0:
        raise ValueError("amount_required")

    ensure_expense_request_id_schema(conn)
    request_id = str(request_id or "").strip()[:128] or None
    if request_id:
        existing = conn.execute(
            """SELECT id, amount, category, merchant
               FROM expenses WHERE user_id = ? AND request_id = ? LIMIT 1""",
            (user_id, request_id),
        ).fetchone()
        if existing:
            return {
                "id": int(existing["id"]),
                "amount": round(float(existing["amount"] or 0), 2),
                "category": str(existing["category"] or ""),
                "merchant": str(existing["merchant"] or ""),
                "paid_cash": False,
                "cash_applied": 0.0,
                "giro_applied": 0.0,
                "idempotent_replay": True,
                "reward": None,
            }

    ensure_app_cash_movements_table(conn)
    pilot = is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1)
    account_id = None
    if pilot:
        ensure_financial_account_reference_schema(conn)
        if paid_cash:
            account = get_legacy_financial_account(conn, user_id, "bargeld")
            if not account or str(account["status"]) != "active":
                raise LookupError("legacy_financial_account_not_found")
        else:
            account = require_account_role(conn, user_id, "expense")
        account_id = int(account["id"])
        if paid_cash and amount > float(account["balance"] or 0.0) + 0.009:
            raise ValueError("cash_balance_insufficient")

    if pilot:
        cursor = conn.execute(
            """INSERT INTO expenses
               (user_id, amount, category, merchant, description, account_id, request_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, amount, category, merchant, description, account_id, request_id),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO expenses
               (user_id, amount, category, merchant, description, request_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, amount, category, merchant, description, request_id),
        )
    expense_id = int(cursor.lastrowid)

    if pilot:
        adjust_financial_account_balance(
            conn, user_id, account_id, -amount, require_funds=paid_cash
        )
        conn.execute(
            """INSERT INTO app_cash_movements
               (user_id, kind, amount, expense_id, source_account_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, "payment" if paid_cash else "card", amount, expense_id, account_id),
        )
    else:
        balances = _legacy_balances(conn, user_id)
        key = "bargeld" if paid_cash else "giro"
        if paid_cash and amount > balances[key] + 0.009:
            raise ValueError("cash_balance_insufficient")
        balances[key] = round(balances[key] - amount, 2)
        _save_legacy_balances(conn, user_id, balances)
        conn.execute(
            """INSERT INTO app_cash_movements (user_id, kind, amount, expense_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, "payment" if paid_cash else "card", amount, expense_id),
        )

    user_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(users)")}
    reward = (
        award_tracking_points(conn, user_id, expense_id=expense_id)
        if {"last_activity_date", "streak_days", "clarity_points"}.issubset(user_columns)
        else None
    )
    return {
        "id": expense_id,
        "amount": amount,
        "category": category,
        "merchant": merchant,
        "paid_cash": paid_cash,
        "cash_applied": amount if paid_cash else 0.0,
        "giro_applied": 0.0 if paid_cash else amount,
        "idempotent_replay": False,
        "reward": reward,
    }
