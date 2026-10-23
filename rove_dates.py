"""Canonical calendar-date helpers for Rov.E's Germany-based financial periods."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE = ZoneInfo("Europe/Berlin")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_FIELDS = (
    "booking_date",
    "transaction_date",
    "occurred_on",
    "value_date",
    "effective_date",
    "date",
    "occurred_at",
    "transaction_at",
    "booking_at",
)
_MONTH_TABLES = frozenset({"expenses", "app_cash_movements"})


def business_today(now: datetime | None = None) -> date:
    """Return today's date in Berlin; naive instants are interpreted as UTC."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(BUSINESS_TIMEZONE).date()


def business_month_key(now: datetime | None = None) -> str:
    return business_today(now).strftime("%Y-%m")


def _value(row, key: str):
    try:
        if key not in row.keys():
            return None
        return row[key]
    except (AttributeError, KeyError, IndexError, TypeError):
        try:
            return row.get(key)
        except AttributeError:
            return None


def parse_business_date(value) -> date | None:
    """Parse a calendar date as-is, or convert an instant to its Berlin date.

    SQLite CURRENT_TIMESTAMP is UTC without an offset, so naive timestamps are
    treated as UTC. Date-only values deliberately never pass through a timezone.
    """
    if isinstance(value, datetime):
        instant = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return instant.astimezone(BUSINESS_TIMEZONE).date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if _DATE_ONLY.fullmatch(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    try:
        instant = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(BUSINESS_TIMEZONE).date()


def effective_business_date(row) -> date | None:
    """Prefer stored event-date fields; otherwise use created_at as last fallback."""
    for field in _DATE_FIELDS:
        parsed = parse_business_date(_value(row, field))
        if parsed is not None:
            return parsed

    created_at = _value(row, "created_at")
    description = str(_value(row, "description") or "")
    if description.startswith("Via Rov.E Screenshot ·"):
        # Screenshot rows already persist a validated bank calendar date in the
        # date prefix of created_at. Preserve that date instead of shifting it.
        date_prefix = str(created_at or "")[:10]
        if _DATE_ONLY.fullmatch(date_prefix):
            try:
                return date.fromisoformat(date_prefix)
            except ValueError:
                pass
    return parse_business_date(created_at)


def select_rows_for_business_month(
    conn: sqlite3.Connection,
    table: str,
    user_id: int,
    month_key: str,
    *,
    cutoff_date: str | date | None = None,
):
    """Select a small boundary window, then apply exact Berlin-date semantics."""
    if table not in _MONTH_TABLES:
        raise ValueError("unsupported_business_month_table")
    try:
        first_day = date.fromisoformat(f"{month_key}-01")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_month_key") from exc
    if first_day.strftime("%Y-%m") != month_key:
        raise ValueError("invalid_month_key")
    if first_day.month == 12:
        next_month = date(first_day.year + 1, 1, 1)
    else:
        next_month = date(first_day.year, first_day.month + 1, 1)
    lower = (first_day - timedelta(days=2)).isoformat()
    upper = (next_month + timedelta(days=2)).isoformat()
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    date_columns = [name for name in (*_DATE_FIELDS, "created_at") if name in columns]
    if not date_columns:
        return []
    candidate_clauses = [
        f"substr(CAST(COALESCE(\"{name}\", '') AS TEXT), 1, 10) >= ? "
        f"AND substr(CAST(COALESCE(\"{name}\", '') AS TEXT), 1, 10) < ?"
        for name in date_columns
    ]
    params: list[object] = [user_id]
    for _ in candidate_clauses:
        params.extend((lower, upper))
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE user_id=? AND ({' OR '.join(candidate_clauses)}) "
        "ORDER BY created_at DESC",
        tuple(params),
    ).fetchall()
    cutoff = parse_business_date(cutoff_date) if cutoff_date is not None else None
    result = []
    for row in rows:
        effective = effective_business_date(row)
        if not effective or effective.strftime("%Y-%m") != month_key:
            continue
        if cutoff is not None and effective > cutoff:
            continue
        result.append(row)
    return result
