"""Persistent, read-only delivery snapshots for Coach V4 shadow insights.

This module deliberately does not import the behavior engine on its read path.
The snapshot is a cache of an already computed result, never a second source of
financial truth.
"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any


SNAPSHOT_TABLE = "app_behavior_snapshot"
SNAPSHOT_VERSION = 1
BEHAVIOR_ENGINE_VERSION = "coach-v4-shadow-1"
BEHAVIOR_CONTRACT_VERSION = 1
SNAPSHOT_STATUS_READY = "ready"
SNAPSHOT_STATUS_STALE = "stale"
SNAPSHOT_STATUS_ERROR = "error"
SNAPSHOT_RECOMPUTE_IDLE = "idle"
SNAPSHOT_RECOMPUTE_PENDING = "pending"
SNAPSHOT_RECOMPUTE_RUNNING = "running"
SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60
SNAPSHOT_COUNTER_COLUMNS = (
    "metrics_invalidations_received",
    "metrics_invalidations_coalesced",
    "metrics_recomputes_avoided_by_coalescing",
    "metrics_recomputes_started",
    "metrics_recomputes_completed",
    "metrics_recomputes_failed",
    "metrics_recompute_duration_total_ms",
    "metrics_recompute_duration_count",
    "metrics_max_recompute_duration_ms",
    "metrics_patterns_detected",
    "metrics_insight_candidates",
    "metrics_coach_eligible",
    "metrics_coach_suppressed",
    "metrics_report_eligible",
    "metrics_report_suppressed",
    "metrics_primary_coach_candidates",
    "metrics_primary_report_candidates",
    "metrics_users_no_eligible_coach",
    "metrics_users_with_eligible_coach",
    "metrics_patterns_detected_no_insight",
    "metrics_insights_with_coach_suppressed",
    "metrics_coach_eligible_not_primary",
    "metrics_no_evidence_users",
)
SNAPSHOT_MAP_COLUMNS = (
    "metrics_suppression_reasons_json",
    "metrics_insight_types_json",
)
SNAPSHOT_METRIC_COLUMNS = SNAPSHOT_COUNTER_COLUMNS + SNAPSHOT_MAP_COLUMNS


def _now_text(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _row_value(row: sqlite3.Row | tuple | list, key: str, index: int) -> Any:
    return row[key] if isinstance(row, sqlite3.Row) else row[index]


def ensure_behavior_snapshot_table(conn: sqlite3.Connection) -> None:
    """Create the additive snapshot schema; callers decide when to migrate."""
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
            user_id INTEGER PRIMARY KEY,
            snapshot_version INTEGER NOT NULL,
            engine_version TEXT NOT NULL,
            behavior_contract_version INTEGER NOT NULL,
            generated_at TEXT,
            source_watermark TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('ready', 'stale', 'error')),
            stale_reason TEXT,
            primary_coach_insight_id TEXT,
            primary_report_insight_id TEXT,
            visible_coach_payload_json TEXT,
            recompute_state TEXT NOT NULL DEFAULT 'idle'
                CHECK(recompute_state IN ('idle', 'pending', 'running')),
            invalidation_version INTEGER NOT NULL DEFAULT 0,
            last_error_class TEXT,
            metrics_invalidations_received INTEGER NOT NULL DEFAULT 0,
            metrics_invalidations_coalesced INTEGER NOT NULL DEFAULT 0,
            metrics_recomputes_avoided_by_coalescing INTEGER NOT NULL DEFAULT 0,
            metrics_recomputes_started INTEGER NOT NULL DEFAULT 0,
            metrics_recomputes_completed INTEGER NOT NULL DEFAULT 0,
            metrics_recomputes_failed INTEGER NOT NULL DEFAULT 0,
            metrics_recompute_duration_total_ms REAL NOT NULL DEFAULT 0,
            metrics_recompute_duration_count INTEGER NOT NULL DEFAULT 0,
            metrics_max_recompute_duration_ms REAL NOT NULL DEFAULT 0,
            metrics_patterns_detected INTEGER NOT NULL DEFAULT 0,
            metrics_insight_candidates INTEGER NOT NULL DEFAULT 0,
            metrics_coach_eligible INTEGER NOT NULL DEFAULT 0,
            metrics_coach_suppressed INTEGER NOT NULL DEFAULT 0,
            metrics_report_eligible INTEGER NOT NULL DEFAULT 0,
            metrics_report_suppressed INTEGER NOT NULL DEFAULT 0,
            metrics_primary_coach_candidates INTEGER NOT NULL DEFAULT 0,
            metrics_primary_report_candidates INTEGER NOT NULL DEFAULT 0,
            metrics_users_no_eligible_coach INTEGER NOT NULL DEFAULT 0,
            metrics_users_with_eligible_coach INTEGER NOT NULL DEFAULT 0,
            metrics_patterns_detected_no_insight INTEGER NOT NULL DEFAULT 0,
            metrics_insights_with_coach_suppressed INTEGER NOT NULL DEFAULT 0,
            metrics_coach_eligible_not_primary INTEGER NOT NULL DEFAULT 0,
            metrics_no_evidence_users INTEGER NOT NULL DEFAULT 0,
            metrics_suppression_reasons_json TEXT NOT NULL DEFAULT '{{}}',
            metrics_insight_types_json TEXT NOT NULL DEFAULT '{{}}',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        f"""CREATE INDEX IF NOT EXISTS idx_{SNAPSHOT_TABLE}_queue
            ON {SNAPSHOT_TABLE}(recompute_state, updated_at)"""
    )


def _snapshot_columns(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, SNAPSHOT_TABLE):
        return set()
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{SNAPSHOT_TABLE}")')}


def _metrics_available(conn: sqlite3.Connection) -> bool:
    columns = _snapshot_columns(conn)
    return all(column in columns for column in SNAPSHOT_METRIC_COLUMNS)


def _record_recompute_metrics(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    outcome: str,
    started_perf: float,
) -> None:
    if not _metrics_available(conn):
        return
    duration_ms = max(0.0, (time.perf_counter() - started_perf) * 1000.0)
    conn.execute(
        f"""UPDATE {SNAPSHOT_TABLE}
               SET metrics_recompute_duration_total_ms =
                       metrics_recompute_duration_total_ms + ?,
                   metrics_recompute_duration_count =
                       metrics_recompute_duration_count + 1,
                   metrics_max_recompute_duration_ms = MAX(
                       metrics_max_recompute_duration_ms, ?
                   ),
                   metrics_recomputes_completed = metrics_recomputes_completed + ?,
                   metrics_recomputes_failed = metrics_recomputes_failed + ?
             WHERE user_id=?""",
        (
            duration_ms,
            duration_ms,
            1 if outcome == "completed" else 0,
            1 if outcome == "failed" else 0,
            int(user_id),
        ),
    )


def _safe_metric_key(value: object) -> str:
    key = str(value or "").strip().lower()
    return key if re.fullmatch(r"[a-z0-9_:-]{1,64}", key) else "__other__"


def _decode_metric_map(value: object) -> dict[str, int]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    result: dict[str, int] = {}
    for key, count in decoded.items():
        try:
            result[_safe_metric_key(key)] = max(0, int(count))
        except (TypeError, ValueError):
            continue
    return result


def _merge_metric_maps(
    current: dict[str, int],
    increments: dict[str, int],
    *,
    max_keys: int = 32,
) -> dict[str, int]:
    merged = dict(current)
    for key, count in increments.items():
        safe_key = _safe_metric_key(key)
        merged[safe_key] = merged.get(safe_key, 0) + max(0, int(count))
    if len(merged) <= max_keys:
        return dict(sorted(merged.items()))
    ranked = sorted(
        ((key, count) for key, count in merged.items() if key != "__other__"),
        key=lambda item: (-item[1], item[0]),
    )
    kept = dict(ranked[: max_keys - 1])
    kept["__other__"] = sum(
        count for key, count in merged.items() if key not in kept
    )
    return dict(sorted(kept.items()))


def summarize_behavior_inspector(inspector: dict[str, Any]) -> dict[str, Any]:
    """Reduce one shadow inspector result to bounded, non-PII counters."""
    patterns = inspector.get("patterns") or []
    insights = inspector.get("insight_candidates") or []
    patterns = patterns if isinstance(patterns, list) else []
    insights = insights if isinstance(insights, list) else []
    suppression_reasons: dict[str, int] = {}
    insight_types: dict[str, int] = {}
    coach_eligible = coach_suppressed = 0
    report_eligible = report_suppressed = 0
    primary_coach = primary_report = 0
    insights_with_coach_suppressed = 0
    coach_eligible_not_primary = 0
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        insight_type = insight.get("insight_type")
        if insight_type:
            safe_type = _safe_metric_key(insight_type)
            insight_types[safe_type] = insight_types.get(safe_type, 0) + 1
        coach_reason = insight.get("coach_suppression_reason")
        report_reason = insight.get("report_suppression_reason")
        if coach_reason:
            key = f"coach:{_safe_metric_key(coach_reason)}"
            suppression_reasons[key] = suppression_reasons.get(key, 0) + 1
            coach_suppressed += 1
            insights_with_coach_suppressed += 1
        if report_reason:
            key = f"report:{_safe_metric_key(report_reason)}"
            suppression_reasons[key] = suppression_reasons.get(key, 0) + 1
            report_suppressed += 1
        if insight.get("coach_eligible"):
            coach_eligible += 1
            if insight.get("primary_coach_insight"):
                primary_coach += 1
            else:
                coach_eligible_not_primary += 1
        if insight.get("report_eligible"):
            report_eligible += 1
            if insight.get("primary_report_insight"):
                primary_report += 1
    has_eligible_coach = coach_eligible > 0
    return {
        "patterns_detected": len(patterns),
        "insight_candidates": len(insights),
        "coach_eligible": coach_eligible,
        "coach_suppressed": coach_suppressed,
        "report_eligible": report_eligible,
        "report_suppressed": report_suppressed,
        "primary_coach_candidates": primary_coach,
        "primary_report_candidates": primary_report,
        "users_no_eligible_coach": int(not has_eligible_coach),
        "users_with_eligible_coach": int(has_eligible_coach),
        "patterns_detected_no_insight": int(bool(patterns) and not insights),
        "insights_with_coach_suppressed": insights_with_coach_suppressed,
        "coach_eligible_not_primary": coach_eligible_not_primary,
        "no_evidence_users": int(not patterns and not insights),
        "suppression_reasons": _merge_metric_maps({}, suppression_reasons),
        "insight_types": _merge_metric_maps({}, insight_types),
    }


def _record_insight_metrics(
    conn: sqlite3.Connection,
    user_id: int,
    inspector: dict[str, Any],
) -> None:
    """Persist only bounded aggregate shadow counters; never source evidence."""
    if not _metrics_available(conn):
        return
    try:
        summary = summarize_behavior_inspector(inspector)
        row = conn.execute(
            f"SELECT metrics_suppression_reasons_json, metrics_insight_types_json "
            f"FROM {SNAPSHOT_TABLE} WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        if not row:
            return
        suppression_map = _merge_metric_maps(
            _decode_metric_map(row[0]), summary["suppression_reasons"]
        )
        insight_type_map = _merge_metric_maps(
            _decode_metric_map(row[1]), summary["insight_types"]
        )
        columns = (
            "patterns_detected", "insight_candidates", "coach_eligible",
            "coach_suppressed", "report_eligible", "report_suppressed",
            "primary_coach_candidates", "primary_report_candidates",
            "users_no_eligible_coach", "users_with_eligible_coach",
            "patterns_detected_no_insight", "insights_with_coach_suppressed",
            "coach_eligible_not_primary", "no_evidence_users",
        )
        assignments = [
            f"metrics_{column}=metrics_{column}+?" for column in columns
        ]
        assignments.extend([
            "metrics_suppression_reasons_json=?",
            "metrics_insight_types_json=?",
        ])
        values = [summary[column] for column in columns]
        values.extend([
            json.dumps(suppression_map, sort_keys=True, separators=(",", ":")),
            json.dumps(insight_type_map, sort_keys=True, separators=(",", ":")),
            int(user_id),
        ])
        conn.execute(
            f"UPDATE {SNAPSHOT_TABLE} SET {', '.join(assignments)} WHERE user_id=?",
            values,
        )
    except (sqlite3.Error, TypeError, ValueError):
        # Observability must never change or block the shadow decision.
        return


def _aggregate(conn: sqlite3.Connection, table: str, user_id: int, time_columns: tuple[str, ...]) -> dict[str, Any]:
    if not _table_exists(conn, table):
        return {"table": table, "count": 0}
    columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
    selected_time = next((column for column in time_columns if column in columns), None)
    max_time = f", MAX({selected_time}) AS max_time" if selected_time else ""
    max_id = ", MAX(rowid) AS max_rowid" if "id" not in columns else ", MAX(id) AS max_id"
    amount_sum = ", ROUND(COALESCE(SUM(amount), 0), 2) AS amount_sum" if "amount" in columns else ""
    row = conn.execute(
        f"SELECT COUNT(*) AS row_count{max_id}{max_time}{amount_sum} FROM \"{table}\" WHERE user_id=?",
        (user_id,),
    ).fetchone()
    result: dict[str, Any] = {"table": table, "count": int(row["row_count"] if isinstance(row, sqlite3.Row) else row[0])}
    offset = 1
    if "id" in columns:
        result["max_id"] = row[offset] if row else None
        offset += 1
    else:
        result["max_rowid"] = row[offset] if row else None
        offset += 1
    if selected_time:
        result["max_time"] = row[offset] if row else None
        offset += 1
    if amount_sum:
        result["amount_sum"] = row[offset] if row else None
    return result


def _user_profile_watermark(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    if not _table_exists(conn, "users"):
        return {"table": "users", "profile_digest": None}
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(users)")}
    selected = [
        name for name in (
            "income", "other_income", "fixed_costs", "etf_savings",
            "cash_savings", "current_cash", "current_investments", "debt_status",
        ) if name in columns
    ]
    if not selected:
        return {"table": "users", "profile_digest": None}
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    values = [row[name] if isinstance(row, sqlite3.Row) else row[index] for index, name in enumerate(selected)] if row else []
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()
    return {"table": "users", "profile_digest": digest}


def compute_behavior_source_watermark(conn: sqlite3.Connection, user_id: int) -> str:
    """Return bounded metadata for relevant source changes, never source rows."""
    aggregates = [
        _user_profile_watermark(conn, user_id),
        _aggregate(conn, "expenses", user_id, ("transaction_at", "created_at")),
        _aggregate(conn, "app_cash_movements", user_id, ("occurred_at", "created_at")),
        _aggregate(conn, "category_budgets", user_id, ("updated_at", "active_month")),
        _aggregate(conn, "app_contracts", user_id, ("updated_at", "created_at")),
        _aggregate(conn, "monthly_financial_snapshots", user_id, ("updated_at", "report_month")),
    ]
    return json.dumps(aggregates, sort_keys=True, separators=(",", ":"), default=str)


def _ensure_row(conn: sqlite3.Connection, user_id: int, now: str) -> None:
    conn.execute(
        f"""INSERT OR IGNORE INTO {SNAPSHOT_TABLE}
           (user_id, snapshot_version, engine_version, behavior_contract_version,
            status, recompute_state, updated_at)
           VALUES (?, ?, ?, 0, 'stale', 'idle', ?)""",
        (user_id, SNAPSHOT_VERSION, BEHAVIOR_ENGINE_VERSION, now),
    )


def invalidate_behavior_snapshot(
    conn: sqlite3.Connection, user_id: int, reason: str
) -> bool:
    """Mark a user's snapshot stale without computing synchronously.

    The helper is intentionally a no-op before the explicit additive migration
    has run, so normal financial writes remain backwards compatible.
    """
    if not _table_exists(conn, SNAPSHOT_TABLE):
        return False
    now = _now_text()
    _ensure_row(conn, int(user_id), now)
    current = conn.execute(
        f"SELECT recompute_state FROM {SNAPSHOT_TABLE} WHERE user_id=?",
        (int(user_id),),
    ).fetchone()
    was_coalesced = bool(current and current[0] in {
        SNAPSHOT_RECOMPUTE_PENDING,
        SNAPSHOT_RECOMPUTE_RUNNING,
    })
    conn.execute(
        f"""UPDATE {SNAPSHOT_TABLE}
            SET status='stale', stale_reason=?, invalidation_version=invalidation_version+1,
                recompute_state=CASE WHEN recompute_state='running' THEN 'running' ELSE 'pending' END,
                updated_at=?
            WHERE user_id=?""",
        (str(reason)[:120], now, int(user_id)),
    )
    if _metrics_available(conn):
        conn.execute(
            f"""UPDATE {SNAPSHOT_TABLE}
                SET metrics_invalidations_received = metrics_invalidations_received + 1,
                    metrics_invalidations_coalesced = metrics_invalidations_coalesced + ?,
                    metrics_recomputes_avoided_by_coalescing =
                        metrics_recomputes_avoided_by_coalescing + ?
                WHERE user_id=?""",
            (
                1 if was_coalesced else 0,
                1 if was_coalesced and current[0] == SNAPSHOT_RECOMPUTE_PENDING else 0,
                int(user_id),
            ),
        )
    return True


def get_behavior_snapshot_status(
    conn: sqlite3.Connection, user_id: int, now: datetime | None = None
) -> dict[str, Any]:
    """Return non-sensitive lifecycle metadata for the authorized inspector."""
    if not _table_exists(conn, SNAPSHOT_TABLE):
        return {
            "status": "missing",
            "recompute_state": "idle",
            "snapshot_version": None,
            "engine_version": None,
            "behavior_contract_version": None,
            "generated_at": None,
            "stale_reason": "snapshot_table_missing",
        }
    row = conn.execute(
        f"""SELECT status, recompute_state, snapshot_version, engine_version,
                   behavior_contract_version, generated_at, stale_reason,
                   invalidation_version, last_error_class
              FROM {SNAPSHOT_TABLE} WHERE user_id=?""",
        (int(user_id),),
    ).fetchone()
    if not row:
        return {
            "status": "missing",
            "recompute_state": "idle",
            "snapshot_version": None,
            "engine_version": None,
            "behavior_contract_version": None,
            "generated_at": None,
            "stale_reason": "snapshot_row_missing",
        }
    result = {
        "status": _row_value(row, "status", 0),
        "recompute_state": _row_value(row, "recompute_state", 1),
        "snapshot_version": _row_value(row, "snapshot_version", 2),
        "engine_version": _row_value(row, "engine_version", 3),
        "behavior_contract_version": _row_value(row, "behavior_contract_version", 4),
        "generated_at": _row_value(row, "generated_at", 5),
        "stale_reason": _row_value(row, "stale_reason", 6),
        "invalidation_version": _row_value(row, "invalidation_version", 7),
        "last_error_class": _row_value(row, "last_error_class", 8),
    }
    generated = result.get("generated_at")
    if generated and result.get("status") == SNAPSHOT_STATUS_READY:
        try:
            stamp = datetime.fromisoformat(str(generated))
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            result["age_seconds"] = max(0, int((current - stamp).total_seconds()))
            result["ttl_expired"] = result["age_seconds"] > SNAPSHOT_MAX_AGE_SECONDS
        except ValueError:
            result["age_seconds"] = None
            result["ttl_expired"] = True
    else:
        result["age_seconds"] = None
        result["ttl_expired"] = False
    return result


def get_visible_behavior_snapshot(
    conn: sqlite3.Connection, user_id: int, now: datetime | None = None
) -> dict[str, Any] | None:
    """O(1) read of a current ready DTO; never recomputes or loads V4 data."""
    if not _table_exists(conn, SNAPSHOT_TABLE):
        return None
    row = conn.execute(
        f"""SELECT snapshot_version, engine_version, behavior_contract_version,
                   generated_at, status, source_watermark, visible_coach_payload_json
              FROM {SNAPSHOT_TABLE} WHERE user_id=?""",
        (int(user_id),),
    ).fetchone()
    if not row or _row_value(row, "status", 4) != SNAPSHOT_STATUS_READY:
        return None
    if int(_row_value(row, "snapshot_version", 0) or 0) != SNAPSHOT_VERSION:
        return None
    if str(_row_value(row, "engine_version", 1) or "") != BEHAVIOR_ENGINE_VERSION:
        return None
    if int(_row_value(row, "behavior_contract_version", 2) or 0) != BEHAVIOR_CONTRACT_VERSION:
        return None
    try:
        generated = datetime.fromisoformat(str(_row_value(row, "generated_at", 3)))
        current = now or datetime.now(timezone.utc)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if (current - generated).total_seconds() > SNAPSHOT_MAX_AGE_SECONDS:
            return None
        payload = json.loads(_row_value(row, "visible_coach_payload_json", 6) or "null")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _claim_recompute(conn: sqlite3.Connection, user_id: int, now: str) -> int | None:
    _ensure_row(conn, user_id, now)
    cursor = conn.execute(
        f"""UPDATE {SNAPSHOT_TABLE}
               SET recompute_state='running', updated_at=?
             WHERE user_id=? AND recompute_state != 'running'""",
        (now, user_id),
    )
    if cursor.rowcount != 1:
        return None
    if _metrics_available(conn):
        conn.execute(
            f"""UPDATE {SNAPSHOT_TABLE}
                SET metrics_recomputes_started = metrics_recomputes_started + 1
                WHERE user_id=?""",
            (int(user_id),),
        )
    row = conn.execute(
        f"SELECT invalidation_version FROM {SNAPSHOT_TABLE} WHERE user_id=?", (user_id,)
    ).fetchone()
    return int(row[0])


def recompute_behavior_snapshot(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bounded recompute entry point used by a future/background worker."""
    started_perf = time.perf_counter()
    effective_now = (now or datetime.now()).replace(tzinfo=None)
    started_at = _now_text()
    if not _table_exists(conn, "users"):
        return {"status": "error", "error_class": "missing_users_table"}
    ensure_behavior_snapshot_table(conn)
    claim = _claim_recompute(conn, int(user_id), started_at)
    if claim is None:
        return {"status": "already_running"}
    before = compute_behavior_source_watermark(conn, int(user_id))
    try:
        from rove_behavior_patterns import (
            VISIBLE_BEHAVIOR_CONTRACT_VERSION,
            build_shadow_inspector,
            build_visible_behavior_insight,
        )

        inspector = build_shadow_inspector(conn, int(user_id), now=effective_now)
        _record_insight_metrics(conn, int(user_id), inspector)
        after = compute_behavior_source_watermark(conn, int(user_id))
        row = conn.execute(
            f"SELECT invalidation_version FROM {SNAPSHOT_TABLE} WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        if int(row[0]) != claim or after != before:
            conn.execute(
                f"""UPDATE {SNAPSHOT_TABLE}
                    SET status='stale', stale_reason='source_changed_during_recompute',
                        recompute_state='pending', updated_at=? WHERE user_id=?""",
                (_now_text(), int(user_id)),
            )
            _record_recompute_metrics(
                conn, int(user_id), outcome="completed", started_perf=started_perf
            )
            return {"status": "stale", "reason": "source_changed_during_recompute"}

        insights = inspector.get("insight_candidates") or []
        primary = next((item for item in insights if item.get("primary_coach_insight")), None)
        payload = build_visible_behavior_insight(primary) if primary else None
        primary_report = next((item for item in insights if item.get("primary_report_insight")), None)
        generated_at = _now_text()
        conn.execute(
            f"""UPDATE {SNAPSHOT_TABLE}
                SET snapshot_version=?, engine_version=?, behavior_contract_version=?,
                    generated_at=?, source_watermark=?, status='ready', stale_reason=NULL,
                    primary_coach_insight_id=?, primary_report_insight_id=?,
                    visible_coach_payload_json=?, recompute_state='idle',
                    last_error_class=NULL, updated_at=?
                WHERE user_id=? AND invalidation_version=?""",
            (
                SNAPSHOT_VERSION,
                BEHAVIOR_ENGINE_VERSION,
                int(VISIBLE_BEHAVIOR_CONTRACT_VERSION),
                generated_at,
                after,
                primary.get("insight_id") if primary else None,
                primary_report.get("insight_id") if primary_report else None,
                json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) if payload else None,
                generated_at,
                int(user_id),
                claim,
            ),
        )
        _record_recompute_metrics(
            conn, int(user_id), outcome="completed", started_perf=started_perf
        )
        return {"status": "ready", "has_visible_coach_payload": bool(payload)}
    except Exception as exc:  # never persist provider/source data or traceback text
        source_changed = False
        try:
            current_version = conn.execute(
                f"SELECT invalidation_version FROM {SNAPSHOT_TABLE} WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            source_changed = int(current_version[0]) != claim
            if not source_changed:
                source_changed = compute_behavior_source_watermark(conn, int(user_id)) != before
        except sqlite3.Error:
            # The safe fallback is still fail-closed; it does not publish a ready row.
            source_changed = False
        next_status = SNAPSHOT_STATUS_STALE if source_changed else SNAPSHOT_STATUS_ERROR
        next_reason = "source_changed_during_recompute" if source_changed else "recompute_failed"
        next_state = SNAPSHOT_RECOMPUTE_PENDING if source_changed else SNAPSHOT_RECOMPUTE_IDLE
        conn.execute(
            f"""UPDATE {SNAPSHOT_TABLE}
                SET status=?, stale_reason=?, recompute_state=?,
                    last_error_class=?, updated_at=?
                WHERE user_id=?""",
            (next_status, next_reason, next_state, type(exc).__name__, _now_text(), int(user_id)),
        )
        _record_recompute_metrics(
            conn, int(user_id), outcome="failed", started_perf=started_perf
        )
        return {
            "status": "stale" if source_changed else "error",
            "reason": next_reason,
            "error_class": type(exc).__name__,
        }


def process_pending_behavior_snapshots(
    conn: sqlite3.Connection, *, limit: int = 20, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Process a bounded queue; SQLite CAS prevents duplicate active recomputes."""
    if not _table_exists(conn, SNAPSHOT_TABLE):
        return []
    batch_limit = max(1, min(int(limit), 20))
    rows = conn.execute(
        f"""SELECT user_id FROM {SNAPSHOT_TABLE}
             WHERE recompute_state='pending' ORDER BY updated_at LIMIT ?""",
        (batch_limit,),
    ).fetchall()
    return [
        {"user_id": int(row[0]), **recompute_behavior_snapshot(conn, int(row[0]), now=now)}
        for row in rows
    ]


def _age_seconds(value: object, current: datetime) -> int | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current - stamp).total_seconds()))


def get_behavior_snapshot_metrics(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    """Return queue gauges and aggregate counters without recomputing any user."""
    empty = {
        "metrics_available": False,
        "sql_query_count": 0,
        "pending_users": 0,
        "running_users": 0,
        "ready_users": 0,
        "stale_users": 0,
        "error_users": 0,
        "oldest_pending_age_seconds": None,
        "average_pending_age_seconds": None,
        "recomputes_started": 0,
        "recomputes_completed": 0,
        "recomputes_failed": 0,
        "average_recompute_duration_ms": None,
        "max_recompute_duration_ms": None,
        "invalidations_received": 0,
        "invalidations_coalesced": 0,
        "recomputes_avoided_by_coalescing": 0,
        "coalescing_rate": 0.0,
        "patterns_detected": 0,
        "insight_candidates": 0,
        "coach_eligible": 0,
        "coach_suppressed": 0,
        "report_eligible": 0,
        "report_suppressed": 0,
        "primary_coach_candidates": 0,
        "primary_report_candidates": 0,
        "users_no_eligible_coach": 0,
        "users_with_eligible_coach": 0,
        "patterns_detected_no_insight": 0,
        "insights_with_coach_suppressed": 0,
        "coach_eligible_not_primary": 0,
        "no_evidence_users": 0,
        "suppression_reasons": {"coach": {}, "report": {}},
        "insight_types": {},
        "coach_eligibility_rate": 0.0,
        "coach_suppression_rate": 0.0,
        "report_eligibility_rate": 0.0,
        "report_suppression_rate": 0.0,
        "primary_coach_rate": 0.0,
        "no_eligible_coach_user_rate": 0.0,
        "top_suppression_reasons": [],
    }
    selected = ["recompute_state", "status", "updated_at", *SNAPSHOT_METRIC_COLUMNS]
    query_count = 0
    try:
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM {SNAPSHOT_TABLE}"
        ).fetchall()
        metrics_available = True
        query_count = 1
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            empty["sql_query_count"] = 1
            return empty
        rows = conn.execute(
            f"SELECT recompute_state, status, updated_at FROM {SNAPSHOT_TABLE}"
        ).fetchall()
        metrics_available = False
        query_count = 2
    current = now or datetime.now(timezone.utc)
    pending_ages = [
        age for row in rows
        if row[0] == SNAPSHOT_RECOMPUTE_PENDING
        for age in [_age_seconds(row[2], current)]
        if age is not None
    ]
    result = {
        **empty,
        "metrics_available": metrics_available,
        "sql_query_count": query_count,
        "pending_users": sum(row[0] == SNAPSHOT_RECOMPUTE_PENDING for row in rows),
        "running_users": sum(row[0] == SNAPSHOT_RECOMPUTE_RUNNING for row in rows),
        "ready_users": sum(row[1] == SNAPSHOT_STATUS_READY for row in rows),
        "stale_users": sum(row[1] == SNAPSHOT_STATUS_STALE for row in rows),
        "error_users": sum(row[1] == SNAPSHOT_STATUS_ERROR for row in rows),
        "oldest_pending_age_seconds": max(pending_ages) if pending_ages else None,
        "average_pending_age_seconds": round(sum(pending_ages) / len(pending_ages), 2)
        if pending_ages else None,
    }
    if not metrics_available:
        return result

    offsets = {name: 3 + index for index, name in enumerate(SNAPSHOT_METRIC_COLUMNS)}
    totals = {
        name: sum(int(row[offsets[name]] or 0) for row in rows)
        for name in SNAPSHOT_COUNTER_COLUMNS
    }
    duration_total = sum(float(row[offsets["metrics_recompute_duration_total_ms"]] or 0) for row in rows)
    duration_count = sum(int(row[offsets["metrics_recompute_duration_count"]] or 0) for row in rows)
    max_duration = max(
        (float(row[offsets["metrics_max_recompute_duration_ms"]] or 0) for row in rows),
        default=0.0,
    )
    received = totals["metrics_invalidations_received"]
    coalesced = totals["metrics_invalidations_coalesced"]
    suppression_map: dict[str, int] = {}
    insight_type_map: dict[str, int] = {}
    for row in rows:
        suppression_map = _merge_metric_maps(
            suppression_map,
            _decode_metric_map(row[offsets["metrics_suppression_reasons_json"]]),
        )
        insight_type_map = _merge_metric_maps(
            insight_type_map,
            _decode_metric_map(row[offsets["metrics_insight_types_json"]]),
        )
    suppression_reasons = {"coach": {}, "report": {}}
    for key, count in suppression_map.items():
        if key.startswith("coach:"):
            suppression_reasons["coach"][key[6:]] = count
        elif key.startswith("report:"):
            suppression_reasons["report"][key[7:]] = count
    insight_candidates = totals["metrics_insight_candidates"]
    coach_eligible = totals["metrics_coach_eligible"]
    coach_suppressed = totals["metrics_coach_suppressed"]
    report_eligible = totals["metrics_report_eligible"]
    report_suppressed = totals["metrics_report_suppressed"]
    user_observations = (
        totals["metrics_users_no_eligible_coach"]
        + totals["metrics_users_with_eligible_coach"]
    )
    top_suppression_reasons = [
        {"scope": scope, "reason": reason, "count": count}
        for scope, values in suppression_reasons.items()
        for reason, count in values.items()
    ]
    top_suppression_reasons.sort(key=lambda item: (-item["count"], item["scope"], item["reason"]))
    result.update({
        "recomputes_started": totals["metrics_recomputes_started"],
        "recomputes_completed": totals["metrics_recomputes_completed"],
        "recomputes_failed": totals["metrics_recomputes_failed"],
        "average_recompute_duration_ms": round(duration_total / duration_count, 2)
        if duration_count else None,
        "max_recompute_duration_ms": round(max_duration, 2) if duration_count else None,
        "invalidations_received": received,
        "invalidations_coalesced": coalesced,
        "recomputes_avoided_by_coalescing": totals[
            "metrics_recomputes_avoided_by_coalescing"
        ],
        "coalescing_rate": round(coalesced / received, 4) if received else 0.0,
        "patterns_detected": totals["metrics_patterns_detected"],
        "insight_candidates": insight_candidates,
        "coach_eligible": coach_eligible,
        "coach_suppressed": coach_suppressed,
        "report_eligible": report_eligible,
        "report_suppressed": report_suppressed,
        "primary_coach_candidates": totals["metrics_primary_coach_candidates"],
        "primary_report_candidates": totals["metrics_primary_report_candidates"],
        "users_no_eligible_coach": totals["metrics_users_no_eligible_coach"],
        "users_with_eligible_coach": totals["metrics_users_with_eligible_coach"],
        "patterns_detected_no_insight": totals["metrics_patterns_detected_no_insight"],
        "insights_with_coach_suppressed": totals["metrics_insights_with_coach_suppressed"],
        "coach_eligible_not_primary": totals["metrics_coach_eligible_not_primary"],
        "no_evidence_users": totals["metrics_no_evidence_users"],
        "suppression_reasons": suppression_reasons,
        "insight_types": insight_type_map,
        "coach_eligibility_rate": round(coach_eligible / insight_candidates, 4)
        if insight_candidates else 0.0,
        "coach_suppression_rate": round(coach_suppressed / insight_candidates, 4)
        if insight_candidates else 0.0,
        "report_eligibility_rate": round(report_eligible / insight_candidates, 4)
        if insight_candidates else 0.0,
        "report_suppression_rate": round(report_suppressed / insight_candidates, 4)
        if insight_candidates else 0.0,
        "primary_coach_rate": round(
            totals["metrics_primary_coach_candidates"] / coach_eligible, 4
        ) if coach_eligible else 0.0,
        "no_eligible_coach_user_rate": round(
            totals["metrics_users_no_eligible_coach"] / user_observations, 4
        ) if user_observations else 0.0,
        "top_suppression_reasons": top_suppression_reasons,
    })
    return result


def delete_behavior_snapshot(conn: sqlite3.Connection, user_id: int) -> None:
    if _table_exists(conn, SNAPSHOT_TABLE):
        conn.execute(f"DELETE FROM {SNAPSHOT_TABLE} WHERE user_id=?", (int(user_id),))


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Manage the additive Coach V4 snapshot table")
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--process-pending", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--user-id", type=int)
    args = parser.parse_args()
    with sqlite3.connect(args.db_path) as connection:
        connection.row_factory = sqlite3.Row
        ensure_behavior_snapshot_table(connection)
        if args.user_id:
            print(recompute_behavior_snapshot(connection, args.user_id))
        elif args.process_pending:
            print(process_pending_behavior_snapshots(connection, limit=args.limit))
        else:
            print(f"migrated={SNAPSHOT_TABLE}")
