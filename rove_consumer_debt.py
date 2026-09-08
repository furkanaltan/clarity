"""User-scoped consumer principal; never cash movements or monthly payments."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
import sqlite3


DEBT_TYPES = ("personal_loan", "installment_loan", "credit_card", "bnpl", "other_consumer_debt")


class ConsumerDebtConflictError(ValueError):
    pass


def ensure_consumer_debt_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS app_consumer_debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        debt_type TEXT NOT NULL CHECK(debt_type IN
            ('personal_loan','installment_loan','credit_card','bnpl','other_consumer_debt')),
        outstanding_balance REAL NOT NULL CHECK(outstanding_balance >= 0 AND outstanding_balance <= 10000000),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_consumer_debts_user ON app_consumer_debts(user_id, active)")
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(app_consumer_debts)")}
    if "request_id" not in columns:
        conn.execute("ALTER TABLE app_consumer_debts ADD COLUMN request_id TEXT")
    if "request_fingerprint" not in columns:
        conn.execute("ALTER TABLE app_consumer_debts ADD COLUMN request_fingerprint TEXT")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_consumer_debts_user_request
                   ON app_consumer_debts(user_id, request_id)
                   WHERE request_id IS NOT NULL AND TRIM(request_id) <> ''""")


def list_consumer_debts(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_consumer_debts'").fetchone():
        return []
    rows = conn.execute("SELECT * FROM app_consumer_debts WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    return [dict(row) for row in rows]


def total_consumer_debt(conn: sqlite3.Connection, user_id: int) -> float:
    return float(sum((Decimal(str(row["outstanding_balance"])) for row in
                      list_consumer_debts(conn, user_id) if row["active"]), Decimal(0)))


def net_worth_total(cash, investments, property_equity, consumer_debt):
    """None means missing historical evidence, not a zero balance."""
    if any(value is None for value in (cash, investments, property_equity, consumer_debt)):
        return None
    return float((Decimal(str(cash)) + Decimal(str(investments)) +
                  Decimal(str(property_equity)) - Decimal(str(consumer_debt))).quantize(Decimal("0.01")))


def _fingerprint(name: str, debt_type: str, balance: Decimal, active: bool) -> str:
    data = json.dumps({"name": name, "debt_type": debt_type,
                       "outstanding_balance": format(balance, ".2f"), "active": active},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def save_consumer_debt(conn: sqlite3.Connection, user_id: int, payload: dict, debt_id=None,
                       request_id: str | None = None) -> int:
    name = payload.get("name")
    debt_type = payload.get("debt_type")
    active = payload.get("active", True)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError("invalid_consumer_debt_name")
    if debt_type not in DEBT_TYPES:
        raise ValueError("invalid_consumer_debt_type")
    if not isinstance(active, bool):
        raise ValueError("invalid_consumer_debt_active")
    try:
        raw = payload["outstanding_balance"]
        if isinstance(raw, bool):
            raise ValueError
        balance = Decimal(str(raw))
        if not balance.is_finite() or not 0 <= balance <= 10000000:
            raise ValueError
        balance = balance.quantize(Decimal("0.01"))
    except (KeyError, ValueError, InvalidOperation):
        raise ValueError("invalid_consumer_debt_balance") from None
    ensure_consumer_debt_schema(conn)
    request_id = str(request_id or "").strip()[:128] or None
    fingerprint = _fingerprint(name.strip(), debt_type, balance, active)
    if debt_id is None and request_id:
        existing = conn.execute(
            "SELECT id, request_fingerprint FROM app_consumer_debts WHERE user_id=? AND request_id=?",
            (user_id, request_id),
        ).fetchone()
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise ConsumerDebtConflictError("consumer_debt_request_conflict")
            return int(existing["id"])
    if debt_id is None:
        return conn.execute("""INSERT INTO app_consumer_debts
            (user_id,name,debt_type,outstanding_balance,active,request_id,request_fingerprint)
            VALUES (?,?,?,?,?,?,?)""",
            (user_id, name.strip(), debt_type, float(balance), int(active), request_id, fingerprint)).lastrowid
    result = conn.execute("""UPDATE app_consumer_debts SET name=?,debt_type=?,
        outstanding_balance=?,active=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
        (name.strip(), debt_type, float(balance), int(active), debt_id, user_id))
    if not result.rowcount:
        raise LookupError("consumer_debt_not_found")
    return debt_id


def delete_consumer_debt(conn: sqlite3.Connection, user_id: int, debt_id: int) -> None:
    ensure_consumer_debt_schema(conn)
    if not conn.execute("DELETE FROM app_consumer_debts WHERE id=? AND user_id=?", (debt_id, user_id)).rowcount:
        raise LookupError("consumer_debt_not_found")
