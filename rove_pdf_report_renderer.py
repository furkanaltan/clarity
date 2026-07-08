from __future__ import annotations

import math
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


APP_DIR = Path(__file__).resolve().parent
FONT_DIR = APP_DIR / "report_html" / "report-main" / "fonts" / "Manrope" / "static"

PAGE_W = 1080
PAGE_H = 675
MARGIN = 54

GERMAN_MONTHS = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}


def next_month_name(report_month: str) -> str:
    year, month = map(int, report_month.split("-"))
    next_month = 1 if month == 12 else month + 1
    return GERMAN_MONTHS.get(next_month, "")

BG = colors.HexColor("#08090B")
CARD = colors.HexColor("#111318")
CARD_SOFT = colors.HexColor("#0D171D")
LINE = colors.HexColor("#24272E")
TEXT = colors.HexColor("#F4F1EA")
MUTED = colors.HexColor("#9EA4A0")
BLUE = colors.HexColor("#3BA7FF")
BLUE_SOFT = colors.HexColor("#123043")
GOLD = colors.HexColor("#D8B66A")


def register_fonts() -> None:
    fonts = {
        "RoveSans": "Manrope-Regular.ttf",
        "RoveSans-Medium": "Manrope-Medium.ttf",
        "RoveSans-SemiBold": "Manrope-SemiBold.ttf",
        "RoveSans-Bold": "Manrope-Bold.ttf",
    }
    for name, filename in fonts.items():
        path = FONT_DIR / filename
        if path.exists() and name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))


CORE_FONTS = {"Times-Roman", "Helvetica", "Helvetica-Bold", "Times-Bold"}


def font(name: str) -> str:
    if name in CORE_FONTS or name in pdfmetrics.getRegisteredFontNames():
        return name
    return {
        "RoveSans": "Helvetica",
        "RoveSans-Medium": "Helvetica",
        "RoveSans-SemiBold": "Helvetica-Bold",
        "RoveSans-Bold": "Helvetica-Bold",
    }.get(name, "Helvetica")


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def money(value, decimals: int = 0) -> str:
    amount = safe_float(value)
    formatted = f"{amount:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if decimals == 0:
        formatted = formatted.replace(",0", "")
    return f"{formatted} €"


def percent(value, decimals: int = 1, fallback: str = "ab Monat 2") -> str:
    if value is None:
        return fallback
    return f"{safe_float(value):.{decimals}f} %".replace(".", ",")


def clean_text(value) -> str:
    text = str(value or "").strip()
    replacements = {
        "EUR": "€",
        "Clarity": "Rov.E",
        "clarity": "Rov.E",
        " - ": " - ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"(\d+)\.(\d{2})\s*€", r"\1,\2 €", text)
    return text


def badge_label(value) -> str:
    text = clean_text(value)
    if re.match(r"inv_\d{4}_\d{2}$", text):
        return "Investment bestätigt"
    if text == "first_investment":
        return "Erstes Investment"
    return text.replace("_", " ").strip().title() if "_" in text else text


def wrap_text(text: str, max_width: float, font_name: str, size: float, max_lines: int | None = None) -> list[str]:
    words = clean_text(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and pdfmetrics.stringWidth(candidate, font_name, size) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;:") + " ..."
    return lines


def draw_wrapped(c, text: str, x: float, y: float, max_width: float, size: float = 12,
                 leading: float = 18, color=TEXT, font_name: str = "RoveSans",
                 max_lines: int | None = None) -> float:
    resolved = font(font_name)
    c.setFont(resolved, size)
    c.setFillColor(color)
    for line in wrap_text(text, max_width, resolved, size, max_lines):
        c.drawString(x, y, line)
        y -= leading
    return y


def begin_page(c, section: str, title: str, page_no: int) -> None:
    c.setFillColor(BG)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    draw_glow(c, PAGE_W - 170, PAGE_H - 120, 210, colors.Color(0.09, 0.35, 0.50, alpha=0.18))
    draw_glow(c, 130, 90, 240, colors.Color(0.06, 0.26, 0.34, alpha=0.12))
    c.setFont(font("RoveSans-Bold"), 9)
    c.setFillColor(BLUE)
    c.drawString(MARGIN, PAGE_H - 80, section.upper())
    c.setFont("Times-Roman", 38)
    c.setFillColor(TEXT)
    c.drawString(MARGIN, PAGE_H - 126, title)
    c.setFont("Times-Roman", 78)
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.035))
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 100, f"{page_no:02d}")


