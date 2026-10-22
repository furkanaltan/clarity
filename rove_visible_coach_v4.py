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
VISIBLE_MODE_ENV = "ROVE_COACH_V4_VISIBLE_MODE"
VISIBLE_GATE_MODES = frozenset({"OFF", "ON_SCOPED", "ON_GLOBAL"})
ALLOWED_VISIBLE_INSIGHT_TYPES = frozenset({
    "budget_attention",
    "spending_trend_improving",
    "repeated_spending_pattern",
    "spending_trend_worsening",
    "post_income_pattern",
    "subscription_cluster",
    "budget_recovery",
})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SAFE_CATEGORIES = frozenset({
    "Apotheke", "Cloud und Software", "Drogerie", "Einkaufen", "Essen",
    "Fitness", "Freizeit", "Gaming", "Gesundheit", "Lebensmittel",
    "Medien", "Medizin", "Mode", "Musik", "Restaurants", "Shopping",
    "Sonstiges", "Sport-Streaming", "Streaming", "Unterhaltung",
})
_CATEGORY_ALIASES = {
    "streaming_video": "Streaming",
    "streaming_music": "Musik",
    "sport_streaming": "Sport-Streaming",
    "cloud_software": "Cloud und Software",
    "news_media": "Medien",
}
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
    "coach_timing_hint",
    "pattern_strength",
    "transaction_count",
    "active_days",
    "merchant_display",
    "weekday",
    "weekday_opportunities",
    "occurrence_ratio",
    "cycles_used",
    "window_days",
    "temporal_correlation_only",
    "service_count",
    "monthly_normalized_total",
    "frequency_complete",
    "activity_confidence",
    "data_quality",
    "duplicate_ambiguous",
    "reference_period_start",
    "reference_period_end",
    "months_over_budget",
    "historical_budget_evidence",
})
_INTERNAL_OR_UNSAFE_FIELDS = frozenset({
    "merchant", "source_ids", "source_pattern_ids",
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


def _strict_allowlisted_user_ids() -> frozenset[int] | None:
    """Parse an explicit scoped allowlist; malformed lists fail closed."""
    raw = os.getenv(VISIBLE_ALLOWLIST_ENV)
    if raw is None or not raw.strip():
        return None

    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not re.fullmatch(r"[1-9]\d{0,18}", item):
            return None
        user_id = int(item)
        if user_id > 9_223_372_036_854_775_807:
            return None
        values.add(user_id)
    return frozenset(values) or None


def _visible_gate_mode() -> str:
    """Resolve explicit modes while retaining the legacy scoped configuration."""
    raw_mode = os.environ.get(VISIBLE_MODE_ENV)
    if raw_mode is None:
        # Existing deployments use ENABLED=1 plus numeric IDs for ON_SCOPED.
        if _flag_enabled() and _strict_allowlisted_user_ids():
            return "ON_SCOPED"
        return "OFF"

    mode = raw_mode.strip().upper()
    if mode not in VISIBLE_GATE_MODES or mode == "OFF" or not _flag_enabled():
        return "OFF"
    if mode == "ON_GLOBAL":
        return mode
    return "ON_SCOPED" if _strict_allowlisted_user_ids() else "OFF"


def visible_pilot_config() -> dict[str, Any]:
    """Return non-sensitive config facts for tests and internal diagnostics."""
    return {
        "enabled": _flag_enabled(),
        "mode": _visible_gate_mode(),
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
    value = _CATEGORY_ALIASES.get(value, value)
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
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return None
        if result:
            previous = datetime.strptime(result[-1]["month"], "%Y-%m")
            if (parsed.year - previous.year) * 12 + parsed.month - previous.month != 1:
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
    if "coach_timing_hint" in payload and (
        not isinstance(payload["coach_timing_hint"], str)
        or payload["coach_timing_hint"] not in {
            "immediate", "next_checkin", "weekend_prevention", "post_salary_window", "month_end",
        }
    ):
        return False
    if "pattern_strength" in payload and (
        not isinstance(payload["pattern_strength"], str)
        or payload["pattern_strength"] not in {"low", "medium", "high"}
    ):
        return False
    if payload.get("confidence_class") == "high" and payload.get("pattern_strength", "high") != "high":
        return False
    for field in (
        "transaction_count", "active_days", "weekday_opportunities", "cycles_used",
        "window_days", "service_count", "months_compared", "months_over_budget",
        "weekday",
    ):
        if field in payload:
            value = _finite_number(payload[field], minimum=0)
            if value is None or not value.is_integer():
                return False
    if "weekday" in payload and payload["weekday"] > 6:
        return False
    if "occurrence_ratio" in payload:
        ratio = _finite_number(payload["occurrence_ratio"], minimum=0)
        if ratio is None or ratio > 1:
            return False
    for field in ("temporal_correlation_only", "frequency_complete", "duplicate_ambiguous"):
        if field in payload and not isinstance(payload[field], bool):
            return False
    if "activity_confidence" in payload and payload["activity_confidence"] not in {"verified", "unknown"}:
        return False
    if "data_quality" in payload and payload["data_quality"] not in {"complete", "activity_unknown", "frequency_unknown"}:
        return False
    if "merchant_display" in payload:
        value = payload["merchant_display"]
        if (
            not isinstance(value, str) or not value.strip() or len(value) > 80
            or _CONTROL_RE.search(value)
        ):
            return False
    if "monthly_amounts" in payload and _safe_monthly_amounts(payload["monthly_amounts"]) is None:
        return False
    if "historical_budget_evidence" in payload:
        rows = payload["historical_budget_evidence"]
        if not isinstance(rows, list) or len(rows) > 12:
            return False
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"month", "amount_spent", "monthly_limit", "amount_over"}
                or not isinstance(row.get("month"), str)
                or not _MONTH_RE.fullmatch(row["month"])
                or any(_finite_number(row.get(key), minimum=0) is None
                       for key in ("amount_spent", "monthly_limit", "amount_over"))
            ):
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
    if amount_current <= budget_amount or round(amount_current - budget_amount, 2) != round(amount_over, 2):
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
        "overall_budget_status": "under_pressure",
        "confidence_class": payload["confidence_class"],
    }
    for field in ("period_start", "period_end"):
        value = _safe_period(payload.get(field))
        if value:
            result[field] = value
    return result


