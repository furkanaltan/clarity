"""User-owned valuables, independent of cash, balances, and financial history.

Write callers must enable SQLite foreign keys, BEGIN a transaction, and roll
back the entire transaction on failure. No helper commits or changes connection
settings. Reads never prepare the schema. Create receipts hash the cleaned item;
origin is insertion metadata and is never changed by a replay or an update.
"""

import hashlib
import json
import re
import sqlite3
import unicodedata
from decimal import Decimal, InvalidOperation


__all__ = [
    "ensure_valuables_schema",
    "list_valuables",
    "clean_valuable",
    "clean_valuables",
    "create_valuable",
    "update_valuable",
    "delete_valuable",
    "claim_valuable_request",
    "valuable_request_matches",
]

_ID = re.compile(r"[0-9a-f]{32}")
_ORIGINS = ("manual", "legacy", "onboarding")
_MAX_VALUE = 10_000_000
_MAX_ITEMS = 50
_MAX_ROWS = 500


def ensure_valuables_schema(conn: sqlite3.Connection) -> None:
    """Add only the live positions and durable, payload-free request receipts."""
    conn.execute("""CREATE TABLE IF NOT EXISTS app_valuables (
        user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        position_id TEXT NOT NULL
            CHECK(length(position_id) = 32 AND position_id NOT GLOB '*[^0-9a-f]*'),
        name TEXT NOT NULL,
        value_cents INTEGER NOT NULL
            CHECK(typeof(value_cents) = 'integer'
                  AND value_cents BETWEEN 1 AND 1000000000),
        origin TEXT NOT NULL CHECK(origin IN ('manual', 'legacy', 'onboarding')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, position_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_valuable_requests (
        user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        request_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL
            CHECK(length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, request_id)
    )""")


def _clean_id(position_id: str) -> str:
    if not isinstance(position_id, str) or _ID.fullmatch(position_id) is None:
        raise ValueError("invalid_valuable_id")
    return position_id


def _value_cents(value: float) -> int:
    # Only JSON number types are accepted; never coerce strings or booleans.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_valuable_value")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 < amount <= _MAX_VALUE:
            raise ValueError("invalid_valuable_value")
        # Integer arithmetic stays exact even if another module changes the
        # thread's Decimal precision or rounding/trap settings.
        numerator, denominator = amount.as_integer_ratio()
        cents, remainder = divmod(numerator * 100, denominator)
        if remainder:
            raise ValueError("invalid_valuable_value")
        return cents
    except InvalidOperation:
        raise ValueError("invalid_valuable_value") from None


def clean_valuable(item: dict) -> dict:
    """Validate exactly {id, n, v}, returning an NFC name and a float value."""
    if not isinstance(item, dict) or set(item) != {"id", "n", "v"}:
        raise ValueError("invalid_valuable")
    position_id = _clean_id(item["id"])
    name = item["n"]
    if not isinstance(name, str) or any(
        unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in name
    ):
        raise ValueError("invalid_valuable_name")
    name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(name) <= 80:
        raise ValueError("invalid_valuable_name")
    return {"id": position_id, "n": name, "v": _value_cents(item["v"]) / 100.0}


def clean_valuables(items: list) -> list[dict]:
    """Validate a list of at most 50 items without generating or merging IDs."""
    if not isinstance(items, list) or len(items) > _MAX_ITEMS:
        raise ValueError("invalid_valuables")
    cleaned = [clean_valuable(item) for item in items]
    if len({item["id"] for item in cleaned}) != len(cleaned):
        raise ValueError("duplicate_valuable_id")
    return cleaned


def list_valuables(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Read only this user's live positions, in stable ID order, without DDL."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_valuables'"
    ).fetchone():
        return []
    rows = conn.execute(
        """SELECT position_id, name, value_cents FROM app_valuables
           WHERE user_id=? ORDER BY position_id""",
        (user_id,),
    ).fetchall()
    return [{"id": row[0], "n": row[1], "v": row[2] / 100.0} for row in rows]


def _clean_request_id(request_id: str) -> str:
    if (
        not isinstance(request_id, str)
        or not 1 <= len(request_id) <= 128
        or any(not " " <= char <= "~" for char in request_id)
    ):
        raise ValueError("invalid_valuable_request_id")
    return request_id


