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

from rove_expense_domain import (
    NON_CONSUMPTION_MOVEMENTS,
    canonical_expense_movement_kinds,
)


DEFAULT_WINDOW_DAYS = 30
HISTORICAL_MONTHS = 6
MIN_HISTORICAL_MONTHS = 3
MIN_OVER_BUDGET_MONTHS = 3
MIN_MAJOR_CATEGORY_OVERAGE_PERCENT = 0.50
MAX_COMPARABLE_BUDGET_CHANGE = 0.10
MIN_TREND_DELTA_EUR = 30.0
MIN_TREND_RELATIVE_CHANGE = 0.25
MAX_SINGLE_EXPENSE_SHARE = 0.75
MIN_REPEATED_TRANSACTIONS = 3
MIN_WEEKDAY_OCCURRENCES = 4
MIN_RELEVANT_TOTAL_EUR = 30.0
SALARY_HISTORY_MONTHS = 6
MIN_SALARY_CYCLES = 3
MIN_SALARY_GAP_DAYS = 20
MAX_SALARY_GAP_DAYS = 45
SALARY_WINDOW_DAYS = (3, 7)
MIN_POST_INCOME_DELTA_EUR = 30.0
MIN_POST_INCOME_RELATIVE_CHANGE = 0.25
MIN_BASELINE_DATES = 3
MIN_POST_INCOME_ITEMS_PER_CYCLE = 2
SIMILAR_SERVICE_MIN_COUNT = 2
SIMILAR_SERVICE_MEDIUM_COUNT = 3
SIMILAR_SERVICE_HIGH_COUNT = 5
SIMILAR_SERVICE_MEDIUM_EUR = 40.0
SIMILAR_SERVICE_HIGH_EUR = 80.0
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
SALARY_LABEL_HINTS = (
    "gehalt",
    "salary",
    "lohn",
    "payroll",
    "arbeitsentgelt",
    "nettolohn",
)
NON_SALARY_INCOME_HINTS = (
    "13. gehalt",
    "13gehalt",
    "weihnachtsgeld",
    "urlaubsgeld",
    "jahresbonus",
    "bonus",
    "praemie",
    "sonderzahlung",
    "gratifikation",
    "einmalzahlung",
    "tantieme",
    "dividend",
    "kredit",
    "darlehen",
    "loan",
    "refund",
    "erstattung",
    "ruckerstattung",
    "rueckerstattung",
    "gutschrift",
    "dividende",
    "transfer",
    "umbuch",
    "investment",
    "verkauf",
)
AMBIGUOUS_SALARY_SOURCE_HINTS = (
    "nebenjob",
    "minijob",
    "partner",
    "privat",
    "familie",
    "freund",
    "unterhalt",
    "kindergeld",
    "rente",
    "pension",
    "miete",
    "mietzahlung",
    "rental",
    "rent",
    "vermietung",
)

SERVICE_CLASS_HINTS = {
    "streaming_video": (
        "netflix", "disney+", "disney plus", "paramount", "prime video",
        "max streaming", "sky stream", "sky cinema", "sky entertainment",
        "wow tv", "rtl+", "joyn",
    ),
    "streaming_music": (
        "spotify", "apple music", "tidal", "deezer", "youtube music",
    ),
    "sport_streaming": (
        "dazn", "sky sport", "eurosport player",
    ),
    "fitness": (
        "fitnessstudio", "fitness studio", "mcfit", "clever fit", "gym",
        "urban sports", "classpass",
    ),
    "cloud_software": (
        "icloud", "dropbox", "google one", "microsoft 365", "office 365",
        "adobe creative cloud", "notion", "canva pro",
    ),
    "news_media": (
        "zeit", "spiegel", "faz", "new york times", "nyt", "newspaper",
    ),
    "gaming": (
        "xbox game pass", "playstation plus", "nintendo online", "game pass",
    ),
}
SERVICE_STATUS_ACTIVE = {"active", "enabled", "current", "laufend"}
SERVICE_STATUS_INACTIVE = {
    "cancelled", "canceled", "inactive", "ended", "paused", "deleted",
    "terminated", "beendet", "gekuendigt", "pausiert",
}
SERVICE_FREQUENCY_MONTHLY = {"month", "monthly", "monat", "monatlich", "m"}
SERVICE_FREQUENCY_ANNUAL = {
    "annual", "annually", "year", "yearly", "jahr", "jaehrlich", "y", "12m",
}


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


def _salary_label_key(value: object) -> str:
    """Normalize salary labels without weakening the general text key."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(str.maketrans({
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    }))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _salary_contains_hint(value: object, hints: tuple[str, ...]) -> bool:
    text = _salary_label_key(value)
    compact = text.replace(" ", "")
    return any(
        _salary_label_key(hint) in text
        or _salary_label_key(hint).replace(" ", "") in compact
        for hint in hints
    )


def _category_kind(items: list[dict[str, Any]], category: str) -> str:
    """Keep merchant-level essential classifications in category aggregates."""
    kinds = {
        _merchant_class(str(item.get("merchant") or ""), category)
        for item in items
    }
    if "essential" in kinds or "tank" in kinds:
        return "essential"
    return _merchant_class("", category)


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


def _shift_month(value: datetime, offset: int) -> datetime:
    month_index = value.year * 12 + (value.month - 1) + offset
    year, month_index = divmod(month_index, 12)
    return value.replace(year=year, month=month_index + 1, day=1)


def _completed_month_keys(now: datetime, count: int = HISTORICAL_MONTHS) -> list[str]:
    current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return [
        _shift_month(current_month, offset).strftime("%Y-%m")
        for offset in range(-max(1, count), 0)
    ]


def _month_end(month_key: str) -> datetime:
    year, month = (int(part) for part in month_key.split("-"))
    return datetime(year, month, monthrange(year, month)[1], 23, 59, 59)


def _historical_budgets(
    conn: sqlite3.Connection,
    user_id: int,
    month_keys: list[str],
) -> dict[tuple[str, str], float]:
    columns = _columns(conn, "category_budgets")
    if not {"user_id", "category", "monthly_limit", "active_month"}.issubset(columns):
        return {}
    placeholders = ",".join("?" for _ in month_keys)
    rows = conn.execute(
        f"""SELECT category, monthly_limit, active_month,
                          {"created_at" if "created_at" in columns else "NULL AS created_at"}
                FROM category_budgets
               WHERE user_id=? AND active_month IN ({placeholders})""",
        (user_id, *month_keys),
    ).fetchall()
    row_counts = Counter(
        (_key(row["category"]), str(row["active_month"]))
        for row in rows
    )
    budgets: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (_key(row["category"]), str(row["active_month"]))
        # Multiple unversioned rows for the same month do not tell us which
        # limit applied when. Do not reconstruct a historical budget timeline.
        if row_counts[key] > 1:
            continue
        limit = float(row["monthly_limit"] or 0)
        if limit <= 0:
            continue
        created_at = _parse_timestamp(row["created_at"])
        month_start = datetime.fromisoformat(f"{key[1]}-01 00:00:00")
        if created_at and created_at > month_start:
            # A creation/update timestamp after month start only proves a
            # partial-month budget; applying it to the full month is unsafe.
            continue
        budgets[key] = round(limit, 2)
    return budgets


def _historical_category_items(
    items: list[dict[str, Any]],
    month_keys: list[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    allowed = set(month_keys)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        month_key = item["occurred_at"].strftime("%Y-%m")
        if month_key in allowed:
            grouped[(item["category_key"], month_key)].append(item)
    return grouped


def _month_quality(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(float(item["amount"]) for item in items)
    largest = max((float(item["amount"]) for item in items), default=0.0)
    share = largest / total if total else 0.0
    return {
        "transaction_count": len(items),
        "max_single_expense_share": round(share, 3),
        "single_expense_dominated": bool(items) and share >= MAX_SINGLE_EXPENSE_SHARE,
    }


def _trend_direction(values: list[float]) -> str | None:
    if len(values) < MIN_HISTORICAL_MONTHS:
        return None
    if all(left < right for left, right in zip(values, values[1:])):
        return "worsening"
    if all(left > right for left, right in zip(values, values[1:])):
        return "improving"
    return None


def _historical_pattern(
    *,
    pattern_type: str,
    items: list[dict[str, Any]],
    month_keys: list[str],
    category: str,
    observations: dict[str, Any],
    direction: str,
    pattern_strength: str,
    financial_relevance: str,
    relevance_reason: str,
    eligible_for_coach: bool,
    eligible_for_report: bool,
    related_pattern_ids: list[str] | None = None,
    period_end_override: datetime | None = None,
) -> dict[str, Any]:
    result = _pattern(
        pattern_type=pattern_type,
        items=items,
        period_start=datetime.fromisoformat(f"{month_keys[0]}-01 00:00:00"),
        period_end=period_end_override or _month_end(month_keys[-1]),
        category=category,
        merchant=None,
        observations=observations,
        pattern_strength=pattern_strength,
        financial_relevance=financial_relevance,
        relevance_reason=relevance_reason,
        eligible_for_coach=eligible_for_coach,
        eligible_for_report=eligible_for_report,
        stable_period_key=f"completed:{','.join(month_keys)}",
        related_pattern_ids=related_pattern_ids,
    )
    result["direction"] = direction
    return result


def _mark_composite(
    pattern: dict[str, Any],
    *,
    composite_type: str,
    related_pattern_ids: list[str],
    direction: str,
    evidence_summary: str,
    conflicting_evidence: bool = False,
) -> dict[str, Any]:
    """Annotate an existing evidence object as a fused shadow signal."""
    related = sorted(set(related_pattern_ids))
    pattern.update({
        "pattern_kind": "composite_behavior_pattern",
        "composite_type": composite_type,
        "related_pattern_ids": related,
        "direction": direction,
        "evidence_summary": evidence_summary,
        "conflicting_evidence": bool(conflicting_evidence),
        "supersedes": related,
    })
    return pattern


def _positive_composite(
    pattern: dict[str, Any],
    *,
    composite_type: str,
    evidence_summary: str,
    relevance_reason: str,
    observations_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a positive shadow object without changing source evidence."""
    source_ids = list(pattern["source_ids"])
    period_key = f"{composite_type}:{pattern['period_start']}:{pattern['period_end']}"
    result = dict(pattern)
    result["observations"] = {
        **dict(pattern.get("observations") or {}),
        **(observations_update or {}),
    }
    result["pattern_id"] = _stable_id(
        "composite_behavior_pattern", source_ids, period_key,
    )
    result["pattern_type"] = "composite_behavior_pattern"
    result["pattern_kind"] = "composite_behavior_pattern"
    result["composite_type"] = composite_type
    result["related_pattern_ids"] = [pattern["pattern_id"]]
    result["supersedes"] = []
    result["superseded_by_pattern_id"] = None
    result["direction"] = "improving"
    result["evidence_summary"] = evidence_summary
    result["conflicting_evidence"] = False
    result["relevance_reason"] = relevance_reason
    return result


def _date_range_overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = left.get("period_start")
    left_end = left.get("period_end")
    right_start = right.get("period_start")
    right_end = right.get("period_end")
    if not all((left_start, left_end, right_start, right_end)):
        return False
    return left_start <= right_end and right_start <= left_end


def _composites_share_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_category = _key(left.get("category"))
    right_category = _key(right.get("category"))
    if not left_category or left_category != right_category:
        return False
    left_merchant = _key(left.get("merchant"))
    right_merchant = _key(right.get("merchant"))
    if left_merchant and right_merchant and left_merchant != right_merchant:
        return False
    left_ids = set(left.get("related_pattern_ids") or [])
    left_ids.add(left.get("pattern_id"))
    right_ids = set(right.get("related_pattern_ids") or [])
    right_ids.add(right.get("pattern_id"))
    return bool(left_ids & right_ids) or _date_range_overlaps(left, right)


