from __future__ import annotations

import html
import re
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from report_engine import GERMAN_MONTHS, build_report_data


REPORT_BUNDLE_DIR = Path(__file__).resolve().parent / "report_html" / "report-main"
PAGE_DIR = REPORT_BUNDLE_DIR / "pages"
GENERATED_DIR = REPORT_BUNDLE_DIR / "generated"
HELL_REFERENCE_TEMPLATE = Path(__file__).resolve().parent / "report_templates" / "rove_pdf_hell_original.html"
HELL_PAGES_TEMPLATE = Path(__file__).resolve().parent / "report_templates" / "rove_pdf_hell_pages.html"

PAGE_FILES = [
    ("01-cover.html", "Rov.E Report"),
    ("02-financial-story.html", "Überblick"),
    ("03-dein-monat.html", "Insight"),
    ("04-clarity-score.html", "Financial Story"),
    ("05-wealth-journey.html", "Money Map"),
    ("06-your-goal.html", "Rov.E Score"),
    ("07-money-map.html", "Your Goal"),
    ("08-meilensteine.html", "Meilensteine"),
    ("09-clarity-recap.html", "Rov.E Recap"),
    ("10-closing.html", "Plan"),
]


LOGO_SVG = """
<svg
  class="logo-icon"
  viewBox="0 0 24 24"
  fill="none"
  xmlns="http://www.w3.org/2000/svg"
>
  <circle cx="12" cy="12" r="9.5" stroke="#111111" stroke-width="1.3" />
  <path
    d="M15.5 8.5A5 5 0 1 0 15.5 15.5"
    stroke="#111111"
    stroke-width="1.3"
    stroke-linecap="round"
    fill="none"
  />
</svg>
""".strip()