def _request_payload_hash(payload: object) -> str:
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError):
        raise ValueError("invalid_valuable_request_payload") from None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def valuable_request_matches(
    conn: sqlite3.Connection, user_id: int, request_id: str, payload: object
) -> bool:
    """Read-only receipt check; missing or differing receipts return False.

    Invalid keys/payloads raise the same ValueError as claim, but this helper
    never prepares tables, claims a key, or changes connection settings.
    """
    request_id = _clean_request_id(request_id)
    payload_hash = _request_payload_hash(payload)
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_valuable_requests'"
    ).fetchone():
        return False
    row = conn.execute(
        """SELECT payload_hash FROM app_valuable_requests
           WHERE user_id=? AND request_id=?""",
        (user_id, request_id),
    ).fetchone()
    return row is not None and row[0] == payload_hash


def claim_valuable_request(
    conn: sqlite3.Connection, user_id: int, request_id: str, payload: object
) -> bool:
    """Claim an exact caller-supplied key; return False for an identical replay."""
    request_id = _clean_request_id(request_id)
    payload_hash = _request_payload_hash(payload)
    ensure_valuables_schema(conn)
    result = conn.execute(
        """INSERT INTO app_valuable_requests(user_id, request_id, payload_hash)
           VALUES (?, ?, ?) ON CONFLICT(user_id, request_id) DO NOTHING""",
        (user_id, request_id, payload_hash),
    )
    if result.rowcount:
        return True
    previous = conn.execute(
        """SELECT payload_hash FROM app_valuable_requests
           WHERE user_id=? AND request_id=?""",
        (user_id, request_id),
    ).fetchone()
    if previous[0] != payload_hash:
        raise ValueError("valuable_request_conflict")
    return False


def create_valuable(
    conn: sqlite3.Connection, user_id: int, item: dict, *, origin: str = "manual"
) -> bool:
    """Insert without overwriting; return False for a replay or identical row.

    The receipt write serializes writers before the live-row check and limit.
    Receipts intentionally have no position foreign key, so deletion never
    permits an old create request to resurrect a position.
    """
    item = clean_valuable(item)
    if not isinstance(origin, str) or origin not in _ORIGINS:
        raise ValueError("invalid_valuable_origin")
    if not claim_valuable_request(conn, user_id, "create:" + item["id"], item):
        return False
    cents = _value_cents(item["v"])
    existing = conn.execute(
        "SELECT name, value_cents FROM app_valuables WHERE user_id=? AND position_id=?",
        (user_id, item["id"]),
    ).fetchone()
    if existing is not None:
        if existing[0] != item["n"] or existing[1] != cents:
            raise ValueError("valuable_request_conflict")
        return False
    count = conn.execute(
        "SELECT COUNT(*) FROM app_valuables WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    if count >= _MAX_ROWS:
        raise ValueError("valuable_limit_reached")
    conn.execute(
        """INSERT INTO app_valuables(user_id, position_id, name, value_cents, origin)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, item["id"], item["n"], cents, origin),
    )
    return True


def update_valuable(
    conn: sqlite3.Connection, user_id: int, position_id: str, item: dict
) -> bool:
    """Replace an owned row's name/value, never its identity or origin."""
    position_id = _clean_id(position_id)
    item = clean_valuable(item)
    if item["id"] != position_id:
        raise ValueError("invalid_valuable_id")
    ensure_valuables_schema(conn)
    result = conn.execute(
        """UPDATE app_valuables SET name=?, value_cents=?, updated_at=CURRENT_TIMESTAMP
           WHERE user_id=? AND position_id=?""",
        (item["n"], _value_cents(item["v"]), user_id, position_id),
    )
    return bool(result.rowcount)


def delete_valuable(conn: sqlite3.Connection, user_id: int, position_id: str) -> bool:
    """Delete only an owned live position, preserving all request receipts."""
    position_id = _clean_id(position_id)
    ensure_valuables_schema(conn)
    result = conn.execute(
        "DELETE FROM app_valuables WHERE user_id=? AND position_id=?",
        (user_id, position_id),
    )
    return bool(result.rowcount)
