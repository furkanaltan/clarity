"""Daily market valuation for explicitly configured ETF and stock positions.

The module is independent from Telegram so the App API and the daily scheduler can
share one valuation path. A position only becomes automatic after a symbol,
quantity and quote currency have been verified successfully.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path


TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
SUPPORTED_QUOTE_CURRENCIES = frozenset({"EUR", "USD", "GBP", "CHF"})
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,31}$")


def ensure_market_tracking_schema(conn: sqlite3.Connection) -> None:
    """Add live-valuation fields without changing existing manual holdings."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'portfolio_holdings'"
    ).fetchone()
    if not table:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_holdings)").fetchall()}
    migrations = (
        ("instrument_type", "ALTER TABLE portfolio_holdings ADD COLUMN instrument_type TEXT NOT NULL DEFAULT 'etf'"),
        ("quantity", "ALTER TABLE portfolio_holdings ADD COLUMN quantity REAL"),
        ("quote_currency", "ALTER TABLE portfolio_holdings ADD COLUMN quote_currency TEXT"),
        ("market_value", "ALTER TABLE portfolio_holdings ADD COLUMN market_value REAL"),
        ("market_value_updated_at", "ALTER TABLE portfolio_holdings ADD COLUMN market_value_updated_at DATETIME"),
        ("valuation_enabled", "ALTER TABLE portfolio_holdings ADD COLUMN valuation_enabled INTEGER NOT NULL DEFAULT 0"),
    )
    for name, ddl in migrations:
        if name not in columns:
            conn.execute(ddl)


def normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    return symbol if SYMBOL_RE.fullmatch(symbol) else ""


def normalize_currency(value: object) -> str:
    currency = str(value or "EUR").strip().upper()
    return currency if currency in SUPPORTED_QUOTE_CURRENCIES else ""


