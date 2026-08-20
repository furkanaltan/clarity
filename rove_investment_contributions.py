"""Holding-bound ETF contributions without inventing shares or market prices."""

from __future__ import annotations

import sqlite3


PENDING_EVENT_TYPE = "recurring_plan_pending"
RECONCILIATION_EVENT_TYPE = "holding_reconciliation"
CONTRIBUTION_EVENT_TYPES = ("recurring_plan", PENDING_EVENT_TYPE)


def ensure_investment_contribution_schema(conn: sqlite3.Connection) -> None:
    """Add the nullable holding reference to the existing event ledger."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investment_events'"
    ).fetchone()
    if not table:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(investment_events)")}
    if "holding_id" not in columns:
        conn.execute("ALTER TABLE investment_events ADD COLUMN holding_id INTEGER")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_investment_events_holding
               ON investment_events(user_id, holding_id, created_at)"""
    )


def require_user_holding(
    conn: sqlite3.Connection, user_id: int, holding_id: int
) -> sqlite3.Row:
    row = conn.execute(
        """SELECT id, user_id, instrument_label,
                  LOWER(COALESCE(instrument_type, 'etf')) AS instrument_type,
                  COALESCE(total_invested, 0) AS total_invested,
                  COALESCE(valuation_enabled, 0) AS valuation_enabled,
                  quantity, price_symbol
             FROM portfolio_holdings
            WHERE id = ? AND user_id = ? LIMIT 1""",
        (int(holding_id), int(user_id)),
    ).fetchone()
    if not row:
        raise LookupError("etf_holding_not_found")
    if str(row["instrument_type"]).lower() != "etf":
        raise LookupError("etf_holding_not_found")
    return row


def holding_contribution_summary(
    conn: sqlite3.Connection, user_id: int, holding_id: int
) -> dict[str, float]:
    """Return historical contributions and the still-unpriced live remainder."""
    ensure_investment_contribution_schema(conn)
    require_user_holding(conn, user_id, holding_id)
    row = conn.execute(
        """SELECT
               COALESCE(SUM(CASE
                   WHEN event_type IN ('recurring_plan', 'recurring_plan_pending')
                    AND direction = 'in' THEN amount ELSE 0 END), 0) AS contributed,
               COALESCE(SUM(CASE
                   WHEN event_type = 'recurring_plan_pending' AND direction = 'in' THEN amount
                   WHEN event_type = 'holding_reconciliation' AND direction = 'out' THEN -amount
                   ELSE 0 END), 0) AS pending
             FROM investment_events
            WHERE user_id = ? AND holding_id = ? AND asset_type = 'etf'""",
        (int(user_id), int(holding_id)),
    ).fetchone()
    return {
        "contributed": round(max(0.0, float(row["contributed"] or 0)), 2),
        "pending": round(max(0.0, float(row["pending"] or 0)), 2),
    }


def record_holding_contribution(
    conn: sqlite3.Connection,
    user_id: int,
    holding_id: int,
    amount: float,
    *,
    source: str = "app_etf_plan",
    note: str = "ETF-Sparplanzahlung in Rov.E erfasst",
    created_at: str | None = None,
) -> dict[str, object]:
    """Book a real contribution while leaving live valuation fields untouched."""
    ensure_investment_contribution_schema(conn)
    holding = require_user_holding(conn, user_id, holding_id)
    value = round(float(amount), 2)
    if value <= 0:
        raise ValueError("valid_etf_contribution_required")
    is_live = bool(
        holding["valuation_enabled"]
        and float(holding["quantity"] or 0) > 0
        and str(holding["price_symbol"] or "").strip()
    )
    event_type = PENDING_EVENT_TYPE if is_live else "recurring_plan"
    if not is_live:
        conn.execute(
            """UPDATE portfolio_holdings
                  SET total_invested = ROUND(COALESCE(total_invested, 0) + ?, 2),
                      updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?""",
            (value, int(holding_id), int(user_id)),
        )
    timestamp_sql = "COALESCE(?, CURRENT_TIMESTAMP)"
    conn.execute(
        f"""INSERT INTO investment_events
               (user_id, amount, direction, asset_type, asset_name, event_type,
                source, note, holding_id, created_at)
           VALUES (?, ?, 'in', 'etf', ?, ?, ?, ?, ?, {timestamp_sql})""",
        (
            int(user_id), value, str(holding["instrument_label"]), event_type,
            source, note, int(holding_id), created_at,
        ),
    )
    return {
        "holdingId": int(holding_id),
        "name": str(holding["instrument_label"]),
        "amount": value,
        "pending": is_live,
    }


def reconcile_pending_contribution(
    conn: sqlite3.Connection,
    user_id: int,
    holding_id: int,
    amount: float,
) -> float:
    """Move a pending contribution into a newly supplied real share quantity."""
    ensure_investment_contribution_schema(conn)
    holding = require_user_holding(conn, user_id, holding_id)
    pending = holding_contribution_summary(conn, user_id, holding_id)["pending"]
    applied = round(min(max(0.0, float(amount)), pending), 2)
    if applied < 0.01:
        return 0.0
    total_row = conn.execute(
        "SELECT current_investments FROM users WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    current_total = round(max(0.0, float(total_row["current_investments"] or 0)), 2)
    if applied > current_total + 0.009:
        raise ValueError("investment_total_inconsistent")
    conn.execute(
        """INSERT INTO investment_events
               (user_id, amount, direction, asset_type, asset_name, event_type,
                source, note, holding_id)
           VALUES (?, ?, 'out', 'etf', ?, 'holding_reconciliation', 'app',
                   'Sparplanzahlung durch aktualisierte Stückzahl abgebildet', ?)""",
        (int(user_id), applied, str(holding["instrument_label"]), int(holding_id)),
    )
    conn.execute(
        "UPDATE users SET current_investments = ? WHERE user_id = ?",
        (round(current_total - applied, 2), int(user_id)),
    )
    return applied