def end_page(c, page_no: int) -> None:
    c.setStrokeColor(colors.Color(1, 1, 1, alpha=0.08))
    c.setLineWidth(0.8)
    c.line(MARGIN, 36, PAGE_W - MARGIN, 36)
    c.setFont(font("RoveSans-Medium"), 7.5)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, 22, "Rov.E Report")
    c.drawCentredString(PAGE_W / 2, 22, "Persönlich & vertraulich")
    c.drawRightString(PAGE_W - MARGIN, 22, f"{page_no} / 10")
    c.showPage()


def draw_glow(c, x: float, y: float, radius: float, color) -> None:
    # ReportLab has no blur; layered transparent circles create a quiet glow.
    for i in range(8, 0, -1):
        alpha = 0.012 * i
        c.setFillColor(colors.Color(color.red, color.green, color.blue, alpha=alpha))
        r = radius * i / 8
        c.circle(x, y, r, fill=1, stroke=0)


def draw_card(c, x: float, y: float, w: float, h: float, label: str, value: str,
              sub: str | None = None, accent: bool = False, value_size: float = 28,
              wrap_value: bool = False) -> None:
    c.setFillColor(CARD_SOFT if accent else CARD)
    c.setStrokeColor(colors.Color(BLUE.red, BLUE.green, BLUE.blue, alpha=0.35) if accent else LINE)
    c.setLineWidth(1.0)
    c.roundRect(x, y - h, w, h, 15, fill=1, stroke=1)
    c.setFont(font("RoveSans-Bold"), 8)
    c.setFillColor(BLUE if accent else MUTED)
    c.drawString(x + 22, y - 28, label.upper())
    c.setFont("Times-Roman", value_size)
    c.setFillColor(BLUE if accent else TEXT)
    if wrap_value:
        draw_wrapped(c, clean_text(value), x + 22, y - 52, w - 44, size=value_size,
                     leading=value_size * 1.25, color=BLUE if accent else TEXT,
                     font_name="Times-Roman", max_lines=3)
    else:
        c.drawString(x + 22, y - 67, clean_text(value))
    if sub:
        draw_wrapped(c, sub, x + 22, y - 91, w - 44, size=9.5, leading=13, color=MUTED, max_lines=2)


def draw_bar(c, x: float, y: float, w: float, h: float, ratio: float, color=BLUE) -> None:
    ratio = max(0.0, min(1.0, ratio))
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.07))
    c.roundRect(x, y, w, h, h / 2, fill=1, stroke=0)
    c.setFillColor(color)
    c.roundRect(x, y, w * ratio, h, h / 2, fill=1, stroke=0)


def draw_ring(c, x: float, y: float, r: float, ratio: float) -> None:
    ratio = max(0.0, min(1.0, ratio))
    c.setStrokeColor(colors.Color(1, 1, 1, alpha=0.08))
    c.setLineWidth(11)
    c.circle(x, y, r, fill=0, stroke=1)
    c.setStrokeColor(BLUE)
    c.setLineWidth(12)
    extent = -360 * ratio
    c.arc(x - r, y - r, x + r, y + r, 90, extent)


def top_category(data: dict) -> dict:
    categories = data["pages"]["money_map"].get("categories", [])
    return categories[0] if categories else {"category": "Noch offen", "total": 0}


def biggest_expense(data: dict) -> dict | None:
    return data["pages"]["month"].get("biggest_expense")


