"""Gated delivery and deterministic explanations for existing Coach V4 insights.

This module does not detect patterns, calculate finances, or change the V4 gate.
It joins the existing validated insight contract to the existing visible DTO.
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime
from typing import Any

from rove_behavior_patterns import RELIABLE_TIMESTAMP_COLUMNS, build_shadow_inspector
from rove_behavior_snapshot import compute_behavior_source_watermark
from rove_financial_insights import adapt_v4_shadow_candidate


VISIBLE_TYPES = frozenset({"budget_attention", "spending_trend_improving"})
MAX_EXAMPLE_SOURCE_IDS = 500
MAX_EXAMPLES = 5


def _period(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, str) or not isinstance(end, str) or start > end:
        return None
    try:
        datetime.fromisoformat(start)
        datetime.fromisoformat(end)
    except ValueError:
        return None
    return {"start": start, "end": end}


def _finite_number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _first_number(metrics: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite_number(metrics.get(key))
        if value is not None:
            return round(value, 2)
    return None


def _same_money(left: object, right: object) -> bool:
    a, b = _finite_number(left), _finite_number(right)
    return a is not None and b is not None and round(a, 2) == round(b, 2)


def _monthly_values(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    values = metrics.get("monthly_amounts") or metrics.get("monthly_values")
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        if not isinstance(item, dict):
            return []
        month, amount = item.get("month"), _finite_number(item.get("amount"))
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month) or amount is None:
            return []
        result.append({"month": month, "amount": round(amount, 2)})
    return result


def build_visible_mentor_insight(
    v3_candidate: object,
    contracts: object,
    visible_dto: object,
) -> dict[str, Any] | None:
    """Join the exact shared contract to the existing gated V4 presentation."""
    if v3_candidate or not isinstance(contracts, list) or len(contracts) != 1:
        return None
    contract, visible = contracts[0], visible_dto
    if not isinstance(contract, dict) or not isinstance(visible, dict):
        return None
    insight_type = contract.get("insight_type")
    if insight_type not in VISIBLE_TYPES or visible.get("insight_type") != insight_type:
        return None
    insight_id = contract.get("insight_id")
    if not isinstance(insight_id, str) or not re.fullmatch(r"v4:[A-Za-z0-9:_-]{1,200}", insight_id):
        return None
    period = _period(contract.get("period"))
    if not period or period != {
        "start": visible.get("period_start"),
        "end": visible.get("period_end"),
    }:
        return None
    evidence = contract.get("evidence")
    metrics = evidence.get("metrics") if isinstance(evidence, dict) else None
    if not isinstance(metrics, dict):
        return None

    facts: dict[str, Any] = {
        "category": visible.get("category_display"),
        "period": period,
    }
    safe_metrics: dict[str, Any] = {}
    if insight_type == "budget_attention":
        spent = _first_number(metrics, "amount_spent", "current_amount_eur", "budget_amount_spent")
        limit = _first_number(metrics, "monthly_limit", "budget_monthly_limit")
        over = _first_number(metrics, "amount_over", "budget_amount_over")
        if not (
            _same_money(spent, visible.get("amount_current"))
            and _same_money(limit, visible.get("budget_amount"))
            and _same_money(over, visible.get("amount_over_budget"))
            and visible.get("overall_budget_status") == "under_pressure"
        ):
            return None
        facts.update({"amount_current": spent, "budget_amount": limit, "amount_over_budget": over})
        safe_metrics.update({"amount_spent": spent, "monthly_limit": limit, "amount_over": over})
    else:
        monthly = _monthly_values(metrics)
        if len(monthly) < 3 or monthly != visible.get("monthly_amounts"):
            return None
        facts.update({"monthly_amounts": monthly, "months_compared": len(monthly)})
        safe_metrics.update({"monthly_amounts": monthly, "months_compared": len(monthly)})
    source_count = metrics.get("source_transaction_count")
    if isinstance(source_count, int) and not isinstance(source_count, bool) and 0 < source_count <= 10000:
        facts["transaction_count"] = source_count
        safe_metrics["source_transaction_count"] = source_count

    title, message = visible.get("title"), visible.get("message")
    if not isinstance(title, str) or not isinstance(message, str) or not title or not message:
        return None
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
    safe_summary = {
        key: value[:80]
        for key in ("pattern_type", "composite_type", "direction")
        if isinstance((value := summary.get(key)), str)
    }
    return {
        "insight_id": insight_id,
        "insight_type": insight_type,
        "title": title[:180],
        "message": message[:500],
        "period": period,
        "facts": facts,
        "evidence": {
            "summary": safe_summary,
            "metrics": safe_metrics,
        },
        "claim_limits": list(contract.get("claim_limits") or []),
        "confidence": contract.get("confidence"),
    }


def v4_question_mode(message: str) -> str | None:
    text = " ".join(str(message or "").casefold().replace("ß", "ss").split())
    if any(word in text for word in ("warum", "wieso", "weshalb")):
        return "why"
    if re.search(r"\b(?:ausgaben?|buchungen?|posten)\b", text) and (
        re.search(r"\b(?:welche|welchen|welcher|meinst|nenn|zeig|zeige)\b", text)
    ):
        return "transactions"
    if re.search(r"\bwas kann ich\b", text) and re.search(r"\b(?:aendern|ändern|konkret|machen|veraendern|verändern)\b", text):
        return "action"
    return None


def _euro(value: object) -> str:
    number = _finite_number(value)
    if number is None:
        return ""
    return f"{number:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".") + " €"


def _booking_count(value: int) -> str:
    return f"{value} Buchung" if value == 1 else f"{value} Buchungen"


def answer_v4_question(
    mode: str,
    insight: dict[str, Any],
    transactions: dict[str, Any] | None = None,
) -> str:
    """Answer only with validated visible facts and exact source references."""
    facts = insight.get("facts") if isinstance(insight.get("facts"), dict) else {}
    category = facts.get("category")
    period = facts.get("period") if isinstance(facts.get("period"), dict) else {}
    if mode == "transactions":
        if not isinstance(transactions, dict):
            return "Ich kann die einzelnen Buchungen zu diesem Hinweis gerade nicht sicher auflösen. Bitte aktualisiere deine Übersicht und versuche es erneut."
        count = transactions.get("count")
        examples = transactions.get("examples")
        if not isinstance(count, int) or count < 1 or not isinstance(examples, list):
            return "Für diesen Hinweis reichen die gespeicherten Daten nicht aus, um einzelne Buchungen sicher zu benennen."
        start, end = period.get("start"), period.get("end")
        lines = [f"Der Hinweis stützt sich auf {_booking_count(count)} für {category or 'diese Kategorie'} vom {start} bis {end}."]
        if examples:
            lines.append("Beispiele: " + "; ".join(
                f"{item['date']} – {item['label']} – {_euro(item['amount'])}"
                for item in examples if isinstance(item, dict)
            ) + ".")
        else:
            lines.append("Die Buchungen sind gezählt, aber nicht einzeln auflösbar.")
        lines.append("Das beschreibt die Datenbasis, nicht die Ursache der Ausgaben.")
        return " ".join(lines)
    if mode == "action":
        if insight.get("insight_type") == "budget_attention":
            return "Du kannst die aufgeführten Buchungen prüfen und danach selbst entscheiden, ob du deinen Rahmen für den nächsten Monat anpassen möchtest. Es wird nichts automatisch verändert."
        return "Du kannst die Monatswerte vergleichen oder deine bisherige Entwicklung beibehalten. Der Hinweis verlangt keine Änderung und erklärt keine Ursache."
    if mode == "why":
        if insight.get("insight_type") == "budget_attention":
            amount = _euro(facts.get("amount_current"))
            limit = _euro(facts.get("budget_amount"))
            over = _euro(facts.get("amount_over_budget"))
            if not all((amount, limit, over, category, period.get("start"), period.get("end"))):
                return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            source_count = facts.get("transaction_count")
            count_clause = (
                f" Sie stützt sich auf {_booking_count(source_count)}."
                if isinstance(source_count, int) and not isinstance(source_count, bool)
                else ""
            )
            return (
                f"Der Hinweis bezieht sich auf {category} vom {period['start']} bis {period['end']}. "
                f"Die belegte Ausgabe liegt bei {amount} gegenüber deinem Rahmen von {limit}"
                f"; die Abweichung beträgt {over}."
                f"{count_clause} Das zeigt eine Differenz, aber nicht ihre Ursache."
            )
        monthly = facts.get("monthly_amounts")
        if not isinstance(monthly, list) or len(monthly) < 3 or not category:
            return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
        first, last = monthly[0], monthly[-1]
        source_count = facts.get("transaction_count")
        count_clause = (
            f" Die Auswertung umfasst {_booking_count(source_count)}."
            if isinstance(source_count, int) and not isinstance(source_count, bool)
            else ""
        )
        return (
            f"Für {category} zeigt der Monatsverlauf "
            f"{_euro(first['amount'])} "
            f"im {first['month']} auf {_euro(last['amount'])} im {last['month']}. "
            f"{count_clause} "
            "Das beschreibt den Verlauf; eine Ursache lässt sich daraus nicht ableiten."
        )
    return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."


def resolve_v4_transaction_examples(
    conn: sqlite3.Connection,
    user_id: int,
    contract: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Re-run the existing detector read-only and resolve only its exact source IDs."""
    if contract.get("insight_type") not in VISIBLE_TYPES:
        return None
    raw_insight_id = str(contract.get("insight_id") or "")
    if not raw_insight_id.startswith("v4:"):
        return None
    source_watermark = compute_behavior_source_watermark(conn, int(user_id))
    inspector = build_shadow_inspector(conn, int(user_id), now=now)
    if compute_behavior_source_watermark(conn, int(user_id)) != source_watermark:
        return None
    raw = next((item for item in inspector.get("insight_candidates", [])
                if isinstance(item, dict) and item.get("insight_id") == raw_insight_id[3:]), None)
    if not raw or not raw.get("coach_eligible") or not raw.get("primary_coach_insight"):
        return None
    generated_at = (contract.get("freshness") or {}).get("generated_at")
    current_contract = adapt_v4_shadow_candidate(
        raw, generated_at=generated_at, source_watermark=source_watermark
    )
    if not current_contract or any(
        current_contract.get(key) != contract.get(key)
        for key in ("insight_id", "insight_type", "period", "evidence", "claim_limits")
    ):
        return None

    source_ids = raw.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        return None
    normalized_ids = []
    for value in source_ids:
        text = str(value)
        if not re.fullmatch(r"[1-9][0-9]{0,17}", text):
            return None
        normalized_ids.append(int(text))
    ids = sorted(set(normalized_ids))
    if len(ids) != len(source_ids):
        return None
    if len(ids) > MAX_EXAMPLE_SOURCE_IDS:
        return {"count": len(ids), "examples": []}

    columns = {str(row[1]) for row in conn.execute('PRAGMA table_info("expenses")')}
    if not {"id", "user_id", "amount"}.issubset(columns):
        return None
    timestamp = next((name for name in RELIABLE_TIMESTAMP_COLUMNS if name in columns), None)
    if timestamp and "created_at" in columns:
        event_time = f'COALESCE("{timestamp}", "created_at")'
    elif timestamp:
        event_time = f'"{timestamp}"'
    elif "created_at" in columns:
        event_time = '"created_at"'
    else:
        return None
    category = '"category"' if "category" in columns else "''"
    merchant = '"merchant"' if "merchant" in columns else "''"
    rows: list[sqlite3.Row | tuple] = []
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        marks = ",".join("?" for _ in chunk)
        rows.extend(conn.execute(
            f"SELECT id, amount, {category} AS category, {merchant} AS merchant, "
            f"{event_time} AS event_time FROM expenses WHERE user_id=? AND id IN ({marks})",
            (int(user_id), *chunk),
        ).fetchall())
    if len(rows) != len(ids):
        return None

    results = []
    for row in rows:
        amount = _finite_number(row["amount"] if isinstance(row, sqlite3.Row) else row[1])
        raw_time = row["event_time"] if isinstance(row, sqlite3.Row) else row[4]
        raw_label = row["merchant"] if isinstance(row, sqlite3.Row) else row[3]
        raw_category = row["category"] if isinstance(row, sqlite3.Row) else row[2]
        if amount is None or amount <= 0 or not raw_time:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        except ValueError:
            return None
        label = " ".join(str(raw_label or raw_category or "Ausgabe").split())
        label = "".join(char for char in label if char >= " " and char != "\x7f")[:64]
        if not label:
            label = "Ausgabe"
        results.append({"date": parsed.date().isoformat(), "label": label, "amount": round(amount, 2)})
    results.sort(key=lambda item: (item["date"], item["label"]))
    return {"count": len(ids), "examples": results[:MAX_EXAMPLES]}