def _render_spending_improvement(payload: dict[str, Any]) -> dict[str, Any] | None:
    category = _safe_category(payload.get("category_display"))
    monthly = _safe_monthly_amounts(payload.get("monthly_amounts"))
    delta = _finite_number(payload.get("amount_delta"))
    # Positive patterns have medium financial relevance in the existing engine.
    # Keep their confidence label and require the engine's high pattern strength.
    strong_evidence = payload.get("confidence_class") == "high" or (
        payload.get("confidence_class") == "medium" and payload.get("pattern_strength") == "high"
    )
    if (
        payload.get("insight_type") != "spending_trend_improving"
        or not strong_evidence
        or payload.get("direction") != "improving"
        or not category
        or not monthly
        or len(monthly) < 3
        or (delta is not None and delta >= 0)
        or not all(left["amount"] > right["amount"] for left, right in zip(monthly, monthly[1:]))
    ):
        return None
    result = {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "spending_trend_improving",
        "category_display": category,
        "title": "Deine Ausgaben entwickeln sich positiv",
        "message": (
            f"Bei {category} sind deine erfassten Ausgaben über {len(monthly)} Monate "
            f"von {_euro(monthly[0]['amount'])} auf {_euro(monthly[-1]['amount'])} gesunken."
        ),
        "direction": "improving",
        "monthly_amounts": monthly,
        "months_compared": len(monthly),
        "confidence_class": payload["confidence_class"],
    }
    if delta is not None:
        result["amount_delta"] = delta
    for field in ("period_start", "period_end"):
        value = _safe_period(payload.get(field))
        if value:
            result[field] = value
    return result


def _visible_period_payload(payload: dict[str, Any]) -> dict[str, str] | None:
    start = _safe_period(payload.get("period_start"))
    end = _safe_period(payload.get("period_end"))
    if not start or not end or start > end:
        return None
    return {"period_start": start, "period_end": end}


