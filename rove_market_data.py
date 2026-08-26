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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from rove_investment_contributions import (
    ensure_investment_contribution_schema,
    reconcile_pending_contribution,
)


TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
LEEWAY_BASE_URL = "https://api.leeway.tech/api/v1/public"
COINMARKETCAP_BASE_URL = "https://pro-api.coinmarketcap.com"
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
        ("market_data_provider", "ALTER TABLE portfolio_holdings ADD COLUMN market_data_provider TEXT"),
        ("valuation_enabled", "ALTER TABLE portfolio_holdings ADD COLUMN valuation_enabled INTEGER NOT NULL DEFAULT 0"),
        ("provider_asset_id", "ALTER TABLE portfolio_holdings ADD COLUMN provider_asset_id TEXT"),
        ("position_source", "ALTER TABLE portfolio_holdings ADD COLUMN position_source TEXT"),
        ("import_key", "ALTER TABLE portfolio_holdings ADD COLUMN import_key TEXT"),
    )
    for name, ddl in migrations:
        if name not in columns:
            conn.execute(ddl)
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_crypto_holding_provider_asset
           ON portfolio_holdings(user_id, market_data_provider, provider_asset_id)
           WHERE LOWER(COALESCE(instrument_type, '')) = 'crypto'
             AND provider_asset_id IS NOT NULL AND TRIM(provider_asset_id) <> ''"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_crypto_holding_import_key
           ON portfolio_holdings(user_id, import_key)
           WHERE import_key IS NOT NULL AND TRIM(import_key) <> ''"""
    )


def _cmc_request_json(
    path: str, params: dict[str, object], api_key: str | None = None
) -> dict:
    key = (api_key or os.getenv("COINMARKETCAP_API_KEY") or "").strip()
    if not key:
        raise ValueError("crypto_provider_key_missing")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{COINMARKETCAP_BASE_URL}{path}?{query}",
        headers={"User-Agent": "Rov.E/1.0", "X-CMC_PRO_API_KEY": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("crypto_provider_auth_failed") from exc
        if exc.code == 429:
            raise ValueError("crypto_provider_rate_limit") from exc
        if exc.code in {400, 404, 422}:
            raise ValueError("crypto_asset_not_found") from exc
        raise ValueError("crypto_provider_unavailable") from exc
    except Exception as exc:
        raise ValueError("crypto_provider_unavailable") from exc
    if not isinstance(data, dict):
        raise ValueError("crypto_provider_unavailable")
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    if status.get("error_code"):
        message = str(status.get("error_message") or "").lower()
        if "credit" in message or "rate" in message:
            raise ValueError("crypto_provider_rate_limit")
        raise ValueError("crypto_provider_unavailable")
    return data


def search_crypto_assets(
    query: str, api_key: str | None = None, limit: int = 8
) -> list[dict]:
    """Resolve an exact CoinMarketCap symbol or slug without exposing the key."""
    clean = " ".join(str(query or "").strip().split())[:80]
    if not clean:
        return []
    requests: list[dict[str, object]] = []
    if re.fullmatch(r"[A-Za-z0-9._-]{1,24}", clean):
        requests.append({"symbol": clean.upper(), "listing_status": "active"})
    slug = re.sub(r"[^a-z0-9]+", "-", clean.casefold()).strip("-")
    if slug:
        requests.append({"slug": slug, "listing_status": "active"})
    found: dict[int, dict] = {}
    last_error: ValueError | None = None
    for params in requests:
        try:
            payload = _cmc_request_json("/v1/cryptocurrency/map", params, api_key)
        except ValueError as exc:
            last_error = exc
            continue
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            try:
                asset_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            name = str(row.get("name") or "").strip()
            symbol = str(row.get("symbol") or "").strip().upper()
            if name and symbol:
                found[asset_id] = {
                    "providerAssetId": str(asset_id),
                    "name": name[:80],
                    "symbol": symbol[:24],
                    "provider": "coinmarketcap",
                }
    if not found and last_error and str(last_error) not in {"crypto_asset_not_found"}:
        raise last_error
    needle = clean.casefold()
    ranked = sorted(
        found.values(),
        key=lambda row: (
            0 if row["symbol"].casefold() == needle else 1,
            0 if row["name"].casefold() == needle else 1,
            row["name"],
        ),
    )
    return ranked[: max(1, min(int(limit), 20))]


def fetch_crypto_eur_quotes(
    provider_asset_ids: list[str | int], api_key: str | None = None
) -> dict[str, dict]:
    """Fetch CoinMarketCap quotes in EUR, batched by stable provider IDs."""
    ids: list[str] = []
    for value in provider_asset_ids:
        text = str(value or "").strip()
        if text.isdigit() and int(text) > 0 and text not in ids:
            ids.append(text)
    if not ids:
        return {}
    quotes: dict[str, dict] = {}
    for offset in range(0, len(ids), 100):
        chunk = ids[offset:offset + 100]
        payload = _cmc_request_json(
            "/v2/cryptocurrency/quotes/latest",
            {"id": ",".join(chunk), "convert": "EUR", "skip_invalid": "true"},
            api_key,
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        for asset_id in chunk:
            row = data.get(asset_id)
            if isinstance(row, list):
                row = row[0] if row else None
            if not isinstance(row, dict):
                continue
            eur = row.get("quote", {}).get("EUR", {}) if isinstance(row.get("quote"), dict) else {}
            try:
                price = float(eur.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            quotes[asset_id] = {
                "name": str(row.get("name") or "")[:80],
                "symbol": str(row.get("symbol") or asset_id).upper(),
                "resolved_symbol": str(row.get("symbol") or asset_id).upper(),
                "currency": "EUR",
                "native_price": round(price, 12),
                "eur_price": round(price, 12),
                "provider": "coinmarketcap",
                "provider_asset_id": asset_id,
                "updated_at": eur.get("last_updated") or row.get("last_updated"),
            }
    return quotes


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
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("market_provider_auth_failed") from exc
        if exc.code == 429:
            raise ValueError("market_provider_rate_limit") from exc
        if exc.code in {400, 404, 422}:
            raise ValueError("market_symbol_not_found") from exc
        raise ValueError("market_provider_unavailable") from exc
    except Exception as exc:
        raise ValueError("market_provider_unavailable") from exc
    if not isinstance(data, dict):
        raise ValueError("market_provider_unavailable")
    try:
        provider_code = int(data.get("code") or 0)
    except (TypeError, ValueError):
        provider_code = 0
    if provider_code in {401, 403}:
        raise ValueError("market_provider_auth_failed")
    if provider_code == 429:
        raise ValueError("market_provider_rate_limit")
    if data.get("status") == "error" or provider_code:
        raise ValueError("market_symbol_not_found")
    return data


def _leeway_symbol(symbol: str, currency: str) -> str:
    """Translate a user-facing exchange suffix into Leeway's SYMBOL.EXCHANGE form."""
    clean = normalize_symbol(symbol)
    if ":" in clean:
        ticker, exchange = clean.rsplit(":", 1)
        exchange = {"XETR": "XETRA"}.get(exchange, exchange)
        return f"{ticker}.{exchange}"
    if "." in clean:
        return clean
    return f"{clean}.XETRA" if currency == "EUR" else clean


