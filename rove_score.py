"""Shared, database-backed Rov.E Score and habit-points logic.

The app, reports and the remaining Telegram bridge must describe the same financial
state. This module keeps the calculation independent from Flask and bot handlers.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta


SCORE_RANKS = [
    (0, 44, "Rookie", "🥚"),
    (45, 54, "Stratege", "🔍"),
    (55, 64, "Controller", "📊"),
    (65, 74, "Investor", "🧱"),
    (75, 84, "Manager", "🏗️"),
    (85, 92, "Kapitalist", "🏛️"),
    (93, 100, "Rov.E Elite", "💎"),
]

POINT_RANKS = [
    (0, "Rookie", "🥚"),
    (50, "Stratege", "🔍"),
    (200, "Controller", "📊"),
    (500, "Investor", "🧱"),
    (1000, "Manager", "🏗️"),
    (2500, "Kapitalist", "🏛️"),
    (5000, "Rov.E Elite", "💎"),
]


def _value(row, key: str, default=0):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _number(row, key: str) -> float:
    try:
        return float(_value(row, key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(row, key: str) -> int:
    try:
        return int(_value(row, key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def score_rank(score: int) -> tuple[str, str]:
    for low, high, name, icon in SCORE_RANKS:
        if low <= score <= high:
            return name, icon
    return SCORE_RANKS[-1][2], SCORE_RANKS[-1][3]


def points_rank(points: int) -> dict:
    current_name, current_icon = POINT_RANKS[0][1], POINT_RANKS[0][2]
    next_threshold = None
    next_name = None
    for threshold, name, icon in POINT_RANKS:
        if points >= threshold:
            current_name, current_icon = name, icon
        elif next_threshold is None:
            next_threshold = threshold
            next_name = name
    return {
        "value": points,
        "rank_name": current_name,
        "rank_icon": current_icon,
        "to_next": max(0, next_threshold - points) if next_threshold else 0,
        "next_rank": next_name or "",
    }


def platform_days(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> int:
    row = conn.execute(
        "SELECT MIN(DATE(created_at)) AS first_day FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()
    first_day = _value(row, "first_day", "") if row else ""
    try:
        first_date = date.fromisoformat(str(first_day))
    except ValueError:
        return 0
    return max(1, ((today or date.today()) - first_date).days + 1)


def tracking_days_90(conn: sqlite3.Connection, user_id: int, today: date | None = None) -> int:
    since = ((today or date.today()) - timedelta(days=89)).isoformat()
    row = conn.execute(
        """SELECT COUNT(DISTINCT DATE(created_at)) AS days
             FROM expenses
             WHERE user_id = ? AND DATE(created_at) >= DATE(?)""",
        (user_id, since),
    ).fetchone()
    return _int(row, "days") if row else 0


def savings_confirmed(conn: sqlite3.Connection, user_id: int, report_month: str) -> bool:
    if _table_exists(conn, "user_badges"):
        badge_key = f"inv_{report_month.replace('-', '_')}"
        if conn.execute(
            "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_key = ?",
            (user_id, badge_key),
        ).fetchone():
            return True
    if not _table_exists(conn, "investment_events"):
        return False
    return conn.execute(
        """SELECT 1 FROM investment_events
             WHERE user_id = ?
               AND source IN ('investiert_command', 'app_monthly_plan')
               AND strftime('%Y-%m', created_at) = ?
             LIMIT 1""",
        (user_id, report_month),
    ).fetchone() is not None


def score_cap(days: int) -> tuple[int, int, int]:
    if days < 30:
        return 59, max(0, 30 - days), 60
    if days < 60:
        return 69, 60 - days, 70
    if days < 90:
        return 79, 90 - days, 80
    if days < 180:
        return 85, 180 - days, 86
    if days < 365:
        return 92, 365 - days, 93
    return 100, 0, 100


def start_score(user) -> int:
    income = _number(user, "income") + _number(user, "other_income")
    fixed = _number(user, "fixed_costs")
    savings = _number(user, "etf_savings") + _number(user, "cash_savings")
    investments = _number(user, "current_investments")
    cash = _number(user, "current_cash")
    savings_ratio = savings / income if income > 0 else 0

    score = 30
    if savings > 0:
        score += 5
    if savings_ratio >= 0.10:
        score += 5
    if savings_ratio >= 0.20:
        score += 5
    if investments > 0:
        score += 5
    if fixed > 0 and cash >= fixed * 3:
        score += 7
    elif fixed > 0 and cash >= fixed:
        score += 3
    if (
        _int(user, "onboarding_step") >= 10
        and income > 0
        and fixed >= 0
        and bool(str(_value(user, "goal_description", "")).strip())
        and _number(user, "goal_amount") > 0
    ):
        score += 5
    return min(score, 65)


def savings_points(savings_ratio: float, confirmed: bool) -> int:
    if confirmed and savings_ratio >= 0.20:
        return 25
    if confirmed and savings_ratio >= 0.15:
        return 18
    if confirmed and savings_ratio >= 0.10:
        return 12
    if savings_ratio >= 0.20:
        return 10
    if savings_ratio >= 0.15:
        return 6
    if savings_ratio >= 0.10:
        return 3
    return 0


def _factor(key: str, name: str, points: int, tint: str, why: str, lever: str) -> dict:
    return {
        "key": key,
        "n": name,
        "points": points,
        "max": 25,
        "p": round(points / 25 * 100),
        "v": f"{points}/25",
        "tint": tint,
        "why": why,
        "lever": lever,
    }


def calculate_score(
    conn: sqlite3.Connection,
    user_id: int,
    user,
    total_expenses: float | None = None,
    report_month: str | None = None,
    today: date | None = None,
) -> dict:
    """Calculate the Rov.E Score from one DB connection and return UI-safe factors."""
    today = today or date.today()
    report_month = report_month or today.strftime("%Y-%m")
    if total_expenses is None:
        row = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS total FROM expenses
                 WHERE user_id = ? AND strftime('%Y-%m', created_at) = ?""",
            (user_id, report_month),
        ).fetchone()
        total_expenses = _number(row, "total") if row else 0.0
    total_expenses = max(0.0, float(total_expenses or 0))

    income = _number(user, "income") + _number(user, "other_income")
    fixed = _number(user, "fixed_costs")
    free_budget = income - fixed
    remaining = free_budget - total_expenses
    savings_amount = _number(user, "etf_savings") + _number(user, "cash_savings")
    savings_ratio = savings_amount / income if income > 0 else 0.0
    cash = _number(user, "current_cash")

    budget = 0
    if free_budget > 0:
        budget = 25 if remaining / free_budget >= 0.30 else max(0, int(25 * (remaining / free_budget) / 0.30))

    confirmed = savings_confirmed(conn, user_id, report_month)
    savings = savings_points(savings_ratio, confirmed)
    tracked_days = tracking_days_90(conn, user_id, today)
    consistency = min(25, int(25 * min(tracked_days, 90) / 90))

    structure = 0
    if fixed > 0 and cash >= fixed * 3:
        structure += 10
    if savings_ratio >= 0.15:
        structure += 8
    if free_budget > 0:
        structure += 7

    days = platform_days(conn, user_id, today)
    cap, days_to_unlock, next_unlock_level = score_cap(days)
    raw_total = budget + savings + consistency + structure
    total = min(max(raw_total, start_score(user) if days == 0 else min(start_score(user), cap)), cap)
    rank_name, rank_icon = score_rank(total)

    if free_budget <= 0:
        budget_why = "Ohne positiven Betrag nach Fixkosten kann Rov.E diesen Faktor noch nicht fair bewerten."
        budget_lever = "Prüfe zuerst Einkommen und Fixkosten im Profil."
    elif remaining < 0:
        budget_why = f"Deine variablen Ausgaben liegen {abs(remaining):.0f} € über dem Betrag nach Fixkosten."
        budget_lever = "Ein klarer Budgettopf mit zu viel Druck ist der direkteste Hebel."
    else:
        budget_why = f"Nach deinen variablen Ausgaben bleiben {remaining:.0f} € innerhalb des Betrags nach Fixkosten."
        budget_lever = "Halte diesen Rahmen bis Monatsende stabil."

    savings_why = (
        f"{savings_amount:.0f} € monatliche Sparrate entsprechen {savings_ratio * 100:.0f} % deines Einkommens."
        if income > 0 else
        "Für eine faire Bewertung fehlen Einkommen oder Sparrate."
    )
    savings_lever = (
        "Die Sparrate ist diesen Monat bestätigt und zählt voll in deine Umsetzung."
        if confirmed else
        "Bestätige deine Sparrate im Monatsplan, sobald sie wirklich ausgeführt wurde."
    )
    consistency_why = f"Du hast an {tracked_days} der letzten 90 Tage mindestens eine Ausgabe dokumentiert."
    consistency_lever = "Ein echter Tracking-Tag zählt mehr als viele kleine Buchungen an einem Tag."
    if fixed > 0 and cash >= fixed * 3:
        structure_why = "Dein Cash-Puffer deckt mindestens drei Monate Fixkosten."
    elif fixed > 0 and cash >= fixed:
        structure_why = "Dein Cash-Puffer deckt mindestens einen Monat Fixkosten."
    else:
        structure_why = "Dein Cash-Puffer liegt noch unter einem Monat Fixkosten."
    structure_lever = "Ein verlässlicher Puffer und eine feste Sparquote stärken diesen Bereich."

    factors = [
        _factor("budget", "Budget-Kontrolle", budget, "#35D07F", budget_why, budget_lever),
        _factor("savings", "Sparrate", savings, "#D8B66A", savings_why, savings_lever),
        _factor("consistency", "Tracking-Konstanz", consistency, "#2AABEE", consistency_why, consistency_lever),
        _factor("structure", "Finanzielle Struktur", structure, "#8B7DF5", structure_why, structure_lever),
    ]
    weakest = min(factors, key=lambda factor: factor["points"])
    if days < 30:
        phase = "Aufbauphase"
        description = f"Dein Score wird mit echten Daten bis {cap}/100 freigeschaltet."
    elif days < 90:
        phase = "Proof-Phase"
        description = "Deine Routinen werden gerade über mehrere Monate bestätigt."
    else:
        phase = "Verifiziert"
        description = "Dein Score basiert auf deiner laufenden Finanzroutine."

    return {
        "value": total,
        "total": total,
        "raw_total": raw_total,
        "label": rank_name,
        "rank_name": rank_name,
        "rank_icon": rank_icon,
        "rank_emoji": rank_icon,
        "phase": phase,
        "cap": cap,
        "platform_days": days,
        "proof_days": days,
        "tracking_days_90": tracked_days,
        "savings_confirmed": confirmed,
        "savings_ratio": savings_ratio,
        "budget": budget,
        "savings": savings,
        "consistency": consistency,
        "structure": structure,
        "start_score": start_score(user),
        "days_to_unlock": days_to_unlock,
        "next_unlock_level": next_unlock_level,
        "desc": description,
        "next_lever": weakest["n"],
        "factors": factors,
        "points": points_rank(_int(user, "clarity_points")),
    }


