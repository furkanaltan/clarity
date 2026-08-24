"""
Rov.E App API v1

Kleine, getrennte Schreibschicht fuer die Web-App. Der Telegram-Bot bleibt unveraendert
und laeuft weiter. Authentifizierung erfolgt ueber den privaten /app-State-Token aus
app_state_links. v1 schreibt bewusst nur Ausgaben in die bestehende Bot-Datenbank.
"""

import base64
import csv
import json
import gzip
import hashlib
import hmac
import io
import os
import re
import secrets
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, jsonify, make_response, request, send_file
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
import rove_account_delete_cleanup as account_delete_cleanup
from rove_app_state import (
    ACCOUNT_META,
    ASSET_ORDER_KEYS,
    PUBLIC_APP_STATE_DIR,
    REPORTS_ARCHIVE_DIR,
    REPORTS_DIR,
    _build_tx,
    build_live_app_data,
    ensure_app_account_balances_table,
    ensure_app_asset_order_table,
    ensure_app_cash_movements_table,
    ensure_app_contracts_table,
    sync_contract_fixed_costs,
    ensure_app_etf_position_plans_table,
    ensure_app_etf_savings_plan_table,
    ensure_app_goals_table,
    get_app_etf_savings_plan,
    ensure_app_monthly_plan_table,
    ensure_app_scheduled_savings_table,
    apply_due_scheduled_savings,
    get_app_scheduled_savings,
    ensure_app_primary_goal_progress_table,
    ensure_app_properties_table,
)
from rove_score import award_tracking_points, reverse_tracking_points_for_deleted_expense
from rove_market_data import (
    apply_market_quote,
    ensure_market_tracking_schema,
    fetch_eur_quote,
    normalize_currency,
    normalize_symbol,
)
from rove_investment_contributions import (
    ensure_investment_contribution_schema,
    record_holding_contribution,
)
from rove_expense_domain import (
    begin_expense_write,
    create_expense_for_user,
)
from rove_financial_accounts import (
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    archive_financial_account,
    adjust_financial_account_balance,
    apply_financial_account_deltas,
    create_financial_account,
    delete_financial_account_data,
    ensure_financial_account_reference_schema,
    get_legacy_financial_account,
    is_feature_enabled,
    list_financial_accounts,
    rename_financial_account,
    require_account_role,
    require_financial_account,
    set_account_role,
    set_legacy_financial_account_balance,
    transfer_financial_account_balance,
    update_financial_account_balance,
)


APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME
PUBLIC_REPORT_DIR = Path(
    os.getenv("ROVE_REPORT_PUBLIC_DIR", str(APP_DIR / "public" / "reports"))
)

_configured_origins = os.getenv("ROVE_APP_ALLOWED_ORIGINS")
if not _configured_origins:
    _configured_origins = f"{os.getenv('ROVE_APP_ALLOWED_ORIGIN', 'https://getrove.de')},https://www.getrove.de"
ALLOWED_ORIGINS = frozenset(
    origin.strip().rstrip("/")
    for origin in _configured_origins.split(",")
    if origin.strip()
)
PUBLIC_APP_STATE_BASE_URL = os.getenv("ROVE_APP_STATE_PUBLIC_BASE_URL", "").rstrip("/")
PAIR_ATTEMPT_WINDOW_SECONDS = 5 * 60
PAIR_ATTEMPT_LIMIT = 8
_pair_attempts: dict[str, list[float]] = {}
AUTH_CODE_TTL_MINUTES = int(os.getenv("ROVE_APP_AUTH_CODE_TTL_MINUTES", "10"))
ACCOUNT_DELETE_CODE_TTL_MINUTES = int(os.getenv("ROVE_ACCOUNT_DELETE_CODE_TTL_MINUTES", "10"))
AUTH_SESSION_TTL_DAYS = int(os.getenv("ROVE_APP_AUTH_SESSION_TTL_DAYS", "180"))
AUTH_ATTEMPT_WINDOW_SECONDS = 15 * 60
AUTH_ATTEMPT_LIMIT = 5
AUTH_PASSWORD_MAX_LENGTH = 1024
AUTH_PASSWORD_MIN_LENGTH = 10
AUTH_RESET_TTL_MINUTES = 10
AUTH_BUCKETS: dict[str, dict[str, list[float]]] = {}
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16, type=Type.ID)
APP_REGISTRATION_FLOW = "app_registration"
APP_USER_ID_BASE = 8_000_000_000_000_000
SCREENSHOT_ATTEMPT_WINDOW_SECONDS = 24 * 60 * 60
SCREENSHOT_ATTEMPT_LIMIT = int(os.getenv("ROVE_SCREENSHOT_DAILY_LIMIT", "10"))
_screenshot_attempts: dict[int, list[float]] = {}
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
AUTH_SECRET = os.getenv("ROVE_APP_AUTH_SECRET", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
SCREENSHOT_MODEL = os.getenv("ROVE_SCREENSHOT_MODEL", "gpt-4o-mini").strip()
SCREENSHOT_MAX_BYTES = int(os.getenv("ROVE_SCREENSHOT_MAX_BYTES", str(5 * 1024 * 1024)))
SCREENSHOT_MAX_ROWS = int(os.getenv("ROVE_SCREENSHOT_MAX_ROWS", "20"))
ADMIN_USER_IDS = frozenset(
    int(value.strip())
    for value in os.getenv("ROVE_ADMIN_USER_IDS", "").split(",")
    if value.strip().isdigit()
)
# Nur fuer interne Server-zu-Server-Hinweise, niemals an den Browser ausliefern.
INTERNAL_PUSH_SECRET = os.getenv("ROVE_INTERNAL_PUSH_SECRET", "").strip()
LOGIN_FROM_EMAIL = os.getenv("ROVE_LOGIN_FROM_EMAIL", "info@getrove.de").strip()
LOGIN_FROM_NAME = os.getenv("ROVE_LOGIN_FROM_NAME", "Rov.E").strip()
SESSION_COOKIE_NAME = os.getenv("ROVE_APP_SESSION_COOKIE", "rove_app_session")
SESSION_COOKIE_SECURE = os.getenv("ROVE_APP_COOKIE_SECURE", "1") != "0"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

APP_TO_BOT_CATEGORY = {
    "Lebensmittel": "LEBENSMITTEL",
    "Mobilität": "MOBILITAET",
    "Restaurant": "RESTAURANTS",
    "Restaurants": "RESTAURANTS",
    "Abos": "ABOS",
    "Shopping": "SHOPPING",
    "Freizeit": "FREIZEIT",
    "Drogerie": "DROGERIE",
    "Gesundheit": "GESUNDHEIT",
    "Pflege": "PFLEGE",
    "Sonstiges": "SONSTIGES",
}
BOT_TO_APP_CATEGORY = {
    "LEBENSMITTEL": "Lebensmittel",
    "MOBILITAET": "Mobilität",
    "RESTAURANTS": "Restaurant",
    "ABOS": "Abos",
    "SHOPPING": "Shopping",
    "FREIZEIT": "Freizeit",
    "DROGERIE": "Drogerie",
    "GESUNDHEIT": "Gesundheit",
    "PFLEGE": "Pflege",
    "SONSTIGES": "Sonstiges",
}
ACCOUNT_KEYS = frozenset(ACCOUNT_META)

# Bewusste Positivliste fuer den Nutzerexport. Interne Zugangsdaten wie Login-Codes,
# Session-Hashes, State-Tokens und Push-Schluessel gehoeren weder in einen Finanzexport
# noch auf das Endgeraet des Nutzers.
DATA_EXPORT_TABLES = (
    ("profil", "users"),
    ("buchungen", "expenses"),
    ("budgets", "category_budgets"),
    ("kategorie_regeln", "user_category_rules"),
    ("kontostaende", "app_account_balances"),
    ("kontobewegungen", "app_cash_movements"),
    ("financial_accounts", "app_financial_accounts"),
    ("financial_account_roles", "app_financial_account_roles"),
    ("vertraege", "app_contracts"),
    ("ziele", "app_goals"),
    ("hauptziel_fortschritt", "app_primary_goal_progress"),
    ("immobilien", "app_properties"),
    ("investments", "portfolio_holdings"),
    ("investment_bewegungen", "investment_events"),
    ("portfolio_verlauf", "portfolio_snapshots"),
    ("monatsplaene", "app_monthly_plan_status"),
    ("geplante_sparraten", "app_scheduled_savings"),
    ("etf_sparplan", "app_etf_savings_plan"),
    ("etf_positionsplaene", "app_etf_position_plans"),
    ("score_verlauf", "score_history"),
    ("rove_points", "rove_point_events"),
    ("badges", "user_badges"),
    ("monatssnapshots", "monthly_snapshots"),
    ("reports", "report_jobs"),
    ("zugangsstatus", "user_access"),
    ("push_einstellungen", "app_push_preferences"),
    ("push_zustellungen", "app_push_delivery_log"),
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SCREENSHOT_MAX_BYTES + 512 * 1024


@contextmanager
def db():
    # Wartezeit statt Sofortabbruch: seit begin_write() die Schreibsperre vorzieht,
    # treffen parallele Buchungen aufeinander. Der zweite Request soll kurz warten
    # und dann den bereits gesenkten Stand lesen, nicht mit "database is locked"
    # abbrechen. 15 s ist grosszuegig — ein Endpunkt haelt die Sperre wenige ms.
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # sqlite3.Connection.__exit__ commits or rolls back but does not close the
    # file handle. Closing explicitly prevents one leaked connection per API call.
    with closing(conn):
        with conn:
            yield conn


def begin_write(conn: sqlite3.Connection) -> None:
    """Nimmt die Schreibsperre SOFORT statt erst beim ersten UPDATE.

    Fund 4 aus dem Kontostand-Audit vom 26.07.: Python oeffnet die Transaktion
    erst beim ersten schreibenden Statement. Das `SELECT` in app_cash_accounts()
    lag damit ausserhalb — zwei fast gleichzeitige Buchungen lasen beide denselben
    Stand, rechneten beide von dort und die zweite ueberschrieb die erste. Beide
    Ausgaben standen in der Liste, nur eine war vom Konto weg, ohne Fehlermeldung.
    Gemessen: acht parallele Buchungen a 30 EUR senkten das Giro um 30 statt 240.

    Ausloeser ist nicht "App und Bot gleichzeitig", sondern die App gegen sich
    selbst: commitEntry() feuert syncExpenseToServer() ohne `await`, damit die
    Eingabe nicht haengt. Bei schlechtem Netz ist der erste POST noch unterwegs,
    wenn der zweite abgeschickt wird.

    BEGIN IMMEDIATE zieht die Sperre vor das Lesen. Der zweite Request wartet
    (siehe timeout in db()) und liest danach den korrekten Stand.

    Gehoert an den Anfang jedes Endpunkts, der einen Kontostand liest und daraus
    einen neuen berechnet. Reine Leser (/v1/state) brauchen es nicht.
    """
    conn.execute("BEGIN IMMEDIATE")


def cors(resp):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


@app.after_request
def after_request(resp):
    return cors(resp)


@app.before_request
def reject_untrusted_browser_writes():
    """Nur die Rov.E-Webseite darf Daten im Namen eines App-Tokens veraendern."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin not in ALLOWED_ORIGINS:
        return jsonify({"ok": False, "error": "untrusted_origin"}), 403
    return None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "rove-app-api",
        "marketDataConfigured": bool(os.getenv("TWELVE_DATA_API_KEY", "").strip()),
        "europeMarketDataConfigured": bool(os.getenv("LEEWAY_API_TOKEN", "").strip()),
        "screenshotImportConfigured": bool(OPENAI_API_KEY),
    })


@app.route("/v1/auth/request-code", methods=["OPTIONS"])
@app.route("/v1/auth/verify-code", methods=["OPTIONS"])
@app.route("/v1/auth/me", methods=["OPTIONS"])
@app.route("/v1/auth/logout", methods=["OPTIONS"])
@app.route("/v1/auth/password/setup", methods=["OPTIONS"])
@app.route("/v1/auth/password/login", methods=["OPTIONS"])
@app.route("/v1/auth/password/change", methods=["OPTIONS"])
@app.route("/v1/auth/password/reset/request", methods=["OPTIONS"])
@app.route("/v1/auth/password/reset/confirm", methods=["OPTIONS"])
@app.route("/v1/onboarding", methods=["OPTIONS"])
@app.route("/v1/admin/overview", methods=["OPTIONS"])
@app.route("/v1/admin/invitations", methods=["OPTIONS"])
@app.route("/v1/admin/invitations/<int:invitation_id>", methods=["OPTIONS"])
@app.route("/v1/admin/access/<int:target_user_id>", methods=["OPTIONS"])
def auth_options():
    return ("", 204)


@app.route("/v1/expenses", methods=["OPTIONS"])
def expenses_options():
    return ("", 204)


@app.route("/v1/import/screenshot", methods=["OPTIONS"])
@app.route("/v1/import/screenshot/commit", methods=["OPTIONS"])
def screenshot_import_options():
    return ("", 204)


@app.route("/v1/expenses/<int:expense_id>", methods=["OPTIONS"])
def expense_item_options(expense_id: int):
    return ("", 204)


@app.route("/v1/income", methods=["OPTIONS"])
def income_options():
    return ("", 204)


@app.route("/v1/expenses/<int:expense_id>/category", methods=["OPTIONS"])
def expense_category_options(expense_id: int):
    return ("", 204)


@app.route("/v1/cash-movements/<int:movement_id>", methods=["OPTIONS"])
def cash_movement_item_options(movement_id: int):
    return ("", 204)


@app.route("/v1/pair", methods=["OPTIONS"])
def pair_options():
    return ("", 204)


@app.route("/v1/transactions", methods=["OPTIONS"])
def transactions_options():
    return ("", 204)


@app.route("/v1/state", methods=["OPTIONS"])
def state_options():
    return ("", 204)


@app.route("/v1/budgets", methods=["OPTIONS"])
def budgets_options():
    return ("", 204)


@app.route("/v1/accounts", methods=["OPTIONS"])
@app.route("/v1/asset-order", methods=["OPTIONS"])
def accounts_options():
    return ("", 204)


@app.route("/v1/property", methods=["OPTIONS"])
def property_options():
    return ("", 204)


@app.route("/v1/investments", methods=["OPTIONS"])
def investments_options():
    return ("", 204)


@app.route("/v1/portfolio-tracking", methods=["OPTIONS"])
def portfolio_tracking_options():
    return ("", 204)


@app.route("/v1/etf-position-plan", methods=["OPTIONS"])
def etf_position_plan_options():
    return ("", 204)


@app.route("/v1/monthly-plan", methods=["OPTIONS"])
def monthly_plan_options():
    return ("", 204)


@app.route("/v1/goals", methods=["OPTIONS"])
def goals_options():
    return ("", 204)


@app.route("/v1/contracts", methods=["OPTIONS"])
def contracts_options():
    return ("", 204)


@app.route("/v1/reports/<report_month>/pdf", methods=["OPTIONS"])
def report_pdf_options(report_month: str):
    return ("", 204)


@app.route("/v1/data-export", methods=["OPTIONS"])
def data_export_options():
    return ("", 204)


@app.route("/v1/account/delete-code", methods=["OPTIONS"])
@app.route("/v1/account", methods=["OPTIONS"])
def account_delete_options():
    return ("", 204)


def token_from_request() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    data = request.get_json(silent=True) or {}
    return str(data.get("token") or "").strip()


def user_from_token(conn: sqlite3.Connection, token: str) -> int | None:
    """Compatibility boundary for the retired state-link bearer flow.

    Legacy callers still pass a token argument, but it is deliberately ignored. Every
    finance endpoint derives its user from the HttpOnly session cookie, leaving one
    central authorization point for a later device-lock check.
    """
    session = session_user_from_cookie(conn)
    return session[0] if session else None


def is_admin_user(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def scalar_count(conn: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(query, params).fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.OperationalError:
        return 0


def ensure_admin_tables(conn: sqlite3.Connection) -> None:
    """Nachvollziehbares Protokoll fuer manuelle Beta-Zugriffsentscheidungen."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_admin_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id   INTEGER NOT NULL,
            action          TEXT NOT NULL,
            target_user_id  INTEGER,
            target_email    TEXT DEFAULT '',
            details         TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_admin_events_created ON app_admin_events(created_at)"
    )


def authenticated_admin(conn: sqlite3.Connection):
    """Prueft E-Mail-Session und Adminrolle fuer jeden Admin-Endpunkt."""
    ensure_auth_tables(conn)
    session = session_user_from_cookie(conn)
    if not session:
        return None, (jsonify({"ok": False, "error": "reauthentication_required"}), 401)
    user_id, _session_id = session
    if not is_admin_user(user_id):
        return None, (jsonify({"ok": False, "error": "forbidden"}), 403)
    ensure_admin_tables(conn)
    return user_id, None


def clean_text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    return text[:80] if text else fallback


def ensure_expense_request_id_schema(conn: sqlite3.Connection) -> None:
    """Adds an optional user-scoped idempotency key to existing expense rows."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(expenses)")}
    if "request_id" not in columns:
        conn.execute("ALTER TABLE expenses ADD COLUMN request_id TEXT")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_user_request_id
           ON expenses(user_id, request_id)
           WHERE request_id IS NOT NULL AND TRIM(request_id) <> ''"""
    )


def screenshot_mime_type(payload: bytes) -> str | None:
    """Vertraut nicht dem Dateinamen oder dem vom Browser gelieferten MIME-Typ."""
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def parse_screenshot_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    # Ein Bank-Screenshot darf keine erfundene Zukunftsbuchung erzeugen.
    if parsed.date() > (datetime.now().date() + timedelta(days=1)):
        return None
    return parsed.strftime("%Y-%m-%d")


