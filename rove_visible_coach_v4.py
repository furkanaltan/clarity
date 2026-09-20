"""Server-side delivery gate for the Coach V4 visible pilot.

This module only reads the ready snapshot DTO. It never runs detection, loads
financial truth, or performs a live recompute. The feature is deliberately
default-off and the allowlist is controlled by server environment only.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime
from threading import Lock
from typing import Any

from rove_behavior_snapshot import (
    BEHAVIOR_CONTRACT_VERSION,
    BEHAVIOR_ENGINE_VERSION,
    SNAPSHOT_VERSION,
    get_behavior_snapshot_status,
    get_visible_behavior_snapshot,
)
from rove_behavior_patterns import VISIBLE_BEHAVIOR_CONTRACT_VERSION


VISIBLE_FLAG_ENV = "ROVE_COACH_V4_VISIBLE_ENABLED"
VISIBLE_ALLOWLIST_ENV = "ROVE_COACH_V4_VISIBLE_USER_IDS"
ALLOWED_VISIBLE_INSIGHT_TYPES = frozenset({
    "budget_attention",
    "spending_trend_improving",
})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SAFE_CATEGORIES = frozenset({
    "Apotheke", "Cloud und Software", "Drogerie", "Einkaufen", "Essen",
    "Fitness", "Freizeit", "Gaming", "Gesundheit", "Lebensmittel",
    "Medien", "Medizin", "Mode", "Musik", "Restaurants", "Shopping",
    "Sonstiges", "Sport-Streaming", "Streaming", "Unterhaltung",
})
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ALLOWED_SOURCE_FIELDS = frozenset({
    "visible_behavior_contract_version",
    "insight_type",
    "category_display",
    "period_start",
    "period_end",
    "direction",
    "confidence_class",
    "monthly_amounts",
    "amount_current",
    "amount_reference",
    "amount_delta",
    "budget_amount",
    "amount_over_budget",
    "overall_budget_status",
    "months_compared",
})
_INTERNAL_OR_UNSAFE_FIELDS = frozenset({
    "merchant", "merchant_display", "source_ids", "source_pattern_ids",
    "pattern_id", "insight_id", "account_id", "provider_id", "iban",
    "token", "secret", "suppression_reason", "coach_suppression_reason",
    "report_suppression_reason", "coach_eligible", "report_eligible",
    "primary_coach_insight", "primary_report_insight",
})

_metrics_lock = Lock()
_metrics = {
    "requests": 0,
    "eligible": 0,
    "delivered": 0,
    "suppressed": 0,
    "skipped": Counter(),
    "suppression_reasons": Counter(),
    "insight_types": Counter(),
}


def _flag_enabled() -> bool:
    return os.getenv(VISIBLE_FLAG_ENV, "").strip().lower() in _TRUE_VALUES


def _allowlisted_user_ids() -> frozenset[int]:
    raw = os.getenv(VISIBLE_ALLOWLIST_ENV, "")
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            user_id = int(item)
        except (TypeError, ValueError):
            continue
        if user_id > 0:
            values.add(user_id)
    return frozenset(values)


def visible_pilot_config() -> dict[str, Any]:
    """Return non-sensitive config facts for tests and internal diagnostics."""
    return {
        "enabled": _flag_enabled(),
        "allowlist_configured": bool(_allowlisted_user_ids()),
        "allowed_insight_types": sorted(ALLOWED_VISIBLE_INSIGHT_TYPES),
        "contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
    }


def reset_visible_pilot_metrics() -> None:
    """Reset process-local counters; intended for isolated tests only."""
    with _metrics_lock:
        _metrics["requests"] = 0
        _metrics["eligible"] = 0
        _metrics["delivered"] = 0
        _metrics["suppressed"] = 0
        _metrics["skipped"] = Counter()
        _metrics["suppression_reasons"] = Counter()
        _metrics["insight_types"] = Counter()


def _record_request() -> None:
    with _metrics_lock:
        _metrics["requests"] += 1


def _record_skip(reason: str) -> None:
    with _metrics_lock:
        _metrics["skipped"][reason] += 1


def _record_suppressed(reason: str) -> None:
    with _metrics_lock:
        _metrics["suppressed"] += 1
        _metrics["suppression_reasons"][reason] += 1


def _record_delivery(insight_type: str) -> None:
    with _metrics_lock:
        _metrics["eligible"] += 1
        _metrics["delivered"] += 1
        _metrics["insight_types"][insight_type] += 1


def get_visible_pilot_metrics() -> dict[str, Any]:
    """Return bounded process-local delivery aggregates without user dimensions."""
    with _metrics_lock:
        requests = int(_metrics["requests"])
        eligible = int(_metrics["eligible"])
        delivered = int(_metrics["delivered"])
        suppressed = int(_metrics["suppressed"])
        skipped = dict(sorted(_metrics["skipped"].items()))
        suppression_reasons = dict(sorted(_metrics["suppression_reasons"].items()))
        insight_types = dict(sorted(_metrics["insight_types"].items()))
    decisions = delivered + suppressed + sum(skipped.values())
    return {
        "metrics_scope": "process",
        "requests": requests,
        "eligible": eligible,
        "delivered": delivered,
        "suppressed": suppressed,
        "skipped": skipped,
        "suppression_reasons": suppression_reasons,
        "insight_types": insight_types,
        "eligibility_rate": round(eligible / decisions, 4) if decisions else 0.0,
        "delivery_rate": round(delivered / decisions, 4) if decisions else 0.0,
        "suppression_rate": round(suppressed / decisions, 4) if decisions else 0.0,
    }


def _finite_number(value: object, *, minimum: float | None = None) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if minimum is not None and number < minimum:
        return None
    return round(number, 2)


def _safe_category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    if value not in _SAFE_CATEGORIES or _CONTROL_RE.search(value):
        return None
    return value


def _safe_period(value: object) -> str | None:
    if not isinstance(value, str) or _CONTROL_RE.search(value):
        return None
    value = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value):
        return None
    return value


def _safe_monthly_amounts(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        month = item.get("month")
        amount = _finite_number(item.get("amount"), minimum=0)
        if not isinstance(month, str) or not _MONTH_RE.fullmatch(month) or amount is None:
            return None
        result.append({"month": month, "amount": amount})
    return result


def _source_payload_is_safe(payload: dict[str, Any]) -> bool:
    if set(payload) - _ALLOWED_SOURCE_FIELDS:
        return False
    if set(payload) & _INTERNAL_OR_UNSAFE_FIELDS:
        return False
    if payload.get("visible_behavior_contract_version") != VISIBLE_BEHAVIOR_CONTRACT_VERSION:
        return False
    return True


def _euro(value: float) -> str:
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


def _render_budget_attention(payload: dict[str, Any]) -> dict[str, Any] | None:
    category = _safe_category(payload.get("category_display"))
    if (
        payload.get("insight_type") != "budget_attention"
        or payload.get("confidence_class") != "high"
        or payload.get("overall_budget_status") != "under_pressure"
        or not category
    ):
        return None
    amount_current = _finite_number(payload.get("amount_current"), minimum=0)
    budget_amount = _finite_number(payload.get("budget_amount"), minimum=0.01)
    amount_over = _finite_number(payload.get("amount_over_budget"), minimum=0.01)
    if amount_current is None or budget_amount is None or amount_over is None:
        return None
    if amount_current <= budget_amount:
        return None
    result = {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "budget_attention",
        "category_display": category,
        "title": "Dein Budget ist diesen Monat unter Druck",
        "message": (
            f"Bei {category} liegst du diesen Monat { _euro(amount_over) } "
            "über deinem eigenen Rahmen."
        ),
        "amount_current": amount_current,
        "budget_amount": budget_amount,
        "amount_over_budget": amount_over,
    }
    for field in ("period_start", "period_end"):
        value = _safe_period(payload.get(field))
        if value:
            result[field] = value
    return result


def _render_spending_improvement(payload: dict[str, Any]) -> dict[str, Any] | None:
    category = _safe_category(payload.get("category_display"))
    monthly = _safe_monthly_amounts(payload.get("monthly_amounts"))
    if (
        payload.get("insight_type") != "spending_trend_improving"
        or payload.get("confidence_class") != "high"
        or payload.get("direction") != "improving"
        or not category
        or not monthly
        or len(monthly) < 3
        or monthly[-1]["amount"] >= monthly[0]["amount"]
    ):
        return None
    result = {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "spending_trend_improving",
        "category_display": category,
        "title": "Deine Ausgaben entwickeln sich positiv",
        "message": (
            f"Bei {category} sind deine Ausgaben über {len(monthly)} Monate "
            f"von {_euro(monthly[0]['amount'])} auf {_euro(monthly[-1]['amount'])} gesunken."
        ),
        "monthly_amounts": monthly,
        "months_compared": len(monthly),
    }
    for field in ("period_start", "period_end"):
        value = _safe_period(payload.get(field))
        if value:
            result[field] = value
    return result


def _render_visible_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not _source_payload_is_safe(payload):
        return None
    insight_type = payload.get("insight_type")
    if insight_type not in ALLOWED_VISIBLE_INSIGHT_TYPES:
        return None
    if insight_type == "budget_attention":
        return _render_budget_attention(payload)
    return _render_spending_improvement(payload)


def _snapshot_skip_reason(status: dict[str, Any]) -> str | None:
    state = status.get("status")
    if state == "missing":
        return "snapshot_missing"
    if state == "error":
        return "error"
    if state == "stale" or status.get("ttl_expired"):
        return "stale"
    if status.get("snapshot_version") != SNAPSHOT_VERSION or status.get("engine_version") != BEHAVIOR_ENGINE_VERSION:
        return "version_mismatch"
    if status.get("behavior_contract_version") != BEHAVIOR_CONTRACT_VERSION:
        return "contract_mismatch"
    if status.get("recompute_state") != "idle":
        return "recompute_active"
    if state != "ready":
        return "stale"
    return None


def get_visible_coach_v4(
    conn: sqlite3.Connection,
    user_id: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the visible pilot DTO, or None for every unsafe/non-pilot state."""
    _record_request()
    if not _flag_enabled():
        _record_skip("flag_off")
        return None
    if int(user_id) not in _allowlisted_user_ids():
        _record_skip("not_allowlisted")
        return None
    try:
        status = get_behavior_snapshot_status(conn, int(user_id), now=now)
        reason = _snapshot_skip_reason(status)
        if reason:
            _record_skip(reason)
            return None
        payload = get_visible_behavior_snapshot(conn, int(user_id), now=now)
    except (sqlite3.Error, TypeError, ValueError):
        _record_skip("snapshot_read_error")
        return None
    if payload is None:
        _record_skip("no_visible_candidate")
        return None
    visible = _render_visible_payload(payload)
    if visible is None:
        reason = "hard_suppressed_or_weak_evidence"
        if isinstance(payload, dict) and payload.get("insight_type") not in ALLOWED_VISIBLE_INSIGHT_TYPES:
            reason = "unsupported_insight_type"
        _record_suppressed(reason)
        return None
    _record_delivery(str(visible["insight_type"]))
    return visible