def ensure_point_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rove_point_events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL,
               event_key TEXT NOT NULL,
               event_date TEXT NOT NULL,
               event_type TEXT NOT NULL,
               points INTEGER NOT NULL,
               expense_id INTEGER,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               UNIQUE(user_id, event_key)
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_rove_point_events_expense
           ON rove_point_events(user_id, expense_id)"""
    )


def _tracking_reward(streak: int) -> int:
    reward = 1
    if streak == 7:
        reward += 10
    elif streak == 30:
        reward += 30
    elif streak > 7 and streak % 7 == 0:
        reward += 5
    return reward


def _latest_tracking_state(conn: sqlite3.Connection, user_id: int) -> tuple[str | None, int]:
    rows = conn.execute(
        """SELECT DISTINCT DATE(created_at) AS tracking_day
             FROM expenses
             WHERE user_id = ?
             ORDER BY tracking_day DESC""",
        (user_id,),
    ).fetchall()
    if not rows:
        return None, 0
    latest_key = str(_value(rows[0], "tracking_day", ""))
    try:
        expected = date.fromisoformat(latest_key)
    except ValueError:
        return None, 0
    streak = 0
    for row in rows:
        try:
            current = date.fromisoformat(str(_value(row, "tracking_day", "")))
        except ValueError:
            continue
        if current != expected:
            break
        streak += 1
        expected -= timedelta(days=1)
    return latest_key, streak


def award_tracking_points(
    conn: sqlite3.Connection,
    user_id: int,
    expense_id: int | None = None,
    today: date | None = None,
) -> dict:
    """Award the once-per-day tracking point for App activity, safely shared with the bot."""
    today = today or date.today()
    today_key = today.isoformat()
    yesterday_key = (today - timedelta(days=1)).isoformat()
    ensure_point_events_table(conn)
    event_key = f"tracking:{today_key}"
    existing = conn.execute(
        """SELECT points FROM rove_point_events
           WHERE user_id = ? AND event_key = ?""",
        (user_id, event_key),
    ).fetchone()
    if existing:
        user = conn.execute(
            "SELECT streak_days, clarity_points FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return {
            "awarded": 0,
            "streak": _int(user, "streak_days") if user else 0,
            "points": _int(user, "clarity_points") if user else 0,
        }
    user = conn.execute(
        "SELECT last_activity_date, streak_days, clarity_points FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not user:
        return {"awarded": 0, "streak": 0, "points": 0}
    if str(_value(user, "last_activity_date", "")) == today_key:
        return {
            "awarded": 0,
            "streak": _int(user, "streak_days"),
            "points": _int(user, "clarity_points"),
        }
    streak = _int(user, "streak_days") + 1 if str(_value(user, "last_activity_date", "")) == yesterday_key else 1
    awarded = _tracking_reward(streak)
    points = _int(user, "clarity_points") + awarded
    conn.execute(
        """UPDATE users SET last_activity_date = ?, streak_days = ?, clarity_points = ?
           WHERE user_id = ?""",
        (today_key, streak, points, user_id),
    )
    conn.execute(
        """INSERT INTO rove_point_events
           (user_id, event_key, event_date, event_type, points, expense_id)
           VALUES (?, ?, ?, 'tracking_day', ?, ?)""",
        (user_id, event_key, today_key, awarded, expense_id),
    )
    return {"awarded": awarded, "streak": streak, "points": points}


def reverse_tracking_points_for_deleted_expense(
    conn: sqlite3.Connection,
    user_id: int,
    expense_id: int,
    created_at: str,
) -> int:
    """Reverse App RP only when the deleted expense was the last one of its day."""
    ensure_point_events_table(conn)
    event_date = str(created_at or "")[:10]
    if not event_date:
        return 0
    event_key = f"tracking:{event_date}"
    event = conn.execute(
        """SELECT id, points, expense_id FROM rove_point_events
           WHERE user_id = ? AND event_key = ? AND event_type = 'tracking_day'""",
        (user_id, event_key),
    ).fetchone()
    if not event:
        return 0

    replacement = conn.execute(
        """SELECT id FROM expenses
           WHERE user_id = ? AND DATE(created_at) = DATE(?)
           ORDER BY id LIMIT 1""",
        (user_id, event_date),
    ).fetchone()
    if replacement:
        if _int(event, "expense_id") == expense_id:
            conn.execute(
                "UPDATE rove_point_events SET expense_id = ? WHERE id = ?",
                (_int(replacement, "id"), _int(event, "id")),
            )
        return 0

    reversed_points = max(0, _int(event, "points"))
    conn.execute("DELETE FROM rove_point_events WHERE id = ?", (_int(event, "id"),))
    latest_day, streak = _latest_tracking_state(conn, user_id)
    conn.execute(
        """UPDATE users
           SET clarity_points = MAX(0, COALESCE(clarity_points, 0) - ?),
               last_activity_date = ?, streak_days = ?
           WHERE user_id = ?""",
        (reversed_points, latest_day, streak, user_id),
    )
    return reversed_points