def _render_repeated_spending(payload: dict[str, Any]) -> dict[str, Any] | None:
    subject = payload.get("merchant_display") or _safe_category(payload.get("category_display"))
    period = _visible_period_payload(payload)
    weekday = payload.get("weekday")
    opportunities = payload.get("weekday_opportunities")
    active_days = payload.get("active_days")
    count = payload.get("transaction_count")
    total = _finite_number(payload.get("amount_current"), minimum=30)
    ratio = _finite_number(payload.get("occurrence_ratio"), minimum=0.60)
    confidence = payload.get("confidence_class")
    if not (
        subject and period and isinstance(weekday, int) and not isinstance(weekday, bool)
        and 0 <= weekday <= 6 and isinstance(opportunities, int) and opportunities >= 4
        and isinstance(active_days, int) and 3 <= active_days <= opportunities
        and isinstance(count, int) and count >= active_days
        and total is not None and ratio is not None and ratio <= 1
        and confidence in {"medium", "high"}
    ):
        return None
    weekday_name = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")[weekday]
    return {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "repeated_spending_pattern",
        **period,
        "category_display": _safe_category(payload.get("category_display")),
        "merchant_display": payload.get("merchant_display"),
        "weekday": weekday,
        "weekday_opportunities": opportunities,
        "occurrence_ratio": ratio,
        "active_days": active_days,
        "transaction_count": count,
        "amount_current": total,
        "confidence_class": confidence,
        "title": "Wiederkehrende Bestellungen",
        "message": (
            f"An {active_days} von {opportunities} {weekday_name}en gab es Buchungen "
            f"bei {subject} mit insgesamt {_euro(total)}."
        ),
    }


def _render_spending_worsening(payload: dict[str, Any]) -> dict[str, Any] | None:
    category = _safe_category(payload.get("category_display"))
    period = _visible_period_payload(payload)
    monthly = _safe_monthly_amounts(payload.get("monthly_amounts"))
    count = payload.get("transaction_count")
    change = _finite_number(payload.get("amount_delta"), minimum=0.01)
    confidence = payload.get("confidence_class")
    if not (
        category and period and monthly and len(monthly) >= 3
        and payload.get("months_compared") == len(monthly)
        and payload.get("direction") == "worsening"
        and all(left["amount"] < right["amount"] for left, right in zip(monthly, monthly[1:]))
        and isinstance(count, int) and count > 0 and change is not None
        and confidence in {"medium", "high"}
    ):
        return None
    return {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "spending_trend_worsening",
        **period,
        "category_display": category,
        "direction": "worsening",
        "monthly_amounts": monthly,
        "months_compared": len(monthly),
        "amount_reference": monthly[0]["amount"],
        "amount_current": monthly[-1]["amount"],
        "amount_delta": change,
        "transaction_count": count,
        "confidence_class": confidence,
        "title": "Ausgaben steigen",
        "message": (
            f"Bei {category} stiegen die erfassten Ausgaben über {len(monthly)} "
            f"aufeinanderfolgende Monate von {_euro(monthly[0]['amount'])} "
            f"auf {_euro(monthly[-1]['amount'])}."
        ),
    }


def _render_post_income(payload: dict[str, Any]) -> dict[str, Any] | None:
    period = _visible_period_payload(payload)
    cycles = payload.get("cycles_used")
    window = payload.get("window_days")
    count = payload.get("transaction_count")
    after = _finite_number(payload.get("amount_current"), minimum=0)
    baseline = _finite_number(payload.get("amount_reference"), minimum=0)
    delta = _finite_number(payload.get("amount_delta"), minimum=0.01)
    confidence = payload.get("confidence_class")
    if not (
        period and isinstance(cycles, int) and cycles >= 3
        and window in {3, 7} and isinstance(count, int) and count >= cycles * 2
        and after is not None and baseline is not None and delta is not None
        and round(after - baseline, 2) == delta
        and payload.get("temporal_correlation_only") is True
        and confidence in {"medium", "high"}
    ):
        return None
    return {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "post_income_pattern",
        **period,
        "cycles_used": cycles,
        "window_days": window,
        "amount_current": after,
        "amount_reference": baseline,
        "amount_delta": delta,
        "transaction_count": count,
        "temporal_correlation_only": True,
        "confidence_class": confidence,
        "title": "Ausgaben nach Geldeingang",
        "message": (
            f"In {cycles} erfassten Gehaltszyklen lagen Ausgaben in den ersten "
            f"{window} Tagen im Schnitt bei {_euro(after)} gegenüber "
            f"{_euro(baseline)} in deiner Vergleichsbasis. Das ist ein zeitlicher "
            "Zusammenhang, kein Beleg für eine Ursache."
        ),
    }


