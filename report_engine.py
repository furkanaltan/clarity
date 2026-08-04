import os
import sqlite3
import calendar
import json
import gzip
import shutil
import logging
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from rove_score import calculate_score as calculate_live_score

load_dotenv()
logger = logging.getLogger(__name__)

DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
APP_DIR = Path(__file__).resolve().parent
REPORTS_DIR = Path(os.getenv("CLARITY_REPORTS_DIR", str(APP_DIR / "reports")))
MIN_TRACKING_DAYS = int(os.getenv("MIN_TRACKING_DAYS", "14"))
APP_PUSH_INTERNAL_URL = os.getenv("ROVE_APP_INTERNAL_PUSH_URL", "http://127.0.0.1:5057/v1/internal/push")
APP_PUSH_INTERNAL_SECRET = os.getenv("ROVE_INTERNAL_PUSH_SECRET", "").strip()


class ReportSkipped(Exception):
    pass

GERMAN_MONTHS = {
    1: "Januar",
    2: "Februar",
    3: "März",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Dezember",
}

RANKS = [
    (0, "Rookie", "🥚"),
    (50, "Stratege", "🔍"),
    (200, "Controller", "📊"),
    (500, "Investor", "🧱"),
    (1000, "Manager", "🏗️"),
    (2500, "Kapitalist", "🏛️"),
    (5000, "Rov.E Elite", "💎"),
]

SCORE_RANKS = [
    (0, 44, "Rookie", "🥚"),
    (45, 54, "Stratege", "🔍"),
    (55, 64, "Controller", "📊"),
    (65, 74, "Investor", "🧱"),
    (75, 84, "Manager", "🏗️"),
    (85, 92, "Kapitalist", "🏛️"),
    (93, 100, "Rov.E Elite", "💎"),
]

BADGE_LABELS = {
    "streak_7": "Erste Woche",
    "streak_30": "Eiserner Monat",
    "first_investment": "Erstes Investment",
    "thousand_club": "Tausender-Club",
    "emergency_fund": "Notgroschen",
    "savings_master": "Spar-Meister",
    "ten_k_club": "Fünfstellige Freiheit",
    "month_win": "Monats-Sieg",
    "fastfood_free": "Fast-Food-Pause",
    "no_fastfood_30": "30 Tage ohne Fast Food",
}

PAGE_W, PAGE_H = landscape(A4)
MARGIN_X = 50
INK = HexColor("#111111")
MUTED = HexColor("#6B6B6B")
SOFT = HexColor("#F5F5F5")
LINE = HexColor("#E8E8E8")
GOOD = HexColor("#1F7A4D")
ALERT = HexColor("#A33A32")


def register_font(name: str, candidates: list[str], fallback: str) -> str:
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                pdfmetrics.registerFont(TTFont(name, candidate))
                return name
            except Exception:
                continue
    return fallback


FONT_DISPLAY = register_font(
    "ClarityDisplay",
    [
        "/System/Library/Fonts/Supplemental/BigCaslon.ttf",
        "/System/Library/Fonts/NewYork.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
    ],
    "Times-Roman",
)
FONT_TEXT = "Helvetica"


def fmt_eur(value) -> str:
    try:
        return f"{float(value or 0):,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00 EUR"


def fmt_delta(value) -> str:
    if value is None:
        return "ab Monat 2"
    prefix = "+" if value >= 0 else ""
    return f"{prefix}{fmt_eur(value)}"


def fmt_eur_cover(value) -> str:
    try:
        amount = round(float(value or 0))
    except Exception:
        amount = 0
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(amount):,.0f} €".replace(",", ".")


def fmt_percent_cover(value) -> str:
    if value is None:
        return "ab Monat 2"
    sign = "+" if value >= 0 else ""
    return f"{sign}{float(value):.1f} %".replace(".", ",")