def draw_cover(c, data):
    cover = data["pages"]["cover"]
    month = data["meta"]["month_label"]
    begin_page(c, "Dein Monatsabschluss", "", 1)
    c.setFont("Times-Roman", 88)
    c.setFillColor(TEXT)
    c.drawString(MARGIN, PAGE_H - 205, "Rov.E")
    c.setFont("Times-Italic", 88)
    c.setFillColor(BLUE)
    c.drawString(MARGIN, PAGE_H - 286, "Report")
    draw_wrapped(
        c,
        "Vermögen, Verhalten und der nächste Schritt zu deinem Ziel - ehrlich ausgewertet, auf den Punkt gebracht.",
        MARGIN,
        PAGE_H - 336,
        410,
        size=15,
        leading=22,
        color=MUTED,
        font_name="RoveSans-Medium",
        max_lines=3,
    )
    card_y = 170
    card_w = (PAGE_W - 2 * MARGIN - 28) / 3
    draw_card(c, MARGIN, card_y, card_w, 112, "Zeitraum", month, value_size=30)
    draw_card(c, MARGIN + card_w + 14, card_y, card_w, 112, "Fortschritt", money(cover.get("freedom_step")), "näher an deinem Ziel", True, 30)
    draw_card(c, MARGIN + (card_w + 14) * 2, card_y, card_w, 112, "Entwicklung", percent(cover.get("development_percent")), "zum Vormonat", False, 30)
    c.setFont(font("RoveSans-Bold"), 8)
    c.setFillColor(MUTED)
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 46, "AUSGABE 01 · PERSÖNLICH & VERTRAULICH")
    end_page(c, 1)


def draw_overview(c, data):
    story = data["pages"]["financial_story"]
    month = data["pages"]["month"]
    begin_page(c, "Überblick", "Dein Monat auf einen Blick.", 2)
    draw_card(c, 120, 460, 840, 130, "Nettovermögen", money(story["net_worth"]), value_size=42)
    draw_card(c, 120, 304, 405, 112, "Investments", money(story["investments"]), accent=True, value_size=34)
    draw_card(c, 555, 304, 405, 112, "Cash", money(story["cash"]), value_size=34)
    draw_card(c, 120, 160, 405, 100, "Ausgaben", money(month["total_expenses"]), value_size=30)
    draw_card(c, 555, 160, 405, 100, "Restbudget", money(month["remaining_budget"]), value_size=30)
    end_page(c, 2)


def draw_month(c, data):
    month = data["pages"]["month"]
    biggest = biggest_expense(data)
    strongest = top_category(data)
    begin_page(c, "Insight", "Was diesen Monat wirklich auffällt.", 3)
    draw_card(c, 110, 468, 860, 92, "Beste Entscheidung", clean_text(month["best_decision"]), value_size=23, accent=True)
    biggest_value = "Noch keine Ausgabe"
    biggest_sub = ""
    if biggest:
        biggest_value = f"{money(biggest.get('amount'))} · {biggest.get('merchant') or 'Ausgabe'}"
        biggest_sub = biggest.get("category") or ""
    draw_card(c, 110, 348, 410, 96, "Größte Ausgabe", biggest_value, biggest_sub, value_size=24)
    draw_card(c, 560, 348, 410, 96, "Stärkste Kategorie", f"{money(strongest.get('total'))} · {strongest.get('category')}", value_size=24)
    draw_card(c, 110, 222, 410, 96, "Tracking-Tage", f"{data['meta'].get('tracked_days', 0)} Tage", "Datenbasis für deinen Report", value_size=24)
    draw_card(c, 560, 222, 410, 96, "Fokus", clean_text(month["focus"]), value_size=20)
    end_page(c, 3)


def draw_story(c, data):
    story = data["pages"]["financial_story"]
    profile = data["profile"]
    begin_page(c, "Financial Story", "Was dein Vermögen diesen Monat erzählt.", 4)
    draw_card(c, 100, 470, 420, 130, "Nettovermögen", money(story["net_worth"]), value_size=42, accent=True)
    draw_wrapped(c, story["text"], 570, 444, 380, 16, 24, MUTED, "RoveSans-Medium", 4)
    total = max(safe_float(story["cash"]) + safe_float(story["investments"]), 1)
    y = 260
    c.setFont(font("RoveSans-Bold"), 8)
    c.setFillColor(MUTED)
    c.drawString(110, y + 58, "AUFTEILUNG")
    draw_bar(c, 110, y + 30, 760, 14, safe_float(story["investments"]) / total, BLUE)
    c.setFont(font("RoveSans-Medium"), 11)
    c.setFillColor(TEXT)
    c.drawString(110, y, f"Investments: {money(story['investments'])}")
    c.drawString(430, y, f"Cash: {money(story['cash'])}")
    c.drawString(700, y, f"Sparrate: {percent(profile.get('savings_rate'), 1, '0,0 %')}")
    end_page(c, 4)