def _request_json(path: str, params: dict[str, object], api_key: str | None = None) -> dict:
    key = (api_key or os.getenv("TWELVE_DATA_API_KEY") or "").strip()
    if not key:
        raise ValueError("market_api_key_missing")
    query = urllib.parse.urlencode({**params, "apikey": key})
    request = urllib.request.Request(
        f"{TWELVE_DATA_BASE_URL}{path}?{query}",
        headers={"User-Agent": "Rov.E/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except Exception as exc:
        raise ValueError("market_provider_unavailable") from exc
    if not isinstance(data, dict) or data.get("status") == "error" or data.get("code"):
        raise ValueError("market_symbol_not_found")
    return data


def fetch_eur_quote(
    symbol: str,
    quote_currency: str,
    api_key: str | None = None,
    fx_cache: dict[str, float] | None = None,
) -> dict:
    """Return the latest verified price and its EUR equivalent."""
    clean_symbol = normalize_symbol(symbol)
    clean_currency = normalize_currency(quote_currency)
    if not clean_symbol or not clean_currency:
        raise ValueError("valid_market_position_required")
    quote = _request_json("/quote", {"symbol": clean_symbol}, api_key)
    provider_currency = str(quote.get("currency") or "").strip().upper()
    if provider_currency and provider_currency != clean_currency:
        raise ValueError("market_currency_mismatch")
    try:
        native_price = float(quote["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("market_symbol_not_found") from exc
    if native_price <= 0:
        raise ValueError("market_symbol_not_found")

    fx_rate = 1.0
    if clean_currency != "EUR":
        fx_rate = float((fx_cache or {}).get(clean_currency) or 0)
        if fx_rate <= 0:
            fx = _request_json("/price", {"symbol": f"{clean_currency}/EUR"}, api_key)
            try:
                fx_rate = float(fx["price"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("market_currency_unavailable") from exc
            if fx_rate <= 0:
                raise ValueError("market_currency_unavailable")
            if fx_cache is not None:
                fx_cache[clean_currency] = fx_rate
    return {
        "symbol": clean_symbol,
        "currency": clean_currency,
        "native_price": round(native_price, 8),
        "eur_price": round(native_price * fx_rate, 8),
    }


def apply_market_quote(
    conn: sqlite3.Connection,
    holding_id: int,
    quote: dict,
    *,
    expected_symbol: str | None = None,
) -> dict:
    """Apply one quote atomically and adjust the user's aggregate by the delta only."""
    ensure_market_tracking_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """SELECT id, user_id, instrument_label, price_symbol, quantity,
                  total_invested, market_value, valuation_enabled
             FROM portfolio_holdings WHERE id = ?""",
        (holding_id,),
    ).fetchone()
    if not row:
        conn.rollback()
        raise ValueError("market_position_not_found")
    if expected_symbol and str(row["price_symbol"] or "").upper() != expected_symbol.upper():
        conn.rollback()
        raise ValueError("market_position_changed")
    quantity = float(row["quantity"] or 0)
    if not row["valuation_enabled"] or quantity <= 0:
        conn.rollback()
        raise ValueError("market_position_not_configured")

    market_value = round(quantity * float(quote["eur_price"]), 2)
    previous_value = row["market_value"]
    if previous_value is None:
        previous_value = row["total_invested"] or 0
    delta = round(market_value - float(previous_value), 2)
    total_row = conn.execute(
        "SELECT current_investments FROM users WHERE user_id = ?", (row["user_id"],)
    ).fetchone()
    if not total_row:
        conn.rollback()
        raise ValueError("market_user_not_found")
    new_total = round(max(0.0, float(total_row["current_investments"] or 0) + delta), 2)

    conn.execute(
        """UPDATE portfolio_holdings
              SET last_price = ?, market_value = ?, last_checked_at = CURRENT_TIMESTAMP,
                  market_value_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?""",
        (quote["native_price"], market_value, holding_id),
    )
    conn.execute(
        "UPDATE users SET current_investments = ? WHERE user_id = ?",
        (new_total, row["user_id"]),
    )
    if abs(delta) >= 0.01:
        conn.execute(
            """INSERT INTO investment_events
               (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
               VALUES (?, ?, ?, ?, ?, 'market_valuation', 'twelve_data', ?)""",
            (
                row["user_id"], abs(delta), "in" if delta > 0 else "out", "market",
                row["instrument_label"],
                f"Taegliche Kursbewertung {quote['symbol']} in EUR",
            ),
        )
    conn.commit()
    return {"market_value": market_value, "delta": delta, "investment_total": new_total}


def refresh_all_market_positions(db_path: str | Path, api_key: str | None = None) -> dict:
    """Refresh all enabled positions; one bad symbol never blocks the remaining portfolio."""
    path = str(db_path)
    with sqlite3.connect(path, timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        ensure_market_tracking_schema(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT id, price_symbol, quote_currency
                 FROM portfolio_holdings
                WHERE valuation_enabled = 1 AND quantity > 0 AND price_symbol IS NOT NULL"""
        ).fetchall()
    updated = 0
    failures: list[dict[str, str]] = []
    fx_cache: dict[str, float] = {}
    for row in rows:
        try:
            quote = fetch_eur_quote(
                row["price_symbol"], row["quote_currency"], api_key, fx_cache=fx_cache
            )
            with sqlite3.connect(path, timeout=20) as conn:
                conn.row_factory = sqlite3.Row
                apply_market_quote(conn, row["id"], quote, expected_symbol=row["price_symbol"])
            updated += 1
        except Exception as exc:
            # Eine einzelne falsche Position oder ein kurzzeitiger DB-Konflikt darf
            # die Bewertungen aller anderen Nutzer nicht verhindern.
            failures.append({
                "symbol": str(row["price_symbol"] or ""),
                "error": str(exc) or exc.__class__.__name__,
            })
    return {
        "updated": updated,
        "failed": len(failures),
        "total": len(rows),
        "failures": failures,
    }