def clamp_text(text, max_chars: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def draw_spaced_text(c, x, y, text, spacing=8, font=FONT_TEXT, size=11):
    c.setFont(font, size)
    cursor = x
    for char in text:
        c.drawString(cursor, y, char)
        cursor += c.stringWidth(char, font, size) + spacing


def draw_clarity_mark(c, x, y, scale=1.0, color=MUTED):
    c.setStrokeColor(color)
    c.setLineWidth(1.25 * scale)
    try:
        c.setLineCap(1)
    except Exception:
        pass
    r = 9.6 * scale
    c.arc(x - r, y - r, x + r, y + r, 38, 322)
    c.arc(x - r * 0.62, y - r * 0.62, x + r * 0.62, y + r * 0.62, 38, 322)


def draw_clarity_logo(c, x, y, scale=1.0):
    c.setFillColor(MUTED)
    draw_clarity_mark(c, x, y, scale, MUTED)
    draw_spaced_text(c, x + 29 * scale, y - 4.2 * scale, "ROV.E", spacing=6.8 * scale, size=10.5 * scale)


def draw_footer(c, page_number=1):
    y = 58
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.line(MARGIN_X, y + 36, PAGE_W - MARGIN_X, y + 36)
    draw_clarity_logo(c, MARGIN_X + 10, y + 10, 0.78)
    c.setStrokeColor(LINE)
    c.line(MARGIN_X + 118, y - 2, MARGIN_X + 118, y + 22)
    c.setFont(FONT_TEXT, 9)
    c.setFillColor(MUTED)
    c.drawString(MARGIN_X + 138, y + 6, "Rov.E Report")
    c.drawRightString(PAGE_W - MARGIN_X, y + 6, f"{page_number} / 10")


def draw_cover_icon(c, icon, x, y):
    c.setFillColor(HexColor("#F3F3F3"))
    c.circle(x, y, 19, fill=1, stroke=0)
    c.setStrokeColor(MUTED)
    c.setLineWidth(1.25)
    if icon == "calendar":
        c.roundRect(x - 8, y - 9, 16, 16, 2, fill=0, stroke=1)
        c.line(x - 8, y + 3, x + 8, y + 3)
        c.line(x - 4, y + 10, x - 4, y + 6)
        c.line(x + 4, y + 10, x + 4, y + 6)
        c.line(x - 4, y - 1, x + 4, y - 1)
        c.line(x - 4, y - 5, x + 3, y - 5)
    elif icon == "mountain":
        c.line(x - 12, y - 10, x - 1, y + 8)
        c.line(x - 1, y + 8, x + 12, y - 10)
        c.line(x - 12, y - 10, x + 12, y - 10)
        c.line(x - 4, y - 10, x + 4, y + 1)
        c.line(x + 1, y + 9, x + 1, y + 17)
        c.line(x + 1, y + 15, x + 7, y + 13)
        c.line(x + 7, y + 13, x + 1, y + 11)
    elif icon == "trend":
        c.line(x - 12, y - 6, x - 4, y + 2)
        c.line(x - 4, y + 2, x + 2, y - 2)
        c.line(x + 2, y - 2, x + 12, y + 10)
        c.line(x + 12, y + 10, x + 12, y + 2)
        c.line(x + 12, y + 10, x + 4, y + 10)


def begin_page(c, title: str = None):
    c.setFillColor(HexColor("#FFFFFF"))
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    if title:
        c.setFont("Helvetica-Bold", 17)
        c.setFillColor(INK)
        c.drawString(MARGIN_X, PAGE_H - 70, title)


def end_page(c):
    c.showPage()


def draw_label(c, x, y, text, size=9):
    c.setFont("Helvetica", size)
    c.setFillColor(MUTED)
    c.drawString(x, y, str(text).upper())


def draw_value(c, x, y, text, size=24):
    c.setFont("Helvetica-Bold", size)
    c.setFillColor(INK)
    c.drawString(x, y, str(text))


def draw_body(c, x, y, text, width_chars=72, size=11, leading=16, max_lines=4):
    c.setFont("Helvetica", size)
    c.setFillColor(INK)
    words = str(text or "").split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    for line in lines[:max_lines]:
        c.drawString(x, y, line)
        y -= leading
    return y


def draw_card(c, x, y, w, h, label, value, sub=None, value_size=18):
    c.setFillColor(SOFT)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.roundRect(x, y - h, w, h, 8, fill=1, stroke=1)
    draw_label(c, x + 16, y - 25, label)
    draw_value(c, x + 16, y - 55, clamp_text(value, 34), value_size)
    if sub:
        c.setFont("Helvetica", 9.5)
        c.setFillColor(MUTED)
        c.drawString(x + 16, y - h + 18, clamp_text(sub, 48))


def draw_cover_card(c, x, y, w, h, icon, label, main, sub=None, main_size=48, sub_size=22):
    c.setFillColor(HexColor("#FFFFFF"))
    c.setStrokeColor(HexColor("#D2D2D2"))
    c.setLineWidth(0.85)
    c.roundRect(x, y - h, w, h, 13, fill=1, stroke=1)
    draw_cover_icon(c, icon, x + 37, y - 38)
    c.setFont(FONT_DISPLAY, 15)
    c.setFillColor(HexColor("#555555"))
    c.drawString(x + 76, y - 43, label)
    c.setStrokeColor(HexColor("#D8D8D8"))
    c.setLineWidth(0.7)
    c.line(x + 26, y - 72, x + w - 26, y - 72)
    c.setFont(FONT_DISPLAY, main_size)
    c.setFillColor(INK)
    c.drawCentredString(x + w / 2, y - 143, main)
    if sub:
        c.setFont(FONT_DISPLAY, sub_size)
        c.setFillColor(HexColor("#666666"))
        c.drawCentredString(x + w / 2, y - 178, sub)


def draw_progress_bar(c, x, y, w, h, percent):
    percent = max(0, min(100, float(percent or 0)))
    c.setFillColor(HexColor("#EFEFEF"))
    c.roundRect(x, y, w, h, h / 2, fill=1, stroke=0)
    c.setFillColor(INK)
    c.roundRect(x, y, w * percent / 100.0, h, h / 2, fill=1, stroke=0)


def draw_line_chart(c, x, y, w, h, points):
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.rect(x, y, w, h, fill=0, stroke=1)
    if len(points) < 2:
        c.setFont("Helvetica", 11)
        c.setFillColor(MUTED)
        c.drawCentredString(x + w / 2, y + h / 2, "Kurve ab Monat 2 sichtbar")
        return
    values = [p["net_worth"] for p in points]
    low, high = min(values), max(values)
    span = high - low if high != low else 1
    step = w / max(1, len(points) - 1)
    coords = []
    for idx, point in enumerate(points):
        px = x + idx * step
        py = y + 18 + ((point["net_worth"] - low) / span) * (h - 36)
        coords.append((px, py))
    c.setStrokeColor(INK)
    c.setLineWidth(2)
    for idx in range(len(coords) - 1):
        c.line(coords[idx][0], coords[idx][1], coords[idx + 1][0], coords[idx + 1][1])


def draw_score_circle(c, x, y, radius, score):
    c.setStrokeColor(LINE)
    c.setLineWidth(9)
    c.circle(x, y, radius, fill=0, stroke=1)
    c.setStrokeColor(INK)
    c.setLineWidth(9)
    extent = max(0, min(100, score)) * 3.6
    c.arc(x - radius, y - radius, x + radius, y + radius, 90, 90 - extent)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 32)
    c.drawCentredString(x, y - 10, str(int(score or 0)))


def draw_section_rows(c, x, y, rows, row_gap=26):
    c.setFont("Helvetica", 11)
    for label, value in rows:
        c.setFillColor(MUTED)
        c.drawString(x, y, str(label))
        c.setFillColor(INK)
        c.drawRightString(PAGE_W - MARGIN_X, y, str(value))
        y -= row_gap
    return y


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def ensure_net_worth_column():
    with get_db() as conn:
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(monthly_snapshots)").fetchall()]
        if "net_worth" not in cols:
            conn.execute("ALTER TABLE monthly_snapshots ADD COLUMN net_worth REAL DEFAULT 0.0")
            conn.commit()


