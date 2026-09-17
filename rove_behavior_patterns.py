"""Deterministic behavioral observations for Coach V4 shadow mode.

This module is deliberately separate from Coach V3 prioritization. It only reads
canonical transactions and budgets and returns evidence objects. Nothing here
writes financial truth, emits notifications, or changes the active coach.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from typing import Any


DEFAULT_WINDOW_DAYS = 30
MIN_REPEATED_TRANSACTIONS = 3
MIN_WEEKDAY_OCCURRENCES = 4
MIN_RELEVANT_TOTAL_EUR = 30.0
LATE_NIGHT_START = time(20, 30)
LATE_NIGHT_END = time(0, 30)
RELIABLE_TIMESTAMP_COLUMNS = ("occurred_at", "transaction_at", "transacted_at", "booking_at")

NON_CONSUMPTION_MOVEMENTS = {
    "transfer",
    "withdrawal",
    "income",
    "fixed",
    "investment",
    "savings",
    "contribution",
}

ESSENTIAL_MERCHANT_HINTS = (
    "aldi",
    "lidl",
    "rewe",
    "edeka",
    "dm",
    "drogerie",
    "rossmann",
    "apotheke",
    "pharmacy",
)
TANK_MERCHANT_HINTS = (
    "tankstelle",
    "shell",
    "aral",
    "esso",
    "jet",
    "total",
    "star",
    "hem",
    "avia",
    "q1",
)
DISCRETIONARY_MERCHANT_HINTS = (
    "lieferando",
    "wolt",
    "uber eats",
    "amazon",
    "zalando",
    "netflix",
    "spotify",
    "disney",
    "prime",
)
ESSENTIAL_CATEGORY_HINTS = (
    "lebensmittel",
    "drogerie",
    "gesundheit",
    "apotheke",
    "medizin",
    "pflege",
)
DISCRETIONARY_CATEGORY_HINTS = (
    "restaurant",
    "liefer",
    "shopping",
    "mode",
    "freizeit",
    "entertainment",
    "stream",
    "abo",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def _timestamp_quality(value: object) -> str:
    """Classify timestamp precision without assuming timezone semantics."""
    raw = str(value or "").strip()
    if not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", raw):
        return "date_only"
    if re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", raw):
        return "timezone_aware"
    return "local_clock"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _contains_hint(value: object, hints: tuple[str, ...]) -> bool:
    text = _key(value)
    return any(hint in text for hint in hints)


def _merchant_class(merchant: str, category: str) -> str:
    if _contains_hint(merchant, TANK_MERCHANT_HINTS):
        return "tank"
    if _contains_hint(merchant, ESSENTIAL_MERCHANT_HINTS) or _contains_hint(
        category, ESSENTIAL_CATEGORY_HINTS
    ):
        return "essential"
    if _contains_hint(merchant, DISCRETIONARY_MERCHANT_HINTS) or _contains_hint(
        category, DISCRETIONARY_CATEGORY_HINTS
    ):
        return "discretionary"
    return "other"


def _stable_id(pattern_type: str, source_ids: list[str], period_key: str) -> str:
    raw = "|".join((pattern_type, period_key, *sorted(source_ids)))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"shadow:{pattern_type}:{digest}"


def _strength(count: int) -> str:
    if count >= 5:
        return "high"
    if count >= 3:
        return "medium"
    return "low"


def _pattern(
    *,
    pattern_type: str,
    items: list[dict[str, Any]],
    period_start: datetime,
    period_end: datetime,
    category: str | None,
    merchant: str | None,
    observations: dict[str, Any],
    pattern_strength: str,
    financial_relevance: str,
    relevance_reason: str,
    eligible_for_coach: bool,
    eligible_for_report: bool,
    stable_period_key: str | None = None,
    related_pattern_ids: list[str] | None = None,
) -> dict[str, Any]:
    source_ids = [str(item["source_id"]) for item in items]
    start = period_start.date().isoformat()
    end = period_end.date().isoformat()
    timestamps = [item["occurred_at"] for item in items if item["occurred_at"]]
    first_seen = min(timestamps) if timestamps else None
    last_seen = max(timestamps) if timestamps else None
    result = {
        "pattern_id": _stable_id(
            pattern_type,
            source_ids,
            stable_period_key or f"{start}:{end}",
        ),
        "pattern_type": pattern_type,
        "period_start": start,
        "period_end": end,
        "source_ids": sorted(source_ids),
        "category": category,
        "merchant": merchant,
        "observations": observations,
        "amount_total": round(sum(float(item["amount"]) for item in items), 2),
        "pattern_strength": pattern_strength,
        "financial_relevance": financial_relevance,
        "relevance_reason": relevance_reason,
        "eligible_for_coach": bool(eligible_for_coach),
        "eligible_for_report": bool(eligible_for_report),
        "related_pattern_ids": sorted(related_pattern_ids or []),
        "superseded_by_pattern_id": None,
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
    }
    return result


def _load_consumption_items(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    period_start: datetime,
    period_end: datetime,
) -> list[dict[str, Any]]:
    columns = _columns(conn, "expenses")
    required = {"id", "user_id", "amount"}
    if not required.issubset(columns):
        return []

    def field(name: str, fallback: str = "NULL") -> str:
        return name if name in columns else f"{fallback} AS {name}"

    reliable_time_column = next(
        (name for name in RELIABLE_TIMESTAMP_COLUMNS if name in columns),
        None,
    )
    if reliable_time_column and "created_at" in columns:
        event_time = f"COALESCE({reliable_time_column}, created_at) AS event_time"
    elif reliable_time_column:
        event_time = f"{reliable_time_column} AS event_time"
    else:
        event_time = f"{field('created_at')} AS event_time"
    reliable_event_time = (
        f"{reliable_time_column} AS reliable_event_time"
        if reliable_time_column
        else "NULL AS reliable_event_time"
    )
    query = f"""SELECT id, amount, {field('category', "''")},
                       {field('merchant', "''")}, {field('description', "''")},
                       {event_time}, {reliable_event_time}
                  FROM expenses
                 WHERE user_id=?"""
    rows = conn.execute(query, (user_id,)).fetchall()

    movement_by_expense: dict[int, str] = {}
    movement_columns = _columns(conn, "app_cash_movements")
    if {"expense_id", "kind", "user_id"}.issubset(movement_columns):
        for movement in conn.execute(
            """SELECT expense_id, kind FROM app_cash_movements
                WHERE user_id=? AND expense_id IS NOT NULL
                ORDER BY id""",
            (user_id,),
        ).fetchall():
            movement_by_expense[int(movement["expense_id"])] = str(
                movement["kind"] or ""
            ).strip().casefold()

    items: list[dict[str, Any]] = []
    for row in rows:
        occurred_at = _parse_timestamp(row["event_time"])
        if occurred_at is None or not (period_start <= occurred_at <= period_end):
            continue
        movement_kind = movement_by_expense.get(int(row["id"]), "")
        if movement_kind in NON_CONSUMPTION_MOVEMENTS:
            continue
        amount = float(row["amount"] or 0)
        if amount <= 0:
            continue
        category = str(row["category"] or "").strip()
        merchant = str(row["merchant"] or "").strip()
        description = str(row["description"] or "").strip()
        if _contains_hint(f"{merchant} {description}", ("refund", "erstattung", "storno", "gutschrift")):
            continue
        items.append({
            "source_id": str(row["id"]),
            "amount": round(amount, 2),
            "category": category or "Unbekannt",
            "category_key": _key(category),
            "merchant": merchant,
            "merchant_key": _key(merchant),
            "description": description,
            "occurred_at": occurred_at,
            "calendar_time_reliable": bool(row["reliable_event_time"])
            and _timestamp_quality(row["reliable_event_time"]) in {"date_only", "local_clock"},
            "clock_time_reliable": bool(row["reliable_event_time"])
            and _timestamp_quality(row["reliable_event_time"]) == "local_clock",
        })
    return items


def _category_budget_pressures(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    columns = _columns(conn, "category_budgets")
    if not {"user_id", "category", "monthly_limit"}.issubset(columns):
        return []
    month_key = now.strftime("%Y-%m")
    query = """SELECT category, monthly_limit
                 FROM category_budgets
                WHERE user_id=?"""
    params: list[Any] = [user_id]
    if "active_month" in columns:
        query += " AND active_month=?"
        params.append(month_key)
    budgets = conn.execute(query, tuple(params)).fetchall()
    days_in_month = monthrange(now.year, now.month)[1]
    elapsed_fraction = now.day / days_in_month
    pressures: list[dict[str, Any]] = []
    for budget in budgets:
        category = str(budget["category"] or "").strip() or "Unbekannt"
        category_key = _key(category)
        limit = float(budget["monthly_limit"] or 0)
        if limit <= 0:
            continue
        category_items = [
            item for item in items
            if item["category_key"] == category_key
            and item["occurred_at"].strftime("%Y-%m") == month_key
        ]
        spent = round(sum(float(item["amount"]) for item in category_items), 2)
        used_fraction = spent / limit
        if used_fraction < 0.75 or used_fraction <= elapsed_fraction + 0.10:
            continue
        pressure_gap = used_fraction - elapsed_fraction
        pressure = _pattern(
            pattern_type="category_budget_pressure",
            items=category_items,
            period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            period_end=now,
            category=category,
            merchant=None,
            observations={
                "month_elapsed_fraction": round(elapsed_fraction, 3),
                "budget_used_fraction": round(used_fraction, 3),
                "monthly_limit": round(limit, 2),
                "amount_spent": spent,
            },
            pattern_strength="high" if pressure_gap >= 0.25 else "medium",
            financial_relevance="high",
            relevance_reason="Kategorie-Budget ist gemessen am Monatsfortschritt deutlich unter Druck.",
            eligible_for_coach=True,
            eligible_for_report=True,
            stable_period_key=month_key,
        )
        pressures.append(pressure)
    return pressures


def detect_behavior_patterns(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Return shadow-only evidence objects from canonical transaction data."""
    now = (now or datetime.now()).replace(tzinfo=None)
    period_start = now - timedelta(days=max(1, window_days) - 1)
    stable_window_key = f"rolling-{max(1, window_days)}d"
    items = _load_consumption_items(
        conn,
        user_id,
        period_start=period_start,
        period_end=now,
    )
    patterns: list[dict[str, Any]] = []

    by_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item["merchant_key"] and _merchant_class(item["merchant"], item["category"]) != "tank":
            by_merchant[item["merchant_key"]].append(item)

    for merchant_items in by_merchant.values():
        if len(merchant_items) < 2:
            continue
        merchant = Counter(item["merchant"] for item in merchant_items).most_common(1)[0][0]
        category = Counter(item["category"] for item in merchant_items).most_common(1)[0][0]
        kind = _merchant_class(merchant, category)
        if kind == "tank":
            continue
        if kind == "essential":
            reason = "Grundbedarf: Haeufigkeit allein ist kein Coach- oder Report-Grund."
            relevance = "low"
        else:
            reason = "Reine Haeufigkeitsbeobachtung; ohne weitere Evidenz nicht ausspielen."
            relevance = "low"
        patterns.append(_pattern(
            pattern_type="merchant_frequency",
            items=merchant_items,
            period_start=period_start,
            period_end=now,
            category=category,
            merchant=merchant,
            observations={
                "merchant": merchant,
                "transaction_count": len(merchant_items),
                "period": f"{period_start.date().isoformat()}..{now.date().isoformat()}",
            },
            pattern_strength=_strength(len(merchant_items)),
            financial_relevance=relevance,
            relevance_reason=reason,
            eligible_for_coach=False,
            eligible_for_report=False,
            stable_period_key=stable_window_key,
        ))

    by_weekday: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if _merchant_class(item["merchant"], item["category"]) == "discretionary":
            by_weekday[(item["merchant_key"], item["occurred_at"].weekday())].append(item)
    for (merchant_key, weekday), weekday_items in by_weekday.items():
        opportunities = sum(
            1 for offset in range((now.date() - period_start.date()).days + 1)
            if (period_start + timedelta(days=offset)).weekday() == weekday
        )
        occurrence_dates = {item["occurred_at"].date() for item in weekday_items}
        if len(occurrence_dates) < MIN_REPEATED_TRANSACTIONS or opportunities < MIN_WEEKDAY_OCCURRENCES:
            continue
        ratio = len(occurrence_dates) / opportunities
        if ratio < 0.60:
            continue
        merchant = weekday_items[0]["merchant"]
        category = Counter(item["category"] for item in weekday_items).most_common(1)[0][0]
        relevant = sum(float(item["amount"]) for item in weekday_items) >= MIN_RELEVANT_TOTAL_EUR
        patterns.append(_pattern(
            pattern_type="merchant_weekday_pattern",
            items=weekday_items,
            period_start=period_start,
            period_end=now,
            category=category,
            merchant=merchant,
            observations={
                "merchant": merchant,
                "weekday": weekday,
                "transaction_count": len(weekday_items),
                "occurrence_date_count": len(occurrence_dates),
                "weekday_opportunities": opportunities,
                "occurrence_ratio": round(ratio, 3),
            },
            pattern_strength=_strength(len(weekday_items)),
            financial_relevance="medium" if relevant else "low",
            relevance_reason="Wiederholung an einem Wochentag ist nur bei ausreichender Evidenz belastbar.",
            eligible_for_coach=relevant and all(
                item.get("calendar_time_reliable", False) for item in weekday_items
            ),
            eligible_for_report=False,
            stable_period_key=stable_window_key,
        ))

    late_items = [
        item for item in items
        if _merchant_class(item["merchant"], item["category"]) == "discretionary"
        and item.get("clock_time_reliable", False)
        and (
            item["occurred_at"].time() >= LATE_NIGHT_START
            or item["occurred_at"].time() <= LATE_NIGHT_END
        )
    ]
    by_late_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in late_items:
        if item["merchant_key"]:
            by_late_merchant[item["merchant_key"]].append(item)
    for late_merchant_items in by_late_merchant.values():
        if len(late_merchant_items) < MIN_REPEATED_TRANSACTIONS:
            continue
        merchant = Counter(item["merchant"] for item in late_merchant_items).most_common(1)[0][0]
        category = Counter(item["category"] for item in late_merchant_items).most_common(1)[0][0]
        relevant = sum(float(item["amount"]) for item in late_merchant_items) >= MIN_RELEVANT_TOTAL_EUR
        patterns.append(_pattern(
            pattern_type="late_night_discretionary_spending",
            items=late_merchant_items,
            period_start=period_start,
            period_end=now,
            category=category,
            merchant=merchant,
            observations={
                "merchant": merchant,
                "transaction_count": len(late_merchant_items),
                "time_window": "20:30-00:30",
            },
            pattern_strength=_strength(len(late_merchant_items)),
            financial_relevance="medium" if relevant else "low",
            relevance_reason="Mehrere diskretionaere Buchungen liegen im definierten spaeten Zeitfenster.",
            eligible_for_coach=relevant,
            eligible_for_report=False,
            stable_period_key=stable_window_key,
        ))

    pressures = _category_budget_pressures(conn, user_id, now=now, items=items)
    patterns.extend(pressures)

    items_by_id = {item["source_id"]: item for item in items}
    behavior_patterns = [
        pattern for pattern in patterns
        if pattern["pattern_type"] in {
            "merchant_weekday_pattern",
            "late_night_discretionary_spending",
        } and pattern["eligible_for_coach"]
    ]
    for pressure in pressures:
        matching = [
            behavior for behavior in behavior_patterns
            if _key(behavior.get("category")) == _key(pressure.get("category"))
        ]
        if not matching:
            continue
        behavior = max(
            matching,
            key=lambda pattern: (
                pattern["pattern_strength"] == "high",
                pattern["amount_total"],
                pattern["pattern_type"],
            ),
        )
        behavior_category = _key(behavior.get("category"))
        if behavior_category != _key(pressure.get("category")):
            continue
        combined_ids = sorted(set(behavior["source_ids"]) | set(pressure["source_ids"]))
        combined_items = [items_by_id[source_id] for source_id in combined_ids if source_id in items_by_id]
        combined = _pattern(
            pattern_type="behavior_plus_budget_pressure",
            items=combined_items,
            period_start=period_start,
            period_end=now,
            category=pressure["category"],
            merchant=behavior.get("merchant"),
            observations={
                "behavior_pattern_id": behavior["pattern_id"],
                "budget_pattern_id": pressure["pattern_id"],
                "behavior_type": behavior["pattern_type"],
                "budget_used_fraction": pressure["observations"]["budget_used_fraction"],
            },
            pattern_strength="high",
            financial_relevance="high",
            relevance_reason="Belastbares Verhaltensmuster trifft auf konkreten Kategorie-Budgetdruck.",
            eligible_for_coach=True,
            eligible_for_report=True,
            stable_period_key=f"{stable_window_key}|{now.strftime('%Y-%m')}",
            related_pattern_ids=[behavior["pattern_id"], pressure["pattern_id"]],
        )
        behavior["eligible_for_coach"] = False
        behavior["eligible_for_report"] = False
        behavior["superseded_by_pattern_id"] = combined["pattern_id"]
        pressure["eligible_for_coach"] = False
        pressure["eligible_for_report"] = False
        pressure["superseded_by_pattern_id"] = combined["pattern_id"]
        patterns.append(combined)

    return sorted(patterns, key=lambda pattern: (
        pattern["period_start"],
        pattern["pattern_type"],
        pattern["merchant"] or "",
        pattern["pattern_id"],
    ))


def build_shadow_inspector(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Small read-only inspector payload for authorized internal tooling."""
    return {
        "mode": "shadow",
        "coach_v3_affected": False,
        "patterns": detect_behavior_patterns(conn, user_id, now=now),
    }