def _leeway_symbol_candidates(symbol: str, currency: str) -> tuple[str, ...]:
    """Return likely exchanges without overriding an exchange chosen by the user."""
    clean = normalize_symbol(symbol)
    primary = _leeway_symbol(clean, currency)
    if currency == "EUR" and ":" not in clean and "." not in clean:
        return primary, f"{clean}.F"
    return (primary,)


def _request_single_leeway_quote(provider_symbol: str, currency: str, key: str) -> dict:
    query = urllib.parse.urlencode({"apitoken": key})
    request = urllib.request.Request(
        f"{LEEWAY_BASE_URL}/live/{urllib.parse.quote(provider_symbol)}?{query}",
        headers={"User-Agent": "Rov.E/1.0", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("market_europe_provider_auth_failed") from exc
        if exc.code == 429:
            raise ValueError("market_provider_rate_limit") from exc
        if exc.code in {400, 404, 422}:
            raise ValueError("market_symbol_not_found") from exc
        raise ValueError("market_provider_unavailable") from exc
    except Exception as exc:
        raise ValueError("market_provider_unavailable") from exc
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise ValueError("market_symbol_not_found")
    price = next(
        (data.get(key) for key in ("close", "price", "last", "c") if data.get(key) is not None),
        None,
    )
    try:
        native_price = float(price)
    except (TypeError, ValueError) as exc:
        raise ValueError("market_symbol_not_found") from exc
    if native_price <= 0:
        raise ValueError("market_symbol_not_found")
    provider_currency = str(data.get("currency") or currency).strip().upper()
    if provider_currency and provider_currency != currency:
        raise ValueError("market_currency_mismatch")
    return {
        "symbol": provider_symbol,
        "currency": currency,
        "native_price": native_price,
        "provider": "leeway",
    }


def _request_leeway_quote(symbol: str, currency: str, token: str | None = None) -> dict:
    key = (token or os.getenv("LEEWAY_API_TOKEN") or "").strip()
    if not key:
        raise ValueError("market_europe_provider_missing")
    last_not_found: ValueError | None = None
    for provider_symbol in _leeway_symbol_candidates(symbol, currency):
        try:
            return _request_single_leeway_quote(provider_symbol, currency, key)
        except ValueError as exc:
            if str(exc) != "market_symbol_not_found":
                raise
            last_not_found = exc
    raise ValueError("market_symbol_not_found") from last_not_found


def fetch_eur_quote(
    symbol: str,
    quote_currency: str,
    api_key: str | None = None,
    fx_cache: dict[str, float] | None = None,
    leeway_token: str | None = None,
) -> dict:
    """Return the latest verified price and its EUR equivalent."""
    clean_symbol = normalize_symbol(symbol)
    clean_currency = normalize_currency(quote_currency)
    if not clean_symbol or not clean_currency:
        raise ValueError("valid_market_position_required")
    try:
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
        provider = "twelve_data"
        resolved_symbol = clean_symbol
    except ValueError as twelve_error:
        if str(twelve_error) == "market_currency_mismatch":
            raise
        try:
            fallback = _request_leeway_quote(clean_symbol, clean_currency, leeway_token)
        except ValueError as fallback_error:
            if str(fallback_error) == "market_europe_provider_missing":
                raise twelve_error
            raise fallback_error
        native_price = fallback["native_price"]
        provider = fallback["provider"]
        resolved_symbol = fallback["symbol"]

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
        "resolved_symbol": resolved_symbol,
        "currency": clean_currency,
        "native_price": round(native_price, 8),
        "eur_price": round(native_price * fx_rate, 8),
        "provider": provider,
    }


def apply_market_quote(
    conn: sqlite3.Connection,
    holding_id: int,
    quote: dict,
    *,
    expected_symbol: str | None = None,
    reconcile_pending: bool = False,
    manage_transaction: bool = True,
) -> dict:
    """Apply one quote and adjust the user's aggregate by the delta only.

    Existing callers keep the helper-owned transaction. API callers that already
    hold a write transaction can opt out so metadata and valuation commit together.
    """
    ensure_market_tracking_schema(conn)
    ensure_investment_contribution_schema(conn)
    if manage_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """SELECT id, user_id, instrument_label, price_symbol, quantity,
                      total_invested, market_value, valuation_enabled
                 FROM portfolio_holdings WHERE id = ?""",
            (holding_id,),
        ).fetchone()
        if not row:
            raise ValueError("market_position_not_found")
        if expected_symbol and str(row["price_symbol"] or "").upper() != expected_symbol.upper():
            raise ValueError("market_position_changed")
        quantity = float(row["quantity"] or 0)
        if not row["valuation_enabled"] or quantity <= 0:
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
            raise ValueError("market_user_not_found")
        absorbed = 0.0
        if reconcile_pending and delta > 0:
            absorbed = reconcile_pending_contribution(
                conn, int(row["user_id"]), int(holding_id), delta
            )
            total_row = conn.execute(
                "SELECT current_investments FROM users WHERE user_id = ?", (row["user_id"],)
            ).fetchone()
        new_total = round(max(0.0, float(total_row["current_investments"] or 0) + delta), 2)
        valuation_delta = round(delta - absorbed, 2) if delta > 0 else delta

        conn.execute(
            """UPDATE portfolio_holdings
                  SET last_price = ?, market_value = ?, market_data_provider = ?,
                      last_checked_at = CURRENT_TIMESTAMP,
                      market_value_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?""",
            (quote["native_price"], market_value, quote.get("provider", "twelve_data"), holding_id),
        )
        conn.execute(
            "UPDATE users SET current_investments = ? WHERE user_id = ?",
            (new_total, row["user_id"]),
        )
        if abs(valuation_delta) >= 0.01:
            conn.execute(
                """INSERT INTO investment_events
                   (user_id, amount, direction, asset_type, asset_name, event_type,
                    source, note, holding_id)
                   VALUES (?, ?, ?, ?, ?, 'market_valuation', ?, ?, ?)""",
                (
                    row["user_id"], abs(valuation_delta),
                    "in" if valuation_delta > 0 else "out", "market",
                    row["instrument_label"], quote.get("provider", "twelve_data"),
                    f"Taegliche Kursbewertung {quote.get('resolved_symbol', quote['symbol'])} in EUR",
                    int(holding_id),
                ),
            )
        if manage_transaction:
            conn.commit()
        return {
            "market_value": market_value,
            "delta": valuation_delta,
            "pending_absorbed": absorbed,
            "investment_total": new_total,
        }
    except Exception:
        if manage_transaction:
            conn.rollback()
        raise


