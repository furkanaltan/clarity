"""Gated delivery and deterministic explanations for existing Coach V4 insights.

This module does not detect patterns, calculate finances, or change the V4 gate.
It joins the existing validated insight contract to the existing visible DTO.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime
from typing import Any

from rove_behavior_patterns import (
    RELIABLE_TIMESTAMP_COLUMNS,
    SERVICE_FREQUENCY_ANNUAL,
    SERVICE_FREQUENCY_MONTHLY,
    build_shadow_inspector,
)
from rove_behavior_snapshot import compute_behavior_source_watermark
from rove_financial_insights import adapt_v4_shadow_candidate


VISIBLE_TYPES = frozenset({
    "budget_attention", "spending_trend_improving", "repeated_spending_pattern",
    "spending_trend_worsening", "post_income_pattern", "subscription_cluster",
    "budget_recovery",
})
MAX_EXAMPLE_SOURCE_IDS = 500
MAX_EXAMPLES = 5


def coach_insight_evidence_fingerprint(insight: dict[str, Any]) -> str:
    """Fingerprint only validated user-visible evidence, never snapshot time."""
    material = {
        "version": 1,
        "insight_id": insight.get("insight_id"),
        "insight_type": insight.get("insight_type"),
        "period": insight.get("period"),
        "facts": insight.get("facts"),
        "evidence": insight.get("evidence"),
        "claim_limits": insight.get("claim_limits"),
        "confidence": insight.get("confidence"),
    }
    encoded = json.dumps(
        material, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    if "confidence_class" in visible and visible["confidence_class"] != contract.get("confidence"):
        return None
    insight_id = contract.get("insight_id")
    if not isinstance(insight_id, str) or not re.fullmatch(r"v4:[A-Za-z0-9:_-]{1,200}", insight_id):
        return None
    dedupe_key = contract.get("dedupe_key")
    if dedupe_key != f"coach:{insight_type}":
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

    facts: dict[str, Any] = {"category": visible.get("category_display"), "period": period}
    safe_metrics: dict[str, Any] = {}
    summary = evidence.get("summary") if isinstance(evidence.get("summary"), dict) else {}
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
    elif insight_type in {"spending_trend_improving", "spending_trend_worsening"}:
        monthly = _monthly_values(metrics)
        if len(monthly) < 3 or monthly != visible.get("monthly_amounts"):
            return None
        facts.update({"monthly_amounts": monthly, "months_compared": len(monthly)})
        safe_metrics.update({"monthly_amounts": monthly, "months_compared": len(monthly)})
        if insight_type == "spending_trend_worsening":
            direction = summary.get("direction")
            delta = _first_number(metrics, "absolute_change_eur")
            months_compared = metrics.get("months_compared")
            if (
                direction != "worsening" or direction != visible.get("direction")
                or months_compared != len(monthly)
                or delta is None or not _same_money(delta, visible.get("amount_delta"))
            ):
                return None
            facts.update({
                "amount_reference": monthly[0]["amount"],
                "amount_current": monthly[-1]["amount"], "amount_delta": delta,
            })
            safe_metrics.update({"amount_delta": delta, "direction": direction})
    elif insight_type == "repeated_spending_pattern":
        weekday = metrics.get("weekday")
        opportunities = metrics.get("weekday_opportunities")
        ratio = _finite_number(metrics.get("occurrence_ratio"))
        occurrences = metrics.get("occurrence_date_count")
        total = _first_number(metrics, "amount_total")
        count = metrics.get("source_transaction_count")
        if not (
            isinstance(weekday, int) and not isinstance(weekday, bool) and 0 <= weekday <= 6
            and isinstance(opportunities, int) and opportunities == visible.get("weekday_opportunities")
            and isinstance(occurrences, int) and occurrences == visible.get("active_days")
            and isinstance(count, int) and count == visible.get("transaction_count")
            and ratio is not None and _same_money(ratio, visible.get("occurrence_ratio"))
            and total is not None and _same_money(total, visible.get("amount_current"))
            and weekday == visible.get("weekday")
        ):
            return None
        facts.update({
            "merchant": visible.get("merchant_display"), "weekday": weekday,
            "weekday_opportunities": opportunities, "occurrence_ratio": ratio,
            "active_days": occurrences, "transaction_count": count,
            "amount_current": total,
        })
        safe_metrics.update({key: facts[key] for key in (
            "weekday", "weekday_opportunities", "occurrence_ratio", "active_days",
            "transaction_count", "amount_current",
        )})
    elif insight_type == "post_income_pattern":
        cycles, window = metrics.get("cycles_used"), metrics.get("window_days")
        after = _first_number(metrics, "post_income_average_eur")
        baseline = _first_number(metrics, "personal_baseline_average_eur")
        delta = _first_number(metrics, "absolute_delta_eur")
        count = metrics.get("source_transaction_count")
        if not (
            isinstance(cycles, int) and cycles == visible.get("cycles_used") and cycles >= 3
            and isinstance(window, int) and window == visible.get("window_days") and window in {3, 7}
            and metrics.get("temporal_correlation_only") is True
            and visible.get("temporal_correlation_only") is True
            and after is not None and _same_money(after, visible.get("amount_current"))
            and baseline is not None and _same_money(baseline, visible.get("amount_reference"))
            and delta is not None and _same_money(delta, visible.get("amount_delta"))
            and round(after - baseline, 2) == round(delta, 2)
            and isinstance(count, int) and count == visible.get("transaction_count")
            and count >= cycles * 2
        ):
            return None
        facts.update({
            "cycles_used": cycles, "window_days": window, "amount_current": after,
            "amount_reference": baseline, "amount_delta": delta,
            "transaction_count": count, "temporal_correlation_only": True,
        })
        safe_metrics.update({key: facts[key] for key in (
            "cycles_used", "window_days", "amount_current", "amount_reference",
            "amount_delta", "transaction_count", "temporal_correlation_only",
        )})
    elif insight_type == "subscription_cluster":
        service_count = metrics.get("service_count")
        monthly_total = _first_number(metrics, "monthly_normalized_total")
        if not (
            isinstance(service_count, int) and service_count == visible.get("service_count") and service_count >= 2
            and monthly_total is not None and _same_money(monthly_total, visible.get("monthly_normalized_total"))
            and metrics.get("frequency_complete") is True and visible.get("frequency_complete") is True
            and metrics.get("activity_confidence") == "verified"
            and metrics.get("data_quality") == "complete"
            and metrics.get("duplicate_ambiguous") is visible.get("duplicate_ambiguous")
        ):
            return None
        facts.update({
            "service_count": service_count, "monthly_normalized_total": monthly_total,
            "frequency_complete": True, "activity_confidence": "verified",
            "data_quality": "complete", "duplicate_ambiguous": metrics["duplicate_ambiguous"],
        })
        safe_metrics.update({key: facts[key] for key in (
            "service_count", "monthly_normalized_total", "frequency_complete",
            "activity_confidence", "data_quality", "duplicate_ambiguous",
        )})
    elif insight_type == "budget_recovery":
        context = metrics.get("budget_context")
        history = metrics.get("historical_budget_evidence")
        months_over = metrics.get("months_over_budget")
        count = metrics.get("source_transaction_count")
        if not isinstance(context, dict) or not isinstance(history, list):
            return None
        spent = _first_number(context, "current_spent")
        limit = _first_number(context, "current_limit")
        expected_history = [
            {
                "month": row.get("month"),
                "amount_spent": _first_number(row, "amount_spent"),
                "monthly_limit": _first_number(row, "monthly_limit"),
                "amount_over": _first_number(row, "amount_over"),
            }
            for row in history if isinstance(row, dict) and row.get("over_budget") is True
        ]
        if not (
            context.get("comparable") is True and isinstance(months_over, int) and months_over >= 3
            and expected_history == visible.get("historical_budget_evidence")
            and len(expected_history) >= 3
            and spent is not None and _same_money(spent, visible.get("amount_current"))
            and limit is not None and _same_money(limit, visible.get("budget_amount"))
            and spent <= limit and isinstance(count, int) and count == visible.get("transaction_count")
            and count > 0
            and metrics.get("historical_period_start") == visible.get("reference_period_start")
            and metrics.get("historical_period_end") == visible.get("reference_period_end")
        ):
            return None
        facts.update({
            "amount_current": spent, "budget_amount": limit,
            "months_over_budget": months_over, "historical_budget_evidence": expected_history,
            "transaction_count": count,
            "reference_period": {
                "start": visible["reference_period_start"],
                "end": visible["reference_period_end"],
            },
        })
        safe_metrics.update({key: facts[key] for key in (
            "amount_current", "budget_amount", "months_over_budget",
            "historical_budget_evidence", "transaction_count",
        )})
    source_count = metrics.get("source_transaction_count")
    if isinstance(source_count, int) and not isinstance(source_count, bool) and 0 < source_count <= 10000:
        facts["transaction_count"] = source_count
        safe_metrics["source_transaction_count"] = source_count

    title, message = visible.get("title"), visible.get("message")
    if not isinstance(title, str) or not isinstance(message, str) or not title or not message:
        return None
    safe_summary = {
        key: value[:80]
        for key in ("pattern_type", "composite_type", "direction")
        if isinstance((value := summary.get(key)), str)
    }
    insight = {
        "insight_id": insight_id,
        "dedupe_key": dedupe_key,
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
    insight["evidence_fingerprint"] = coach_insight_evidence_fingerprint(insight)
    return insight


def v4_question_mode(message: str) -> str | None:
    text = " ".join(str(message or "").casefold().replace("ß", "ss").split())
    if any(word in text for word in ("warum", "wieso", "weshalb")):
        return "why"
    if re.search(r"\b(?:ausgaben?|buchungen?|posten|verträge|vertraege|abos|abonnements)\b", text) and (
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


def _display_date(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def _display_month(value: str) -> str:
    months = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember")
    parsed = datetime.strptime(value, "%Y-%m")
    return f"{months[parsed.month - 1]} {parsed.year}"


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
        if transactions.get("kind") == "contracts":
            if len(examples) > MAX_EXAMPLES:
                return "Die Vertragsdaten lassen sich gerade nicht sicher anzeigen. Bitte aktualisiere deine Übersicht und versuche es erneut."
            rendered = []
            for item in examples:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    return "Die Vertragsdaten lassen sich gerade nicht sicher anzeigen. Bitte aktualisiere deine Übersicht und versuche es erneut."
                frequency = "pro Monat" if item.get("frequency") == "monthly" else "pro Jahr" if item.get("frequency") == "annual" else None
                amount = _euro(item.get("amount"))
                if not frequency or not amount:
                    return "Die Vertragsdaten lassen sich gerade nicht sicher anzeigen. Bitte aktualisiere deine Übersicht und versuche es erneut."
                rendered.append(f"{item['name']} – {amount} {frequency}")
            return (
                f"Die aktuelle Prüfung umfasst {count} aktive Vertragszeilen. "
                + "; ".join(rendered)
                + ". Ähnliche Einträge beweisen weder ein unnötiges Doppelabo noch ein bestimmtes Einsparpotenzial."
            )
        expected_count = facts.get("transaction_count")
        if isinstance(expected_count, int) and expected_count != count:
            return "Die einzelnen Buchungen stimmen nicht mehr mit dem gespeicherten Hinweis überein. Bitte aktualisiere deine Übersicht und versuche es erneut."
        start, end = _display_date(period.get("start")), _display_date(period.get("end"))
        timeframe = f" vom {start} bis {end}" if start and end else ""
        lines = [f"Der Hinweis stützt sich auf {_booking_count(count)} für {category or 'diese Kategorie'}{timeframe}."]
        if examples:
            lines.append("Beispiele: " + "; ".join(
                f"{_display_date(item['date'])} – {item['label']} – {_euro(item['amount'])}"
                for item in examples if isinstance(item, dict)
            ) + ".")
        else:
            lines.append("Die Buchungen sind gezählt, aber nicht einzeln auflösbar.")
        lines.append("Das beschreibt die Datenbasis, nicht die Ursache der Ausgaben.")
        return " ".join(lines)
    if mode == "action":
        if insight.get("insight_type") == "budget_attention":
            return "Du kannst die zugehörigen Buchungen prüfen, weitere Ausgaben in dieser Kategorie planen oder deinen Budgetrahmen bei Bedarf selbst anpassen. Es wird nichts automatisch verändert."
        if insight.get("insight_type") == "repeated_spending_pattern":
            return "Wenn du möchtest, prüfe die genannten Tage und Buchungen. Du kannst für solche Bestellungen selbst einen Wochenrahmen festlegen; Rov.E ändert nichts automatisch."
        if insight.get("insight_type") == "spending_trend_worsening":
            return "Du kannst die Buchungen der Kategorie prüfen und entscheiden, ob sie zu deinem Plan passen. Eine Budgetänderung bleibt deine Entscheidung."
        if insight.get("insight_type") == "post_income_pattern":
            return "Wenn du möchtest, kannst du nach Geldeingang erst deine geplanten Ausgaben prüfen oder dir selbst einen Wochenrahmen setzen. Der zeitliche Zusammenhang erklärt nicht die Ursache."
        if insight.get("insight_type") == "subscription_cluster":
            return "Du kannst die aufgeführten Verträge einzeln darauf prüfen, ob du sie noch nutzt. Ähnlichkeit allein ist kein Kündigungsgrund; Rov.E ändert oder kündigt nichts."
        if insight.get("insight_type") == "budget_recovery":
            return "Wenn der aktuelle Rahmen zu deinem Alltag passt, kannst du deinen bisherigen Umgang beibehalten. Die Buchungen bleiben prüfbar; es wird nichts automatisch angepasst."
        return "Wenn der Verlauf deinem Ziel entspricht, kannst du deinen bisherigen Plan beibehalten. Alternativ prüfst du die Buchungen und entscheidest, ob du deinen Rahmen anpassen möchtest."
    if mode == "why":
        if insight.get("insight_type") == "budget_attention":
            amount = _euro(facts.get("amount_current"))
            limit = _euro(facts.get("budget_amount"))
            over = _euro(facts.get("amount_over_budget"))
            if not all((amount, limit, over, category, period.get("start"), period.get("end"))):
                return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            source_count = facts.get("transaction_count")
            count_clause = (
                f" Grundlage: {_booking_count(source_count)}."
                if isinstance(source_count, int) and not isinstance(source_count, bool)
                else ""
            )
            return (
                f"Der Hinweis bezieht sich auf {category} vom {_display_date(period['start'])} bis {_display_date(period['end'])}. "
                f"Die erfassten Ausgaben liegen bei {amount} gegenüber deinem Rahmen von {limit}"
                f"; die Abweichung beträgt {over}."
                f"{count_clause} Das zeigt eine Differenz, aber nicht ihre Ursache."
            )
        insight_type = insight.get("insight_type")
        if insight_type == "repeated_spending_pattern":
            start, end = _display_date(period.get("start")), _display_date(period.get("end"))
            weekday = facts.get("weekday")
            weekdays = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
            active_days, opportunities = facts.get("active_days"), facts.get("weekday_opportunities")
            count, amount = facts.get("transaction_count"), _euro(facts.get("amount_current"))
            subject = facts.get("merchant") or category
            if not (
                start and end and isinstance(weekday, int) and 0 <= weekday <= 6
                and isinstance(active_days, int) and isinstance(opportunities, int)
                and isinstance(count, int) and amount and subject
            ):
                return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            return (
                f"Vom {start} bis {end} gab es an {active_days} von {opportunities} "
                f"{weekdays[weekday]}en Buchungstage bei {subject}. Dabei wurden "
                f"{_booking_count(count)} über insgesamt {amount} erfasst. Das beschreibt "
                "eine Wiederholung, nicht ihre Ursache."
            )
        if insight_type == "spending_trend_worsening":
            monthly = facts.get("monthly_amounts")
            if not isinstance(monthly, list) or len(monthly) < 3 or not category:
                return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            source_count = facts.get("transaction_count")
            count_clause = f" Die Auswertung umfasst {_booking_count(source_count)}." if isinstance(source_count, int) else ""
            values = "; ".join(f"{_display_month(item['month'])}: {_euro(item['amount'])}" for item in monthly)
            return (
                f"Bei {category} stiegen die erfassten Ausgaben in aufeinanderfolgenden Monaten. "
                f"Die belegten Monatswerte sind {values}.{count_clause} Eine Ursache lässt sich daraus nicht ableiten."
            )
        if insight_type == "post_income_pattern":
            cycles, window = facts.get("cycles_used"), facts.get("window_days")
            after, baseline, delta = (_euro(facts.get(key)) for key in ("amount_current", "amount_reference", "amount_delta"))
            count = facts.get("transaction_count")
            start, end = _display_date(period.get("start")), _display_date(period.get("end"))
            if not (
                isinstance(cycles, int) and cycles >= 3 and isinstance(window, int)
                and after and baseline and delta and isinstance(count, int) and start and end
                and facts.get("temporal_correlation_only") is True
            ):
                return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            return (
                f"Vom {start} bis {end} wurden {cycles} Gehaltszyklen betrachtet. In den ersten "
                f"{window} Tagen lagen Ausgaben im Schnitt bei {after}, gegenüber {baseline} "
                f"in der persönlichen Vergleichsbasis; die ausgewiesene Differenz beträgt {delta}. "
                f"Grundlage sind {_booking_count(count)}. Das ist ein zeitlicher Zusammenhang, "
                "kein Beleg für eine Ursache."
            )
        if insight_type == "subscription_cluster":
            service_count = facts.get("service_count")
            monthly_total = _euro(facts.get("monthly_normalized_total"))
            if (
                not isinstance(service_count, int) or service_count < 2 or not monthly_total
                or facts.get("frequency_complete") is not True
                or facts.get("activity_confidence") != "verified"
            ):
                return "Die vorhandenen Vertragsdaten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            duplicate_note = (
                " Mehrere Zeilen derselben Servicefamilie wurden in der Anzahl nicht doppelt gezählt."
                if facts.get("duplicate_ambiguous") else ""
            )
            return (
                f"Die aktuelle Vertragsprüfung ordnet {service_count} aktive Services als {category or 'ähnlich'} ein. "
                f"Aus den eingetragenen Frequenzen ergibt sich ein Monatswert von {monthly_total}. "
                "Das belegt weder ein unnötiges Doppelabo noch eine mögliche Ersparnis."
                + duplicate_note
            )
        if insight_type == "budget_recovery":
            spent, limit = _euro(facts.get("amount_current")), _euro(facts.get("budget_amount"))
            history = facts.get("historical_budget_evidence")
            reference = facts.get("reference_period")
            count = facts.get("transaction_count")
            if not (
                category and spent and limit and isinstance(history, list) and len(history) >= 3
                and isinstance(reference, dict) and isinstance(count, int)
            ):
                return "Die vorhandenen Daten reichen nicht aus, um diesen Hinweis sicher zu erklären."
            history_rows = "; ".join(
                f"{_display_month(row['month'])}: {_euro(row['amount_spent'])} von {_euro(row['monthly_limit'])}"
                for row in history
            )
            return (
                f"Für {category} liegen im abgeschlossenen Monat {_booking_count(count)} mit {spent} "
                f"gegenüber einem Rahmen von {limit} vor. In den zuvor über dem Rahmen liegenden "
                f"Monaten waren die belegten Werte: {history_rows}. Das betrifft diese Kategorie; "
                "daraus folgt keine Aussage über dein Gesamtbudget oder die Ursache der Veränderung."
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
            f"Bei {category} sind die erfassten Ausgaben von {_euro(first['amount'])} "
            f"im {_display_month(first['month'])} auf {_euro(last['amount'])} im {_display_month(last['month'])} gesunken."
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

    if contract.get("insight_type") == "subscription_cluster":
        primary_pattern_id = raw.get("primary_pattern_id")
        contract_pattern_ids = (contract.get("evidence") or {}).get("source_pattern_ids")
        if (
            not isinstance(primary_pattern_id, str)
            or not isinstance(contract_pattern_ids, list)
            or primary_pattern_id not in contract_pattern_ids
        ):
            return None
        pattern = next((item for item in inspector.get("patterns", [])
                        if isinstance(item, dict) and item.get("pattern_id") == primary_pattern_id), None)
        observations = pattern.get("observations") if isinstance(pattern, dict) else None
        services = observations.get("services") if isinstance(observations, dict) else None
        if not isinstance(services, list) or not services:
            return None
        expected: dict[str, dict[str, Any]] = {}
        for service in services:
            if not isinstance(service, dict):
                return None
            ids = service.get("contract_ids")
            if (
                not isinstance(ids, list) or not ids
                or service.get("contract_row_count") != len(ids)
                or service.get("duplicate_ambiguous") is not (len(ids) > 1)
                or not isinstance(service.get("name"), str)
                or _finite_number(service.get("raw_amount_eur"),) is None
                or service.get("frequency") not in {"monthly", "annual"}
            ):
                return None
            for contract_id in ids:
                if not isinstance(contract_id, str) or not contract_id or len(contract_id) > 128 or contract_id in expected:
                    return None
                expected[contract_id] = service
        ids = sorted(expected)
        if not ids or len(ids) > MAX_EXAMPLE_SOURCE_IDS:
            return None
        columns = {str(row[1]) for row in conn.execute('PRAGMA table_info("app_contracts")')}
        frequency_column = next((name for name in (
            "frequency", "billing_frequency", "billing_cycle", "recurrence", "period",
        ) if name in columns), None)
        if not {"contract_id", "user_id", "name", "amount"}.issubset(columns) or not frequency_column:
            return None
        rows: list[sqlite3.Row | tuple] = []
        for offset in range(0, len(ids), 400):
            chunk = ids[offset:offset + 400]
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f'SELECT contract_id, name, amount, "{frequency_column}" AS raw_frequency FROM app_contracts '
                f"WHERE user_id=? AND contract_id IN ({marks})",
                (int(user_id), *chunk),
            ).fetchall())
        if len(rows) != len(ids):
            return None
        resolved = {}
        for row in rows:
            contract_id = str(row["contract_id"] if isinstance(row, sqlite3.Row) else row[0])
            name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
            amount = row["amount"] if isinstance(row, sqlite3.Row) else row[2]
            raw_frequency = row["raw_frequency"] if isinstance(row, sqlite3.Row) else row[3]
            service = expected.get(contract_id)
            if (
                service is None
                or not _same_money(amount, service["raw_amount_eur"])
            ):
                return None
            frequency_key = str(raw_frequency or "").strip().casefold()
            frequency = (
                "monthly" if frequency_key in SERVICE_FREQUENCY_MONTHLY
                else "annual" if frequency_key in SERVICE_FREQUENCY_ANNUAL
                else None
            )
            if not frequency:
                return None
            clean_name = " ".join(str(name).split())
            clean_name = "".join(char for char in clean_name if char >= " " and char != "\x7f")[:64]
            if not clean_name:
                return None
            resolved[contract_id] = {
                "name": clean_name,
                "amount": round(float(amount), 2),
                "frequency": frequency,
            }
        if compute_behavior_source_watermark(conn, int(user_id)) != source_watermark:
            return None
        return {
            "kind": "contracts",
            "count": len(resolved),
            "examples": [resolved[contract_id] for contract_id in ids[:MAX_EXAMPLES]],
        }

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
