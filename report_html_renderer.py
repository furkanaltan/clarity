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

PAGE_FILES = [
    ("01-cover.html", "Clarity Report"),
    ("02-financial-story.html", "Financial Story"),
    ("03-dein-monat.html", "Dein Monat"),
    ("04-clarity-score.html", "Clarity Score"),
    ("05-wealth-journey.html", "Wealth Journey"),
    ("06-your-goal.html", "Your Goal"),
    ("07-money-map.html", "Money Map"),
    ("08-meilensteine.html", "Meilensteine"),
    ("09-clarity-recap.html", "Clarity Recap"),
    ("10-closing.html", "Clarity Report"),
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
    return html.escape(str(value or ""))


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
            CLARITY
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
            '<div class="b-desc">Dein aktueller Rang im Clarity System</div>'
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


def render_page(page_filename: str, data: dict) -> str:
    page_path = PAGE_DIR / page_filename
    page_html = page_path.read_text(encoding="utf-8")
    main_contents = extract_main_contents(page_html)
    return PAGE_RENDERERS[page_filename](main_contents, data)


def build_html_report(user_id: int, report_month: str, report_data: dict | None = None) -> Path:
    report_data = report_data or build_report_data(user_id, report_month)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    rendered_pages = [render_page(filename, report_data) for filename, _title in PAGE_FILES]
    doc = build_html_document(rendered_pages)
    output_path = GENERATED_DIR / f"clarity_report_{user_id}_{report_month}.html"
    output_path.write_text(doc, encoding="utf-8")
    latest_path = GENERATED_DIR / "latest_preview.html"
    latest_path.write_text(doc, encoding="utf-8")
    return output_path


def build_html_document(pages: list[str]) -> str:
    return """<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Clarity Report</title>
    <link rel="stylesheet" href="../style.css" />
  </head>
  <body>
    <main class="report" aria-label="Clarity Monatsreport">
{pages}
    </main>
  </body>
</html>
""".format(pages="\n\n".join(pages))


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

    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pypdf fehlt. Installiere es mit: python3 -m pip install pypdf"
        ) from exc

    # Render each page independently and merge only the first rendered page.
    # This prevents WeasyPrint from turning one long HTML document into 11+ pages.
    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        writer = PdfWriter()
        for idx, (filename, _title) in enumerate(PAGE_FILES, start=1):
            single_html_path = tmp_path / f"page_{idx:02d}.html"
            single_pdf_path = tmp_path / f"page_{idx:02d}.pdf"
            single_html_path.write_text(
                build_html_document([render_page(filename, report_data)]),
                encoding="utf-8",
            )
            HTML(filename=str(single_html_path), base_url=str(html_path.parent)).write_pdf(str(single_pdf_path))
            reader = PdfReader(str(single_pdf_path))
            if not reader.pages:
                raise RuntimeError(f"PDF-Seite {idx} konnte nicht gerendert werden.")
            writer.add_page(reader.pages[0])

        with output_path.open("wb") as pdf_file:
            writer.write(pdf_file)
    return output_path


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 report_html_renderer.py <user_id> <YYYY-MM>")
        sys.exit(1)
    user_id = int(sys.argv[1])
    report_month = sys.argv[2]
    output = build_html_report(user_id, report_month)
    print(output)
