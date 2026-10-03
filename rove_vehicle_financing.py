"""User-scoped vehicle financing linked to the canonical contract rate."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation


FINANCING_TYPES = frozenset({"financed", "leasing"})


class VehicleFinancingConflictError(ValueError):
    pass


def ensure_vehicle_financing_schema(conn) -> None:
    from rove_app_state import ensure_app_contracts_table

    ensure_app_contracts_table(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_vehicle_financings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL,
            contract_id         TEXT NOT NULL,
            vehicle_name        TEXT NOT NULL,
            financing_type      TEXT NOT NULL CHECK(financing_type IN ('financed', 'leasing')),
            purchase_price      REAL,
            outstanding_balance REAL,
            remaining_months   INTEGER,
            contract_end_date  TEXT,
            active              INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            request_id          TEXT,
            request_fingerprint TEXT,
            created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, contract_id) REFERENCES app_contracts(user_id, contract_id)
                ON DELETE CASCADE
        )"""
    )
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_financing_user_contract
                    ON app_vehicle_financings(user_id, contract_id)""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_financing_user_request
                    ON app_vehicle_financings(user_id, request_id)
                    WHERE request_id IS NOT NULL AND TRIM(request_id) <> ''""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_vehicle_financing_user_active
                    ON app_vehicle_financings(user_id, active)""")


def _money(value, *, required=False, maximum=100_000_000.0):
    if value in (None, "") and not required:
        return None
    try:
        if isinstance(value, str):
            text = value.strip().replace(" ", "").replace("€", "")
            if "," in text:
                text = text.replace(".", "").replace(",", ".")
            elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
                text = text.replace(".", "")
            amount = Decimal(text)
        else:
            amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_vehicle_financing_amount") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal(str(maximum)):
        raise ValueError("invalid_vehicle_financing_amount")
    return amount.quantize(Decimal("0.01"))


def _months(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("invalid_vehicle_financing_term")
    try:
        months = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_vehicle_financing_term") from None
    if months < 0 or months > 600:
        raise ValueError("invalid_vehicle_financing_term")
    return months


def _end_date(value):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError("invalid_vehicle_financing_end_date") from None


def _validated(payload: dict, *, existing: dict | None = None) -> dict:
    current = existing or {}
    name = str(payload.get("vehicle_name", current.get("vehicle_name", ""))).strip()
    financing_type = str(payload.get("financing_type", current.get("financing_type", ""))).strip().lower()
    if not name or len(name) > 120:
        raise ValueError("invalid_vehicle_name")
    if financing_type not in FINANCING_TYPES:
        raise ValueError("invalid_vehicle_financing_type")

    purchase = _money(payload.get("purchase_price", current.get("purchase_price")),
                      required=financing_type == "financed")
    balance = _money(payload.get("outstanding_balance", current.get("outstanding_balance")),
                     required=financing_type == "financed")
    if financing_type == "financed":
        if purchase is None or purchase <= 0 or balance is None or balance > purchase:
            raise ValueError("invalid_vehicle_financing_balance")
    else:
        if balance not in (None, Decimal("0.00")):
            raise ValueError("leasing_cannot_have_outstanding_balance")
        purchase = balance = None

    monthly = _money(payload.get("monthly_payment", current.get("monthly_payment")), required=True)
    if monthly is None or monthly <= 0:
        raise ValueError("valid_vehicle_financing_payment_required")
    return {
        "vehicle_name": name,
        "financing_type": financing_type,
        "purchase_price": purchase,
        "outstanding_balance": balance,
        "monthly_payment": monthly,
        "remaining_months": _months(payload.get("remaining_months", current.get("remaining_months"))),
        "contract_end_date": _end_date(payload.get("contract_end_date", current.get("contract_end_date"))),
    }


def _fingerprint(values: dict) -> str:
    normalized = {
        key: (format(value, ".2f") if isinstance(value, Decimal) else value)
        for key, value in values.items()
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contract_name(values: dict) -> str:
    label = "Finanzierung" if values["financing_type"] == "financed" else "Leasing"
    return f"{values['vehicle_name']} · {label}"


def _helpers():
    from rove_app_state import ensure_app_contracts_table, sync_contract_fixed_costs

    return ensure_app_contracts_table, sync_contract_fixed_costs


def _row(conn, user_id: int, financing_id: int):
    return conn.execute(
        """SELECT v.*, c.amount AS monthly_payment
             FROM app_vehicle_financings v
             LEFT JOIN app_contracts c
               ON c.user_id=v.user_id AND c.contract_id=v.contract_id
            WHERE v.user_id=? AND v.id=?""",
        (user_id, financing_id),
    ).fetchone()


def _serialize(row) -> dict:
    purchase = float(row["purchase_price"]) if row["purchase_price"] is not None else None
    balance = float(row["outstanding_balance"]) if row["outstanding_balance"] is not None else None
    return {
        "id": int(row["id"]), "user_id": int(row["user_id"]),
        "contract_id": str(row["contract_id"]), "vehicle_name": str(row["vehicle_name"]),
        "financing_type": str(row["financing_type"]), "purchase_price": purchase,
        "outstanding_balance": balance,
        "paid_amount": round(purchase - balance, 2) if purchase is not None and balance is not None else None,
        "remaining_months": row["remaining_months"], "contract_end_date": row["contract_end_date"],
        "monthly_payment": float(row["monthly_payment"]) if row["monthly_payment"] is not None else None,
        "active": bool(row["active"]),
    }


def list_vehicle_financings(conn, user_id: int) -> list[dict]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_vehicle_financings'").fetchone():
        return []
    rows = conn.execute(
        """SELECT v.*, c.amount AS monthly_payment
             FROM app_vehicle_financings v
             LEFT JOIN app_contracts c
               ON c.user_id=v.user_id AND c.contract_id=v.contract_id
            WHERE v.user_id=? AND v.active=1 ORDER BY v.id""",
        (user_id,),
    ).fetchall()
    return [_serialize(row) for row in rows]


def create_vehicle_financing(conn, user_id: int, payload: dict, request_id: str) -> int:
    ensure_contracts, sync_contracts = _helpers()
    ensure_contracts(conn)
    ensure_vehicle_financing_schema(conn)
    request_id = str(request_id or "").strip()[:128]
    if not request_id:
        raise ValueError("vehicle_financing_request_id_required")
    values = _validated(payload)
    fingerprint = _fingerprint(values)
    existing = conn.execute(
        "SELECT id, request_fingerprint FROM app_vehicle_financings WHERE user_id=? AND request_id=?",
        (user_id, request_id),
    ).fetchone()
    if existing:
        if existing["request_fingerprint"] != fingerprint:
            raise VehicleFinancingConflictError("vehicle_financing_request_conflict")
        return int(existing["id"])
    contract_id = secrets.token_urlsafe(9)
    contract_name = _contract_name(values)
    if conn.execute(
        "SELECT 1 FROM app_contracts WHERE user_id=? AND LOWER(TRIM(name))=LOWER(TRIM(?)) LIMIT 1",
        (user_id, contract_name),
    ).fetchone():
        raise VehicleFinancingConflictError("vehicle_financing_contract_exists")
    conn.execute(
        """INSERT INTO app_contracts
           (user_id, contract_id, detail_key, name, category, amount, icon, tint, cancelable)
           VALUES (?, ?, ?, ?, 'Mobilität', ?, 'car', '#D07D00', 0)""",
        (user_id, contract_id, f"vehicle_{contract_id}", contract_name, float(values["monthly_payment"])),
    )
    row = conn.execute(
        """INSERT INTO app_vehicle_financings
           (user_id, contract_id, vehicle_name, financing_type, purchase_price,
            outstanding_balance, remaining_months, contract_end_date, request_id, request_fingerprint)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, contract_id, values["vehicle_name"], values["financing_type"],
         float(values["purchase_price"]) if values["purchase_price"] is not None else None,
         float(values["outstanding_balance"]) if values["outstanding_balance"] is not None else None,
         values["remaining_months"], values["contract_end_date"], request_id, fingerprint),
    )
    sync_contracts(conn, user_id)
    return int(row.lastrowid)


