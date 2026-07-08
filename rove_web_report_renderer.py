from __future__ import annotations

import html
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


load_dotenv()

APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME

TEMPLATE_PATH = Path(
    os.getenv("ROVE_WEB_TEMPLATE_PATH", str(APP_DIR / "report_templates" / "rove_web_report.html"))
)
PUBLIC_REPORT_DIR = Path(
    os.getenv("ROVE_REPORT_PUBLIC_DIR", str(APP_DIR / "public" / "reports"))
)
PUBLIC_REPORT_BASE_URL = os.getenv("ROVE_REPORT_PUBLIC_BASE_URL", "").rstrip("/")
REPORT_LINK_TTL_DAYS = int(os.getenv("ROVE_REPORT_LINK_TTL_DAYS", "30"))


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
      [data-screen-label="06 Rov.E Score"] * { box-sizing: border-box; }
      [data-screen-label="06 Rov.E Score"] div,
      [data-screen-label="07 Ziel"] div { overflow-wrap: anywhere; }
      @media (max-width: 760px) {
        [data-screen-label="06 Rov.E Score"] {
          padding-left: 20px !important;
          padding-right: 20px !important;
        }
        [data-screen-label="06 Rov.E Score"] [style*="minmax(320px"] {
          grid-template-columns: minmax(0, 1fr) !important;
        }
        [data-screen-label="06 Rov.E Score"] [style*="padding: 38px 42px"] {
          padding: 28px 24px !important;
        }
        [data-screen-label="07 Ziel"] [style*="padding: 52px 54px"] {
          padding: 36px 28px !important;
        }
        [data-screen-label="07 Ziel"] [style*="font-size: 24px"] {
          font-size: 21px !important;
          line-height: 1.45 !important;
        }
      }
      @media print {
        body { background: #08090B !important; }
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
      [data-screen-label="10 Plan für Juli"] > div {
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
      [data-screen-label="10 Plan für Juli"] {
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
  const ease = 'cubic-bezier(0.2, 0.7, 0.2, 1)';
  const fmt = (v, d) => new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: d,
    maximumFractionDigits: d
  }).format(v);

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
      el.style.strokeDasharray = '540.4';
      el.style.strokeDashoffset = '540.4';
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
        if (el.hasAttribute('data-ring')) {
          el.style.strokeDashoffset = el._target;
          el.setAttribute('stroke-dashoffset', el._target);
        }
        if (el.hasAttribute('data-count') && !el._done) {
          el._done = true;
          runCount(el);
        }
        io.unobserve(el);
      });
    }, { threshold: 0.2 });

    reveals.concat(bars, rings, counts).forEach(el => io.observe(el));
    window.setTimeout(() => {
      rings.forEach(el => {
        if (el._target) {
          el.style.strokeDashoffset = el._target;
          el.setAttribute('stroke-dashoffset', el._target);
        }
      });
    }, 1200);

    const assistant = document.querySelector('[data-rove-assistant]');
    const planSection = document.querySelector('[data-screen-label="10 Plan für Juli"]');
    if (assistant && planSection) {
      const assistantIo = new IntersectionObserver((entries) => {
        entries.forEach(entry => assistant.classList.toggle('is-visible', entry.isIntersecting));
      }, { threshold: 0.36 });
      assistantIo.observe(planSection);
    }
  });
})();
</script>
""".strip()


def month_names(report_month: str) -> tuple[str, str]:
    year, month = map(int, report_month.split("-"))
    names = {
        1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
        7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
    }
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year
    return f"{names[month]} {year}", f"{names[next_month]} {next_year}"


def render_template(template: str, data: dict) -> str:
    meta = data["meta"]
    profile = data["profile"]
    pages = data["pages"]
    month_label, next_label = month_names(meta["report_month"])
    next_month_name = next_label.split(" ", 1)[0]
    month_short = month_label.split(" ", 1)[0]

    cover = pages["cover"]
    story = pages["financial_story"]
    month = pages["month"]
    score = pages["score"]
    goal = pages["goal"]
    money_map = pages["money_map"]
    milestones = pages["milestones"]
    recap = pages["recap"]

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
    goal_pct = round(min(100.0, goal.get("progress_percent") or 0), 1)

    net_worth = story.get("net_worth") or profile.get("net_worth") or 0
    investments = story.get("investments") or profile.get("current_investments") or 0
    cash = story.get("cash") or profile.get("cash_reserve") or 0
    total_expenses = month.get("total_expenses") or 0
    invest_total = investments + cash
    investments_pct = round((investments / invest_total * 100) if invest_total > 0 else 0, 1)
    cash_pct = round(100 - investments_pct, 1) if invest_total > 0 else 0

    investment_summary = data["pages"]["wealth_journey"].get("investment_summary", {})
    investment_total = investment_summary.get("net_contributions", 0)
    invested_amount_raw = investment_total or savings_plan

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
    biggest_share_pct = int(round((biggest_amount / total_expenses * 100) if total_expenses else 0))

    if invested_amount_raw >= strongest_amount * 2 and strongest_amount > 0:
        invest_vs_strongest_text = f"Mehr als doppelt so viel, wie du in {h(strongest_name)} ausgegeben hast."
    elif invested_amount_raw > strongest_amount:
        invest_vs_strongest_text = f"Mehr, als du in {h(strongest_name)} ausgegeben hast."
    else:
        invest_vs_strongest_text = f"Ein wichtiger Baustein neben deinen Ausgaben in {h(strongest_name)}."

    if investments_pct >= 50:
        invest_story_headline = f"Mit {fmt_percent(investments_pct, 1)} investiertem Kapital bist du keiner, der nur spart — du baust auf."
    elif investments_pct > 0:
        invest_story_headline = f"Mit {fmt_percent(investments_pct, 1)} investiertem Kapital hast du einen soliden Grundstein gelegt."
    else:
        invest_story_headline = "Dein Vermögen liegt aktuell als Cash bereit — der nächste Schritt ist, es arbeiten zu lassen."
    invest_story_sub = (
        f"Deine {money_text(cash)} Liquidität decken Unerwartetes, ohne dein Wachstum zu bremsen. "
        f"Dieser Monat ist dein Startpunkt — ab {h(next_month_name)} wird die Entwicklung sichtbar."
    )

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

    goal_gap = max(goal_target - net_worth, 0)
    if savings_plan > 0 and months_to_goal:
        goal_honest_text = f"Bei {money_text(savings_plan)}/Monat liegt dein {h(goal_desc)} noch rund {h(goal_duration)} entfernt."
    else:
        goal_honest_text = "Sobald deine Sparrate sauber steht, wird die Zielprognose sichtbar."
    if freed_up_monthly > 0 and savings_plan > 0 and months_to_goal:
        boosted_months = calculate_goal_projection(goal_target, net_worth, savings_plan + freed_up_monthly)
        years_saved = None
        if boosted_months is not None and months_to_goal:
            years_saved = round(max(0, months_to_goal - boosted_months) / 12, 1)
        if years_saved and years_saved >= 0.5:
            goal_lever_text = f"Schon +{money_text(freed_up_monthly)}/Monat bringt dich rund {de_number(years_saved, 1)} Jahre früher ans Ziel."
        else:
            goal_lever_text = f"Schon +{money_text(freed_up_monthly)}/Monat bringt dich spürbar früher ans Ziel."
    else:
        goal_lever_text = "Sobald deine Sparrate steht, wird dein persönlicher Hebel sichtbar."

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
        "month_label": h(month_label),
        "next_label": h(next_label),
        "next_month_name": h(next_month_name),
        "month_short": h(month_short),
        "freedom_step_text": money_text(cover.get("freedom_step") or 0, 0) if (cover.get("freedom_step") or 0) < 0 else f"+{money_text(cover.get('freedom_step') or 0)}",
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
        "tracked_days": meta.get("tracked_days", 0),
        "invested_amount": money_text(invested_amount_raw),
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
        "investments_pct_raw": investments_pct,
        "investments_pct_text": fmt_percent(investments_pct, 1),
        "cash_pct_text": fmt_percent(cash_pct, 1),
        "investments_amount": money_text(investments),
        "cash_amount": money_text(cash),
        "invest_story_headline": invest_story_headline,
        "invest_story_sub": invest_story_sub,
        "money_map_categories": money_map_categories,
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
        "net_worth_amount": money_text(net_worth),
        "goal_remaining_amount": money_text(goal_gap),
        "goal_honest_text": goal_honest_text,
        "goal_lever_text": goal_lever_text,
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
            f"Meilenstein in {milestone_months} Mt." if milestone_months else "Meilenstein-Boost"
        ),
    }

    html_doc = template
    html_doc = strip_dc_runtime(html_doc)
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
    output_path = output_dir / "index.html"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    doc = render_template(template, report_data)
    doc = inject_expiry_meta(doc, expires_at)
    output_path.write_text(doc, encoding="utf-8")

    public_url = f"{PUBLIC_REPORT_BASE_URL}/{token}/" if PUBLIC_REPORT_BASE_URL else ""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT token, html_path FROM report_links WHERE status = 'active' AND expires_at <= ?",
            (now_str,),
        ).fetchall()
        for row in rows:
            html_path = Path(row["html_path"])
            report_dir = html_path.parent
            try:
                if report_dir.exists() and report_dir.parent == PUBLIC_REPORT_DIR:
                    shutil.rmtree(report_dir)
                    removed += 1
            except Exception:
                pass
            conn.execute(
                "UPDATE report_links SET status = 'expired' WHERE token = ?",
                (row["token"],),
            )
        conn.commit()
    return removed


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python3 rove_web_report_renderer.py <user_id> <YYYY-MM>")
        raise SystemExit(1)
    result = build_web_report(int(sys.argv[1]), sys.argv[2])
    print(result["url"] or result["path"])
