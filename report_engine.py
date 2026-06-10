import os
import sqlite3
import calendar
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

load_dotenv()

DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
REPORTS_DIR = Path(os.getenv("CLARITY_REPORTS_DIR", "reports"))
MIN_TRACKING_DAYS = int(os.getenv("MIN_TRACKING_DAYS", "14"))


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


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
    label = datetime(year, month, 1).strftime("%B %Y")
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


def build_pdf(user_id: int, report_month: str):
    ensure_net_worth_column()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    user = get_user(user_id)
    if not user:
        raise ValueError("User nicht gefunden")

    total_expenses, tracked_days, categories = get_expense_stats(user_id, report_month)
    snapshot = get_snapshot(user_id, report_month)
    prev_snapshot = get_prev_snapshot(user_id, report_month)

    income = row_float(user, "income") + row_float(user, "other_income")
    fixed_costs = row_float(user, "fixed_costs")
    etf_rate = row_float(user, "etf_savings")
    cash_rate = row_float(user, "cash_savings")
    target_amount = row_float(user, "goal_amount")
    current_investments = row_float(user, "current_investments")
    cash_reserve = row_float(user, "current_cash")

    free_budget = income - fixed_costs
    remaining = free_budget - total_expenses
    savings_plan = etf_rate + cash_rate
    savings_quote = (savings_plan / income * 100.0) if income > 0 else 0.0

    net_worth = float(snapshot["net_worth"]) if snapshot and snapshot["net_worth"] is not None else (current_investments + cash_reserve)
    prev_net_worth = float(prev_snapshot["net_worth"]) if prev_snapshot and prev_snapshot["net_worth"] is not None else None
    net_worth_delta = net_worth - prev_net_worth if prev_net_worth is not None else None

    clarity_score = int(snapshot["clarity_score"]) if snapshot and snapshot["clarity_score"] is not None else 0
    budget_ok = bool(snapshot["budget_ok"]) if snapshot and snapshot["budget_ok"] is not None else (remaining >= 0)

    _, _, month_label = month_bounds(report_month)
    file_path = REPORTS_DIR / f"clarity_report_{user_id}_{report_month}.pdf"

    c = canvas.Canvas(str(file_path), pagesize=A4)
    width, height = A4

    dark = HexColor("#111111")
    muted = HexColor("#666666")
    line = HexColor("#E7E7E7")
    green = HexColor("#1F8A4D")
    red = HexColor("#C0392B")

    def hr(y):
        c.setStrokeColor(line)
        c.setLineWidth(1)
        c.line(50, y, width - 50, y)

    y = height - 70
    c.setFont("Helvetica-Bold", 26)
    c.setFillColor(dark)
    c.drawString(50, y, "Clarity Monatsreport")

    y -= 28
    c.setFont("Helvetica", 13)
    c.setFillColor(muted)
    c.drawString(50, y, month_label)

    y -= 38
    hr(y)

    y -= 38
    c.setFont("Helvetica", 11)
    c.setFillColor(muted)
    c.drawString(50, y, "Nettovermoegen")
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(dark)
    c.drawString(50, y - 24, f"{net_worth:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))

    if net_worth_delta is not None:
        c.setFont("Helvetica", 11)
        c.setFillColor(green if net_worth_delta >= 0 else red)
        prefix = "+" if net_worth_delta >= 0 else ""
        c.drawString(50, y - 46, f"vs. Vormonat: {prefix}{net_worth_delta:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))

    c.setFont("Helvetica", 11)
    c.setFillColor(muted)
    c.drawString(320, y, "Clarity Score")
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(dark)
    c.drawString(320, y - 24, f"{clarity_score}/100")

    y -= 90
    hr(y)

    y -= 34
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(dark)
    c.drawString(50, y, "Monatsueberblick")

    y -= 28
    c.setFont("Helvetica", 12)
    c.setFillColor(dark)
    c.drawString(50, y, f"Einkommen gesamt: {income:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    y -= 22
    c.drawString(50, y, f"Fixkosten: {fixed_costs:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    y -= 22
    c.drawString(50, y, f"Ausgaben im Monat: {total_expenses:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    y -= 22
    c.drawString(50, y, f"Restbudget: {remaining:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    y -= 22
    c.drawString(50, y, f"ETF-Sparrate: {etf_rate:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    y -= 22
    c.drawString(50, y, f"Cash-Sparrate: {cash_rate:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
    y -= 22
    c.drawString(50, y, f"Geplante Sparquote: {savings_quote:.1f}%")
    y -= 22
    c.drawString(50, y, f"Tracking-Tage: {tracked_days}")

    y -= 40
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Top Kategorien")

    y -= 26
    c.setFont("Helvetica", 11)
    if categories:
        for row in categories:
            c.drawString(50, y, f"{row['category']}: {float(row['total'] or 0):,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))
            y -= 20
            if y < 100:
                c.showPage()
                y = height - 70
                c.setFont("Helvetica", 11)
    else:
        c.drawString(50, y, "Keine Ausgaben in diesem Monat gefunden.")
        y -= 20

    if y < 170:
        c.showPage()
        y = height - 70

    hr(y - 10)
    y -= 40
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Clarity Insight")

    y -= 28
    c.setFont("Helvetica", 11)
    insight = []
    if budget_ok:
        insight.append("Du hast den Monat innerhalb deines Budgets abgeschlossen.")
    else:
        insight.append("Dein Budget war in diesem Monat unter Druck und verdient im naechsten Monat frueh Aufmerksamkeit.")

    if savings_plan > 0:
        insight.append(f"Dein geplanter Vermoegensaufbau liegt bei {savings_plan:,.2f} EUR pro Monat.".replace(",", "X").replace(".", ",").replace("X", "."))
    else:
        insight.append("Es ist noch keine aktive Sparrate hinterlegt.")

    if target_amount > 0:
        progress = ((current_investments + cash_reserve) / target_amount) * 100.0
        insight.append(f"Dein Ziel ist aktuell zu {progress:.1f}% erreicht.")

    for line_text in insight:
        c.drawString(50, y, line_text)
        y -= 20

    c.save()
    return file_path, tracked_days


def send_report_to_user(user_id: int, report_month: str, bot):
    file_path, tracked_days = build_pdf(user_id, report_month)

    if MIN_TRACKING_DAYS > 0 and tracked_days < MIN_TRACKING_DAYS:
        return False

    with open(file_path, "rb") as f:
        bot.send_document(
            user_id,
            f,
            visible_file_name=file_path.name,
            caption=f"Dein Clarity Report fuer {report_month} ist da."
        )
    return True