def _render_subscription_cluster(payload: dict[str, Any]) -> dict[str, Any] | None:
    category = _safe_category(payload.get("category_display"))
    period = _visible_period_payload(payload)
    count = payload.get("service_count")
    monthly_total = _finite_number(payload.get("monthly_normalized_total"), minimum=0.01)
    confidence = payload.get("confidence_class")
    duplicate_ambiguous = payload.get("duplicate_ambiguous")
    if not (
        category and period and isinstance(count, int) and count >= 2
        and monthly_total is not None and payload.get("frequency_complete") is True
        and payload.get("activity_confidence") == "verified"
        and payload.get("data_quality") == "complete"
        and isinstance(duplicate_ambiguous, bool)
        and confidence in {"medium", "high"}
    ):
        return None
    duplicate_note = (
        " Ähnliche Mehrfachzeilen wurden nicht als zusätzliche Servicefamilie gezählt."
        if duplicate_ambiguous else ""
    )
    return {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "subscription_cluster",
        **period,
        "category_display": category,
        "service_count": count,
        "monthly_normalized_total": monthly_total,
        "frequency_complete": True,
        "activity_confidence": "verified",
        "data_quality": "complete",
        "duplicate_ambiguous": duplicate_ambiguous,
        "confidence_class": confidence,
        "title": "Ähnliche laufende Verträge",
        "message": (
            f"{count} laufende Verträge sind als {category} eingeordnet; "
            f"der erfasste Monatswert beträgt {_euro(monthly_total)}. "
            f"Ähnlichkeit beweist kein Doppelabo oder Einsparpotenzial.{duplicate_note}"
        ),
    }


def _render_budget_recovery(payload: dict[str, Any]) -> dict[str, Any] | None:
    category = _safe_category(payload.get("category_display"))
    period = _visible_period_payload(payload)
    reference_start = _safe_period(payload.get("reference_period_start"))
    reference_end = _safe_period(payload.get("reference_period_end"))
    current = _finite_number(payload.get("amount_current"), minimum=0)
    limit = _finite_number(payload.get("budget_amount"), minimum=0.01)
    months_over = payload.get("months_over_budget")
    history = payload.get("historical_budget_evidence")
    count = payload.get("transaction_count")
    confidence = payload.get("confidence_class")
    if not (
        category and period and reference_start and reference_end
        and reference_start <= reference_end and current is not None and limit is not None
        and current <= limit and isinstance(months_over, int) and months_over >= 3
        and isinstance(history, list) and len(history) >= 3
        and isinstance(count, int) and count > 0
        and confidence in {"medium", "high"}
    ):
        return None
    return {
        "visible_behavior_contract_version": VISIBLE_BEHAVIOR_CONTRACT_VERSION,
        "insight_type": "budget_recovery",
        **period,
        "reference_period_start": reference_start,
        "reference_period_end": reference_end,
        "category_display": category,
        "amount_current": current,
        "budget_amount": limit,
        "months_over_budget": months_over,
        "historical_budget_evidence": history,
        "transaction_count": count,
        "confidence_class": confidence,
        "title": "Budget-Recovery bei " + category,
        "message": (
            f"Im abgeschlossenen Monat lagen die erfassten Ausgaben bei "
            f"{_euro(current)} von {_euro(limit)}. Zuvor lag {category} "
            f"in {months_over} Monaten über dem gesetzten Rahmen."
        ),
    }


def _render_visible_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not _source_payload_is_safe(payload):
        return None
    insight_type = payload.get("insight_type")
    if insight_type not in ALLOWED_VISIBLE_INSIGHT_TYPES:
        return None
    if insight_type == "budget_attention":
        return _render_budget_attention(payload)
    renderers = {
        "spending_trend_improving": _render_spending_improvement,
        "repeated_spending_pattern": _render_repeated_spending,
        "spending_trend_worsening": _render_spending_worsening,
        "post_income_pattern": _render_post_income,
        "subscription_cluster": _render_subscription_cluster,
        "budget_recovery": _render_budget_recovery,
    }
    renderer = renderers.get(insight_type)
    return renderer(payload) if renderer else None


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
    mode = _visible_gate_mode()
    if mode == "OFF":
        _record_skip("flag_off")
        return None
    if mode == "ON_SCOPED" and int(user_id) not in _allowlisted_user_ids():
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