def update_vehicle_financing(conn, user_id: int, financing_id: int, payload: dict) -> int:
    ensure_contracts, sync_contracts = _helpers()
    ensure_contracts(conn)
    ensure_vehicle_financing_schema(conn)
    row = _row(conn, user_id, financing_id)
    if not row or not row["active"]:
        raise LookupError("vehicle_financing_not_found")
    values = _validated(payload, existing=dict(row))
    contract_name = _contract_name(values)
    if conn.execute(
        """SELECT 1 FROM app_contracts
            WHERE user_id=? AND LOWER(TRIM(name))=LOWER(TRIM(?)) AND contract_id<>? LIMIT 1""",
        (user_id, contract_name, row["contract_id"]),
    ).fetchone():
        raise VehicleFinancingConflictError("vehicle_financing_contract_exists")
    conn.execute(
        """UPDATE app_contracts SET name=?, amount=?, category='Mobilität', icon='car', tint='#D07D00',
               updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND contract_id=?""",
        (contract_name, float(values["monthly_payment"]), user_id, row["contract_id"]),
    )
    conn.execute(
        """UPDATE app_vehicle_financings
              SET vehicle_name=?, financing_type=?, purchase_price=?, outstanding_balance=?,
                  remaining_months=?, contract_end_date=?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=? AND id=?""",
        (values["vehicle_name"], values["financing_type"],
         float(values["purchase_price"]) if values["purchase_price"] is not None else None,
         float(values["outstanding_balance"]) if values["outstanding_balance"] is not None else None,
         values["remaining_months"], values["contract_end_date"], user_id, financing_id),
    )
    sync_contracts(conn, user_id)
    return financing_id


def delete_vehicle_financing(conn, user_id: int, financing_id: int) -> None:
    ensure_contracts, sync_contracts = _helpers()
    ensure_contracts(conn)
    ensure_vehicle_financing_schema(conn)
    row = _row(conn, user_id, financing_id)
    if not row:
        raise LookupError("vehicle_financing_not_found")
    conn.execute("DELETE FROM app_vehicle_financings WHERE user_id=? AND id=?", (user_id, financing_id))
    conn.execute("DELETE FROM app_contracts WHERE user_id=? AND contract_id=?", (user_id, row["contract_id"]))
    sync_contracts(conn, user_id)
