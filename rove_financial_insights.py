"""Shared, deterministic contract for V3 mentor and V4 shadow insights.

This module adapts existing decisions and evidence only. It does not calculate
financial values, rank V3 candidates, or make V4 candidates user-visible.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any


INSIGHT_CONTRACT_VERSION = 1
MAX_SHADOW_CANDIDATES = 1
MAX_SHADOW_PAYLOAD_BYTES = 16 * 1024
_METRICS_LOCK = threading.Lock()
_METRICS: Counter[str] = Counter()

_V3_TYPE_MAP = {
    "budget_overrun": "budget_attention",
}
_SENSITIVE_EVIDENCE_KEYS = frozenset({
    "source_ids", "diagnostic_source_ids", "merchant", "salary_source_label",
    "user_id", "account_id", "iban", "email", "name", "label", "token",
    "transaction_id", "expense_id", "movement_id", "row_id", "raw_id",
})
_ALLOWED_V4_SUMMARY_KEYS = frozenset({
    "pattern_type", "composite_type", "category", "direction",
})
_EXPENSE_EVIDENCE_TYPES = frozenset({
    "budget_attention", "repeated_spending_pattern", "spending_trend_improving",
    "spending_trend_worsening", "post_income_pattern", "budget_recovery",
})


def _now_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _safe_evidence(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Keep bounded evidence values while excluding direct identifiers."""
    if key.casefold() in _SENSITIVE_EVIDENCE_KEYS or depth > 5:
        return None
    if value is None or isinstance(value, (bool, str, int)):
        if isinstance(value, str):
            return value[:160]
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for child_key, child_value in list(value.items())[:48]:
            if not isinstance(child_key, str):
                continue
            if child_key.casefold() in _SENSITIVE_EVIDENCE_KEYS:
                continue
            child = _safe_evidence(child_value, key=child_key, depth=depth + 1)
            if child is not None:
                safe[child_key[:80]] = child
        return safe
    if isinstance(value, (list, tuple)):
        return [
            child for item in value[:24]
            if (child := _safe_evidence(item, depth=depth + 1)) is not None
        ]
    return None


def _period(start: Any, end: Any) -> dict[str, str | None] | None:
    if start is not None and not isinstance(start, str):
        return None
    if end is not None and not isinstance(end, str):
        return None
    if not start or not end or start > end:
        return None
    try:
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
    except ValueError:
        return None
    return {"start": start, "end": end}


def _dedupe_key(insight_type: str) -> str:
    # One active V3 mentor item already represents the next action; a V4
    # variant of the same insight type stays shadow-only and is not duplicated.
    return f"coach:{insight_type}"