def _composite_relevance_rank(pattern: dict[str, Any]) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(
        pattern.get("financial_relevance"),
        0,
    )


def _composite_specificity_rank(pattern: dict[str, Any]) -> int:
    return int(pattern.get("composite_type") in {
        "behavior_driving_budget_pressure",
        "post_income_behavior_with_budget_pressure",
    })


def _budgets_are_comparable(
    historical_limits: list[float],
    current_limit: float,
) -> bool:
    limits = [*historical_limits, current_limit]
    if not historical_limits or any(limit <= 0 for limit in limits):
        return False
    reference = max(limits)
    return all(
        abs(limit - reference) / reference <= MAX_COMPARABLE_BUDGET_CHANGE
        for limit in limits
    )


def _arbitrate_composites(patterns: list[dict[str, Any]]) -> None:
    """Resolve overlapping shadow composites without changing raw evidence."""
    composites = [
        pattern for pattern in patterns
        if pattern.get("pattern_kind") == "composite_behavior_pattern"
    ]
    for pattern in composites:
        pattern["primary_composite"] = False

    groups: list[list[dict[str, Any]]] = []
    for pattern in composites:
        matching = [group for group in groups if any(
            _composites_share_scope(pattern, other) for other in group
        )]
        if not matching:
            groups.append([pattern])
            continue
        target = matching[0]
        target.append(pattern)
        for other in matching[1:]:
            target.extend(other)
            groups.remove(other)

    for group in groups:
        directions = {pattern.get("direction") for pattern in group}
        conflict = "worsening" in directions and "improving" in directions
        if conflict:
            for pattern in group:
                pattern["conflicting_evidence"] = True
                pattern["conflict_reason"] = "opposite_directions_same_scope"
                pattern["primary_composite"] = False
                pattern["eligible_for_coach"] = False
                pattern["eligible_for_report"] = False
            continue

        reliable = [pattern for pattern in group if pattern.get("eligible_for_coach")]
        if not reliable:
            continue
        winner = max(
            reliable,
            key=lambda pattern: (
                _composite_relevance_rank(pattern),
                _composite_specificity_rank(pattern),
                pattern.get("pattern_strength") == "high",
                pattern.get("pattern_id") or "",
            ),
        )
        winner["primary_composite"] = True
        superseded = []
        for pattern in group:
            if pattern is winner:
                continue
            pattern["eligible_for_coach"] = False
            pattern["eligible_for_report"] = False
            pattern["superseded_by_pattern_id"] = winner["pattern_id"]
            superseded.append(pattern["pattern_id"])
        winner["supersedes"] = sorted(set(winner.get("supersedes") or []) | set(superseded))


def _insight_type_for_pattern(pattern: dict[str, Any]) -> str | None:
    pattern_type = pattern.get("pattern_type")
    composite_type = pattern.get("composite_type")
    if composite_type == "budget_recovery":
        return "budget_recovery"
    if composite_type in {
        "repeated_discretionary_overspend",
        "repeated_discretionary_budget_pressure",
        "behavior_driving_budget_pressure",
    }:
        return "budget_attention"
    if composite_type == "post_income_behavior_with_budget_pressure":
        return "post_income_pattern"
    if pattern_type in {
        "category_budget_pressure",
        "category_repeated_over_budget",
    }:
        return "budget_attention"
    if pattern_type in {
        "merchant_frequency",
        "merchant_weekday_pattern",
        "late_night_discretionary_spending",
    }:
        return "repeated_spending_pattern"
    if pattern_type == "category_spending_worsening":
        return "spending_trend_worsening"
    if pattern_type == "category_spending_improving":
        return "spending_trend_improving"
    if pattern_type.startswith("post_income_"):
        return "post_income_pattern"
    if pattern_type == "similar_recurring_services":
        return "subscription_cluster"
    if pattern_type == "behavior_change_vs_personal_baseline":
        return (
            "spending_trend_worsening"
            if pattern.get("direction") == "worsening"
            else "spending_trend_improving"
        )
    return None


def _insight_metrics(
    pattern: dict[str, Any],
    insight_type: str,
) -> dict[str, Any]:
    observations = pattern.get("observations") or {}
    keys_by_type = {
        "budget_attention": (
            "amount_total", "amount_spent", "monthly_limit", "amount_over",
            "budget_used_fraction", "months_considered", "months_over_budget",
            "average_over_budget_percent", "total_deviation_eur",
            "monthly_values", "historical_budget_evidence",
            "overall_pressure_months", "historical_overall_budget_status",
            "overall_budget_status", "overall_variable_budget_eur",
            "overall_variable_spend_eur", "overall_free_remaining_eur",
            "month_elapsed_fraction",
            "overall_context_known_and_healthy", "category_deviation_is_large",
            "behavior_pattern_id", "budget_pattern_id", "behavior_type",
            "behavior_amount_total", "behavior_transaction_count",
            "behavior_occurrence_date_count", "budget_amount_spent",
            "budget_monthly_limit", "budget_amount_over",
        ),
        "repeated_spending_pattern": (
            "amount_total", "transaction_count", "occurrence_date_count",
            "weekday_opportunities", "occurrence_ratio", "time_window",
        ),
        "spending_trend_worsening": (
            "months_compared", "monthly_amounts", "absolute_change_eur",
            "relative_change", "baseline_months", "baseline_amounts",
            "baseline_average_eur", "current_month", "current_amount_eur",
            "difference_eur", "fair_baseline_eur", "comparison_mode",
        ),
        "spending_trend_improving": (
            "months_compared", "monthly_amounts", "absolute_change_eur",
            "relative_change", "baseline_months", "baseline_amounts",
            "baseline_average_eur", "current_month", "current_amount_eur",
            "difference_eur", "fair_baseline_eur", "comparison_mode",
        ),
        "post_income_pattern": (
            "amount_total",
            "salary_cycles", "window_days", "available_windows",
            "post_income_average_eur", "personal_baseline_average_eur",
            "absolute_delta_eur", "relative_delta", "temporal_correlation_only",
            "budget_pressure_months", "budget_status_by_month",
        ),
        "subscription_cluster": (
            "amount_total",
            "cluster_type", "service_count", "monthly_normalized_total",
            "frequency_complete", "activity_confidence", "duplicate_ambiguous",
            "data_quality",
        ),
        "budget_recovery": (
            "amount_total",
            "budget_context",
            "months_over_budget", "historical_budget_evidence",
        ),
    }
    metrics = {
        key: observations[key]
        for key in keys_by_type.get(insight_type, ())
        if key in observations
    }
    if "amount_total" in keys_by_type.get(insight_type, ()) and "amount_total" in pattern:
        metrics["amount_total"] = pattern["amount_total"]
    return metrics


def _insight_suppression_reason(
    pattern: dict[str, Any],
    insight_type: str,
) -> str | None:
    observations = pattern.get("observations") or {}
    if pattern.get("conflicting_evidence"):
        return "conflicting_evidence"
    if pattern.get("superseded_by_pattern_id"):
        return "superseded"
    if observations.get("activity_confidence") not in (None, "verified"):
        return "uncertain_activity_status"
    if observations.get("uncertain_event_time"):
        return "uncertain_event_time"
    if observations.get("hard_quality_exclusion"):
        return str(observations["hard_quality_exclusion"])
    if observations.get("single_expense_dominated"):
        return "single_outlier"
    if (
        insight_type == "budget_attention"
        and pattern.get("pattern_type") == "category_budget_pressure"
    ):
        overall_status = observations.get("overall_budget_status")
        if overall_status == "healthy":
            return "healthy_overall_budget"
        if overall_status != "under_pressure":
            return "overall_budget_status_unknown"
    if _merchant_class("", str(pattern.get("category") or "")) == "essential":
        return "essential_spending"
    if (
        insight_type == "budget_attention"
        and observations.get("overall_context_known_and_healthy")
        and not observations.get("overall_pressure_months")
        and not observations.get("category_deviation_is_large")
    ):
        return "healthy_overall_budget"
    if pattern.get("financial_relevance") == "low":
        return "low_financial_relevance"
    if not pattern.get("eligible_for_coach") and not pattern.get("eligible_for_report"):
        return "insufficient_history"
    return None


def _insight_report_eligible(
    pattern: dict[str, Any],
    insight_type: str,
    suppression_reason: str | None,
) -> bool:
    if suppression_reason:
        return False
    if insight_type == "budget_attention":
        if pattern.get("pattern_type") == "category_budget_pressure":
            return False
        return bool(pattern.get("eligible_for_report"))
    if insight_type in {"spending_trend_worsening", "spending_trend_improving"}:
        observations = pattern.get("observations") or {}
        return bool(
            pattern.get("eligible_for_report")
            and observations.get("months_compared", 0) >= MIN_HISTORICAL_MONTHS
        )
    if insight_type == "repeated_spending_pattern":
        observations = pattern.get("observations") or {}
        return bool(
            pattern.get("pattern_type") == "merchant_weekday_pattern"
            and pattern.get("eligible_for_coach")
            and observations.get("occurrence_date_count", 0) >= MIN_REPEATED_TRANSACTIONS
        )
    if insight_type == "post_income_pattern":
        return bool(
            pattern.get("eligible_for_report")
            and (pattern.get("observations") or {}).get("salary_cycles", 0) >= MIN_SALARY_CYCLES
        )
    if insight_type == "subscription_cluster":
        return bool(
            pattern.get("eligible_for_report")
            and (pattern.get("observations") or {}).get("activity_confidence") == "verified"
        )
    return bool(pattern.get("eligible_for_report"))


def _suppressed_salary_time_insight(
    context: dict[str, Any],
) -> dict[str, Any] | None:
    source_ids = [str(source_id) for source_id in context.get(
        "unreliable_salary_source_ids", []
    )]
    if not source_ids:
        return None
    check_id = _stable_id(
        "salary_event_time_check",
        source_ids,
        "unreliable-event-time",
    )
    source_count = len(source_ids)
    return {
        "candidate_type": "behavior_insight_candidate",
        "insight_id": check_id,
        "insight_type": "post_income_pattern",
        "source_pattern_ids": [check_id],
        "primary_pattern_id": check_id,
        "period_start": None,
        "period_end": None,
        "category": None,
        "merchant": None,
        "direction": None,
        "evidence_summary": {
            "pattern_type": "post_income_event_time_check",
            "source": "app_cash_movements",
            "event_time_reliable": False,
            "source_count": source_count,
        },
        "evidence_metrics": {
            "event_time_reliable": False,
            "source_count": source_count,
        },
        "pattern_strength": "low",
        "financial_relevance": "low",
        "confidence_class": "unknown",
        "coach_eligible": False,
        "report_eligible": False,
        "coach_timing_hint": None,
        "report_section_hint": "behavior_summary",
        "suppression_reason": "uncertain_event_time",
        "conflicting_evidence": False,
        "superseded_by_pattern_id": None,
    }


def _insight_timing_hint(pattern: dict[str, Any], insight_type: str) -> str | None:
    if insight_type == "post_income_pattern":
        return "post_salary_window"
    if pattern.get("pattern_type") == "merchant_weekday_pattern":
        return "weekend_prevention"
    if insight_type == "budget_recovery":
        return "month_end"
    if pattern.get("pattern_type") == "category_budget_pressure":
        return "immediate"
    if insight_type in {"budget_attention", "spending_trend_worsening"}:
        return "next_checkin"
    return "next_checkin" if insight_type else None