def refresh_all_market_positions(db_path: str | Path, api_key: str | None = None) -> dict:
    """Refresh all enabled positions; one bad symbol never blocks the remaining portfolio."""
    path = str(db_path)
    with sqlite3.connect(path, timeout=20) as conn:
        conn.row_factory = sqlite3.Row
        ensure_market_tracking_schema(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT id, price_symbol, quote_currency, instrument_type,
                      market_data_provider, provider_asset_id
                 FROM portfolio_holdings
                WHERE valuation_enabled = 1 AND quantity > 0 AND price_symbol IS NOT NULL"""
        ).fetchall()
    updated = 0
    failures: list[dict[str, str]] = []
    fx_cache: dict[str, float] = {}
    crypto_rows = [
        row for row in rows
        if str(row["instrument_type"] or "").lower() == "crypto"
        and str(row["market_data_provider"] or "").lower() == "coinmarketcap"
        and str(row["provider_asset_id"] or "").strip()
    ]
    crypto_quotes: dict[str, dict] = {}
    crypto_batch_failed = False
    if crypto_rows:
        try:
            crypto_quotes = fetch_crypto_eur_quotes(
                [row["provider_asset_id"] for row in crypto_rows]
            )
        except Exception as exc:
            crypto_batch_failed = True
            failures.extend({
                "symbol": str(row["price_symbol"] or ""),
                "error": str(exc) or exc.__class__.__name__,
            } for row in crypto_rows)
    for row in crypto_rows:
        quote = crypto_quotes.get(str(row["provider_asset_id"] or ""))
        if not quote:
            if not crypto_batch_failed:
                failures.append({"symbol": str(row["price_symbol"] or ""), "error": "crypto_asset_not_found"})
            continue
        try:
            with sqlite3.connect(path, timeout=20) as conn:
                conn.row_factory = sqlite3.Row
                apply_market_quote(conn, row["id"], quote, expected_symbol=row["price_symbol"])
            updated += 1
        except Exception as exc:
            failures.append({"symbol": str(row["price_symbol"] or ""), "error": str(exc) or exc.__class__.__name__})

    crypto_ids = {int(row["id"]) for row in crypto_rows}
    for row in rows:
        if int(row["id"]) in crypto_ids:
            continue
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