def adapt_v3_mentor_candidate(
    candidate: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Wrap the existing V3 result without changing its priority or copy."""
    if not isinstance(candidate, dict):
        return None
    source_id = candidate.get("id")
    source_type = candidate.get("type")
    reason = candidate.get("reason")
    try:
        priority = int(candidate.get("priority"))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not isinstance(source_id, str) or not source_id or len(source_id) > 180
        or not isinstance(source_type, str) or not source_type or len(source_type) > 80
        or not isinstance(reason, str) or not reason or len(reason) > 180
        or priority < 0 or priority > 1000
    ):
        return None
    insight_type = _V3_TYPE_MAP.get(source_type, source_type)
    return {
        "contract_version": INSIGHT_CONTRACT_VERSION,
        "insight_id": f"v3:{source_id}",
        "producer": "coach_v3",
        "source_version": "3",
        "insight_type": insight_type,
        "source_type": source_type,
        "period": None,
        "evidence": {"source": "canonical_v3_candidate", "basis": reason},
        "freshness": {"status": "current_request", "generated_at": _now_iso(now)},
        "priority": {"value": priority, "basis": "existing_v3_priority"},
        "claim_limits": ["preserve_existing_v3_statement"],
        "explanation": {
            "title": str(candidate.get("title") or "")[:180],
            "text": str(candidate.get("message") or "")[:500],
            "action_label": str(candidate.get("action_label") or "")[:100],
            "deep_link": str(candidate.get("deep_link") or "")[:100],
        },
        "dedupe_key": _dedupe_key(insight_type),
        "visibility": "active_v3_unchanged",
    }


def adapt_v4_shadow_candidate(
    insight: dict[str, Any],
    *,
    generated_at: str,
    source_watermark: str,
) -> dict[str, Any] | None:
    """Wrap one already-arbitrated V4 candidate; fail closed on weak evidence."""
    if not isinstance(insight, dict):
        return None
    if (
        insight.get("coach_eligible") is not True
        or insight.get("primary_coach_insight") is not True
        or insight.get("conflicting_evidence") is True
        or insight.get("superseded_by_pattern_id")
        or insight.get("coach_suppression_reason")
    ):
        return None
    insight_id = insight.get("insight_id")
    insight_type = insight.get("insight_type")
    strength = insight.get("pattern_strength")
    relevance = insight.get("financial_relevance")
    confidence = insight.get("confidence_class")
    evidence_summary = insight.get("evidence_summary")
    evidence_metrics = insight.get("evidence_metrics")
    period = _period(insight.get("period_start"), insight.get("period_end"))
    if (
        not isinstance(insight_id, str) or not insight_id or len(insight_id) > 180
        or not isinstance(insight_type, str) or not insight_type or len(insight_type) > 80
        or strength not in {"medium", "high"}
        or relevance not in {"medium", "high"}
        or confidence not in {"medium", "high"}
        or not isinstance(evidence_summary, dict) or not isinstance(evidence_metrics, dict)
        or not evidence_metrics or period is None
    ):
        return None
    summary = {
        key: str(evidence_summary[key])[:120]
        for key in _ALLOWED_V4_SUMMARY_KEYS
        if evidence_summary.get(key) is not None
    }
    metrics = _safe_evidence(evidence_metrics)
    if not isinstance(metrics, dict) or not metrics:
        return None
    raw_sources = insight.get("source_ids")
    if (
        insight_type in _EXPENSE_EVIDENCE_TYPES
        and isinstance(raw_sources, (list, tuple))
        and 0 < len(raw_sources) <= 10000
        and all(isinstance(source_id, (str, int)) for source_id in raw_sources)
    ):
        # Count-only evidence is safe to share; raw booking identifiers remain
        # excluded from the persisted contract and are resolved only on demand.
        metrics["source_transaction_count"] = len({str(source_id) for source_id in raw_sources})
    raw_pattern_ids = insight.get("source_pattern_ids")
    if not isinstance(raw_pattern_ids, (list, tuple)):
        return None
    pattern_ids = sorted({
        str(pattern_id)[:180]
        for pattern_id in raw_pattern_ids
        if isinstance(pattern_id, str) and pattern_id
    })[:12]
    if not pattern_ids:
        return None
    claim_limits = ["descriptive_only", "no_causal_claims", "no_new_financial_calculation"]
    if insight_type == "post_income_pattern":
        if metrics.get("temporal_correlation_only") is not True:
            return None
        claim_limits.append("temporal_correlation_only")
    watermark_digest = hashlib.sha256(source_watermark.encode("utf-8")).hexdigest()
    try:
        generated = datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return {
        "contract_version": INSIGHT_CONTRACT_VERSION,
        "insight_id": f"v4:{insight_id}",
        "producer": "coach_v4_shadow",
        "source_version": "4-shadow",
        "insight_type": insight_type,
        "period": period,
        "evidence": {
            "summary": summary,
            "metrics": metrics,
            "source_pattern_ids": pattern_ids,
        },
        "freshness": {
            "status": "snapshot_validated_on_read",
            "generated_at": generated.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "max_age_seconds": 24 * 60 * 60,
            "source_watermark_sha256": watermark_digest,
        },
        "priority": {"value": None, "basis": "existing_v4_primary_coach_arbitration"},
        "confidence": confidence,
        "claim_limits": claim_limits,
        "explanation": {"evidence_summary": summary},
        "dedupe_key": _dedupe_key(insight_type),
        "visibility": "shadow_only",
    }


def build_v4_shadow_contracts(
    insights: list[dict[str, Any]],
    *,
    generated_at: str,
    source_watermark: str,
) -> list[dict[str, Any]]:
    """Persist at most the V4 engine's already-selected primary coach insight."""
    if not isinstance(insights, list):
        return []
    primaries = [
        item for item in insights
        if isinstance(item, dict) and item.get("primary_coach_insight") is True
    ]
    if len(primaries) != 1:
        return []
    contract = adapt_v4_shadow_candidate(
        primaries[0], generated_at=generated_at, source_watermark=source_watermark
    )
    if contract is None:
        return []
    encoded = json.dumps(contract, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SHADOW_PAYLOAD_BYTES:
        return []
    return [contract]


def build_shadow_insight_path(
    v3_candidate: dict[str, Any] | None,
    v4_candidates: list[dict[str, Any]],
    *,
    snapshot_status: str,
    profile_mode: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Join contracts for internal shadow comparison; never chooses V4 for display."""
    v3 = adapt_v3_mentor_candidate(v3_candidate, now=now)
    valid_modes = {"legacy", "multi_account"}
    mode = profile_mode if profile_mode in valid_modes else "unknown"
    accepted: list[dict[str, Any]] = []
    duplicate_count = 0
    if snapshot_status == "ready" and isinstance(v4_candidates, list):
        for candidate in v4_candidates[:MAX_SHADOW_CANDIDATES]:
            if (
                not isinstance(candidate, dict)
                or candidate.get("contract_version") != INSIGHT_CONTRACT_VERSION
                or candidate.get("producer") != "coach_v4_shadow"
                or candidate.get("visibility") != "shadow_only"
                or not isinstance(candidate.get("insight_id"), str)
                or not isinstance(candidate.get("evidence"), dict)
                or not candidate.get("evidence")
                or not isinstance(candidate.get("freshness"), dict)
                or not isinstance(candidate.get("priority"), dict)
                or not isinstance(candidate.get("claim_limits"), list)
                or not isinstance(candidate.get("dedupe_key"), str)
                or candidate.get("dedupe_key") != _dedupe_key(candidate.get("insight_type", ""))
                or _period(
                    (candidate.get("period") or {}).get("start")
                    if isinstance(candidate.get("period"), dict) else None,
                    (candidate.get("period") or {}).get("end")
                    if isinstance(candidate.get("period"), dict) else None,
                ) is None
            ):
                continue
            if v3 and candidate.get("dedupe_key") == v3.get("dedupe_key"):
                duplicate_count += 1
                continue
            accepted.append(candidate)
    return {
        "contract_version": INSIGHT_CONTRACT_VERSION,
        "mode": "shadow",
        "profile_mode": mode,
        "active_v3_candidate": v3,
        "v4_candidates": accepted,
        "v4_snapshot_status": snapshot_status,
        "v4_deduplicated_against_v3": duplicate_count,
    }


def record_shadow_insight_evaluation(result: dict[str, Any]) -> None:
    """Record bounded, non-user-identifying process metrics for admin inspection."""
    with _METRICS_LOCK:
        _METRICS["evaluations"] += 1
        _METRICS[f"profile:{result.get('profile_mode', 'unknown')}"] += 1
        _METRICS[f"snapshot:{result.get('v4_snapshot_status', 'unknown')}"] += 1
        if result.get("active_v3_candidate"):
            _METRICS["v3_candidate_present"] += 1
        _METRICS["v4_candidates_kept"] += len(result.get("v4_candidates") or [])
        _METRICS["v4_deduplicated_against_v3"] += int(
            result.get("v4_deduplicated_against_v3") or 0
        )


def get_shadow_insight_metrics() -> dict[str, Any]:
    with _METRICS_LOCK:
        values = dict(sorted(_METRICS.items()))
    return {"scope": "process_local", "counts": values}


def reset_shadow_insight_metrics() -> None:
    with _METRICS_LOCK:
        _METRICS.clear()