def h(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def humanize_text(text: str) -> str:
    text = str(text or "")
    text = re.sub(
        r"(-?\d+(?:\.\d+)?)\s*EUR",
        lambda match: fmt_money(float(match.group(1)), 0),
        text,
    )
    text = text.replace("Monat(en)", "Monaten")
    return text


def fmt_money(value, decimals: int = 0) -> str:
    try:
        amount = float(value or 0)
    except Exception:
        amount = 0.0
    formatted = f"{amount:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if decimals == 0:
        formatted = formatted.replace(",0", "")
    return f"{formatted} €"


def fmt_percent(value, decimals: int = 1) -> str:
    if value is None:
        return "—"
    try:
        amount = float(value)
    except Exception:
        amount = 0.0
    formatted = f"{amount:.{decimals}f}".replace(".", ",")
    if decimals == 0:
        formatted = formatted.replace(",0", "")
    return f"{formatted} %"


def split_month_label(month_label: str) -> tuple[str, str]:
    if " " not in month_label:
        return month_label.upper(), ""
    month_name, year = month_label.rsplit(" ", 1)
    return month_name.upper(), year


def month_name_only(month_label: str) -> str:
    month_name, _year = split_month_label(month_label)
    return month_name.title()


def clean_goal_description(value: str) -> str:
    text = str(value or "").strip()
    command_values = {
        "/zurueck",
        "/zurück",
        "zurueck",
        "zurück",
        "back",
        "/back",
    }
    if not text or text.lower() in command_values:
        return "Dein Ziel"
    return text


def next_month_label(report_month: str) -> str:
    year, month = map(int, report_month.split("-"))
    if month == 12:
        month = 1
    else:
        month += 1
    return GERMAN_MONTHS[month].upper()


def replace_first(text: str, pattern: str, replacement: str, flags: int = 0) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count == 0:
        raise ValueError(f"Pattern not found: {pattern}")
    return result


def replace_all_sequence(text: str, pattern: str, replacements: list[str], flags: int = 0) -> str:
    iterator = iter(replacements)

    def repl(_match):
        try:
            return next(iterator)
        except StopIteration as exc:
            raise ValueError(f"Too many matches for pattern: {pattern}") from exc

    result, count = re.subn(pattern, repl, text, flags=flags)
    if count < len(replacements):
        raise ValueError(f"Missing matches for pattern: {pattern}")
    return result


def extract_main_contents(page_html: str) -> str:
    match = re.search(r"<main\b[^>]*>([\s\S]*?)</main>", page_html, flags=re.IGNORECASE)
    if not match:
        raise ValueError("Page file does not contain a <main> block.")
    return match.group(1).strip()


def build_footer(page_number: int, title: str = "") -> str:
    return f"""
        <div class="page-footer">
          <div class="logo">
            {LOGO_SVG}
            ROV.E
          </div>
          <div class="footer-title"></div>
          <div class="page-num">{page_number} / 10</div>
        </div>
""".rstrip()


def month_summary_text(data: dict) -> str:
    month = data["pages"]["month"]
    strongest = month["strongest_category"]
    biggest = month["biggest_expense"]
    lines = []
    if month["tracked_days"] > 0:
        lines.append(f"Du hast an {month['tracked_days']} Tagen aktiv getrackt.")
    lines.append(month["best_decision"])
    if strongest:
        lines.append(
            f"Die stärkste Kategorie war {strongest['category']} mit {fmt_money(strongest['total'])}."
        )
    if biggest:
        merchant = biggest["merchant"] or "Unbekannt"
        lines.append(f"Die größte Einzelbuchung ging an {merchant}.")
    lines.append(month["focus"])
    return " ".join(h(humanize_text(line)) for line in lines[:4])


def story_paragraphs(data: dict) -> list[str]:
    story = data["pages"]["financial_story"]
    net_worth = max(data["profile"]["net_worth"], 1)
    cash_share = data["profile"]["cash_reserve"] / net_worth * 100
    investment_share = data["profile"]["current_investments"] / net_worth * 100
    items = [
        humanize_text(story["text"]),
        f"Cash macht aktuell {fmt_percent(cash_share, 1)} deines Nettovermögens aus.",
        f"Investments tragen {fmt_percent(investment_share, 1)} zu deiner Vermögensbasis bei.",
    ]
    return [h(item) for item in items]


def recap_lists(data: dict) -> dict:
    month = data["pages"]["month"]
    score = data["pages"]["score"]
    recap = data["pages"]["recap"]
    investment_summary = data["pages"]["wealth_journey"]["investment_summary"]
    good = [humanize_text(recap["what_went_well"])]
    if month["tracked_days"] > 0:
        good.append(f"{month['tracked_days']} Tracking-Tage geben dir bereits eine klare Datenbasis.")
    if investment_summary["recurring_in"] > 0:
        good.append(
            f"Dein Sparplan wurde mit {fmt_money(investment_summary['recurring_in'])} sauber erfasst."
        )

    attention = [humanize_text(recap["needs_attention"])]
    if month["biggest_expense"]:
        biggest = month["biggest_expense"]
        attention.append(
            f"Die größte Einzelbuchung war {biggest['merchant']} mit {fmt_money(biggest['amount'])}."
        )
    if score["parts"]["budget"] < 15:
        attention.append("Dein freies Budget verdient im nächsten Monat noch mehr Aufmerksamkeit.")

    lever = [humanize_text(recap["next_lever"])]
    if data["pages"]["goal"]["months_to_goal"]:
        lever.append(
            f"Dein Ziel ist bei Konstanz in rund {data['pages']['goal']['months_to_goal']} Monaten erreichbar."
        )
    if score["parts"]["consistency"] < 25:
        lever.append("Mehr regelmäßiges Tracking stärkt deinen Score sichtbar.")

    def unique(items: list[str]) -> list[str]:
        seen = set()
        ordered = []
        for item in items:
            key = item.strip()
            semantic_key = re.sub(r"\d+(?:[,.]\d+)?\s*€", "betrag", key.lower())
            if "sparplan" in semantic_key:
                semantic_key = "sparplan"
            if key and semantic_key not in seen:
                seen.add(key)
                seen.add(semantic_key)
                ordered.append(item)
        return ordered[:3]

    return {
        "good": unique(good),
        "attention": unique(attention),
        "lever": unique(lever),
    }


def build_recap_list(items: list[str], tone: str) -> str:
    dot_class = {
        "good": "li-dot",
        "attention": "li-dot warn",
        "lever": "li-dot arrow",
    }[tone]
    symbol = {"good": "✓", "attention": "!", "lever": "›"}[tone]
    rows = []
    for item in items:
        rows.append(
            f'<li><div class="{dot_class}">{symbol}</div>{h(item)}</li>'
        )
    return "\n".join(rows)


def build_money_rows(categories: list[dict]) -> str:
    if not categories:
        return (
            '<div class="bar-row"><span class="bar-name">Noch keine Daten</span>'
            '<div class="bar-track"><div class="bar-fill" style="width: 8%"></div></div>'
            '<span class="bar-amt">0 €</span></div>'
        )
    colors = ["", " blue", " sand", " mauve", " rose"]
    max_total = max(float(row["total"] or 0) for row in categories[:5]) or 1
    rows = []
    for idx, row in enumerate(categories[:5]):
        width = max(8.0, min(100.0, float(row["total"] or 0) / max_total * 100.0))
        color = colors[idx % len(colors)]
        rows.append(
            '<div class="bar-row">'
            f'<span class="bar-name">{h(row["category"])}</span>'
            '<div class="bar-track">'
            f'<div class="bar-fill{color}" style="width: {width:.1f}%"></div>'
            "</div>"
            f'<span class="bar-amt">{h(fmt_money(row["total"]))}</span>'
            "</div>"
        )
    return "\n".join(rows)


def build_insight_tiles(insights: list[str]) -> str:
    fallback = ["Der erste Monatsvergleich entsteht, sobald mehr Daten vorliegen."]
    icons = [
        """<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#888" stroke-width="1.3" stroke-linecap="round"><line x1="5" y1="2" x2="5" y2="7" /><line x1="3" y1="2" x2="3" y2="7" /><line x1="7" y1="2" x2="7" y2="7" /><path d="M3 7a2 2 0 0 0 4 0" /><line x1="5" y1="9" x2="5" y2="14" /><path d="M10 2v5l2 2v5" /></svg>""",
        """<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#888" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2 5h12l-1.5 8H3.5L2 5z" /><path d="M5.5 5V4a2.5 2.5 0 0 1 5 0v1" /></svg>""",
        """<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#888" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13L13 3M7 3h6v6" /></svg>""",
    ]
    rows = []
    for idx, insight in enumerate((insights or fallback)[:3]):
        rows.append(
            '<div class="card insight-tile">'
            f'<div class="round-icon it-icon">{icons[idx % len(icons)]}</div>'
            f"<p>{h(insight)}</p>"
            "</div>"
        )
    return "\n".join(rows)


def build_badge_tiles(data: dict) -> str:
    milestones = data["pages"]["milestones"]
    badges = milestones["badges"][:3]
    tiles = []
    for badge in badges:
        label = badge["label"]
        if str(badge.get("key", "")).startswith("inv_"):
            label = "Investment bestätigt"
        earned = ""
        if badge.get("earned_at"):
            try:
                earned = datetime.fromisoformat(str(badge["earned_at"]).replace("Z", "+00:00")).strftime("%d.%m.%Y")
            except Exception:
                earned = str(badge["earned_at"])[:10]
        tiles.append(
            '<div class="badge-tile">'
            '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#888" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M3 13L13 3M7 3h6v6" /></svg>'
            "<div>"
            f'<div class="b-name">{h(label)}</div>'
            f'<div class="b-desc">Freigeschaltet {h(earned or "diesen Monat")}</div>'
            "</div>"
            '<div class="badge-check done">✓</div>'
            "</div>"
        )
    if not tiles:
        rank = milestones["rank"]["name"]
        tiles.append(
            '<div class="badge-tile">'
            '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#888" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="1.5" y="8" width="3" height="6" rx="0.5" />'
            '<rect x="6.5" y="5" width="3" height="9" rx="0.5" />'
            '<rect x="11.5" y="2" width="3" height="12" rx="0.5" /></svg>'
            "<div>"
            f'<div class="b-name">{h(rank)}</div>'
            '<div class="b-desc">Dein aktueller Rang im Rov.E System</div>'
            "</div>"
            '<div class="badge-check">○</div>'
            "</div>"
        )
    return "\n".join(tiles)


def milestone_progress(net_worth: float) -> dict:
    step = 5000
    current = max(0.0, float(net_worth or 0))
    reached = int(current // step) * step
    target = reached + step
    progress = 100.0 if target == reached else (current - reached) / (target - reached) * 100.0
    return {
        "reached": reached,
        "target": target,
        "remaining": max(0.0, target - current),
        "progress": max(0.0, min(100.0, progress)),
    }


def build_chart_svg(points: list[dict]) -> str:
    if len(points) < 2:
        return (
            '<svg viewBox="0 0 960 160" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
            '<line x1="0" y1="20" x2="960" y2="20" stroke="#E8E8E8" stroke-width="0.8" stroke-dasharray="4,4" />'
            '<line x1="0" y1="60" x2="960" y2="60" stroke="#E8E8E8" stroke-width="0.8" stroke-dasharray="4,4" />'
            '<line x1="0" y1="100" x2="960" y2="100" stroke="#E8E8E8" stroke-width="0.8" stroke-dasharray="4,4" />'
            '<line x1="0" y1="140" x2="960" y2="140" stroke="#E8E8E8" stroke-width="0.8" stroke-dasharray="4,4" />'
            '<text x="480" y="88" text-anchor="middle" fill="#888888" font-size="14">Kurve ab Monat 2 sichtbar</text>'
            "</svg>"
        )

    values = [float(point["net_worth"] or 0) for point in points]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    coords = []
    for idx, value in enumerate(values):
        x = 960 * idx / max(1, len(values) - 1)
        y = 140 - ((value - low) / span) * 120
        coords.append(f"{x:.1f},{y:.1f}")
    grid = "\n".join(
        f'<line x1="0" y1="{y}" x2="960" y2="{y}" stroke="#E8E8E8" stroke-width="0.8" stroke-dasharray="4,4" />'
        for y in (20, 60, 100, 140)
    )
    end_x, end_y = coords[-1].split(",")
    return (
        '<svg viewBox="0 0 960 160" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
        f"{grid}"
        f'<polyline fill="none" stroke="#111111" stroke-width="1.8" stroke-linejoin="round" points="{" ".join(coords)}" />'
        f'<circle cx="{end_x}" cy="{end_y}" r="5" fill="#2D6A4F" />'
        "</svg>"
    )


def build_axis_labels(points: list[dict], fallback_label: str) -> str:
    if not points:
        return f'<span class="axis-tick">{h(month_name_only(fallback_label))}</span>'
    labels = []
    for point in points[-5:]:
        month_key = point["month"]
        year, month = map(int, month_key.split("-"))
        labels.append(f'<span class="axis-tick">{h(GERMAN_MONTHS[month][:3])}</span>')
    return "\n".join(labels)


def render_cover(page_html: str, data: dict) -> str:
    cover = data["pages"]["cover"]
    month_name, year = split_month_label(cover["period"])
    development_value = fmt_percent(cover["development_percent"], 1) if cover["development_percent"] is not None else "—"
    development_sub = "zum Vormonat" if cover["development_percent"] is not None else "ab Monat 2 sichtbar"
    page_html = replace_first(page_html, r'(<span class="cover-date">)(.*?)(</span>)', rf"\g<1>{h(month_name)}\g<3>")
    page_html = replace_first(page_html, r'(<span class="cover-year">)(.*?)(</span>)', rf"\g<1>{h(year)}\g<3>")
    page_html = replace_all_sequence(
        page_html,
        r'<div class="big-num">.*?</div>',
        [
            f'<div class="big-num">{h(fmt_money(cover["freedom_step"], 0))}</div>',
            f'<div class="big-num">{h(development_value)}</div>',
        ],
        flags=re.S,
    )
    page_html = replace_all_sequence(
        page_html,
        r'<div class="big-num-label">.*?</div>',
        [
            '<div class="big-num-label">näher an deinem Ziel</div>',
            f'<div class="big-num-label">{h(development_sub)}</div>',
        ],
        flags=re.S,
    )
    return page_html


def render_financial_story(page_html: str, data: dict) -> str:
    story = data["pages"]["financial_story"]
    net_worth = max(data["profile"]["net_worth"], 1)
    cash_share = data["profile"]["cash_reserve"] / net_worth * 100
    inv_share = data["profile"]["current_investments"] / net_worth * 100
    delta_text = fmt_money(story["delta"], 0) if story["delta"] is not None else "—"
    delta_sub = "zum Vormonat" if story["delta"] is not None else "ab Monat 2 sichtbar"
    page_html = replace_first(page_html, r'(<div class="fin-hero-value">).*?(</div>)', rf"\g<1>{h(fmt_money(story['net_worth'], 0))}\g<2>", flags=re.S)
    page_html = replace_first(page_html, r'(<div class="delta">).*?(</div>)', rf"\g<1>{h(delta_text)}\g<2>", flags=re.S)
    page_html = replace_all_sequence(
        page_html,
        r'<div class="delta-sub">.*?</div>',
        ['<div class="delta-sub">näher an deinem Ziel</div>', f'<div class="delta-sub">{h(delta_sub)}</div>'],
        flags=re.S,
    )
    page_html = replace_all_sequence(
        page_html,
        r'<div class="big-num big-num-compact">.*?</div>',
        [
            f'<div class="big-num big-num-compact">{h(fmt_money(story["cash"], 0))}</div>',
            f'<div class="big-num big-num-compact">{h(fmt_money(story["investments"], 0))}</div>',
            f'<div class="big-num big-num-compact">{h(fmt_money(story["net_worth"], 0))}</div>',
        ],
        flags=re.S,
    )
    page_html = replace_all_sequence(
        page_html,
        r'<div class="fin-kpi-pct">.*?</div>',
        [
            f'<div class="fin-kpi-pct">{h(fmt_percent(cash_share, 1))}<br />deines Nettovermögens</div>',
            f'<div class="fin-kpi-pct">{h(fmt_percent(inv_share, 1))}<br />deines Nettovermögens</div>',
            '<div class="fin-kpi-pct">100 %<br />deiner finanziellen Basis</div>',
        ],
        flags=re.S,
    )
    paragraphs = story_paragraphs(data)
    page_html = replace_all_sequence(
        page_html,
        r"<p>[\s\S]*?</p>",
        [f"<p>{item}</p>" for item in paragraphs],
        flags=re.S,
    )
    return page_html


def render_month(page_html: str, data: dict) -> str:
    month = data["pages"]["month"]
    biggest = month["biggest_expense"]
    strongest = month["strongest_category"]
    values = [
        humanize_text(month["best_decision"]),
        "Keine Ausgabe" if not biggest else f"{biggest['merchant']} · {fmt_money(biggest['amount'], 0)}",
        "Noch keine Kategorie" if not strongest else strongest["category"],
        humanize_text(month["focus"]),
    ]
    page_html = replace_all_sequence(
        page_html,
        r'<div class="monat-val">.*?</div>',
        [f'<div class="monat-val">{h(value)}</div>' for value in values],
        flags=re.S,
    )
    page_html = replace_first(page_html, r"<p>[\s\S]*?</p>", f"<p>{month_summary_text(data)}</p>", flags=re.S)
    return page_html


def render_score(page_html: str, data: dict) -> str:
    score = data["pages"]["score"]
    parts = score["parts"]
    values = [
        f'{parts.get("budget", 0)}/25',
        f'{parts.get("savings", 0)}/25',
        f'{parts.get("consistency", 0)}/25',
        f'{parts.get("structure", 0)}/25',
    ]
    page_html = replace_first(page_html, r'(<div class="score-num">).*?(</div>)', rf"\g<1>{h(score['clarity_score'])}\g<2>", flags=re.S)
    page_html = replace_first(page_html, r'(<div class="score-word">).*?(</div>)', rf"\g<1>{h(score['rank_name'])}\g<2>", flags=re.S)
    page_html = replace_all_sequence(
        page_html,
        r'<div class="score-val-(?:green|gold)">.*?</div>',
        [
            f'<div class="score-val-green">{h(values[0])}</div>',
            f'<div class="score-val-green">{h(values[1])}</div>',
            f'<div class="score-val-gold">{h(values[2])}</div>',
            f'<div class="score-val-green">{h(values[3])}</div>',
        ],
        flags=re.S,
    )
    unlock_text = ""
    if score["days_to_unlock"] > 0:
        unlock_text = f'Noch {score["days_to_unlock"]} Tage bis {score["next_unlock_level"]}+'
    page_html = replace_first(page_html, r'(<div class="score-unlock">).*?(</div>)', rf'\g<1>{h(unlock_text)}\g<2>', flags=re.S)
    page_html = replace_first(page_html, r'(<div class="score-cta">)([\s\S]*?)(</div>)', rf'\g<1><span>◇</span>{h(score["share_cta"])}<span class="cta-arrow">→</span>\g<3>', flags=re.S)
    return page_html


def render_journey(page_html: str, data: dict) -> str:
    journey = data["pages"]["wealth_journey"]
    summary = journey["investment_summary"]
    points = journey["points"]
    current = points[-1]["net_worth"] if points else data["profile"]["net_worth"]
    start = points[0]["net_worth"] if points else current
    growth = current - start
    page_html = replace_first(page_html, r'(<span class="chart-val">).*?(</span>)', rf'\g<1>{h(fmt_money(current, 0))}\g<2>', flags=re.S)
    page_html = replace_first(page_html, r'(<div class="chart-area">)([\s\S]*?)(</div>)', rf'\g<1>{build_chart_svg(points)}\g<3>', flags=re.S)
    page_html = replace_first(page_html, r'(<div class="chart-x-axis">)([\s\S]*?)(</div>)', rf'\g<1>{build_axis_labels(points, data["meta"]["month_label"])}\g<3>', flags=re.S)
    caption = journey["note"] if not journey["is_visible"] else f'Seit Start liegt dein Vermögen bei {fmt_money(growth, 0)} Wachstum.'
    page_html = replace_first(page_html, r'(<div class="chart-caption">)([\s\S]*?)(</div>)', rf'\g<1>{h(caption)}\g<3>', flags=re.S)
    page_html = replace_all_sequence(
        page_html,
        r'<div class="jkpi-val(?: green)?">.*?</div>',
        [
            f'<div class="jkpi-val">{h(fmt_money(start, 0))}</div>',
            f'<div class="jkpi-val">{h(fmt_money(current, 0))}</div>',
            f'<div class="jkpi-val green">{h(fmt_money(growth, 0))}</div>',
        ],
        flags=re.S,
    )
    return page_html


def render_goal(page_html: str, data: dict) -> str:
    goal = data["pages"]["goal"]
    target = float(goal["target_amount"] or 0)
    current = float(data["profile"]["net_worth"] or 0)
    remaining = max(0.0, target - current)
    page_html = replace_first(page_html, r'(<div class="goal-name">).*?(</div>)', rf'\g<1>{h(clean_goal_description(goal["description"]))}\g<2>', flags=re.S)
    page_html = replace_first(page_html, r'(<div class="goal-pct">).*?(</div>)', rf'\g<1>{h(fmt_percent(goal["progress_percent"], 0))}\g<2>', flags=re.S)
    page_html = replace_first(
        page_html,
        r'<div class="progress-fill"></div>',
        f'<div class="progress-fill" style="width: {max(0.0, min(100.0, goal["progress_percent"])):.1f}%"></div>',
    )
    page_html = replace_first(page_html, r'(<div class="goal-hint">)([\s\S]*?)(</div>)', rf'\g<1>{h(humanize_text(goal["forecast_text"]))}\g<3>', flags=re.S)
    page_html = replace_all_sequence(
        page_html,
        r'<div class="gk-val">.*?</div>',
        [
            f'<div class="gk-val">{h(fmt_money(target, 0))}</div>',
            f'<div class="gk-val">{h(fmt_money(current, 0))}</div>',
            f'<div class="gk-val">{h(fmt_money(remaining, 0))}</div>',
        ],
        flags=re.S,
    )
    return page_html


def render_money_map(page_html: str, data: dict) -> str:
    money = data["pages"]["money_map"]
    return f"""
      <div class="page">
        <div class="display">Money Map</div>

        <div class="card money-map-card">
          <div class="bar-list">{build_money_rows(money["categories"])}</div>
        </div>

        <div class="insights-grid">{build_insight_tiles(money["insights"])}</div>

{build_footer(7, "Dein Geld im Überblick")}
      </div>
""".rstrip()


def render_milestones(page_html: str, data: dict) -> str:
    milestones = data["pages"]["milestones"]
    info = milestone_progress(data["profile"]["net_worth"])
    return f"""
      <div class="page">
        <div class="display">Meilensteine</div>

        <div class="card milestone-card">
          <div class="milestone-header">
            <h3>Vermögens-Meilenstein</h3>
            <p>Vermögensrang: {h(milestones["rank"]["name"])}</p>
          </div>
          <div class="ms-progress-track">
            <div class="ms-progress-fill" style="width: {info["progress"]:.1f}%"></div>
          </div>
          <div class="ms-progress-pct">{h(fmt_percent(info["progress"], 0))}</div>
          <div class="ms-kpis">
            <div class="ms-kpi">
              <div class="mk-label">Erreicht</div>
              <div class="mk-val">{h(fmt_money(info["reached"], 0))}</div>
            </div>
            <div class="ms-kpi">
              <div class="mk-label">Aktueller Stand</div>
              <div class="mk-val">{h(fmt_money(data["profile"]["net_worth"], 0))}</div>
            </div>
            <div class="ms-kpi">
              <div class="mk-label">Nächstes Ziel</div>
              <div class="mk-val">{h(fmt_money(info["target"], 0))}</div>
            </div>
          </div>
          <div class="ms-next">
            <span>↗</span>
            Noch <strong>&nbsp;{h(fmt_money(info["remaining"], 0))}&nbsp;</strong> bis zum nächsten Meilenstein.
          </div>
        </div>

        <div class="card badges-card">
          <div class="badges-header">
            <h3>Deine Entwicklung</h3>
            <span class="pts">Noch {h(milestones["rank"]["points_to_next"])} Punkte bis zum nächsten Vermögenslevel</span>
          </div>
          <div class="badges-sub">Vermögensstufe: {h(milestones["rank"]["name"])}</div>
          <div class="badges-grid">{build_badge_tiles(data)}</div>
        </div>

{build_footer(8, "Meilensteine")}
      </div>
""".rstrip()


def render_recap(page_html: str, data: dict) -> str:
    recap = recap_lists(data)
    page_html = replace_all_sequence(
        page_html,
        r"<ul>[\s\S]*?</ul>",
        [
            f"<ul>{build_recap_list(recap['good'], 'good')}</ul>",
            f"<ul>{build_recap_list(recap['attention'], 'attention')}</ul>",
            f"<ul>{build_recap_list(recap['lever'], 'lever')}</ul>",
        ],
        flags=re.S,
    )
    return page_html


def render_closing(page_html: str, data: dict) -> str:
    cover = data["pages"]["cover"]
    month = data["pages"]["month"]
    next_month = next_month_label(data["meta"]["report_month"])
    current_month = month_name_only(data["meta"]["month_label"])
    body_lines = [
        f'Du hast im {current_month} {fmt_money(cover["freedom_step"], 0)} für deine Zukunft arbeiten lassen.',
        f'Du hast an {month["tracked_days"]} Tagen Klarheit geschaffen.' if month["tracked_days"] else data["pages"]["closing"]["message"],
        "Du bist auf Kurs geblieben." if month["remaining_budget"] >= 0 else "Du weißt jetzt genauer, wo dein größter Hebel liegt.",
    ]
    body_html = "<br />\n            ".join(h(line) for line in body_lines)
    page_html = replace_first(page_html, r'(<div class="closing-headline">)([\s\S]*?)(</div>)', rf'\g<1>Jeder Euro<br />hat eine Aufgabe.\g<3>', flags=re.S)
    page_html = replace_first(page_html, r'(<div class="closing-body">)([\s\S]*?)(</div>)', rf'\g<1>{body_html}\g<3>', flags=re.S)
    page_html = replace_first(page_html, r'(<div class="closing-next">)([\s\S]*?)(</div>)', rf'\g<1>Wir sehen uns im <strong>{h(next_month)}</strong>.\g<3>', flags=re.S)
    return page_html


PAGE_RENDERERS = {
    "01-cover.html": render_cover,
    "02-financial-story.html": render_financial_story,
    "03-dein-monat.html": render_month,
    "04-clarity-score.html": render_score,
    "05-wealth-journey.html": render_journey,
    "06-your-goal.html": render_goal,
    "07-money-map.html": render_money_map,
    "08-meilensteine.html": render_milestones,
    "09-clarity-recap.html": render_recap,
    "10-closing.html": render_closing,
}


ICON_SVGS = {
    "calendar": '<svg width="19" height="19" viewBox="0 0 16 16" fill="none" stroke="#6e6e73" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="2.5" width="13" height="12" rx="1.5"></rect><line x1="5" y1="1" x2="5" y2="4"></line><line x1="11" y1="1" x2="11" y2="4"></line><line x1="1.5" y1="6.5" x2="14.5" y2="6.5"></line></svg>',
    "trend": '<svg width="19" height="19" viewBox="0 0 16 16" fill="none" stroke="#6e6e73" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2 11.5l4-4 3 3 5-5.5"></path><path d="M14 5h-3M14 5v3"></path></svg>',
    "flag": '<svg width="19" height="19" viewBox="0 0 16 16" fill="none" stroke="#6e6e73" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 14.5h13"></path><path d="M4 14.5L8.5 5.5l3.5 9"></path><path d="M8.5 5.5V2.5"></path><path d="M8.5 2.8h3l-.9 1.1.9 1.1h-3"></path></svg>',
    "bars": '<svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#86868b" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2 14h12"></path><rect x="2.6" y="8.5" width="2.6" height="3.5"></rect><rect x="6.7" y="5.5" width="2.6" height="6.5"></rect><rect x="10.8" y="3" width="2.6" height="9"></rect></svg>',
    "cash": '<svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#86868b" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="4.5" width="13" height="7" rx="1"></rect><circle cx="8" cy="8" r="1.8"></circle></svg>',
    "bag": '<svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="#86868b" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h8l-.7 8.4a1 1 0 0 1-1 .9H5.7a1 1 0 0 1-1-.9L4 5z"></path><path d="M6 5V3.8A2 2 0 0 1 8 1.8a2 2 0 0 1 2 2V5"></path></svg>',
    "check": '<svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="#3d8b5b" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8l3.5 3.5L13 4"></path></svg>',
    "arrow": '<svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="#3d8b5b" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13L13 3M7 3h6v6"></path></svg>',
    "star": '<svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="#3d8b5b" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5l1.8 4.2 4.5.4-3.4 3 1 4.4L8 11.2 4.1 13.5l1-4.4-3.4-3 4.5-.4z"></path></svg>',
    "target": '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#b07d35" stroke-width="1.3" stroke-linecap="round"><circle cx="8" cy="8" r="6.5"></circle><circle cx="8" cy="8" r="3.5"></circle><circle cx="8" cy="8" r="1" fill="#b07d35" stroke="none"></circle></svg>',
    "clock": '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#86868b" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v5l3 2"></path><circle cx="8" cy="8" r="6.2"></circle></svg>',
}


def icon(name: str, kind: str = "square-icon") -> str:
    return f'<div class="{kind}">{ICON_SVGS.get(name, ICON_SVGS["trend"])}</div>'


def footer(page_number: int) -> str:
    return f"""
    <div class="page-footer">
      <div class="logo">{LOGO_SVG}ROV.E</div>
      <div class="page-num">{page_number:02d} / 10</div>
    </div>
""".rstrip()


def category_label(value: str) -> str:
    labels = {
        "LEBENSMITTEL": "Lebensmittel",
        "MOBILITAET": "Mobilität",
        "RESTAURANTS": "Restaurants",
        "ABOS": "Abos",
        "FREIZEIT": "Freizeit",
        "SHOPPING": "Shopping",
        "VERSICHERUNG": "Versicherungen",
        "GESUNDHEIT": "Gesundheit",
        "DROGERIE": "Drogerie",
        "PFLEGE": "Pflege",
        "SONSTIGES": "Sonstiges",
    }
    return labels.get(str(value or "").upper(), str(value or "Noch keine Daten").title())


def category_total(data: dict, category: str) -> float:
    for row in data["pages"]["money_map"]["categories"]:
        if str(row.get("category", "")).upper() == category.upper():
            return float(row.get("total") or 0)
    return 0.0


def strongest_category(data: dict) -> dict:
    return data["pages"]["month"]["strongest_category"] or {"category": "Noch keine Daten", "total": 0}


def biggest_expense(data: dict) -> dict | None:
    return data["pages"]["month"]["biggest_expense"]


def invested_share(data: dict) -> float:
    net_worth = max(float(data["profile"].get("net_worth") or 0), 1.0)
    return max(0.0, min(100.0, float(data["profile"].get("current_investments") or 0) / net_worth * 100.0))


def cash_share(data: dict) -> float:
    net_worth = max(float(data["profile"].get("net_worth") or 0), 1.0)
    return max(0.0, min(100.0, float(data["profile"].get("cash_reserve") or 0) / net_worth * 100.0))


def clean_month_title(month_label: str) -> tuple[str, str]:
    month, year = split_month_label(month_label)
    return month.title(), year


def monthly_investment_summary(data: dict) -> dict:
    return data["pages"]["wealth_journey"].get("investment_summary") or {}


def actual_month_progress(data: dict) -> float:
    progress = data["pages"]["wealth_journey"].get("savings_progress") or {}
    if progress.get("full_plan_confirmed"):
        return float(progress.get("full_plan_amount") or 0)
    summary = monthly_investment_summary(data)
    return float(summary.get("net_contributions") or 0)


def displayed_progress_amount(data: dict) -> float:
    actual = actual_month_progress(data)
    if abs(actual) > 0.005:
        return actual
    return float(data["profile"].get("savings_plan") or 0)


def has_actual_month_progress(data: dict) -> bool:
    return abs(actual_month_progress(data)) > 0.005


def progress_label(data: dict) -> str:
    return "Monatsfortschritt" if has_actual_month_progress(data) else "Geplante Sparrate"


def progress_subline(data: dict) -> str:
    if has_actual_month_progress(data):
        return "neu investiert oder zurückgelegt"
    if displayed_progress_amount(data) > 0:
        return "monatlich geplant"
    return "noch nicht hinterlegt"


def has_behavior_data(data: dict, min_days: int = 3) -> bool:
    month = data["pages"]["month"]
    categories = data["pages"]["money_map"].get("categories") or []
    return month.get("tracked_days", 0) >= min_days and float(month.get("total_expenses") or 0) > 0 and bool(categories)


def has_strong_behavior_data(data: dict) -> bool:
    categories = data["pages"]["money_map"].get("categories") or []
    return has_behavior_data(data, min_days=7) and len(categories) >= 2


def score_summary(score: dict) -> str:
    consistency = score["parts"].get("consistency", 0)
    budget = score["parts"].get("budget", 0)
    if consistency < 10:
        return "Budget und Struktur sind sichtbar. Was noch fehlt, ist Konstanz beim Tracking."
    if budget < 15:
        return "Deine Datenbasis wächst. Der stärkste Hebel liegt aktuell bei der Budgetkontrolle."
    return "Du steuerst dein Geld bereits bewusst. Jetzt geht es darum, diese Struktur zu halten."


def score_next_step(score: dict) -> str:
    if score.get("days_to_unlock", 0) > 0:
        return f"Noch {score['days_to_unlock']} Tage bis Score-Level {score['next_unlock_level']}+ freigeschaltet wird."
    parts = score["parts"]
    weakest = min(
        [
            ("Budget Control", parts.get("budget", 0)),
            ("Savings Execution", parts.get("savings", 0)),
            ("Tracking Consistency", parts.get("consistency", 0)),
            ("Financial Structure", parts.get("structure", 0)),
        ],
        key=lambda item: item[1],
    )[0]
    return f"Dein nächster Hebel: {weakest} stärken."


def goal_months_text(months) -> str:
    if months is None:
        return "Sparrate hinterlegen, damit die Prognose sichtbar wird."
    if months <= 0:
        return "Dein Ziel ist rechnerisch bereits erreicht."
    return f"Bei gleicher Sparrate liegt dein Ziel noch rund {format_month_duration(months)} entfernt."


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


def milestone_info(data: dict) -> dict:
    return milestone_progress(float(data["profile"].get("net_worth") or 0))


def build_new_money_rows(data: dict) -> str:
    categories = data["pages"]["money_map"]["categories"][:5]
    if not categories:
        categories = [{"category": "Noch keine Daten", "total": 0}]
    max_total = max(float(row.get("total") or 0) for row in categories) or 1.0
    rows = []
    for idx, row in enumerate(categories):
        total = float(row.get("total") or 0)
        pct = max(0, min(100, total / max_total * 100))
        share = 0 if sum(float(item.get("total") or 0) for item in categories) <= 0 else total / sum(float(item.get("total") or 0) for item in categories) * 100
        fill_class = "" if idx == 0 else (" alt" if idx == 1 else " muted")
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-name">{h(category_label(row.get("category")))}</div>'
            f'<div class="bar-track"><div class="bar-fill{fill_class}" style="width:{max(10, pct):.1f}%">{share:.0f} %</div></div>'
            f'<div class="bar-amount">{h(fmt_money(total, 0))}</div>'
            '</div>'
        )
    return "\n".join(rows)


def build_badges_777(data: dict) -> str:
    badges = data["pages"]["milestones"]["badges"][:3]
    if not badges:
        badges = [
            {"label": data["pages"]["milestones"]["rank"]["name"], "earned_at": data["meta"]["generated_at"]},
            {"label": "Profil aufgebaut", "earned_at": data["meta"]["generated_at"]},
            {"label": "Report gestartet", "earned_at": data["meta"]["generated_at"]},
        ]
    cards = []
    for idx, badge in enumerate(badges[:3]):
        label = str(badge.get("label") or "Meilenstein")
        if str(badge.get("key", "")).startswith("inv_"):
            label = "Investment bestätigt"
        earned = str(badge.get("earned_at") or data["meta"]["generated_at"])
        try:
            date_text = datetime.fromisoformat(earned.replace("Z", "+00:00")).strftime("%d.%m.%Y")
        except Exception:
            date_text = earned[:10]
        cards.append(
            '<div class="badge">'
            f'<div class="round-icon green">{ICON_SVGS["star" if idx == 0 else "check"]}</div>'
            f'<div class="badge-title">{h(label)}</div>'
            f'<div class="badge-date">{h(date_text)}</div>'
            '</div>'
        )
    return "\n".join(cards)


def plan_items(data: dict) -> list[tuple[str, str, str]]:
    month = data["pages"]["month"]
    score = data["pages"]["score"]
    strongest = strongest_category(data)
    savings_plan = float(data["profile"].get("savings_plan") or 0)
    cat_name = category_label(strongest.get("category"))
    cat_total = float(strongest.get("total") or 0)
    next_score = score.get("next_unlock_level") or min(100, int(score.get("clarity_score", 0)) + 5)
    items = [
        ("Tracke an mindestens 10 Tagen.", f"Aktuell stehen {month['tracked_days']} Tracking-Tage im Report.", f"Score {next_score}+"),
    ]
    if has_behavior_data(data):
        items.append((f"Halte {cat_name} bewusst.", f"Diese Kategorie liegt aktuell bei {fmt_money(cat_total, 0)}.", "Mehr Kontrolle"))
    else:
        items.append(("Tracke 2-3 echte Ausgaben pro Tag.", "Dann wird deine Money Map im nächsten Report deutlich präziser.", "Klarere Muster"))
    if savings_plan > 0:
        items.append((f"Halte deine Sparrate von {fmt_money(savings_plan, 0)}.", "Ein Dauerauftrag macht aus einem guten Monat eine Gewohnheit.", "Routine"))
    else:
        items.append(("Hinterlege deine monatliche Sparrate.", "Dann kann Rov.E deine Zielprognose sauber berechnen.", "Bessere Prognose"))
    return items


def render_777_cover(_page_html: str, data: dict) -> str:
    cover = data["pages"]["cover"]
    month, year = clean_month_title(cover["period"])
    dev = fmt_percent(cover["development_percent"], 1) if cover["development_percent"] is not None else "—"
    dev_sub = "zum Vormonat" if cover["development_percent"] is not None else "ab Monat 2 sichtbar"
    dev_sub_class = "kpi-sub" if cover["development_percent"] is not None else "kpi-sub small"
    progress = displayed_progress_amount(data)
    return f"""
  <section class="page cover">
    <div style="display:flex;justify-content:flex-end;"><div class="topline">Ausgabe 01</div></div>
    <div class="cover-title">Rov.E<br><span>Report</span></div>
    <div class="cover-subtitle">Dein finanzieller Monatsabschluss für {h(cover["period"])} - Vermögen, Verhalten und der nächste Schritt zu deinem Ziel.</div>
    <div class="cover-kpis">
      <div class="card cover-kpi"><div class="kpi-head">{icon("calendar", "round-icon")}Zeitraum</div><div class="kpi-line"></div><div class="kpi-bottom"><div class="kpi-value">{h(month)}</div><div class="kpi-sub">{h(year)}</div></div></div>
      <div class="card cover-kpi"><div class="kpi-head">{icon("flag", "round-icon")}{h(progress_label(data))}</div><div class="kpi-line"></div><div class="kpi-bottom"><div class="kpi-value">{h(fmt_money(progress, 0))}</div><div class="kpi-sub">{h(progress_subline(data))}</div></div></div>
      <div class="card cover-kpi"><div class="kpi-head">{icon("trend", "round-icon")}Entwicklung</div><div class="kpi-line"></div><div class="kpi-bottom"><div class="kpi-value">{h(dev)}</div><div class="{dev_sub_class}">{h(dev_sub)}</div></div></div>
    </div>
{footer(1)}
  </section>
""".rstrip()


def render_777_overview(_page_html: str, data: dict) -> str:
    month = data["pages"]["month"]
    biggest = biggest_expense(data)
    strongest = strongest_category(data)
    story = data["pages"]["financial_story"]
    progress = displayed_progress_amount(data)
    biggest_amount = fmt_money(biggest["amount"], 0) if biggest else "—"
    biggest_name = biggest["merchant"] if biggest else "Noch keine Ausgabe"
    strongest_total = float(strongest.get("total") or 0)
    tracking_part = f"Du hast an <span class=\"green\">{month['tracked_days']} Tagen</span> aktiv getrackt." if month["tracked_days"] else "Für diesen Monat entsteht gerade erst deine Datenbasis."
    progress_part = (
        f"<span class=\"green\">{h(fmt_money(progress, 0))}</span> wurden neu investiert oder zurückgelegt."
        if has_actual_month_progress(data)
        else f"Deine geplante Sparrate liegt bei <span class=\"green\">{h(fmt_money(progress, 0))}</span>."
        if progress > 0
        else "Eine monatliche Sparrate ist noch nicht sauber hinterlegt."
    )
    if has_behavior_data(data):
        category_part = f"Der stärkste sichtbare Block war <span class=\"green\">{h(category_label(strongest.get('category')))}</span> mit {h(fmt_money(strongest_total, 0))}."
    else:
        category_part = "Mit mehr echten Buchungen wird sichtbar, welche Kategorie wirklich dominiert."
    summary = f"{tracking_part} {progress_part} {category_part}"
    return f"""
  <section class="page">
    <div class="display">Dein Monat auf einen Blick.</div>
    <div class="divider"></div>
    <div class="overview-grid">
      <div class="card overview-tile">{icon("bars")}<div><div class="label">Nettovermögen</div><div class="tile-value">{h(fmt_money(story["net_worth"], 0))}</div></div></div>
      <div class="card overview-tile">{icon("trend", "square-icon green")}<div><div class="label">Investments</div><div class="tile-value">{h(fmt_money(story["investments"], 0))}</div></div></div>
      <div class="card overview-tile">{icon("cash")}<div><div class="label">Cash</div><div class="tile-value">{h(fmt_money(story["cash"], 0))}</div></div></div>
      <div class="card overview-tile">{icon("bag")}<div><div class="label">Größte Ausgabe</div><div class="tile-value">{h(biggest_amount)}</div><div class="tile-sub">{h(biggest_name)}</div></div></div>
      <div class="card overview-tile">{icon("trend")}<div><div class="label">Stärkste Kategorie</div><div class="tile-value">{h(fmt_money(strongest_total, 0))}</div><div class="tile-sub">{h(category_label(strongest.get("category")))}</div></div></div>
      <div class="card overview-tile">{icon("calendar")}<div><div class="label">Tracking-Tage</div><div class="tile-value">{h(month["tracked_days"])}</div></div></div>
    </div>
    <div class="card story-card">{icon("bars", "square-icon")}<div><div class="label">Monatsfazit</div><div class="story-text">{summary}</div></div></div>
{footer(2)}
  </section>
""".rstrip()


def render_777_insight(_page_html: str, data: dict) -> str:
    strongest = strongest_category(data)
    cat_name = category_label(strongest.get("category"))
    cat_total = float(strongest.get("total") or 0)
    savings = displayed_progress_amount(data)
    progress_word = "gesparten" if has_actual_month_progress(data) else "geplanten"
    if not has_behavior_data(data):
        tracked_days = int(data["pages"]["month"].get("tracked_days") or 0)
        total_expenses = float(data["pages"]["month"].get("total_expenses") or 0)
        return f"""
  <section class="page">
    <div class="topline green"><span style="display:inline-block;width:18px;height:1px;background:var(--green);vertical-align:middle;margin-right:10px;"></span>Insight des Monats</div>
    <div class="insight-hero">
      <div class="insight-headline">Deine ersten Muster entstehen gerade.</div>
      <div class="insight-sub">Noch zu früh für ein finales Urteil - aber genau daraus entsteht dein Monatsbild.</div>
    </div>
    <div class="insight-kpis">
      <div class="card insight-kpi"><div class="label">Tracking-Tage</div><div class="value">{h(tracked_days)}</div></div>
      <div class="card insight-kpi"><div class="label">Getrackte Ausgaben</div><div class="value">{h(fmt_money(total_expenses, 0))}</div></div>
      <div class="card insight-kpi"><div class="label">{h(progress_label(data))}</div><div class="value">{h(fmt_money(savings, 0))}</div></div>
    </div>
    <div class="insight-copy">Rov.E bewertet diesen Monat noch vorsichtig. Tracke weiter echte Ausgaben - dann wird sichtbar, welche Kategorie wirklich dein größter Hebel ist.</div>
    <div class="impact-box"><div class="round-icon green">{ICON_SVGS["arrow"]}</div><div><div class="impact-title">Was jetzt zählt</div><div class="impact-value">Mehr echte Daten</div><div class="note-body">Schon 7 bis 10 Tracking-Tage machen den nächsten Report deutlich präziser.</div></div></div>
{footer(3)}
  </section>
""".rstrip()
    ratio = cat_total / savings if savings > 0 else 0
    half = cat_total / 2
    annual = half * 12
    return f"""
  <section class="page">
    <div class="topline green"><span style="display:inline-block;width:18px;height:1px;background:var(--green);vertical-align:middle;margin-right:10px;"></span>Insight des Monats</div>
    <div class="insight-hero">
      <div class="insight-headline">Dein größter Hebel liegt diesen Monat bei <em>{h(cat_name)}</em>.</div>
      <div class="insight-sub">Für jeden {h(progress_word)} Euro sind {h(fmt_money(ratio, 2))} in diese Kategorie geflossen.</div>
    </div>
    <div class="insight-kpis">
      <div class="card insight-kpi"><div class="label">{h(progress_label(data))}</div><div class="value">{h(fmt_money(savings, 0))}</div></div>
      <div class="card insight-kpi"><div class="label">{h(cat_name)} im Monat</div><div class="value">{h(fmt_money(cat_total, 0))}</div></div>
      <div class="card insight-kpi"><div class="label">Auf jeden {h(progress_word)} €</div><div class="value">{h(fmt_money(ratio, 2))}</div></div>
    </div>
    <div class="insight-copy">Das ist keine Verzichtsübung - nur Bewusstsein. Würdest du diese Kategorie halbieren, blieben jeden Monat rund <strong>{h(fmt_money(half, 0))}</strong> mehr übrig.</div>
    <div class="impact-box"><div class="round-icon green">{ICON_SVGS["arrow"]}</div><div><div class="impact-title">Was das pro Jahr bedeutet</div><div class="impact-value">+{h(fmt_money(annual, 0))} fürs Ziel</div><div class="note-body">Ganz ohne mehr zu verdienen - nur durch eine bewusstere Gewohnheit.</div></div></div>
{footer(3)}
  </section>
""".rstrip()


def render_777_financial_story(_page_html: str, data: dict) -> str:
    story = data["pages"]["financial_story"]
    inv_pct = invested_share(data)
    cash_pct = cash_share(data)
    note = humanize_text(story["text"])
    net_worth = float(story.get("net_worth") or 0)
    investments = max(0.0, float(story.get("investments") or 0))
    if net_worth < 0:
        headline = "Dein finanzieller Ausgangspunkt ist jetzt klar."
        note_title = f"Dein Nettovermögen liegt aktuell bei {fmt_money(net_worth, 0)}."
    elif net_worth == 0:
        headline = "Dein Startpunkt ist klar sichtbar."
        note_title = "Dein Nettovermögen liegt aktuell bei 0 €."
    elif investments <= 0:
        headline = "Dein Vermögen liegt aktuell liquide bereit."
        note_title = "Bisher ist noch kein Anteil deines Vermögens investiert."
    elif inv_pct >= 50:
        headline = "Mehr als die Hälfte deines Vermögens ist bereits investiert."
        note_title = f"{fmt_percent(inv_pct, 1)} deines Vermögens sind bereits investiert."
    else:
        headline = f"{fmt_percent(inv_pct, 1)} deines Vermögens sind bereits investiert."
        note_title = headline
    return f"""
  <section class="page">
    <div class="topline">Financial Story · Wo du stehst</div>
    <div class="display">{h(headline)}</div>
    <div class="divider"></div>
    <div class="card financial-card">
      <div class="label">Nettovermögen · {h(data["meta"]["month_label"])}</div>
      <div class="net-worth">{h(fmt_money(story["net_worth"], 0))}</div>
      <div class="wealth-bar">
        <div class="wealth-invested" style="width:{max(0, inv_pct):.1f}%">Investments · {h(fmt_percent(inv_pct, 1))}</div>
        <div class="wealth-cash" style="width:{max(0, cash_pct):.1f}%">Cash · {h(fmt_percent(cash_pct, 1))}</div>
      </div>
      <div class="wealth-labels"><div><strong>{h(fmt_money(story["investments"], 0))}</strong> investiert</div><div><strong>{h(fmt_money(story["cash"], 0))}</strong> liquide</div></div>
    </div>
    <div class="card financial-note"><div class="round-icon green">{ICON_SVGS["arrow"]}</div><div><div class="note-title">{h(note_title)}</div><div class="note-body">{h(note)}</div></div></div>
{footer(4)}
  </section>
""".rstrip()


def render_777_money_map(_page_html: str, data: dict) -> str:
    strongest = strongest_category(data)
    cat_name = category_label(strongest.get("category"))
    cat_total = float(strongest.get("total") or 0)
    biggest = biggest_expense(data)
    total_expenses = float(data["pages"]["month"].get("total_expenses") or 0)
    biggest_line = "Noch keine Einzelbuchung sichtbar."
    if biggest:
        share = (float(biggest["amount"] or 0) / total_expenses * 100) if total_expenses > 0 else 0
        biggest_line = f'{h(biggest["merchant"] or "Unbekannt")}, {h(fmt_money(biggest["amount"], 0))} - {share:.0f} % deiner getrackten Ausgaben.'
    display_title = f"{h(cat_name)} ist dein<br>größter Hebel." if has_behavior_data(data) else "Deine Money Map<br>entsteht gerade."
    if not has_behavior_data(data):
        biggest_line = "Noch zu wenige Buchungen für eine belastbare Kategorie-Aussage."
    return f"""
  <section class="page">
    <div class="topline">Money Map · Dein Verhalten</div>
    <div class="display">{display_title}</div>
    <div class="divider"></div>
    <div class="card money-card">{build_new_money_rows(data)}</div>
    <div class="money-bottom">
      <div class="card money-insight"><div class="label">Die eine Zahl, die zählt</div><div class="headline">{biggest_line}</div><p>Hier verändert ein kleiner Vorsatz am meisten. Nicht streichen - bewusst entscheiden.</p></div>
      <div class="card money-insight"><div class="label" style="color:var(--green);">Beste Entscheidung</div><div class="headline">{h(humanize_text(data["pages"]["month"]["best_decision"]))}</div></div>
    </div>
{footer(5)}
  </section>
""".rstrip()


def render_777_score(_page_html: str, data: dict) -> str:
    score = data["pages"]["score"]
    parts = score["parts"]
    value = int(score["clarity_score"] or 0)
    circumference = 540.4
    offset = circumference - (max(0, min(100, value)) / 100 * circumference)
    rank_width = max(3, min(100, value))
    return f"""
  <section class="page">
    <div class="topline">Rov.E Score · Wie bewusst du steuerst</div>
    <div class="display">{value} von 100 - du hast dein Geld im Blick.</div>
    <div class="divider"></div>
    <div class="score-layout">
      <div class="card score-card">
        <div class="score-ring"><svg viewBox="0 0 200 200"><circle cx="100" cy="100" r="86" fill="none" stroke="#ececee" stroke-width="13"></circle><circle cx="100" cy="100" r="86" fill="none" stroke="#3d8b5b" stroke-width="13" stroke-linecap="round" stroke-dasharray="{circumference}" stroke-dashoffset="{offset:.1f}" transform="rotate(-90 100 100)"></circle></svg><div class="score-center"><div class="score-number">{value}</div><div class="score-rank">{h(score["rank_name"])}</div></div></div>
        <div class="rank-strip"><div class="rank-labels"><span>Rookie</span><span>Controller</span><span>Manager</span><span>Elite</span></div><div class="rank-line"><div class="rank-fill" style="width:{rank_width}%"></div></div><div class="tile-sub" style="text-align:center;">{h(score["proof_days"])}d verified</div></div>
      </div>
      <div class="card score-parts">
        <div class="score-row"><div class="score-name">{icon("calendar", "square-icon")}Budget Control</div><div class="score-value">{h(parts.get("budget", 0))}<span style="color:#c7c7cc;font-size:16px;">/25</span></div></div>
        <div class="score-row"><div class="score-name">{icon("clock", "square-icon")}Savings Execution</div><div class="score-value">{h(parts.get("savings", 0))}<span style="color:#c7c7cc;font-size:16px;">/25</span></div></div>
        <div class="score-row"><div class="score-name">{icon("target", "square-icon gold")}Tracking Consistency</div><div class="score-value gold">{h(parts.get("consistency", 0))}<span style="color:#c7c7cc;font-size:16px;">/25</span></div></div>
        <div class="score-row"><div class="score-name">{icon("trend", "square-icon")}Financial Structure</div><div class="score-value">{h(parts.get("structure", 0))}<span style="color:#c7c7cc;font-size:16px;">/25</span></div></div>
      </div>
    </div>
    <div class="card score-note"><div><div class="score-note-title">Was {h(score["rank_name"])} bedeutet</div><p>{h(score_summary(score))}</p></div><div class="split-left"><div class="score-note-title" style="color:var(--green);">Dein nächster Schritt</div><div class="next">{h(score_next_step(score))}</div><p>Später kannst du deinen Score teilen, ohne echte Geldbeträge zu zeigen.</p></div></div>
{footer(6)}
  </section>
""".rstrip()


def render_777_goal(_page_html: str, data: dict) -> str:
    goal = data["pages"]["goal"]
    target = float(goal["target_amount"] or 0)
    current = float(data["profile"].get("net_worth") or 0)
    remaining = max(0.0, target - current)
    savings = float(data["profile"].get("savings_plan") or 0)
    progress = max(0.0, min(100.0, float(goal["progress_percent"] or 0)))
    goal_name = clean_goal_description(goal["description"])
    if target <= 0:
        return f"""
  <section class="page">
    <div class="topline">Your Goal · Wohin du willst</div>
    <div class="display">Dein Ziel wird sichtbar,<br>sobald ein Zielbetrag steht.</div>
    <div class="divider"></div>
    <div class="card goal-card">
      <div>
        <div class="goal-head"><div><div class="label">Ziel</div><div class="goal-name">{h(goal_name)}</div></div><div class="goal-percent"><div class="value">—</div><div class="tile-sub">offen</div></div></div>
        <div class="progress-track"><div class="progress-fill" style="width:0%"></div></div>
        <div class="goal-kpis"><div class="goal-kpi"><div class="label">Zielbetrag</div><div class="value">Noch offen</div></div><div class="goal-kpi"><div class="label">Aktueller Stand</div><div class="value" style="color:var(--green);">{h(fmt_money(current, 0))}</div></div><div class="goal-kpi"><div class="label">Nächster Schritt</div><div class="value">Zielbetrag setzen</div></div></div>
      </div>
      <div class="goal-forecast"><div class="forecast-block"><div class="forecast-title">Ehrlich gesagt</div><div class="forecast-main">Ohne Zielbetrag gibt es noch keine saubere Prognose.</div><div class="forecast-copy">Sobald Zielbetrag und Sparrate stehen, berechnet Rov.E den realistischen Weg dorthin.</div></div><div class="forecast-block"><div class="forecast-title" style="color:var(--green);">Dein Hebel</div><div class="forecast-main">Ein klares Ziel macht Fortschritt messbar.</div></div></div>
    </div>
{footer(7)}
  </section>
""".rstrip()
    return f"""
  <section class="page">
    <div class="topline">Your Goal · Wohin du willst</div>
    <div class="display">{h(goal_name)} rückt mit jedem<br>Monat näher.</div>
    <div class="divider"></div>
    <div class="card goal-card">
      <div>
        <div class="goal-head"><div><div class="label">Ziel</div><div class="goal-name">{h(goal_name)}</div></div><div class="goal-percent"><div class="value">{h(fmt_percent(progress, 1))}</div><div class="tile-sub">erreicht</div></div></div>
        <div class="progress-track"><div class="progress-fill" style="width:{progress:.1f}%"></div></div>
        <div class="goal-kpis"><div class="goal-kpi"><div class="label">Zielbetrag</div><div class="value">{h(fmt_money(target, 0))}</div></div><div class="goal-kpi"><div class="label">Aktueller Stand</div><div class="value" style="color:var(--green);">{h(fmt_money(current, 0))}</div></div><div class="goal-kpi"><div class="label">Noch</div><div class="value">{h(fmt_money(remaining, 0))}</div></div></div>
      </div>
      <div class="goal-forecast"><div class="forecast-block"><div class="forecast-title">Ehrlich gesagt</div><div class="forecast-main">{h(goal_months_text(goal["months_to_goal"]))}</div><div class="forecast-copy">{h(humanize_text(goal["forecast_text"]))}</div></div><div class="forecast-block"><div class="forecast-title" style="color:var(--green);">Dein Hebel</div><div class="forecast-main">Schon +{h(fmt_money(max(50, savings * 0.15), 0))}/Monat verändert die Prognose.</div></div></div>
    </div>
{footer(7)}
  </section>
""".rstrip()


def render_777_milestones(_page_html: str, data: dict) -> str:
    info = milestone_info(data)
    net_worth = float(data["profile"].get("net_worth") or 0)
    savings = float(data["profile"].get("savings_plan") or 0)
    months = int((info["remaining"] + savings - 0.01) // savings) if info["remaining"] > 0 and savings > 0 else 0
    hint = (
        f'{ICON_SVGS["arrow"]} Bei {h(fmt_money(savings, 0))}/Monat erreichst du den nächsten Meilenstein in <strong>{months} Monaten</strong>.'
        if savings > 0 and info["remaining"] > 0
        else f'{ICON_SVGS["arrow"]} Hinterlege eine Sparrate, damit Rov.E den nächsten Meilenstein zeitlich einordnen kann.'
    )
    rank = data["pages"]["milestones"]["rank"]
    return f"""
  <section class="page">
    <div class="topline">Meilensteine · Deine Etappen</div>
    <div class="display">Noch {h(fmt_money(info["remaining"], 0))} bis zum<br>nächsten Meilenstein.</div>
    <div class="divider"></div>
    <div class="card milestone-card"><div class="milestone-top"><div class="label">Von {h(fmt_money(info["reached"], 0))} zu {h(fmt_money(info["target"], 0))}</div><div style="font-family:var(--display);font-size:18px;color:var(--green);">{h(fmt_percent(info["progress"], 0))}</div></div><div class="milestone-progress"><div class="progress-fill" style="width:{info["progress"]:.1f}%"></div></div><div class="milestone-scale"><span>{h(fmt_money(info["reached"], 0))}</span><span style="color:var(--green);font-weight:600;">{h(fmt_money(net_worth, 0))} · du bist hier</span><span>{h(fmt_money(info["target"], 0))}</span></div><div class="milestone-hint">{hint}</div></div>
    <div class="card badge-card"><div class="label">Vermögenslevel · {h(rank["name"])}</div><div class="badge-grid">{build_badges_777(data)}</div></div>
{footer(8)}
  </section>
""".rstrip()


def render_777_recap(_page_html: str, data: dict) -> str:
    recap = data["pages"]["recap"]
    display = (
        "Ein ehrlicher Zwischenstand -<br>deine Datenbasis wächst."
        if not has_behavior_data(data)
        else "Ein starker Start - mit einem<br>klaren nächsten Schritt."
    )
    return f"""
  <section class="page">
    <div class="topline">Recap · Ehrlich zusammengefasst</div>
    <div class="display">{display}</div>
    <div class="divider"></div>
    <div class="recap-list">
      <div class="card recap-item"><div class="recap-icon">✓</div><div><div class="recap-title">Was gut lief</div><div class="recap-copy">{h(humanize_text(recap["what_went_well"]))}</div></div></div>
      <div class="card recap-item"><div class="recap-icon warn">!</div><div><div class="recap-title">Was Aufmerksamkeit braucht</div><div class="recap-copy">{h(humanize_text(recap["needs_attention"]))}</div></div></div>
      <div class="card recap-item"><div class="recap-icon soft">{ICON_SVGS["arrow"]}</div><div><div class="recap-title">Dein größter Hebel</div><div class="recap-copy">{h(humanize_text(recap["next_lever"]))}</div></div></div>
    </div>
{footer(9)}
  </section>
""".rstrip()


def render_777_plan(_page_html: str, data: dict) -> str:
    next_month = next_month_label(data["meta"]["report_month"]).title()
    items = plan_items(data)
    rows = []
    for idx, (title, copy, effect) in enumerate(items, start=1):
        rows.append(
            f'<div class="card plan-item"><div class="plan-num">{idx:02d}</div><div class="plan-main"><div class="plan-title">{h(title)}</div><div class="plan-copy">{h(copy)}</div></div><div class="plan-effect"><div class="label">Wirkung</div><div class="value">{h(effect)}</div></div></div>'
        )
    return f"""
  <section class="page">
    <div class="topline">Next Month Plan · {h(next_month)}</div>
    <div class="display big">Dein Plan für {h(next_month)}.</div>
    <div class="plan-intro">Drei klare Schritte. Mehr brauchst du nicht, um den nächsten Monat bewusster zu gestalten.</div>
    <div class="divider"></div>
    <div class="plan-list">{''.join(rows)}</div>
    <div class="closing-line">Jeder Euro hat eine Aufgabe.</div>
{footer(10)}
  </section>
""".rstrip()


NEW_PAGE_RENDERERS = {
    "01-cover.html": render_777_cover,
    "02-financial-story.html": render_777_overview,
    "03-dein-monat.html": render_777_insight,
    "04-clarity-score.html": render_777_financial_story,
    "05-wealth-journey.html": render_777_money_map,
    "06-your-goal.html": render_777_score,
    "07-money-map.html": render_777_goal,
    "08-meilensteine.html": render_777_milestones,
    "09-clarity-recap.html": render_777_recap,
    "10-closing.html": render_777_plan,
}


def _extract_labeled_pages(source: str) -> list[str]:
    pages: list[str] = []
    cursor = 0
    marker = '<div data-screen-label="'
    tag_pattern = re.compile(r"</?div\b[^>]*>", re.IGNORECASE)
    while True:
        start = source.find(marker, cursor)
        if start < 0:
            break
        depth = 0
        end = None
        for match in tag_pattern.finditer(source, start):
            if match.group(0).lower().startswith("</div"):
                depth -= 1
                if depth == 0:
                    end = match.end()
                    break
            else:
                depth += 1
        if end is None:
            raise ValueError("Unvollstaendige PDF-Seite in der hellen Referenzvorlage.")
        pages.append(source[start:end])
        cursor = end
    return pages


def _render_hell_pages(data: dict) -> list[str]:
    import jinja2
    # Local import avoids a module cycle: the web renderer reuses the formatting
    # helpers from this module, while both renderers share the immutable V2 facts.
    from rove_web_report_renderer import build_render_context

    template = HELL_PAGES_TEMPLATE.read_text(encoding="utf-8")
    rendered = jinja2.Template(template).render(**build_render_context(data))
    pages = _extract_labeled_pages(rendered)
    if len(pages) != 10:
        raise ValueError(f"Die helle PDF-Vorlage muss exakt 10 Seiten liefern, erhalten: {len(pages)}")
    return pages


def render_page(page_filename: str, data: dict) -> str:
    filenames = [filename for filename, _title in PAGE_FILES]
    return _render_hell_pages(data)[filenames.index(page_filename)]


def build_html_report(user_id: int, report_month: str, report_data: dict | None = None) -> Path:
    report_data = report_data or build_report_data(user_id, report_month)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    rendered_pages = _render_hell_pages(report_data)
    doc = build_html_document(rendered_pages)
    output_path = GENERATED_DIR / f"clarity_report_{user_id}_{report_month}.html"
    output_path.write_text(doc, encoding="utf-8")
    latest_path = GENERATED_DIR / "latest_preview.html"
    latest_path.write_text(doc, encoding="utf-8")
    return output_path


def build_html_document(pages: list[str]) -> str:
    source = HELL_REFERENCE_TEMPLATE.read_text(encoding="utf-8")
    reference_pages = _extract_labeled_pages(source)
    if len(reference_pages) != 10:
        raise ValueError("Die originale helle PDF-Referenz enthaelt nicht exakt 10 Seiten.")
    reference_styles = "\n".join(
        re.findall(r"<style\b[^>]*>[\s\S]*?</style>", source, flags=re.IGNORECASE)
    )
    print_css = """
<style>
  @page { size: 820px 1080px; margin: 0; }
  html, body { margin: 0 !important; padding: 0 !important; }
  body { background: #E3E1DC; }
  body > div { min-height: 0 !important; padding: 0 !important; gap: 0 !important; display: block !important; }
  [data-screen-label] { box-sizing: border-box; width: 820px !important; min-width: 0 !important; border-radius: 0 !important; box-shadow: none !important; break-after: page; }
  [data-screen-label] [style*="display:flex"] { min-width: 0; }
  [data-screen-label] [style*="display:flex"] > * { min-width: 0; box-sizing: border-box; }
  [data-screen-label]:last-child { break-after: auto; }
</style>
"""
    return f"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8">{reference_styles}{print_css}</head>
<body><div>{' '.join(pages)}</div></body>
</html>"""


def build_pdf_report(user_id: int, report_month: str, output_path: Path, report_data: dict | None = None) -> Path:
    """Render the premium HTML report and convert it into a sendable PDF."""
    report_data = report_data or build_report_data(user_id, report_month)
    html_path = build_html_report(user_id, report_month, report_data=report_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from weasyprint import HTML
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "WeasyPrint fehlt. Installiere es mit: python3 -m pip install weasyprint"
        ) from exc

    # The fixed screen wrappers have explicit page breaks. Rendering the complete
    # document preserves the reference geometry without an optional PDF merger.
    HTML(filename=str(html_path), base_url=str(html_path.parent)).write_pdf(str(output_path))
    return output_path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 report_html_renderer.py <user_id> <YYYY-MM>")
        sys.exit(1)
    user_id = int(sys.argv[1])
    report_month = sys.argv[2]
    output = build_html_report(user_id, report_month)
    print(output)
