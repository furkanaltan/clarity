from __future__ import annotations

import html
import logging
import math
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import jinja2
from dotenv import load_dotenv

from report_engine import build_report_data, calculate_goal_projection, format_month_duration, SCORE_RANKS
from report_html_renderer import fmt_money, fmt_percent, humanize_text
from report_story_v2 import get_report_wealth, story_from_snapshot_data, valid_report_merchant


load_dotenv()

APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME

TEMPLATE_PATH = Path(
    os.getenv("ROVE_WEB_TEMPLATE_PATH", str(APP_DIR / "report_templates" / "rove_web_report.html"))
)
PUBLIC_REPORT_DIR = Path(
    os.getenv("ROVE_REPORT_PUBLIC_DIR", "/var/www/reports")
)
PUBLIC_REPORT_BASE_URL = os.getenv("ROVE_REPORT_PUBLIC_BASE_URL", "").rstrip("/")
REPORT_LINK_TTL_DAYS = int(os.getenv("ROVE_REPORT_LINK_TTL_DAYS", "30"))
logger = logging.getLogger(__name__)


def ensure_report_links_table() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS report_links (
                token        TEXT PRIMARY KEY,
                user_id      INTEGER NOT NULL,
                report_month TEXT    NOT NULL,
                html_path    TEXT    NOT NULL,
                public_url   TEXT    DEFAULT '',
                expires_at   TEXT    NOT NULL,
                created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
                status       TEXT    NOT NULL DEFAULT 'active'
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_report_links_expiry
               ON report_links(status, expires_at)"""
        )
        conn.commit()


def h(value) -> str:
    return html.escape(str(value or ""))


def de_number(value, decimals: int = 0) -> str:
    try:
        amount = float(value or 0)
    except Exception:
        amount = 0.0
    formatted = f"{amount:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if decimals == 0:
        formatted = formatted.replace(",0", "")
    return formatted


def money_text(value, decimals: int = 0) -> str:
    return fmt_money(value, decimals)


def wealth_position_copy(
    net_worth: float,
    investments: float,
    cash: float,
    wealth_total: float,
) -> dict[str, str]:
    """Return factual wealth copy for every financial starting point."""
    net_worth = float(net_worth or 0)
    investments = max(0.0, float(investments or 0))
    cash = max(0.0, float(cash or 0))
    wealth_total = max(0.0, float(wealth_total or 0))
    investments_pct = investments / wealth_total * 100 if wealth_total > 0 else 0.0

    if net_worth < 0:
        return {
            "headline": "Dein finanzieller Ausgangspunkt ist jetzt klar.",
            "sentence": (
                f"Dein Nettovermögen liegt aktuell bei {money_text(net_worth)}. "
                "Der Fokus liegt jetzt auf Transparenz und einem stabilen Puffer."
            ),
            "story_headline": "Dein aktueller Stand ist klar sichtbar.",
            "story_sub": (
                "Der Report zeigt dir den Ausgangspunkt. Mit jedem dokumentierten Monat "
                "siehst du, was sich konkret verändert."
            ),
        }
    if net_worth == 0:
        return {
            "headline": "Dein finanzieller Ausgangspunkt ist jetzt klar.",
            "sentence": (
                "Dein Nettovermögen liegt aktuell bei 0 €. "
                "Der Fokus liegt jetzt auf Transparenz und einem ersten stabilen Puffer."
            ),
            "story_headline": "Dein Startpunkt ist klar sichtbar.",
            "story_sub": (
                "Mit jedem dokumentierten Monat entsteht ein verlässliches Bild davon, "
                "was bei dir ankommt und wofür es wieder rausgeht."
            ),
        }
    if investments <= 0:
        return {
            "headline": "Dein Vermögen liegt aktuell liquide bereit.",
            "sentence": "Dein Vermögen liegt aktuell liquide bereit; bisher ist noch kein Anteil investiert.",
            "story_headline": "Dein Vermögen liegt aktuell liquide bereit.",
            "story_sub": (
                "Das gibt dir Flexibilität. Wenn du künftig investierst oder gezielt zurücklegst, "
                "wird auch diese Entwicklung im Monatsbild sichtbar."
            ),
        }
    if investments_pct >= 50:
        return {
            "headline": "Mehr als die Hälfte deines Vermögens ist bereits investiert.",
            "sentence": (
                "Dein Vermögen verteilt sich auf Investments, Liquidität und "
                "gegebenenfalls Immobilien-Eigenkapital."
            ),
            "story_headline": f"{fmt_percent(investments_pct, 1)} deines Vermögens sind bereits investiert.",
            "story_sub": (
                f"Deine {money_text(cash)} Liquidität bleibt verfügbar. "
                "So ist dein Vermögen aufgeteilt, ohne dass der Überblick verloren geht."
            ),
        }
    return {
        "headline": f"{fmt_percent(investments_pct, 1)} deines Vermögens sind bereits investiert.",
        "sentence": (
            "Der Report zeigt dir, wie dein Vermögen zwischen Investments, "
            "Liquidität und gegebenenfalls Immobilien-Eigenkapital verteilt ist."
        ),
        "story_headline": f"{fmt_percent(investments_pct, 1)} deines Vermögens sind bereits investiert.",
        "story_sub": (
            f"Deine {money_text(cash)} Liquidität bleibt verfügbar. "
            "Der Report macht sichtbar, wie sich diese Aufteilung entwickelt."
        ),
    }


def category_label(value: str) -> str:
    text = str(value or "Sonstiges").replace("_", " ").strip()
    return text.title() if text.isupper() else text


def data_count_span(value, decimals: int = 0) -> str:
    raw = f"{float(value or 0):.{decimals}f}"
    label = de_number(value, decimals)
    extra = f' data-decimals="{decimals}"' if decimals else ""
    return f'<span data-count="{raw}"{extra}>{label}</span>'


RANK_BLURBS = {
    "Rookie": "Du baust gerade die Grundlage auf. Jede getrackte Ausgabe macht das Bild klarer.",
    "Stratege": "Die ersten Muster stehen. Jetzt geht es darum, sie zur Gewohnheit zu machen.",
    "Controller": "Budget und Struktur stimmen, Sparen läuft. Der nächste Hebel ist Konstanz.",
    "Investor": "Du sparst nicht nur, du baust Vermögen auf. Bleib bei der Konsequenz.",
    "Manager": "Struktur, Sparen und Tracking greifen ineinander. Das ist kein Zufall mehr.",
    "Kapitalist": "Dein System läuft nahezu rund. Feinschliff bringt dich in die Spitze.",
    "Rov.E Elite": "Budget, Sparen, Tracking und Struktur sind auf Top-Niveau. Halte das Tempo.",
}


def rank_band(score_value: int) -> dict:
    names = [r[2] for r in SCORE_RANKS]
    current_index = 0
    for idx, (low, high, _name, _icon) in enumerate(SCORE_RANKS):
        if low <= score_value <= high:
            current_index = idx
            break
    low, high, name, _icon = SCORE_RANKS[current_index]
    prev_name = names[current_index - 1] if current_index > 0 else None
    next_name = names[current_index + 1] if current_index < len(names) - 1 else None
    return {
        "prev_name": prev_name,
        "current_name": name,
        "next_name": next_name,
        "low": low,
        "high": high,
    }


def milestone_band(net_worth: float, step: int = 5000) -> dict:
    current = int(net_worth // step) * step
    nxt = current + step
    pct = ((net_worth - current) / step * 100) if step else 0
    return {"from_amount": current, "to_amount": nxt, "pct": max(0.0, min(100.0, pct))}


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        return text
    return text.replace(old, new, 1)


def replace_all(text: str, mapping: dict[str, str]) -> str:
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def strip_dc_runtime(template: str) -> str:
    template = re.sub(r'\s*<script src="\.\./support\.js"></script>', "", template)
    template = re.sub(
        r'<script type="text/x-dc"[\s\S]*?</script>',
        standalone_script(),
        template,
        count=1,
    )
    return template


def inject_report_css(template: str) -> str:
    css = """
    <style>
      [data-screen-label] { overflow-wrap: anywhere; }
      @media print {
        body { background: #07111a !important; }
        section {
          min-height: 100vh !important;
          break-after: page;
          page-break-after: always;
        }
        section:last-of-type {
          break-after: auto;
          page-break-after: auto;
        }
        .rove-cover-cameo,
        .rove-report-assistant { display: none !important; }
      }
    </style>
    """
    return template.replace("</head>", f"{css}\n</head>", 1)


def pdf_css() -> str:
    return """
    <style>
      @page { size: 1440px 900px; margin: 0; }
      html,
      body {
        width: 1440px;
        margin: 0 !important;
        padding: 0 !important;
        background: #08090B !important;
      }
      body {
        overflow: hidden !important;
        color: #F4F1EA;
        -webkit-font-smoothing: antialiased;
      }
      section {
        width: 1440px !important;
        height: 900px !important;
        min-height: 900px !important;
        max-height: 900px !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
        break-after: page !important;
        page-break-after: always !important;
      }
      section:last-of-type {
        break-after: auto !important;
        page-break-after: auto !important;
      }
      .pdf-page-inner {
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        height: 1286px !important;
        overflow: hidden !important;
        transform: scale(0.70);
        transform-origin: top left;
        width: 142.86% !important;
        margin-left: 0 !important;
      }
      [data-screen-label="01 Cover"] .pdf-page-inner {
        transform: none !important;
        width: 100% !important;
        height: 900px !important;
        margin-left: 0 !important;
      }
      [data-progressbar],
      .rove-cover-cameo,
      .rove-report-assistant {
        display: none !important;
      }
      [data-reveal],
      [data-grow],
      [data-ring],
      [data-count],
      [data-glow] {
        opacity: 1 !important;
        transition: none !important;
        animation: none !important;
      }
      [data-reveal],
      [data-grow],
      [data-ring],
      [data-count],
      [data-glow] {
        transform: none !important;
      }
      [data-grow] {
        width: attr(data-grow) !important;
      }
      section:not([data-screen-label="01 Cover"]) {
        padding-top: 76px !important;
        padding-bottom: 58px !important;
      }
      [data-screen-label="06 Rov.E Score"] > div,
      [data-screen-label="07 Dein Ziel"] > div,
      [data-screen-label="08 Meilensteine"] > div,
      [data-screen-label="09 Recap"] > div,
      [data-screen-label="10 Nächster Monat"] > div {
        max-width: 1040px !important;
      }
      [data-screen-label="06 Rov.E Score"] [style*="grid-template-columns: repeat(auto-fit"] {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
      }
      [data-screen-label="06 Rov.E Score"] [style*="padding: 44px 36px"] {
        padding: 34px 30px !important;
      }
      [data-screen-label="06 Rov.E Score"] [style*="padding: 38px 42px"] {
        padding: 26px 34px !important;
      }
      [data-screen-label="06 Rov.E Score"] [style*="font-size: 16px; color: #9EA4A0; line-height: 1.65"] {
        font-size: 14px !important;
        line-height: 1.48 !important;
      }
      [data-screen-label="06 Rov.E Score"] [style*="font-size: 26px; line-height: 1.3"] {
        font-size: 23px !important;
        line-height: 1.2 !important;
      }
      [data-screen-label="07 Dein Ziel"] [style*="padding: 52px 54px"] {
        padding: 34px 42px !important;
      }
      [data-screen-label="07 Dein Ziel"] [style*="font-size: 80px"] {
        font-size: 66px !important;
      }
      [data-screen-label="07 Dein Ziel"] [style*="font-size: 30px"] {
        font-size: 26px !important;
      }
      [data-screen-label="07 Dein Ziel"] [style*="margin-top: 44px"] {
        margin-top: 30px !important;
      }
      [data-screen-label="07 Dein Ziel"] [style*="padding-top: 36px"] {
        padding-top: 26px !important;
      }
      [data-screen-label="07 Dein Ziel"] [style*="font-size: 24px"] {
        font-size: 21px !important;
        line-height: 1.32 !important;
      }
      [data-screen-label="10 Nächster Monat"] {
        padding-bottom: 70px !important;
      }
    </style>
    """


def build_static_pdf_html(rendered_doc: str) -> str:
    """Create a fixed 10-page PDF document from the interactive web report."""
    helmet_match = re.search(r"<helmet>([\s\S]*?)</helmet>", rendered_doc)
    helmet = helmet_match.group(1) if helmet_match else ""
    sections = re.findall(r"<section\b[\s\S]*?</section>", rendered_doc)

    if not sections:
        return rendered_doc.replace("</head>", f"{pdf_css()}\n</head>", 1)

    wrapped_sections = [
        re.sub(
            r"(<section\b[^>]*>)([\s\S]*)(</section>)",
            r'\1<div class="pdf-page-inner">\2</div>\3',
            section,
            count=1,
        )
        for section in sections
    ]

    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=1440, initial-scale=1">\n'
        f"{helmet}\n"
        f"{pdf_css()}\n"
        "</head>\n"
        "<body>\n"
        + "\n".join(wrapped_sections)
        + "\n</body>\n</html>\n"
    )


def inject_expiry_meta(template: str, expires_at: datetime) -> str:
    expiry_iso = expires_at.isoformat(timespec="seconds")
    meta = (
        f'<meta name="robots" content="noindex,nofollow">\n'
        f'<meta name="rove-report-expires-at" content="{h(expiry_iso)}">'
    )
    template = template.replace("</head>", f"{meta}\n</head>", 1)
    template = template.replace(
        "<body>",
        f'<body data-report-expires-at="{h(expiry_iso)}">',
        1,
    )
    return template


def standalone_script() -> str:
    return r"""
<script>
(function () {
  function showExpiredState() {
    const expiry = document.body.getAttribute('data-report-expires-at');
    if (!expiry || Date.now() <= new Date(expiry).getTime()) return false;
    document.body.innerHTML = '<main style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#08090B;color:#F4F1EA;font-family:Hanken Grotesk,Arial,sans-serif;padding:32px;text-align:center;"><div><div style="font-family:Newsreader,serif;font-size:48px;margin-bottom:16px;">Dieser Report-Link ist abgelaufen.</div><div style="color:#9EA4A0;font-size:18px;line-height:1.6;max-width:560px;">Dein PDF bleibt für deine Unterlagen bestehen. Der Web-Report ist nur für eine begrenzte Zeit verfügbar.</div></div></main>';
    return true;
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (showExpiredState()) return;

    const bar = document.querySelector('[data-progressbar]');
    if (bar) {
      const onScroll = () => {
        const h = document.documentElement;
        const max = h.scrollHeight - h.clientHeight;
        bar.style.transform = 'scaleX(' + (max > 0 ? h.scrollTop / max : 0) + ')';
      };
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();
    }

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const ease = 'cubic-bezier(0.2, 0.7, 0.2, 1)';
    const fmt = (v, d) => new Intl.NumberFormat('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);

    const reveals = Array.from(document.querySelectorAll('[data-reveal]'));
    reveals.forEach(el => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(28px)';
      el.style.transition = 'opacity 0.9s ' + ease + ', transform 0.9s ' + ease;
      el.style.transitionDelay = (parseInt(el.getAttribute('data-delay') || '0', 10)) + 'ms';
    });

    const bars = Array.from(document.querySelectorAll('[data-grow]'));
    bars.forEach(el => {
      el._target = el.getAttribute('data-grow');
      el.style.transition = 'width 1.3s ' + ease;
      el.style.width = '0%';
    });

    const rings = Array.from(document.querySelectorAll('[data-ring]'));
    rings.forEach(el => {
      el._target = el.getAttribute('data-ring');
      el.style.transition = 'stroke-dashoffset 1.6s ' + ease;
      el.setAttribute('stroke-dashoffset', '540.4');
    });

    const counts = Array.from(document.querySelectorAll('[data-count]'));
    counts.forEach(el => {
      el._targetVal = parseFloat(el.getAttribute('data-count'));
      el._decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
    });

    const runCount = (el) => {
      const dur = 1500, start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / dur);
        const e = 1 - Math.pow(1 - t, 3);
        el.textContent = fmt(el._targetVal * e, el._decimals);
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    };

    const io = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        if (el.hasAttribute('data-reveal')) {
          el.style.opacity = '1';
          el.style.transform = 'translateY(0)';
        }
        if (el.hasAttribute('data-grow')) el.style.width = el._target;
        if (el.hasAttribute('data-ring')) el.style.strokeDashoffset = el._target;
        if (el.hasAttribute('data-count') && !el._done) { el._done = true; runCount(el); }
        io.unobserve(el);
      });
    }, { threshold: 0.2 });

    reveals.concat(bars, rings, counts).forEach(el => io.observe(el));

    const assistant = document.querySelector('[data-rove-assistant]');
    const planSection = document.querySelector('[data-screen-label="10 Nächster Monat"]');
    if (assistant && planSection) {
      const assistantIo = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          assistant.classList.toggle('is-visible', entry.isIntersecting);
        });
      }, { threshold: 0.36 });
      assistantIo.observe(planSection);
    }

    const wealthEl = document.querySelector('.wealth-pulse');
    if (wealthEl) {
      const wealthIo = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          setTimeout(() => wealthEl.classList.add('is-pulsing'), 1500);
          wealthIo.disconnect();
        });
      }, { threshold: 0.6 });
      wealthIo.observe(wealthEl);
    }
  });
})();
</script>
""".strip()


def month_label_with_offset(report_month: str, months_ahead: int = 0) -> str:
    year, month = map(int, report_month.split("-"))
    names = {
        1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
        7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
    }
    month_index = year * 12 + (month - 1) + months_ahead
    target_year, target_month_index = divmod(month_index, 12)
    return f"{names[target_month_index + 1]} {target_year}"


def month_names(report_month: str) -> tuple[str, str]:
    return month_label_with_offset(report_month), month_label_with_offset(report_month, 1)


def _story_money(value) -> str:
    return money_text(float(value or 0), 2).replace(",00", "")


def _story_metric_value(metric: dict) -> str:
    value = metric.get("value")
    key = str(metric.get("semantic_key") or "")
    if value is None:
        return "Noch offen"
    if key in {"month_summary", "next_month_priorities", "rove_score"}:
        return de_number(value)
    return _story_money(value)


def _localize_story_text(value) -> str:
    text = str(value or "")

    def replace_amount(match: re.Match) -> str:
        amount = float(match.group(1))
        return _story_money(amount)

    return re.sub(r"(?<![\d.,])(\d+(?:\.\d{2}))\s+EUR\b", replace_amount, text)


def _pre_truth_story_render_context(data: dict) -> dict:
    """Map frozen pre-V2 facts without consulting current application state."""
    meta = data.get("meta") or {}
    profile = data.get("profile") or {}
    pages = data.get("pages") or {}
    report_month = str(meta.get("report_month") or "")
    month_label, next_month_label = month_names(report_month)
    financial_story = pages.get("financial_story") or {}
    month = pages.get("month") or {}
    journey = pages.get("wealth_journey") or {}
    investment_summary = journey.get("investment_summary") or {}
    goal_page = pages.get("goal") or {}
    score_page = pages.get("score") or {}
    money_map = pages.get("money_map") or {}
    recap = pages.get("recap") or {}

    def frozen_value(*values):
        return next((value for value in values if value is not None), None)

    cash = frozen_value(financial_story.get("cash"), profile.get("cash_reserve"))
    investments = frozen_value(
        financial_story.get("investments"), profile.get("current_investments")
    )
    property_equity = profile.get("property_equity")
    wealth_total = frozen_value(financial_story.get("net_worth"), profile.get("net_worth"))
    if wealth_total is None and all(value is not None for value in (cash, investments, property_equity)):
        wealth_total = float(cash) + float(investments) + float(property_equity)

    consumption = month.get("total_expenses")
    contribution = frozen_value(
        investment_summary.get("net_contributions"),
        (pages.get("cover") or {}).get("freedom_step"),
    )
    categories = money_map.get("categories") or []
    category_text = next(
        (str(value).strip() for value in money_map.get("insights") or [] if str(value).strip()),
        (
            "Deine Kategorien basieren auf dem eingefrorenen Monatsabschluss."
            if categories
            else "Dieser Report bleibt in seiner ursprünglichen Fassung erhalten."
        ),
    )

    goal_target = float(goal_page.get("target_amount") or 0)
    goal_current = float(goal_page.get("current_amount") or 0)
    goal_progress = (
        float(goal_page.get("progress_percent"))
        if goal_page.get("progress_percent") is not None
        else min(100.0, goal_current / goal_target * 100) if goal_target > 0 else 0.0
    )
    goal_available = goal_target > 0

    category_context = []
    total_consumption = float(consumption or 0)
    max_category = max((float(item.get("total") or 0) for item in categories), default=1.0) or 1.0
    for index, item in enumerate(categories[:5]):
        amount = float(item.get("total") or 0)
        category_context.append({
            "rank": index + 1,
            "name": category_label(item.get("category")).title(),
            "amount": _story_money(amount),
            "share": fmt_percent(amount / total_consumption * 100 if total_consumption else 0, 1),
            "share_raw": amount / total_consumption * 100 if total_consumption else 0,
            "bar": max(4.0, min(100.0, amount / max_category * 100)),
            "count": int(item.get("transaction_count") or 0),
            "average": _story_money(item.get("avg_transaction")),
            "delta": 0.0,
            "class": "unclear",
        })

    def display_money(value) -> str:
        return _story_money(value) if value is not None else "—"

    return {
        "page_count": 0,
        "month_label": month_label,
        "next_month_label": next_month_label,
        "facts": [],
        "flow": [],
        "categories": category_context,
        "merchants": [],
        "changes": [],
        "allocation": [],
        "contributions": [],
        "contribution_more_count": 0,
        "goal": {
            "available": goal_available,
            "name": str(goal_page.get("description") or "Dein Ziel"),
            "current": display_money(goal_current) if goal_available else "—",
            "target": display_money(goal_target) if goal_available else "—",
            "remaining": display_money(max(0.0, goal_target - goal_current)) if goal_available else "—",
            "progress": fmt_percent(goal_progress, 1),
            "progress_raw": goal_progress,
        },
        "score": int(score_page.get("clarity_score") or 0),
        "insight": {"type": "legacy_snapshot", "text": "Dieser Report bleibt in seiner ursprünglichen Fassung erhalten.", "tone": "neutral", "safe_to_coach": False},
        "next_steps": [],
        "recap_good": str(recap.get("what_went_well") or ""),
        "recap_attention": str(recap.get("needs_attention") or ""),
        "metrics": {},
        "pages": {
            "page_3": {
                "text": category_text,
            },
        },
        "wealth_total": display_money(wealth_total),
        "consumption_total": display_money(consumption),
        "contribution_total": display_money(contribution),
        "cash_total": display_money(cash),
        "income_total": display_money(profile.get("income_total")),
        "fixed_costs_total": display_money(profile.get("fixed_costs")),
    }


def build_story_render_context(data: dict) -> dict:
    """Build presentation-only fields from the immutable Story V2 payload."""
    if not data.get("report_truth") and not data.get("report_story_v2"):
        return _pre_truth_story_render_context(data)
    story = story_from_snapshot_data(data)
    pages = story["pages"]
    truth = data.get("report_truth") or {}
    report_month = str((data.get("meta") or {}).get("report_month") or story.get("report_month") or "")
    month_label, next_label = month_names(report_month)

    def metric(page_key: str) -> dict:
        raw = dict(pages[page_key].get("primary_metric") or {})
        raw["display_value"] = _story_metric_value(raw)
        return raw

    facts = []
    for item in pages["page_1"].get("supporting_metrics") or []:
        key = str(item.get("key") or "")
        value = item.get("value")
        if isinstance(value, (int, float)):
            value = fmt_percent(value, 1) if key == "goal_progress" else _story_money(value)
        facts.append({"label": str(item.get("label") or "Fakt"), "value": _localize_story_text(value or "-")})

    flow = []
    for item in pages["page_2"].get("supporting_metrics") or []:
        flow.append({
            "key": str(item.get("key") or ""),
            "label": str(item.get("label") or ""),
            "value": _story_money(item.get("value")),
            "confirmed": bool(item.get("confirmed")),
        })

    categories_raw = pages["page_3"].get("supporting_metrics") or []
    categories = []
    max_category = max((float(item.get("amount") or 0) for item in categories_raw), default=1.0) or 1.0
    for index, item in enumerate(categories_raw[:6]):
        amount = float(item.get("amount") or 0)
        budget = dict(item.get("budget") or {})
        budget_limit = float(budget.get("limit") or 0)
        budget_used = float(budget.get("used") or amount or 0)
        budget_line = ""
        if budget_limit > 0:
            diff = budget_used - budget_limit
            budget_line = (
                f"Budget {_story_money(budget_limit)} · {_story_money(diff)} über Budget"
                if diff > 0
                else f"Budget {_story_money(budget_limit)} · {_story_money(abs(diff))} frei"
            )
        categories.append({
            "rank": index + 1,
            "name": str(item.get("category") or "Sonstiges"),
            "amount": _story_money(amount),
            "amount_raw": amount,
            "share": fmt_percent(item.get("share") or 0, 1),
            "share_raw": max(0.0, min(100.0, float(item.get("share") or 0))),
            "bar": max(4.0, min(100.0, amount / max_category * 100)),
            "count": int(item.get("transaction_count") or 0),
            "average": _story_money(item.get("avg_transaction")),
            "delta": float(item.get("delta") or 0),
            "class": str(item.get("class") or "unclear"),
            "budget": budget,
            "budget_line": budget_line,
            "budget_over": bool(item.get("budget_over")),
        })

    merchants = []
    for item in pages["page_4"].get("supporting_metrics") or []:
        if not valid_report_merchant(item.get("merchant"), item.get("category")):
            continue
        merchants.append({
            "rank": len(merchants) + 1,
            "name": str(item.get("merchant") or "Unbekannt"),
            "category": category_label(item.get("category") or "Sonstiges"),
            "amount": _story_money(item.get("amount")),
            "amount_raw": float(item.get("amount") or 0),
            "count": int(item.get("transaction_count") or 0),
            "average": _story_money(item.get("avg_transaction")),
            "share": fmt_percent(item.get("share") or 0, 1),
            "share_raw": max(0.0, min(100.0, float(item.get("share") or 0))),
        })
        if len(merchants) == 3:
            break

    changes = []
    for item in (pages["page_5"].get("supporting_metrics") or [])[:3]:
        delta = float(item.get("delta") or 0)
        current = item.get("current")
        previous = item.get("previous")
        delta_percent = item.get("delta_percent")
        if delta_percent is None and previous is not None and abs(float(previous)) > 0.0049:
            delta_percent = round(delta / abs(float(previous)) * 100, 2)
        amount_text = f"{'+' if delta > 0 else '-'}{_story_money(abs(delta))}" if delta else "0 €"
        pct_label = (
            f"{'+' if float(delta_percent) > 0 else ''}{de_number(delta_percent, 1)} %"
            if delta_percent is not None else "Vergleich"
        )
        changes.append({
            "label": str(item.get("label") or "Veränderung"),
            "context": _localize_story_text(item.get("context")),
            "amount_text": amount_text,
            "pct_label": pct_label,
            "delta": _story_money(abs(delta)),
            "delta_raw": delta,
            "delta_percent": delta_percent,
            "current_raw": float(current) if current is not None else None,
            "previous_raw": float(previous) if previous is not None else None,
            "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
        })

    allocation = []
    allocation_palette = ["blue", "cyan", "sand", "mint", "slate"]
    for index, item in enumerate(pages["page_6"].get("supporting_metrics") or []):
        amount = float(item.get("amount") or 0)
        if amount <= 0:
            continue
        allocation.append({
            "label": str(item.get("label") or "Vermögen"),
            "amount": _story_money(amount),
            "share": fmt_percent(item.get("share") or 0, 1),
            "share_raw": max(0.0, min(100.0, float(item.get("share") or 0))),
            "tone": allocation_palette[index % len(allocation_palette)],
        })
    if not allocation:
        wealth_truth = truth.get("wealth") or {}
        wealth_total = float(wealth_truth.get("total") or 0)
        fallback_items = (
            ("Cash", wealth_truth.get("cash")),
            ("Investments", wealth_truth.get("investments")),
            ("Immobilie", wealth_truth.get("property_equity")),
        )
        for label, raw_amount in fallback_items:
            amount = float(raw_amount or 0)
            if amount <= 0:
                continue
            share = amount / wealth_total * 100 if wealth_total > 0 else 0
            allocation.append({
                "label": label,
                "amount": _story_money(amount),
                "share": fmt_percent(share, 1),
                "share_raw": max(0.0, min(100.0, share)),
                "tone": allocation_palette[len(allocation) % len(allocation_palette)],
            })

    contribution_items = [
        item
        for item in (pages["page_7"].get("supporting_metrics") or [])
        if float(item.get("amount", item.get("total", 0)) or 0) != 0
    ]
    contributions = []
    for item in contribution_items[:4]:
        amount = item.get("amount", item.get("total", 0))
        name = item.get("name") or item.get("asset_type") or "Investment"
        contributions.append({
            "name": str(name),
            "asset_type": str(item.get("asset_type") or "investment").upper(),
            "amount": _story_money(amount),
        })

    goal_visual = (pages["page_8"].get("visual") or {}).get("data") or {}
    primary_goal = goal_visual.get("primary_goal") or {}
    target = float(primary_goal.get("target_amount") or 0)
    current = float(primary_goal.get("current_amount") or 0)
    goal_progress = min(100.0, current / target * 100) if target > 0 else 0.0
    goal = {
        "available": bool(primary_goal and target > 0),
        "name": str(primary_goal.get("name") or "Dein Ziel"),
        "current": _story_money(current),
        "target": _story_money(target),
        "remaining": _story_money(max(0.0, target - current)),
        "progress": fmt_percent(goal_progress, 1),
        "progress_raw": goal_progress,
    }
    score = goal_visual.get("score") or {}
    score_value = int(score.get("clarity_score") or (score.get("parts") or {}).get("total") or 0)

    insight = (story.get("insight_engine") or {}).get("selected") or {}
    next_steps = [
        {
            "number": index + 1,
            "title": str(item.get("title") or "Nächster Schritt"),
            "text": _localize_story_text(item.get("text")),
        }
        for index, item in enumerate((story.get("next_month_engine") or {}).get("steps") or [])
    ]

    budget = truth.get("budget") or {}
    savings_truth = truth.get("savings") or {}
    investment_contribution_total = float(
        ((truth.get("investments") or {}).get("contributions") or {}).get("net_contributions") or 0
    )
    contribution_total = (
        float(savings_truth.get("actual_amount") or 0)
        if savings_truth.get("confirmed")
        else investment_contribution_total
    )
    if budget.get("has_budgets") and budget.get("on_track"):
        recap_good = "Deine gesetzten Budgets lagen im Rahmen."
    elif savings_truth.get("confirmed"):
        recap_good = f"Du hast {_story_money(contribution_total)} tatsächliche Sparleistung bestätigt."
    elif contribution_total > 0:
        recap_good = f"Du hast {_story_money(contribution_total)} investiert."
    else:
        recap_good = "Dein Monat ist vollständig sichtbar und damit vergleichbar."
    if insight.get("suggested_tone") == "positive":
        recap_good = _localize_story_text(insight.get("fallback_text") or recap_good)
        recap_attention = changes[0]["context"] if changes else "Die Entwicklung bleibt im nächsten Monat vergleichbar."
    else:
        recap_attention = _localize_story_text(
            insight.get("fallback_text")
            or (changes[0]["context"] if changes else "Behalte die Entwicklung im Blick.")
        )

    page_copy = {
        key: {
            **page,
            "text": _localize_story_text(page.get("text")),
            "empty_state": _localize_story_text(page.get("empty_state")),
        }
        for key, page in pages.items()
    }

    return {
        "story_version": story.get("story_version"),
        "page_count": story.get("page_count"),
        "month_label": month_label,
        "next_month_label": next_label,
        "tracked_days": int((data.get("meta") or {}).get("tracked_days") or 0),
        "facts": facts[:4],
        "flow": flow,
        "categories": categories,
        "merchants": merchants,
        "changes": changes,
        "allocation": allocation,
        "contributions": contributions,
        "contribution_label": (
            "Diesen Monat tatsächlich gespart"
            if savings_truth.get("confirmed")
            else "Diesen Monat investiert oder zurückgelegt"
            if contribution_total > 0 else "Kein neuer Beitrag in diesem Monat"
        ),
        "contribution_empty_text": "Keine Position mit neuem Beitrag in diesem Monat.",
        "contribution_more_count": max(0, len(contribution_items) - len(contributions)),
        "goal": goal,
        "score": score_value,
        "insight": {
            "type": str(insight.get("type") or "stable_month"),
            "text": _localize_story_text(insight.get("fallback_text") or pages["page_9"].get("text")),
            "tone": str(insight.get("suggested_tone") or "neutral"),
            "safe_to_coach": bool(insight.get("safe_to_coach")),
        },
        "next_steps": next_steps,
        "recap_good": recap_good,
        "recap_attention": recap_attention,
        "metrics": {key: metric(key) for key in pages},
        "pages": page_copy,
        "wealth_total": metric("page_6")["display_value"],
        "consumption_total": _story_money((truth.get("expenses") or {}).get("total_consumption")),
        "contribution_total": _story_money(contribution_total),
        "cash_total": _story_money((truth.get("cash") or {}).get("current_cash")),
        "income_total": _story_money((truth.get("income") or {}).get("amount")),
        "fixed_costs_total": _story_money((truth.get("fixed_costs") or {}).get("amount")),
    }


def _v2_legacy_visual_context(data: dict) -> dict:
    """Map immutable Story V2 data onto the original web report visual language."""
    report = build_story_render_context(data)
    truth = data.get("report_truth") or {}
    wealth = get_report_wealth(data)
    expenses = truth.get("expenses") or {}
    score_truth = truth.get("score") or {}
    score_parts_raw = (score_truth.get("parts") or {}).get("factors") or []
    score_value = int(report["score"] or 0)
    score_dash = round(540.4 * (100 - max(0, min(100, score_value))) / 100, 1)
    band = rank_band(score_value)
    band_span = max(1, band["high"] - band["low"])
    rank_name = next(
        (name for low, high, name, _icon in SCORE_RANKS if low <= score_value <= high),
        "Rookie",
    )

    categories = report["categories"]
    strongest = categories[0] if categories else {
        "name": "Noch offen", "amount": "0 €", "bar": 0, "share_raw": 0, "share": "0,0 %"
    }
    category_count = len(categories)
    transaction_count = int(expenses.get("transaction_count") or sum(item.get("count", 0) for item in categories))
    merchants = report["merchants"]
    biggest = merchants[0] if merchants else {
        "name": "Kein Händler erkannt", "amount": "0 €", "amount_raw": 0,
        "share_raw": 0, "share": "0,0 %"
    }
    merchant_rows = [
        {
            "rank": str(index),
            "name": h(item["name"]),
            "amount": item["amount"],
            "count_text": f"{item['count']} {'Ausgabe' if item['count'] == 1 else 'Ausgaben'}",
            "average": item["average"],
            "category": h(item["category"]),
        }
        for index, item in enumerate(merchants[:3], start=1)
    ]
    month_short = report["month_label"].split(" ", 1)[0]
    next_month_name = report["next_month_label"].split(" ", 1)[0]
    savings_truth = truth.get("savings") or {}
    investment_contribution_total = float(
        ((truth.get("investments") or {}).get("contributions") or {}).get("net_contributions") or 0
    )
    contribution_total_raw = (
        float(savings_truth.get("actual_amount") or 0)
        if savings_truth.get("confirmed")
        else investment_contribution_total
    )
    wealth_available = bool(wealth.get("available"))
    net_worth_raw = float(wealth.get("total") or 0)
    cash_raw = float(wealth.get("cash") or 0)
    investments_raw = float(wealth.get("investments") or 0)
    property_raw = float(wealth.get("property_equity") or 0)
    wealth_total = max(0.0, net_worth_raw)

    allocation_total = sum(float(item.get("share_raw") or 0) for item in report["allocation"]) or 100.0
    investment_share = (investments_raw / wealth_total * 100) if wealth_total > 0 else 0.0
    cash_share = (cash_raw / wealth_total * 100) if wealth_total > 0 else 0.0
    property_share = (property_raw / wealth_total * 100) if wealth_total > 0 else 0.0

    money_map_categories = []
    palette = [
        {"bar_color": "linear-gradient(90deg, #2D7FCC 0%, #3BA7FF 100%)", "text_color": "#111318"},
        {"bar_color": "rgba(42,171,238,0.4)", "text_color": "#F4F1EA"},
        {"bar_color": "rgba(255,255,255,0.18)", "text_color": "#F4F1EA"},
    ]
    for index, item in enumerate(categories):
        colors = palette[min(index, len(palette) - 1)]
        money_map_categories.append({
            "name": h(item["name"]),
            "bar_pct": item["bar"],
            "pct_text": int(round(float(item.get("share_raw") or 0))),
            "amount_text": item["amount"],
            "count": item.get("count", 0),
            "average": item.get("average", "0 €"),
            "budget": dict(item.get("budget") or {}),
            "budget_over": bool(item.get("budget_over")),
            "bar_color": colors["bar_color"],
            "text_color": colors["text_color"],
        })

    comparison_rows = []
    max_change = max((abs(float(item.get("delta_raw") or 0)) for item in report["changes"]), default=1.0) or 1.0
    for index, item in enumerate(report["changes"][:5]):
        colors = palette[min(index, len(palette) - 1)]
        delta = float(item.get("delta_raw") or 0)
        delta_percent = item.get("delta_percent")
        if delta_percent is None:
            previous = item.get("previous_raw")
            current = item.get("current_raw")
            if previous is None and current is not None:
                previous = float(current) - delta
            if previous is not None and abs(float(previous)) > 0.0049:
                delta_percent = round(delta / abs(float(previous)) * 100, 2)
        comparison_rows.append({
            "name": h(item["label"]),
            "bar_pct": max(4.0, min(100.0, abs(delta) / max_change * 100)),
            "pct_label": (
                f"{'+' if float(delta_percent) > 0 else ''}{de_number(delta_percent, 1)} %"
                if delta_percent is not None else "Vergleich"
            ),
            "amount_text": f"{'+' if delta > 0 else '-'}{_story_money(abs(delta))}" if delta else "0 €",
            "bar_color": colors["bar_color"],
            "text_color": colors["text_color"],
        })

    score_parts = []
    for item in score_parts_raw:
        score_parts.append({
            "key": str(item.get("key") or ""),
            "label": str(item.get("n") or item.get("label") or item.get("key") or "Faktor"),
            "value": int(item.get("points") or 0),
            "max": int(item.get("max") or 25),
            "warn": False,
        })
    if score_parts:
        weakest = min(range(len(score_parts)), key=lambda idx: score_parts[idx]["value"])
        score_parts[weakest]["warn"] = True

    score_names = {
        "budget": "Budget",
        "savings": "Sparausführung",
        "consistency": "Tracking",
        "structure": "Struktur",
    }
    weakest_part = min(
        score_parts,
        key=lambda item: item["value"] / max(1, item["max"]),
        default=None,
    )
    strong_parts = [
        score_names.get(item["key"], item["label"])
        for item in score_parts
        if item["value"] / max(1, item["max"]) >= 0.8
    ]
    weakest_name = score_names.get((weakest_part or {}).get("key"), (weakest_part or {}).get("label", "deinem nächsten Teilbereich"))
    if contribution_total_raw <= 0 and any(item.get("key") == "savings" and item["value"] >= item["max"] for item in score_parts):
        rank_blurb = (
            "Der Spar-Teilscore liegt bei 25/25; in diesem Monat ist jedoch kein neuer "
            f"Investment- oder Sparbeitrag hinzugekommen. Bei {weakest_name} liegt dein nächster Prüfpunkt."
        )
    elif strong_parts:
        strong_text = " und ".join(strong_parts[:2])
        rank_blurb = f"{strong_text} {'sind' if len(strong_parts) > 1 else 'ist'} stark. Bei {weakest_name} liegt dein nächster Prüfpunkt."
    else:
        rank_blurb = f"Dein niedrigster Teilscore liegt bei {weakest_name}. Dort ist der nächste sachliche Prüfpunkt."

    strongest_amount_raw = float((expenses.get("categories") or [{}])[0].get("amount") or 0)
    goal = report["goal"]
    goal_name = goal["name"] if goal["available"] else "Dein Ziel"
    savings_plan = float((data.get("profile") or {}).get("savings_plan") or 0)
    tracked_days = int(report["tracked_days"] or 0)

    over_budget = [
        item for item in report["categories"]
        if item.get("budget_over") and float((item.get("budget") or {}).get("limit") or 0) > 0
    ]
    budget_issue = max(
        over_budget,
        key=lambda item: float((item.get("budget") or {}).get("used") or item.get("amount_raw") or 0)
        - float((item.get("budget") or {}).get("limit") or 0),
        default=None,
    )
    budget_limit = float((budget_issue or {}).get("budget", {}).get("limit") or 0)
    budget_used = float((budget_issue or {}).get("budget", {}).get("used") or (budget_issue or {}).get("amount_raw") or 0)
    budget_over = max(0.0, budget_used - budget_limit)
    budget_fact = (
        f"{budget_issue['name']} lag {_story_money(budget_over)} über deinem gesetzten Budget von {_story_money(budget_limit)}."
        if budget_issue else ""
    )

    contribution_text = (
        f"{_story_money(contribution_total_raw)} tatsächlich gespart."
        if savings_truth.get("confirmed") and contribution_total_raw > 0
        else f"{_story_money(contribution_total_raw)} investiert oder zurückgelegt."
        if contribution_total_raw > 0
        else "In diesem Monat ist kein neuer Investment- oder Sparbeitrag hinzugekommen."
    )
    tracking_summary = (
        f"{tracked_days} Tage getrackt" if tracked_days > 0 else "dein Monatsbild vollständig erfasst"
    )
    month_summary_text = (
        f"{month_short}: {tracking_summary}. {contribution_text} "
        f"Deine stärkste Kategorie war {strongest['name']} mit {strongest['amount']}."
    )
    comparison_text = (
        f"{report['changes'][0]['label']}: {report['changes'][0]['context']}"
        if report["changes"] else "Noch kein belastbarer Vormonatsvergleich verfügbar."
    )
    development_text = (
        f"{'+' if float(report['changes'][0].get('delta_raw') or 0) > 0 else '-'}{_story_money(abs(float(report['changes'][0].get('delta_raw') or 0)))}"
        if report["changes"] else "Kein Vergleich"
    )

    if budget_issue:
        recap_good = "Du hast in diesem Monat aktiv Vermögen aufgebaut." if contribution_total_raw > 0 else "Deine Ausgaben sind klar nach Kategorien aufgeschlüsselt."
        recap_attention = budget_fact
        recap_lever = "Prüfe im nächsten Monat, ob diese Abweichung einmalig war oder erneut auftritt."
    elif (truth.get("budget") or {}).get("has_budgets") and (truth.get("budget") or {}).get("on_track"):
        recap_good = "Deine gesetzten Kategorie-Budgets lagen im vorgesehenen Rahmen."
        recap_attention = "In diesem Monat ist keine Budgetüberschreitung hervorgehoben."
        recap_lever = "Behalte die bestehenden Budgets als Vergleichsrahmen für den nächsten Monat bei."
    else:
        recap_good = f"Du hast an {tracked_days} Tagen getrackt und damit eine belastbare Monatsbasis geschaffen."
        recap_attention = report["insight"]["text"]
        recap_lever = "Beobachte im nächsten Monat, ob sich derselbe Zusammenhang wiederholt."

    score_next_title = (
        f"{weakest_name} im nächsten Monat gezielt beobachten."
        if weakest_part else "Halte den nächsten Monat vollständig sichtbar."
    )
    score_next_text = (
        f"Ein stabilerer Wert würde deinen Teilscore für {weakest_name} stärken."
        if weakest_part else "So bleibt dein Report belastbar vergleichbar."
    )

    plan_step1_title = "Halte deine Buchungen im nächsten Monat vollständig."
    plan_step1_sub = "So bleibt auch der nächste Monatsvergleich aussagekräftig."
    if budget_issue:
        plan_step2_title = f"Behalte dein {budget_issue['name']}-Budget von {_story_money(budget_limit)} im Blick."
        plan_step2_sub = "So erkennst du früh, ob die Abweichung einmalig war."
        plan_step2_impact = f"Budget {_story_money(budget_limit)}"
    else:
        plan_step2_title = f"Behalte {strongest['name']} im Blick."
        plan_step2_sub = "Prüfe, ob die Kategorie im nächsten Monat erneut auffällt."
        plan_step2_impact = "Beobachten"
    if savings_plan > 0:
        plan_step3_title = f"Halte deine geplante Sparrate von {_story_money(savings_plan)} ein."
        plan_step3_sub = "Plane die Rate wie vorgesehen ein."
        plan_step3_impact = f"Plan {_story_money(savings_plan)}"
    else:
        plan_step3_title = "Prüfe deinen Sparplan für den nächsten Monat."
        plan_step3_sub = "Ohne hinterlegte Sparrate nennt Rov.E keinen eigenen Zielbetrag."
        plan_step3_impact = "Plan prüfen"

    primary_goal = (truth.get("goals") or {}).get("primary") or {}
    goal_remaining = max(
        0.0,
        float(primary_goal.get("target_amount") or 0) - float(primary_goal.get("current_amount") or 0),
    ) if goal["available"] else 0.0
    if goal["available"] and savings_plan > 0 and goal_remaining > 0:
        goal_months = max(1, math.ceil(goal_remaining / savings_plan))
        goal_honest_text = (
            f"Bei deiner geplanten Sparrate von {_story_money(savings_plan)} pro Monat "
            f"entspricht der offene Betrag rechnerisch rund {goal_months} Monaten."
        )
        goal_honest_subtext = "Deine hinterlegte Sparrate dient dabei als Orientierung."
        goal_lever_text = f"{_story_money(savings_plan)} pro Monat sind aktuell eingeplant."
        goal_lever_subtext = "Änderst du die Rate, verändert sich auch der Zeitraum."
    else:
        goal_honest_text = "Für dein Ziel ist noch kein monatlicher Zeitraum hinterlegt."
        goal_honest_subtext = "Sobald du eine Rate festlegst, erhältst du eine zeitliche Orientierung."
        goal_lever_text = "Noch keine monatliche Sparrate eingeplant."
        goal_lever_subtext = "Der Zielstand bleibt davon unverändert sichtbar."

    contribution_details = report.get("contributions") or []
    if contribution_total_raw > 0 and contribution_details:
        build_detail_text = " · ".join(
            f"{item['name']}: {item['amount']}" for item in contribution_details[:3]
        )
    elif contribution_total_raw > 0:
        build_detail_text = "Damit bleibt sichtbar, was du in diesem Monat aktiv aufgebaut hast."
    else:
        build_detail_text = "In diesem Monat kam kein neuer Beitrag hinzu."

    previous_month_label = month_label_with_offset((data.get("meta") or {}).get("report_month", ""), -1)
    comparison_basis_text = f"{report['month_label']} im Vergleich zu {previous_month_label}."

    mband = milestone_band(wealth_total)
    milestone_remaining = max(0.0, mband["to_amount"] - wealth_total)
    milestone_headline = f"Noch {_story_money(milestone_remaining)} bis zum nächsten Meilenstein."
    milestone_fact = f"Dir fehlen noch {_story_money(milestone_remaining)} bis {_story_money(mband['to_amount'])}."

    return {
        "report": report,
        "month_label": h(report["month_label"]),
        "next_label": h(report["next_month_label"]),
        "next_month_name": h(next_month_name),
        "next_report_delivery_month_name": h(month_label_with_offset((data.get("meta") or {}).get("report_month", ""), 2).split(" ", 1)[0]),
        "month_short": h(month_short),
        "freedom_step_label": "Diesen Monat aufgebaut",
        "freedom_step_text": f"+{report['contribution_total']}" if contribution_total_raw > 0 else "Kein neuer Beitrag",
        "freedom_step_subline": (
            "tatsächlich gespart"
            if savings_truth.get("confirmed") and contribution_total_raw > 0
            else "investiert oder zurückgelegt"
            if contribution_total_raw > 0
            else "kein neuer Beitrag"
        ),
        "development_percent_text": development_text,
        "development_subline": h(
            f"{report['changes'][0]['label']} zum Vormonat"
            if report["changes"] else "kein belastbarer Vormonat"
        ),
        "net_worth_span": data_count_span(net_worth_raw) if wealth_available else "—",
        "investments_span": data_count_span(investments_raw) if wealth_available else "—",
        "cash_span": data_count_span(cash_raw) if wealth_available else "—",
        "biggest_amount_span": data_count_span(biggest.get("amount_raw") or 0),
        "biggest_name": h(biggest["name"]),
        "strongest_amount_span": data_count_span(strongest_amount_raw),
        "strongest_name": h(strongest["name"]),
        "tracked_days": report["tracked_days"],
        "invested_amount": report["contribution_total"],
        "month_summary_text": h(month_summary_text),
        "month_savings_sentence": h(contribution_text),
        "wealth_headline": h(report["pages"]["page_6"].get("text") or "So ist dein Vermoegen verteilt."),
        "wealth_sentence": h("Nur vorhandene Vermoegensklassen werden gezeigt."),
        "strongest_amount": strongest["amount"],
        "ratio_sentence": h(report["pages"]["page_9"].get("text") or report["insight"]["text"]),
        "invested_span": data_count_span(contribution_total_raw),
        "ratio_span": data_count_span(0, 2),
        "halve_sentence": h(report["insight"]["text"]),
        "yearly_span": data_count_span(0),
        "goal_desc": h(goal_name),
        "goal_headline": h("Dein Ziel"),
        "goal_context_text": h(goal_name),
        "investments_pct_raw": round(investment_share, 1),
        "investments_pct_text": fmt_percent(investment_share, 1),
        "cash_pct_text": fmt_percent(cash_share, 1),
        "cash_pct_raw": round(cash_share, 1),
        "property_pct_raw": round(property_share, 1),
        "property_pct_text": fmt_percent(property_share, 1),
        "has_property_equity": property_raw > 0,
        "investments_amount": _story_money(investments_raw),
        "cash_amount": _story_money(cash_raw),
        "property_equity_amount": _story_money(property_raw),
        "invest_story_headline": h(report["pages"]["page_6"].get("question") or "Wo steckt dein Vermoegen heute?"),
        "invest_story_sub": h(report["pages"]["page_6"].get("text") or ""),
        "money_map_categories": money_map_categories,
        "money_map_category_count": category_count,
        "money_map_transaction_count": transaction_count,
        "comparison_rows": comparison_rows,
        "comparison_text": h(comparison_text),
        "has_budget_status": bool((truth.get("budget") or {}).get("has_budgets")),
        "budget_status_title": "Budgetrahmen",
        "budget_status_text": h(report["recap_good"]),
        "budget_status_subtext": h(report["recap_attention"]),
        "biggest_amount": biggest["amount"],
        "biggest_share_pct": int(round(float(biggest.get("share_raw") or 0))),
        "merchant_rows": merchant_rows,
        "build_summary_text": h(contribution_text),
        "invest_vs_strongest_text": h(build_detail_text),
        "score_value": score_value,
        "score_span": data_count_span(score_value),
        "score_headline_suffix": "Rov.E hat den Monat eingeordnet.",
        "score_dash": score_dash,
        "rank_name": h(rank_name),
        "prev_rank_name": h(band["prev_name"]) if band["prev_name"] else "",
        "next_rank_name": h(band["next_name"]) if band["next_name"] else "",
        "rank_band_low": round(band["low"] / 100 * 100, 1),
        "rank_band_high": round((band["high"] + 1) / 100 * 100, 1),
        "rank_band_text": f"{band['low']}-{band['high']}",
        "score_parts": score_parts,
        "rank_blurb": h(rank_blurb),
        "next_step_headline": h(score_next_title),
        "next_step_sub": h(score_next_text),
        "goal_pct_span": data_count_span(goal["progress_raw"], 1),
        "goal_pct_raw": goal["progress_raw"],
        "goal_target_amount": goal["target"],
        "goal_current_amount": goal["current"],
        "net_worth_amount": _story_money(wealth_total) if wealth_available else "—",
        "goal_remaining_amount": goal["remaining"],
        "goal_title_text": h(f"Dein Ziel: {goal_name}."),
        "goal_honest_text": h(goal_honest_text),
        "goal_honest_subtext": h(goal_honest_subtext),
        "goal_lever_label": "Dein Plan",
        "goal_lever_text": h(goal_lever_text),
        "goal_lever_subtext": h(goal_lever_subtext),
        "milestone_headline": h(milestone_headline),
        "milestone_from": _story_money(mband["from_amount"]),
        "milestone_to": _story_money(mband["to_amount"]),
        "milestone_pct_text": int(round(mband["pct"])),
        "milestone_pct_raw": round(mband["pct"], 1),
        "milestone_eta_text": h(comparison_basis_text),
        "milestone_fact_text": h(milestone_fact),
        "badges": [],
        "recap_good_text": h(recap_good),
        "recap_attention_text": h(recap_attention),
        "recap_lever_text": h(recap_lever),
        "plan_step1_title": h(plan_step1_title),
        "plan_step1_sub": h(plan_step1_sub),
        "plan_step1_impact": "Datenbasis",
        "plan_step2_title": h(plan_step2_title),
        "plan_step2_target": _story_money(budget_limit) if budget_issue else strongest["amount"],
        "plan_step2_sub": h(plan_step2_sub),
        "plan_step2_impact": h(plan_step2_impact),
        "plan_step3_title": h(plan_step3_title),
        "plan_step3_sub": h(plan_step3_sub),
        "plan_step3_target": _story_money(savings_plan) if savings_plan > 0 else "—",
        "plan_step3_impact": h(plan_step3_impact),
    }


def build_render_context(data: dict) -> dict:
    if data.get("report_truth"):
        return _v2_legacy_visual_context(data)

    meta = data["meta"]
    profile = data["profile"]
    pages = data["pages"]
    month_label, next_label = month_names(meta["report_month"])
    next_month_name = next_label.split(" ", 1)[0]
    next_report_delivery_month_name = month_label_with_offset(
        meta["report_month"], 2
    ).split(" ", 1)[0]
    month_short = month_label.split(" ", 1)[0]

    cover = pages["cover"]
    story = pages["financial_story"]
    month = pages["month"]
    score = pages["score"]
    goal = pages["goal"]
    money_map = pages["money_map"]
    budget_frame = pages.get("budget") or {}
    milestones = pages["milestones"]
    recap = pages["recap"]
    ai = data.get("ai_narratives") or {}

    strongest = month.get("strongest_category") or {"category": "Noch offen", "total": 0}
    biggest = month.get("biggest_expense") or {"merchant": "Noch offen", "amount": 0}
    strongest_name = category_label(strongest.get("category"))
    strongest_amount = float(strongest.get("total") or 0)
    biggest_name = biggest.get("merchant") or "Noch offen"
    biggest_amount = float(biggest.get("amount") or 0)

    score_value = int(score.get("clarity_score") or 0)
    score_dash = round(540.4 * (100 - max(0, min(100, score_value))) / 100, 1)
    rank_name = score.get("rank_name") or "Rookie"
    parts = score.get("parts") or {}
    band = rank_band(score_value)
    band_span = max(1, band["high"] - band["low"])
    rank_band_low = round(band["low"] / 100 * 100, 1)
    rank_band_high = round((band["high"] + 1) / 100 * 100, 1)

    goal_desc = goal.get("description") or "Dein Ziel"
    months_to_goal = goal.get("months_to_goal")
    goal_duration = format_month_duration(months_to_goal) if months_to_goal else "noch nicht berechenbar"
    savings_plan = profile.get("savings_plan") or 0
    goal_target = goal.get("target_amount") or 0
    goal_current = goal.get("current_amount") or 0
    goal_monthly_rate = goal.get("goal_monthly_rate")
    goal_pct = round(min(100.0, goal.get("progress_percent") or 0), 1)
    tracked_days = int(meta.get("tracked_days") or 0)

    net_worth = story.get("net_worth") or profile.get("net_worth") or 0
    investments = story.get("investments") or profile.get("current_investments") or 0
    cash = story.get("cash") or profile.get("cash_reserve") or 0
    property_equity = profile.get("property_equity") or 0
    total_expenses = month.get("total_expenses") or 0
    wealth_total = investments + cash + property_equity
    investments_pct = round((investments / wealth_total * 100) if wealth_total > 0 else 0, 1)
    cash_pct = round((cash / wealth_total * 100) if wealth_total > 0 else 0, 1)
    property_pct = round((property_equity / wealth_total * 100) if wealth_total > 0 else 0, 1)

    investment_summary = data["pages"]["wealth_journey"].get("investment_summary", {})
    investment_total = float(investment_summary.get("net_contributions") or 0)
    savings_progress = data["pages"]["wealth_journey"].get("savings_progress", {})
    full_plan_amount = max(0.0, float(savings_progress.get("full_plan_amount") or 0))
    automatic_etf_amount = max(0.0, float(savings_progress.get("automatic_etf_amount") or 0))
    full_plan_confirmed = bool(savings_progress.get("full_plan_confirmed")) and full_plan_amount > 0
    actual_savings = full_plan_amount if full_plan_confirmed else automatic_etf_amount
    invested_amount_raw = actual_savings
    wealth_copy = wealth_position_copy(net_worth, investments, cash, wealth_total)
    if full_plan_confirmed:
        month_savings_sentence = f"Du hast {money_text(actual_savings)} deiner Sparrate umgesetzt."
        freedom_step_label = "Sparfortschritt"
        freedom_step_subline = "diesen Monat bestätigt"
    elif automatic_etf_amount > 0:
        month_savings_sentence = f"Dein ETF-Sparplan wurde mit {money_text(automatic_etf_amount)} erfasst."
        freedom_step_label = "ETF-Sparplan"
        freedom_step_subline = "automatisch in Rov.E erfasst"
    elif savings_plan > 0:
        month_savings_sentence = f"Für deine Sparrate sind {money_text(savings_plan)} pro Monat geplant."
        freedom_step_label = "Sparrate"
        freedom_step_subline = "monatlich geplant · noch nicht bestätigt"
    else:
        month_savings_sentence = "Dein Monatsbild wird mit jeder Buchung klarer."
        freedom_step_label = "Sparrate"
        freedom_step_subline = "noch nicht geplant"

    # "Hebel"-Berechnung: Kategorie halbieren, freigesetzten Betrag hochrechnen
    half_target = strongest_amount / 2
    freed_up_monthly = max(0.0, strongest_amount - half_target)
    freed_up_yearly = freed_up_monthly * 12

    ratio_to_savings = (strongest_amount / invested_amount_raw) if invested_amount_raw > 0 else 0

    categories = money_map.get("categories") or []
    max_cat = max((c.get("total") or 0 for c in categories), default=0) or 1
    palette = [
        {"bar_color": "linear-gradient(90deg, #2D7FCC 0%, #3BA7FF 100%)", "text_color": "#111318"},
        {"bar_color": "rgba(42,171,238,0.4)", "text_color": "#F4F1EA"},
        {"bar_color": "rgba(255,255,255,0.18)", "text_color": "#F4F1EA"},
    ]
    money_map_categories = []
    for idx, cat in enumerate(categories):
        total = float(cat.get("total") or 0)
        colors = palette[min(idx, len(palette) - 1)]
        money_map_categories.append({
            "name": h(category_label(cat.get("category"))),
            "bar_pct": round((total / max_cat * 100) if max_cat else 0, 1),
            "pct_text": int(round((total / total_expenses * 100) if total_expenses else 0)),
            "amount_text": money_text(total),
            "bar_color": colors["bar_color"],
            "text_color": colors["text_color"],
        })

    budget_items = budget_frame.get("items") or []
    budget_over_items = [item for item in budget_items if item.get("over")]
    has_budget_status = bool(budget_frame.get("has_budgets")) and bool(budget_items)
    budget_status_title = "Rov.E Budgetrahmen"
    budget_status_text = ""
    budget_status_subtext = ""
    if has_budget_status and not budget_over_items:
        budget_status_text = (
            "Deinen gesetzten Rahmen hast du eingehalten."
            if len(budget_items) == 1
            else f"Du hast alle {len(budget_items)} gesetzten Rahmen eingehalten."
        )
        budget_status_subtext = (
            f"{money_text(budget_frame.get('total_used') or 0)} von "
            f"{money_text(budget_frame.get('total_limit') or 0)} in deinen Budgetkategorien genutzt."
        )
    elif has_budget_status:
        first_over = budget_over_items[0]
        category_name = category_label(first_over.get("category"))
        over_amount = max(0.0, float(first_over.get("used") or 0) - float(first_over.get("limit") or 0))
        if len(budget_over_items) == 1:
            budget_status_text = f"Im Bereich {category_name} liegst du {money_text(over_amount)} über deinem Rahmen."
        else:
            budget_status_text = f"{len(budget_over_items)} deiner gesetzten Rahmen wurden überschritten."
        kept_count = len(budget_items) - len(budget_over_items)
        budget_status_subtext = (
            f"{kept_count} von {len(budget_items)} gesetzten Rahmen eingehalten."
        )
    biggest_share_pct = int(round((biggest_amount / total_expenses * 100) if total_expenses else 0))

    if invested_amount_raw >= strongest_amount * 2 and strongest_amount > 0:
        invest_vs_strongest_text = f"Mehr als doppelt so viel, wie du in {h(strongest_name)} ausgegeben hast."
    elif invested_amount_raw > strongest_amount:
        invest_vs_strongest_text = f"Mehr, als du in {h(strongest_name)} ausgegeben hast."
    else:
        invest_vs_strongest_text = f"Ein wichtiger Baustein neben deinen Ausgaben in {h(strongest_name)}."

    invest_story_headline = wealth_copy["story_headline"]
    invest_story_sub = wealth_copy["story_sub"]

    score_headline_suffix = (
        "du hast dein Geld fest im Griff." if score_value >= 70
        else "du hast dein Geld im Griff." if score_value >= 45
        else "dein Bild wird mit jedem Tracking-Tag klarer."
    )
    rank_blurb = h(RANK_BLURBS.get(rank_name, RANK_BLURBS["Controller"]))

    lowest_key, lowest_label = min(
        [("consistency", "Tracking Consistency"), ("budget", "Budget Control"),
         ("savings", "Savings Execution"), ("structure", "Financial Structure")],
        key=lambda pair: parts.get(pair[0], 0),
    )
    score_parts = [
        {"label": "Budget Control", "value": parts.get("budget", 0), "max": 25, "warn": lowest_key == "budget"},
        {"label": "Savings Execution", "value": parts.get("savings", 0), "max": 25, "warn": lowest_key == "savings"},
        {"label": "Tracking Consistency", "value": parts.get("consistency", 0), "max": 25, "warn": lowest_key == "consistency"},
        {"label": "Financial Structure", "value": parts.get("structure", 0), "max": 25, "warn": lowest_key == "structure"},
    ]
    if lowest_key == "consistency":
        next_step_headline = f"Tracke an mindestens 10 Tagen im {h(next_month_name)}."
    elif lowest_key == "budget":
        next_step_headline = "Halte dein Budget diesen Monat konsequent ein."
    elif lowest_key == "savings":
        next_step_headline = "Setze deine Sparrate diesen Monat verlässlich um."
    else:
        next_step_headline = "Baue deinen Cash-Puffer und deine Sparquote weiter aus."
    next_step_sub = (
        f"Das allein hebt dich in Richtung {h(band['next_name'])}-Status."
        if band["next_name"] else
        f"Das hält dich stabil im {h(rank_name)}-Status."
    )

    goal_gap = max(goal_target - goal_current, 0)
    if goal_target > 0 and goal_pct >= 100:
        goal_honest_text = "Dein Zieltopf ist vollständig gefüllt."
    elif goal_monthly_rate and months_to_goal:
        goal_honest_text = f"Mit deiner geplanten Rate von {money_text(goal_monthly_rate)}/Monat erreichst du dein {h(goal_desc)} rechnerisch in rund {h(goal_duration)}."
    else:
        goal_honest_text = "Sobald deine Sparrate sauber steht, wird die Zielprognose sichtbar."
    if goal_target > 0 and goal_pct >= 100:
        goal_lever_text = "Lege dein nächstes Ziel bewusst fest."
        goal_lever_subtext = "Dein bisheriger Zieltopf ist erreicht und bleibt klar vom Gesamtvermögen getrennt."
    elif tracked_days < 3:
        goal_lever_text = "Tracke noch ein paar Tage weiter, dann wird dein persönlicher Hebel belastbar."
        goal_lever_subtext = "Noch zu früh für eine belastbare Kategorie-Empfehlung."
    elif goal_monthly_rate and months_to_goal:
        goal_lever_text = f"Bei gleichbleibender Rate von {money_text(goal_monthly_rate)}/Monat bleibt diese Zielrechnung nachvollziehbar."
        goal_lever_subtext = "Die Rechnung nutzt ausschließlich deine ausdrücklich hinterlegte Zielrate."
    else:
        goal_lever_text = "Sobald deine Sparrate steht, wird dein persönlicher Hebel sichtbar."
        goal_lever_subtext = "Mit mehr Daten wird der nächste sinnvolle Schritt sichtbar."

    if goal_target <= 0:
        goal_headline = "Dein nächstes Ziel bekommt jetzt einen klaren Platz."
        goal_context_text = "Lege ein Ziel an, damit Rov.E deinen Fortschritt getrennt vom Gesamtvermögen zeigen kann."
    elif goal_pct >= 100:
        goal_headline = f"Dein {h(goal_desc)} ist erreicht."
        goal_context_text = "Der Zieltopf ist vollständig gefüllt. Zeit, den nächsten Schritt bewusst zu planen."
    elif goal_current > 0:
        goal_headline = f"Dein {h(goal_desc)} rückt mit jedem Beitrag näher."
        goal_context_text = "Jeder Betrag im Zieltopf verkürzt die Strecke sichtbar."
    else:
        goal_headline = f"Dein {h(goal_desc)} hat jetzt einen klaren Startpunkt."
        goal_context_text = "Der erste Betrag im Zieltopf macht deinen Fortschritt sichtbar."

    # Zielzeit und Zielhebel bleiben absichtlich deterministisch. Diese Aussagen
    # hängen unmittelbar am Zieltopf und dürfen nicht von freiem KI-Text
    # überschrieben werden.

    mband = milestone_band(net_worth)
    milestone_remaining = max(mband["to_amount"] - net_worth, 0)
    milestone_headline = f"Noch {money_text(milestone_remaining)} bis zum nächsten Meilenstein."
    if savings_plan > 0:
        milestone_months = max(1, int(-(-milestone_remaining // savings_plan))) if milestone_remaining > 0 else 0
        milestone_eta_text = (
            f"Bei {money_text(savings_plan)}/Monat erreichst du den nächsten Meilenstein in "
            f"rund {milestone_months} {'Monat' if milestone_months == 1 else 'Monaten'}."
        )
    else:
        milestone_months = None
        milestone_eta_text = "Sobald deine Sparrate steht, wird deine Meilenstein-Prognose sichtbar."

    badges_raw = milestones.get("badges") or []
    badges = []
    for badge in badges_raw[:3]:
        earned_at = badge.get("earned_at") or ""
        date_text = earned_at
        try:
            date_text = datetime.fromisoformat(str(earned_at)).strftime("%d.%m.%Y")
        except Exception:
            pass
        badges.append({"label": h(badge.get("label") or badge.get("key") or ""), "date": h(date_text)})

    plan_step2_target_amount = max(50, round(strongest_amount * 0.8 / 50) * 50) if strongest_amount else 100
    plan_step3_target_amount = max(50, savings_plan or 50)

    context = {
        "report": build_story_render_context(data),
        "month_label": h(month_label),
        "next_label": h(next_label),
        "next_month_name": h(next_month_name),
        "next_report_delivery_month_name": h(next_report_delivery_month_name),
        "month_short": h(month_short),
        "freedom_step_label": freedom_step_label,
        "freedom_step_text": f"+{money_text(actual_savings)}" if actual_savings > 0 else "offen",
        "freedom_step_subline": freedom_step_subline,
        "development_percent_text": (
            fmt_percent(cover.get("development_percent"), 1)
            if cover.get("development_percent") is not None else "ab Monat 2"
        ),
        "net_worth_span": data_count_span(net_worth),
        "investments_span": data_count_span(investments),
        "cash_span": data_count_span(cash),
        "biggest_amount_span": data_count_span(biggest_amount),
        "biggest_name": h(biggest_name),
        "strongest_amount_span": data_count_span(strongest_amount),
        "strongest_name": h(strongest_name),
        "tracked_days": tracked_days,
        "invested_amount": money_text(invested_amount_raw),
        "month_savings_sentence": h(month_savings_sentence),
        "wealth_headline": h(wealth_copy["headline"]),
        "wealth_sentence": h(wealth_copy["sentence"]),
        "strongest_amount": money_text(strongest_amount),
        "ratio_sentence": h(
            f"Für jeden gesparten Euro sind {round(ratio_to_savings * 100)} Cent in {strongest_name} geflossen."
            if invested_amount_raw > 0 and strongest_amount > 0
            else "Deine ersten Muster werden mit jedem Tracking-Tag klarer."
        ),
        "invested_span": data_count_span(invested_amount_raw),
        "ratio_span": data_count_span(ratio_to_savings, 2),
        "halve_sentence": h(
            f"Das ist keine Verzichtsübung — nur Bewusstsein. Würdest du diese Kategorie auf "
            f"~{money_text(half_target)} halbieren, blieben jeden Monat rund {money_text(freed_up_monthly)} mehr übrig."
            if strongest_amount > 0 else
            "Das ist keine Verzichtsübung — nur Bewusstsein für dein Verhalten."
        ),
        "yearly_span": data_count_span(freed_up_yearly),
        "goal_desc": h(goal_desc),
        "goal_headline": goal_headline,
        "goal_context_text": goal_context_text,
        "investments_pct_raw": investments_pct,
        "investments_pct_text": fmt_percent(investments_pct, 1),
        "cash_pct_text": fmt_percent(cash_pct, 1),
        "cash_pct_raw": cash_pct,
        "property_pct_raw": property_pct,
        "property_pct_text": fmt_percent(property_pct, 1),
        "has_property_equity": property_equity > 0,
        "investments_amount": money_text(investments),
        "cash_amount": money_text(cash),
        "property_equity_amount": money_text(property_equity),
        "invest_story_headline": invest_story_headline,
        "invest_story_sub": invest_story_sub,
        "money_map_categories": money_map_categories,
        "has_budget_status": has_budget_status,
        "budget_status_title": budget_status_title,
        "budget_status_text": h(budget_status_text),
        "budget_status_subtext": h(budget_status_subtext),
        "biggest_amount": money_text(biggest_amount),
        "biggest_share_pct": biggest_share_pct,
        "invest_vs_strongest_text": invest_vs_strongest_text,
        "score_value": score_value,
        "score_span": data_count_span(score_value),
        "score_headline_suffix": score_headline_suffix,
        "score_dash": score_dash,
        "rank_name": h(rank_name),
        "prev_rank_name": h(band["prev_name"]) if band["prev_name"] else "",
        "next_rank_name": h(band["next_name"]) if band["next_name"] else "",
        "rank_band_low": rank_band_low,
        "rank_band_high": rank_band_high,
        "rank_band_text": f"{band['low']}–{band['high']}",
        "score_parts": score_parts,
        "rank_blurb": rank_blurb,
        "next_step_headline": next_step_headline,
        "next_step_sub": next_step_sub,
        "goal_pct_span": data_count_span(goal_pct, 1),
        "goal_pct_raw": goal_pct,
        "goal_target_amount": money_text(goal_target),
        "goal_current_amount": money_text(goal_current),
        "net_worth_amount": money_text(net_worth),
        "goal_remaining_amount": money_text(goal_gap),
        "goal_honest_text": h(goal_honest_text),
        "goal_lever_text": h(goal_lever_text),
        "goal_lever_subtext": goal_lever_subtext,
        "milestone_headline": milestone_headline,
        "milestone_from": money_text(mband["from_amount"]),
        "milestone_to": money_text(mband["to_amount"]),
        "milestone_pct_text": int(round(mband["pct"])),
        "milestone_pct_raw": round(mband["pct"], 1),
        "milestone_eta_text": milestone_eta_text,
        "badges": badges,
        "recap_good_text": h(humanize_text(recap.get("what_went_well") or "")),
        "recap_attention_text": h(humanize_text(recap.get("needs_attention") or "")),
        "recap_lever_text": h(humanize_text(recap.get("next_lever") or "")),
        "plan_step1_sub": f"Das allein hebt deinen Rov.E Score über {band['high'] + 1} — in den {h(band['next_name'] or rank_name)}-Status.",
        "plan_step1_impact": f"Score {band['high'] + 1}+",
        "plan_step2_target": money_text(plan_step2_target_amount),
        "plan_step2_sub": f"Rund {money_text(freed_up_monthly)} mehr pro Monat fürs {h(goal_desc)} — ohne auf alles zu verzichten.",
        "plan_step2_impact": f"+{money_text(freed_up_yearly)}/Jahr",
        "plan_step3_target": money_text(plan_step3_target_amount),
        "plan_step3_impact": (
            f"Meilenstein: {milestone_months} {'Monat' if milestone_months == 1 else 'Monate'}"
            if milestone_months else "Meilenstein-Boost"
        ),
    }

    # Pre-V2 snapshots keep their original frozen wording; these aliases only
    # satisfy the shared template and never consult live financial state.
    context.update({
        "development_subline": "zum Vormonat",
        "month_summary_text": h(
            f"Du hast an {tracked_days} Tagen aktiv getrackt. "
            f"{month_savings_sentence} Deine stärkste Kategorie war {strongest_name} mit {money_text(strongest_amount)}."
        ),
        "comparison_rows": [],
        "comparison_text": "Für diesen Altbericht ist kein V2-Vormonatsvergleich eingefroren.",
        "build_summary_text": h(month_savings_sentence),
        "goal_title_text": h(goal_headline),
        "goal_honest_subtext": "Diese Aussage stammt aus dem ursprünglichen Monatsabschluss.",
        "milestone_fact_text": h(milestone_eta_text),
        "plan_step1_title": h(next_step_headline),
        "plan_step2_title": h(f"Behalte {strongest_name} im Blick."),
        "plan_step3_title": h(f"Halte deine geplante Sparrate von {money_text(plan_step3_target_amount)} ein."),
        "plan_step3_sub": "Dieser Betrag stammt aus dem ursprünglichen Monatsplan.",
    })

    return context


def render_template(template: str, data: dict) -> str:
    context = build_render_context(data)
    html_doc = strip_dc_runtime(template)
    html_doc = inject_report_css(html_doc)
    return jinja2.Template(html_doc).render(**context)


def build_web_report(user_id: int, report_month: str, report_data: dict | None = None) -> dict:
    ensure_report_links_table()
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Rov.E Web-Report-Template fehlt: {TEMPLATE_PATH}")

    report_data = report_data or build_report_data(user_id, report_month)
    token = secrets.token_urlsafe(18)
    expires_at = datetime.now() + timedelta(days=REPORT_LINK_TTL_DAYS)
    output_dir = PUBLIC_REPORT_DIR / token
    output_dir.mkdir(parents=True, exist_ok=True)
    # Nginx needs traverse permission for the opaque, public report URL.
    output_dir.chmod(0o755)
    output_path = output_dir / "index.html"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    doc = render_template(template, report_data)
    doc = inject_expiry_meta(doc, expires_at)
    output_path.write_text(doc, encoding="utf-8")
    output_path.chmod(0o644)

    public_url = f"{PUBLIC_REPORT_BASE_URL}/{token}/" if PUBLIC_REPORT_BASE_URL else ""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE report_links
                  SET status = 'superseded'
                WHERE user_id = ? AND report_month = ? AND status = 'active'""",
            (user_id, report_month),
        )
        conn.execute(
            """INSERT INTO report_links
               (token, user_id, report_month, html_path, public_url, expires_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (
                token,
                user_id,
                report_month,
                str(output_path),
                public_url,
                expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()

    return {
        "token": token,
        "path": output_path,
        "url": public_url,
        "expires_at": expires_at,
    }


def build_pdf_report(user_id: int, report_month: str, output_path: Path, report_data: dict | None = None) -> Path:
    """Render the Rov.E web design into a PDF archive copy."""
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Rov.E Web-Report-Template fehlt: {TEMPLATE_PATH}")

    try:
        from weasyprint import HTML
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "WeasyPrint fehlt. Installiere es mit: python3 -m pip install weasyprint"
        ) from exc

    report_data = report_data or build_report_data(user_id, report_month)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    doc = render_template(template, report_data)
    doc = build_static_pdf_html(doc)

    with TemporaryDirectory() as tmp_dir:
        html_path = Path(tmp_dir) / "rove_report.html"
        html_path.write_text(doc, encoding="utf-8")
        HTML(filename=str(html_path), base_url=str(TEMPLATE_PATH.parent)).write_pdf(str(output_path))

    return output_path


def cleanup_expired_reports(now: datetime | None = None) -> int:
    ensure_report_links_table()
    now = now or datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    removed = 0
    public_dir = PUBLIC_REPORT_DIR.resolve(strict=False)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT token, html_path FROM report_links
                 WHERE status IN ('active', 'superseded') AND expires_at <= ?""",
            (now_str,),
        ).fetchall()
        for row in rows:
            try:
                report_dir = Path(row["html_path"]).resolve(strict=False).parent
                if report_dir.parent != public_dir:
                    logger.warning("Webreport cleanup deferred: unexpected generated-report path")
                    continue
                if report_dir.exists():
                    shutil.rmtree(report_dir)
            except OSError as exc:
                # Keep the link eligible for the next maintenance run until its files are gone.
                logger.warning("Webreport cleanup deferred after %s", type(exc).__name__)
                continue
            conn.execute(
                "UPDATE report_links SET status = 'expired' WHERE token = ?",
                (row["token"],),
            )
            removed += 1
        conn.commit()
    return removed


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python3 rove_web_report_renderer.py <user_id> <YYYY-MM>")
        raise SystemExit(1)
    result = build_web_report(int(sys.argv[1]), sys.argv[2])
    print(result["url"] or result["path"])