def draw_money_map(c, data):
    money_map = data["pages"]["money_map"]
    categories = money_map.get("categories", [])[:5]
    begin_page(c, "Money Map", "Wo dein Geld wirklich hingeht.", 5)
    total = sum(safe_float(row.get("total")) for row in categories) or 1
    y = 438
    if categories:
        for row in categories:
            label = clean_text(row.get("category") or "Sonstiges")
            amount = safe_float(row.get("total"))
            c.setFont(font("RoveSans-SemiBold"), 13)
            c.setFillColor(TEXT)
            c.drawString(130, y + 10, label)
            c.setFillColor(BLUE)
            c.drawRightString(900, y + 10, money(amount))
            draw_bar(c, 130, y - 14, 770, 10, amount / total, BLUE)
            y -= 58
    else:
        draw_card(
            c,
            130,
            410,
            790,
            120,
            "Datenbasis",
            "Noch keine Kategorien sichtbar.",
            "Sobald du Ausgaben trackst, entsteht hier deine Money Map.",
            value_size=24,
        )
    insight_y = 146
    c.setFont(font("RoveSans-Bold"), 8)
    c.setFillColor(BLUE)
    c.drawString(130, insight_y + 62, "ERKENNTNIS")
    insight = (money_map.get("insights") or ["Noch entsteht deine Datenbasis."])[-1]
    draw_wrapped(c, insight, 130, insight_y + 40, 790, 14, 20, MUTED, "RoveSans-Medium", 3)
    end_page(c, 5)


def draw_score(c, data):
    score = data["pages"]["score"]
    parts = score["parts"]
    score_value = safe_float(score["clarity_score"])
    begin_page(c, "Rov.E Score", f"{int(score_value)} von 100 - du hast dein Geld im Griff.", 6)
    draw_card(c, 85, 470, 360, 280, "Score", "", accent=False)
    draw_ring(c, 265, 342, 74, score_value / 100)
    c.setFont("Times-Roman", 48)
    c.setFillColor(TEXT)
    c.drawCentredString(265, 324, str(int(score_value)))
    c.setFont(font("RoveSans-Bold"), 9)
    c.setFillColor(BLUE)
    c.drawCentredString(265, 286, score.get("rank_name", "Rookie").upper())
    c.setFont(font("RoveSans-Bold"), 7.5)
    c.drawCentredString(265, 244, "SCORE TEILEN")
    draw_card(c, 485, 470, 500, 280, "Breakdown", "", value_size=10)
    rows = [
        ("Budget Control", parts.get("budget", 0), BLUE),
        ("Savings Execution", parts.get("savings", 0), BLUE),
        ("Tracking Consistency", parts.get("consistency", 0), GOLD),
        ("Financial Structure", parts.get("structure", 0), BLUE),
    ]
    y = 414
    for label, val, col in rows:
        c.setFont(font("RoveSans-Medium"), 12)
        c.setFillColor(TEXT if col == BLUE else GOLD)
        c.drawString(520, y, label)
        c.setFont("Times-Roman", 20)
        c.setFillColor(col)
        c.drawRightString(920, y - 2, f"{int(val)}/25")
        c.setStrokeColor(colors.Color(1, 1, 1, alpha=0.08))
        c.line(520, y - 24, 920, y - 24)
        y -= 55
    draw_card(c, 85, 152, 900, 92, f"Was {score.get('rank_name', 'Rookie')} bedeutet", "Budget und Struktur stehen. Der nächste Hebel ist Konstanz.", value_size=18)
    end_page(c, 6)


def draw_goal(c, data):
    goal = data["pages"]["goal"]
    profile = data["profile"]
    current = profile.get("net_worth", 0)
    target = goal.get("target_amount", 0)
    progress = safe_float(goal.get("progress_percent")) / 100
    remaining = max(0, safe_float(target) - safe_float(current))
    begin_page(c, "Dein Ziel", f"Dein {goal.get('description', 'Ziel')} rückt näher.", 7)
    draw_card(c, 95, 455, 890, 120, "Ziel", clean_text(goal.get("description") or "Dein Ziel"), f"{percent(goal.get('progress_percent'), 1, '0,0 %')} erreicht", True, 42)
    draw_bar(c, 120, 290, 830, 14, progress, BLUE)
    draw_card(c, 95, 250, 280, 92, "Zielbetrag", money(target), value_size=25)
    draw_card(c, 400, 250, 280, 92, "Aktueller Stand", money(current), accent=True, value_size=25)
    draw_card(c, 705, 250, 280, 92, "Noch", money(remaining), value_size=25)
    draw_card(c, 95, 138, 890, 110, "Ehrlich gesagt", clean_text(goal.get("forecast_text")), value_size=16, wrap_value=True)
    end_page(c, 7)