def month_bounds(report_month: str):
    year, month = map(int, report_month.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{last_day:02d}"
    label = f"{GERMAN_MONTHS.get(month, datetime(year, month, 1).strftime('%B'))} {year}"
    return start, end, label


def get_user(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row

def row_float(row, key: str, default: float = 0.0) -> float:
    if row is None:
        return default
    try:
        if key not in row.keys():
            return default
        value = row[key]
        return float(value or default)
    except Exception:
        return default


def row_int(row, key: str, default: int = 0) -> int:
    if row is None:
        return default
    try:
        if key not in row.keys():
            return default
        return int(row[key] or default)
    except Exception:
        return default


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def parse_details(user) -> dict:
    if user is None:
        return {}
    try:
        return json.loads(user["fixed_costs_details"] or "{}")
    except Exception:
        return {}


def get_rank(points: int) -> dict:
    current_name, current_icon = RANKS[0][1], RANKS[0][2]
    next_threshold = None
    for threshold, name, icon in RANKS:
        if points >= threshold:
            current_name, current_icon = name, icon
        elif next_threshold is None:
            next_threshold = threshold
    return {
        "name": current_name,
        "icon": current_icon,
        "points_to_next": max(0, next_threshold - points) if next_threshold else 0,
    }


def get_score_rank(score: int) -> tuple:
    for low, high, name, icon in SCORE_RANKS:
        if low <= score <= high:
            return name, icon
    return SCORE_RANKS[-1][2], SCORE_RANKS[-1][3]


def get_platform_days(user_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT MIN(DATE(created_at)) AS first_day FROM expenses WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    first_day = row["first_day"] if row else None
    if not first_day:
        return 0
    try:
        first_date = date.fromisoformat(first_day)
    except ValueError:
        return 0
    return max(1, (date.today() - first_date).days + 1)


def get_tracking_days_90(user_id: int) -> int:
    since = (date.today() - timedelta(days=89)).isoformat()
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(DISTINCT DATE(created_at)) AS days
               FROM expenses
               WHERE user_id = ? AND DATE(created_at) >= DATE(?)""",
            (user_id, since),
        ).fetchone()
    return int(row["days"] or 0) if row else 0


def has_confirmed_investment_for_month(user_id: int, month_key: str) -> bool:
    badge_key = f"inv_{month_key.replace('-', '_')}"
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_key = ?",
            (user_id, badge_key),
        ).fetchone()
        if row:
            return True
        if not table_exists(conn, "investment_events"):
            return False
        row = conn.execute(
            """SELECT 1 FROM investment_events
               WHERE user_id = ?
               AND source = 'investiert_command'
               AND strftime('%Y-%m', created_at) = ?
               LIMIT 1""",
            (user_id, month_key),
        ).fetchone()
    return row is not None


def get_score_cap(platform_days: int) -> tuple:
    if platform_days < 30:
        return 59, max(0, 30 - platform_days), 60
    if platform_days < 60:
        return 69, 60 - platform_days, 70
    if platform_days < 90:
        return 79, 90 - platform_days, 80
    if platform_days < 180:
        return 85, 180 - platform_days, 86
    if platform_days < 365:
        return 92, 365 - platform_days, 93
    return 100, 0, 100


def calculate_start_score(user) -> int:
    income = row_float(user, "income") + row_float(user, "other_income")
    fixed = row_float(user, "fixed_costs")
    savings = row_float(user, "etf_savings") + row_float(user, "cash_savings")
    investments = row_float(user, "current_investments")
    cash = row_float(user, "current_cash")
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
        row_int(user, "onboarding_step") == 10
        and income > 0
        and fixed >= 0
        and bool(user["goal_description"] if "goal_description" in user.keys() else "")
        and row_float(user, "goal_amount") > 0
    ):
        score += 5
    return min(score, 65)


def calculate_savings_execution_points(savings_ratio: float, confirmed: bool) -> int:
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


def calculate_clarity_score_v2(user_id: int, user, total_expenses: float, report_month: str) -> dict:
    with get_db() as conn:
        return calculate_live_score(conn, user_id, user, total_expenses, report_month)


def calculate_goal_projection(goal_amount: float, current_value: float, monthly_savings: float):
    remaining = goal_amount - current_value
    if goal_amount <= 0 or remaining <= 0:
        return 0
    if monthly_savings <= 0:
        return None
    return int((remaining + monthly_savings - 0.01) // monthly_savings)


def format_month_duration(months) -> str:
    months = int(months or 0)
    if months <= 0:
        return "0 Monate"
    if months < 12:
        return f"{months} Monat" if months == 1 else f"{months} Monate"
    years, rest = divmod(months, 12)
    year_text = f"{years} Jahr" if years == 1 else f"{years} Jahre"
    if rest == 0:
        return year_text
    month_text = f"{rest} Monat" if rest == 1 else f"{rest} Monate"
    return f"{year_text} und {month_text}"


def get_expense_stats(user_id: int, report_month: str):
    start, end, _ = month_bounds(report_month)
    with get_db() as conn:
        total = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM expenses
            WHERE user_id = ? AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
            """,
            (user_id, start, end),
        ).fetchone()["total"]

        tracked_days = conn.execute(
            """
            SELECT COUNT(DISTINCT DATE(created_at)) AS days
            FROM expenses
            WHERE user_id = ? AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
            """,
            (user_id, start, end),
        ).fetchone()["days"]

        cats = conn.execute(
            """
            SELECT category, COALESCE(SUM(amount), 0) AS total
            FROM expenses
            WHERE user_id = ? AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
            GROUP BY category
            ORDER BY total DESC
            LIMIT 6
            """,
            (user_id, start, end),
        ).fetchall()

    return float(total or 0), int(tracked_days or 0), cats


def get_app_property_equity(user_id: int) -> float:
    """Return central App property equity without requiring App tables for bot-only users."""
    with get_db() as conn:
        try:
            row = conn.execute(
                """SELECT market_value, remaining_debt
                     FROM app_properties
                    WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0.0

    if not row:
        return 0.0
    return max(0.0, float(row["market_value"] or 0) - float(row["remaining_debt"] or 0))


def get_report_goal(user_id: int, user) -> tuple[str, float, float]:
    """Return the report goal and its explicitly assigned goal-pot balance.

    Older Telegram users store their primary goal on ``users``. New App users
    store goals in ``app_goals`` instead. Reports must understand both paths.
    """
    description = str(user["goal_description"] or "").strip() if "goal_description" in user.keys() else ""
    target_amount = row_float(user, "goal_amount")
    if description and target_amount > 0:
        with get_db() as conn:
            progress = 0.0
            if table_exists(conn, "app_primary_goal_progress"):
                row = conn.execute(
                    """SELECT current_amount FROM app_primary_goal_progress
                         WHERE user_id = ?""",
                    (user_id,),
                ).fetchone()
                progress = float(row["current_amount"] or 0) if row else 0.0
        return description, target_amount, min(max(0.0, progress), target_amount)

    with get_db() as conn:
        if not table_exists(conn, "app_goals"):
            return "", 0.0, 0.0
        row = conn.execute(
            """SELECT name, target_amount, current_amount
                 FROM app_goals
                WHERE user_id = ? AND target_amount > 0
                ORDER BY datetime(created_at), goal_id
                LIMIT 1""",
            (user_id,),
        ).fetchone()
    if not row:
        return "", 0.0, 0.0
    target_amount = float(row["target_amount"] or 0)
    progress = float(row["current_amount"] or 0)
    return str(row["name"] or "").strip(), target_amount, min(max(0.0, progress), target_amount)


def get_biggest_expense(user_id: int, report_month: str):
    start, end, _ = month_bounds(report_month)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT amount, category, merchant, created_at
            FROM expenses
            WHERE user_id = ? AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
            ORDER BY amount DESC, created_at DESC
            LIMIT 1
            """,
            (user_id, start, end),
        ).fetchone()
    if not row:
        return None
    return {
        "amount": float(row["amount"] or 0),
        "category": row["category"] or "SONSTIGES",
        "merchant": row["merchant"] or "Unbekannt",
        "created_at": row["created_at"],
    }


def get_investment_summary(user_id: int, report_month: str) -> dict:
    start, end, _ = month_bounds(report_month)
    empty = {
        "one_time_in": 0.0,
        "recurring_in": 0.0,
        "out": 0.0,
        "net_contributions": 0.0,
        "by_asset": [],
        "largest_event": None,
        "events_count": 0,
    }
    with get_db() as conn:
        if not table_exists(conn, "investment_events"):
            return empty

        rows = conn.execute(
            """
            SELECT amount, direction, asset_type, asset_name, event_type, source, created_at
            FROM investment_events
            WHERE user_id = ? AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
              AND event_type NOT IN ('manual_adjustment', 'asset_update', 'correction')
            ORDER BY created_at ASC, id ASC
            """,
            (user_id, start, end),
        ).fetchall()

        by_asset_rows = conn.execute(
            """
            SELECT asset_type, COALESCE(SUM(
                CASE WHEN direction = 'out' THEN -amount ELSE amount END
            ), 0) AS total
            FROM investment_events
            WHERE user_id = ? AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)
              AND event_type NOT IN ('manual_adjustment', 'asset_update', 'correction')
            GROUP BY asset_type
            ORDER BY total DESC
            """,
            (user_id, start, end),
        ).fetchall()

    summary = dict(empty)
    largest = None
    for row in rows:
        amount = float(row["amount"] or 0)
        direction = row["direction"] or "in"
        event_type = row["event_type"] or "one_time"
        signed = -amount if direction == "out" else amount
        summary["net_contributions"] += signed
        if direction == "out":
            summary["out"] += amount
        elif event_type == "recurring_plan":
            summary["recurring_in"] += amount
        else:
            summary["one_time_in"] += amount
        if largest is None or amount > largest["amount"]:
            largest = {
                "amount": amount,
                "direction": direction,
                "asset_type": row["asset_type"] or "investment",
                "asset_name": row["asset_name"] or row["asset_type"] or "Investment",
                "event_type": event_type,
                "source": row["source"] or "chat",
                "created_at": row["created_at"],
            }

    summary["largest_event"] = largest
    summary["events_count"] = len(rows)
    summary["by_asset"] = [
        {"asset_type": row["asset_type"] or "investment", "total": float(row["total"] or 0)}
        for row in by_asset_rows
    ]
    return summary


def get_monthly_execution(user_id: int, report_month: str) -> dict:
    """Liest nur die explizit bestaetigten Monatsbewegungen der App.

    Profilwerte bleiben eine Planung. Im Report darf daraus keine ausgefuehrte
    Buchung werden, solange der Nutzer sie fuer den jeweiligen Monat nicht
    bestaetigt hat.
    """
    execution = {
        "income_confirmed": False,
        "fixed_costs_confirmed": False,
        "savings_confirmed": False,
    }
    with get_db() as conn:
        row = None
        if table_exists(conn, "app_monthly_plan_status"):
            row = conn.execute(
                """SELECT income_status, fixed_costs_status, savings_status
                     FROM app_monthly_plan_status
                    WHERE user_id = ? AND month_key = ?""",
                (user_id, report_month),
            ).fetchone()
        if row:
            execution["income_confirmed"] = row["income_status"] == "confirmed"
            execution["fixed_costs_confirmed"] = row["fixed_costs_status"] == "confirmed"
            execution["savings_confirmed"] = row["savings_status"] == "confirmed"

        # Ein ETF-Sparplan kann automatisch laufen, waehrend die flexible
        # Cash-Sparrate noch offen ist. Deshalb gilt ausschliesslich die
        # explizite Monatsplan-Bestaetigung als bestaetigte Gesamtsparrate.
        # Die ETF-Bewegung erscheint separat als tatsaechliches Investment.
    return execution


def get_report_savings_progress(user_id: int, report_month: str, execution: dict) -> dict:
    """Return only savings-plan progress that the App can substantiate.

    The investment summary intentionally includes all investment activity. That is
    useful on the wealth page, but it must not turn a one-off investment or a
    legacy booking into a completed monthly savings rate on the cover.
    """
    progress = {
        "full_plan_confirmed": bool(execution.get("savings_confirmed")),
        "full_plan_amount": 0.0,
        "automatic_etf_amount": 0.0,
    }
    with get_db() as conn:
        if not table_exists(conn, "investment_events"):
            return progress
        rows = conn.execute(
            """SELECT source, COALESCE(SUM(amount), 0) AS amount
                 FROM investment_events
                WHERE user_id = ?
                  AND direction != 'out'
                  AND strftime('%Y-%m', created_at) = ?
                  AND source IN ('app_monthly_plan', 'app_etf_plan')
                GROUP BY source""",
            (user_id, report_month),
        ).fetchall()

    amounts = {str(row["source"]): float(row["amount"] or 0) for row in rows}
    progress["automatic_etf_amount"] = max(0.0, amounts.get("app_etf_plan", 0.0))
    if progress["full_plan_confirmed"]:
        # A confirmed plan can contain cash-only bookings, or cash plus the
        # separately scheduled ETF plan. Both belong to the confirmed plan.
        progress["full_plan_amount"] = max(
            0.0,
            amounts.get("app_monthly_plan", 0.0) + progress["automatic_etf_amount"],
        )
    return progress


def get_latest_portfolio_snapshots(user_id: int) -> list:
    with get_db() as conn:
        if not table_exists(conn, "portfolio_snapshots"):
            return []
        rows = conn.execute(
            """
            SELECT amount, scope, source, note, created_at
            FROM portfolio_snapshots
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 6
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "amount": float(row["amount"] or 0),
            "scope": row["scope"] or "investments",
            "source": row["source"] or "",
            "note": row["note"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_wealth_history(user_id: int, report_month: str, limit: int = 12) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT month, net_worth, clarity_score
            FROM monthly_snapshots
            WHERE user_id = ? AND month <= ?
            ORDER BY month DESC
            LIMIT ?
            """,
            (user_id, report_month, limit),
        ).fetchall()
    points = [
        {
            "month": row["month"],
            "net_worth": float(row["net_worth"] or 0),
            "clarity_score": int(row["clarity_score"] or 0),
        }
        for row in rows
    ]
    return list(reversed(points))


def get_user_badges(user_id: int, limit: int = 8) -> list:
    """Nur echte Errungenschaften (BADGE_LABELS-Keys) — user_badges enthaelt daneben auch
    interne Dedup-Marker (moment_*, budget_invite_sent_*, budget_resolved_*, inv_YYYY_MM),
    die bot.py's eigener /badges-Befehl ebenfalls ausblendet."""
    with get_db() as conn:
        if not table_exists(conn, "user_badges"):
            return []
        placeholders = ",".join("?" for _ in BADGE_LABELS)
        rows = conn.execute(
            f"""
            SELECT badge_key, earned_at
            FROM user_badges
            WHERE user_id = ? AND badge_key IN ({placeholders})
            ORDER BY earned_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, *BADGE_LABELS.keys(), limit),
        ).fetchall()
    return [
        {
            "key": row["badge_key"],
            "label": BADGE_LABELS[row["badge_key"]],
            "earned_at": row["earned_at"],
        }
        for row in rows
    ]


def get_snapshot(user_id: int, report_month: str):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM monthly_snapshots
            WHERE user_id = ? AND month = ?
            """,
            (user_id, report_month),
        ).fetchone()
    return row


def get_prev_snapshot(user_id: int, report_month: str):
    year, month = map(int, report_month.split("-"))
    if month == 1:
        prev_key = f"{year-1:04d}-12"
    else:
        prev_key = f"{year:04d}-{month-1:02d}"

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM monthly_snapshots
            WHERE user_id = ? AND month = ?
            """,
            (user_id, prev_key),
        ).fetchone()
    return row


def get_budget_frame(user_id: int, report_month: str) -> dict:
    """
    Liest die fuer report_month gemeinsam gesetzten Kategorie-Budgets (Tabelle
    category_budgets, gepflegt vom Bot) und stellt sie den tatsaechlichen Ausgaben
    des Monats gegenueber. Kein Import aus bot.py (Kreis-Import) - direkte Query.

    Rueckgabe immer sicher: fehlt die Tabelle oder gibt es keine Budgets fuer den
    Monat, kommt {"has_budgets": False, ...} zurueck.
    """
    empty = {"has_budgets": False, "items": [], "total_limit": 0.0,
             "total_used": 0.0, "adherence_pct": None, "on_track": None}
    start, end, _ = month_bounds(report_month)
    try:
        with get_db() as conn:
            budgets = conn.execute(
                "SELECT category, monthly_limit FROM category_budgets "
                "WHERE user_id = ? AND active_month = ?",
                (user_id, report_month),
            ).fetchall()
            if not budgets:
                return empty
            items = []
            total_limit = 0.0
            total_used = 0.0
            for b in budgets:
                category = b["category"]
                limit = float(b["monthly_limit"] or 0)
                used = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses "
                    "WHERE user_id = ? AND category = ? "
                    "AND DATE(created_at) BETWEEN DATE(?) AND DATE(?)",
                    (user_id, category, start, end),
                ).fetchone()["s"]
                used = float(used or 0)
                total_limit += limit
                total_used += used
                items.append({
                    "category": category,
                    "limit": limit,
                    "used": used,
                    "left": limit - used,
                    "pct_used": round(used / limit * 100, 1) if limit > 0 else None,
                    "over": used > limit,
                })
    except Exception as e:
        logger.warning("Budget-Frame konnte nicht geladen werden: %s", e)
        return empty

    return {
        "has_budgets": True,
        "items": items,
        "total_limit": total_limit,
        "total_used": total_used,
        "adherence_pct": round(total_used / total_limit * 100, 1) if total_limit > 0 else None,
        "on_track": total_used <= total_limit,
    }


def build_report_data(user_id: int, report_month: str) -> dict:
    ensure_net_worth_column()

    user = get_user(user_id)
    if not user:
        raise ValueError("User nicht gefunden")

    start, end, month_label = month_bounds(report_month)
    total_expenses, tracked_days, category_rows = get_expense_stats(user_id, report_month)
    biggest_expense = get_biggest_expense(user_id, report_month)
    snapshot = get_snapshot(user_id, report_month)
    prev_snapshot = get_prev_snapshot(user_id, report_month)

    income = row_float(user, "income")
    other_income = row_float(user, "other_income")
    income_total = income + other_income
    fixed_costs = row_float(user, "fixed_costs")
    etf_rate = row_float(user, "etf_savings")
    cash_rate = row_float(user, "cash_savings")
    current_investments = row_float(user, "current_investments")
    cash_reserve = row_float(user, "current_cash")
    property_equity = get_app_property_equity(user_id)
    goal_description, target_amount, goal_current_amount = get_report_goal(user_id, user)
    clarity_points = row_int(user, "clarity_points")

    free_budget = income_total - fixed_costs
    remaining_budget = free_budget - total_expenses
    savings_plan = etf_rate + cash_rate
    savings_rate = (savings_plan / income_total * 100.0) if income_total > 0 else 0.0

    # A test report for the open month must show the current App and bot state.
    # Closed months remain anchored to their saved monthly snapshot.
    is_current_month = report_month == datetime.now().strftime("%Y-%m")
    net_worth = (
        current_investments + cash_reserve + property_equity
        if is_current_month
        else (
            float(snapshot["net_worth"])
            if snapshot and snapshot["net_worth"] is not None
            else current_investments + cash_reserve + property_equity
        )
    )
    prev_net_worth = (
        float(prev_snapshot["net_worth"])
        if prev_snapshot and prev_snapshot["net_worth"] is not None
        else None
    )
    net_worth_delta = net_worth - prev_net_worth if prev_net_worth is not None else None
    net_worth_delta_percent = (
        (net_worth_delta / prev_net_worth * 100.0)
        if prev_net_worth not in (None, 0) and net_worth_delta is not None
        else None
    )

    score_parts = calculate_clarity_score_v2(user_id, user, total_expenses, report_month)
    clarity_score = score_parts["total"]
    budget_ok = bool(snapshot["budget_ok"]) if snapshot and snapshot["budget_ok"] is not None else (remaining_budget >= 0)

    top_categories = [
        {"category": row["category"], "total": float(row["total"] or 0)}
        for row in category_rows
    ]
    strongest_category = top_categories[0] if top_categories else None
    goal_progress = (goal_current_amount / target_amount * 100.0) if target_amount > 0 else 0.0
    months_to_goal = calculate_goal_projection(target_amount, goal_current_amount, savings_plan)
    investment_summary = get_investment_summary(user_id, report_month)
    monthly_execution = get_monthly_execution(user_id, report_month)
    savings_progress = get_report_savings_progress(user_id, report_month, monthly_execution)
    wealth_history = get_wealth_history(user_id, report_month)
    portfolio_snapshots = get_latest_portfolio_snapshots(user_id)
    badges = get_user_badges(user_id)
    rank = get_rank(clarity_points)
    details = parse_details(user)
    budget_frame = get_budget_frame(user_id, report_month)

    if net_worth_delta is None:
        development_text = "Der erste Referenzmonat ist aufgebaut. Ab Monat 2 wird die Entwicklung sichtbar."
    elif net_worth_delta >= 0:
        development_text = f"Dein Nettovermögen ist im Vergleich zum Vormonat um {net_worth_delta:.2f} EUR gestiegen."
    else:
        development_text = f"Dein Nettovermögen liegt {abs(net_worth_delta):.2f} EUR unter dem Vormonat."

    if (
        monthly_execution["income_confirmed"]
        and monthly_execution["fixed_costs_confirmed"]
        and monthly_execution["savings_confirmed"]
    ):
        best_decision = "Dein Monatsplan wurde bestätigt: Gehalt, Fixkosten und Sparrate sind erfasst."
    elif monthly_execution["savings_confirmed"]:
        best_decision = f"Deine Sparrate von {savings_plan:.2f} EUR wurde für diesen Monat bestätigt."
    elif investment_summary["net_contributions"] > 0:
        best_decision = f"Du hast {investment_summary['net_contributions']:.2f} EUR investiert oder zurückgelegt."
    elif savings_plan > 0:
        best_decision = f"Deine geplante Sparrate liegt bei {savings_plan:.2f} EUR pro Monat."
    elif tracked_days > 0:
        best_decision = f"Du hast an {tracked_days} Tag(en) deine Finanzen sichtbar gemacht."
    else:
        best_decision = "Der erste Schritt ist gemacht: dein Profil steht."

    if remaining_budget < 0:
        focus = "Budgetdruck früh erkennen und variable Ausgaben senken."
    elif strongest_category:
        focus = f"{strongest_category['category']} bewusst beobachten."
    else:
        focus = "Konsequent weiter tracken, damit dein erster Vergleich entsteht."

    money_map_insights = []
    if biggest_expense:
        money_map_insights.append(
            f"Größte Einzelbuchung: {biggest_expense['merchant']} mit {biggest_expense['amount']:.2f} EUR."
        )
    if strongest_category:
        money_map_insights.append(
            f"Stärkste Kategorie: {strongest_category['category']} mit {strongest_category['total']:.2f} EUR."
        )
    if remaining_budget < 0:
        money_map_insights.append("Dein freies Budget ist überzogen - hier liegt dein dringendster Hebel.")
    elif strongest_category:
        money_map_insights.append(
            f"{strongest_category['category']} dominiert deinen Monat - hier liegt dein größter Hebel."
        )
    elif free_budget > 0:
        money_map_insights.append("Dein freies Budget bleibt stabil - diesen Vorsprung solltest du halten.")

    recap_good = "Deine Struktur steht: Einnahmen, Fixkosten, Sparziel und Vermögenswerte sind erfasst."
    if tracked_days >= 7:
        recap_good = f"Du hast {tracked_days} Tracking-Tage aufgebaut. Das ist eine starke Datenbasis."
    if investment_summary["recurring_in"] > 0:
        recap_good = f"Deine Sparplan-Konstanz ist sichtbar: {investment_summary['recurring_in']:.2f} EUR wurden planmäßig erfasst."

    needs_attention = "Noch fehlen Vergleichsmonate. Der Report wird mit jedem Monatsabschluss präziser."
    if tracked_days < 3:
        needs_attention = "Der Monat ist noch frisch. Tracke weiter, bevor du aus einzelnen Tagen Schlüsse ziehst."
    elif remaining_budget < 0:
        needs_attention = "Dein Restbudget war negativ. Hier liegt der wichtigste Hebel."
    elif strongest_category:
        needs_attention = f"Behalte {strongest_category['category']} im Blick, weil diese Kategorie den Monat dominiert."

    next_lever = "Diesen Monat weiter sauber tracken."
    if months_to_goal is None:
        next_lever = "Eine monatliche Sparrate hinterlegen, damit die Zielprognose sichtbar wird."
    elif months_to_goal > 0:
        next_lever = f"Bei gleicher Sparrate erreichst du dein Ziel in etwa {format_month_duration(months_to_goal)}."

    data = {
        "meta": {
            "user_id": user_id,
            "report_month": report_month,
            "period_start": start,
            "period_end": end,
            "month_label": month_label,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "tracked_days": tracked_days,
            "has_minimum_tracking": tracked_days >= MIN_TRACKING_DAYS,
        },
        "profile": {
            "income": income,
            "other_income": other_income,
            "income_total": income_total,
            "fixed_costs": fixed_costs,
            "details": details,
            "etf_rate": etf_rate,
            "cash_rate": cash_rate,
            "savings_plan": savings_plan,
            "savings_rate": savings_rate,
            "current_investments": current_investments,
            "cash_reserve": cash_reserve,
            "property_equity": property_equity,
            "net_worth": net_worth,
        },
        "pages": {
            "cover": {
                "period": month_label,
                # A planned savings rate is not a completed step toward a goal.
                "freedom_step": investment_summary["net_contributions"],
                "development": net_worth_delta,
                "development_percent": net_worth_delta_percent,
                "development_text": development_text,
            },
            "financial_story": {
                "net_worth": net_worth,
                "cash": cash_reserve,
                "investments": current_investments,
                "previous_net_worth": prev_net_worth,
                "delta": net_worth_delta,
                "text": development_text,
            },
            "month": {
                "best_decision": best_decision,
                "biggest_expense": biggest_expense,
                "strongest_category": strongest_category,
                "focus": focus,
                "total_expenses": total_expenses,
                "remaining_budget": remaining_budget,
                "tracked_days": tracked_days,
            },
            "score": {
                "clarity_score": clarity_score,
                "parts": score_parts,
                "rank_name": score_parts["rank_name"],
                "rank_icon": score_parts["rank_icon"],
                "phase": score_parts["phase"],
                "proof_days": score_parts["proof_days"],
                "days_to_unlock": score_parts["days_to_unlock"],
                "next_unlock_level": score_parts["next_unlock_level"],
                "share_cta": "Share Score",
            },
            "wealth_journey": {
                "points": wealth_history,
                "is_visible": len(wealth_history) >= 2,
                "note": "Die Vermögenskurve wird ab dem zweiten Monatsabschluss sichtbar.",
                "investment_summary": investment_summary,
                "monthly_execution": monthly_execution,
                "savings_progress": savings_progress,
                "portfolio_snapshots": portfolio_snapshots,
            },
            "goal": {
                "description": goal_description or "Dein Ziel",
                "target_amount": target_amount,
                "current_amount": goal_current_amount,
                "progress_percent": min(100.0, goal_progress),
                "months_to_goal": months_to_goal,
                "forecast_text": next_lever,
            },
            "money_map": {
                "categories": top_categories,
                "insights": money_map_insights,
            },
            "budget": budget_frame,
            "milestones": {
                "clarity_points": clarity_points,
                "rank": rank,
                "badges": badges,
            },
            "recap": {
                "what_went_well": recap_good,
                "needs_attention": needs_attention,
                "next_lever": next_lever,
                "benchmark": None,
            },
            "closing": {
                "headline": "Jeder Euro hat eine Aufgabe.",
                "message": "Der nächste Monat baut auf genau diesen Entscheidungen auf.",
            },
        },
    }

    # --- KI-personalisierte Texte (mit Fallback auf die Formel-Texte oben) ---
    # generate_ai_narratives liefert bei fehlendem Key/Fehler/deaktivierter KI ein
    # leeres Dict; dann bleibt jedes Feld auf seinem Formel-Text -> altes Verhalten.
    try:
        from report_ai_text import generate_ai_narratives
        ai = generate_ai_narratives(data)
    except Exception as e:
        logger.warning("KI-Report-Texte konnten nicht erzeugt werden: %s", e)
        ai = {}

    data["ai_narratives"] = ai
    p = data["pages"]
    if ai.get("development"):
        p["cover"]["development_text"] = ai["development"]
        p["financial_story"]["text"] = ai["development"]
    if ai.get("best_decision") and not all(monthly_execution.values()):
        p["month"]["best_decision"] = ai["best_decision"]
    if ai.get("focus"):
        p["month"]["focus"] = ai["focus"]
    if ai.get("recap_good"):
        p["recap"]["what_went_well"] = ai["recap_good"]
    if ai.get("recap_attention"):
        p["recap"]["needs_attention"] = ai["recap_attention"]
    if ai.get("recap_lever"):
        p["recap"]["next_lever"] = ai["recap_lever"]

    return data


def draw_cover_page(c, data):
    cover = data["pages"]["cover"]
    month_name, year = cover["period"].split(" ", 1) if " " in cover["period"] else (cover["period"], "")
    has_development = cover.get("development_percent") is not None
    development = fmt_percent_cover(cover.get("development_percent")) if has_development else "-"
    development_sub = "zum Vormonat" if has_development else "ab Monat 2 sichtbar"
    development_size = 41 if has_development else 54

    begin_page(c)
    draw_clarity_logo(c, MARGIN_X + 8, PAGE_H - 66, 1.0)

    c.setFont(FONT_DISPLAY, 72)
    c.setFillColor(INK)
    c.drawString(MARGIN_X, PAGE_H - 165, "Rov.E Report")

    y = PAGE_H - 225
    card_w = 236
    card_h = 215
    gap = 14
    draw_cover_card(
        c, MARGIN_X, y, card_w, card_h,
        "calendar", "Zeitraum", month_name.upper(), year, main_size=59, sub_size=29
    )
    draw_cover_card(
        c, MARGIN_X + card_w + gap, y, card_w, card_h,
        "mountain", "Fortschritt", fmt_eur_cover(cover["freedom_step"]),
        "näher an deinem Ziel", main_size=61, sub_size=22
    )
    draw_cover_card(
        c, MARGIN_X + (card_w + gap) * 2, y, card_w, card_h,
        "trend", "Entwicklung", development, development_sub, main_size=max(development_size, 61), sub_size=22
    )
    draw_footer(c, 1)
    end_page(c)


def draw_financial_story_page(c, data):
    story = data["pages"]["financial_story"]
    begin_page(c, "Financial Story")
    draw_label(c, MARGIN_X, PAGE_H - 145, "Nettovermögen")
    draw_value(c, MARGIN_X, PAGE_H - 185, fmt_eur(story["net_worth"]), 30)
    c.setFont("Helvetica", 12)
    c.setFillColor(GOOD if (story["delta"] or 0) >= 0 else ALERT)
    c.drawString(MARGIN_X, PAGE_H - 210, f"Veränderung: {fmt_delta(story['delta'])}")

    y = PAGE_H - 285
    draw_card(c, MARGIN_X, y, 220, 90, "Cash", fmt_eur(story["cash"]), value_size=18)
    draw_card(c, MARGIN_X + 250, y, 220, 90, "Investments", fmt_eur(story["investments"]), value_size=18)
    draw_body(c, MARGIN_X, y - 130, story["text"], width_chars=78, size=12, leading=18, max_lines=5)
    end_page(c)


def draw_month_page(c, data):
    month = data["pages"]["month"]
    biggest = month["biggest_expense"]
    strongest = month["strongest_category"]
    biggest_value = "Keine Ausgabe" if not biggest else f"{biggest['merchant']} · {fmt_eur(biggest['amount'])}"
    strongest_value = "Noch keine Kategorie" if not strongest else f"{strongest['category']} · {fmt_eur(strongest['total'])}"

    begin_page(c, "Dein Monat")
    y = PAGE_H - 135
    draw_card(c, MARGIN_X, y, 230, 105, "Beste Entscheidung", month["best_decision"], value_size=13)
    draw_card(c, MARGIN_X + 260, y, 230, 105, "Größte Ausgabe", biggest_value, value_size=13)
    y -= 135
    draw_card(c, MARGIN_X, y, 230, 105, "Stärkste Kategorie", strongest_value, value_size=13)
    draw_card(c, MARGIN_X + 260, y, 230, 105, "Fokus", month["focus"], value_size=13)
    end_page(c)


def draw_score_page(c, data):
    score = data["pages"]["score"]
    parts = score["parts"]
    begin_page(c, "Rov.E Score")
    draw_score_circle(c, PAGE_W / 2, PAGE_H - 220, 70, score["clarity_score"])
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(INK)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 320, score["rank_name"])
    c.setFont("Helvetica", 11)
    c.setFillColor(MUTED)
    proof_line = f"{score['phase']} · {score['proof_days']}d verified"
    c.drawCentredString(PAGE_W / 2, PAGE_H - 342, proof_line)
    if score["days_to_unlock"] > 0:
        unlock = f"Noch {score['days_to_unlock']} Tage bis {score['next_unlock_level']}+ freigeschaltet wird."
        c.drawCentredString(PAGE_W / 2, PAGE_H - 360, unlock)

    rows = [
        ("Budget Control", f"{parts.get('budget', 0)}/25"),
        ("Savings Execution", f"{parts.get('savings', 0)}/25"),
        ("Tracking Consistency", f"{parts.get('consistency', 0)}/25"),
        ("Financial Structure", f"{parts.get('structure', 0)}/25"),
    ]
    draw_section_rows(c, MARGIN_X, PAGE_H - 410, rows)
    c.setFont("Helvetica", 10)
    c.setFillColor(MUTED)
    c.drawRightString(PAGE_W - MARGIN_X, 75, score.get("share_cta", "Share Score"))
    end_page(c)


def draw_wealth_journey_page(c, data):
    journey = data["pages"]["wealth_journey"]
    summary = journey["investment_summary"]
    begin_page(c, "Wealth Journey")
    draw_line_chart(c, MARGIN_X, PAGE_H - 355, PAGE_W - 2 * MARGIN_X, 190, journey["points"])
    if not journey["is_visible"]:
        draw_body(c, MARGIN_X, PAGE_H - 390, journey["note"], width_chars=78, size=11, max_lines=2)

    y = PAGE_H - 455
    draw_card(c, MARGIN_X, y, 150, 85, "Sparplan", fmt_eur(summary["recurring_in"]), value_size=15)
    draw_card(c, MARGIN_X + 170, y, 150, 85, "Einmalig", fmt_eur(summary["one_time_in"]), value_size=15)
    draw_card(c, MARGIN_X + 340, y, 150, 85, "Gesamt", fmt_eur(summary["net_contributions"]), value_size=15)
    end_page(c)


def draw_goal_page(c, data):
    goal = data["pages"]["goal"]
    begin_page(c, "Your Goal")
    draw_label(c, MARGIN_X, PAGE_H - 140, "Ziel")
    draw_value(c, MARGIN_X, PAGE_H - 180, goal["description"], 28)
    draw_label(c, MARGIN_X, PAGE_H - 240, "Fortschritt")
    draw_value(c, MARGIN_X, PAGE_H - 275, f"{goal['progress_percent']:.1f}%", 30)
    draw_progress_bar(c, MARGIN_X, PAGE_H - 315, PAGE_W - 2 * MARGIN_X, 14, goal["progress_percent"])
    c.setFont("Helvetica", 12)
    c.setFillColor(INK)
    c.drawString(MARGIN_X, PAGE_H - 370, goal["forecast_text"])
    end_page(c)


def draw_money_map_page(c, data):
    money = data["pages"]["money_map"]
    begin_page(c, "Dein Geld im Überblick")
    categories = money["categories"][:6]
    total = sum(row["total"] for row in categories) or 1
    y = PAGE_H - 145
    for row in categories:
        pct = row["total"] / total * 100
        c.setFont("Helvetica", 11)
        c.setFillColor(INK)
        c.drawString(MARGIN_X, y, row["category"])
        c.drawRightString(PAGE_W - MARGIN_X, y, fmt_eur(row["total"]))
        draw_progress_bar(c, MARGIN_X, y - 18, PAGE_W - 2 * MARGIN_X, 8, pct)
        y -= 48
    if not categories:
        draw_body(c, MARGIN_X, y, "Noch keine Kategorien für diesen Monat.", size=12)

    y = 230
    draw_label(c, MARGIN_X, y, "Erkenntnisse")
    y -= 28
    for insight in money["insights"][:3]:
        y = draw_body(c, MARGIN_X, y, f"- {insight}", width_chars=82, size=11, leading=17, max_lines=2) - 6
    end_page(c)


def draw_milestones_page(c, data):
    milestones = data["pages"]["milestones"]
    rank = milestones["rank"]
    begin_page(c, "Meilensteine")
    draw_label(c, MARGIN_X, PAGE_H - 140, "Level")
    draw_value(c, MARGIN_X, PAGE_H - 180, rank["name"], 30)
    draw_card(c, MARGIN_X, PAGE_H - 245, 230, 95, "Rov.E Points", str(milestones["clarity_points"]), value_size=24)
    draw_card(c, MARGIN_X + 260, PAGE_H - 245, 230, 95, "Bis naechstes Level", str(rank["points_to_next"]), value_size=24)

    y = PAGE_H - 395
    draw_label(c, MARGIN_X, y, "Errungenschaften")
    y -= 32
    badges = milestones["badges"][:5]
    if badges:
        for badge in badges:
            c.setFont("Helvetica", 12)
            c.setFillColor(INK)
            c.drawString(MARGIN_X, y, badge["label"])
            y -= 24
    else:
        draw_body(c, MARGIN_X, y, "Die ersten Meilensteine entstehen mit deiner Nutzung.", size=12)
    end_page(c)


def draw_recap_page(c, data):
    recap = data["pages"]["recap"]
    begin_page(c, "Rov.E Recap")
    y = PAGE_H - 145
    draw_card(c, MARGIN_X, y, PAGE_W - 2 * MARGIN_X, 95, "Was gut lief", recap["what_went_well"], value_size=13)
    y -= 125
    draw_card(c, MARGIN_X, y, PAGE_W - 2 * MARGIN_X, 95, "Was Aufmerksamkeit braucht", recap["needs_attention"], value_size=13)
    y -= 125
    draw_card(c, MARGIN_X, y, PAGE_W - 2 * MARGIN_X, 95, "Nächster Hebel", recap["next_lever"], value_size=13)
    end_page(c)


def draw_closing_page(c, data):
    closing = data["pages"]["closing"]
    begin_page(c)
    c.setFont("Helvetica-Bold", 32)
    c.setFillColor(INK)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 + 20, closing["headline"])
    c.setFont("Helvetica", 12)
    c.setFillColor(MUTED)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 18, closing["message"])


def build_pdf(user_id: int, report_month: str, report_data: dict = None):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report_data = report_data or build_report_data(user_id, report_month)
    file_path = REPORTS_DIR / f"rove_report_{user_id}_{report_month}.pdf"

    # Neues helles PDF (WeasyPrint, gleiche Datenquelle wie der Weblink).
    # Faellt bei jedem Fehler (z.B. WeasyPrint fehlt) automatisch auf den
    # alten, stabilen ReportLab-Renderer zurueck -> Bot bleibt funktionsfaehig.
    try:
        from rove_pdf_light_renderer import build_pdf_report
        build_pdf_report(user_id, report_month, file_path, report_data=report_data)
    except Exception:
        logger.exception("Helles PDF fehlgeschlagen - Fallback auf ReportLab-Renderer")
        from rove_pdf_report_renderer import build_pdf_report as build_pdf_legacy
        build_pdf_legacy(user_id, report_month, file_path, report_data=report_data)
    return file_path, report_data["meta"]["tracked_days"]


REPORTS_ARCHIVE_DIR = REPORTS_DIR / "archive"
REPORT_ARCHIVE_AFTER_DAYS = int(os.getenv("CLARITY_REPORT_ARCHIVE_DAYS", "60"))


def archive_old_reports(days: int = REPORT_ARCHIVE_AFTER_DAYS) -> int:
    """Komprimiert PDFs, die aelter als `days` Tage sind, nach reports/archive/ (gzip).

    Loescht dabei nichts inhaltlich - jede Datei bleibt vollstaendig als .pdf.gz erhalten
    (wichtig fuer den geplanten Jahresreport, der alle Monate zurueckreichen braucht).
    Nur die unkomprimierte Kopie im Hauptordner verschwindet, um Plattenplatz zu sparen.
    """
    if not REPORTS_DIR.exists():
        return 0
    cutoff = datetime.now().timestamp() - days * 86400
    archived = 0
    for pdf_path in REPORTS_DIR.glob("*.pdf"):
        if not pdf_path.is_file() or pdf_path.stat().st_mtime > cutoff:
            continue
        REPORTS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        target = REPORTS_ARCHIVE_DIR / f"{pdf_path.name}.gz"
        if target.exists():
            pdf_path.unlink()
            continue
        with open(pdf_path, "rb") as f_in, gzip.open(target, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        pdf_path.unlink()
        archived += 1
    return archived


def send_report_push(user_id: int, report_month: str) -> None:
    """Gibt nach erfolgreichem Report-Versand einen App-Push in Auftrag.

    Der Bot besitzt absichtlich keine Web-Push-Bibliothek. Der laufende Rov.E-App-Server besitzt
    sie und erhaelt deshalb ausschliesslich ueber localhost einen signierten Auftrag. Fehler bleiben
    folgenlos: Ein Report darf niemals wegen einer Benachrichtigung erneut versendet werden.
    """
    if not APP_PUSH_INTERNAL_SECRET:
        logger.info("Report-Push uebersprungen: interner Push-Zugang ist nicht konfiguriert.")
        return

    month_label = report_month
    try:
        year, month = map(int, report_month.split("-"))
        month_label = f"{GERMAN_MONTHS.get(month, report_month)} {year}"
    except (TypeError, ValueError):
        pass

    payload = json.dumps({
        "user_id": user_id,
        "title": "Dein Rov.E Report ist bereit",
        "body": f"Dein Monatsreport fuer {month_label} wartet in deiner App.",
        "tag": f"rove-report-{report_month}",
        "url": "./",
    }).encode("utf-8")
    req = urllib.request.Request(
        APP_PUSH_INTERNAL_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-RovE-Internal": APP_PUSH_INTERNAL_SECRET,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("ok"):
            logger.info("Report-Push fuer User %s an %s Geraet(e) uebergeben.", user_id, result.get("sent", 0))
        else:
            logger.warning("Report-Push fuer User %s abgelehnt: %s", user_id, result)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        logger.warning("Report-Push fuer User %s fehlgeschlagen: %s", user_id, exc)


def send_report_to_user(user_id: int, report_month: str, bot):
    report_data = build_report_data(user_id, report_month)
    tracked_days = report_data["meta"]["tracked_days"]

    if MIN_TRACKING_DAYS > 0 and tracked_days < MIN_TRACKING_DAYS:
        raise ReportSkipped(f"Zu wenig Tracking-Tage: {tracked_days}/{MIN_TRACKING_DAYS}")

    web_report = None
    try:
        import rove_web_report_renderer
        web_report = rove_web_report_renderer.build_web_report(
            user_id,
            report_month,
            report_data=report_data,
        )
    except Exception as e:
        logger.warning("Rov.E Web-Report konnte nicht erzeugt werden: %s", e)

    if web_report and web_report.get("url"):
        expires_label = web_report["expires_at"].strftime("%d.%m.%Y")
        bot.send_message(
            user_id,
            (
                "Dein Rov.E Web-Report ist bereit.\n\n"
                f"{web_report['url']}\n\n"
                f"Der Link ist bis zum {expires_label} aktiv. "
                "Das PDF bekommst du zusätzlich für deine Unterlagen."
            )
        )

    file_path, tracked_days = build_pdf(user_id, report_month, report_data=report_data)
    with open(file_path, "rb") as f:
        bot.send_document(
            user_id,
            f,
            visible_file_name=file_path.name,
            caption=(
                "Dein Rov.E Report ist fertig.\n\n"
                "Er zeigt dir, was in diesem Monat wirklich passiert ist - "
                "klar, ruhig und ohne unnötige Zahlen.\n\n"
                "Nimm dir kurz Zeit dafür. Du wirst Dinge sehen, die dir sonst entgehen."
            )
        )

    send_report_push(user_id, report_month)

    # Das PDF bleibt nach dem Telegram-Versand im Report-Archiv liegen. Die App kann
    # es dadurch dauerhaft unter "Reports" öffnen; archive_old_reports() komprimiert
    # die Datei später automatisch statt sie zu verlieren.
    return True