def screenshot_row_key(user_id: int, image_digest: str, index: int, row: dict) -> str:
    material = "|".join((
        str(user_id), image_digest, str(index), str(row.get("date") or ""),
        str(row.get("merchant") or ""), f"{float(row.get('amount') or 0):.2f}",
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def request_screenshot_analysis(image_bytes: bytes, mime_type: str) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("screenshot_import_not_configured")

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""Du liest einen Screenshot mit Bankumsaetzen fuer eine deutsche Finanz-App.
Heute ist {today}.

Extrahiere ausschliesslich sichtbare, tatsaechliche Umsatzzeilen. Ignoriere Kontostaende,
verfuegbare Betraege, Werbetexte, Summen, vorgemerkte Gesamtwerte und Navigation.
Erfinde keine Werte. Ein Minus, 'Lastschrift', 'Kartenzahlung' oder eine Belastung ist expense.
Eine Gutschrift oder Einzahlung ist income. Wenn die Richtung nicht sicher erkennbar ist,
setze confidence unter 0.6. Gib Datumswerte als YYYY-MM-DD aus; ist das Jahr nicht sichtbar,
verwende das aktuelle Jahr nur dann, wenn der Screenshot eindeutig den aktuellen Zeitraum zeigt,
sonst null.

Erlaubte Kategorien fuer Ausgaben:
Lebensmittel, Mobilitaet, Restaurant, Abos, Shopping, Freizeit, Drogerie, Gesundheit,
Pflege, Sonstiges.

Antworte als reines JSON:
{{"transactions":[{{"date":"YYYY-MM-DD oder null","merchant":"kurzer Name",
"amount":12.34,"direction":"expense oder income","category":"eine erlaubte Kategorie",
"confidence":0.0}}]}}
Maximal {SCREENSHOT_MAX_ROWS} Zeilen. Betraege immer positiv und exakt wie im Bild."""
    body = {
        "model": SCREENSHOT_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}",
                    "detail": "high",
                }},
            ],
        }],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 1800,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("screenshot_rate_limited") from exc
        if exc.code in {401, 403}:
            raise RuntimeError("screenshot_provider_auth_failed") from exc
        raise RuntimeError("screenshot_provider_unavailable") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("screenshot_provider_unavailable") from exc

    try:
        content = response_data["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("screenshot_invalid_response") from exc


def normalize_screenshot_rows(raw: object) -> list[dict]:
    rows = raw if isinstance(raw, list) else []
    normalized: list[dict] = []
    for row in rows[:SCREENSHOT_MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        try:
            amount = round(abs(float(row.get("amount") or 0)), 2)
            confidence = min(1.0, max(0.0, float(row.get("confidence") or 0)))
        except (TypeError, ValueError):
            continue
        merchant = clean_text(row.get("merchant"))
        direction = str(row.get("direction") or "").strip().lower()
        if not merchant or amount <= 0 or amount > 1_000_000 or direction not in {"expense", "income"}:
            continue
        category = clean_text(row.get("category"), "Sonstiges")
        if category not in APP_TO_BOT_CATEGORY:
            category = "Sonstiges"
        normalized.append({
            "date": parse_screenshot_date(row.get("date")),
            "merchant": merchant,
            "amount": amount,
            "direction": direction,
            "category": category,
            "confidence": round(confidence, 2),
        })
    return normalized


def probable_expense_duplicate(
    conn: sqlite3.Connection, user_id: int, row: dict
) -> bool:
    booking_date = row.get("date") or datetime.now().strftime("%Y-%m-%d")
    candidates = conn.execute(
        """SELECT merchant FROM expenses
            WHERE user_id = ? AND ABS(amount - ?) < 0.005
              AND date(created_at) = date(?)""",
        (user_id, float(row["amount"]), booking_date),
    ).fetchall()
    wanted = normalize_category_rule_alias(row.get("merchant"))
    return any(normalize_category_rule_alias(item["merchant"]) == wanted for item in candidates)


def normalize_category_rule_alias(value: object) -> str:
    """Gleiche robuste Händler-Normalisierung wie der Telegram-Bot.

    Die Regel liegt bewusst in der gemeinsamen `user_category_rules`-Tabelle.
    Damit merkt sich Rov.E eine Korrektur sowohl in der App als auch im Bot.
    """
    alias = clean_text(value).lower()
    alias = re.sub(
        r"\b(das|der|die|den|dem|mein|meine|meinen|meiner|bitte|künftig|kuenftig|zukünftig|zukunftig)\b",
        " ",
        alias,
    )
    alias = re.sub(r"[^a-z0-9äöüß ]+", " ", alias)
    return re.sub(r"\s+", " ", alias).strip()[:80]


def ensure_user_category_rules_table(conn: sqlite3.Connection) -> None:
    """Macht die App auch bei sehr alten Bot-Profilen migrationssicher."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_category_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            alias       TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            label       TEXT    DEFAULT '',
            usage_count INTEGER DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, alias)
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_user_category_rules_user
           ON user_category_rules(user_id, alias)"""
    )


def category_rule_for_merchant(conn: sqlite3.Connection, user_id: int, merchant: str) -> str | None:
    normalized = normalize_category_rule_alias(merchant)
    if not normalized:
        return None
    ensure_user_category_rules_table(conn)
    rows = conn.execute(
        """SELECT alias, category
             FROM user_category_rules
            WHERE user_id = ?
            ORDER BY LENGTH(alias) DESC""",
        (user_id,),
    ).fetchall()
    for row in rows:
        alias = str(row["alias"] or "")
        if alias and re.search(rf"\b{re.escape(alias)}\b", normalized):
            conn.execute(
                """UPDATE user_category_rules
                      SET usage_count = usage_count + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND alias = ?""",
                (user_id, alias),
            )
            return str(row["category"])
    return None


def save_category_rule(conn: sqlite3.Connection, user_id: int, merchant: str, category: str) -> None:
    alias = normalize_category_rule_alias(merchant)
    if len(alias) < 2 or category not in BOT_TO_APP_CATEGORY:
        return
    ensure_user_category_rules_table(conn)
    conn.execute(
        """INSERT INTO user_category_rules
               (user_id, alias, category, label, usage_count, updated_at)
           VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, alias)
           DO UPDATE SET category = excluded.category,
                         label = excluded.label,
                         updated_at = CURRENT_TIMESTAMP""",
        (user_id, alias, category, alias.title()),
    )


def clean_pairing_code(value: object) -> str:
    raw = "".join(char for char in str(value or "").upper() if char.isalnum())
    return f"{raw[:4]}-{raw[4:8]}" if len(raw) == 8 else ""


def pairing_attempt_allowed() -> bool:
    """Bremst Rateversuche am achtstelligen Verbindungs-Code pro IP aus."""
    ip = request.headers.get("X-Real-IP", request.remote_addr or "unknown")
    now = time.monotonic()
    attempts = [stamp for stamp in _pair_attempts.get(ip, []) if now - stamp < PAIR_ATTEMPT_WINDOW_SECONDS]
    if len(attempts) >= PAIR_ATTEMPT_LIMIT:
        _pair_attempts[ip] = attempts
        return False
    attempts.append(now)
    _pair_attempts[ip] = attempts
    return True


def screenshot_attempt_allowed(user_id: int) -> bool:
    """Kostenbremse pro Nutzer; ein Neustart setzt nur das In-Memory-Fenster zurueck."""
    now = time.monotonic()
    attempts = [
        stamp for stamp in _screenshot_attempts.get(user_id, [])
        if now - stamp < SCREENSHOT_ATTEMPT_WINDOW_SECONDS
    ]
    if len(attempts) >= SCREENSHOT_ATTEMPT_LIMIT:
        _screenshot_attempts[user_id] = attempts
        return False
    attempts.append(now)
    _screenshot_attempts[user_id] = attempts
    return True


def normalize_email(value: object) -> str:
    email = str(value or "").strip().casefold()
    return email if EMAIL_RE.match(email) and len(email) <= 254 else ""


def auth_secret() -> str:
    # Codes, sessions and reset codes must never share an external provider secret.
    return AUTH_SECRET


def keyed_hash(value: str) -> str:
    secret = auth_secret()
    if not secret:
        raise RuntimeError("app_auth_not_configured")
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def auth_attempt_allowed(bucket: str, email: str, limit: int = AUTH_ATTEMPT_LIMIT) -> bool:
    ip = request.headers.get("X-Real-IP", request.remote_addr or "unknown")
    key = f"{ip}:{email.casefold()}"
    now = time.monotonic()
    attempts = [stamp for stamp in AUTH_BUCKETS.get(bucket, {}).get(key, []) if now - stamp < AUTH_ATTEMPT_WINDOW_SECONDS]
    if len(attempts) >= limit:
        AUTH_BUCKETS.setdefault(bucket, {})[key] = attempts
        return False
    attempts.append(now)
    AUTH_BUCKETS.setdefault(bucket, {})[key] = attempts
    return True


def validate_password(value: object) -> str | None:
    password = value if isinstance(value, str) else ""
    if not (AUTH_PASSWORD_MIN_LENGTH <= len(password) <= AUTH_PASSWORD_MAX_LENGTH):
        return None
    return password


def issue_session(conn: sqlite3.Connection, account_id: int) -> tuple[str, datetime]:
    raw_session = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(days=AUTH_SESSION_TTL_DAYS)
    conn.execute("INSERT INTO app_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                 (keyed_hash(raw_session), account_id, expires_at.strftime("%Y-%m-%d %H:%M:%S")))
    return raw_session, expires_at


def ensure_auth_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_state_links (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'active',
            pairing_code TEXT
        )"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(app_state_links)")}
    if "pairing_code" not in columns:
        conn.execute("ALTER TABLE app_state_links ADD COLUMN pairing_code TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_state_links_expiry ON app_state_links(status, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_state_links_pairing ON app_state_links(pairing_code, status)")
    # State-link bearers are retired. Normal HttpOnly sessions live in app_sessions and
    # remain untouched, so this cutover does not force existing web users to log in again.
    conn.execute("UPDATE app_state_links SET status = 'revoked' WHERE status = 'active'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL UNIQUE,
            user_id     INTEGER NOT NULL,
            verified_at TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    # Anzeigename (27.07.): Die App zeigte bei JEDEM Nutzer „Furkan / project-clarity@outlook.com"
    # — die Werte standen fest im HTML und der Server lieferte ueberhaupt keine Identitaet. Weder
    # `users` (Bot) noch `app_accounts` hatten ein Namensfeld. Nullable: wer nichts setzt, bekommt
    # in der App den Teil vor dem @ seiner Login-Adresse.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(app_accounts)")}
    if "display_name" not in columns:
        conn.execute("ALTER TABLE app_accounts ADD COLUMN display_name TEXT")
    if "source" not in columns:
        conn.execute("ALTER TABLE app_accounts ADD COLUMN source TEXT NOT NULL DEFAULT 'telegram'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_login_codes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email        TEXT NOT NULL,
            code_hash    TEXT NOT NULL,
            pairing_code TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at   TEXT NOT NULL,
            attempts     INTEGER NOT NULL DEFAULT 0,
            consumed_at  TEXT
        )"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(app_login_codes)")}
    if "flow" not in columns:
        conn.execute("ALTER TABLE app_login_codes ADD COLUMN flow TEXT NOT NULL DEFAULT 'login'")
    if "invitation_id" not in columns:
        conn.execute("ALTER TABLE app_login_codes ADD COLUMN invitation_id INTEGER")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_invitations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL UNIQUE,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at  TEXT NOT NULL,
            consumed_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_user_sequence (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash  TEXT NOT NULL UNIQUE,
            account_id  INTEGER NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at  TEXT NOT NULL,
            last_seen_at TEXT,
            revoked_at  TEXT,
            FOREIGN KEY(account_id) REFERENCES app_accounts(id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_account_delete_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            code_hash   TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at  TEXT NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 0,
            consumed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_login_codes_email ON app_login_codes(email, consumed_at, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_sessions_hash ON app_sessions(token_hash, revoked_at, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_delete_codes_user ON app_account_delete_codes(user_id, consumed_at, expires_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_credentials (
        account_id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL, password_version INTEGER NOT NULL DEFAULT 1,
        password_set_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, password_changed_at TEXT,
        FOREIGN KEY(account_id) REFERENCES app_accounts(id) ON DELETE CASCADE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_password_reset_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL, code_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        consumed_at TEXT, FOREIGN KEY(account_id) REFERENCES app_accounts(id) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_password_reset_codes_account ON app_password_reset_codes(account_id, consumed_at, expires_at)")


def create_app_only_user(conn: sqlite3.Connection, email: str) -> tuple[int, int]:
    """Legt die minimale zentrale Identitaet an; Finanzdaten folgen erst im Onboarding."""
    sequence = conn.execute("INSERT INTO app_user_sequence DEFAULT VALUES")
    user_id = APP_USER_ID_BASE + int(sequence.lastrowid)
    conn.execute(
        """INSERT INTO users
           (user_id, onboarding_step, current_month, fixed_costs_details)
           VALUES (?, 0, ?, '{}')""",
        (user_id, datetime.now().strftime("%Y-%m")),
    )
    conn.execute(
        """INSERT INTO user_access
           (user_id, status, approved_at, display_name, username, note)
           VALUES (?, 'app_only', CURRENT_TIMESTAMP, '', '', 'App-only Beta')""",
        (user_id,),
    )
    account = conn.execute(
        """INSERT INTO app_accounts (email, user_id, verified_at, source)
           VALUES (?, ?, CURRENT_TIMESTAMP, 'app')""",
        (email, user_id),
    )
    return user_id, int(account.lastrowid)


def latest_score(conn: sqlite3.Connection, user_id: int) -> tuple[int, str]:
    try:
        row = conn.execute(
            """SELECT clarity_score, rank_name
                 FROM score_history
                WHERE user_id = ?
                ORDER BY recorded_date DESC, id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        return int(row["clarity_score"] or 0), str(row["rank_name"] or "—")
    try:
        row = conn.execute(
            """SELECT clarity_score
                 FROM monthly_snapshots
                WHERE user_id = ?
                ORDER BY month DESC, id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    return (int(row["clarity_score"] or 0), "—") if row else (0, "—")


def revoke_legacy_state_links(conn: sqlite3.Connection) -> None:
    """Invalidates old state-link bearers without touching normal web sessions."""
    conn.execute("UPDATE app_state_links SET status = 'revoked' WHERE status = 'active'")


def send_login_email(email: str, code: str) -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("brevo_not_configured")
    payload = {
        "sender": {"name": LOGIN_FROM_NAME, "email": LOGIN_FROM_EMAIL},
        "to": [{"email": email}],
        "subject": "Dein Rov.E Login-Code",
        "textContent": (
            f"Dein Rov.E Login-Code lautet: {code}\n\n"
            f"Der Code ist {AUTH_CODE_TTL_MINUTES} Minuten gültig. "
            "Wenn du das nicht warst, kannst du diese E-Mail ignorieren."
        ),
        "htmlContent": (
            "<html><body style=\"font-family:Arial,sans-serif;color:#111\">"
            "<p>Dein Rov.E Login-Code lautet:</p>"
            f"<p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">{code}</p>"
            f"<p>Der Code ist {AUTH_CODE_TTL_MINUTES} Minuten gültig.</p>"
            "<p style=\"color:#666\">Wenn du das nicht warst, kannst du diese E-Mail ignorieren.</p>"
            "</body></html>"
        ),
        "tags": ["rove-app-login"],
    }
    req = urllib.request.Request(
        BREVO_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"brevo_status_{response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"brevo_status_{exc.code}:{body}") from exc


def send_password_reset_email(email: str, code: str) -> None:
    """Sends a separate, short-lived recovery code without exposing it in logs."""
    if not BREVO_API_KEY:
        raise RuntimeError("brevo_not_configured")
    payload = {
        "sender": {"name": LOGIN_FROM_NAME, "email": LOGIN_FROM_EMAIL},
        "to": [{"email": email}],
        "subject": "Dein Rov.E Passwort zurücksetzen",
        "textContent": (
            f"Dein Rov.E Code zum Zurücksetzen lautet: {code}\n\n"
            f"Der Code ist {AUTH_RESET_TTL_MINUTES} Minuten gültig. "
            "Wenn du das nicht angefordert hast, kannst du diese E-Mail ignorieren."
        ),
        "htmlContent": (
            "<html><body style=\"font-family:Arial,sans-serif;color:#111\">"
            "<p>Dein Rov.E Code zum Zurücksetzen lautet:</p>"
            f"<p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">{code}</p>"
            f"<p>Der Code ist {AUTH_RESET_TTL_MINUTES} Minuten gültig.</p>"
            "<p style=\"color:#666\">Wenn du das nicht angefordert hast, kannst du diese E-Mail ignorieren.</p>"
            "</body></html>"
        ),
        "tags": ["rove-app-password-reset"],
    }
    req = urllib.request.Request(
        BREVO_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"accept": "application/json", "api-key": BREVO_API_KEY, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"brevo_status_{response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"brevo_status_{exc.code}:{body}") from exc


def send_account_delete_email(email: str, code: str) -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("brevo_not_configured")
    payload = {
        "sender": {"name": LOGIN_FROM_NAME, "email": LOGIN_FROM_EMAIL},
        "to": [{"email": email}],
        "subject": "Rov.E Konto löschen - Bestätigungscode",
        "textContent": (
            f"Dein Code für die endgültige Löschung deines Rov.E Kontos lautet: {code}\n\n"
            f"Der Code ist {ACCOUNT_DELETE_CODE_TTL_MINUTES} Minuten gültig. "
            "Wenn du die Löschung nicht angefordert hast, ignoriere diese E-Mail."
        ),
        "htmlContent": (
            "<html><body style=\"font-family:Arial,sans-serif;color:#111\">"
            "<p>Dein Code für die endgültige Löschung deines Rov.E Kontos lautet:</p>"
            f"<p style=\"font-size:28px;font-weight:700;letter-spacing:4px\">{code}</p>"
            f"<p>Der Code ist {ACCOUNT_DELETE_CODE_TTL_MINUTES} Minuten gültig.</p>"
            "<p style=\"color:#666\">Wenn du die Löschung nicht angefordert hast, "
            "ignoriere diese E-Mail.</p></body></html>"
        ),
        "tags": ["rove-account-delete"],
    }
    req = urllib.request.Request(
        BREVO_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"brevo_status_{response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"brevo_status_{exc.code}:{body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("brevo_unavailable") from exc


def session_user_from_cookie(conn: sqlite3.Connection) -> tuple[int, int] | None:
    raw = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not raw:
        return None
    try:
        token_hash = keyed_hash(raw)
    except RuntimeError:
        return None
    row = conn.execute(
        """SELECT s.id AS session_id, a.user_id
             FROM app_sessions s
             JOIN app_accounts a ON a.id = s.account_id
             LEFT JOIN user_access ua ON ua.user_id = a.user_id
            WHERE s.token_hash = ?
              AND s.revoked_at IS NULL
              AND COALESCE(ua.status, 'approved') IN ('approved', 'app_only')
              AND datetime(s.expires_at) >= datetime('now', 'localtime')""",
        (token_hash,),
    ).fetchone()
    if not row:
        return None
    conn.execute("UPDATE app_sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?", (row["session_id"],))
    return int(row["user_id"]), int(row["session_id"])


def set_session_cookie(resp, raw_token: str, expires_at: datetime):
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=AUTH_SESSION_TTL_DAYS * 24 * 60 * 60,
        expires=expires_at,
        secure=SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
        path="/app-api/",
    )
    return resp


@app.route("/v1/auth/request-code", methods=["POST"])
def request_login_code():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    pairing_code = clean_pairing_code(payload.get("pairing_code"))
    registration = payload.get("registration") is True
    if not email:
        return jsonify({"ok": False, "error": "valid_email_required"}), 400
    if not auth_attempt_allowed("verification_code_request", email):
        return jsonify({"ok": False, "error": "too_many_login_attempts"}), 429

    try:
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = keyed_hash(f"{email}:{code}")
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    with db() as conn:
        ensure_auth_tables(conn)
        account = conn.execute("SELECT id FROM app_accounts WHERE email = ?", (email,)).fetchone()
        invitation_id = None
        flow = "login"
        if registration:
            if account:
                return jsonify({"ok": False, "error": "account_already_exists"}), 409
            invitation = conn.execute(
                """SELECT id FROM app_invitations
                    WHERE email = ? AND consumed_at IS NULL
                      AND datetime(expires_at) >= datetime('now', 'localtime')""",
                (email,),
            ).fetchone()
            if not invitation:
                return jsonify({"ok": False, "error": "invitation_required"}), 403
            invitation_id = int(invitation["id"])
            flow = APP_REGISTRATION_FLOW
        elif not account:
            return jsonify({"ok": False, "error": "account_required"}), 409
        conn.execute(
            """INSERT INTO app_login_codes
               (email, code_hash, pairing_code, expires_at, flow, invitation_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                email,
                code_hash,
                pairing_code or None,
                (datetime.now() + timedelta(minutes=AUTH_CODE_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S"),
                flow,
                invitation_id,
            ),
        )
        conn.commit()

    try:
        send_login_email(email, code)
    except RuntimeError as exc:
        app.logger.warning("Login-Code an %s konnte nicht gesendet werden: %s", email, exc)
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify({"ok": True, "sent": True, "needsPairing": not bool(account) and not registration})


@app.route("/v1/auth/verify-code", methods=["POST"])
def verify_login_code():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    code = re.sub(r"\D", "", str(payload.get("code") or ""))[:6]
    if not email or len(code) != 6:
        return jsonify({"ok": False, "error": "valid_email_and_code_required"}), 400
    if not auth_attempt_allowed("verification_code_verify", email):
        return jsonify({"ok": False, "error": "too_many_code_attempts"}), 429

    try:
        code_hash = keyed_hash(f"{email}:{code}")
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    with db() as conn:
        ensure_auth_tables(conn)
        row = conn.execute(
            """SELECT id, pairing_code, attempts, flow, invitation_id
                 FROM app_login_codes
                WHERE email = ?
                  AND consumed_at IS NULL
                  AND datetime(expires_at) >= datetime('now', 'localtime')
                ORDER BY datetime(created_at) DESC, id DESC LIMIT 1""",
            (email,),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "code_expired"}), 401
        if int(row["attempts"] or 0) >= 5:
            return jsonify({"ok": False, "error": "too_many_code_attempts"}), 429
        if not conn.execute(
            "SELECT 1 FROM app_login_codes WHERE id = ? AND code_hash = ?", (row["id"], code_hash)
        ).fetchone():
            conn.execute("UPDATE app_login_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return jsonify({"ok": False, "error": "invalid_code"}), 401

        account = conn.execute("SELECT id, user_id FROM app_accounts WHERE email = ?", (email,)).fetchone()
        new_account = False
        if account:
            account_id = int(account["id"])
            user_id = int(account["user_id"])
            access = conn.execute(
                "SELECT status FROM user_access WHERE user_id = ?", (user_id,)
            ).fetchone()
            if access and str(access["status"] or "") not in {"approved", "app_only"}:
                return jsonify({"ok": False, "error": "access_not_active"}), 403
        elif str(row["flow"] or "") == APP_REGISTRATION_FLOW:
            invitation = conn.execute(
                """SELECT id FROM app_invitations
                    WHERE id = ? AND email = ? AND consumed_at IS NULL
                      AND datetime(expires_at) >= datetime('now', 'localtime')""",
                (row["invitation_id"], email),
            ).fetchone()
            if not invitation:
                return jsonify({"ok": False, "error": "invitation_expired"}), 409
            user_id, account_id = create_app_only_user(conn, email)
            conn.execute(
                "UPDATE app_invitations SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (invitation["id"],),
            )
            new_account = True
        else:
            return jsonify({"ok": False, "error": "account_required"}), 409

        raw_session, expires_at = issue_session(conn, account_id)
        conn.execute("UPDATE app_login_codes SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
        conn.commit()
        credential = conn.execute("SELECT 1 FROM app_credentials WHERE account_id = ?", (account_id,)).fetchone()

    resp = make_response(jsonify({
        "ok": True,
        "new_account": new_account,
        "onboarding_required": new_account,
        "password_setup_required": not bool(credential),
    }))
    return set_session_cookie(resp, raw_session, expires_at)


def password_values(payload: dict) -> tuple[str | None, str | None]:
    password = validate_password(payload.get("password"))
    confirmation = payload.get("password_confirmation")
    if password is None or not isinstance(confirmation, str) or not hmac.compare_digest(password, confirmation):
        return None, None
    return password, confirmation


@app.route("/v1/auth/password/setup", methods=["POST"])
def setup_password():
    password, _confirmation = password_values(request.get_json(silent=True) or {})
    if password is None:
        return jsonify({"ok": False, "error": "password_policy_or_confirmation_failed"}), 400
    with db() as conn:
        begin_write(conn)
        ensure_auth_tables(conn)
        session = session_user_from_cookie(conn)
        if not session:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        user_id, session_id = session
        account = conn.execute(
            "SELECT account_id FROM app_sessions WHERE id = ? AND revoked_at IS NULL", (session_id,)
        ).fetchone()
        if not account:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        account_id = int(account["account_id"])
        if conn.execute("SELECT 1 FROM app_credentials WHERE account_id = ?", (account_id,)).fetchone():
            return jsonify({"ok": False, "error": "password_already_set"}), 409
        conn.execute(
            "INSERT INTO app_credentials (account_id, password_hash) VALUES (?, ?)",
            (account_id, PASSWORD_HASHER.hash(password)),
        )
        conn.commit()
    return jsonify({"ok": True, "password_set": True})


@app.route("/v1/auth/password/login", methods=["POST"])
def password_login():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    password = payload.get("password") if isinstance(payload.get("password"), str) else ""
    # Keep the public error identical for unknown emails, invalid passwords and malformed input.
    if not auth_attempt_allowed("password_login", email or "invalid"):
        return jsonify({"ok": False, "error": "invalid_credentials"}), 401
    try:
        with db() as conn:
            begin_write(conn)
            ensure_auth_tables(conn)
            account = conn.execute(
                """SELECT a.id, a.user_id, c.password_hash
                     FROM app_accounts a JOIN app_credentials c ON c.account_id = a.id
                    WHERE a.email = ?""",
                (email,),
            ).fetchone()
            verified = False
            if account and password:
                try:
                    verified = PASSWORD_HASHER.verify(str(account["password_hash"]), password)
                except (InvalidHashError, VerificationError):
                    verified = False
            if not account or not verified:
                return jsonify({"ok": False, "error": "invalid_credentials"}), 401
            access = conn.execute("SELECT status FROM user_access WHERE user_id = ?", (account["user_id"],)).fetchone()
            if access and str(access["status"] or "") not in {"approved", "app_only"}:
                return jsonify({"ok": False, "error": "invalid_credentials"}), 401
            if PASSWORD_HASHER.check_needs_rehash(str(account["password_hash"])):
                conn.execute(
                    "UPDATE app_credentials SET password_hash = ?, password_changed_at = CURRENT_TIMESTAMP WHERE account_id = ?",
                    (PASSWORD_HASHER.hash(password), account["id"]),
                )
            raw_session, expires_at = issue_session(conn, int(account["id"]))
            conn.commit()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    resp = make_response(jsonify({"ok": True, "onboarding_required": False}))
    return set_session_cookie(resp, raw_session, expires_at)


@app.route("/v1/auth/password/reset/request", methods=["POST"])
def request_password_reset():
    email = normalize_email((request.get_json(silent=True) or {}).get("email"))
    if not auth_attempt_allowed("password_reset_request", email or "invalid"):
        return jsonify({"ok": True, "sent": True})
    try:
        with db() as conn:
            begin_write(conn)
            ensure_auth_tables(conn)
            account = conn.execute(
                """SELECT a.id, a.email FROM app_accounts a
                     JOIN app_credentials c ON c.account_id = a.id WHERE a.email = ?""", (email,)
            ).fetchone()
            if not account:
                conn.commit()
                return jsonify({"ok": True, "sent": True})
            code = f"{secrets.randbelow(1_000_000):06d}"
            conn.execute(
                "UPDATE app_password_reset_codes SET consumed_at = CURRENT_TIMESTAMP WHERE account_id = ? AND consumed_at IS NULL",
                (account["id"],),
            )
            conn.execute(
                "INSERT INTO app_password_reset_codes (account_id, code_hash, expires_at) VALUES (?, ?, ?)",
                (account["id"], keyed_hash(f"reset:{account['id']}:{code}"),
                 (datetime.now() + timedelta(minutes=AUTH_RESET_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        try:
            send_password_reset_email(str(account["email"]), code)
        except RuntimeError:
            app.logger.warning("Passwort-Reset-E-Mail konnte nicht gesendet werden")
        return jsonify({"ok": True, "sent": True})
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.route("/v1/auth/password/reset/confirm", methods=["POST"])
def confirm_password_reset():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    code = re.sub(r"\D", "", str(payload.get("code") or ""))[:6]
    password, _confirmation = password_values(payload)
    if not auth_attempt_allowed("password_reset_verify", email or "invalid"):
        return jsonify({"ok": False, "error": "invalid_or_expired_reset_code"}), 401
    if not email or len(code) != 6 or password is None:
        return jsonify({"ok": False, "error": "invalid_or_expired_reset_code"}), 401
    try:
        with db() as conn:
            begin_write(conn)
            ensure_auth_tables(conn)
            row = conn.execute(
                """SELECT r.id, r.account_id, r.code_hash, r.attempts, a.user_id
                     FROM app_password_reset_codes r JOIN app_accounts a ON a.id = r.account_id
                    WHERE a.email = ? AND r.consumed_at IS NULL
                      AND datetime(r.expires_at) >= datetime('now', 'localtime')
                    ORDER BY datetime(r.created_at) DESC, r.id DESC LIMIT 1""", (email,)
            ).fetchone()
            expected = keyed_hash(f"reset:{row['account_id']}:{code}") if row else ""
            if not row or int(row["attempts"] or 0) >= 5 or not hmac.compare_digest(str(row["code_hash"]), expected):
                if row:
                    conn.execute("UPDATE app_password_reset_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
                    conn.commit()
                return jsonify({"ok": False, "error": "invalid_or_expired_reset_code"}), 401
            conn.execute(
                "UPDATE app_credentials SET password_hash = ?, password_changed_at = CURRENT_TIMESTAMP WHERE account_id = ?",
                (PASSWORD_HASHER.hash(password), row["account_id"]),
            )
            conn.execute("UPDATE app_password_reset_codes SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            conn.execute("UPDATE app_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE account_id = ? AND revoked_at IS NULL", (row["account_id"],))
            raw_session, expires_at = issue_session(conn, int(row["account_id"]))
            conn.commit()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    resp = make_response(jsonify({"ok": True}))
    return set_session_cookie(resp, raw_session, expires_at)


@app.route("/v1/auth/password/change", methods=["POST"])
def change_password():
    payload = request.get_json(silent=True) or {}
    current_password = payload.get("current_password") if isinstance(payload.get("current_password"), str) else ""
    password, _confirmation = password_values(payload)
    if password is None:
        return jsonify({"ok": False, "error": "password_policy_or_confirmation_failed"}), 400
    with db() as conn:
        begin_write(conn)
        ensure_auth_tables(conn)
        session = session_user_from_cookie(conn)
        if not session:
            return jsonify({"ok": False, "error": "not_logged_in"}), 401
        user_id, session_id = session
        row = conn.execute(
            """SELECT s.account_id, c.password_hash FROM app_sessions s
                 JOIN app_credentials c ON c.account_id = s.account_id WHERE s.id = ?""", (session_id,)
        ).fetchone()
        try:
            verified = bool(row and PASSWORD_HASHER.verify(str(row["password_hash"]), current_password))
        except (InvalidHashError, VerificationError):
            verified = False
        if not verified:
            return jsonify({"ok": False, "error": "current_password_invalid"}), 401
        conn.execute(
            "UPDATE app_credentials SET password_hash = ?, password_changed_at = CURRENT_TIMESTAMP WHERE account_id = ?",
            (PASSWORD_HASHER.hash(password), row["account_id"]),
        )
        conn.execute("UPDATE app_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE account_id = ? AND revoked_at IS NULL", (row["account_id"],))
        raw_session, expires_at = issue_session(conn, int(row["account_id"]))
        conn.commit()
    resp = make_response(jsonify({"ok": True}))
    return set_session_cookie(resp, raw_session, expires_at)


@app.route("/v1/auth/me", methods=["GET"])
def auth_me():
    try:
        with db() as conn:
            ensure_auth_tables(conn)
            session = session_user_from_cookie(conn)
            if not session:
                return jsonify({"ok": False, "error": "not_logged_in"}), 401
            _user_id, _session_id = session
            conn.commit()
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True})


@app.route("/v1/auth/logout", methods=["POST"])
def auth_logout():
    raw = request.cookies.get(SESSION_COOKIE_NAME, "")
    if raw:
        try:
            token_hash = keyed_hash(raw)
            with db() as conn:
                ensure_auth_tables(conn)
                conn.execute(
                    "UPDATE app_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ?",
                    (token_hash,),
                )
                conn.commit()
        except RuntimeError:
            pass
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/app-api/")
    return resp


def clean_budget_updates(value: object) -> list[tuple[str, float, str]]:
    """Validiert einzelne App-Budgetaenderungen vor dem DB-Write."""
    if not isinstance(value, list) or not value:
        return []

    updates: dict[str, tuple[float, str]] = {}
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        category = APP_TO_BOT_CATEGORY.get(clean_text(item.get("category")))
        try:
            amount = round(float(item.get("limit")), 2)
        except (TypeError, ValueError):
            continue
        if not category or amount < 0 or amount > 100_000:
            continue
        source = "suggested" if item.get("source") == "suggested" else "manual"
        updates[category] = (amount, source)
    return [(category, amount, source) for category, (amount, source) in updates.items()]


def goal_amount(value: object) -> float | None:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return amount if 0 <= amount <= 10_000_000 else None


CONTRACT_CATEGORIES = frozenset({"Abos", "Versicherungen", "Wohnen", "Kredite", "Mobilität", "Sonstiges"})


def sync_app_contract_details(conn: sqlite3.Connection, user_id: int) -> None:
    """Keeps the legacy aggregate in sync with the unified contract set."""
    sync_contract_fixed_costs(conn, user_id)


def app_cash_accounts(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    """Laedt die drei Cash-Konten und migriert alte Bot-Cashwerte beim ersten App-Write."""
    ensure_app_account_balances_table(conn)
    rows = conn.execute(
        """SELECT account_key, amount FROM app_account_balances WHERE user_id = ?""",
        (user_id,),
    ).fetchall()
    balances = {key: 0.0 for key in ACCOUNT_KEYS}
    if rows:
        for row in rows:
            if row["account_key"] in balances:
                value = float(row["amount"] or 0)
                # Giro darf einen Dispo/negativen Stand abbilden. Tagesgeld und Bargeld
                # bleiben echte Guthabenkonten und koennen nicht unter null fallen.
                balances[row["account_key"]] = round(
                    value if row["account_key"] == "giro" else max(0.0, value), 2
                )
        return balances

    # Alte Bot-Profile kannten nur eine Cash-Gesamtsumme. Sie startet sicher im Girokonto;
    # der Nutzer verschiebt danach Tagesgeld oder Bargeld, ohne Vermögen zu erfinden.
    user = conn.execute("SELECT current_cash FROM users WHERE user_id = ?", (user_id,)).fetchone()
    balances["giro"] = round(max(0.0, float(user["current_cash"] or 0)), 2) if user else 0.0
    return balances


def save_app_cash_accounts(conn: sqlite3.Connection, user_id: int, balances: dict[str, float]) -> None:
    for key in ACCOUNT_KEYS:
        value = float(balances.get(key, 0.0))
        if key != "giro":
            value = max(0.0, value)
        value = min(10_000_000.0, max(-10_000_000.0, value))
        conn.execute(
            """INSERT INTO app_account_balances (user_id, account_key, amount, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, account_key)
               DO UPDATE SET amount = excluded.amount, updated_at = CURRENT_TIMESTAMP""",
            (user_id, key, round(value, 2)),
        )
    conn.execute(
        "UPDATE users SET current_cash = ? WHERE user_id = ?",
        (round(sum(balances.values()), 2), user_id),
    )


def multi_cash_accounts_enabled(conn: sqlite3.Connection, user_id: int) -> bool:
    """Read the Sprint-2 pilot flag without changing flag-off money paths."""
    return is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1)


def require_multi_cash_pilot(conn: sqlite3.Connection, user_id: int):
    if not multi_cash_accounts_enabled(conn, user_id):
        return jsonify({"ok": False, "error": "multi_cash_accounts_not_enabled"}), 404
    prepare_multi_cash_write(conn)
    return None


def financial_account_error(exc: Exception):
    code = str(exc)
    status = 404 if code == "financial_account_not_found" else 400
    return jsonify({"ok": False, "error": code}), status


def prepare_multi_cash_write(conn: sqlite3.Connection) -> None:
    """Ensure only additive nullable reference columns for a flagged write."""
    ensure_app_cash_movements_table(conn)
    ensure_financial_account_reference_schema(conn)


def legacy_financial_account_id(
    conn: sqlite3.Connection, user_id: int, legacy_key: str
) -> int:
    account = get_legacy_financial_account(conn, user_id, legacy_key)
    if not account or str(account["status"]) != "active":
        raise LookupError("legacy_financial_account_not_found")
    return int(account["id"])


def role_financial_account_id(conn: sqlite3.Connection, user_id: int, role: str) -> int:
    return int(require_account_role(conn, user_id, role)["id"])


def resolve_etf_source_account(
    conn: sqlite3.Connection, user_id: int, payload: dict, legacy_source: str
) -> tuple[str, int | None]:
    """Resolve a pilot ETF source while retaining the legacy type aggregate."""
    if not multi_cash_accounts_enabled(conn, user_id):
        return legacy_source, None
    prepare_multi_cash_write(conn)
    raw_id = payload.get("source_account_id", payload.get("sourceAccountId"))
    if raw_id not in (None, ""):
        account = require_financial_account(conn, user_id, int(raw_id))
        account_type = str(account["account_type"])
        if account_type not in {"checking", "savings"}:
            raise ValueError("valid_etf_source_required")
        return ("giro" if account_type == "checking" else "tagesgeld"), int(account["id"])
    return legacy_source, legacy_financial_account_id(conn, user_id, legacy_source)


def apply_stored_account_deltas(
    conn: sqlite3.Connection,
    user_id: int,
    deltas: dict[int, float],
    *,
    require_funds_for: set[int] | None = None,
) -> dict[str, float]:
    """Reverse saved account references without corrupting a flag-off legacy period.

    With the pilot enabled, Financial Accounts are the source of truth. After a
    rollback, legacy writes intentionally remain the source of truth and may have
    advanced while Financial Accounts stayed unchanged. A reversal still targets
    the originally saved account, but calculates from the current legacy balance
    and updates only that matching compatibility account. It must never mirror a
    stale full Financial-Account snapshot over newer legacy writes.
    """
    required = require_funds_for or set()
    if multi_cash_accounts_enabled(conn, user_id):
        return apply_financial_account_deltas(
            conn, user_id, deltas, require_funds_for=required
        )

    balances = app_cash_accounts(conn, user_id)
    updates: list[tuple[int, str, float]] = []
    seen_keys: set[str] = set()
    for raw_account_id, raw_delta in deltas.items():
        account_id = int(raw_account_id)
        account = require_financial_account(conn, user_id, account_id)
        legacy_key = str(account["legacy_key"] or "")
        if legacy_key not in ACCOUNT_KEYS or legacy_key in seen_keys:
            raise ValueError("stored_account_not_legacy_compatible")
        updated = round(float(balances[legacy_key]) + float(raw_delta), 2)
        if account_id in required and updated < -0.0049:
            raise ValueError("financial_account_balance_insufficient")
        if str(account["account_type"]) in {"savings", "wallet"} and updated < -0.0049:
            raise ValueError("financial_account_balance_insufficient")
        updates.append((account_id, legacy_key, 0.0 if abs(updated) < 0.005 else updated))
        seen_keys.add(legacy_key)

    for account_id, legacy_key, updated in updates:
        cur = conn.execute(
            """UPDATE app_financial_accounts
                  SET balance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND status = 'active'""",
            (updated, account_id, user_id),
        )
        if cur.rowcount != 1:
            raise LookupError("financial_account_not_found")
        balances[legacy_key] = updated
    save_app_cash_accounts(conn, user_id, balances)
    return balances


def adjust_stored_account_balance(
    conn: sqlite3.Connection,
    user_id: int,
    account_id: int,
    delta: float,
    *,
    require_funds: bool = False,
) -> dict[str, float]:
    return apply_stored_account_deltas(
        conn,
        user_id,
        {int(account_id): float(delta)},
        require_funds_for={int(account_id)} if require_funds else set(),
    )


def _etf_plan_due_day(today: datetime, configured_day: int) -> int:
    """31. wird in kurzen Monaten fair als letzter Kalendertag behandelt."""
    first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_day = (first_next - timedelta(days=1)).day
    return min(max(1, int(configured_day)), last_day)


def record_due_etf_plan(conn: sqlite3.Connection, user_id: int, *, force: bool = False) -> dict:
    """Erfasst einen ETF-Sparplan einmal pro Monat in Rov.E, nie bei Bank oder Broker."""
    ensure_app_etf_savings_plan_table(conn)
    ensure_app_etf_position_plans_table(conn)
    ensure_investment_contribution_schema(conn)
    user = conn.execute(
        "SELECT current_investments, etf_savings FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not user:
        return {"ok": False, "error": "user_not_found"}
    amount = round(float(user["etf_savings"] or 0), 2)
    plan = conn.execute(
        """SELECT execution_day, source_account, source_account_id, mode, active, start_month
             FROM app_etf_savings_plan WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    if amount <= 0:
        return {"ok": False, "error": "etf_plan_not_configured"}

    now = datetime.now()
    month_key = now.strftime("%Y-%m")
    has_holdings = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_holdings'"
    ).fetchone()
    position_rows = []
    if has_holdings:
        position_rows = conn.execute(
            """SELECT pp.holding_id, pp.monthly_amount, pp.execution_day,
                      pp.source_account, pp.source_account_id, pp.mode,
                      ph.instrument_label, COALESCE(ph.instrument_type, 'etf') AS instrument_type
                 FROM app_etf_position_plans pp
                 JOIN portfolio_holdings ph
                   ON ph.id = pp.holding_id AND ph.user_id = pp.user_id
                WHERE pp.user_id = ? AND pp.active = 1 AND pp.monthly_amount > 0
                  AND pp.start_month <= ?
                ORDER BY pp.holding_id""",
            (user_id, month_key),
        ).fetchall()
    position_total = round(sum(float(row["monthly_amount"] or 0) for row in position_rows), 2)
    positions_match = bool(position_rows) and abs(position_total - amount) < 0.005

    allocations: list[dict] = []
    if positions_match:
        for row in position_rows:
            if str(row["instrument_type"]).lower() != "etf":
                return {"ok": False, "error": "etf_position_plan_invalid"}
            if not force:
                if str(row["mode"]) != "auto":
                    continue
                if now.day < _etf_plan_due_day(now, int(row["execution_day"])):
                    continue
            already = conn.execute(
                """SELECT 1 FROM investment_events
                     WHERE user_id = ? AND holding_id = ?
                       AND source = 'app_etf_plan' AND asset_type = 'etf'
                       AND event_type IN ('recurring_plan', 'recurring_plan_pending')
                       AND strftime('%Y-%m', created_at) = ? LIMIT 1""",
                (user_id, int(row["holding_id"]), month_key),
            ).fetchone()
            if already:
                continue
            allocations.append({
                "holding_id": int(row["holding_id"]),
                "name": str(row["instrument_label"]),
                "amount": round(float(row["monthly_amount"] or 0), 2),
                "source": str(row["source_account"]),
                "source_id": int(row["source_account_id"] or 0),
            })
        if not allocations:
            recorded = conn.execute(
                """SELECT 1 FROM investment_events
                     WHERE user_id = ? AND holding_id IS NOT NULL
                       AND source = 'app_etf_plan' AND asset_type = 'etf'
                       AND event_type IN ('recurring_plan', 'recurring_plan_pending')
                       AND strftime('%Y-%m', created_at) = ? LIMIT 1""",
                (user_id, month_key),
            ).fetchone()
            if recorded:
                return {"ok": True, "alreadyRecorded": True, "amount": 0.0}
            if not force and any(str(row["mode"]) != "auto" for row in position_rows):
                return {"ok": False, "error": "etf_plan_needs_confirmation"}
            return {"ok": False, "error": "etf_plan_not_due"}
    else:
        if not plan:
            return {"ok": False, "error": "etf_plan_not_configured"}
        if not bool(plan["active"]):
            return {"ok": False, "error": "etf_plan_paused"}
        if str(plan["start_month"]) > month_key:
            return {"ok": False, "error": "etf_plan_starts_later"}
        if not force:
            if str(plan["mode"]) != "auto":
                return {"ok": False, "error": "etf_plan_needs_confirmation"}
            if now.day < _etf_plan_due_day(now, int(plan["execution_day"])):
                return {"ok": False, "error": "etf_plan_not_due"}
        exists = conn.execute(
            """SELECT 1 FROM investment_events
                 WHERE user_id = ? AND holding_id IS NULL
                   AND source = 'app_etf_plan' AND asset_type = 'etf'
                   AND strftime('%Y-%m', created_at) = ? LIMIT 1""",
            (user_id, month_key),
        ).fetchone()
        if exists:
            return {"ok": True, "alreadyRecorded": True, "amount": amount}
        allocations = [{
            "holding_id": None,
            "name": "ETF-Sparplan",
            "amount": amount,
            "source": str(plan["source_account"]),
            "source_id": int(plan["source_account_id"] or 0),
        }]

    total_amount = round(sum(item["amount"] for item in allocations), 2)
    if multi_cash_accounts_enabled(conn, user_id):
        prepare_multi_cash_write(conn)
        deltas: dict[int, float] = {}
        require_funds_for: set[int] = set()
        for item in allocations:
            source = item["source"]
            source_id = int(item["source_id"] or 0)
            if source_id:
                source_account = require_financial_account(conn, user_id, source_id)
                account_type = str(source_account["account_type"])
                expected_source = "giro" if account_type == "checking" else "tagesgeld"
                if account_type not in {"checking", "savings"} or expected_source != source:
                    raise ValueError("etf_plan_source_account_mismatch")
            else:
                source_id = legacy_financial_account_id(conn, user_id, source)
                table = "app_etf_position_plans" if item["holding_id"] else "app_etf_savings_plan"
                condition = "user_id = ? AND holding_id = ?" if item["holding_id"] else "user_id = ?"
                params = (source_id, user_id, item["holding_id"]) if item["holding_id"] else (source_id, user_id)
                conn.execute(
                    f"UPDATE {table} SET source_account_id = ?, updated_at = CURRENT_TIMESTAMP WHERE {condition}",
                    params,
                )
                item["source_id"] = source_id
            deltas[source_id] = round(deltas.get(source_id, 0.0) - item["amount"], 2)
            if source == "tagesgeld":
                require_funds_for.add(source_id)
        try:
            balances = apply_financial_account_deltas(
                conn, user_id, deltas, require_funds_for=require_funds_for
            )
        except ValueError as exc:
            if str(exc) == "financial_account_balance_insufficient":
                return {"ok": False, "error": "etf_plan_source_insufficient"}
            raise
    else:
        balances = app_cash_accounts(conn, user_id)
        by_source = {"giro": 0.0, "tagesgeld": 0.0}
        for item in allocations:
            by_source[item["source"]] = round(by_source[item["source"]] + item["amount"], 2)
        if balances["tagesgeld"] + 0.009 < by_source["tagesgeld"]:
            return {"ok": False, "error": "etf_plan_source_insufficient"}
        balances["giro"] = round(balances["giro"] - by_source["giro"], 2)
        balances["tagesgeld"] = round(balances["tagesgeld"] - by_source["tagesgeld"], 2)
        save_app_cash_accounts(conn, user_id, balances)

    investments = round(float(user["current_investments"] or 0) + total_amount, 2)
    conn.execute("UPDATE users SET current_investments = ? WHERE user_id = ?", (investments, user_id))
    for item in allocations:
        booking_note = (
            f"Automatisch in Rov.E erfasst · Quelle: {item['source']}"
            if not force else f"In Rov.E erfasst · Quelle: {item['source']}"
        )
        if item["holding_id"]:
            record_holding_contribution(
                conn, user_id, item["holding_id"], item["amount"], note=booking_note
            )
        else:
            conn.execute(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, event_type,
                        source, note, holding_id)
                   VALUES (?, ?, 'in', 'etf', 'ETF-Sparplan', 'recurring_plan',
                           'app_etf_plan', ?, NULL)""",
                (user_id, item["amount"], booking_note),
            )
    conn.execute(
        """INSERT INTO portfolio_snapshots (user_id, amount, scope, source, note)
           VALUES (?, ?, 'investments', 'app_etf_plan', 'Stand nach ETF-Sparplan')""",
        (user_id, investments),
    )
    conn.execute(
        """INSERT INTO portfolio_snapshots (user_id, amount, scope, source, note)
           VALUES (?, ?, 'cash', 'app_etf_plan', 'Stand nach ETF-Sparplan')""",
        (user_id, round(sum(balances.values()), 2)),
    )
    return {
        "ok": True,
        "amount": total_amount,
        "source": allocations[0]["source"] if len(allocations) == 1 else "multiple",
        "allocations": [
            {"holdingId": item["holding_id"], "name": item["name"], "amount": item["amount"]}
            for item in allocations
        ],
    }


@app.route("/v1/pair", methods=["POST"])
def pair_app():
    """Retired: pairing codes previously yielded public bearer state links."""
    return jsonify({"ok": False, "error": "pairing_retired"}), 410


@app.route("/v1/transactions", methods=["GET"])
def current_transactions():
    """Liest die aktuellen Monatsbuchungen fuer eine bereits gekoppelte App."""
    token = token_from_request()
    with db() as conn:
        # This endpoint also activates due scheduled savings. Serialize the
        # read-modify-delete path so concurrent GETs cannot materialize twice.
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        apply_due_scheduled_savings(conn, user_id)
        tx = _build_tx(conn, user_id)

    return jsonify({"ok": True, "tx": tx})


@app.route("/v1/state", methods=["GET"])
def current_app_state():
    """Aktualisiert die vom Bot gefuehrten Bereiche einer gekoppelten App."""
    token = token_from_request()
    with db() as conn:
        ensure_market_tracking_schema(conn)
        conn.commit()
        begin_write(conn)
        # Retire every legacy bearer row on first API-state access. These rows are
        # independent from app_sessions, so existing browser sessions keep working.
        revoke_legacy_state_links(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        # Eine fuer diesen Monat vorgemerkte Rate muss VOR dem ETF-Lauf aktiv werden.
        # Sonst wuerde am Monatsersten noch einmal der alte Betrag gebucht.
        apply_due_scheduled_savings(conn, user_id)
        # Ein automatischer ETF-Sparplan wird beim ersten sicheren App-Kontakt am
        # Ausfuehrungstag erfasst. Ohne Broker-API bleibt das ausschliesslich die
        # interne Rov.E-Abbildung; die echte Order wird dadurch nie behauptet.
        record_due_etf_plan(conn, user_id)
        state = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, **state})


@app.route("/v1/admin/overview", methods=["GET"])
def admin_overview():
    """Kompakte Betriebsdaten fuer das mobile Admin-Kontrollzentrum."""
    with db() as conn:
        token_user_id, auth_error = authenticated_admin(conn)
        if auth_error:
            return auth_error

        integrity_row = conn.execute("PRAGMA quick_check(1)").fetchone()
        database_ok = bool(integrity_row and str(integrity_row[0]).lower() == "ok")
        access_rows = conn.execute(
            "SELECT status, COUNT(*) FROM user_access GROUP BY status"
        ).fetchall() if table_exists(conn, "user_access") else []
        access_counts = {str(row[0]): int(row[1]) for row in access_rows}
        report_rows = conn.execute(
            "SELECT status, COUNT(*) FROM report_jobs GROUP BY status"
        ).fetchall() if table_exists(conn, "report_jobs") else []
        report_counts = {str(row[0]): int(row[1]) for row in report_rows}

        accounts_total = scalar_count(conn, "SELECT COUNT(*) FROM app_accounts")
        accounts_new_7d = scalar_count(
            conn,
            "SELECT COUNT(*) FROM app_accounts WHERE datetime(created_at) >= datetime('now', '-7 days')",
        )
        expenses_today = scalar_count(
            conn, "SELECT COUNT(*) FROM expenses WHERE date(created_at) = date('now', 'localtime')"
        )
        expenses_month = scalar_count(
            conn,
            "SELECT COUNT(*) FROM expenses WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')",
        )
        push_devices = scalar_count(
            conn, "SELECT COUNT(*) FROM app_push_subscriptions"
        ) if table_exists(conn, "app_push_subscriptions") else 0
        overdue_reports = scalar_count(
            conn,
            """SELECT COUNT(*) FROM report_jobs
                 WHERE status = 'pending' AND datetime(scheduled_at) < datetime('now', '-30 minutes')""",
        ) if table_exists(conn, "report_jobs") else 0
        report_failures = sum(
            count for status, count in report_counts.items()
            if status.lower() in {"failed", "error"}
        )
        invitation_rows = conn.execute(
            """SELECT id, email, created_at, expires_at
                 FROM app_invitations
                WHERE consumed_at IS NULL
                  AND datetime(expires_at) >= datetime('now', 'localtime')
                ORDER BY datetime(created_at) DESC LIMIT 30"""
        ).fetchall()
        managed_rows = conn.execute(
            """SELECT ua.user_id, ua.status, ua.requested_at, ua.approved_at,
                      ua.display_name AS access_name, ua.username,
                      aa.email, aa.display_name AS account_name, aa.source
                 FROM user_access ua
                 LEFT JOIN app_accounts aa ON aa.id = (
                     SELECT MAX(a2.id) FROM app_accounts a2 WHERE a2.user_id = ua.user_id
                 )
                WHERE ua.status IN ('pending', 'approved', 'app_only', 'revoked')
                ORDER BY CASE ua.status WHEN 'pending' THEN 0 WHEN 'revoked' THEN 1 ELSE 2 END,
                         datetime(COALESCE(NULLIF(ua.requested_at, ''), ua.approved_at)) DESC
                LIMIT 60"""
        ).fetchall()

        invitations = [
            {
                "id": int(row["id"]),
                "email": str(row["email"] or ""),
                "createdAt": str(row["created_at"] or ""),
                "expiresAt": str(row["expires_at"] or ""),
            }
            for row in invitation_rows
        ]
        managed_users = []
        for row in managed_rows:
            display_name = str(row["account_name"] or row["access_name"] or "").strip()
            username = str(row["username"] or "").strip()
            email = str(row["email"] or "").strip()
            managed_users.append({
                "userId": int(row["user_id"]),
                "status": str(row["status"] or "pending"),
                "name": display_name or username or email or "Unbekannt",
                "email": email,
                "source": str(row["source"] or ("telegram" if username else "")),
                "requestedAt": str(row["requested_at"] or ""),
                "isSelf": int(row["user_id"]) == token_user_id,
            })

    backup_dir = APP_DIR / "backups" / "automatic"
    backups = sorted(backup_dir.glob("*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    latest_backup = backups[0] if backups else None
    backup_age_hours = (
        round((time.time() - latest_backup.stat().st_mtime) / 3600, 1)
        if latest_backup else None
    )
    disk = shutil.disk_usage(APP_DIR)
    disk_used_percent = round((disk.used / disk.total) * 100, 1) if disk.total else 0.0

    alerts = []
    if not database_ok:
        alerts.append({"level": "critical", "title": "Datenbankprüfung fehlgeschlagen"})
    if latest_backup is None or (backup_age_hours is not None and backup_age_hours > 30):
        alerts.append({"level": "critical", "title": "Automatisches Backup ist überfällig"})
    if report_failures:
        alerts.append({"level": "critical", "title": f"{report_failures} Report-Job(s) fehlgeschlagen"})
    if overdue_reports:
        alerts.append({"level": "warning", "title": f"{overdue_reports} Report-Job(s) überfällig"})
    if disk_used_percent >= 85:
        alerts.append({"level": "critical", "title": f"Speicherplatz zu {disk_used_percent:g} % belegt"})
    elif disk_used_percent >= 75:
        alerts.append({"level": "warning", "title": f"Speicherplatz zu {disk_used_percent:g} % belegt"})

    return jsonify({
        "ok": True,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "health": {
            "api": True,
            "database": database_ok,
            "backup": bool(latest_backup and backup_age_hours is not None and backup_age_hours <= 30),
            "diskUsedPercent": disk_used_percent,
        },
        "users": {
            "accounts": accounts_total,
            "new7d": accounts_new_7d,
            "active": sum(access_counts.get(status, 0) for status in ("approved", "app_only")),
            "status": access_counts,
            "pushDevices": push_devices,
            "managed": managed_users,
        },
        "invitations": invitations,
        "activity": {"expensesToday": expenses_today, "expensesMonth": expenses_month},
        "reports": {"status": report_counts, "failed": report_failures, "overdue": overdue_reports},
        "backup": {
            "available": latest_backup is not None,
            "ageHours": backup_age_hours,
            "file": latest_backup.name if latest_backup else "",
        },
        "alerts": alerts,
    })


@app.route("/v1/admin/invitations", methods=["POST"])
def admin_create_invitation():
    """Bereitet einen zeitlich begrenzten App-only Beta-Zugang vor."""
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    try:
        valid_days = int(payload.get("days", 14))
    except (TypeError, ValueError):
        valid_days = 14
    if not email:
        return jsonify({"ok": False, "error": "valid_email_required"}), 400
    if not 1 <= valid_days <= 90:
        return jsonify({"ok": False, "error": "invalid_invitation_days"}), 400

    with db() as conn:
        admin_user_id, auth_error = authenticated_admin(conn)
        if auth_error:
            return auth_error
        if conn.execute("SELECT 1 FROM app_accounts WHERE email = ?", (email,)).fetchone():
            return jsonify({"ok": False, "error": "account_already_exists"}), 409
        expires_at = (datetime.now() + timedelta(days=valid_days)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO app_invitations (email, expires_at, consumed_at)
               VALUES (?, ?, NULL)
               ON CONFLICT(email) DO UPDATE SET
                   created_at = CURRENT_TIMESTAMP,
                   expires_at = excluded.expires_at,
                   consumed_at = NULL""",
            (email, expires_at),
        )
        row = conn.execute(
            "SELECT id, created_at FROM app_invitations WHERE email = ?", (email,)
        ).fetchone()
        conn.execute(
            """INSERT INTO app_admin_events
               (admin_user_id, action, target_email, details)
               VALUES (?, 'invitation_created', ?, ?)""",
            (admin_user_id, email, json.dumps({"days": valid_days})),
        )
        conn.commit()
    return jsonify({
        "ok": True,
        "invitation": {
            "id": int(row["id"]), "email": email,
            "createdAt": str(row["created_at"] or ""), "expiresAt": expires_at,
        },
    })


@app.route("/v1/admin/invitations/<int:invitation_id>", methods=["DELETE"])
def admin_delete_invitation(invitation_id: int):
    """Zieht eine noch nicht verwendete Beta-Einladung zurueck."""
    with db() as conn:
        admin_user_id, auth_error = authenticated_admin(conn)
        if auth_error:
            return auth_error
        row = conn.execute(
            "SELECT email, consumed_at FROM app_invitations WHERE id = ?", (invitation_id,)
        ).fetchone()
        if not row or row["consumed_at"]:
            return jsonify({"ok": False, "error": "invitation_not_found"}), 404
        conn.execute("DELETE FROM app_invitations WHERE id = ?", (invitation_id,))
        conn.execute(
            """INSERT INTO app_admin_events
               (admin_user_id, action, target_email)
               VALUES (?, 'invitation_withdrawn', ?)""",
            (admin_user_id, str(row["email"] or "")),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/v1/admin/access/<int:target_user_id>", methods=["POST"])
def admin_update_access(target_user_id: int):
    """Gibt einen vorhandenen Zugang frei oder sperrt ihn mit echter API-Wirkung."""
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    if action not in {"approve", "revoke"}:
        return jsonify({"ok": False, "error": "invalid_admin_action"}), 400

    with db() as conn:
        admin_user_id, auth_error = authenticated_admin(conn)
        if auth_error:
            return auth_error
        if target_user_id == admin_user_id:
            return jsonify({"ok": False, "error": "cannot_change_own_access"}), 409
        target = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (target_user_id,)
        ).fetchone()
        if not target:
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        account = conn.execute(
            """SELECT email, source FROM app_accounts
                 WHERE user_id = ? ORDER BY id DESC LIMIT 1""", (target_user_id,)
        ).fetchone()
        email = str(account["email"] or "") if account else ""

        if action == "approve":
            next_status = "app_only" if account and str(account["source"] or "") == "app" else "approved"
            conn.execute(
                """INSERT INTO user_access (user_id, status, approved_at, approved_by, revoked_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP, ?, '')
                   ON CONFLICT(user_id) DO UPDATE SET
                       status = excluded.status,
                       approved_at = excluded.approved_at,
                       approved_by = excluded.approved_by,
                       revoked_at = ''""",
                (target_user_id, next_status, admin_user_id),
            )
        else:
            conn.execute(
                """INSERT INTO user_access (user_id, status, revoked_at, approved_by)
                   VALUES (?, 'revoked', CURRENT_TIMESTAMP, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       status = 'revoked',
                       revoked_at = CURRENT_TIMESTAMP,
                       approved_by = excluded.approved_by""",
                (target_user_id, admin_user_id),
            )
            conn.execute(
                """UPDATE app_sessions SET revoked_at = CURRENT_TIMESTAMP
                    WHERE account_id IN (SELECT id FROM app_accounts WHERE user_id = ?)
                      AND revoked_at IS NULL""",
                (target_user_id,),
            )
            conn.execute(
                "UPDATE app_state_links SET status = 'revoked' WHERE user_id = ? AND status = 'active'",
                (target_user_id,),
            )
            if table_exists(conn, "app_push_subscriptions"):
                conn.execute("DELETE FROM app_push_subscriptions WHERE user_id = ?", (target_user_id,))

        conn.execute(
            """INSERT INTO app_admin_events
               (admin_user_id, action, target_user_id, target_email)
               VALUES (?, ?, ?, ?)""",
            (admin_user_id, f"access_{action}", target_user_id, email),
        )
        conn.commit()
    return jsonify({"ok": True, "status": next_status if action == "approve" else "revoked"})


@app.route("/v1/monthly-plan", methods=["POST"])
def update_monthly_plan():
    """Bestaetigt oder oeffnet Monatsplan-Posten, ohne Kontobewegungen zu erfinden."""
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    field_by_action = {
        "confirm_income": ("income_status", "confirmed"),
        "confirm_fixed_costs": ("fixed_costs_status", "confirmed"),
        "confirm_savings": ("savings_status", "confirmed"),
        "reopen_income": ("income_status", "planned"),
        "reopen_fixed_costs": ("fixed_costs_status", "planned"),
        "reopen_savings": ("savings_status", "planned"),
    }
    if action not in field_by_action:
        return jsonify({"ok": False, "error": "valid_monthly_plan_action_required"}), 400

    token = token_from_request()
    with db() as conn:
        begin_write(conn)   # bucht die Cash-Sparrate aufs Tagesgeld — siehe begin_write()
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        # Falls eine neue Rate zum Monatswechsel faellig wurde, muss der
        # Monatscheck direkt mit ihr rechnen, auch ohne vorherigen App-Refresh.
        apply_due_scheduled_savings(conn, user_id)
        ensure_app_monthly_plan_table(conn)
        ensure_app_cash_movements_table(conn)
        ensure_financial_account_reference_schema(conn)
        pilot = multi_cash_accounts_enabled(conn, user_id)
        if pilot:
            prepare_multi_cash_write(conn)
        month_key = datetime.now().strftime("%Y-%m")
        field, status = field_by_action[action]

        # ===== Gehalt und Fixkosten wirklich buchen (27.07.) =====
        # Bis hierher setzten `confirm_income` und `confirm_fixed_costs` NUR einen Status —
        # `confirm_savings` bucht dagegen seit jeher echtes Geld. Diese Asymmetrie war die
        # Ursache dafuer, dass der Kontostand nur fallen konnte: Ausgaben gingen ab, aber es
        # kam nie etwas rein (Furkan-Fund 27.07.). Der Bot hat NIE ein Gehalt gebucht, es gibt
        # in bot.py ueberhaupt keine Zahltag-Logik — `users.income` ist reiner Planungswert.
        #
        # Doppelbuchungssperre wie bei confirm_savings: gepruefft wird die Bewegung selbst, nicht
        # der Status. Ein Status kann von der Wahrheit abweichen (Nutzer loescht die Buchung
        # wieder), die Bewegung nicht. Reopen + erneutes Bestaetigen bucht deshalb nicht doppelt,
        # solange die Bewegung existiert — und bucht korrekt neu, wenn sie geloescht wurde.
        # „Doch noch nicht da" / „Doch noch nicht abgebucht" muss die Buchung wirklich zuruecknehmen
        # (Furkan-Fund 27.07.: der Knopf stellte nur den Status um, das Geld blieb — er musste die
        # Zeile von Hand im Cashflow loeschen). Rueckgaengig heisst rueckgaengig, sonst ist der
        # Knopf eine Luege.
        if action in ("reopen_income", "reopen_fixed_costs", "reopen_savings"):
            if action == "reopen_income":
                zeile = conn.execute(
                    """SELECT id, amount, source_account_id, target_account_id
                         FROM app_cash_movements
                         WHERE user_id = ? AND kind = 'income'
                           AND strftime('%Y-%m', created_at) = ?
                           AND lower(COALESCE(label, '')) LIKE '%gehalt%'
                         ORDER BY id DESC LIMIT 1""",
                    (user_id, month_key),
                ).fetchone()
                richtung = -1        # Gehalt zurueckgenommen: Geld verlaesst das Giro wieder
            elif action == "reopen_fixed_costs":
                zeile = conn.execute(
                    """SELECT id, amount, source_account_id, target_account_id
                         FROM app_cash_movements
                         WHERE user_id = ? AND kind = 'fixed'
                           AND strftime('%Y-%m', created_at) = ?
                         ORDER BY id DESC LIMIT 1""",
                    (user_id, month_key),
                ).fetchone()
                richtung = 1         # Fixkosten zurueckgenommen: Geld kommt aufs Giro zurueck
            else:
                # Sparrate ist keine neue Einnahme. Sie verschiebt bereits vorhandenes Geld
                # vom Giro in ETF/Investments bzw. Tagesgeld. Beim Rueckgaengigmachen muss
                # dieselbe Umschichtung exakt andersherum laufen, sonst bleibt Vermoegen
                # kuenstlich erzeugt im System stehen.
                rows = conn.execute(
                    """SELECT asset_type, COALESCE(SUM(amount), 0) AS amount
                         FROM investment_events
                        WHERE user_id = ?
                          AND source = 'app_monthly_plan'
                          AND strftime('%Y-%m', created_at) = ?
                          AND asset_type IN ('etf', 'cash')
                        GROUP BY asset_type""",
                    (user_id, month_key),
                ).fetchall()
                savings = {str(row["asset_type"]): round(float(row["amount"] or 0), 2) for row in rows}
                etf_amount = savings.get("etf", 0.0)
                cash_amount = savings.get("cash", 0.0)
                if etf_amount or cash_amount:
                    user = conn.execute(
                        "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
                    ).fetchone()
                    investments = round(float(user["current_investments"] or 0), 2) if user else 0.0
                    balances = app_cash_accounts(conn, user_id)
                    # Keine Korrektur auf Verdacht: Wurden Tagesgeld oder Investments danach
                    # bereits separat vermindert, wuerde eine automatische Rueckbuchung Geld
                    # erfinden. Dann bleibt die bestaetigte Sparrate stehen, bis der Nutzer
                    # die spaetere Aenderung zuerst geklaert hat.
                    if investments + 0.009 < etf_amount or balances["tagesgeld"] + 0.009 < cash_amount:
                        return jsonify({"ok": False, "error": "savings_already_moved"}), 400
                    if pilot:
                        giro_id = legacy_financial_account_id(conn, user_id, "giro")
                        tagesgeld_id = legacy_financial_account_id(conn, user_id, "tagesgeld")
                        balances = apply_financial_account_deltas(
                            conn,
                            user_id,
                            {
                                giro_id: etf_amount + cash_amount,
                                tagesgeld_id: -cash_amount,
                            },
                            require_funds_for={tagesgeld_id},
                        )
                    else:
                        balances["giro"] = round(balances["giro"] + etf_amount + cash_amount, 2)
                        balances["tagesgeld"] = round(balances["tagesgeld"] - cash_amount, 2)
                        save_app_cash_accounts(conn, user_id, balances)
                    conn.execute(
                        "UPDATE users SET current_investments = ? WHERE user_id = ?",
                        (round(investments - etf_amount, 2), user_id),
                    )
                    conn.execute(
                        """DELETE FROM investment_events
                             WHERE user_id = ?
                               AND source = 'app_monthly_plan'
                               AND strftime('%Y-%m', created_at) = ?
                               AND asset_type IN ('etf', 'cash')""",
                        (user_id, month_key),
                    )
                    conn.execute(
                        """DELETE FROM portfolio_snapshots
                             WHERE user_id = ?
                               AND source = 'app_monthly_plan'
                               AND strftime('%Y-%m', created_at) = ?""",
                        (user_id, month_key),
                    )
                zeile = None
                richtung = 0
            if zeile:
                betrag = round(abs(float(zeile["amount"] or 0)), 2)
                saved_account_id = (
                    int(zeile["target_account_id"] or 0)
                    if action == "reopen_income"
                    else int(zeile["source_account_id"] or 0)
                )
                if saved_account_id:
                    balances = adjust_stored_account_balance(
                        conn, user_id, saved_account_id, richtung * betrag
                    )
                else:
                    balances = app_cash_accounts(conn, user_id)
                    balances["giro"] = round(balances["giro"] + richtung * betrag, 2)
                    save_app_cash_accounts(conn, user_id, balances)
                conn.execute(
                    "DELETE FROM app_cash_movements WHERE id = ? AND user_id = ?",
                    (zeile["id"], user_id),
                )

        if action in ("confirm_income", "confirm_fixed_costs"):
            user = conn.execute(
                "SELECT income, other_income, fixed_costs FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            balances = app_cash_accounts(conn, user_id)   # unter der Sperre aus begin_write()

            if action == "confirm_income":
                betrag = round(float(user["income"] or 0) + float(user["other_income"] or 0), 2) if user else 0.0
                schon_da = conn.execute(
                    """SELECT 1 FROM app_cash_movements
                         WHERE user_id = ? AND kind = 'income'
                           AND strftime('%Y-%m', created_at) = ?
                           AND lower(COALESCE(label, '')) LIKE '%gehalt%'
                         LIMIT 1""",
                    (user_id, month_key),
                ).fetchone()
                if not schon_da and betrag > 0:
                    if pilot:
                        target_account_id = role_financial_account_id(conn, user_id, "income")
                        balances = adjust_financial_account_balance(
                            conn, user_id, target_account_id, betrag
                        )
                        conn.execute(
                            """INSERT INTO app_cash_movements
                                   (user_id, kind, amount, label, target_account_id)
                               VALUES (?, 'income', ?, 'Gehalt', ?)""",
                            (user_id, betrag, target_account_id),
                        )
                    else:
                        balances["giro"] = round(balances["giro"] + betrag, 2)
                        save_app_cash_accounts(conn, user_id, balances)
                        conn.execute(
                            """INSERT INTO app_cash_movements (user_id, kind, amount, label)
                               VALUES (?, 'income', ?, 'Gehalt')""",
                            (user_id, betrag),
                        )
            else:
                betrag = round(float(user["fixed_costs"] or 0), 2) if user else 0.0
                schon_da = conn.execute(
                    """SELECT 1 FROM app_cash_movements
                         WHERE user_id = ? AND kind = 'fixed'
                           AND strftime('%Y-%m', created_at) = ?
                         LIMIT 1""",
                    (user_id, month_key),
                ).fetchone()
                if not schon_da and betrag > 0:
                    # Bewusst OHNE Deckungspruefung: die Abbuchung hat real stattgefunden, der
                    # Nutzer bestaetigt sie nur. Wir erfinden kein Geld und blockieren auch nicht
                    # die Wahrheit, wenn das Konto knapp ist — gleiche Haltung wie beim Loeschen
                    # einer Einnahme.
                    if pilot:
                        source_account_id = role_financial_account_id(conn, user_id, "fixed_cost")
                        balances = adjust_financial_account_balance(
                            conn, user_id, source_account_id, -betrag
                        )
                        conn.execute(
                            """INSERT INTO app_cash_movements
                                   (user_id, kind, amount, label, source_account_id)
                               VALUES (?, 'fixed', ?, 'Fixkosten', ?)""",
                            (user_id, betrag, source_account_id),
                        )
                    else:
                        balances["giro"] = round(balances["giro"] - betrag, 2)
                        save_app_cash_accounts(conn, user_id, balances)
                        conn.execute(
                            """INSERT INTO app_cash_movements (user_id, kind, amount, label)
                               VALUES (?, 'fixed', ?, 'Fixkosten')""",
                            (user_id, betrag),
                        )
        conn.execute(
            """INSERT INTO app_monthly_plan_status (user_id, month_key)
               VALUES (?, ?)
               ON CONFLICT(user_id, month_key) DO NOTHING""",
            (user_id, month_key),
        )
        if action == "confirm_savings":
            user = conn.execute(
                """SELECT current_investments, etf_savings, cash_savings
                     FROM users WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
            etf_savings = round(float(user["etf_savings"] or 0), 2) if user else 0.0
            cash_savings = round(float(user["cash_savings"] or 0), 2) if user else 0.0
            # Sobald ein ETF-Plan eingerichtet ist, gehoert ETF nicht mehr in den
            # gemeinsamen Monatscheck. Sonst wuerde eine flexible Cash-Bestaetigung den
            # ETF-Sparplan doppelt oder zum falschen Zeitpunkt buchen.
            etf_plan = get_app_etf_savings_plan(conn, user_id, etf_savings)
            etf_to_book = 0.0 if etf_plan.get("configured") else etf_savings
            already_confirmed = conn.execute(
                """SELECT 1 FROM investment_events
                     WHERE user_id = ?
                       AND source IN ('investiert_command', 'app_monthly_plan')
                       AND strftime('%Y-%m', created_at) = ?
                     LIMIT 1""",
                (user_id, month_key),
            ).fetchone()
            if not already_confirmed and (etf_to_book > 0 or cash_savings > 0):
                new_investments = round(float(user["current_investments"] or 0) + etf_to_book, 2)
                conn.execute(
                    "UPDATE users SET current_investments = ? WHERE user_id = ?",
                    (new_investments, user_id),
                )
                balances = app_cash_accounts(conn, user_id)
                # Sparen ist eine Umschichtung, keine zweite Einnahme: Der Betrag verlaesst
                # das Girokonto. ETF wandert ins Investment, Cash ins Tagesgeld. Dadurch bleibt
                # das Nettovermoegen gleich und nur seine Verteilung aendert sich.
                if pilot:
                    giro_id = legacy_financial_account_id(conn, user_id, "giro")
                    tagesgeld_id = legacy_financial_account_id(conn, user_id, "tagesgeld")
                    balances = apply_financial_account_deltas(
                        conn,
                        user_id,
                        {
                            giro_id: -(etf_to_book + cash_savings),
                            tagesgeld_id: cash_savings,
                        },
                    )
                else:
                    balances["giro"] = round(balances["giro"] - etf_to_book - cash_savings, 2)
                    balances["tagesgeld"] = round(balances["tagesgeld"] + cash_savings, 2)
                    save_app_cash_accounts(conn, user_id, balances)
                if etf_to_book > 0:
                    conn.execute(
                        """INSERT INTO investment_events
                           (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                           VALUES (?, ?, 'in', 'etf', 'ETF-Sparrate', 'recurring_plan', 'app_monthly_plan',
                                   'Monatliche ETF-Sparrate in der App bestätigt')""",
                        (user_id, etf_to_book),
                    )
                if cash_savings > 0:
                    conn.execute(
                        """INSERT INTO investment_events
                           (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                           VALUES (?, ?, 'in', 'cash', 'Cash-Sparrate', 'recurring_plan', 'app_monthly_plan',
                                   'Monatliche Cash-Sparrate in der App bestätigt')""",
                        (user_id, cash_savings),
                    )
                conn.execute(
                    """INSERT INTO portfolio_snapshots (user_id, amount, scope, source, note)
                       VALUES (?, ?, 'investments', 'app_monthly_plan', 'Stand nach Sparrate')""",
                    (user_id, new_investments),
                )
                conn.execute(
                    """INSERT INTO portfolio_snapshots (user_id, amount, scope, source, note)
                       VALUES (?, ?, 'cash', 'app_monthly_plan', 'Stand nach Sparrate')""",
                    (user_id, round(sum(balances.values()), 2)),
                )
        conn.execute(
            f"""UPDATE app_monthly_plan_status
                   SET {field} = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE user_id = ? AND month_key = ?""",
            (status, user_id, month_key),
        )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, **live_data})


@app.route("/v1/etf-plan", methods=["POST", "OPTIONS"])
def update_etf_plan():
    """Pausiert, aktiviert oder bestaetigt den getrennten ETF-Sparplan."""
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    if action not in {"pause", "resume", "execute"}:
        return jsonify({"ok": False, "error": "valid_etf_plan_action_required"}), 400

    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_app_etf_savings_plan_table(conn)
        if action in {"pause", "resume"}:
            row = conn.execute(
                "SELECT 1 FROM app_etf_savings_plan WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "etf_plan_not_configured"}), 400
            conn.execute(
                "UPDATE app_etf_savings_plan SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (1 if action == "resume" else 0, user_id),
            )
        else:
            apply_due_scheduled_savings(conn, user_id)
            result = record_due_etf_plan(conn, user_id, force=True)
            if not result.get("ok"):
                return jsonify(result), 400
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, **live_data})


# ===================== PUSH-BENACHRICHTIGUNGEN (27.07.) =====================
# Bewusst so gebaut, dass ein Deploy OHNE Schluessel und ohne Bibliothek nichts kaputtmacht:
# fehlt eines von beidem, meldet der Server `push.available = false`, die App blendet den Schalter
# gar nicht erst ein und der Versand ist ein stiller No-Op. Ein Schalter, der nichts tut, war
# genau der Fehler, den wir am 27.07. entfernt haben — der kommt nicht durch die Hintertuer zurueck.
VAPID_PUBLIC_KEY = os.getenv("ROVE_VAPID_PUBLIC", "").strip()
VAPID_PRIVATE_KEY = os.getenv("ROVE_VAPID_PRIVATE", "").strip()
VAPID_SUBJECT = os.getenv("ROVE_VAPID_SUBJECT", "mailto:info@getrove.de").strip()

try:
    from pywebpush import webpush, WebPushException   # type: ignore
    PUSH_LIB_OK = True
except Exception:                                     # Bibliothek nicht installiert
    webpush = None
    WebPushException = Exception
    PUSH_LIB_OK = False


def push_available() -> bool:
    return bool(PUSH_LIB_OK and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def ensure_push_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_push_subscriptions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            endpoint   TEXT NOT NULL UNIQUE,
            p256dh     TEXT NOT NULL,
            auth       TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subs_user ON app_push_subscriptions(user_id)"
    )


def ensure_push_preferences_table(conn: sqlite3.Connection) -> None:
    """Kleine nutzerbezogene Push-Praeferenz, getrennt von Geraete-Abonnements."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_push_preferences (
            user_id                    INTEGER PRIMARY KEY,
            tracking_reminder_enabled  INTEGER NOT NULL DEFAULT 0,
            timezone                   TEXT NOT NULL DEFAULT 'Europe/Berlin',
            updated_at                 TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def valid_push_timezone(value: object) -> str | None:
    timezone = str(value or "").strip()[:80]
    if not timezone:
        return None
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return timezone


def send_push_to_user(conn: sqlite3.Connection, user_id: int, title: str, body: str,
                      tag: str = "rove", url: str = "./") -> int:
    """Schickt eine Benachrichtigung an alle Geraete eines Nutzers. Gibt die Anzahl Zustellungen zurueck.

    Abgelaufene Abos (404/410 vom Push-Dienst) werden geloescht — sonst sammeln sich tote Endpunkte
    an und jeder Versand laeuft in Fehler. Der Rest wird geloggt, aber nie nach oben geworfen: eine
    fehlgeschlagene Benachrichtigung darf niemals eine Buchung oder einen Report scheitern lassen.
    """
    if not push_available():
        return 0
    try:
        ensure_push_table(conn)
        rows = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM app_push_subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0

    nutzlast = json.dumps({"title": title, "body": body, "tag": tag, "url": url})
    zugestellt = 0
    for row in rows:
        try:
            webpush(
                subscription_info={
                    "endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
                },
                data=nutzlast,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=10,
            )
            zugestellt += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                conn.execute("DELETE FROM app_push_subscriptions WHERE id = ?", (row["id"],))
            else:
                app.logger.warning("Push fehlgeschlagen (%s): %s", status, exc)
        except Exception as exc:
            app.logger.warning("Push fehlgeschlagen: %s", exc)
    return zugestellt


@app.route("/v1/push/key", methods=["OPTIONS"])
@app.route("/v1/push/subscribe", methods=["OPTIONS"])
@app.route("/v1/push/unsubscribe", methods=["OPTIONS"])
@app.route("/v1/push/preferences", methods=["OPTIONS"])
def push_options():
    return ("", 204)


@app.route("/v1/push/key", methods=["GET"])
def push_key():
    """Sagt der App, ob Push ueberhaupt eingerichtet ist — und wenn ja, mit welchem Schluessel.

    Bewusst ein eigener Endpunkt statt ein Feld im State: der oeffentliche Schluessel und die
    Verfuegbarkeit sind Server-Konfiguration, keine Nutzerdaten. So bleibt rove_app_state.py frei
    von Push-Wissen. Ohne Token abrufbar — der oeffentliche Schluessel ist per Definition oeffentlich.
    """
    return jsonify({"available": push_available(), "publicKey": VAPID_PUBLIC_KEY if push_available() else ""})


@app.route("/v1/push/subscribe", methods=["POST"])
def push_subscribe():
    """Merkt sich das Geraete-Abo. Mehrere Geraete pro Nutzer sind ausdruecklich erlaubt."""
    if not push_available():
        return jsonify({"ok": False, "error": "push_not_configured"}), 503
    payload = request.get_json(silent=True) or {}
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    timezone = valid_push_timezone(payload.get("timezone")) or "Europe/Berlin"
    tracking_reminder = payload.get("trackingReminder")
    if not endpoint or not p256dh or not auth:
        return jsonify({"ok": False, "error": "subscription_incomplete"}), 400

    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_push_table(conn)
        ensure_push_preferences_table(conn)
        # Derselbe Endpunkt kann nach einem Geraetewechsel einem anderen Konto gehoeren.
        conn.execute(
            """INSERT INTO app_push_subscriptions (user_id, endpoint, p256dh, auth)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth""",
            (user_id, endpoint, p256dh, auth),
        )
        existing = conn.execute(
            "SELECT tracking_reminder_enabled FROM app_push_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        enabled = int(bool(tracking_reminder)) if isinstance(tracking_reminder, bool) else int(existing[0] if existing else 0)
        conn.execute(
            """INSERT INTO app_push_preferences
                   (user_id, tracking_reminder_enabled, timezone, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   tracking_reminder_enabled = excluded.tracking_reminder_enabled,
                   timezone = excluded.timezone,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, enabled, timezone),
        )
        conn.commit()
    return jsonify({"ok": True, "trackingReminder": bool(enabled), "timezone": timezone})


@app.route("/v1/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    payload = request.get_json(silent=True) or {}
    endpoint = str(payload.get("endpoint") or "").strip()
    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_push_table(conn)
        if endpoint:
            conn.execute(
                "DELETE FROM app_push_subscriptions WHERE user_id = ? AND endpoint = ?",
                (user_id, endpoint),
            )
        else:
            conn.execute("DELETE FROM app_push_subscriptions WHERE user_id = ?", (user_id,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/v1/push/preferences", methods=["GET", "POST"])
def push_preferences():
    """Liest oder aendert optionale Push-Hinweise; das Push-Abo selbst bleibt separat."""
    payload = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
    token = token_from_request()
    with db() as conn:
        if request.method == "POST":
            begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_push_preferences_table(conn)
        row = conn.execute(
            "SELECT tracking_reminder_enabled, timezone FROM app_push_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        enabled = bool(row["tracking_reminder_enabled"]) if row else False
        timezone = str(row["timezone"] or "Europe/Berlin") if row else "Europe/Berlin"
        if request.method == "POST":
            if "trackingReminder" in payload and not isinstance(payload.get("trackingReminder"), bool):
                return jsonify({"ok": False, "error": "invalid_tracking_reminder"}), 400
            if "trackingReminder" in payload:
                enabled = bool(payload["trackingReminder"])
            if "timezone" in payload:
                supplied_timezone = valid_push_timezone(payload.get("timezone"))
                if not supplied_timezone:
                    return jsonify({"ok": False, "error": "invalid_timezone"}), 400
                timezone = supplied_timezone
            conn.execute(
                """INSERT INTO app_push_preferences
                       (user_id, tracking_reminder_enabled, timezone, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                       tracking_reminder_enabled = excluded.tracking_reminder_enabled,
                       timezone = excluded.timezone,
                       updated_at = CURRENT_TIMESTAMP""",
                (user_id, int(enabled), timezone),
            )
            conn.commit()
    return jsonify({"ok": True, "trackingReminder": enabled, "timezone": timezone})


@app.route("/v1/internal/push", methods=["POST"])
def internal_push():
    """Nimmt einen Push-Auftrag vom lokalen Monatsreport-Prozess entgegen."""
    supplied = request.headers.get("X-RovE-Internal", "")
    if not INTERNAL_PUSH_SECRET or not hmac.compare_digest(supplied, INTERNAL_PUSH_SECRET):
        return jsonify({"ok": False, "error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    try:
        user_id = int(payload.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    title = clean_text(payload.get("title"))[:120]
    body = clean_text(payload.get("body"))[:500]
    tag = clean_text(payload.get("tag"))[:120] or "rove"
    url = clean_text(payload.get("url"))[:300] or "./"
    if user_id <= 0 or not title:
        return jsonify({"ok": False, "error": "invalid_push_payload"}), 400

    with db() as conn:
        sent = send_push_to_user(conn, user_id, title, body, tag=tag, url=url)
        conn.commit()
    return jsonify({"ok": True, "sent": sent})


def ensure_payday_column(conn: sqlite3.Connection) -> None:
    """`users.payday` gab es bis 27.07. nicht — der Bot kennt bis heute keinen Zahltag."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "payday" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN payday INTEGER")


def onboarding_amount(value: object, maximum: float = 10_000_000.0) -> float | None:
    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        return None
    return amount if 0 <= amount <= maximum else None


def optional_profile_amount(value: object, maximum: float = 1_000_000.0) -> float | None:
    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        return None
    return amount if 0 <= amount <= maximum else None


@app.route("/v1/onboarding", methods=["POST"])
def complete_app_onboarding():
    """Schreibt ein eingeladenes App-only-Profil einmalig in die produktive Datenbasis."""
    payload = request.get_json(silent=True) or {}
    token = token_from_request()

    name = clean_text(payload.get("name"))[:40]
    income = onboarding_amount(payload.get("income"))
    other_income = onboarding_amount(payload.get("other_income"))
    etf_savings = onboarding_amount(payload.get("etf_savings"), 100_000.0)
    cash_savings = onboarding_amount(payload.get("cash_savings"), 100_000.0)
    wealth = payload.get("wealth") if isinstance(payload.get("wealth"), dict) else {}
    amounts = {
        key: onboarding_amount(wealth.get(key))
        for key in ("giro", "tagesgeld", "bargeld", "etf", "krypto", "property_value", "property_debt")
    }
    try:
        payday = int(payload.get("payday") or 0)
    except (TypeError, ValueError):
        payday = 0
    if (
        income is None or other_income is None or etf_savings is None or cash_savings is None
        or any(value is None for value in amounts.values())
        or (payday and not 1 <= payday <= 31)
    ):
        return jsonify({"ok": False, "error": "invalid_onboarding_values"}), 400

    contracts = payload.get("contracts")
    goals = payload.get("goals")
    if not isinstance(contracts, list) or len(contracts) > 50:
        return jsonify({"ok": False, "error": "invalid_onboarding_contracts"}), 400
    if not isinstance(goals, list) or len(goals) > 10:
        return jsonify({"ok": False, "error": "invalid_onboarding_goals"}), 400

    cleaned_contracts = []
    for index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            return jsonify({"ok": False, "error": "invalid_onboarding_contract"}), 400
        contract_name = clean_text(contract.get("name"))[:80]
        category = clean_text(contract.get("category"))
        amount = onboarding_amount(contract.get("amount"), 1_000_000.0)
        if not contract_name or category not in CONTRACT_CATEGORIES or amount is None or amount <= 0:
            return jsonify({"ok": False, "error": "invalid_onboarding_contract"}), 400
        cleaned_contracts.append({
            "id": f"onboarding_{index + 1}_{secrets.token_hex(4)}",
            "name": contract_name,
            "category": category,
            "amount": amount,
            "icon": clean_text(contract.get("icon"), "doc")[:24],
            "tint": clean_text(contract.get("tint"), "#8FA8BC")[:16],
            "cancelable": 1 if bool(contract.get("cancelable", True)) else 0,
        })

    cleaned_goals = []
    for index, goal in enumerate(goals):
        if not isinstance(goal, dict):
            return jsonify({"ok": False, "error": "invalid_onboarding_goal"}), 400
        goal_name = clean_text(goal.get("name"))[:80]
        target = onboarding_amount(goal.get("target"))
        current = onboarding_amount(goal.get("current"))
        if not goal_name or target is None or target <= 0 or current is None:
            return jsonify({"ok": False, "error": "invalid_onboarding_goal"}), 400
        cleaned_goals.append({
            "id": f"onboarding_{index + 1}_{secrets.token_hex(4)}",
            "name": goal_name,
            "target": target,
            "current": min(current, target),
            "icon": clean_text(goal.get("icon"), "coins")[:24],
            "tint": clean_text(goal.get("tint"), "#2AABEE")[:16],
        })

    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_auth_tables(conn)
        account = conn.execute(
            "SELECT source FROM app_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        user = conn.execute(
            "SELECT onboarding_step FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not account or str(account["source"] or "") != "app":
            return jsonify({"ok": False, "error": "app_registration_required"}), 403
        if user and int(user["onboarding_step"] or 0) >= 10:
            return jsonify({"ok": False, "error": "onboarding_already_completed"}), 409

        # A resumed onboarding initializes missing data; it must never reset values that
        # were already recorded through a canonical App path.
        has_cash_accounts = bool(conn.execute(
            "SELECT 1 FROM app_account_balances WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()) if table_exists(conn, "app_account_balances") else False
        has_holdings = bool(conn.execute(
            "SELECT 1 FROM portfolio_holdings WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone())
        has_investment_events = bool(conn.execute(
            "SELECT 1 FROM investment_events WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone())
        has_contracts = bool(conn.execute(
            "SELECT 1 FROM app_contracts WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()) if table_exists(conn, "app_contracts") else False

        ensure_payday_column(conn)
        conn.execute(
            """UPDATE users SET income = ?, other_income = ?, etf_savings = ?, cash_savings = ?,
                      current_investments = CASE WHEN ? THEN current_investments ELSE ? END,
                      current_cash = CASE WHEN ? THEN current_cash ELSE ? END, onboarding_step = 10,
                      current_month = ?
                WHERE user_id = ?""",
            (
                income, other_income, etf_savings, cash_savings,
                has_holdings or has_investment_events,
                round(amounts["etf"] + amounts["krypto"], 2),
                has_cash_accounts or multi_cash_accounts_enabled(conn, user_id),
                round(amounts["giro"] + amounts["tagesgeld"] + amounts["bargeld"], 2),
                datetime.now().strftime("%Y-%m"), user_id,
            ),
        )
        conn.execute("UPDATE users SET payday = ? WHERE user_id = ?", (payday or None, user_id))
        conn.execute(
            """UPDATE app_accounts SET display_name = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE user_id = ?""",
            (name or None, user_id),
        )
        conn.execute(
            "UPDATE user_access SET display_name = ?, note = 'App-only Beta' WHERE user_id = ?",
            (name, user_id),
        )

        ensure_app_account_balances_table(conn)
        if not has_cash_accounts and not multi_cash_accounts_enabled(conn, user_id):
            save_app_cash_accounts(conn, user_id, {
                "giro": amounts["giro"],
                "tagesgeld": amounts["tagesgeld"],
                "bargeld": amounts["bargeld"],
            })

        ensure_app_contracts_table(conn)
        for contract in cleaned_contracts:
            duplicate = conn.execute(
                """SELECT 1 FROM app_contracts
                     WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?) LIMIT 1""",
                (user_id, contract["name"]),
            ).fetchone()
            if duplicate:
                continue
            conn.execute(
                """INSERT INTO app_contracts
                   (user_id, contract_id, detail_key, name, category, amount, icon, tint, cancelable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, contract["id"], f"app_{contract['id']}", contract["name"],
                    contract["category"], contract["amount"], contract["icon"],
                    contract["tint"], contract["cancelable"],
                ),
            )
        if cleaned_contracts or has_contracts:
            sync_app_contract_details(conn, user_id)

        ensure_app_goals_table(conn)
        for goal in cleaned_goals:
            duplicate = conn.execute(
                """SELECT 1 FROM app_goals
                     WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?) LIMIT 1""",
                (user_id, goal["name"]),
            ).fetchone()
            if duplicate:
                continue
            conn.execute(
                """INSERT INTO app_goals
                   (user_id, goal_id, name, target_amount, current_amount, icon, tint)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, goal["id"], goal["name"], goal["target"], goal["current"],
                    goal["icon"], goal["tint"],
                ),
            )

        ensure_app_properties_table(conn)
        if amounts["property_value"] > 0:
            conn.execute(
                """INSERT INTO app_properties (user_id, market_value, remaining_debt, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                     market_value = excluded.market_value,
                     remaining_debt = excluded.remaining_debt,
                     updated_at = CURRENT_TIMESTAMP""",
                (user_id, amounts["property_value"], amounts["property_debt"]),
            )

        for asset_type, amount, label in (
            ("etf", amounts["etf"], "ETF & Investments"),
            ("crypto", amounts["krypto"], "Krypto"),
        ):
            if amount > 0:
                existing = conn.execute(
                    """SELECT 1 FROM investment_events
                         WHERE user_id = ? AND source = 'app_onboarding'
                           AND asset_type = ? LIMIT 1""",
                    (user_id, asset_type),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                       VALUES (?, ?, 'in', ?, ?, 'initial_balance', 'app_onboarding', 'Startwert aus App-Onboarding')""",
                    (user_id, amount, asset_type, label),
                )

        ensure_app_etf_savings_plan_table(conn)
        if etf_savings > 0:
            plan = payload.get("etf_plan") if isinstance(payload.get("etf_plan"), dict) else {}
            try:
                execution_day = int(plan.get("execution_day") or 1)
            except (TypeError, ValueError):
                execution_day = 1
            source_account = clean_text(plan.get("source_account"), "giro").lower()
            mode = clean_text(plan.get("mode"), "auto").lower()
            if not 1 <= execution_day <= 31 or source_account not in {"giro", "tagesgeld"} or mode not in {"auto", "confirm"}:
                return jsonify({"ok": False, "error": "invalid_onboarding_etf_plan"}), 400
            now = datetime.now()
            start_month = now.strftime("%Y-%m")
            if now.day > execution_day:
                next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
                start_month = next_month.strftime("%Y-%m")
            source_account_id = None
            if multi_cash_accounts_enabled(conn, user_id):
                prepare_multi_cash_write(conn)
                source_account_id = legacy_financial_account_id(conn, user_id, source_account)
            conn.execute(
                """INSERT INTO app_etf_savings_plan
                   (user_id, execution_day, source_account, source_account_id, mode, active, start_month, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                     execution_day = excluded.execution_day,
                     source_account = excluded.source_account,
                     source_account_id = excluded.source_account_id,
                     mode = excluded.mode,
                     active = excluded.active,
                     start_month = excluded.start_month,
                     updated_at = CURRENT_TIMESTAMP""",
                (user_id, execution_day, source_account, source_account_id, mode, start_month),
            )

        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, "onboardingRequired": False, **live_data})


@app.route("/v1/profile", methods=["OPTIONS"])
def profile_options():
    return ("", 204)


@app.route("/v1/profile", methods=["POST"])
def update_profile():
    """Aktualisiert sichere, dauerhafte Profilfelder des angemeldeten Nutzers.

    Die E-Mail bleibt bewusst unveraenderlich — sie ist der Login-Schluessel. Ein Wechsel muesste
    ueber eine neue Bestaetigung laufen, sonst koennte man ein fremdes Konto uebernehmen.
    """
    payload = request.get_json(silent=True) or {}

    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_auth_tables(conn)

        # Nur senden, was sich aendern soll — sonst wuerde ein Namens-Update den Zahltag loeschen.
        if "name" in payload:
            name = clean_text(payload.get("name"))[:40]
            conn.execute(
                """UPDATE app_accounts SET display_name = ?, updated_at = CURRENT_TIMESTAMP
                     WHERE user_id = ?""",
                (name or None, user_id),
            )

        if "payday" in payload:
            # Zahltag (27.07.): Der Monatscheck oeffnete sich pauschal am 1., unabhaengig davon,
            # wann das Gehalt kommt. Ein Feld dafuer gab es nirgends — weder im Bot noch im API.
            ensure_payday_column(conn)
            try:
                day = int(payload.get("payday") or 0)
            except (TypeError, ValueError):
                day = 0
            if day and not 1 <= day <= 31:
                return jsonify({"ok": False, "error": "payday_out_of_range"}), 400
            conn.execute(
                "UPDATE users SET payday = ? WHERE user_id = ?",
                (day or None, user_id),
            )

        profile_income_updates = {}
        if "income" in payload:
            income = optional_profile_amount(payload.get("income"))
            if income is None:
                return jsonify({"ok": False, "error": "valid_income_required"}), 400
            profile_income_updates["income"] = income
        if "other_income" in payload:
            other_income = optional_profile_amount(payload.get("other_income"))
            if other_income is None:
                return jsonify({"ok": False, "error": "valid_other_income_required"}), 400
            profile_income_updates["other_income"] = other_income
        if profile_income_updates:
            assignments = ", ".join(f"{key} = ?" for key in profile_income_updates)
            conn.execute(
                f"UPDATE users SET {assignments} WHERE user_id = ?",
                (*profile_income_updates.values(), user_id),
            )

        if "etf_plan" in payload:
            # Erst konfigurieren, dann automatisch buchen: Bei bestehenden Kunden fehlen
            # Ausfuehrungstag und Quellkonto. Ohne diese drei expliziten Angaben passiert nie
            # still eine ETF-Buchung.
            plan = payload.get("etf_plan") or {}
            if not isinstance(plan, dict):
                return jsonify({"ok": False, "error": "valid_etf_plan_required"}), 400
            try:
                execution_day = int(plan.get("execution_day") or 0)
            except (TypeError, ValueError):
                execution_day = 0
            source_account = clean_text(plan.get("source_account"), "giro").lower()
            mode = clean_text(plan.get("mode")).lower()
            active_raw = plan.get("active", True)
            if not 1 <= execution_day <= 31:
                return jsonify({"ok": False, "error": "etf_execution_day_out_of_range"}), 400
            if source_account not in {"giro", "tagesgeld"}:
                return jsonify({"ok": False, "error": "valid_etf_source_required"}), 400
            if mode not in {"auto", "confirm"}:
                return jsonify({"ok": False, "error": "valid_etf_mode_required"}), 400
            if active_raw not in {True, False, 0, 1}:
                return jsonify({"ok": False, "error": "valid_etf_active_required"}), 400
            active = 1 if bool(active_raw) else 0
            ensure_app_etf_savings_plan_table(conn)
            try:
                source_account, source_account_id = resolve_etf_source_account(
                    conn, user_id, plan, source_account
                )
            except (TypeError, ValueError, LookupError) as exc:
                return financial_account_error(exc)
            now = datetime.now()
            # Kein rueckwirkendes Buchen beim Einrichten: Ist der gewuenschte Tag
            # bereits vorbei, beginnt die Automatik erst mit dem naechsten Monat.
            start_month = now.strftime("%Y-%m")
            if execution_day < now.day:
                start_month = (
                    (now.replace(day=28) + timedelta(days=4)).replace(day=1)
                ).strftime("%Y-%m")
            conn.execute(
                """INSERT INTO app_etf_savings_plan
                       (user_id, execution_day, source_account, source_account_id,
                        mode, active, start_month, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                       execution_day = excluded.execution_day,
                       source_account = excluded.source_account,
                       source_account_id = excluded.source_account_id,
                       mode = excluded.mode,
                       active = excluded.active,
                       start_month = CASE
                         WHEN app_etf_savings_plan.start_month > excluded.start_month
                         THEN app_etf_savings_plan.start_month ELSE excluded.start_month END,
                       updated_at = CURRENT_TIMESTAMP""",
                (user_id, execution_day, source_account, source_account_id,
                 mode, active, start_month),
            )

        scheduled_savings = None
        # Sparrate gehoert zur Monatsplanung und darf nicht nur im Browser leben.
        # Sonst zeigt Rov.E bis zum naechsten Refresh einen anderen Betrag als
        # Monatsplan, Coach und Report. Bereits ausgefuehrte Sparraten bleiben
        # unveraendert: Sie sind echte Umschichtungen und keine editierbare Planung.
        savings_keys = {"etf_savings", "cash_savings"}
        if savings_keys.intersection(payload):
            ensure_app_monthly_plan_table(conn)
            month_key = datetime.now().strftime("%Y-%m")
            status = conn.execute(
                """SELECT savings_status FROM app_monthly_plan_status
                     WHERE user_id = ? AND month_key = ?""",
                (user_id, month_key),
            ).fetchone()
            executed = conn.execute(
                """SELECT 1 FROM investment_events
                     WHERE user_id = ?
                       AND source IN ('investiert_command', 'app_monthly_plan', 'app_etf_plan')
                       AND strftime('%Y-%m', created_at) = ?
                     LIMIT 1""",
                (user_id, month_key),
            ).fetchone()
            current = conn.execute(
                "SELECT etf_savings, cash_savings FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            pending = get_app_scheduled_savings(conn, user_id)

            def savings_amount(key: str, fallback: float) -> float:
                if key not in payload:
                    return fallback
                try:
                    value = round(float(payload.get(key)), 2)
                except (TypeError, ValueError):
                    raise ValueError(key)
                if value < 0 or value > 100000:
                    raise ValueError(key)
                return value

            try:
                etf_savings = savings_amount(
                    "etf_savings",
                    float(pending["etf"] if pending else current["etf_savings"] or 0),
                )
                cash_savings = savings_amount(
                    "cash_savings",
                    float(pending["cash"] if pending else current["cash_savings"] or 0),
                )
            except ValueError:
                return jsonify({"ok": False, "error": "valid_savings_amount_required"}), 400
            if (status and status["savings_status"] == "confirmed") or executed:
                next_month = (
                    (datetime.now().replace(day=28) + timedelta(days=4)).replace(day=1)
                ).strftime("%Y-%m")
                ensure_app_scheduled_savings_table(conn)
                conn.execute(
                    """INSERT INTO app_scheduled_savings
                           (user_id, effective_month, etf_savings, cash_savings, updated_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(user_id) DO UPDATE SET
                           effective_month = excluded.effective_month,
                           etf_savings = excluded.etf_savings,
                           cash_savings = excluded.cash_savings,
                           updated_at = CURRENT_TIMESTAMP""",
                    (user_id, next_month, etf_savings, cash_savings),
                )
                scheduled_savings = {
                    "effectiveMonth": next_month,
                    "etf": etf_savings,
                    "cash": cash_savings,
                }
            else:
                conn.execute(
                    "UPDATE users SET etf_savings = ?, cash_savings = ? WHERE user_id = ?",
                    (etf_savings, cash_savings, user_id),
                )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify(
        {
            "ok": True,
            **live_data,
            "scheduledSavings": scheduled_savings
            if scheduled_savings is not None
            else live_data.get("scheduledSavings"),
        }
    )


@app.route("/v1/goals", methods=["POST"])
def update_goals():
    """Verwaltet App-Ziele zentral, damit sie App-Neustarts und Geraetewechsel ueberleben."""
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    if action not in {"create", "assign", "set_target", "set_rate", "delete"}:
        return jsonify({"ok": False, "error": "valid_goal_action_required"}), 400

    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        begin_write(conn)
        ensure_app_goals_table(conn)

        if action == "create":
            name = clean_text(payload.get("name"))
            target = goal_amount(payload.get("target"))
            icon = clean_text(payload.get("icon"), "coins")
            tint = clean_text(payload.get("tint"), "#2AABEE")
            if not name or target is None or target <= 0:
                return jsonify({"ok": False, "error": "valid_goal_name_and_target_required"}), 400
            rate = None
            if "goal_monthly_rate" in payload and payload.get("goal_monthly_rate") not in (None, ""):
                rate = goal_amount(payload.get("goal_monthly_rate"))
                if rate is None or rate <= 0:
                    return jsonify({"ok": False, "error": "valid_goal_monthly_rate_required"}), 400
            duplicate = conn.execute(
                """SELECT 1 FROM app_goals
                     WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?) LIMIT 1""",
                (user_id, name),
            ).fetchone()
            if duplicate:
                return jsonify({"ok": False, "error": "goal_name_already_exists"}), 409
            goal_id = secrets.token_urlsafe(9)
            conn.execute(
                """INSERT INTO app_goals
                   (user_id, goal_id, name, target_amount, goal_monthly_rate, icon, tint)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, goal_id, name, target, rate, icon, tint),
            )
        else:
            goal_id = clean_text(payload.get("goal_id"))
            if not goal_id:
                return jsonify({"ok": False, "error": "goal_id_required"}), 400
            if goal_id == "primary":
                primary = conn.execute(
                    "SELECT goal_description, goal_amount FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                if not primary or not clean_text(primary["goal_description"]):
                    return jsonify({"ok": False, "error": "goal_not_found"}), 404
                target = float(primary["goal_amount"] or 0)
                ensure_app_primary_goal_progress_table(conn)
                progress = conn.execute(
                    "SELECT current_amount FROM app_primary_goal_progress WHERE user_id = ?", (user_id,)
                ).fetchone()
                current = float(progress["current_amount"] or 0) if progress else 0.0

                if action == "delete":
                    conn.execute(
                        "UPDATE users SET goal_description = '', goal_amount = 0 WHERE user_id = ?", (user_id,)
                    )
                    conn.execute("DELETE FROM app_primary_goal_progress WHERE user_id = ?", (user_id,))
                elif action == "set_rate":
                    raw_rate = payload.get("goal_monthly_rate")
                    if raw_rate in (None, ""):
                        next_rate = None
                    else:
                        next_rate = goal_amount(raw_rate)
                        if next_rate is None or next_rate <= 0:
                            return jsonify({"ok": False, "error": "valid_goal_monthly_rate_required"}), 400
                    conn.execute(
                        """INSERT INTO app_primary_goal_progress (user_id, goal_monthly_rate)
                           VALUES (?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                             goal_monthly_rate = excluded.goal_monthly_rate,
                             updated_at = CURRENT_TIMESTAMP""",
                        (user_id, next_rate),
                    )
                elif action == "assign":
                    amount = goal_amount(payload.get("amount"))
                    if amount is None or amount <= 0:
                        return jsonify({"ok": False, "error": "valid_goal_amount_required"}), 400
                    next_amount = round(min(target, current + amount), 2)
                    conn.execute(
                        """INSERT INTO app_primary_goal_progress (user_id, current_amount)
                           VALUES (?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                             current_amount = excluded.current_amount,
                             updated_at = CURRENT_TIMESTAMP""",
                        (user_id, next_amount),
                    )
                else:  # set_target
                    next_target = goal_amount(payload.get("target"))
                    if next_target is None or next_target <= 0:
                        return jsonify({"ok": False, "error": "valid_goal_target_required"}), 400
                    conn.execute("UPDATE users SET goal_amount = ? WHERE user_id = ?", (next_target, user_id))
                    conn.execute(
                        """INSERT INTO app_primary_goal_progress (user_id, current_amount)
                           VALUES (?, ?)
                           ON CONFLICT(user_id) DO UPDATE SET
                             current_amount = MIN(app_primary_goal_progress.current_amount, excluded.current_amount),
                             updated_at = CURRENT_TIMESTAMP""",
                        (user_id, min(current, next_target)),
                    )
                live_data = build_live_app_data(conn, user_id)
                conn.commit()
                return jsonify({"ok": True, **live_data})
            goal = conn.execute(
                """SELECT target_amount, current_amount FROM app_goals
                     WHERE user_id = ? AND goal_id = ?""",
                (user_id, goal_id),
            ).fetchone()
            if not goal:
                return jsonify({"ok": False, "error": "goal_not_found"}), 404

            if action == "delete":
                conn.execute("DELETE FROM app_goals WHERE user_id = ? AND goal_id = ?", (user_id, goal_id))
            elif action == "set_rate":
                raw_rate = payload.get("goal_monthly_rate")
                if raw_rate in (None, ""):
                    next_rate = None
                else:
                    next_rate = goal_amount(raw_rate)
                    if next_rate is None or next_rate <= 0:
                        return jsonify({"ok": False, "error": "valid_goal_monthly_rate_required"}), 400
                conn.execute(
                    """UPDATE app_goals
                          SET goal_monthly_rate = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ? AND goal_id = ?""",
                    (next_rate, user_id, goal_id),
                )
            elif action == "assign":
                amount = goal_amount(payload.get("amount"))
                if amount is None or amount <= 0:
                    return jsonify({"ok": False, "error": "valid_goal_amount_required"}), 400
                target = float(goal["target_amount"] or 0)
                current = float(goal["current_amount"] or 0)
                conn.execute(
                    """UPDATE app_goals
                          SET current_amount = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ? AND goal_id = ?""",
                    (round(min(target, current + amount), 2), user_id, goal_id),
                )
            else:  # set_target
                target = goal_amount(payload.get("target"))
                if target is None or target <= 0:
                    return jsonify({"ok": False, "error": "valid_goal_target_required"}), 400
                conn.execute(
                    """UPDATE app_goals
                          SET target_amount = ?, current_amount = MIN(current_amount, ?),
                              updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ? AND goal_id = ?""",
                    (target, target, user_id, goal_id),
                )

        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, **live_data})


@app.route("/v1/contracts", methods=["POST"])
def update_contracts():
    """Speichert App-Verträge zentral und rechnet sie sofort in die Fixkosten ein."""
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    if action not in {"create", "update", "delete"}:
        return jsonify({"ok": False, "error": "valid_contract_action_required"}), 400
    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_app_contracts_table(conn)

        if action == "create":
            name = clean_text(payload.get("name"))
            category = clean_text(payload.get("category"), "Sonstiges")
            amount = goal_amount(payload.get("amount"))
            if not name or category not in CONTRACT_CATEGORIES or amount is None or amount <= 0:
                return jsonify({"ok": False, "error": "valid_contract_required"}), 400
            duplicate = conn.execute(
                "SELECT 1 FROM app_contracts WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?) LIMIT 1",
                (user_id, name),
            ).fetchone()
            if duplicate:
                return jsonify({"ok": False, "error": "contract_name_already_exists"}), 409
            contract_id = secrets.token_urlsafe(9)
            icon = clean_text(payload.get("icon"), "doc")
            tint = clean_text(payload.get("tint"), "#8FA8BC")
            cancelable = 0 if category in {"Wohnen", "Kredite"} else 1
            conn.execute(
                """INSERT INTO app_contracts
                   (user_id, contract_id, detail_key, name, category, amount, icon, tint, cancelable)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, contract_id, f"app_{contract_id}", name, category, amount, icon, tint, cancelable),
            )
        else:
            contract_id = clean_text(payload.get("contract_id"))
            if not contract_id:
                return jsonify({"ok": False, "error": "contract_id_required"}), 400
            existing = conn.execute(
                "SELECT 1 FROM app_contracts WHERE user_id = ? AND contract_id = ?",
                (user_id, contract_id),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "contract_not_found"}), 404
            if action == "delete":
                conn.execute("DELETE FROM app_contracts WHERE user_id = ? AND contract_id = ?", (user_id, contract_id))
            else:
                amount = goal_amount(payload.get("amount"))
                if amount is None or amount <= 0:
                    return jsonify({"ok": False, "error": "valid_contract_amount_required"}), 400
                conn.execute(
                    """UPDATE app_contracts SET amount = ?, updated_at = CURRENT_TIMESTAMP
                         WHERE user_id = ? AND contract_id = ?""",
                    (amount, user_id, contract_id),
                )

        sync_app_contract_details(conn, user_id)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, **live_data})


@app.route("/v1/budgets", methods=["POST"])
def update_budgets():
    """Speichert App-Budgetanpassungen in der Bot-Datenbank fuer den aktuellen Monat."""
    payload = request.get_json(silent=True) or {}
    updates = clean_budget_updates(payload.get("budgets"))
    if not updates:
        return jsonify({"ok": False, "error": "valid_budget_updates_required"}), 400

    token = token_from_request()
    active_month = datetime.now().strftime("%Y-%m")
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        for category, limit, source in updates:
            conn.execute(
                """INSERT INTO category_budgets
                   (user_id, category, monthly_limit, source, active_month)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, category, active_month)
                   DO UPDATE SET monthly_limit = excluded.monthly_limit,
                                 source = excluded.source""",
                (user_id, category, limit, source, active_month),
            )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, "budgets": live_data["budgets"]})


@app.route("/v1/accounts", methods=["POST"])
def update_accounts():
    """Korrigiert Cash-Konten oder verschiebt Geld zwischen ihnen ohne Doppelzaehlung."""
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    token = token_from_request()

    with db() as conn:
        begin_write(conn)   # siehe begin_write(): Lesen und Schreiben muessen ein Block sein
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        pilot = multi_cash_accounts_enabled(conn, user_id)
        if pilot:
            prepare_multi_cash_write(conn)
        balances = app_cash_accounts(conn, user_id)   # unter der Sperre aus begin_write()
        try:
            amount = round(abs(float(payload.get("amount") or 0)), 2)
        except (TypeError, ValueError):
            amount = 0.0

        if action == "transfer":
            source = clean_text(payload.get("from")).lower()
            target = clean_text(payload.get("to")).lower()
            if source not in ACCOUNT_KEYS or target not in ACCOUNT_KEYS or source == target:
                return jsonify({"ok": False, "error": "valid_transfer_accounts_required"}), 400
            if amount <= 0 or (amount > balances[source] and (not pilot or source != "giro")):
                return jsonify({"ok": False, "error": "transfer_amount_not_available"}), 400
            source_account_id = None
            target_account_id = None
            if pilot:
                source_account_id = legacy_financial_account_id(conn, user_id, source)
                target_account_id = legacy_financial_account_id(conn, user_id, target)
                balances = transfer_financial_account_balance(
                    conn, user_id, source_account_id, target_account_id, amount,
                    require_source_funds=source != "giro",
                )
            else:
                balances[source] = round(balances[source] - amount, 2)
                balances[target] = round(balances[target] + amount, 2)
            # Nur eine echte Abhebung wird als Buchungszeile gemerkt (die App schickt dafuer
            # log:"withdrawal"). Ein normales Umbuchen im Konten-Detail bleibt bewusst still —
            # sonst tauchten in der Buchungsliste ploetzlich Zeilen auf, die es dort nie gab.
            if clean_text(payload.get("log")).lower() == "withdrawal" and source == "giro" and target == "bargeld":
                ensure_app_cash_movements_table(conn)
                if pilot:
                    conn.execute(
                        """INSERT INTO app_cash_movements
                               (user_id, kind, amount, source_account_id, target_account_id)
                           VALUES (?, 'withdrawal', ?, ?, ?)""",
                        (user_id, amount, source_account_id, target_account_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO app_cash_movements (user_id, kind, amount)
                           VALUES (?, 'withdrawal', ?)""",
                        (user_id, amount),
                    )
        elif action == "set":
            account = clean_text(payload.get("account")).lower()
            if account not in ACCOUNT_KEYS or amount > 10_000_000:
                return jsonify({"ok": False, "error": "valid_account_amount_required"}), 400
            balances[account] = amount
            if pilot:
                _account_id, balances = set_legacy_financial_account_balance(
                    conn, user_id, account, amount
                )
        elif action == "adjust":
            account = clean_text(payload.get("account")).lower()
            direction = clean_text(payload.get("direction"), "add").lower()
            if account not in ACCOUNT_KEYS or direction not in {"add", "subtract"} or amount <= 0:
                return jsonify({"ok": False, "error": "valid_account_adjustment_required"}), 400
            delta = amount if direction == "add" else -amount
            if balances[account] + delta < 0 or balances[account] + delta > 10_000_000:
                return jsonify({"ok": False, "error": "account_adjustment_not_available"}), 400
            balances[account] = round(balances[account] + delta, 2)
            if pilot:
                _account_id, balances = set_legacy_financial_account_balance(
                    conn, user_id, account, balances[account]
                )
        else:
            return jsonify({"ok": False, "error": "valid_account_action_required"}), 400

        if not pilot:
            save_app_cash_accounts(conn, user_id, balances)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, "accounts": balances, **live_data})


@app.route("/v1/financial-accounts", methods=["POST"])
def create_financial_account_endpoint():
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    try:
        balance = round(float(payload.get("balance") or 0), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_financial_account_balance"}), 400
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if error := require_multi_cash_pilot(conn, user_id):
            return error
        try:
            account_id, _balances = create_financial_account(
                conn, user_id, clean_text(payload.get("type")).lower(),
                clean_text(payload.get("name")), balance,
            )
        except (ValueError, LookupError) as exc:
            return financial_account_error(exc)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, "accountId": account_id, **live_data})


@app.route("/v1/financial-accounts/<int:account_id>", methods=["PATCH"])
def update_financial_account_endpoint(account_id: int):
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if error := require_multi_cash_pilot(conn, user_id):
            return error
        try:
            if action == "rename":
                rename_financial_account(conn, user_id, account_id, clean_text(payload.get("name")))
            elif action == "set_balance":
                balance = round(float(payload.get("balance")), 2)
                if abs(balance) > 10_000_000:
                    raise ValueError("invalid_financial_account_balance")
                account = require_financial_account(conn, user_id, account_id)
                if str(account["account_type"]) in {"savings", "wallet"} and balance < -0.0049:
                    raise ValueError("financial_account_balance_insufficient")
                update_financial_account_balance(conn, user_id, account_id, balance)
            elif action == "archive":
                archive_financial_account(conn, user_id, account_id)
            else:
                return jsonify({"ok": False, "error": "valid_financial_account_action_required"}), 400
        except (TypeError, ValueError, LookupError) as exc:
            return financial_account_error(exc)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, **live_data})


@app.route("/v1/financial-accounts/transfer", methods=["POST"])
def transfer_financial_accounts_endpoint():
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    try:
        source_id = int(payload.get("sourceAccountId") or 0)
        target_id = int(payload.get("targetAccountId") or 0)
        amount = round(float(payload.get("amount") or 0), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "valid_financial_account_transfer_required"}), 400
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if error := require_multi_cash_pilot(conn, user_id):
            return error
        try:
            # Girokonten duerfen wie im bestehenden Rov.E-Modell ins Minus gehen.
            # Tagesgeld und Wallet bleiben durch die typabhaengige Primitive geschuetzt.
            transfer_financial_account_balance(
                conn, user_id, source_id, target_id, amount, require_source_funds=False
            )
        except (ValueError, LookupError) as exc:
            return financial_account_error(exc)
        ensure_app_cash_movements_table(conn)
        conn.execute(
            """INSERT INTO app_cash_movements
                   (user_id, kind, amount, label, source_account_id, target_account_id)
               VALUES (?, 'transfer', ?, 'Umbuchung', ?, ?)""",
            (user_id, amount, source_id, target_id),
        )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, **live_data})


@app.route("/v1/financial-account-roles", methods=["POST"])
def update_financial_account_role_endpoint():
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    try:
        account_id = int(payload.get("accountId") or 0)
    except (TypeError, ValueError):
        account_id = 0
    role = clean_text(payload.get("role")).lower()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if error := require_multi_cash_pilot(conn, user_id):
            return error
        try:
            account = require_financial_account(conn, user_id, account_id)
            if str(account["account_type"]) != "checking":
                raise ValueError("financial_account_role_requires_checking")
            set_account_role(conn, user_id, role, account_id)
        except (ValueError, LookupError) as exc:
            return financial_account_error(exc)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, **live_data})


def fixed_costs_total(details: dict) -> float:
    return round(sum(
        float(value or 0)
        for section in details.values() if isinstance(section, dict)
        for key, value in section.items()
        if key not in {"restschuld", "gesamtbetrag", "schulden_gesamt", "gesamt"}
    ), 2)


@app.route("/v1/property", methods=["POST"])
def update_property():
    """Speichert Immobilienwert, Restschuld und optionale laufende Kosten zentral."""
    payload = request.get_json(silent=True) or {}
    token = token_from_request()

    def money(key: str) -> float:
        try:
            return round(max(0.0, float(payload.get(key) or 0)), 2)
        except (TypeError, ValueError):
            return 0.0

    market_value = money("market_value")
    remaining_debt = money("remaining_debt")
    monthly_rate = money("monthly_rate")
    house_fee = money("house_fee")
    management_fee = money("management_fee")
    if market_value <= 0 or market_value > 100_000_000 or remaining_debt > 100_000_000:
        return jsonify({"ok": False, "error": "valid_property_values_required"}), 400

    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_properties_table(conn)
        conn.execute(
            """INSERT INTO app_properties
               (user_id, market_value, remaining_debt, monthly_rate, house_fee,
                management_fee, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 market_value = excluded.market_value,
                 remaining_debt = excluded.remaining_debt,
                 monthly_rate = excluded.monthly_rate,
                 house_fee = excluded.house_fee,
                 management_fee = excluded.management_fee,
                 updated_at = CURRENT_TIMESTAMP""",
            (user_id, market_value, remaining_debt, monthly_rate, house_fee, management_fee),
        )

        user = conn.execute(
            "SELECT fixed_costs_details FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        try:
            details = json.loads(user["fixed_costs_details"] or "{}") if user else {}
        except (json.JSONDecodeError, TypeError):
            details = {}
        credits = details.get("kredite") if isinstance(details.get("kredite"), dict) else {}

        def upsert_existing_detail(key: str, value: float, sections: tuple[str, ...]) -> None:
            if not value:
                return
            for section in sections:
                values = details.get(section)
                if isinstance(values, dict) and key in values:
                    values[key] = value
                    return
            credits[key] = value

        # Leere optionale Felder lassen bestehende Bot-Einträge in Ruhe. So führt eine reine
        # Vermögenskorrektur nicht versehentlich zum Löschen schon gepflegter Fixkosten.
        if remaining_debt:
            credits["restschuld"] = remaining_debt
        upsert_existing_detail("immobilie", monthly_rate, ("kredite",))
        upsert_existing_detail("hausgeld", house_fee, ("kredite", "wohnen"))
        upsert_existing_detail("hausverwalter", management_fee, ("kredite", "wohnen"))
        if credits:
            details["kredite"] = credits
        conn.execute(
            """UPDATE users
                  SET fixed_costs_details = ?, fixed_costs = ?
                WHERE user_id = ?""",
            (json.dumps(details, ensure_ascii=False), fixed_costs_total(details), user_id),
        )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, **live_data})


@app.route("/v1/property", methods=["DELETE"])
def delete_property():
    """Entfernt nur den Vermoegenswert; bestehende Vertraege bleiben bewusst erhalten."""
    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_app_properties_table(conn)
        conn.execute("DELETE FROM app_properties WHERE user_id = ?", (user_id,))
        live_data = build_live_app_data(conn, user_id)
        conn.commit()
    return jsonify({"ok": True, **live_data})


@app.route("/v1/asset-order", methods=["POST"])
def update_asset_order():
    """Speichert ausschliesslich die Reihenfolge der Vermoegenskacheln."""
    payload = request.get_json(silent=True) or {}
    requested = payload.get("order")
    token = token_from_request()

    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        allowed = set(ASSET_ORDER_KEYS)
        if multi_cash_accounts_enabled(conn, user_id):
            allowed.update(
                f"cash-account:{int(account['id'])}"
                for account in list_financial_accounts(conn, user_id)
            )
        if not isinstance(requested, list) or not requested or len(requested) > len(allowed):
            return jsonify({"ok": False, "error": "valid_asset_order_required"}), 400
        order = [clean_text(value) for value in requested]
        if any(key not in allowed for key in order) or len(set(order)) != len(order):
            return jsonify({"ok": False, "error": "valid_asset_order_required"}), 400
        ensure_app_asset_order_table(conn)
        conn.execute("DELETE FROM app_asset_order WHERE user_id = ?", (user_id,))
        conn.executemany(
            """INSERT INTO app_asset_order
               (user_id, asset_key, sort_position, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            [(user_id, key, position) for position, key in enumerate(order)],
        )
        conn.commit()

    return jsonify({"ok": True, "assetOrder": order})


@app.route("/v1/portfolio-tracking", methods=["POST"])
def configure_portfolio_tracking():
    """Enable exact daily valuation for one existing ETF or stock position."""
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    label = clean_text(payload.get("asset_name"))
    instrument_type = clean_text(payload.get("asset_type"), "stock").lower()
    symbol = normalize_symbol(payload.get("price_symbol"))
    currency = normalize_currency(payload.get("quote_currency"))
    try:
        quantity = round(float(str(payload.get("quantity") or "0").replace(",", ".")), 8)
    except (TypeError, ValueError):
        quantity = 0.0
    if instrument_type not in {"etf", "stock"} or not label or not symbol or not currency:
        return jsonify({"ok": False, "error": "valid_market_position_required"}), 400
    if quantity <= 0 or quantity > 1_000_000_000:
        return jsonify({"ok": False, "error": "valid_market_quantity_required"}), 400

    # Authorize before calling the external provider, otherwise this endpoint
    # could be abused as an unauthenticated market-data proxy.
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

    try:
        quote = fetch_eur_quote(symbol, currency)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422

    with db() as conn:
        ensure_market_tracking_schema(conn)
        conn.commit()
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        begin_write(conn)
        holding = conn.execute(
            """SELECT id, instrument_key, quantity, valuation_enabled,
                      COALESCE(market_value, total_invested, 0) AS value
                 FROM portfolio_holdings
                WHERE user_id = ? AND LOWER(TRIM(instrument_label)) = LOWER(?)
                LIMIT 1""",
            (user_id, label),
        ).fetchone()
        if not holding:
            manual = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS value
                     FROM investment_events
                    WHERE user_id = ? AND asset_type = 'stock'
                      AND LOWER(TRIM(asset_name)) = LOWER(?)""",
                (user_id, label),
            ).fetchone()
            baseline = round(max(0.0, float(manual["value"] or 0)), 2)
            if baseline <= 0:
                return jsonify({"ok": False, "error": "market_position_not_found"}), 404
            key_hash = hashlib.sha256(f"{user_id}:{label.lower()}".encode("utf-8")).hexdigest()[:16]
            cursor = conn.execute(
                """INSERT INTO portfolio_holdings
                   (user_id, instrument_key, instrument_label, isin, price_symbol,
                    monthly_contribution, total_invested, start_price, last_price,
                    instrument_type, quantity, quote_currency, valuation_enabled,
                    last_checked_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 1,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    user_id, f"live_{key_hash}", label, symbol, symbol, baseline,
                    quote["native_price"], quote["native_price"], instrument_type,
                    quantity, currency,
                ),
            )
            holding_id = int(cursor.lastrowid)
            quantity_increased = False
        else:
            holding_id = int(holding["id"])
            previous_quantity = float(holding["quantity"] or 0)
            quantity_increased = bool(holding["valuation_enabled"]) and quantity > previous_quantity
            conn.execute(
                """UPDATE portfolio_holdings
                      SET instrument_type = ?,
                          start_price = CASE
                              WHEN UPPER(COALESCE(price_symbol, '')) != ? THEN ?
                              ELSE COALESCE(start_price, ?)
                          END,
                          price_symbol = ?, quantity = ?,
                          quote_currency = ?, valuation_enabled = 1,
                          updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id = ?""",
                (
                    instrument_type, symbol, quote["native_price"], quote["native_price"],
                    symbol, quantity, currency, holding_id, user_id,
                ),
            )
        conn.commit()

        result = apply_market_quote(
            conn,
            holding_id,
            quote,
            expected_symbol=symbol,
            reconcile_pending=quantity_increased,
            manage_transaction=False,
        )
        live_data = build_live_app_data(conn, user_id)

    return jsonify({
        "ok": True,
        "position": {
            "asset_type": instrument_type,
            "asset_name": label,
            "price_symbol": symbol,
            "quantity": quantity,
            "quote_currency": currency,
            "provider": quote.get("provider", "twelve_data"),
            **result,
        },
        **live_data,
    })


@app.route("/v1/etf-position-plan", methods=["POST"])
def update_etf_position_plan():
    """Speichert den ausführbaren Sparplan einer konkreten ETF-Position."""
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    try:
        holding_id = int(payload.get("holding_id") or 0)
        monthly_amount = round(float(payload.get("monthly_amount") or 0), 2)
        execution_day = int(payload.get("execution_day") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "valid_etf_position_plan_required"}), 400
    source_account = clean_text(payload.get("source_account"), "giro").lower()
    mode = clean_text(payload.get("mode")).lower()
    active_raw = payload.get("active", True)
    if holding_id <= 0 or not 0 <= monthly_amount <= 100_000 or not 1 <= execution_day <= 31:
        return jsonify({"ok": False, "error": "valid_etf_position_plan_required"}), 400
    if source_account not in {"giro", "tagesgeld"}:
        return jsonify({"ok": False, "error": "valid_etf_source_required"}), 400
    if mode not in {"auto", "confirm"}:
        return jsonify({"ok": False, "error": "valid_etf_mode_required"}), 400
    if active_raw not in {True, False, 0, 1}:
        return jsonify({"ok": False, "error": "valid_etf_active_required"}), 400

    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_market_tracking_schema(conn)
        ensure_app_etf_position_plans_table(conn)
        holding = conn.execute(
            """SELECT id, instrument_label, COALESCE(instrument_type, 'etf') AS instrument_type
                 FROM portfolio_holdings WHERE id = ? AND user_id = ? LIMIT 1""",
            (holding_id, user_id),
        ).fetchone()
        if not holding or str(holding["instrument_type"]).lower() != "etf":
            return jsonify({"ok": False, "error": "etf_holding_not_found"}), 404

        try:
            source_account, source_account_id = resolve_etf_source_account(
                conn, user_id, payload, source_account
            )
        except (TypeError, ValueError, LookupError) as exc:
            return financial_account_error(exc)

        now = datetime.now()
        start_month = now.strftime("%Y-%m")
        if execution_day < now.day:
            start_month = (
                (now.replace(day=28) + timedelta(days=4)).replace(day=1)
            ).strftime("%Y-%m")
        conn.execute(
            """INSERT INTO app_etf_position_plans
                   (user_id, holding_id, monthly_amount, execution_day, source_account,
                    source_account_id, mode, active, start_month, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, holding_id) DO UPDATE SET
                   monthly_amount = excluded.monthly_amount,
                   execution_day = excluded.execution_day,
                   source_account = excluded.source_account,
                   source_account_id = excluded.source_account_id,
                   mode = excluded.mode,
                   active = excluded.active,
                   start_month = CASE
                     WHEN app_etf_position_plans.start_month > excluded.start_month
                     THEN app_etf_position_plans.start_month ELSE excluded.start_month END,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                user_id, holding_id, monthly_amount, execution_day, source_account,
                source_account_id, mode, 1 if bool(active_raw) else 0, start_month,
            ),
        )
        total = conn.execute(
            """SELECT COALESCE(SUM(monthly_amount), 0) AS total
                 FROM app_etf_position_plans WHERE user_id = ? AND active = 1""",
            (user_id,),
        ).fetchone()
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "positionPlanTotal": round(float(total["total"] or 0), 2),
        "positionName": str(holding["instrument_label"]),
        **live_data,
    })


@app.route("/v1/investments", methods=["POST"])
def update_investment_position():
    """Setzt manuelle Krypto-, Aktien- oder bestehende ETF-Werte ohne Doppelzaehlung."""
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    asset_type = clean_text(payload.get("asset_type"), "crypto").lower()
    asset_name = clean_text(payload.get("asset_name"))
    try:
        target_value = round(max(0.0, float(payload.get("value") or 0)), 2)
    except (TypeError, ValueError):
        target_value = -1.0
    if asset_type not in {"crypto", "stock", "etf"} or not asset_name or target_value < 0 or target_value > 100_000_000:
        return jsonify({"ok": False, "error": "valid_investment_position_required"}), 400

    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_market_tracking_schema(conn)
        conn.commit()
        begin_write(conn)
        live_holding = conn.execute(
            """SELECT 1 FROM portfolio_holdings
                WHERE user_id = ? AND LOWER(TRIM(instrument_label)) = LOWER(?)
                  AND valuation_enabled = 1
                LIMIT 1""",
            (user_id, asset_name),
        ).fetchone()
        if live_holding:
            return jsonify({"ok": False, "error": "market_position_is_live"}), 409

        # Ein bestehendes ETF-Holding darf nicht als neue Aktienposition daneben
        # angelegt werden. Die Korrektur aktualisiert deshalb genau das Depot-Holding.
        if asset_type == "etf":
            try:
                holding = conn.execute(
                    """SELECT instrument_key, instrument_label, COALESCE(total_invested, 0) AS value
                         FROM portfolio_holdings
                         WHERE user_id = ? AND LOWER(TRIM(instrument_label)) = LOWER(?)
                         LIMIT 1""",
                    (user_id, asset_name),
                ).fetchone()
            except sqlite3.OperationalError:
                holding = None
            if not holding:
                # Wurde dieselbe Position in der App versehentlich als Aktie angelegt,
                # wird nur diese App-Korrektur in ein ETF-Holding umgewandelt. Der
                # Gesamtwert bleibt dabei im unzugeordneten Bestand erhalten und wird
                # nicht ein zweites Mal zum Vermoegen addiert.
                app_stock = conn.execute(
                    """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                         FROM investment_events
                        WHERE user_id = ? AND asset_type = 'stock' AND source = 'app'
                          AND LOWER(TRIM(asset_name)) = LOWER(?)""",
                    (user_id, asset_name),
                ).fetchone()
                if float(app_stock["net"] or 0) > 0:
                    conn.execute(
                        """DELETE FROM investment_events
                            WHERE user_id = ? AND asset_type = 'stock' AND source = 'app'
                              AND LOWER(TRIM(asset_name)) = LOWER(?)""",
                        (user_id, asset_name),
                    )
                total_row = conn.execute(
                    "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                current_total = round(max(0.0, float(total_row["current_investments"] or 0)), 2)
                crypto_row = conn.execute(
                    """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                         FROM investment_events WHERE user_id = ? AND asset_type = 'crypto'""",
                    (user_id,),
                ).fetchone()
                stocks_row = conn.execute(
                    """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                         FROM investment_events WHERE user_id = ? AND asset_type = 'stock'""",
                    (user_id,),
                ).fetchone()
                etf_row = conn.execute(
                    """SELECT COALESCE(SUM(total_invested), 0) AS total
                         FROM portfolio_holdings
                        WHERE user_id = ? AND LOWER(COALESCE(instrument_type, 'etf')) = 'etf'""",
                    (user_id,),
                ).fetchone()
                crypto_total = max(0.0, float(crypto_row["net"] or 0))
                stocks_total = max(0.0, float(stocks_row["net"] or 0))
                etf_total = max(0.0, float(etf_row["total"] or 0))
                unassigned = max(0.0, current_total - crypto_total - stocks_total - etf_total)
                total_delta = round(max(0.0, target_value - unassigned), 2)
                key_hash = hashlib.sha256(
                    f"{user_id}:{asset_name.lower()}".encode("utf-8")
                ).hexdigest()[:16]
                cursor = conn.execute(
                    """INSERT INTO portfolio_holdings
                           (user_id, instrument_key, instrument_label, isin,
                            monthly_contribution, total_invested, instrument_type,
                            valuation_enabled, updated_at)
                       VALUES (?, ?, ?, '', 0, ?, 'etf', 0, CURRENT_TIMESTAMP)""",
                    (user_id, f"app_etf_{key_hash}", asset_name, target_value),
                )
                if total_delta >= 0.01:
                    conn.execute(
                        """INSERT INTO investment_events
                           (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                           VALUES (?, ?, 'in', 'etf', ?, 'manual_adjustment', 'app',
                                   'Neue manuelle ETF-Position')""",
                        (user_id, total_delta, asset_name),
                    )
                    conn.execute(
                        "UPDATE users SET current_investments = ? WHERE user_id = ?",
                        (round(current_total + total_delta, 2), user_id),
                    )
                live_data = build_live_app_data(conn, user_id)
                conn.commit()
                return jsonify({"ok": True, "position": {
                    "id": int(cursor.lastrowid), "asset_type": "etf",
                    "asset_name": asset_name, "value": target_value,
                }, **live_data})
            current_value = round(max(0.0, float(holding["value"] or 0)), 2)
            delta = round(target_value - current_value, 2)
            total_row = conn.execute(
                "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            current_total = round(max(0.0, float(total_row["current_investments"] or 0)), 2)
            if current_total + delta < -0.009:
                return jsonify({"ok": False, "error": "investment_total_not_available"}), 400
            if abs(delta) >= 0.01:
                conn.execute(
                    """UPDATE portfolio_holdings
                           SET total_invested = ?, instrument_type = 'etf',
                               updated_at = CURRENT_TIMESTAMP
                         WHERE user_id = ? AND instrument_key = ?""",
                    (target_value, user_id, holding["instrument_key"]),
                )
                # Audit-Trail ohne falsche Investitionsbehauptung im Monatsreport.
                conn.execute(
                    """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                       VALUES (?, ?, ?, 'etf', ?, 'manual_adjustment', 'app', 'Manuelle ETF-Wertkorrektur')""",
                    (user_id, abs(delta), "in" if delta > 0 else "out", holding["instrument_label"]),
                )
                conn.execute(
                    "UPDATE users SET current_investments = ? WHERE user_id = ?",
                    (round(current_total + delta, 2), user_id),
                )
            live_data = build_live_app_data(conn, user_id)
            conn.commit()
            return jsonify({"ok": True, "position": {
                "asset_type": "etf", "asset_name": holding["instrument_label"], "value": target_value,
            }, **live_data})

        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net,
                      MAX(asset_name) AS stored_name
                 FROM investment_events
                WHERE user_id = ? AND asset_type = ? AND LOWER(TRIM(asset_name)) = LOWER(?)""",
            (user_id, asset_type, asset_name),
        ).fetchone()
        current_value = round(max(0.0, float(row["net"] or 0)), 2)
        stored_name = clean_text(row["stored_name"], asset_name)
        delta = round(target_value - current_value, 2)
        total_row = conn.execute(
            "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        current_total = round(max(0.0, float(total_row["current_investments"] or 0)), 2)
        total_delta = delta
        if asset_type == "stock" and current_value < 0.01:
            crypto_row = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                     FROM investment_events WHERE user_id = ? AND asset_type = 'crypto'""",
                (user_id,),
            ).fetchone()
            stocks_row = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                     FROM investment_events WHERE user_id = ? AND asset_type = 'stock'""",
                (user_id,),
            ).fetchone()
            try:
                etf_row = conn.execute(
                    """SELECT COALESCE(SUM(total_invested), 0) AS total
                         FROM portfolio_holdings
                        WHERE user_id = ? AND LOWER(COALESCE(instrument_type, 'etf')) = 'etf'""",
                    (user_id,),
                ).fetchone()
                etf_total = max(0.0, float(etf_row["total"] or 0))
            except sqlite3.OperationalError:
                etf_total = 0.0
            crypto_total = max(0.0, float(crypto_row["net"] or 0))
            stocks_total = max(0.0, float(stocks_row["net"] or 0))
            non_crypto_total = max(0.0, current_total - crypto_total)
            unassigned = max(0.0, non_crypto_total - etf_total - stocks_total)
            # Ein bereits im Gesamtwert enthaltener Rest wird nur benannt. Erst ein Betrag
            # oberhalb dieses Restes erhoeht das Vermoegen wirklich.
            total_delta = round(max(0.0, target_value - unassigned), 2)

        if current_total + total_delta < 0:
            return jsonify({"ok": False, "error": "investment_total_not_available"}), 400
        if abs(delta) >= 0.01:
            conn.execute(
                """INSERT INTO investment_events
                   (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                   VALUES (?, ?, ?, ?, ?, 'manual_adjustment', 'app', 'Manuelle Wertkorrektur')""",
                (user_id, abs(delta), "in" if delta > 0 else "out", asset_type, stored_name),
            )
            conn.execute(
                "UPDATE users SET current_investments = ? WHERE user_id = ?",
                (round(current_total + total_delta, 2), user_id),
            )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, "position": {
        "asset_type": asset_type, "asset_name": stored_name, "value": target_value,
    }, **live_data})


@app.route("/v1/investments", methods=["DELETE"])
def delete_investment_position():
    """Entfernt eine Position aus dem aktuellen Vermoegensstand.

    Historische Buchungen und Marktbewegungen bleiben fuer Reports erhalten. Die
    konkrete Holding, ihr Sparplan und ihr aktueller Wert werden dagegen sauber
    aus dem aktiven Depot entfernt.
    """
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    asset_type = clean_text(payload.get("asset_type")).lower()
    asset_name = clean_text(payload.get("asset_name"))
    delete_all = payload.get("delete_all") is True
    try:
        holding_id = max(0, int(payload.get("holding_id") or 0))
    except (TypeError, ValueError):
        holding_id = 0
    if (
        asset_type not in {"crypto", "stock", "etf"}
        or (not asset_name and not (delete_all and asset_type == "crypto"))
    ):
        return jsonify({"ok": False, "error": "valid_investment_position_required"}), 400

    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        begin_write(conn)

        if delete_all and asset_type == "crypto":
            rows = conn.execute(
                """SELECT COALESCE(NULLIF(TRIM(asset_name), ''), 'Krypto') AS name,
                          COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                     FROM investment_events
                    WHERE user_id = ? AND asset_type = 'crypto'
                    GROUP BY COALESCE(NULLIF(TRIM(asset_name), ''), 'Krypto')""",
                (user_id,),
            ).fetchall()
            active_positions = [
                (str(row["name"]), round(float(row["net"] or 0), 2))
                for row in rows
                if float(row["net"] or 0) >= 0.01
            ]
            total_removed = round(sum(value for _, value in active_positions), 2)
            if total_removed < 0.01:
                return jsonify({"ok": False, "error": "manual_investment_not_found"}), 404

            total_row = conn.execute(
                "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            current_total = round(max(0.0, float(total_row["current_investments"] or 0)), 2)
            if total_removed > current_total + 0.009:
                return jsonify({"ok": False, "error": "investment_total_inconsistent"}), 409

            for position_name, position_value in active_positions:
                conn.execute(
                    """INSERT INTO investment_events
                           (user_id, amount, direction, asset_type, asset_name,
                            event_type, source, note)
                       VALUES (?, ?, 'out', 'crypto', ?, 'manual_removal', 'app',
                               'Krypto-Kachel in der App entfernt')""",
                    (user_id, position_value, position_name),
                )
            conn.execute(
                "UPDATE users SET current_investments = ? WHERE user_id = ?",
                (round(current_total - total_removed, 2), user_id),
            )
            live_data = build_live_app_data(conn, user_id)
            conn.commit()
            return jsonify({
                "ok": True,
                "removed": {
                    "asset_type": "crypto",
                    "positions": len(active_positions),
                    "value": total_removed,
                },
                **live_data,
            })

        if holding_id:
            ensure_market_tracking_schema(conn)
            holding = conn.execute(
                """SELECT id, instrument_label,
                          LOWER(COALESCE(instrument_type, 'etf')) AS instrument_type,
                          COALESCE(market_value, total_invested, 0) AS current_value
                     FROM portfolio_holdings
                    WHERE id = ? AND user_id = ? LIMIT 1""",
                (holding_id, user_id),
            ).fetchone()
            if not holding:
                return jsonify({"ok": False, "error": "manual_investment_not_found"}), 404

            stored_name = str(holding["instrument_label"] or asset_name)
            stored_type = str(holding["instrument_type"] or asset_type).lower()
            current_value = round(max(0.0, float(holding["current_value"] or 0)), 2)
            total_row = conn.execute(
                "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            current_total = max(0.0, float(total_row["current_investments"] or 0))

            ensure_app_etf_position_plans_table(conn)
            conn.execute(
                "DELETE FROM app_etf_position_plans WHERE user_id = ? AND holding_id = ?",
                (user_id, int(holding["id"])),
            )
            # App-Korrekturen duerfen die geloeschte Position nicht beim naechsten
            # State-Aufbau erneut als manuelle Aktie erscheinen lassen. Historische
            # Sparplan- und Marktbewegungen bleiben dagegen bewusst erhalten.
            conn.execute(
                """DELETE FROM investment_events
                    WHERE user_id = ? AND asset_type = ?
                      AND LOWER(TRIM(asset_name)) = LOWER(?) AND source = 'app'""",
                (user_id, stored_type, stored_name),
            )
            conn.execute(
                "DELETE FROM portfolio_holdings WHERE id = ? AND user_id = ?",
                (int(holding["id"]), user_id),
            )
            conn.execute(
                "UPDATE users SET current_investments = ? WHERE user_id = ?",
                (round(max(0.0, current_total - current_value), 2), user_id),
            )
            live_data = build_live_app_data(conn, user_id)
            conn.commit()
            return jsonify({"ok": True, "removed": {
                "asset_type": stored_type, "asset_name": stored_name,
            }, **live_data})

        if asset_type == "etf":
            ensure_market_tracking_schema(conn)
            if holding_id:
                holding = conn.execute(
                    """SELECT id, instrument_label FROM portfolio_holdings
                         WHERE id = ? AND user_id = ?
                           AND instrument_key LIKE 'app_etf_%' AND valuation_enabled = 0
                         LIMIT 1""",
                    (holding_id, user_id),
                ).fetchone()
            else:
                holding = conn.execute(
                    """SELECT id, instrument_label FROM portfolio_holdings
                         WHERE user_id = ? AND LOWER(TRIM(instrument_label)) = LOWER(?)
                           AND instrument_key LIKE 'app_etf_%' AND valuation_enabled = 0
                         LIMIT 1""",
                    (user_id, asset_name),
                ).fetchone()
            if not holding:
                return jsonify({"ok": False, "error": "manual_investment_not_found"}), 404
            stored_name = str(holding["instrument_label"] or asset_name)

            # Nur der Nettoanteil der App-Ereignisse hat die verbindliche Gesamtsumme
            # tatsaechlich erhoeht. Ein ETF kann auch lediglich einen schon vorhandenen
            # unzugeordneten Bestand benannt haben; dann wird beim Loeschen kein Geld
            # vernichtet, sondern der Betrag erscheint wieder als Restbestand.
            event_row = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                     FROM investment_events
                    WHERE user_id = ? AND asset_type = 'etf'
                      AND LOWER(TRIM(asset_name)) = LOWER(?) AND source = 'app'""",
                (user_id, stored_name),
            ).fetchone()
            net = round(max(0.0, float(event_row["net"] or 0)), 2)
            total_row = conn.execute(
                "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            current_total = max(0.0, float(total_row["current_investments"] or 0))
            ensure_app_etf_position_plans_table(conn)
            conn.execute(
                "DELETE FROM app_etf_position_plans WHERE user_id = ? AND holding_id = ?",
                (user_id, int(holding["id"])),
            )
            conn.execute(
                """DELETE FROM investment_events
                    WHERE user_id = ? AND asset_type = 'etf'
                      AND LOWER(TRIM(asset_name)) = LOWER(?) AND source = 'app'""",
                (user_id, stored_name),
            )
            conn.execute(
                "DELETE FROM portfolio_holdings WHERE id = ? AND user_id = ?",
                (int(holding["id"]), user_id),
            )
            conn.execute(
                "UPDATE users SET current_investments = ? WHERE user_id = ?",
                (round(max(0.0, current_total - net), 2), user_id),
            )
            live_data = build_live_app_data(conn, user_id)
            conn.commit()
            return jsonify({"ok": True, "removed": {
                "asset_type": asset_type, "asset_name": stored_name,
            }, **live_data})

        # Krypto und manuelle Aktien koennen auch aus der frueheren Bot-Zeit stammen.
        # Statt deren Historie zu vernichten, schliesst eine Gegenbuchung die aktive
        # Position auf 0. Dadurch verschwindet sie aus dem State, bleibt in alten
        # Reports aber weiterhin nachvollziehbar.
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                 FROM investment_events
                WHERE user_id = ? AND asset_type = ?
                  AND LOWER(TRIM(asset_name)) = LOWER(?)""",
            (user_id, asset_type, asset_name),
        ).fetchone()
        net = round(max(0.0, float(row["net"] or 0)), 2)
        if net < 0.01:
            return jsonify({"ok": False, "error": "manual_investment_not_found"}), 404

        total_row = conn.execute(
            "SELECT current_investments FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        current_total = max(0.0, float(total_row["current_investments"] or 0))
        conn.execute(
            """INSERT INTO investment_events
                   (user_id, amount, direction, asset_type, asset_name,
                    event_type, source, note)
               VALUES (?, ?, 'out', ?, ?, 'manual_removal', 'app',
                       'Position in der App entfernt')""",
            (user_id, net, asset_type, asset_name),
        )
        conn.execute(
            "UPDATE users SET current_investments = ? WHERE user_id = ?",
            (round(max(0.0, current_total - net), 2), user_id),
        )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, "removed": {"asset_type": asset_type, "asset_name": asset_name}, **live_data})


@app.route("/v1/import/screenshot", methods=["POST"])
def analyze_screenshot_import():
    """Liest Bankumsatz-Zeilen, speichert aber weder Bild noch Buchung."""
    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        upload = request.files.get("image")
        if not upload:
            return jsonify({"ok": False, "error": "screenshot_required"}), 400
        image_bytes = upload.stream.read(SCREENSHOT_MAX_BYTES + 1)
        if not image_bytes or len(image_bytes) > SCREENSHOT_MAX_BYTES:
            return jsonify({"ok": False, "error": "screenshot_too_large"}), 413
        mime_type = screenshot_mime_type(image_bytes)
        if not mime_type:
            return jsonify({"ok": False, "error": "screenshot_format_unsupported"}), 415
        # Nur echte Analyseversuche zaehlen. Eine falsche Datei oder ein zu grosses
        # Bild soll dem Nutzer keinen seiner begrenzten Tagesversuche wegnehmen.
        if not screenshot_attempt_allowed(user_id):
            return jsonify({"ok": False, "error": "screenshot_daily_limit"}), 429

        try:
            result = request_screenshot_analysis(image_bytes, mime_type)
        except RuntimeError as exc:
            error = str(exc)
            status = 503
            if error == "screenshot_rate_limited":
                status = 429
            elif error == "screenshot_import_not_configured":
                status = 503
            return jsonify({"ok": False, "error": error}), status

        rows = normalize_screenshot_rows(result.get("transactions"))
        image_digest = hashlib.sha256(image_bytes).hexdigest()
        expenses = []
        ignored_income_count = 0
        for index, row in enumerate(rows):
            if row["direction"] != "expense":
                ignored_income_count += 1
                continue
            row["importKey"] = screenshot_row_key(user_id, image_digest, index, row)
            row["probableDuplicate"] = probable_expense_duplicate(conn, user_id, row)
            row["selected"] = row["confidence"] >= 0.6 and not row["probableDuplicate"]
            expenses.append(row)

    return jsonify({
        "ok": True,
        "transactions": expenses,
        "ignoredIncomeCount": ignored_income_count,
        "imageStored": False,
    })


@app.route("/v1/import/screenshot/commit", methods=["POST"])
def commit_screenshot_import():
    """Bucht bestaetigte Screenshot-Zeilen atomar und idempotent vom Giro."""
    payload = request.get_json(silent=True) or {}
    requested = payload.get("transactions")
    if not isinstance(requested, list) or not requested or len(requested) > SCREENSHOT_MAX_ROWS:
        return jsonify({"ok": False, "error": "valid_transactions_required"}), 400

    cleaned = []
    for row in requested:
        if not isinstance(row, dict):
            return jsonify({"ok": False, "error": "invalid_transaction"}), 400
        try:
            amount = round(abs(float(row.get("amount") or 0)), 2)
        except (TypeError, ValueError):
            amount = 0
        merchant = clean_text(row.get("merchant"))
        category = clean_text(row.get("category"), "Sonstiges")
        import_key = str(row.get("importKey") or "").strip().lower()
        booking_date = parse_screenshot_date(row.get("date"))
        if (
            amount <= 0 or amount > 1_000_000 or not merchant
            or category not in APP_TO_BOT_CATEGORY
            or not re.fullmatch(r"[a-f0-9]{32}", import_key)
        ):
            return jsonify({"ok": False, "error": "invalid_transaction"}), 400
        cleaned.append({
            "amount": amount,
            "merchant": merchant,
            "category": category,
            "import_key": import_key,
            "date": booking_date,
        })

    token = token_from_request()
    inserted = []
    skipped = []
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_cash_movements_table(conn)
        pilot = multi_cash_accounts_enabled(conn, user_id)
        screenshot_account_id = None
        if pilot:
            prepare_multi_cash_write(conn)
            screenshot_account_id = role_financial_account_id(conn, user_id, "screenshot")
        balances = app_cash_accounts(conn, user_id)
        total_applied = 0.0
        for row in cleaned:
            description = f"Via Rov.E Screenshot · {row['import_key']}"
            existing = conn.execute(
                "SELECT id FROM expenses WHERE user_id = ? AND description = ? LIMIT 1",
                (user_id, description),
            ).fetchone()
            if existing:
                skipped.append({"importKey": row["import_key"], "reason": "already_imported"})
                continue

            bot_category = category_rule_for_merchant(conn, user_id, row["merchant"])
            bot_category = bot_category or APP_TO_BOT_CATEGORY[row["category"]]
            created_at = (
                f"{row['date']} 12:00:00" if row["date"]
                else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            if pilot:
                cur = conn.execute(
                    """INSERT INTO expenses
                           (user_id, amount, category, merchant, description, created_at, account_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, row["amount"], bot_category, row["merchant"], description,
                     created_at, screenshot_account_id),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO expenses
                           (user_id, amount, category, merchant, description, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (user_id, row["amount"], bot_category, row["merchant"], description, created_at),
                )
            expense_id = int(cur.lastrowid)
            total_applied = round(total_applied + row["amount"], 2)
            if pilot:
                conn.execute(
                    """INSERT INTO app_cash_movements
                           (user_id, kind, amount, expense_id, source_account_id)
                       VALUES (?, 'card', ?, ?, ?)""",
                    (user_id, row["amount"], expense_id, screenshot_account_id),
                )
            else:
                balances["giro"] = round(balances["giro"] - row["amount"], 2)
                conn.execute(
                    """INSERT INTO app_cash_movements (user_id, kind, amount, expense_id)
                       VALUES (?, 'card', ?, ?)""",
                    (user_id, row["amount"], expense_id),
                )
            award_tracking_points(conn, user_id, expense_id=expense_id)
            inserted.append({
                "id": expense_id,
                "importKey": row["import_key"],
                "amount": row["amount"],
                "merchant": row["merchant"],
            })

        if pilot and total_applied > 0:
            balances = adjust_financial_account_balance(
                conn, user_id, screenshot_account_id, -total_applied
            )
        elif not pilot:
            save_app_cash_accounts(conn, user_id, balances)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "inserted": inserted,
        "skipped": skipped,
        "accounts": balances,
        "available": live_data["sts"]["available"],
    })


@app.route("/v1/reports/<report_month>/pdf", methods=["GET"])
def download_report_pdf(report_month: str):
    """Liefert nur dem gekoppelten Nutzer seinen tatsaechlich versendeten PDF-Report."""
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", report_month or ""):
        return jsonify({"ok": False, "error": "invalid_report_month"}), 400

    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        try:
            sent = conn.execute(
                """SELECT 1 FROM report_jobs
                    WHERE user_id = ? AND report_month = ? AND status = 'sent' LIMIT 1""",
                (user_id, report_month),
            ).fetchone()
        except sqlite3.OperationalError:
            sent = None
        if not sent:
            return jsonify({"ok": False, "error": "report_not_available"}), 404

    filename = f"rove_report_{user_id}_{report_month}.pdf"
    pdf_path = REPORTS_DIR / filename
    if pdf_path.is_file():
        return send_file(pdf_path, mimetype="application/pdf", as_attachment=False, download_name=filename)

    archive_path = REPORTS_ARCHIVE_DIR / f"{filename}.gz"
    if archive_path.is_file():
        with gzip.open(archive_path, "rb") as compressed:
            payload = io.BytesIO(compressed.read())
        payload.seek(0)
        return send_file(payload, mimetype="application/pdf", as_attachment=False, download_name=filename)
    return jsonify({"ok": False, "error": "report_file_missing"}), 404


def export_table_rows(conn: sqlite3.Connection, table: str, user_id: int) -> tuple[list[str], list[dict]]:
    """Liest nur Tabellen aus DATA_EXPORT_TABLES und nur Zeilen des angemeldeten Nutzers."""
    allowed = {table_name for _filename, table_name in DATA_EXPORT_TABLES}
    if table not in allowed:
        raise ValueError("export_table_not_allowed")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not exists:
        return [], []
    columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
    if "user_id" not in columns:
        return [], []
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,))]
    return columns, rows


def safe_csv_value(value: object) -> object:
    """Verhindert, dass frei eingegebene Texte beim Oeffnen als Tabellenformel laufen."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def add_csv_to_export(archive: zipfile.ZipFile, filename: str, columns: list[str], rows: list[dict]) -> None:
    if not columns:
        return
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([safe_csv_value(row.get(column)) for column in columns])
    archive.writestr(f"daten/{filename}.csv", output.getvalue().encode("utf-8-sig"))


@app.route("/v1/data-export", methods=["GET"])
def download_data_export():
    """Erzeugt einen vollstaendigen, kurzlebigen Rov.E-Datenexport direkt im Arbeitsspeicher."""
    token = token_from_request()
    with db() as conn:
        ensure_auth_tables(conn)
        token_user_id = user_from_token(conn, token)
        session = session_user_from_cookie(conn)
        if not token_user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if not session:
            return jsonify({"ok": False, "error": "reauthentication_required"}), 401
        session_user_id, _session_id = session
        if session_user_id != token_user_id:
            return jsonify({"ok": False, "error": "account_mismatch"}), 403

        identity = conn.execute(
            """SELECT email, display_name, verified_at, created_at, updated_at
                 FROM app_accounts WHERE user_id = ? ORDER BY id DESC LIMIT 1""",
            (token_user_id,),
        ).fetchone()
        exported_tables: dict[str, list[dict]] = {}
        exported_columns: dict[str, list[str]] = {}
        for filename, table in DATA_EXPORT_TABLES:
            columns, rows = export_table_rows(conn, table, token_user_id)
            exported_columns[filename] = columns
            exported_tables[filename] = rows
        conn.commit()

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    account_data = dict(identity) if identity else {}
    complete_export = {
        "export_version": 1,
        "generated_at": generated_at,
        "account": account_data,
        "data": exported_tables,
    }
    payload = io.BytesIO()
    included_reports: list[str] = []
    missing_reports: list[str] = []
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            (
                "Rov.E Datenexport\n"
                "=================\n\n"
                f"Erstellt: {generated_at}\n\n"
                "daten.json enthaelt den vollstaendigen maschinenlesbaren Export.\n"
                "Der Ordner daten enthaelt dieselben Bereiche zusaetzlich als CSV-Dateien.\n"
                "Der Ordner reports enthaelt alle auf dem Server vorhandenen statischen PDF-Reports.\n"
                "Abgelaufene Weblinks sowie Sicherheitsdaten wie Login-Codes, Sessions und Tokens\n"
                "sind aus Sicherheitsgruenden nicht enthalten.\n"
            ).encode("utf-8"),
        )
        archive.writestr(
            "daten.json",
            json.dumps(complete_export, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        if account_data:
            archive.writestr(
                "daten/konto.json",
                json.dumps(account_data, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        for filename, _table in DATA_EXPORT_TABLES:
            add_csv_to_export(
                archive,
                filename,
                exported_columns.get(filename, []),
                exported_tables.get(filename, []),
            )

        sent_months = sorted({
            str(row.get("report_month") or "")
            for row in exported_tables.get("reports", [])
            if row.get("status") == "sent" and re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", str(row.get("report_month") or ""))
        })
        for report_month in sent_months:
            source_name = f"rove_report_{token_user_id}_{report_month}.pdf"
            pdf_path = REPORTS_DIR / source_name
            archive_path = REPORTS_ARCHIVE_DIR / f"{source_name}.gz"
            target_name = f"reports/Rov.E_Report_{report_month}.pdf"
            if pdf_path.is_file():
                archive.write(pdf_path, target_name)
                included_reports.append(report_month)
            elif archive_path.is_file():
                with gzip.open(archive_path, "rb") as compressed:
                    archive.writestr(target_name, compressed.read())
                included_reports.append(report_month)
            else:
                missing_reports.append(report_month)

        archive.writestr(
            "export_info.json",
            json.dumps(
                {
                    "generated_at": generated_at,
                    "included_reports": included_reports,
                    "missing_report_files": missing_reports,
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )

    payload.seek(0)
    filename = f"RovE_Datenexport_{datetime.now().strftime('%Y-%m-%d')}.zip"
    return send_file(payload, mimetype="application/zip", as_attachment=True, download_name=filename)


@app.route("/v1/account/delete-code", methods=["POST"])
def request_account_delete_code():
    """Sendet einen separaten Code fuer die irreversible Kontoloeschung."""
    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        ensure_auth_tables(conn)
        token_user_id = user_from_token(conn, token)
        session = session_user_from_cookie(conn)
        if not token_user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if not session:
            return jsonify({"ok": False, "error": "reauthentication_required"}), 401
        session_user_id, _session_id = session
        if session_user_id != token_user_id:
            return jsonify({"ok": False, "error": "account_mismatch"}), 403

        account = conn.execute(
            "SELECT email FROM app_accounts WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (token_user_id,),
        ).fetchone()
        if not account or not normalize_email(account["email"]):
            return jsonify({"ok": False, "error": "verified_email_required"}), 409
        recent = conn.execute(
            """SELECT COUNT(*) FROM app_account_delete_codes
                WHERE user_id = ?
                  AND datetime(created_at) >= datetime('now', 'localtime', '-15 minutes')""",
            (token_user_id,),
        ).fetchone()[0]
        if int(recent or 0) >= 3:
            return jsonify({"ok": False, "error": "too_many_delete_code_requests"}), 429

        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = keyed_hash(f"delete:{token_user_id}:{code}")
        conn.execute(
            "UPDATE app_account_delete_codes SET consumed_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (token_user_id,),
        )
        cur = conn.execute(
            """INSERT INTO app_account_delete_codes (user_id, code_hash, expires_at)
               VALUES (?, ?, ?)""",
            (
                token_user_id,
                code_hash,
                (datetime.now() + timedelta(minutes=ACCOUNT_DELETE_CODE_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        delete_code_id = int(cur.lastrowid)
        email = normalize_email(account["email"])
        conn.commit()

    try:
        send_account_delete_email(email, code)
    except RuntimeError as exc:
        with db() as conn:
            conn.execute(
                "UPDATE app_account_delete_codes SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (delete_code_id,),
            )
            conn.commit()
        app.logger.warning("Kontoloeschcode konnte nicht gesendet werden: %s", exc)
        return jsonify({"ok": False, "error": "delete_code_delivery_failed"}), 502
    return jsonify({"ok": True, "sent": True})


def ensure_account_delete_cleanup_table(conn: sqlite3.Connection) -> None:
    account_delete_cleanup.ensure_table(conn)


def account_delete_cleanup_roots() -> tuple[Path, Path, Path, Path]:
    return (PUBLIC_APP_STATE_DIR, PUBLIC_REPORT_DIR, REPORTS_DIR, REPORTS_ARCHIVE_DIR)


def _cleanup_path_allowed(path: Path) -> bool:
    return account_delete_cleanup.path_allowed(path, account_delete_cleanup_roots())


def _remove_cleanup_path(path: Path) -> str | None:
    return account_delete_cleanup.remove_path(path, account_delete_cleanup_roots())


def queue_account_cleanup_failures(paths: list[Path]) -> None:
    account_delete_cleanup.queue_paths(DB_PATH, account_delete_cleanup_roots(), paths)


def retry_account_delete_file_cleanup(limit: int = 20) -> int:
    """Retries only persisted, allowlisted leftovers; safe to call from maintenance."""
    return account_delete_cleanup.retry_paths(DB_PATH, account_delete_cleanup_roots(), limit)


def remove_deleted_account_files(user_id: int, state_tokens: list[str], html_paths: list[str]) -> list[Path]:
    """Entfernt nutzerbezogene Dateien nur aus den bekannten Rov.E-Verzeichnissen."""
    errors: list[Path] = []
    for token in state_tokens:
        state_path = PUBLIC_APP_STATE_DIR / f"{token}.json"
        if _remove_cleanup_path(state_path): errors.append(state_path)

    try:
        public_report_root = PUBLIC_REPORT_DIR.resolve()
    except OSError:
        public_report_root = PUBLIC_REPORT_DIR
    for html_path in html_paths:
        try:
            report_dir = Path(html_path).resolve().parent
            if report_dir.parent == public_report_root and _remove_cleanup_path(report_dir): errors.append(report_dir)
        except OSError:
            errors.append(Path(html_path))

    for pdf_path in REPORTS_DIR.glob(f"rove_report_{user_id}_*.pdf"):
        if _remove_cleanup_path(pdf_path): errors.append(pdf_path)
    for archive_path in REPORTS_ARCHIVE_DIR.glob(f"rove_report_{user_id}_*.pdf.gz"):
        if _remove_cleanup_path(archive_path): errors.append(archive_path)
    return errors


@app.route("/v1/account", methods=["DELETE"])
def delete_account():
    """Loescht das App-Konto und alle direkt zugeordneten Rov.E-Finanzdaten."""
    payload = request.get_json(silent=True) or {}
    phrase = str(payload.get("confirmation") or "").strip().upper()
    code = re.sub(r"\D", "", str(payload.get("code") or ""))[:6]
    if phrase not in {"LÖSCHEN", "LOESCHEN"}:
        return jsonify({"ok": False, "error": "delete_confirmation_required"}), 400
    if len(code) != 6:
        return jsonify({"ok": False, "error": "valid_delete_code_required"}), 400

    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        ensure_auth_tables(conn)
        token_user_id = user_from_token(conn, token)
        session = session_user_from_cookie(conn)
        if not token_user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        if not session:
            return jsonify({"ok": False, "error": "reauthentication_required"}), 401
        session_user_id, _session_id = session
        if session_user_id != token_user_id:
            return jsonify({"ok": False, "error": "account_mismatch"}), 403

        delete_code = conn.execute(
            """SELECT id, code_hash, attempts
                 FROM app_account_delete_codes
                WHERE user_id = ?
                  AND consumed_at IS NULL
                  AND datetime(expires_at) >= datetime('now', 'localtime')
                ORDER BY datetime(created_at) DESC, id DESC LIMIT 1""",
            (token_user_id,),
        ).fetchone()
        if not delete_code:
            return jsonify({"ok": False, "error": "delete_code_expired"}), 401
        if int(delete_code["attempts"] or 0) >= 5:
            return jsonify({"ok": False, "error": "too_many_delete_code_attempts"}), 429
        expected_hash = keyed_hash(f"delete:{token_user_id}:{code}")
        if not hmac.compare_digest(str(delete_code["code_hash"]), expected_hash):
            conn.execute(
                "UPDATE app_account_delete_codes SET attempts = attempts + 1 WHERE id = ?",
                (delete_code["id"],),
            )
            conn.commit()
            return jsonify({"ok": False, "error": "invalid_delete_code"}), 401

        account_rows = conn.execute(
            "SELECT id, email FROM app_accounts WHERE user_id = ?", (token_user_id,)
        ).fetchall()
        account_ids = [int(row["id"]) for row in account_rows]
        emails = [normalize_email(row["email"]) for row in account_rows if normalize_email(row["email"])]
        state_tokens = [
            str(row["token"]) for row in conn.execute(
                "SELECT token FROM app_state_links WHERE user_id = ?", (token_user_id,)
            )
        ]
        html_paths = [
            str(row["html_path"]) for row in conn.execute(
                "SELECT html_path FROM report_links WHERE user_id = ?", (token_user_id,)
            )
        ] if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_links'"
        ).fetchone() else []

        conn.execute("PRAGMA defer_foreign_keys = ON")
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            conn.execute(f"DELETE FROM app_password_reset_codes WHERE account_id IN ({placeholders})", account_ids)
            conn.execute(f"DELETE FROM app_credentials WHERE account_id IN ({placeholders})", account_ids)
            conn.execute(f"DELETE FROM app_sessions WHERE account_id IN ({placeholders})", account_ids)
        for email in emails:
            conn.execute("DELETE FROM app_login_codes WHERE email = ?", (email,))

        # Diese Kindtabelle muss vor portfolio_holdings weg; danach entfernt die dynamische
        # user_id-Schleife auch neue, spaeter hinzukommende Rov.E-Tabellen automatisch.
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_etf_position_plans'"
        ).fetchone():
            conn.execute("DELETE FROM app_etf_position_plans WHERE user_id = ?", (token_user_id,))

        # Rollen zuerst entfernen. Damit bleibt die Loeschung auch dann korrekt, wenn
        # Foreign Keys fuer diese Verbindung spaeter global aktiviert werden.
        delete_financial_account_data(conn, token_user_id)

        tables = [str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        excluded = {
            "users", "app_accounts", "app_sessions", "app_login_codes",
            "app_financial_account_roles", "app_financial_accounts", "app_user_features",
        }
        for table in tables:
            if table in excluded:
                continue
            columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")')}
            if "user_id" not in columns:
                continue
            quoted_table = '"' + table.replace('"', '""') + '"'
            conn.execute(f"DELETE FROM {quoted_table} WHERE user_id = ?", (token_user_id,))

        conn.execute("DELETE FROM app_accounts WHERE user_id = ?", (token_user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (token_user_id,))
        conn.commit()

    cleanup_errors = remove_deleted_account_files(token_user_id, state_tokens, html_paths)
    if cleanup_errors:
        queue_account_cleanup_failures(cleanup_errors)
        app.logger.error("Kontodaten geloescht, Dateibereinigung wartet auf Retry: %s", len(cleanup_errors))
    resp = make_response(jsonify({"ok": True, "deleted": True, "cleanupPending": bool(cleanup_errors)}))
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/app-api/")
    return resp


@app.route("/v1/expenses", methods=["POST"])
def create_expense():
    payload = request.get_json(silent=True) or {}

    try:
        amount = abs(float(payload.get("amount") or 0))
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return jsonify({"ok": False, "error": "amount_required"}), 400

    app_category = clean_text(payload.get("category"), "Sonstiges")
    bot_category = APP_TO_BOT_CATEGORY.get(app_category, "SONSTIGES")
    merchant = clean_text(payload.get("merchant") or payload.get("name"), "App-Buchung")
    description = clean_text(payload.get("description"), "Via Rov.E App")
    request_id = clean_text(payload.get("request_id") or payload.get("idempotency_key"))[:128] or None
    # Bar bezahlt ("30 Euro Doener mit Bargeld bezahlt", Furkan 25.07.): eine ganz normale
    # Ausgabe fuer Budget/Bot/Report — das Geld kommt aber aus dem Portemonnaie, nicht vom
    # Girokonto. Beides in EINEM Aufruf, damit Buchung und Bargeldstand nie halb gespeichert
    # sind und die App keine zweite Runde ueber /v1/accounts drehen muss.
    paid_cash = bool(payload.get("paid_cash"))

    token = token_from_request()
    with db() as conn:
        begin_expense_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_expense_request_id_schema(conn)
        bot_category = category_rule_for_merchant(conn, user_id, merchant) or bot_category
        try:
            result = create_expense_for_user(
                conn,
                user_id,
                amount=amount,
                category=bot_category,
                merchant=merchant,
                description=description,
                request_id=request_id,
                paid_cash=paid_cash,
            )
        except (LookupError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        balances = app_cash_accounts(conn, user_id)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "id": result["id"],
        "user_id": user_id,
        "amount": result["amount"],
        "category": result["category"],
        "merchant": result["merchant"],
        "paid_cash": result["paid_cash"],
        "cash_applied": result["cash_applied"],
        "giro_applied": result["giro_applied"],
        "reward": result["reward"],
        "idempotent_replay": result["idempotent_replay"],
        "accounts": balances,
        "available": live_data["sts"]["available"],
    })


@app.route("/v1/income", methods=["POST"])
def create_income():
    """Speichert eine in der App erfasste Einnahme dauerhaft und hebt das Girokonto.

    Bis 26.07. gab es diesen Weg nicht: `syncExpenseToServer()` stieg bei `a >= 0` aus,
    "Gehalt 2450" hob das Konto nur im Browser und der 45s-Refresh entfernte Geld UND
    Buchungszeile kommentarlos wieder. Da Ausgaben das Konto seit dem Karten-Dauerabzug
    dauerhaft senken, lief der Kontostand systematisch nach unten.

    Bewusst NICHT in `expenses`: dort wuerde die Einnahme als Ausgabe in Budget, Bot und
    Report zaehlen. Und bewusst NICHT als Aenderung an `users.income` — das ist das
    monatliche Einkommen im Profil, keine einzelne Buchung.
    """
    payload = request.get_json(silent=True) or {}
    try:
        amount = abs(float(payload.get("amount") or 0))
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return jsonify({"ok": False, "error": "amount_required"}), 400
    label = clean_text(payload.get("label") or payload.get("name"), "Einnahme")

    token = token_from_request()
    with db() as conn:
        begin_write(conn)   # siehe begin_write(): Lesen und Schreiben muessen ein Block sein
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_cash_movements_table(conn)
        applied = round(amount, 2)
        # gelesen unter der Sperre aus begin_write() — sonst geht eine von zwei
        # gleichzeitigen Einnahmen beim Girostand verloren
        balances = app_cash_accounts(conn, user_id)
        if multi_cash_accounts_enabled(conn, user_id):
            prepare_multi_cash_write(conn)
            target_account_id = role_financial_account_id(conn, user_id, "income")
            balances = adjust_financial_account_balance(
                conn, user_id, target_account_id, applied
            )
            cur = conn.execute(
                """INSERT INTO app_cash_movements
                       (user_id, kind, amount, label, target_account_id)
                   VALUES (?, 'income', ?, ?, ?)""",
                (user_id, applied, label, target_account_id),
            )
        else:
            balances["giro"] = round(balances["giro"] + applied, 2)
            save_app_cash_accounts(conn, user_id, balances)
            cur = conn.execute(
                """INSERT INTO app_cash_movements (user_id, kind, amount, label)
                   VALUES (?, 'income', ?, ?)""",
                (user_id, applied, label),
            )
        movement_id = cur.lastrowid
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "id": movement_id,
        "amount": applied,
        "label": label,
        "accounts": balances,
        "available": live_data["sts"]["available"],
    })


@app.route("/v1/expenses/<int:expense_id>/category", methods=["POST"])
def update_expense_category(expense_id: int):
    """Korrigiert eine Buchung und merkt die Kategorie für den Händler zentral."""
    payload = request.get_json(silent=True) or {}
    bot_category = APP_TO_BOT_CATEGORY.get(clean_text(payload.get("category")))
    if not bot_category:
        return jsonify({"ok": False, "error": "valid_category_required"}), 400

    token = token_from_request()
    with db() as conn:
        begin_write(conn)
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        row = conn.execute(
            "SELECT merchant FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "expense_not_found"}), 404

        conn.execute(
            "UPDATE expenses SET category = ? WHERE id = ? AND user_id = ?",
            (bot_category, expense_id, user_id),
        )
        save_category_rule(conn, user_id, str(row["merchant"] or ""), bot_category)
        conn.commit()

    return jsonify({
        "ok": True,
        "id": expense_id,
        "category": BOT_TO_APP_CATEGORY[bot_category],
        "merchant": str(row["merchant"] or ""),
    })


@app.route("/v1/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id: int):
    """Loescht eine Buchung dauerhaft aus der DB (App-Pendant zu /undo im Bot).

    Ohne diesen Endpunkt konnte die App nur ihren eigenen RAM-Stand aendern; der
    45s-Refresh hat die Buchung danach aus der DB zurueckgeholt (Bug 25.07.).
    Das user_id im WHERE ist Pflicht — sonst koennte ein gueltiges Token fremde
    Zeilen loeschen, indem es einfach IDs durchprobiert.
    """
    token = token_from_request()
    with db() as conn:
        begin_write(conn)   # siehe begin_write()
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_cash_movements_table(conn)
        ensure_financial_account_reference_schema(conn)
        # Die Erstattung unten liest den Kontostand und rechnet von dort hoch. Ohne die
        # Sperre aus begin_write() koennte eine parallele Buchung dazwischenschreiben und
        # die Gutschrift wieder verschlucken.
        cash_movement = conn.execute(
            """SELECT id, kind, amount, source_account_id FROM app_cash_movements
                 WHERE user_id = ? AND kind IN ('payment', 'card') AND expense_id = ?""",
            (user_id, expense_id),
        ).fetchone()

        expense = conn.execute(
            "SELECT created_at, account_id FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        ).fetchone()
        if not expense:
            return jsonify({"ok": False, "error": "expense_not_found"}), 404

        cur = conn.execute(
            "DELETE FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        )
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "expense_not_found"}), 404

        reward_reversed = reverse_tracking_points_for_deleted_expense(
            conn, user_id, expense_id, str(expense["created_at"] or "")
        )

        # Beim Löschen geht genau der damals abgezogene Betrag auf sein Ursprungskonto zurück.
        # Alte Bot-Ausgaben ohne App-Kontowirkung haben keine Bewegungszeile und erhalten
        # bewusst keine Gutschrift.
        refunded_cash = 0.0
        refunded_giro = 0.0
        if cash_movement:
            refund = round(max(0.0, float(cash_movement["amount"] or 0)), 2)
            saved_account_id = int(
                cash_movement["source_account_id"] or expense["account_id"] or 0
            )
            target = "bargeld" if cash_movement["kind"] == "payment" else "giro"
            if refund > 0:
                if saved_account_id:
                    saved_account = require_financial_account(conn, user_id, saved_account_id)
                    target = str(saved_account["legacy_key"] or target)
                    balances = adjust_stored_account_balance(
                        conn, user_id, saved_account_id, refund
                    )
                else:
                    balances = app_cash_accounts(conn, user_id)
                    balances[target] = round(balances[target] + refund, 2)
                    save_app_cash_accounts(conn, user_id, balances)
            if target == "bargeld":
                refunded_cash = refund
            else:
                refunded_giro = refund
            conn.execute(
                "DELETE FROM app_cash_movements WHERE id = ? AND user_id = ?",
                (cash_movement["id"], user_id),
            )

        balances = app_cash_accounts(conn, user_id)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "id": expense_id,
        "refunded_cash": refunded_cash,
        "refunded_giro": refunded_giro,
        "reward_reversed": reward_reversed,
        "accounts": balances,
        "available": live_data["sts"]["available"],
    })


@app.route("/v1/cash-movements/<int:movement_id>", methods=["DELETE"])
def delete_cash_movement(movement_id: int):
    """Nimmt eine Bargeld-Abhebung zurueck: Geld zurueck aufs Girokonto, Zeile weg.

    Getrennter Endpunkt, weil eine Abhebung nicht in `expenses` steht. Sie hat deshalb auch
    keine `sid`, sondern eine `csid` — ein DELETE /v1/expenses/<csid> wuerde sonst eine
    fremde Ausgabe mit derselben Nummer treffen.
    """
    token = token_from_request()
    with db() as conn:
        begin_write(conn)   # siehe begin_write()
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_cash_movements_table(conn)
        # Auch hier gilt die Sperre aus begin_write(): die Rueckbuchung unten rechnet
        # vom gelesenen Stand aus, und die Bargeld-Pruefung darf nicht auf einem Wert
        # entscheiden, den ein paralleler Request gerade veraendert.
        ensure_financial_account_reference_schema(conn)
        row = conn.execute(
            """SELECT id, kind, amount, source_account_id, target_account_id
                 FROM app_cash_movements WHERE id = ? AND user_id = ?""",
            (movement_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "cash_movement_not_found"}), 404
        kind = clean_text(row["kind"]).lower()
        if kind not in ("withdrawal", "income", "fixed"):
            # 'payment'- und 'card'-Zeilen haengen an einer Ausgabe und werden ueber
            # DELETE /v1/expenses/<id> mitgeloescht, nie einzeln.
            return jsonify({"ok": False, "error": "cash_movement_not_reversible"}), 400

        amount = round(abs(float(row["amount"] or 0)), 2)
        balances = app_cash_accounts(conn, user_id)
        source_account_id = int(row["source_account_id"] or 0)
        target_account_id = int(row["target_account_id"] or 0)
        used_financial_reference = False
        if kind == "fixed" and source_account_id:
            balances = adjust_stored_account_balance(
                conn, user_id, source_account_id, amount
            )
            used_financial_reference = True
        elif kind == "income" and target_account_id:
            balances = adjust_stored_account_balance(
                conn, user_id, target_account_id, -amount
            )
            used_financial_reference = True
        elif kind == "withdrawal" and source_account_id and target_account_id:
            target = require_financial_account(conn, user_id, target_account_id)
            if amount > float(target["balance"] or 0.0) + 0.009:
                return jsonify({"ok": False, "error": "cash_already_spent"}), 400
            balances = apply_stored_account_deltas(
                conn,
                user_id,
                {source_account_id: amount, target_account_id: -amount},
                require_funds_for={target_account_id},
            )
            used_financial_reference = True
        elif kind == "fixed":
            # Fixkosten-Abbuchung zurueckgenommen (im Monatscheck versehentlich bestaetigt):
            # das Geld kommt aufs Girokonto zurueck. Der Monatscheck-Status bleibt davon
            # unberuehrt — beim naechsten Bestaetigen greift die Doppelbuchungssperre nicht
            # mehr, weil die Bewegung dann weg ist. Genau so soll es sein.
            balances["giro"] = round(balances["giro"] + amount, 2)
        elif kind == "income":
            # Einnahme geloescht: das Geld verlaesst das Girokonto wieder. Bewusst OHNE
            # Guthaben-Pruefung — das Giro darf ins Minus, das ist Furkans Entscheidung
            # (der Nutzer verantwortet sein Konto selbst). Andernfalls waere eine
            # laengst ausgegebene Einnahme unloeschbar.
            balances["giro"] = round(balances["giro"] - amount, 2)
        else:
            if amount > balances["bargeld"]:
                # Das abgehobene Geld ist schon (teilweise) ausgegeben. Wir erfinden hier kein
                # Geld zurueck aufs Girokonto — die App zeigt das und laesst die Zeile stehen.
                return jsonify({"ok": False, "error": "cash_already_spent"}), 400
            balances["bargeld"] = round(balances["bargeld"] - amount, 2)
            balances["giro"] = round(balances["giro"] + amount, 2)
        if not used_financial_reference:
            save_app_cash_accounts(conn, user_id, balances)
        conn.execute(
            "DELETE FROM app_cash_movements WHERE id = ? AND user_id = ?",
            (movement_id, user_id),
        )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "id": movement_id,
        "accounts": balances,
        "available": live_data["sts"]["available"],
    })


if __name__ == "__main__":
    port = int(os.getenv("ROVE_APP_API_PORT", "5057"))
    app.run(host="127.0.0.1", port=port)
