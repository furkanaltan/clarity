from __future__ import annotations

import html
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from report_engine import build_report_data, format_month_duration
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
        if (el.hasAttribute('data-count') && !el._done) {
          el._done = true;
          runCount(el);
        }
        io.unobserve(el);
      });
    }, { threshold: 0.2 });

    reveals.concat(bars, rings, counts).forEach(el => io.observe(el));

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

    cover = pages["cover"]
    story = pages["financial_story"]
    month = pages["month"]
    score = pages["score"]
    goal = pages["goal"]
    money_map = pages["money_map"]
    milestones = pages["milestones"]
    recap = pages["recap"]
    wealth = pages["wealth_journey"]

    strongest = month.get("strongest_category") or {"category": "Noch offen", "total": 0}
    biggest = month.get("biggest_expense") or {"merchant": "Noch offen", "amount": 0}
    strongest_name = category_label(strongest.get("category"))
    biggest_name = biggest.get("merchant") or "Noch offen"
    score_value = int(score.get("clarity_score") or 0)
    score_dash = round(540.4 * (100 - max(0, min(100, score_value))) / 100, 1)
    goal_desc = goal.get("description") or "Dein Ziel"
    months_to_goal = goal.get("months_to_goal")
    goal_duration = format_month_duration(months_to_goal) if months_to_goal else "noch nicht berechenbar"
    savings_plan = profile.get("savings_plan") or 0
    goal_gap = max((goal.get("target_amount") or 0) - (profile.get("net_worth") or 0), 0)

    net_worth = story.get("net_worth") or profile.get("net_worth") or 0
    investments = story.get("investments") or profile.get("current_investments") or 0
    cash = story.get("cash") or profile.get("cash_reserve") or 0
    total_expenses = month.get("total_expenses") or 0
    recurring_in = wealth.get("investment_summary", {}).get("recurring_in", 0)
    one_time_in = wealth.get("investment_summary", {}).get("one_time_in", 0)
    investment_total = wealth.get("investment_summary", {}).get("net_contributions", 0)

    story_text = humanize_text(story.get("text") or "")
    if not story_text:
        story_text = "Dieser Report baut deine erste echte Datenbasis auf."

    insight_line = (
        f"Dein größter Hebel liegt diesen Monat bei {strongest_name}."
        if strongest.get("total", 0) else
        "Deine ersten Muster werden mit jedem Tracking-Tag klarer."
    )
    insight_headline = h(insight_line)
    if strongest.get("total", 0):
        insight_headline = insight_headline.replace(
            h(strongest_name),
            f'<span style="font-style: italic; color: #3BA7FF;">{h(strongest_name)}</span>',
            1,
        )
    insight_subline = (
        f"{strongest_name} liegt aktuell bei {money_text(strongest.get('total', 0))}."
        if strongest.get("total", 0) else
        "Noch ist es zu früh für ein finales Urteil."
    )

    html_doc = template
    html_doc = strip_dc_runtime(html_doc)
    html_doc = replace_all(html_doc, {
        "Mai 2026": h(month_label),
        "MAI": h(month_label.split(" ", 1)[0].upper()),
        "2026": h(month_label.split(" ", 1)[1]),
        "Juli 2026": h(next_label),
        "JULI": h(next_month_name.upper()),
        "Plan für den nächsten Monat · Juli 2026": f"Plan für den nächsten Monat · {h(next_label)}",
        "Dein Plan für Juli.": f"Dein Plan für {h(next_month_name)}.",
        "+420 €": money_text(cover.get("freedom_step") or 0),
        "+3,4 %": fmt_percent(cover.get("development_percent"), 1) if cover.get("development_percent") is not None else "ab Monat 2",
        "15.450": de_number(net_worth),
        "9.350": de_number(investments),
        "6.100": de_number(cash),
        "207": de_number(biggest.get("amount", 0)),
        "McDonald’s": h(biggest_name),
        "257": de_number(strongest.get("total", 0)),
        "Restaurants": h(strongest_name),
        "2 Tagen": f"{meta.get('tracked_days', 0)} Tagen",
        "2 Tage": f"{meta.get('tracked_days', 0)} Tage",
        "750 €": money_text(investment_total or savings_plan),
        "58": str(score_value),
        "Controller": h(score.get("rank_name") or "Rookie"),
        "Haus": h(goal_desc),
        "450.000 €": money_text(goal.get("target_amount") or 0),
        "434.550 €": money_text(goal_gap),
        "3,4 %": fmt_percent(goal.get("progress_percent") or 0, 1),
        "580 Monate": h(goal_duration),
        "48 Jahre und 4 Monate": h(goal_duration),
        "Bei 750 €/Monat liegt dein Haus noch rund 48 Jahre und 4 Monate entfernt.": (
            f"Bei {money_text(savings_plan)}/Monat liegt dein {h(goal_desc)} noch rund {h(goal_duration)} entfernt."
            if savings_plan > 0 and months_to_goal else
            "Sobald deine Sparrate sauber steht, wird die Zielprognose sichtbar."
        ),
        "Dein größter Hebel liegt diesen Monat bei <span style=\"font-style: italic; color: #3BA7FF;\">Restaurants</span>.": (
            insight_headline
        ),
        "Für jeden gesparten Euro sind 34 Cent in Restaurants geflossen.": h(insight_subline),
    })

    html_doc = re.sub(
        r'<span data-count="15450">[^<]+</span>',
        data_count_span(net_worth),
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'<span data-count="9350">[^<]+</span>',
        data_count_span(investments),
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'<span data-count="6100">[^<]+</span>',
        data_count_span(cash),
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'<span data-count="207">[^<]+</span>',
        data_count_span(biggest.get("amount", 0)),
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'<span data-count="257">[^<]+</span>',
        data_count_span(strongest.get("total", 0)),
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'<span data-count="58">[^<]+</span>',
        data_count_span(score_value),
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'data-ring="226\.9"',
        f'data-ring="{score_dash}"',
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'stroke-dashoffset="226\.9"',
        f'stroke-dashoffset="{score_dash}"',
        html_doc,
        count=1,
    )
    html_doc = re.sub(
        r'<div style="font-size: 10\.5px; font-weight: 600; letter-spacing: 0\.18em; text-transform: uppercase; color: #9EA4A0;">Monatsfazit</div>\s*<div[\s\S]*?</div>',
        (
            '<div style="font-size: 10.5px; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase; color: #9EA4A0;">Monatsfazit</div>\n'
            f'        <div style="font-family: \'Newsreader\', serif; font-size: 26px; line-height: 1.55; margin-top: 14px; color: #F4F1EA;">{h(story_text)}</div>'
        ),
        html_doc,
        count=1,
    )

    return html_doc


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