def draw_milestones(c, data):
    milestones = data["pages"]["milestones"]
    rank = milestones.get("rank", {})
    badges = milestones.get("badges", [])[:4]
    begin_page(c, "Meilensteine", "Was du dir bereits aufgebaut hast.", 8)
    draw_card(c, 100, 460, 400, 120, "RP-Level", clean_text(rank.get("name", "Rookie")), f"{milestones.get('clarity_points', 0)} RP", accent=True, value_size=34)
    draw_card(c, 540, 460, 400, 120, "Bis nächstes Level", str(rank.get("points_to_next", 0)), value_size=34)
    y = 290
    if not badges:
        badges = [{"label": "Erste Datenbasis entsteht"}, {"label": "Monatsreport freigeschaltet"}]
    for badge in badges:
        draw_card(c, 140, y, 800, 70, "Erfolg", badge_label(badge.get("label", badge)), value_size=20)
        y -= 86
    end_page(c, 8)


def draw_recap(c, data):
    recap = data["pages"]["recap"]
    begin_page(c, "Recap", "Der Monat in drei klaren Sätzen.", 9)
    draw_card(c, 105, 454, 870, 100, "Was gut lief", clean_text(recap["what_went_well"]), accent=True, value_size=19)
    draw_card(c, 105, 315, 870, 100, "Was Aufmerksamkeit braucht", clean_text(recap["needs_attention"]), value_size=19)
    draw_card(c, 105, 176, 870, 100, "Dein größter Hebel", clean_text(recap["next_lever"]), value_size=19)
    end_page(c, 9)


def draw_closing(c, data):
    closing = data["pages"]["closing"]
    month = next_month_name(data["meta"]["report_month"])
    begin_page(c, f"Plan für den nächsten Monat", f"Dein Plan für {month}.", 10)
    steps = [
        ("01", "Tracke an mindestens 10 Tagen.", "Damit wird dein Monatsbild deutlich klarer."),
        ("02", "Halte deine stärkste Kategorie bewusst im Blick.", "Nicht verzichten - nur früher erkennen."),
        ("03", "Bleib bei deiner Sparrate.", "Konstanz macht aus einem guten Monat eine Gewohnheit."),
    ]
    y = 424
    for number, title, sub in steps:
        c.setFillColor(CARD)
        c.setStrokeColor(LINE)
        c.roundRect(105, y - 70, 870, 74, 14, fill=1, stroke=1)
        c.setFont("Times-Roman", 34)
        c.setFillColor(colors.Color(1, 1, 1, alpha=0.16))
        c.drawString(130, y - 42, number)
        c.setFont("Times-Roman", 22)
        c.setFillColor(TEXT)
        c.drawString(210, y - 26, title)
        c.setFont(font("RoveSans-Medium"), 10)
        c.setFillColor(MUTED)
        c.drawString(210, y - 48, sub)
        y -= 98
    c.setFont("Times-Italic", 24)
    c.setFillColor(BLUE)
    c.drawCentredString(PAGE_W / 2, 88, clean_text(closing["headline"]))
    end_page(c, 10)


def build_pdf_report(user_id: int, report_month: str, output_path: Path, report_data: dict | None = None) -> Path:
    """Build a static, archive-ready Rov.E PDF report."""
    register_fonts()
    if report_data is None:
        from report_engine import build_report_data
        report_data = build_report_data(user_id, report_month)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Rov.E Report {report_month}")
    c.setAuthor("Rov.E")

    draw_cover(c, report_data)
    draw_overview(c, report_data)
    draw_month(c, report_data)
    draw_story(c, report_data)
    draw_money_map(c, report_data)
    draw_score(c, report_data)
    draw_goal(c, report_data)
    draw_milestones(c, report_data)
    draw_recap(c, report_data)
    draw_closing(c, report_data)

    c.save()
    return output_path
