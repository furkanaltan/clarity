"""Deterministic ten-page story for Rov.E Report Snapshot V2.

This module is intentionally pure: it reads one frozen report payload and does
not query the database, call AI, or calculate market performance.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


REPORT_STORY_VERSION = 2
PAGE_COUNT = 10

CANDIDATE_TYPES = (
    "savings_goal_gap_vs_discretionary_delta",
    "budget_overrun_vs_flexible_category",
    "category_frequency_change",
    "merchant_concentration",
    "recurring_contract_cost",
    "investment_consistency",
    "positive_goal_completion",
    "positive_budget_and_saving",
    "stable_month",
)

NEXT_STEP_TYPES = (
    "observe",
    "maintain",
    "review",
    "adjust_budget",
    "continue_contribution",
    "close_goal_gap",
    "review_contract",
)

DISCRETIONARY_CATEGORIES = {
    "RESTAURANTS", "RESTAURANT", "SHOPPING", "FREIZEIT", "PFLEGE",
}
NECESSARY_CATEGORIES = {
    "LEBENSMITTEL", "MOBILITAET", "MOBILITÄT", "GESUNDHEIT",
    "DROGERIE", "WOHNEN", "MIETE", "VERSICHERUNG", "VERSICHERUNGEN",
}
CATEGORY_LABELS = {
    "LEBENSMITTEL": "Lebensmittel",
    "MOBILITAET": "Mobilität",
    "RESTAURANTS": "Restaurant",
    "RESTAURANT": "Restaurant",
    "ABOS": "Abos",
    "SHOPPING": "Shopping",
    "FREIZEIT": "Freizeit",
    "DROGERIE": "Drogerie",
    "GESUNDHEIT": "Gesundheit",
    "SONSTIGES": "Sonstiges",
    "PFLEGE": "Pflege",
    "WOHNEN": "Wohnen",
    "MIETE": "Wohnen",
    "VERSICHERUNG": "Versicherungen",
    "VERSICHERUNGEN": "Versicherungen",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _money(value: Any) -> float:
    return round(_number(value), 2)


def _pct(part: float, total: float) -> float | None:
    return round(part / total * 100, 1) if total > 0 else None


def _category_key(value: Any) -> str:
    key = str(value or "SONSTIGES").strip().upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")
    return {"RESTAURANT": "RESTAURANTS"}.get(key, key)


def category_class(value: Any) -> str:
    key = _category_key(value)
    if key in {_category_key(item) for item in DISCRETIONARY_CATEGORIES}:
        return "discretionary"
    if key in {_category_key(item) for item in NECESSARY_CATEGORIES}:
        return "necessary"
    return "unclear"


def category_label(value: Any) -> str:
    raw = str(value or "Sonstiges").strip()
    return CATEGORY_LABELS.get(_category_key(raw), raw.title())


def _change_label(current: float, previous: float) -> str:
    delta = round(current - previous, 2)
    if previous <= 0 and current > 0:
        return "neu in diesem Monat"
    direction = "mehr" if delta > 0 else "weniger"
    return f"{abs(delta):.2f} EUR {direction} als im Vormonat"


def _defensive_delta_pct(current: float, previous: float) -> float | None:
    if previous < 20:
        return None
    return round((current - previous) / previous * 100, 1)


def _previous_available(truth: dict) -> bool:
    expenses = truth.get("expenses") or {}
    previous = truth.get("previous_month") or {}
    if previous.get("comparison_mode") == "partial":
        return bool(
            _money(expenses.get("previous_total_consumption")) > 0
            or _integer(expenses.get("previous_transaction_count")) > 0
        )
    if "previous_total_consumption" in expenses:
        return bool(
            _money(expenses.get("previous_total_consumption")) > 0
            or _integer(expenses.get("previous_transaction_count")) > 0
            or previous.get("snapshot")
        )
    return bool(
        previous.get("snapshot")
        or any(_money(item.get("previous_amount")) > 0 for item in expenses.get("categories") or [])
    )


def _category_rows(truth: dict) -> list[dict]:
    total = _money((truth.get("expenses") or {}).get("total_consumption"))
    rows = []
    for raw in (truth.get("expenses") or {}).get("categories") or []:
        item = deepcopy(raw)
        item["category_key"] = str(item.get("category") or "SONSTIGES")
        item["category"] = category_label(item["category_key"])
        amount = _money(item.get("amount"))
        previous = _money(item.get("previous_amount"))
        count = _integer(item.get("transaction_count"))
        previous_count = _integer(item.get("previous_transaction_count"))
        share = _number(item.get("share"), _pct(amount, total) or 0)
        item.update({
            "amount": amount,
            "previous_amount": previous,
            "delta": round(amount - previous, 2),
            "delta_percent": _defensive_delta_pct(amount, previous),
            "transaction_count": count,
            "previous_transaction_count": previous_count,
            "transaction_count_delta": count - previous_count,
            "avg_transaction": _money(item.get("avg_transaction")),
            "share": round(share, 2),
            "class": category_class(item.get("category")),
        })
        magnitude = min(50.0, share)
        change = min(25.0, abs(item["delta"]) / max(total, 1) * 100)
        frequency = min(15.0, count * 2.0)
        budget_bonus = 10.0 if item.get("budget_over") else 0.0
        item["relevance_score"] = round(magnitude + change + frequency + budget_bonus, 2)
        rows.append(item)
    return sorted(rows, key=lambda item: (item["relevance_score"], item["amount"]), reverse=True)


def _merchant_rows(truth: dict) -> list[dict]:
    total = _money((truth.get("expenses") or {}).get("total_consumption"))
    rows = []
    for raw in (truth.get("expenses") or {}).get("merchants") or []:
        item = deepcopy(raw)
        item["category_key"] = str(item.get("category") or "SONSTIGES")
        item["category"] = category_label(item["category_key"])
        amount = _money(item.get("amount"))
        previous = _money(item.get("previous_amount"))
        count = _integer(item.get("transaction_count"))
        previous_count = _integer(item.get("previous_transaction_count"))
        item.update({
            "amount": amount,
            "previous_amount": previous,
            "delta": round(amount - previous, 2),
            "delta_percent": _defensive_delta_pct(amount, previous),
            "transaction_count": count,
            "previous_transaction_count": previous_count,
            "transaction_count_delta": count - previous_count,
            "avg_transaction": _money(item.get("avg_transaction")),
            "share": _pct(amount, total) or 0.0,
            "class": category_class(item.get("category")),
        })
        rows.append(item)
    return sorted(rows, key=lambda item: (item["amount"], item["transaction_count"]), reverse=True)


def _budget_by_category(truth: dict) -> dict[str, dict]:
    return {
        _category_key(item.get("category")): item
        for item in (truth.get("budget") or {}).get("items") or []
    }


def _annotate_budget(categories: list[dict], truth: dict) -> None:
    budgets = _budget_by_category(truth)
    for item in categories:
        budget = budgets.get(_category_key(item.get("category")))
        item["budget"] = deepcopy(budget) if budget else None
        item["budget_over"] = bool(budget and budget.get("over"))


def get_report_wealth(data: dict) -> dict:
    """Read wealth only from the immutable snapshot, including legacy V2 payloads."""
    truth = data.get("report_truth") or {}
    frozen = deepcopy(truth.get("wealth") or {})
    if frozen.get("total") is not None:
        frozen.setdefault("available", True)
        return frozen
    profile = data.get("profile") or {}
    cash = truth.get("cash") or {}
    property_truth = truth.get("property") or {}
    cash_value = cash.get("current_cash", profile.get("cash_reserve"))
    investments_value = profile.get("current_investments")
    property_value = property_truth.get("equity", profile.get("property_equity"))
    if cash_value is None or investments_value is None or property_value is None:
        return {
            "total": None,
            "cash": cash_value,
            "investments": investments_value,
            "property_equity": property_value,
            "allocation": [],
            "reconciles": False,
            "goals_included": False,
            "available": False,
        }
    cash_amount = _money(cash_value)
    investments = _money(investments_value)
    property_equity = _money(property_value)
    total = round(cash_amount + investments + property_equity, 2)
    allocation = []
    for key, label, amount in (
        ("cash", "Cash", cash_amount),
        ("investments", "Investments", investments),
        ("property", "Immobilien-Eigenkapital", property_equity),
    ):
        if amount > 0:
            allocation.append({
                "key": key,
                "label": label,
                "asset_class": key,
                "amount": amount,
                "share": _pct(amount, total) or 0.0,
                "source": "frozen_snapshot_fallback",
            })
    return {
        "total": total,
        "cash": cash_amount,
        "investments": investments,
        "property_equity": property_equity,
        "allocation": allocation,
        "reconciles": abs(sum(item["amount"] for item in allocation) - total) <= 0.01,
        "goals_included": False,
        "available": True,
    }


def _wealth(truth: dict, data: dict) -> dict:
    return get_report_wealth(data)


def _monthly_plan(data: dict) -> dict:
    journey = (data.get("pages") or {}).get("wealth_journey") or {}
    return {
        "execution": journey.get("monthly_execution") or {},
        "savings_progress": journey.get("savings_progress") or {},
        "planned_savings": _money((data.get("profile") or {}).get("savings_plan")),
    }


def _savings_context(data: dict, truth: dict) -> dict:
    plan = _monthly_plan(data)
    contributions = ((truth.get("investments") or {}).get("contributions") or {})
    actual = _money(contributions.get("net_contributions"))
    planned = plan["planned_savings"]
    return {
        "planned": planned,
        "actual": actual,
        "gap": round(max(0.0, planned - actual), 2),
        "goal_reached": planned > 0 and actual + 0.01 >= planned,
        "confirmed": bool((plan.get("execution") or {}).get("savings_confirmed")),
        "recurring": _money(contributions.get("recurring_in")),
    }


def _candidate(candidate_type: str, score: float, facts: list[str], fallback: str,
               *, safe: bool, tone: str, metrics: dict) -> dict:
    return {
        "type": candidate_type,
        "relevance_score": round(max(0.0, min(100.0, score)), 2),
        "facts": facts,
        "safe_to_coach": bool(safe),
        "suggested_tone": tone,
        "supporting_metrics": metrics,
        "fallback_text": fallback,
    }


def build_insight_candidates(data: dict, truth: dict, categories: list[dict],
                             merchants: list[dict]) -> list[dict]:
    expenses = truth.get("expenses") or {}
    total = _money(expenses.get("total_consumption"))
    income = _money((truth.get("income") or {}).get("amount"))
    savings = _savings_context(data, truth)
    budget = truth.get("budget") or {}
    previous_available = _previous_available(truth)
    candidates = []
    flexible_up = [item for item in categories if item["class"] == "discretionary" and item["delta"] > 0]
    flexible_up.sort(key=lambda item: item["delta"], reverse=True)

    if savings["gap"] > 0 and previous_available and flexible_up and flexible_up[0]["delta"] >= savings["gap"]:
        item = flexible_up[0]
        score = 55 + min(25, item["delta"] / max(savings["gap"], 1) * 10) + min(15, savings["gap"] / max(income, 1) * 100)
        fallback = (
            f"Dir fehlten {savings['gap']:.2f} EUR zu deiner geplanten Sparrate. "
            f"Gleichzeitig lagen deine Ausgaben in {item['category']} {item['delta']:.2f} EUR über dem Vormonat."
        )
        candidates.append(_candidate(
            "savings_goal_gap_vs_discretionary_delta", score,
            ["Sparraten-Lücke", "Flexible Kategorie im Vormonatsvergleich"], fallback,
            safe=True, tone="coach", metrics={"savings_gap": savings["gap"], "category": item["category"], "category_delta": item["delta"]},
        ))

    over_flexible = [item for item in categories if item["class"] == "discretionary" and item.get("budget_over")]
    if over_flexible:
        item = max(over_flexible, key=lambda row: _money((row.get("budget") or {}).get("used")) - _money((row.get("budget") or {}).get("limit")))
        over = round(_money(item["budget"].get("used")) - _money(item["budget"].get("limit")), 2)
        candidates.append(_candidate(
            "budget_overrun_vs_flexible_category", 55 + min(25, over / max(total, 1) * 100),
            ["Gesetztes Budget", "Flexible Kategorie"],
            f"{item['category']} lag {over:.2f} EUR über deinem gesetzten Budget.",
            safe=not savings["goal_reached"], tone="coach" if not savings["goal_reached"] else "neutral",
            metrics={"category": item["category"], "over_amount": over, "savings_goal_reached": savings["goal_reached"]},
        ))

    frequency_changes = [item for item in categories if previous_available and abs(item["transaction_count_delta"]) >= 2]
    if frequency_changes:
        item = max(frequency_changes, key=lambda row: abs(row["transaction_count_delta"]))
        direction = "mehr" if item["transaction_count_delta"] > 0 else "weniger"
        candidates.append(_candidate(
            "category_frequency_change", 35 + min(25, abs(item["transaction_count_delta"]) * 4),
            ["Buchungshäufigkeit im Vormonatsvergleich"],
            f"In {item['category']} hattest du {abs(item['transaction_count_delta'])} Buchungen {direction} als im Vormonat.",
            safe=item["class"] == "discretionary" and not savings["goal_reached"],
            tone="neutral" if item["class"] != "discretionary" else "coach",
            metrics={"category": item["category"], "transaction_count_delta": item["transaction_count_delta"], "class": item["class"]},
        ))

    concentrated = [item for item in merchants if item["transaction_count"] >= 3 and item["share"] >= 20]
    if concentrated:
        item = concentrated[0]
        candidates.append(_candidate(
            "merchant_concentration", 30 + min(35, item["share"]),
            ["Händleranteil", "Buchungshäufigkeit"],
            f"{item['merchant']} machte mit {item['transaction_count']} Buchungen {item['share']:.1f} % deiner Konsumausgaben aus.",
            safe=False, tone="neutral",
            metrics={"merchant": item["merchant"], "share": item["share"], "transaction_count": item["transaction_count"]},
        ))

    if savings["recurring"] > 0:
        candidates.append(_candidate(
            "investment_consistency", 45 + min(30, savings["recurring"] / max(income, 1) * 100),
            ["Dokumentierte wiederkehrende Investmentbeiträge"],
            f"Du hast {savings['recurring']:.2f} EUR über wiederkehrende Beiträge investiert.",
            safe=True, tone="positive",
            metrics={"recurring_contribution": savings["recurring"]},
        ))

    primary_goal = (truth.get("goals") or {}).get("primary")
    if primary_goal and _money(primary_goal.get("target_amount")) > 0 and _money(primary_goal.get("current_amount")) >= _money(primary_goal.get("target_amount")):
        candidates.append(_candidate(
            "positive_goal_completion", 75,
            ["Primäres Ziel vollständig gefüllt"],
            f"Dein Ziel {primary_goal.get('name') or 'Dein Ziel'} ist vollständig gefüllt.",
            safe=True, tone="positive",
            metrics={"goal_id": primary_goal.get("id"), "goal_name": primary_goal.get("name")},
        ))

    if budget.get("has_budgets") and budget.get("on_track") and savings["goal_reached"]:
        candidates.append(_candidate(
            "positive_budget_and_saving", 85,
            ["Gesetzte Budgets eingehalten", "Geplante Sparrate erreicht"],
            "Du hast deine gesetzten Budgets eingehalten und gleichzeitig deine geplante Sparrate erreicht.",
            safe=True, tone="positive",
            metrics={"actual_contribution": savings["actual"], "planned_savings": savings["planned"]},
        ))

    if not candidates:
        candidates.append(_candidate(
            "stable_month", 20,
            ["Keine belastbare Auffälligkeit"],
            "Dein Monat liefert aktuell keinen einzelnen Ausreißer, der wichtiger wäre als das Gesamtbild.",
            safe=False, tone="neutral", metrics={},
        ))
    return sorted(candidates, key=lambda item: (item["relevance_score"], item["type"]), reverse=True)


def _select_month_facts(data: dict, truth: dict, categories: list[dict], changes: list[dict]) -> list[dict]:
    expenses = truth.get("expenses") or {}
    contributions = ((truth.get("investments") or {}).get("contributions") or {})
    budget = truth.get("budget") or {}
    wealth = _wealth(truth, data)
    facts = []
    if changes:
        facts.append({
            "key": "notable_change",
            "label": changes[0]["label"],
            "value": changes[0]["context"],
            "priority": 10,
        })
    if _money(contributions.get("net_contributions")) > 0:
        facts.append({"key": "investment_contribution", "label": "Investiert", "value": _money(contributions.get("net_contributions")), "priority": 9})
    if budget.get("has_budgets"):
        facts.append({"key": "budget_status", "label": "Budgetstatus", "value": "im Rahmen" if budget.get("on_track") else "über Plan", "priority": 8})
    primary = (truth.get("goals") or {}).get("primary")
    if primary and _money(primary.get("target_amount")) > 0:
        progress = min(100.0, _number(primary.get("current_amount")) / _number(primary.get("target_amount")) * 100)
        facts.append({"key": "goal_progress", "label": str(primary.get("name") or "Ziel"), "value": round(progress, 1), "priority": 7})
    if categories:
        facts.append({"key": "top_category", "label": str(categories[0]["category"]), "value": categories[0]["amount"], "priority": 6})
    facts.append({"key": "total_consumption", "label": "Konsumausgaben", "value": _money(expenses.get("total_consumption")), "priority": 5})
    if _money(wealth.get("total")) > 0:
        facts.append({"key": "wealth_status", "label": "Gesamtvermögen", "value": wealth["total"], "priority": 4})
    return sorted(facts, key=lambda item: item["priority"], reverse=True)[:4]


def _comparison_changes(truth: dict, categories: list[dict], merchants: list[dict]) -> list[dict]:
    expenses = truth.get("expenses") or {}
    current_total = _money(expenses.get("total_consumption"))
    if "previous_total_consumption" in expenses:
        previous_total = _money(expenses.get("previous_total_consumption"))
        quality = "full"
    else:
        previous_total = round(sum(item["previous_amount"] for item in categories), 2)
        quality = "partial"
    threshold = max(20.0, max(current_total, previous_total) * 0.05)
    changes = []
    total_delta = round(current_total - previous_total, 2)
    if abs(total_delta) >= threshold:
        changes.append({
            "type": "total_consumption",
            "label": "Gesamtkonsum",
            "current": current_total,
            "previous": previous_total,
            "delta": total_delta,
            "delta_percent": _defensive_delta_pct(current_total, previous_total),
            "context": _change_label(current_total, previous_total),
        })
    current_contributions = _money(((truth.get("investments") or {}).get("contributions") or {}).get("net_contributions"))
    previous_contribution_data = (truth.get("previous_month") or {}).get("investment_contributions")
    if previous_contribution_data is not None:
        previous_contributions = _money(previous_contribution_data.get("net_contributions"))
        contribution_delta = round(current_contributions - previous_contributions, 2)
        if abs(contribution_delta) >= max(25.0, max(current_contributions, previous_contributions) * 0.05):
            changes.append({
                "type": "investment_contribution",
                "label": "Investmentbeiträge",
                "current": current_contributions,
                "previous": previous_contributions,
                "delta": contribution_delta,
                "delta_percent": _defensive_delta_pct(current_contributions, previous_contributions),
                "context": _change_label(current_contributions, previous_contributions),
            })
    score = truth.get("score") or {}
    previous_month = truth.get("previous_month") or {}
    previous_snapshot = previous_month.get("snapshot") or {}
    current_score = _integer(score.get("clarity_score", (score.get("parts") or {}).get("total")))
    previous_score = _integer(previous_snapshot.get("clarity_score"))
    if previous_month.get("comparison_mode") != "partial" and previous_score and abs(current_score - previous_score) >= 3:
        score_delta = current_score - previous_score
        changes.append({
            "type": "score",
            "label": "Rov.E Score",
            "current": current_score,
            "previous": previous_score,
            "delta": score_delta,
            "delta_percent": None,
            "context": f"{abs(score_delta)} Punkte {'höher' if score_delta > 0 else 'niedriger'} als im Vormonat",
        })
    for item in categories:
        if abs(item["delta"]) >= threshold or abs(item["transaction_count_delta"]) >= 2:
            changes.append({
                "type": "category",
                "label": item["category"],
                "current": item["amount"],
                "previous": item["previous_amount"],
                "delta": item["delta"],
                "delta_percent": item["delta_percent"],
                "transaction_count_delta": item["transaction_count_delta"],
                "context": _change_label(item["amount"], item["previous_amount"]),
            })
    for item in merchants[:5]:
        if abs(item["delta"]) >= threshold:
            changes.append({
                "type": "merchant",
                "label": item["merchant"],
                "current": item["amount"],
                "previous": item["previous_amount"],
                "delta": item["delta"],
                "delta_percent": item["delta_percent"],
                "context": _change_label(item["amount"], item["previous_amount"]),
            })
    changes.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return [{**item, "comparison_quality": quality} for item in changes[:5]]


def _next_steps(data: dict, truth: dict, categories: list[dict], insight: dict) -> list[dict]:
    savings = _savings_context(data, truth)
    budget = truth.get("budget") or {}
    steps = []
    if savings["gap"] > 0:
        steps.append({
            "type": "close_goal_gap",
            "title": "Sparraten-Lücke im Blick behalten",
            "text": f"Zu deiner geplanten Sparrate fehlten {savings['gap']:.2f} EUR.",
        })
    if insight["type"] in {"savings_goal_gap_vs_discretionary_delta", "budget_overrun_vs_flexible_category"}:
        category = insight["supporting_metrics"].get("category")
        steps.append({
            "type": "observe",
            "title": f"{category} beobachten",
            "text": f"Wenn deine Sparrate Priorität hat, ist {category} der naheliegendste flexible Bereich zum Beobachten.",
        })
    if savings["recurring"] > 0:
        steps.append({
            "type": "continue_contribution",
            "title": "Beitrag fortführen",
            "text": f"Dein wiederkehrender Beitrag von {savings['recurring']:.2f} EUR ist dokumentiert.",
        })
    if budget.get("has_budgets") and budget.get("on_track"):
        steps.append({
            "type": "maintain",
            "title": "Rahmen beibehalten",
            "text": "Deine gesetzten Budgets waren im Rahmen. Diese Struktur kannst du beibehalten.",
        })
    primary = (truth.get("goals") or {}).get("primary")
    if primary and _money(primary.get("target_amount")) > _money(primary.get("current_amount")) and len(steps) < 3:
        steps.append({
            "type": "review",
            "title": str(primary.get("name") or "Ziel") + " weiterführen",
            "text": f"Im Zieltopf fehlen noch {round(_money(primary.get('target_amount')) - _money(primary.get('current_amount')), 2):.2f} EUR.",
        })
    if not steps:
        steps.append({
            "type": "maintain",
            "title": "Monat weiter sichtbar halten",
            "text": "Behalte deine Buchungen und deinen Monatsplan weiter vollständig im Blick.",
        })
    unique = []
    seen = set()
    for step in steps:
        if step["type"] not in NEXT_STEP_TYPES or step["type"] in seen:
            continue
        seen.add(step["type"])
        unique.append(step)
    return unique[:3]


def _page(number: int, title: str, question: str, primary_metric: dict,
          *, supporting_metrics=None, visual=None, text="", empty_state=None,
          available=True) -> dict:
    return {
        "page_number": number,
        "title": title,
        "question": question,
        "primary_metric": primary_metric,
        "supporting_metrics": supporting_metrics or [],
        "visual": visual or {"type": "none", "data": []},
        "text": text,
        "empty_state": empty_state,
        "available": bool(available),
    }


def build_report_story_v2(report_data: dict) -> dict:
    truth = report_data.get("report_truth") or {}
    if not truth:
        raise ValueError("report_story_v2_requires_frozen_truth")
    categories = _category_rows(truth)
    _annotate_budget(categories, truth)
    categories.sort(key=lambda item: (item["relevance_score"] + (10 if item.get("budget_over") else 0), item["amount"]), reverse=True)
    merchants = _merchant_rows(truth)
    candidates = build_insight_candidates(report_data, truth, categories, merchants)
    insight = candidates[0]
    expenses = truth.get("expenses") or {}
    income = _money((truth.get("income") or {}).get("amount"))
    fixed = _money((truth.get("fixed_costs") or {}).get("amount"))
    consumption = _money(expenses.get("total_consumption"))
    contributions = ((truth.get("investments") or {}).get("contributions") or {})
    invested = _money(contributions.get("net_contributions"))
    difference = round(income - fixed - consumption - invested, 2) if income > 0 else None
    wealth = _wealth(truth, report_data)
    comparison_available = _previous_available(truth)
    changes = _comparison_changes(truth, categories, merchants) if comparison_available else []
    score = truth.get("score") or {}
    score_parts = score.get("parts") or {}
    factors = score_parts.get("factors") or []
    strongest_factor = max(factors, key=lambda item: _number(item.get("points")), default=None)
    weakest_factor = min(factors, key=lambda item: _number(item.get("points")), default=None)
    goals = truth.get("goals") or {}
    primary_goal = goals.get("primary")
    other_goals = [goal for goal in goals.get("goals") or [] if not goal.get("is_primary")][:4]
    holding_contributions = [
        {
            "holding_id": holding.get("id"),
            "name": holding.get("instrument_label") or "Investment",
            "asset_type": holding.get("instrument_type") or "investment",
            "amount": _money(holding.get("contribution")),
        }
        for holding in (truth.get("investments") or {}).get("holdings") or []
        if _money(holding.get("contribution")) != 0
    ]
    contribution_breakdown = holding_contributions or contributions.get("by_asset") or []
    facts = _select_month_facts(report_data, truth, categories, changes)
    merchant_patterns = []
    for item in merchants:
        if item["transaction_count"] >= 2:
            merchant_patterns.append(
                f"{item['merchant']}: {item['transaction_count']} Buchungen, insgesamt {item['amount']:.2f} EUR, durchschnittlich {item['avg_transaction']:.2f} EUR."
            )
        if len(merchant_patterns) == 2:
            break
    steps = _next_steps(report_data, truth, categories, insight)
    month_label = str((report_data.get("meta") or {}).get("month_label") or (report_data.get("meta") or {}).get("report_month") or "Dein Monat")

    pages = {
        "page_1": _page(1, "Dein Monat", "Was war diesen Monat wichtig?",
            {"semantic_key": "month_summary", "label": month_label, "value": len(facts)},
            supporting_metrics=facts, visual={"type": "fact_cards", "data": facts},
            text="Das sind die Fakten, die deinen Monat am stärksten geprägt haben."),
        "page_2": _page(2, "Dein Geldfluss", "Was kam rein, was ging raus und was blieb übrig?",
            {"semantic_key": "cashflow_difference", "label": "Differenz", "value": difference},
            supporting_metrics=[
                {"key": "income", "label": "Eingang", "value": income, "confirmed": bool((truth.get("income") or {}).get("confirmed"))},
                {"key": "fixed_costs", "label": "Fixkosten", "value": fixed, "confirmed": bool((truth.get("fixed_costs") or {}).get("confirmed"))},
                {"key": "consumption", "label": "Alltag & Konsum", "value": consumption},
                {"key": "invested", "label": "Investiert", "value": invested},
            ], visual={"type": "flow", "data": [income, fixed, consumption, invested, difference]},
            text="Die Differenz ist ein Geldfluss-Ergebnis, keine automatisch bestätigte Sparleistung.",
            empty_state="Für einen vollständigen Geldfluss fehlt ein verwendbarer Einkommenswert.", available=income > 0),
        "page_3": _page(3, "Deine Kategorien", "Wofür hast du dein Geld ausgegeben?",
            {"semantic_key": "top_category", "label": categories[0]["category"] if categories else "Keine Kategorie", "value": categories[0]["amount"] if categories else 0},
            supporting_metrics=categories[:5], visual={"type": "category_ranking", "data": categories[:5]},
            text="Betrag, Anteil, Buchungen und Durchschnitt basieren auf Konsumausgaben.",
            empty_state="Für diesen Monat liegen keine Konsumausgaben nach Kategorien vor.", available=bool(categories)),
        "page_4": _page(4, "Händler & Ausgabenmuster", "Wo konkret ist dein Geld gelandet und wie oft?",
            {"semantic_key": "top_merchant", "label": merchants[0]["merchant"] if merchants else "Keine Händlerdaten", "value": merchants[0]["amount"] if merchants else 0},
            supporting_metrics=merchants[:5], visual={"type": "merchant_ranking", "data": merchants[:5]},
            text=merchant_patterns[0] if merchant_patterns else "Noch kein belastbares Händlermuster in diesem Monat.",
            empty_state="Keine Händlerdaten vorhanden. Kategorien und Buchungshäufigkeit bleiben verfügbar.", available=bool(merchants)),
        "page_5": _page(5, "Was hat sich verändert?", "Was war anders als im Vormonat?",
            {"semantic_key": "month_comparison", "label": changes[0]["label"] if changes else "Noch kein belastbarer Vergleich", "value": changes[0]["delta"] if changes else None},
            supporting_metrics=changes, visual={"type": "change_list", "data": changes},
            text="Nur relevante absolute oder häufigkeitsbezogene Veränderungen werden gezeigt.",
            empty_state="Noch kein vollständiger Vormonat zum Vergleichen.", available=comparison_available),
        "page_6": _page(6, "Dein Vermögen", "Wo steckt dein Vermögen heute?",
            {"semantic_key": "net_worth", "label": "Gesamtvermögen", "value": wealth.get("total")},
            supporting_metrics=wealth.get("allocation") or [], visual={"type": "wealth_allocation", "data": wealth.get("allocation") or []},
            text="Zieltöpfe sind Zweckbindungen und werden nicht als zusätzliches Vermögen gezählt.",
            empty_state="Noch keine Vermögenswerte erfasst.", available=_money(wealth.get("total")) > 0),
        "page_7": _page(7, "Was hast du aufgebaut?", "Was hast du wirklich gespart oder investiert?",
            {"semantic_key": "investment_contributions", "label": "Dokumentierte Beiträge", "value": invested},
            supporting_metrics=contribution_breakdown,
            visual={"type": "contribution_breakdown", "data": contribution_breakdown},
            text=(f"Du hast {invested:.2f} EUR investiert. Es ist keine belastbare Marktbewegung verfügbar." if invested else "Für diesen Monat sind keine belastbaren Investmentbeiträge dokumentiert."),
            empty_state="Keine Investmentbeiträge dokumentiert; es wird keine Sparleistung erfunden.", available=invested != 0),
        "page_8": _page(8, "Score & Ziele", "Wie steht deine finanzielle Struktur und wie weit bist du bei deinen Zielen?",
            {"semantic_key": "rove_score", "label": "Rov.E Score", "value": _integer(score.get("clarity_score", score_parts.get("total")))},
            supporting_metrics=[
                {"key": "strongest_factor", "value": strongest_factor},
                {"key": "next_factor", "value": weakest_factor},
                {"key": "primary_goal", "value": primary_goal},
                {"key": "other_goals", "value": other_goals},
            ], visual={"type": "score_goal", "data": {"score": score, "primary_goal": primary_goal, "other_goals": other_goals}},
            text="Zielstände zeigen zugeordnetes Geld, keinen zusätzlichen Vermögensaufbau.",
            empty_state="Noch kein primäres Ziel ausgewählt.", available=bool(score or primary_goal)),
        "page_9": _page(9, "Rov.E Insight", "Welcher Zusammenhang war diesen Monat wirklich relevant?",
            {"semantic_key": "main_insight", "label": insight["type"], "value": insight["relevance_score"]},
            supporting_metrics=[insight.get("supporting_metrics") or {}], visual={"type": "single_insight", "data": [insight]},
            text=insight["fallback_text"], available=True),
        "page_10": _page(10, "Nächster Monat", "Was sind die sinnvollsten nächsten Schritte?",
            {"semantic_key": "next_month_priorities", "label": "Prioritäten", "value": len(steps)},
            supporting_metrics=steps, visual={"type": "next_steps", "data": steps},
            text="Maximal drei Schritte, direkt aus deinem abgeschlossenen Monat abgeleitet."),
    }
    primary_keys = [page["primary_metric"]["semantic_key"] for page in pages.values()]
    return {
        "story_version": REPORT_STORY_VERSION,
        "page_count": PAGE_COUNT,
        "source": "frozen_report_snapshot_v2",
        "report_month": (report_data.get("meta") or {}).get("report_month"),
        "pages": pages,
        "insight_engine": {
            "candidate_types": list(CANDIDATE_TYPES),
            "candidates": candidates,
            "selected": insight,
            "ranking": "deterministic",
        },
        "next_month_engine": {"allowed_types": list(NEXT_STEP_TYPES), "steps": steps},
        "ai_contract": {
            "enabled": False,
            "eligible_pages": [1, 9, 10],
            "backend_selects_facts": True,
            "ai_may_rephrase_only": True,
            "fallback_complete": True,
        },
        "quality": {
            "exactly_ten_pages": len(pages) == PAGE_COUNT,
            "unique_primary_metrics": len(primary_keys) == len(set(primary_keys)),
            "wealth_reconciles": bool(wealth.get("reconciles")),
            "previous_month_available": comparison_available,
            "merchant_data_available": bool(merchants),
            "investment_data_available": bool(invested or (truth.get("investments") or {}).get("holdings")),
            "primary_goal_available": bool(primary_goal),
        },
    }


def story_from_snapshot_data(report_data: dict) -> dict:
    """Reuse an embedded story or derive it from the same immutable payload."""
    embedded = report_data.get("report_story_v2")
    has_current_wealth_shape = (report_data.get("report_truth") or {}).get("wealth", {}).get("total") is not None
    if embedded and embedded.get("story_version") == REPORT_STORY_VERSION and has_current_wealth_shape:
        return deepcopy(embedded)
    return build_report_story_v2(report_data)