def _insight_report_section(insight_type: str) -> str:
    return {
        "budget_attention": "monthly_attention",
        "repeated_spending_pattern": "spending_patterns",
        "spending_trend_worsening": "behavior_summary",
        "spending_trend_improving": "positive_progress",
        "post_income_pattern": "behavior_summary",
        "subscription_cluster": "recurring_costs",
        "budget_recovery": "positive_progress",
    }[insight_type]


def _insights_share_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_category = _key(left.get("category"))
    right_category = _key(right.get("category"))
    if left_category != right_category:
        return False
    left_merchant = _key(left.get("merchant"))
    right_merchant = _key(right.get("merchant"))
    if left_merchant and right_merchant and left_merchant != right_merchant:
        return False
    left_ids = set(left.get("source_pattern_ids") or [])
    left_ids.add(left.get("primary_pattern_id"))
    right_ids = set(right.get("source_pattern_ids") or [])
    right_ids.add(right.get("primary_pattern_id"))
    return bool(left_ids & right_ids) or _date_range_overlaps(left, right)


def _arbitrate_insights(insights: list[dict[str, Any]]) -> None:
    """Select at most one coach and one report insight per shared subject."""
    for insight in insights:
        insight["primary_coach_insight"] = False
        insight["primary_report_insight"] = False

    groups: list[list[dict[str, Any]]] = []
    for insight in insights:
        matching = [group for group in groups if any(
            _insights_share_scope(insight, other) for other in group
        )]
        if not matching:
            groups.append([insight])
            continue
        target = matching[0]
        target.append(insight)
        for other in matching[1:]:
            target.extend(other)
            groups.remove(other)

    def ranking(insight: dict[str, Any]) -> tuple[int, int, bool, str]:
        return (
            _composite_relevance_rank(insight),
            int(insight["insight_type"] in {
                "budget_attention",
                "spending_trend_worsening",
                "spending_trend_improving",
            }),
            insight.get("pattern_strength") == "high",
            insight.get("insight_id") or "",
        )

    for group in groups:
        active = [
            insight for insight in group
            if insight["coach_eligible"] or insight["report_eligible"]
        ]
        directions = {insight.get("direction") for insight in active}
        if "worsening" in directions and "improving" in directions:
            for insight in active:
                insight["conflicting_evidence"] = True
                insight["suppression_reason"] = "conflicting_evidence"
                insight["coach_eligible"] = False
                insight["report_eligible"] = False
            continue

        coach_candidates = [insight for insight in active if insight["coach_eligible"]]
        if coach_candidates:
            winner = max(coach_candidates, key=ranking)
            winner["primary_coach_insight"] = True
            for insight in coach_candidates:
                if insight is winner:
                    continue
                insight["coach_eligible"] = False
                insight["suppression_reason"] = "superseded"

        report_candidates = [insight for insight in active if insight["report_eligible"]]
        if report_candidates:
            winner = max(report_candidates, key=ranking)
            winner["primary_report_insight"] = True
            for insight in report_candidates:
                if insight is winner:
                    continue
                insight["report_eligible"] = False
                if not insight["suppression_reason"]:
                    insight["suppression_reason"] = "superseded"


def build_behavior_insights(
    patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive shadow-only, structured insights from already arbitrated patterns."""
    insights: list[dict[str, Any]] = []
    for pattern in patterns:
        insight_type = _insight_type_for_pattern(pattern)
        if not insight_type:
            continue
        source_pattern_ids = sorted(set(
            pattern.get("related_pattern_ids") or [pattern["pattern_id"]]
        ))
        suppression_reason = _insight_suppression_reason(pattern, insight_type)
        report_eligible = _insight_report_eligible(
            pattern,
            insight_type,
            suppression_reason,
        )
        coach_eligible = bool(
            pattern.get("eligible_for_coach") and not suppression_reason
        )
        insight_key = f"{insight_type}:{pattern['period_start']}:{pattern['period_end']}"
        insights.append({
            "candidate_type": "behavior_insight_candidate",
            "insight_id": _stable_id(
                "behavior_insight",
                source_pattern_ids,
                insight_key,
            ),
            "insight_type": insight_type,
            "source_pattern_ids": source_pattern_ids,
            "primary_pattern_id": pattern["pattern_id"],
            "period_start": pattern["period_start"],
            "period_end": pattern["period_end"],
            "category": pattern.get("category"),
            "merchant": pattern.get("merchant"),
            "direction": pattern.get("direction"),
            "evidence_summary": {
                "pattern_type": pattern["pattern_type"],
                "composite_type": pattern.get("composite_type"),
                "category": pattern.get("category"),
                "merchant": pattern.get("merchant"),
                "direction": pattern.get("direction"),
            },
            "evidence_metrics": _insight_metrics(pattern, insight_type),
            "pattern_strength": pattern.get("pattern_strength"),
            "financial_relevance": pattern.get("financial_relevance"),
            "confidence_class": (
                "unknown"
                if suppression_reason in {
                    "conflicting_evidence",
                    "uncertain_activity_status",
                    "uncertain_event_time",
                }
                else "high"
                if pattern.get("pattern_strength") == "high"
                and pattern.get("financial_relevance") == "high"
                else "medium"
                if pattern.get("pattern_strength") in {"high", "medium"}
                else "low"
            ),
            "coach_eligible": coach_eligible,
            "report_eligible": report_eligible,
            "coach_timing_hint": _insight_timing_hint(pattern, insight_type),
            "report_section_hint": _insight_report_section(insight_type),
            "suppression_reason": suppression_reason,
            "conflicting_evidence": bool(pattern.get("conflicting_evidence")),
            "superseded_by_pattern_id": pattern.get("superseded_by_pattern_id"),
        })

    _arbitrate_insights(insights)
    return sorted(insights, key=lambda insight: (
        insight["period_start"],
        insight["insight_type"],
        insight.get("category") or "",
        insight["insight_id"],
    ))


def _month_distance(left: str, right: str) -> int:
    left_year, left_month = (int(part) for part in left.split("-"))
    right_year, right_month = (int(part) for part in right.split("-"))
    return (right_year - left_year) * 12 + right_month - left_month


def _contiguous_months(months: list[str]) -> bool:
    return bool(months) and all(
        _month_distance(left, right) == 1
        for left, right in zip(months, months[1:])
    )


def _historical_items_for_category(
    grouped: dict[tuple[str, str], list[dict[str, Any]]],
    category_key: str,
    month_keys: list[str],
) -> list[dict[str, Any]]:
    return [
        item
        for month_key in month_keys
        for item in grouped.get((category_key, month_key), [])
    ]


def _category_label(
    items: list[dict[str, Any]],
    category_key: str,
) -> str:
    labels = [item["category"] for item in items if item["category_key"] == category_key]
    return Counter(labels).most_common(1)[0][0] if labels else "Unbekannt"


def _items_through_day(
    items: list[dict[str, Any]],
    month_key: str,
    day: int,
) -> list[dict[str, Any]]:
    month_end_day = monthrange(
        *(int(part) for part in month_key.split("-"))
    )[1]
    cutoff = min(day, month_end_day)
    return [item for item in items if item["occurred_at"].day <= cutoff]


def _historical_category_patterns(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    month_keys = _completed_month_keys(now)
    history_start = datetime.fromisoformat(f"{month_keys[0]}-01 00:00:00")
    items = _load_consumption_items(
        conn,
        user_id,
        period_start=history_start,
        period_end=now,
    )
    grouped = _historical_category_items(items, month_keys)
    budgets = _historical_budgets(conn, user_id, month_keys)
    historical_overall_budget = _overall_budget_status(
        conn,
        user_id,
        items=items,
        month_keys=month_keys,
        current_month=now.strftime("%Y-%m"),
    )
    if not items and not budgets:
        return []

    category_keys = sorted(
        {category_key for category_key, _ in grouped}
        | {category_key for category_key, _ in budgets}
    )
    patterns: list[dict[str, Any]] = []
    repeated_by_category: dict[str, dict[str, Any]] = {}
    trend_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for category_key in category_keys:
        category = _category_label(items, category_key)
        category_items_all = [
            item for item in items if item["category_key"] == category_key
        ]
        category_kind = _category_kind(category_items_all, category)
        budget_months = [
            month_key
            for month_key in month_keys
            if (category_key, month_key) in budgets
        ]
        over_rows: list[dict[str, Any]] = []
        budget_rows: list[dict[str, Any]] = []
        for month_key in budget_months:
            month_items = grouped.get((category_key, month_key), [])
            spent = round(sum(float(item["amount"]) for item in month_items), 2)
            limit = budgets[(category_key, month_key)]
            quality = _month_quality(month_items)
            row = {
                "month": month_key,
                "amount_spent": spent,
                "monthly_limit": limit,
                "amount_over": round(max(0.0, spent - limit), 2),
                "over_budget": spent > limit,
                "over_budget_percent": round(max(0.0, spent / limit - 1.0), 3),
                "quality": quality,
            }
            budget_rows.append(row)
            if row["over_budget"]:
                over_rows.append(row)

        if len(budget_rows) >= MIN_HISTORICAL_MONTHS and len(over_rows) >= MIN_OVER_BUDGET_MONTHS:
            over_months = [row["month"] for row in over_rows]
            over_items = _historical_items_for_category(grouped, category_key, over_months)
            dominated = any(row["quality"]["single_expense_dominated"] for row in over_rows)
            average_over_budget_percent = round(
                sum(row["over_budget_percent"] for row in over_rows) / len(over_rows),
                3,
            )
            overall_rows = [
                {
                    "month": month_key,
                    **historical_overall_budget.get(month_key, {}),
                }
                for month_key in budget_months
            ]
            known_overall_rows = [
                row for row in overall_rows
                if row.get("status") in {"healthy", "under_pressure"}
            ]
            overall_is_known_and_healthy = bool(overall_rows) and (
                len(known_overall_rows) == len(overall_rows)
                and all(row["status"] == "healthy" for row in known_overall_rows)
            )
            overall_pressure_months = [
                row["month"] for row in overall_rows
                if row.get("status") == "under_pressure"
            ]
            large_repeated_category_deviation = (
                average_over_budget_percent >= MIN_MAJOR_CATEGORY_OVERAGE_PERCENT
            )
            relevance_supported = (
                len(overall_pressure_months) >= 2
                or large_repeated_category_deviation
            )
            eligible = (
                category_kind == "discretionary"
                and not dominated
                and relevance_supported
            )
            financial_relevance = (
                "high" if eligible else
                "medium" if category_kind == "discretionary" and not dominated else
                "low"
            )
            repeated = _historical_pattern(
                pattern_type="category_repeated_over_budget",
                items=over_items,
                month_keys=budget_months,
                category=category,
                observations={
                    "months_considered": len(budget_rows),
                    "months_over_budget": len(over_rows),
                    "monthly_values": budget_rows,
                    "average_over_budget_percent": average_over_budget_percent,
                    "total_deviation_eur": round(
                        sum(row["amount_over"] for row in over_rows), 2
                    ),
                    "single_expense_dominated": dominated,
                    "historical_overall_budget_status": historical_overall_budget,
                    "overall_pressure_months": overall_pressure_months,
                    "overall_context_known_and_healthy": overall_is_known_and_healthy,
                    "large_repeated_category_deviation": large_repeated_category_deviation,
                    "category_kind": category_kind,
                    "hard_quality_exclusion": "single_outlier" if dominated else None,
                },
                direction="worsening" if len(over_rows) >= 4 else "stable",
                pattern_strength="high" if len(over_rows) >= 4 else "medium",
                financial_relevance=financial_relevance,
                relevance_reason=(
                    "Diskretionaere Kategorie liegt wiederholt ueber dem eigenen Monatsbudget."
                    if eligible and not overall_is_known_and_healthy
                    else "Diskretionaere Kategorie liegt wiederholt ueber dem eigenen Monatsbudget; "
                         "zusaetzlicher Gesamtbudgetdruck oder eine grosse wiederholte Abweichung ist belegt."
                    if eligible
                    else "Kategorieabweichung ist beobachtbar, aber der historische Gesamtbudgetkontext "
                         "stuetzt keinen starken negativen Composite."
                    if category_kind == "discretionary" and not dominated
                    else "Historische Budgetabweichung ist nicht belastbar fuer Verhaltenscoaching."
                ),
                eligible_for_coach=eligible,
                eligible_for_report=eligible,
            )
            patterns.append(repeated)
            if category_kind == "discretionary":
                repeated_by_category[category_key] = repeated

        month_rows = []
        for month_key in month_keys:
            month_items = grouped.get((category_key, month_key), [])
            if not month_items:
                continue
            month_rows.append({
                "month": month_key,
                "amount": round(sum(float(item["amount"]) for item in month_items), 2),
                "quality": _month_quality(month_items),
            })
        if len(month_rows) < MIN_HISTORICAL_MONTHS:
            continue
        trend_rows = month_rows[-MIN_HISTORICAL_MONTHS:]
        trend_months = [row["month"] for row in trend_rows]
        values = [float(row["amount"]) for row in trend_rows]
        if not _contiguous_months(trend_months):
            continue
        direction = _trend_direction(values)
        if direction is None:
            continue
        delta = round(values[-1] - values[0], 2)
        relative = abs(delta) / max(abs(values[0]), 1.0)
        if abs(delta) < MIN_TREND_DELTA_EUR or relative < MIN_TREND_RELATIVE_CHANGE:
            continue
        dominated = any(row["quality"]["single_expense_dominated"] for row in trend_rows)
        eligible = category_kind == "discretionary" and not dominated
        trend_type = (
            "category_spending_worsening"
            if direction == "worsening"
            else "category_spending_improving"
        )
        trend = _historical_pattern(
            pattern_type=trend_type,
            items=_historical_items_for_category(grouped, category_key, trend_months),
            month_keys=trend_months,
            category=category,
            observations={
                "months_compared": len(trend_rows),
                "monthly_amounts": trend_rows,
                "absolute_change_eur": delta,
                "relative_change": round(relative, 3),
                "single_expense_dominated": dominated,
            },
            direction=direction,
            pattern_strength="high" if relative >= 0.5 else "medium",
            financial_relevance="high" if eligible and direction == "worsening" else "medium" if eligible else "low",
            relevance_reason=(
                "Eigene Ausgabenentwicklung ist ueber mehrere abgeschlossene Monate klar "
                + ("angestiegen." if direction == "worsening" else "gesunken.")
                if eligible
                else "Historische Entwicklung ist nicht fuer Verhaltenscoaching freigegeben."
            ),
            eligible_for_coach=eligible,
            eligible_for_report=eligible,
        )
        patterns.append(trend)
        if eligible:
            trend_by_category[category_key].append(trend)

    current_month = now.strftime("%Y-%m")
    baseline_months = month_keys[-MIN_HISTORICAL_MONTHS:]
    for category_key in category_keys:
        baseline_month_items = [
            grouped.get((category_key, month_key), [])
            for month_key in baseline_months
        ]
        baseline_rows = [
            _items_through_day(month_items, month_key, now.day)
            for month_items, month_key in zip(baseline_month_items, baseline_months)
        ]
        current_month_items = [
            item for item in items
            if item["category_key"] == category_key
            and item["occurred_at"].strftime("%Y-%m") == current_month
        ]
        current_items = _items_through_day(current_month_items, current_month, now.day)
        if not current_items or any(not month_items for month_items in baseline_rows):
            continue
        category = _category_label(items, category_key)
        if _merchant_class("", category) != "discretionary":
            continue
        baseline_amounts = [
            round(sum(float(item["amount"]) for item in month_items), 2)
            for month_items in baseline_rows
        ]
        baseline_quality = [_month_quality(month_items) for month_items in baseline_rows]
        current_quality = _month_quality(current_items)
        single_expense_dominated = any(
            quality["single_expense_dominated"] for quality in baseline_quality
        ) or current_quality["single_expense_dominated"]
        baseline_average = sum(baseline_amounts) / len(baseline_amounts)
        elapsed_fraction = now.day / monthrange(now.year, now.month)[1]
        # The baseline already covers the same calendar-day segment. Scaling it
        # by the current month's fraction would compare a partial segment twice.
        fair_baseline = baseline_average
        current_amount = round(sum(float(item["amount"]) for item in current_items), 2)
        delta = round(current_amount - fair_baseline, 2)
        relative = abs(delta) / max(fair_baseline, 1.0)
        if abs(delta) < MIN_TREND_DELTA_EUR or relative < MIN_TREND_RELATIVE_CHANGE:
            continue
        direction = "worsening" if delta > 0 else "improving"
        baseline_items = _historical_items_for_category(grouped, category_key, baseline_months)
        pattern = _historical_pattern(
            pattern_type="behavior_change_vs_personal_baseline",
            items=baseline_items + current_items,
            month_keys=baseline_months,
            category=category,
            observations={
                "baseline_months": baseline_months,
                "comparison_mode": "same_day_of_month_segment",
                "comparison_day": now.day,
                "baseline_amounts": baseline_amounts,
                "baseline_average_eur": round(baseline_average, 2),
                "current_month": current_month,
                "current_amount_eur": current_amount,
                "elapsed_fraction": round(elapsed_fraction, 3),
                "fair_baseline_eur": round(fair_baseline, 2),
                "difference_eur": delta,
                "relative_change": round(relative, 3),
                "single_expense_dominated": single_expense_dominated,
            },
            direction=direction,
            pattern_strength="high" if relative >= 0.5 else "medium",
            financial_relevance=(
                "high" if direction == "worsening" and not single_expense_dominated
                else "medium" if not single_expense_dominated
                else "low"
            ),
            relevance_reason=(
                "Aktueller Monat liegt fair anteilig deutlich ueber der eigenen Basislinie."
                if direction == "worsening" and not single_expense_dominated
                else "Aktueller Monat liegt fair anteilig deutlich unter der eigenen Basislinie."
                if not single_expense_dominated
                else "Basislinienvergleich ist wegen einer dominanten Einzelbuchung nicht belastbar."
            ),
            eligible_for_coach=not single_expense_dominated,
            eligible_for_report=not single_expense_dominated,
            period_end_override=now,
        )
        patterns.append(pattern)
        if direction == "worsening" and not single_expense_dominated:
            trend_by_category[category_key].append(pattern)

    for category_key, repeated in repeated_by_category.items():
        related = [repeated, *trend_by_category.get(category_key, [])]
        category = repeated["category"]
        composite_category_kind = repeated["observations"].get(
            "category_kind",
            _category_kind(
                [item for item in items if item["category_key"] == category_key],
                category,
            ),
        )
        related_ids = [pattern["pattern_id"] for pattern in related]
        combined_source_ids = sorted({source_id for pattern in related for source_id in pattern["source_ids"]})
        items_by_id = {item["source_id"]: item for item in items}
        combined_items = [items_by_id[source_id] for source_id in combined_source_ids if source_id in items_by_id]
        repeated_observations = repeated["observations"]
        historical_overall_budget = repeated_observations.get(
            "historical_overall_budget_status",
            {},
        )
        overall_pressure_months = repeated_observations.get(
            "overall_pressure_months",
            [],
        )
        category_deviation_is_large = repeated_observations.get(
            "large_repeated_category_deviation",
            False,
        )
        related_worsening = any(
            pattern.get("direction") == "worsening"
            and pattern.get("eligible_for_coach")
            for pattern in related
        )
        budget_evidence_months = {
            row["month"]
            for row in repeated_observations.get("monthly_values", [])
        }
        relevant_overall_budget = {
            month: status
            for month, status in historical_overall_budget.items()
            if month in budget_evidence_months
        }
        overall_is_known_and_healthy = bool(relevant_overall_budget) and all(
            status.get("status") == "healthy"
            for status in relevant_overall_budget.values()
        )
        composite_supported = (
            len(overall_pressure_months) >= 2
            or category_deviation_is_large
            or related_worsening
        )
        composite_hard_exclusion = bool(
            repeated_observations.get("hard_quality_exclusion")
            or any(
                pattern.get("observations", {}).get("hard_quality_exclusion")
                for pattern in related
            )
        )
        composite_relevance = (
            "high" if composite_supported and not composite_hard_exclusion
            else "medium" if composite_supported
            else "low"
        )
        composite_eligible = (
            composite_supported
            and composite_category_kind == "discretionary"
            and not composite_hard_exclusion
        )
        combined = _historical_pattern(
            pattern_type="repeated_discretionary_budget_pressure",
            items=combined_items,
            month_keys=month_keys,
            category=category,
            observations={
                "component_pattern_ids": related_ids,
                "months_over_budget": repeated["observations"]["months_over_budget"],
                "historical_budget_evidence": repeated["observations"]["monthly_values"],
                "historical_overall_budget_status": historical_overall_budget,
                "overall_pressure_months": overall_pressure_months,
                "overall_context_known_and_healthy": overall_is_known_and_healthy,
                "category_deviation_is_large": category_deviation_is_large,
                "category_kind": composite_category_kind,
                "hard_quality_exclusion": (
                    "single_outlier" if composite_hard_exclusion else None
                ),
            },
            direction="worsening",
            pattern_strength="high",
            financial_relevance=composite_relevance,
            relevance_reason=(
                "Diskretionaere Budgetabweichung ist ueber mehrere Monate belegt."
                if composite_supported and not composite_hard_exclusion
                else "Kategorieabweichung ist beobachtbar; der bekannte Gesamtbudgetstatus bleibt historisch gesund."
            ),
            eligible_for_coach=composite_eligible,
            eligible_for_report=composite_eligible,
            related_pattern_ids=related_ids,
        )
        composite_type = (
            "repeated_discretionary_overspend"
            if any(pattern.get("direction") == "worsening" for pattern in related)
            else "repeated_discretionary_budget_pressure"
        )
        _mark_composite(
            combined,
            composite_type=composite_type,
            related_pattern_ids=related_ids,
            direction="worsening",
            evidence_summary=(
                "Wiederholter diskretionaerer Budgetdruck wird durch die "
                "mehrmonatige Ausgabenentwicklung gestuetzt."
                if composite_type == "repeated_discretionary_overspend"
                else "Wiederholter diskretionaerer Budgetdruck ist ueber mehrere Monate belegt."
            ),
        )
        for component in related:
            component["eligible_for_coach"] = False
            component["eligible_for_report"] = False
            component["superseded_by_pattern_id"] = combined["pattern_id"]
        patterns.append(combined)

    return patterns


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

    movement_by_expense = canonical_expense_movement_kinds(conn, user_id)

    items: list[dict[str, Any]] = []
    for row in rows:
        occurred_at = _parse_timestamp(row["event_time"])
        if occurred_at is None or not (period_start <= occurred_at <= period_end):
            continue
        movement_kind = movement_by_expense.get(int(row["id"]))
        if movement_kind is not None:
            if movement_kind in NON_CONSUMPTION_MOVEMENTS:
                continue
            # V4 must not guess when an explicit movement kind is unknown.
            if movement_kind not in {"card", "payment"}:
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
            "event_time_reliable": bool(row["reliable_event_time"]),
            "occurred_at": occurred_at,
            "calendar_time_reliable": bool(row["reliable_event_time"])
            and _timestamp_quality(row["reliable_event_time"]) in {"date_only", "local_clock"},
            "clock_time_reliable": bool(row["reliable_event_time"])
            and _timestamp_quality(row["reliable_event_time"]) == "local_clock",
        })
    return items


def _load_salary_income_events(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Load only canonical, plausibly recurring salary movements.

    ``app_cash_movements.kind='income'`` is the canonical source. A generic
    credit is not enough: it needs an explicit salary label, and known one-off
    labels are excluded. A planned amount is only context, never a classifier.
    """
    columns = _columns(conn, "app_cash_movements")
    if not {"id", "user_id", "kind", "amount"}.issubset(columns):
        return []
    # Import/creation time is not a safe substitute for the fachliche booking
    # date. Without a reliable event timestamp, salary-cycle shadow analysis is
    # intentionally disabled rather than inventing a timing story.
    timestamp_column = next(
        (name for name in RELIABLE_TIMESTAMP_COLUMNS if name in columns),
        None,
    )
    if timestamp_column is None:
        return []
    label_expression = "label" if "label" in columns else "'' AS label"
    history_start = _shift_month(
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        -SALARY_HISTORY_MONTHS,
    )
    event_time_expression = timestamp_column
    rows = conn.execute(
        f"""SELECT id, amount, {label_expression}, {event_time_expression} AS event_time
               FROM app_cash_movements
              WHERE user_id=? AND kind='income' AND amount > 0
                AND {event_time_expression} >= ? AND {event_time_expression} <= ?
              ORDER BY datetime({event_time_expression}), id""",
        (user_id, _iso(history_start), _iso(now)),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        occurred_at = _parse_timestamp(row["event_time"])
        if occurred_at is None:
            continue
        label = str(row["label"] or "").strip()
        label_key = _salary_label_key(label)
        # Exclusions run before the generic salary hints ("Gehalt", "Lohn").
        if _salary_contains_hint(label_key, NON_SALARY_INCOME_HINTS):
            continue
        if _salary_contains_hint(label_key, AMBIGUOUS_SALARY_SOURCE_HINTS):
            continue
        amount = round(float(row["amount"] or 0), 2)
        explicit_salary = _salary_contains_hint(label_key, SALARY_LABEL_HINTS)
        if not explicit_salary:
            continue
        reason = "explicit_salary_label"
        period_key = occurred_at.strftime("%Y-%m-%d")
        event_id = _stable_id("salary_cycle", [str(row["id"])], period_key)
        events.append({
            "event_id": event_id,
            "source_id": str(row["id"]),
            "occurred_at": occurred_at,
            "amount": amount,
            "label": label,
            "classification_reason": reason,
            "source_key": label_key,
        })
    return events


def _salary_cycles(
    events: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    """Return one conservative monthly salary chain, never inferred credits."""
    if not events:
        return []
    source_keys = {event.get("source_key") for event in events}
    if len(source_keys) != 1:
        return []
    clusters: list[list[dict[str, Any]]] = []
    for event in events:
        if clusters and (event["occurred_at"] - clusters[-1][-1]["occurred_at"]).days <= 7:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    # Multiple salary-like credits in one immediate window are ambiguous
    # (bonus, duplicate booking, correction) and are not a cycle.
    deduplicated = [cluster[0] for cluster in clusters if len(cluster) == 1]
    if not deduplicated:
        return []

    chains: list[list[dict[str, Any]]] = []
    for event in deduplicated:
        if not chains:
            chains.append([event])
            continue
        gap = (event["occurred_at"] - chains[-1][-1]["occurred_at"]).days
        if MIN_SALARY_GAP_DAYS <= gap <= MAX_SALARY_GAP_DAYS:
            chains[-1].append(event)
        else:
            chains.append([event])
    chain = max(chains, key=lambda candidate: (len(candidate), candidate[-1]["occurred_at"]))
    cycles: list[dict[str, Any]] = []
    for index, event in enumerate(chain):
        next_event = chain[index + 1] if index + 1 < len(chain) else None
        window_7_end = event["occurred_at"] + timedelta(days=7)
        cycles.append({
            **event,
            "window_3_end": _iso(event["occurred_at"] + timedelta(days=3)),
            "window_7_end": _iso(window_7_end),
            "window_7_complete": window_7_end <= now and (
                next_event is None
                or next_event["occurred_at"] > window_7_end
            ),
        })
    return cycles


def _window_items(
    items: list[dict[str, Any]],
    cycle: dict[str, Any],
    window_days: int,
) -> list[dict[str, Any]]:
    start = cycle["occurred_at"]
    end = start + timedelta(days=window_days)
    return [
        item for item in items
        if _merchant_class(item["merchant"], item["category"]) == "discretionary"
        and timedelta(0) <= item["occurred_at"] - start <= end - start
    ]


def _salary_window_context(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": cycle["event_id"],
            "source_id": cycle["source_id"],
            "occurred_at": _iso(cycle["occurred_at"]),
            "amount": cycle["amount"],
            "label": cycle["label"],
            "classification_reason": cycle["classification_reason"],
            "window_3_end": cycle["window_3_end"],
            "window_7_end": cycle["window_7_end"],
            "window_7_complete": cycle["window_7_complete"],
        }
        for cycle in cycles
    ]


def _overall_budget_status(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    items: list[dict[str, Any]],
    month_keys: list[str],
    current_month: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for month in month_keys:
        spent = round(sum(
            float(item["amount"])
            for item in items
            if item["occurred_at"].strftime("%Y-%m") == month
        ), 2)
        plan: dict[str, Any] | None = None
        snapshot_columns = _columns(conn, "monthly_financial_snapshots")
        if {"user_id", "report_month", "income", "fixed_costs"}.issubset(snapshot_columns):
            snapshot_fields = [
                name if name in snapshot_columns else f"0 AS {name}"
                for name in ("income", "other_income", "fixed_costs", "etf_savings", "cash_savings")
            ]
            snapshot = conn.execute(
                f"""SELECT {', '.join(snapshot_fields)}
                     FROM monthly_financial_snapshots
                    WHERE user_id=? AND report_month=?""",
                (user_id, month),
            ).fetchone()
            if snapshot and snapshot["income"] is not None and snapshot["fixed_costs"] is not None:
                plan = {
                    "income": float(snapshot["income"] or 0) + float(snapshot["other_income"] or 0),
                    "fixed": float(snapshot["fixed_costs"] or 0),
                    "savings": float(snapshot["etf_savings"] or 0) + float(snapshot["cash_savings"] or 0),
                    "source": "monthly_financial_snapshot",
                }
        if plan is None and month == current_month:
            user_columns = _columns(conn, "users")
            if {"user_id", "income", "fixed_costs"}.issubset(user_columns):
                user = conn.execute(
                    "SELECT * FROM users WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                if user and user["income"] is not None and user["fixed_costs"] is not None:
                    user_keys = set(user.keys())
                    plan = {
                        "income": (
                            float(user["income"] or 0)
                            + float(user["other_income"] or 0)
                            if "other_income" in user_keys
                            else float(user["income"] or 0)
                        ),
                        "fixed": float(user["fixed_costs"] or 0),
                        "savings": sum(
                            float(user[name] or 0)
                            for name in ("etf_savings", "cash_savings")
                            if name in user_keys
                        ),
                        "source": "current_canonical_user_plan",
                    }
        if plan is None:
            result[month] = {
                "status": "unknown",
                "variable_expenses_eur": spent,
                "reason": "historical_budget_snapshot_missing",
            }
            continue
        variable_budget = round(plan["income"] - plan["fixed"] - plan["savings"], 2)
        result[month] = {
            "status": "under_pressure" if spent > variable_budget else "healthy",
            "variable_budget_eur": variable_budget,
            "variable_expenses_eur": spent,
            "free_remaining_eur": round(variable_budget - spent, 2),
            "budget_source": plan["source"],
        }
    return result


def _unreliable_salary_source_ids(
    conn: sqlite3.Connection,
    user_id: int,
) -> list[str]:
    """Identify explicitly labelled salary rows without a usable event time."""
    columns = _columns(conn, "app_cash_movements")
    if not {"id", "user_id", "kind", "amount"}.issubset(columns):
        return []
    label_expression = "label" if "label" in columns else "'' AS label"
    timestamp_column = next(
        (name for name in RELIABLE_TIMESTAMP_COLUMNS if name in columns),
        None,
    )
    event_time_expression = timestamp_column or "NULL"
    rows = conn.execute(
        f"""SELECT id, {label_expression}, {event_time_expression} AS event_time
               FROM app_cash_movements
              WHERE user_id=? AND kind='income' AND amount > 0
              ORDER BY id""",
        (user_id,),
    ).fetchall()
    unreliable: list[str] = []
    for row in rows:
        label_key = _salary_label_key(row["label"])
        if not _salary_contains_hint(label_key, SALARY_LABEL_HINTS):
            continue
        if _salary_contains_hint(label_key, NON_SALARY_INCOME_HINTS):
            continue
        if _salary_contains_hint(label_key, AMBIGUOUS_SALARY_SOURCE_HINTS):
            continue
        if _parse_timestamp(row["event_time"]) is None:
            unreliable.append(str(row["id"]))
    return unreliable


def _post_income_patterns(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    unreliable_salary_source_ids = _unreliable_salary_source_ids(conn, user_id)
    events = _load_salary_income_events(conn, user_id, now=now)
    cycles = _salary_cycles(events, now=now)
    context: dict[str, Any] = {
        "minimum_reliable_cycles": MIN_SALARY_CYCLES,
        "cycles_detected": len(cycles),
        "cycles_used": 0,
        "windows": list(SALARY_WINDOW_DAYS),
        "income_events": _salary_window_context(cycles),
        "unreliable_salary_source_count": len(unreliable_salary_source_ids),
        "unreliable_salary_source_ids": unreliable_salary_source_ids,
        "eligibility_reason": None,
    }
    if len(cycles) < MIN_SALARY_CYCLES:
        context["eligibility_reason"] = "minimum_salary_cycles_not_met"
        return [], context
    usable_cycles = [cycle for cycle in cycles if cycle["window_7_complete"]]
    context["cycles_used"] = len(usable_cycles)
    if len(usable_cycles) < MIN_SALARY_CYCLES:
        context["eligibility_reason"] = "post_income_window_incomplete"
        return [], context

    first_cycle_month = min(cycle["occurred_at"] for cycle in usable_cycles).replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    history_start = _shift_month(first_cycle_month, -SALARY_HISTORY_MONTHS)
    history_end = now
    items = _load_consumption_items(
        conn,
        user_id,
        period_start=history_start,
        period_end=history_end,
    )
    salary_windows = [
        (cycle["occurred_at"], cycle["occurred_at"] + timedelta(days=7))
        for cycle in usable_cycles
    ]
    baseline_items = [
        item for item in items
        if item.get("event_time_reliable")
        and _merchant_class(item["merchant"], item["category"]) == "discretionary"
        and not any(start <= item["occurred_at"] <= end for start, end in salary_windows)
    ]
    baseline_dates = {item["occurred_at"].date() for item in baseline_items}
    context["baseline"] = {
        "available": len(baseline_dates) >= MIN_BASELINE_DATES,
        "observed_dates": len(baseline_dates),
        "source": "discretionary_transactions_outside_post_income_windows",
    }
    if len(baseline_dates) < MIN_BASELINE_DATES:
        context["eligibility_reason"] = "insufficient_personal_baseline"
        return [], context

    patterns: list[dict[str, Any]] = []

    def metrics(
        post_rows: list[dict[str, Any]],
        baseline_rows: list[dict[str, Any]],
        span_days: int,
        cycle_count: int,
    ) -> tuple[float, float, float, float] | None:
        baseline_dates_local = {row["occurred_at"].date() for row in baseline_rows}
        if len(baseline_dates_local) < MIN_BASELINE_DATES:
            return None
        post_average = sum(float(row["amount"]) for row in post_rows) / cycle_count
        daily_baseline = sum(float(row["amount"]) for row in baseline_rows) / len(baseline_dates_local)
        baseline_average = daily_baseline * span_days
        delta = round(post_average - baseline_average, 2)
        relative = round(delta / max(baseline_average, 1.0), 3)
        return round(post_average, 2), round(baseline_average, 2), delta, relative

    def evaluate_window(
        window_days: int,
        *,
        category_key: str | None = None,
    ) -> dict[str, Any] | None:
        rows_by_cycle = []
        for cycle in usable_cycles:
            rows = _window_items(items, cycle, window_days)
            if category_key is not None:
                rows = [row for row in rows if row["category_key"] == category_key]
            # Both sides of the comparison need a fachliches transaction date;
            # created_at fallback data must not create a salary story.
            if not all(row.get("event_time_reliable") for row in rows):
                continue
            quality = _month_quality(rows)
            if (
                len(rows) >= MIN_POST_INCOME_ITEMS_PER_CYCLE
                and not quality["single_expense_dominated"]
            ):
                rows_by_cycle.append((cycle, rows))
        if len(rows_by_cycle) < MIN_SALARY_CYCLES:
            return None
        post_rows = [row for _, rows in rows_by_cycle for row in rows]
        baseline_rows = (
            baseline_items
            if category_key is None
            else [row for row in baseline_items if row["category_key"] == category_key]
        )
        result = metrics(post_rows, baseline_rows, window_days + 1, len(rows_by_cycle))
        if result is None or result[2] < MIN_POST_INCOME_DELTA_EUR or result[3] < MIN_POST_INCOME_RELATIVE_CHANGE:
            return None
        return {
            "window_days": window_days,
            "rows_by_cycle": rows_by_cycle,
            "post_rows": post_rows,
            "metrics": result,
        }

    total_evaluations = [
        evaluation
        for window_days in SALARY_WINDOW_DAYS
        if (evaluation := evaluate_window(window_days)) is not None
    ]
    if total_evaluations:
        selected_total = max(
            total_evaluations,
            key=lambda evaluation: (
                evaluation["metrics"][3],
                evaluation["metrics"][2],
                -evaluation["window_days"],
            ),
        )
        total_by_cycle = selected_total["rows_by_cycle"]
        total_post_items = selected_total["post_rows"]
        post_average, baseline_average, delta, relative = selected_total["metrics"]
        total_pattern = _pattern(
            pattern_type="post_income_discretionary_spike",
            items=total_post_items,
            period_start=min(cycle["occurred_at"] for cycle, _ in total_by_cycle),
            period_end=min(now, max(cycle["occurred_at"] + timedelta(days=selected_total["window_days"]) for cycle, _ in total_by_cycle)),
            category=None,
            merchant=None,
            observations={
                "income_event_ids": [cycle["event_id"] for cycle, _ in total_by_cycle],
                "salary_cycles": len(total_by_cycle),
                "window_days": selected_total["window_days"],
                "window_definition": "income_timestamp_through_income_timestamp_plus_window_days",
                "available_windows": [evaluation["window_days"] for evaluation in total_evaluations],
                "post_income_average_eur": post_average,
                "personal_baseline_average_eur": baseline_average,
                "absolute_delta_eur": delta,
                "relative_delta": relative,
                "temporal_correlation_only": True,
            },
            pattern_strength="high" if relative >= 0.5 else "medium",
            financial_relevance="high" if delta >= 100 else "medium",
            relevance_reason="Diskretionaere Ausgaben liegen nach mehreren Gehaltseingaengen ueber der persoenlichen Basislinie; das belegt eine zeitliche Korrelation, keine Kausalitaet.",
            eligible_for_coach=True,
            eligible_for_report=True,
            stable_period_key="salary-cycle:total:" + ",".join(cycle["event_id"] for cycle, _ in total_by_cycle),
        )
        patterns.append(total_pattern)
    else:
        total_pattern = None

    category_keys = {
        row["category_key"]
        for cycle in usable_cycles
        for window_days in SALARY_WINDOW_DAYS
        for row in _window_items(items, cycle, window_days)
    }
    for category_key in sorted(category_keys):
        category_evaluations = [
            evaluation
            for window_days in SALARY_WINDOW_DAYS
            if (evaluation := evaluate_window(window_days, category_key=category_key)) is not None
        ]
        if not category_evaluations:
            continue
        selected_category = max(
            category_evaluations,
            key=lambda evaluation: (
                evaluation["metrics"][3],
                evaluation["metrics"][2],
                -evaluation["window_days"],
            ),
        )
        rows_by_cycle = selected_category["rows_by_cycle"]
        category_post = selected_category["post_rows"]
        post_average, baseline_average, delta, relative = selected_category["metrics"]
        category = Counter(row["category"] for row in category_post).most_common(1)[0][0]
        patterns.append(_pattern(
            pattern_type="post_income_category_spike",
            items=category_post,
            period_start=min(cycle["occurred_at"] for cycle, _ in rows_by_cycle),
            period_end=min(now, max(cycle["occurred_at"] + timedelta(days=selected_category["window_days"]) for cycle, _ in rows_by_cycle)),
            category=category,
            merchant=None,
            observations={
                "income_event_ids": [cycle["event_id"] for cycle, _ in rows_by_cycle],
                "salary_cycles": len(rows_by_cycle),
                "window_days": selected_category["window_days"],
                "available_windows": [evaluation["window_days"] for evaluation in category_evaluations],
                "post_income_average_eur": post_average,
                "personal_baseline_average_eur": baseline_average,
                "absolute_delta_eur": delta,
                "relative_delta": relative,
                "temporal_correlation_only": True,
            },
            pattern_strength="high" if relative >= 0.5 else "medium",
            financial_relevance="high" if delta >= 100 else "medium",
            relevance_reason="Diese diskretionaere Kategorie liegt nach mehreren Gehaltseingaengen ueber ihrer persoenlichen Basislinie; das belegt keine Ursache.",
            eligible_for_coach=True,
            eligible_for_report=True,
            stable_period_key="salary-cycle:category:" + category_key + ":" + ",".join(cycle["event_id"] for cycle, _ in rows_by_cycle),
        ))

    pattern_month_keys = (
        sorted({cycle["occurred_at"].strftime("%Y-%m") for cycle, _ in total_by_cycle})
        if total_pattern
        else []
    )
    month_keys = sorted({cycle["occurred_at"].strftime("%Y-%m") for cycle in usable_cycles})
    budget_status = _overall_budget_status(
        conn,
        user_id,
        items=items,
        month_keys=month_keys,
        current_month=now.strftime("%Y-%m"),
    )
    context["overall_budget_status"] = budget_status
    budget_pressure_months = [
        month for month, status in budget_status.items()
        if status.get("status") == "under_pressure" and month in pattern_month_keys
    ]
    if total_pattern and budget_pressure_months:
        component_patterns = [
            total_pattern,
            *[
                pattern for pattern in patterns
                if pattern["pattern_type"] == "post_income_category_spike"
            ],
        ]
        component_ids = [pattern["pattern_id"] for pattern in component_patterns]
        pressure = _pattern(
            pattern_type="post_income_budget_pressure",
            items=total_post_items,
            period_start=min(cycle["occurred_at"] for cycle, _ in total_by_cycle),
            period_end=min(
                now,
                max(
                    cycle["occurred_at"] + timedelta(days=total_pattern["observations"]["window_days"])
                    for cycle, _ in total_by_cycle
                ),
            ),
            category=None,
            merchant=None,
            observations={
                "component_pattern_ids": component_ids,
                "budget_status_by_month": budget_status,
                "budget_pressure_months": budget_pressure_months,
                "income_event_ids": [cycle["event_id"] for cycle, _ in total_by_cycle],
                "temporal_correlation_only": True,
            },
            pattern_strength="high",
            financial_relevance="high",
            relevance_reason="Post-Income-Ausgabenmuster trifft auf belegten negativen Gesamtbudgetstatus; eine Kausalitaet wird nicht behauptet.",
            eligible_for_coach=True,
            eligible_for_report=True,
            stable_period_key="salary-cycle:budget-pressure:" + ",".join(cycle["event_id"] for cycle, _ in total_by_cycle),
            related_pattern_ids=component_ids,
        )
        _mark_composite(
            pressure,
            composite_type="post_income_behavior_with_budget_pressure",
            related_pattern_ids=component_ids,
            direction="worsening",
            evidence_summary=(
                "Ein belastbares Post-Income-Ausgabenmuster faellt mit "
                "tatsaechlichem Gesamtbudgetdruck zusammen; eine Kausalitaet "
                "wird nicht behauptet."
            ),
        )
        for component in component_patterns:
            component["eligible_for_coach"] = False
            component["eligible_for_report"] = False
            component["superseded_by_pattern_id"] = pressure["pattern_id"]
        patterns.append(pressure)

    if not patterns:
        context["eligibility_reason"] = context.get("eligibility_reason") or "no_threshold_reached"
    return patterns, context


def _classify_recurring_service(name: str, category: str) -> str | None:
    text = _key(f"{name} {category}")
    # Prime is a bundle; only an explicit Prime Video label is safe to classify
    # as video streaming. Generic Amazon/Prime rows remain unclassified.
    if "prime" in text and "prime video" not in text:
        return None
    for cluster_type, hints in SERVICE_CLASS_HINTS.items():
        if _contains_hint(text, hints):
            return cluster_type
    return None


def _service_key(name: str, cluster_type: str) -> str:
    """Build a stable, provider-scoped key without changing source data."""
    text = re.sub(r"[^a-z0-9]+", " ", _key(name)).strip()
    text = re.sub(r"\b(?:www|com|de|net|org)\b", " ", text)
    text = re.sub(
        r"\b(?:basic|standard|premium|monthly|annual|abo|subscription)\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    aliases = (
        ("netflix", ("netflix",)),
        ("disney_plus", ("disney plus", "disney")),
        ("paramount_plus", ("paramount plus", "paramount")),
        ("prime_video", ("prime video",)),
        ("spotify", ("spotify",)),
        ("apple_music", ("apple music",)),
        ("dazn", ("dazn",)),
        ("sky_stream", ("sky stream",)),
        ("sky_cinema", ("sky cinema",)),
        ("sky_entertainment", ("sky entertainment",)),
        ("wow_tv", ("wow tv",)),
    )
    provider = next(
        (canonical for canonical, hints in aliases if any(hint in text for hint in hints)),
        text or "unknown",
    )
    return f"{cluster_type}:{provider}"


def _contract_frequency(row: sqlite3.Row, columns: set[str]) -> tuple[str, float | None]:
    frequency_column = next(
        (
            name
            for name in (
                "frequency",
                "billing_frequency",
                "billing_cycle",
                "recurrence",
                "period",
            )
            if name in columns
        ),
        None,
    )
    raw_frequency = str(row[frequency_column] or "").strip() if frequency_column else ""
    frequency_key = _key(raw_frequency)
    amount = round(float(row["amount"] or 0), 2)
    if frequency_key in SERVICE_FREQUENCY_MONTHLY:
        return "monthly", amount
    if frequency_key in SERVICE_FREQUENCY_ANNUAL:
        return "annual", round(amount / 12.0, 2)
    return "unknown", None


def _contract_is_active(row: sqlite3.Row, columns: set[str]) -> bool:
    status_column = next(
        (name for name in ("status", "lifecycle_status") if name in columns),
        None,
    )
    if status_column:
        status = _key(row[status_column])
        if status in SERVICE_STATUS_INACTIVE:
            return False
        if status not in SERVICE_STATUS_ACTIVE:
            return False
    active_column = next(
        (name for name in ("active", "is_active") if name in columns),
        None,
    )
    if active_column and row[active_column] not in (1, True, "1", "true", "active"):
        return False
    return True


def _contract_activity_confidence(columns: set[str]) -> str:
    if "status" in columns or "lifecycle_status" in columns or "active" in columns or "is_active" in columns:
        return "verified"
    return "unknown"


def _similar_recurring_service_patterns(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    columns = _columns(conn, "app_contracts")
    required = {"user_id", "contract_id", "name", "amount"}
    context: dict[str, Any] = {
        "source": "app_contracts",
        "clusters_considered": [],
        "eligibility_reason": None,
    }
    if not required.issubset(columns):
        context["eligibility_reason"] = "canonical_contract_source_unavailable"
        return [], context

    rows = conn.execute(
        "SELECT * FROM app_contracts WHERE user_id=? ORDER BY contract_id",
        (user_id,),
    ).fetchall()
    clusters: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    seen_contract_ids: set[str] = set()
    activity_confidence = _contract_activity_confidence(columns)
    for row in rows:
        contract_id = str(row["contract_id"] or "").strip()
        if not contract_id or contract_id in seen_contract_ids:
            continue
        seen_contract_ids.add(contract_id)
        if not _contract_is_active(row, columns):
            continue
        amount = round(float(row["amount"] or 0), 2)
        if amount <= 0:
            continue
        name = str(row["name"] or "").strip()
        category = str(row["category"] or "").strip() if "category" in columns else ""
        cluster_type = _classify_recurring_service(name, category)
        if cluster_type is None:
            continue
        service_key = _service_key(name, cluster_type)
        frequency, monthly_amount = _contract_frequency(row, columns)
        timestamp = None
        for timestamp_column in ("updated_at", "created_at"):
            if timestamp_column in columns:
                timestamp = _parse_timestamp(row[timestamp_column])
                if timestamp:
                    break
        timestamp = timestamp or now
        clusters[cluster_type][service_key].append({
            "source_id": contract_id,
            "service_key": service_key,
            "name": name,
            "category": category,
            "amount": amount,
            "frequency": frequency,
            "monthly_amount": monthly_amount,
            "occurred_at": timestamp,
        })

    patterns: list[dict[str, Any]] = []
    for cluster_type, service_groups in sorted(clusters.items()):
        services: list[dict[str, Any]] = []
        for service_key, rows_for_service in sorted(service_groups.items()):
            rows_for_service.sort(key=lambda service: service["source_id"])
            amounts = {service["amount"] for service in rows_for_service}
            frequencies = {service["frequency"] for service in rows_for_service}
            monthly_amounts = {service["monthly_amount"] for service in rows_for_service}
            representative = dict(rows_for_service[0])
            representative["contract_row_count"] = len(rows_for_service)
            representative["contract_ids"] = [service["source_id"] for service in rows_for_service]
            representative["duplicate_ambiguous"] = len(rows_for_service) > 1
            representative["amount_ambiguous"] = len(amounts) > 1
            representative["frequency_ambiguous"] = len(frequencies) > 1
            representative["monthly_amount"] = (
                next(iter(monthly_amounts))
                if len(monthly_amounts) == 1
                else None
            )
            services.append(representative)
        if len(services) < SIMILAR_SERVICE_MIN_COUNT:
            continue
        services.sort(key=lambda service: (service["name"].casefold(), service["source_id"]))
        monthly_total = (
            round(sum(float(service["monthly_amount"]) for service in services), 2)
            if all(service["monthly_amount"] is not None for service in services)
            else None
        )
        service_count = len(services)
        strength = (
            "high" if service_count >= SIMILAR_SERVICE_HIGH_COUNT
            else "medium" if service_count >= SIMILAR_SERVICE_MEDIUM_COUNT
            else "low"
        )
        relevance = (
            "high" if monthly_total is not None and monthly_total >= SIMILAR_SERVICE_HIGH_EUR
            else "medium" if monthly_total is not None and monthly_total >= SIMILAR_SERVICE_MEDIUM_EUR
            else "low"
        )
        if monthly_total is None:
            reason = (
                "Mehrere aehnlich klassifizierte laufende Services erkannt; "
                "die Frequenz ist nicht belastbar genug fuer eine Monatsnormalisierung."
            )
        else:
            reason = (
                f"{service_count} aehnliche laufende Services mit zusammen "
                f"{monthly_total:.2f} EUR pro Monat erkannt; daraus folgt keine Aussage "
                "ueber Notwendigkeit oder Kuendbarkeit."
            )
        duplicate_ambiguous = any(service["duplicate_ambiguous"] for service in services)
        amount_ambiguous = any(service["amount_ambiguous"] for service in services)
        if duplicate_ambiguous:
            reason += " Mehrfachzeilen derselben Service-Familie werden nur einmal gezaehlt."
        if amount_ambiguous:
            reason += " Unterschiedliche Duplikatbetraege werden nicht zusammengefasst."
        if activity_confidence != "verified":
            reason = (
                "Mehrere klassifizierte Vertragszeilen erkannt; der Aktivitaetsstatus ist "
                "nicht belastbar verifiziert. Daher keine Coach- oder Report-Eignung."
            )
        context["clusters_considered"].append({
            "cluster_type": cluster_type,
            "service_count": service_count,
            "monthly_normalized_total": monthly_total,
            "frequency_complete": monthly_total is not None,
            "activity_confidence": activity_confidence,
            "duplicate_ambiguous": duplicate_ambiguous,
        })
        pattern_items = [
            {
                "source_id": service["source_id"],
                "amount": service["amount"],
                "occurred_at": service["occurred_at"],
            }
            for service in services
        ]
        patterns.append(_pattern(
            pattern_type="similar_recurring_services",
            items=pattern_items,
            period_start=min(service["occurred_at"] for service in services),
            period_end=max(service["occurred_at"] for service in services),
            category=cluster_type,
            merchant=None,
            observations={
                "cluster_type": cluster_type,
                "services": [
                    {
                        "name": service["name"],
                        "service_key": service["service_key"],
                        "contract_row_count": service["contract_row_count"],
                        "contract_ids": service["contract_ids"],
                        "duplicate_ambiguous": service["duplicate_ambiguous"],
                        "raw_amount_eur": service["amount"],
                        "frequency": service["frequency"],
                        "monthly_normalized_amount_eur": service["monthly_amount"],
                    }
                    for service in services
                ],
                "service_count": service_count,
                "monthly_normalized_total": monthly_total,
                "raw_recurring_amounts": [service["amount"] for service in services],
                "frequency_complete": monthly_total is not None,
                "data_quality": (
                    "complete"
                    if monthly_total is not None and activity_confidence == "verified"
                    else "activity_unknown"
                    if activity_confidence != "verified"
                    else "frequency_unknown"
                ),
                "activity_confidence": activity_confidence,
                "duplicate_ambiguous": duplicate_ambiguous,
            },
            pattern_strength=strength,
            financial_relevance=relevance,
            relevance_reason=reason,
            eligible_for_coach=(
                activity_confidence == "verified" and relevance in {"medium", "high"}
            ),
            eligible_for_report=False,
            stable_period_key=f"similar-recurring:{cluster_type}",
        ))
    if not patterns:
        context["eligibility_reason"] = "no_conservative_similarity_cluster"
    return patterns, context


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
    overall_context = _overall_budget_status(
        conn,
        user_id,
        items=items,
        month_keys=[month_key],
        current_month=month_key,
    ).get(month_key, {"status": "unknown"})
    overall_status = overall_context.get("status", "unknown")
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
        category_relevance = (
            "high" if overall_status == "under_pressure"
            else "medium" if overall_status == "healthy"
            else "low"
        )
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
                "amount_over": round(max(0.0, spent - limit), 2),
                "overall_budget_status": overall_status,
                "overall_variable_budget_eur": overall_context.get(
                    "variable_budget_eur"
                ),
                "overall_variable_spend_eur": overall_context.get(
                    "variable_expenses_eur"
                ),
                "overall_free_remaining_eur": overall_context.get(
                    "free_remaining_eur"
                ),
                "month_elapsed_fraction": round(elapsed_fraction, 3),
            },
            pattern_strength="high" if pressure_gap >= 0.25 else "medium",
            financial_relevance=category_relevance,
            relevance_reason=(
                "Kategorie-Budget ist gemessen am Monatsfortschritt deutlich unter Druck; "
                "der gesamte variable Monatsrahmen steht ebenfalls unter Druck."
                if overall_status == "under_pressure"
                else "Kategorie-Budget ist gemessen am Monatsfortschritt deutlich unter Druck; "
                "der gesamte variable Monatsrahmen ist weiterhin gesund."
                if overall_status == "healthy"
                else "Kategorie-Budget ist auffaellig, aber der Gesamtbudgetstatus ist nicht belastbar."
            ),
            eligible_for_coach=overall_status == "under_pressure",
            eligible_for_report=True,
            stable_period_key=month_key,
        )
        pressures.append(pressure)
    return pressures


def _positive_shadow_composites(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime,
    patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build positive evidence only from already validated raw patterns."""
    composites: list[dict[str, Any]] = []
    worsening_categories = {
        _key(pattern.get("category"))
        for pattern in patterns
        if pattern.get("direction") == "worsening"
        and pattern.get("eligible_for_coach")
        and pattern.get("category")
    }
    for pattern in patterns:
        category_key = _key(pattern.get("category"))
        if not pattern.get("eligible_for_coach") or category_key in worsening_categories:
            continue
        if pattern["pattern_type"] == "category_spending_improving":
            composites.append(_positive_composite(
                pattern,
                composite_type="category_spending_improvement_confirmed",
                evidence_summary=(
                    "Die diskretionaeren Ausgaben dieser Kategorie sind ueber "
                    "mehrere belastbare Monate gesunken."
                ),
                relevance_reason=(
                    "Mehrmonatige Verbesserung der eigenen diskretionaeren "
                    "Kategorie ist durch aktuelle Daten belegt."
                ),
            ))
        elif pattern["pattern_type"] == "behavior_change_vs_personal_baseline" and pattern.get("direction") == "improving":
            composites.append(_positive_composite(
                pattern,
                composite_type="discretionary_spending_reduction",
                evidence_summary=(
                    "Die diskretionaeren Ausgaben liegen belastbar unter der "
                    "eigenen vergleichbaren Basislinie."
                ),
                relevance_reason=(
                    "Eine Reduktion gegen die eigene Basislinie ist durch "
                    "vergleichbare Daten belegt."
                ),
            ))

    # A recovery claim needs a completed current month and an explicit current
    # budget; absence of a pressure signal alone is not enough evidence.
    current_month = now.strftime("%Y-%m")
    if now.day != monthrange(now.year, now.month)[1]:
        return composites
    current_budgets = _historical_budgets(conn, user_id, [current_month])
    if not current_budgets:
        return composites
    current_items = _load_consumption_items(
        conn,
        user_id,
        period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        period_end=now,
    )
    for pattern in patterns:
        if pattern["pattern_type"] != "repeated_discretionary_budget_pressure":
            continue
        category = str(pattern.get("category") or "").strip()
        category_key = _key(category)
        limit = current_budgets.get((category_key, current_month))
        category_items = [
            item for item in current_items
            if item["category_key"] == category_key
        ]
        spent = round(sum(float(item["amount"]) for item in category_items), 2)
        historical_limits = [
            float(row["monthly_limit"])
            for row in pattern.get("observations", {}).get(
                "historical_budget_evidence",
                [],
            )
            if row.get("monthly_limit") is not None
        ]
        budgets_comparable = limit is not None and _budgets_are_comparable(
            historical_limits,
            float(limit),
        )
        if (
            not pattern.get("eligible_for_coach")
            or limit is None
            or not category_items
            or spent > limit
            or not budgets_comparable
        ):
            continue
        composites.append(_positive_composite(
            pattern,
            composite_type="budget_recovery",
            evidence_summary=(
                "Eine zuvor wiederholt belastete diskretionaere Kategorie "
                "liegt im abgeschlossenen aktuellen Monat wieder im Budget."
            ),
            relevance_reason=(
                "Historischer Budgetdruck und ein abgeschlossener aktueller "
                "Monat innerhalb des eigenen Budgets sind belegt."
            ),
            observations_update={
                "budget_context": {
                    "current_limit": round(float(limit), 2),
                    "historical_limits": sorted(set(historical_limits)),
                    "comparable": True,
                },
            },
        ))
    return composites


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
        total = sum(float(item["amount"]) for item in weekday_items)
        single_expense_dominated = max(
            (float(item["amount"]) for item in weekday_items),
            default=0.0,
        ) / total >= MAX_SINGLE_EXPENSE_SHARE if total else False
        relevant = total >= MIN_RELEVANT_TOTAL_EUR and not single_expense_dominated
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
                "single_expense_dominated": single_expense_dominated,
            },
            pattern_strength=_strength(len(weekday_items)),
            financial_relevance="medium" if relevant else "low",
            relevance_reason=(
                "Wiederholung an einem Wochentag ist nur bei ausreichender Evidenz belastbar."
                if relevant
                else "Das Wochentagsmuster ist wegen einer dominanten Einzelbuchung nicht belastbar."
            ),
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
        occurrence_dates = {
            item["occurred_at"].date() for item in late_merchant_items
        }
        if len(occurrence_dates) < MIN_REPEATED_TRANSACTIONS:
            continue
        merchant = Counter(item["merchant"] for item in late_merchant_items).most_common(1)[0][0]
        category = Counter(item["category"] for item in late_merchant_items).most_common(1)[0][0]
        total = sum(float(item["amount"]) for item in late_merchant_items)
        single_expense_dominated = max(
            (float(item["amount"]) for item in late_merchant_items),
            default=0.0,
        ) / total >= MAX_SINGLE_EXPENSE_SHARE if total else False
        relevant = total >= MIN_RELEVANT_TOTAL_EUR and not single_expense_dominated
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
                "occurrence_date_count": len(occurrence_dates),
                "time_window": "20:30-00:30",
                "single_expense_dominated": single_expense_dominated,
            },
            pattern_strength=_strength(len(late_merchant_items)),
            financial_relevance="medium" if relevant else "low",
            relevance_reason=(
                "Mehrere diskretionaere Buchungen liegen an verschiedenen Tagen im definierten spaeten Zeitfenster."
                if relevant
                else "Das spaete Zeitfenster liefert wegen fehlender Verteilung oder einer dominanten Einzelbuchung keine belastbare Evidenz."
            ),
            eligible_for_coach=relevant,
            eligible_for_report=False,
            stable_period_key=stable_window_key,
        ))

    budget_period_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    budget_items = _load_consumption_items(
        conn,
        user_id,
        period_start=budget_period_start,
        period_end=now,
    )
    pressures = _category_budget_pressures(
        conn,
        user_id,
        now=now,
        items=budget_items,
    )
    patterns.extend(pressures)
    overall_budget = _overall_budget_status(
        conn,
        user_id,
        items=budget_items,
        month_keys=[now.strftime("%Y-%m")],
        current_month=now.strftime("%Y-%m"),
    ).get(now.strftime("%Y-%m"), {"status": "unknown"})

    historical_patterns = _historical_category_patterns(conn, user_id, now=now)
    patterns.extend(historical_patterns)

    # Phase 2B stays shadow-only: salary-cycle evidence is appended to the
    # existing inspector stream and never enters Coach V3 prioritization.
    post_income_patterns, _ = _post_income_patterns(conn, user_id, now=now)
    patterns.extend(post_income_patterns)

    # Phase 2C stays shadow-only as well. Contract similarity is evidence only;
    # it does not create a visible coach candidate or any cancellation action.
    similar_service_patterns, _ = _similar_recurring_service_patterns(
        conn,
        user_id,
        now=now,
    )
    patterns.extend(similar_service_patterns)

    # A multi-month discretionary pattern supersedes the current one-month
    # pressure so the shadow inspector exposes one coherent explanation.
    historical_combinations = [
        pattern
        for pattern in historical_patterns
        if pattern["pattern_type"] == "repeated_discretionary_budget_pressure"
    ]
    for pressure in pressures:
        matching = [
            combined
            for combined in historical_combinations
            if _key(combined.get("category")) == _key(pressure.get("category"))
        ]
        if not matching:
            continue
        combined = matching[0]
        combined["related_pattern_ids"] = sorted(
            set(combined["related_pattern_ids"]) | {pressure["pattern_id"]}
        )
        combined["observations"]["current_budget_pattern_id"] = pressure["pattern_id"]
        pressure["eligible_for_coach"] = False
        pressure["eligible_for_report"] = False
        pressure["superseded_by_pattern_id"] = combined["pattern_id"]

    items_by_id = {item["source_id"]: item for item in items}
    behavior_patterns = [
        pattern for pattern in patterns
        if pattern["pattern_type"] in {
            "merchant_weekday_pattern",
            "late_night_discretionary_spending",
        } and pattern["eligible_for_coach"]
    ]
    improving_categories = {
        _key(pattern.get("category"))
        for pattern in patterns
        if pattern.get("direction") == "improving"
        and pattern.get("eligible_for_coach")
        and pattern.get("category")
    }
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
        if behavior_category in improving_categories:
            # Keep contradictory raw evidence visible, but do not manufacture
            # a negative composite from a simultaneous improvement signal.
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
                "behavior_amount_total": behavior["amount_total"],
                "behavior_transaction_count": behavior["observations"].get(
                    "transaction_count"
                ),
                "behavior_occurrence_date_count": behavior["observations"].get(
                    "occurrence_date_count"
                ),
                "budget_amount_spent": pressure["observations"].get(
                    "amount_spent"
                ),
                "budget_monthly_limit": pressure["observations"].get(
                    "monthly_limit"
                ),
                "budget_amount_over": pressure["observations"].get(
                    "amount_over"
                ),
                "budget_used_fraction": pressure["observations"]["budget_used_fraction"],
            },
            pattern_strength=(
                "high" if overall_budget.get("status") == "under_pressure" else "medium"
            ),
            financial_relevance=(
                "high" if overall_budget.get("status") == "under_pressure" else "low"
            ),
            relevance_reason=(
                "Belastbares Verhaltensmuster trifft auf konkreten Kategorie-Budgetdruck "
                "und negativen Gesamtbudgetstatus."
                if overall_budget.get("status") == "under_pressure"
                else "Verhaltensmuster faellt mit Kategorie-Budgetdruck zusammen; "
                "der Gesamtbudgetstatus ist nicht belastbar negativ."
            ),
            eligible_for_coach=overall_budget.get("status") == "under_pressure",
            eligible_for_report=overall_budget.get("status") == "under_pressure",
            stable_period_key=f"{stable_window_key}|{now.strftime('%Y-%m')}",
            related_pattern_ids=[behavior["pattern_id"], pressure["pattern_id"]],
        )
        _mark_composite(
            combined,
            composite_type="behavior_driving_budget_pressure",
            related_pattern_ids=[behavior["pattern_id"], pressure["pattern_id"]],
            direction="worsening",
            evidence_summary=(
                "Ein belastbares Verhaltenspattern faellt zeitlich mit "
                "konkretem Kategorie-Budgetdruck zusammen; eine Kausalitaet "
                "wird nicht behauptet."
            ),
        )
        behavior["eligible_for_coach"] = False
        behavior["eligible_for_report"] = False
        behavior["superseded_by_pattern_id"] = combined["pattern_id"]
        pressure["eligible_for_coach"] = False
        pressure["eligible_for_report"] = False
        pressure["superseded_by_pattern_id"] = combined["pattern_id"]
        patterns.append(combined)

    patterns.extend(_positive_shadow_composites(
        conn,
        user_id,
        now=now,
        patterns=patterns,
    ))

    _arbitrate_composites(patterns)

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
    effective_now = (now or datetime.now()).replace(tzinfo=None)
    _, post_income_context = _post_income_patterns(
        conn,
        user_id,
        now=effective_now,
    )
    _, similar_service_context = _similar_recurring_service_patterns(
        conn,
        user_id,
        now=effective_now,
    )
    patterns = detect_behavior_patterns(conn, user_id, now=effective_now)
    insights = build_behavior_insights(patterns)
    suppressed_salary_time = _suppressed_salary_time_insight(post_income_context)
    if suppressed_salary_time:
        insights.append(suppressed_salary_time)
        insights.sort(key=lambda insight: (
            insight.get("period_start") or "",
            insight["insight_type"],
            insight.get("category") or "",
            insight["insight_id"],
        ))
    primary_coach_insights = [
        insight for insight in insights
        if insight.get("primary_coach_insight")
    ]
    primary_report_insights = [
        insight for insight in insights
        if insight.get("primary_report_insight")
    ]

    def primary_key(insight: dict[str, Any]) -> tuple[int, int, bool, str]:
        return (
            _composite_relevance_rank(insight),
            int(insight.get("insight_type") in {
                "budget_attention",
                "spending_trend_worsening",
                "spending_trend_improving",
            }),
            insight.get("pattern_strength") == "high",
            insight.get("insight_id") or "",
        )

    composites = [
        pattern for pattern in patterns
        if pattern.get("pattern_kind") == "composite_behavior_pattern"
    ]
    primary_composites = [
        pattern for pattern in composites
        if pattern.get("primary_composite")
    ]
    historical_budget_status: dict[str, Any] = {}
    for pattern in composites:
        historical_budget_status.update(
            pattern.get("observations", {}).get(
                "historical_overall_budget_status",
                {},
            )
        )
    conflict_reasons = sorted({
        str(pattern["conflict_reason"])
        for pattern in composites
        if pattern.get("conflicting_evidence") and pattern.get("conflict_reason")
    })
    return {
        "mode": "shadow",
        "coach_v3_affected": False,
        "patterns": patterns,
        "composite_patterns": composites,
        "positive_patterns": [
            pattern for pattern in patterns
            if pattern.get("direction") == "improving"
            and pattern.get("pattern_kind") == "composite_behavior_pattern"
        ],
        "primary_composite": (
            primary_composites[0]["pattern_id"]
            if len(primary_composites) == 1
            else None
        ),
        "superseded_by": {
            pattern["pattern_id"]: pattern["superseded_by_pattern_id"]
            for pattern in composites
            if pattern.get("superseded_by_pattern_id")
        },
        "conflicting_evidence": bool(conflict_reasons),
        "conflict_reason": conflict_reasons[0] if conflict_reasons else None,
        "budget_context": {
            "historical_overall_budget_status": historical_budget_status,
        },
        "historical_overall_budget_status": historical_budget_status,
        "post_income_shadow": post_income_context,
        "similar_recurring_shadow": similar_service_context,
        "insight_candidates": insights,
        "primary_coach_insight": (
            max(primary_coach_insights, key=primary_key)["insight_id"]
            if primary_coach_insights
            else None
        ),
        "primary_report_insight": (
            max(primary_report_insights, key=primary_key)["insight_id"]
            if primary_report_insights
            else None
        ),
    }
