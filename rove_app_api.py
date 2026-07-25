"""
Rov.E App API v1

Kleine, getrennte Schreibschicht fuer die Web-App. Der Telegram-Bot bleibt unveraendert
und laeuft weiter. Authentifizierung erfolgt ueber den privaten /app-State-Token aus
app_state_links. v1 schreibt bewusst nur Ausgaben in die bestehende Bot-Datenbank.
"""

import json
import gzip
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_file
from rove_app_state import (
    ACCOUNT_META,
    REPORTS_ARCHIVE_DIR,
    REPORTS_DIR,
    _build_tx,
    build_app_state,
    build_live_app_data,
    ensure_app_account_balances_table,
    ensure_app_cash_movements_table,
    ensure_app_contracts_table,
    ensure_app_goals_table,
    ensure_app_monthly_plan_table,
    ensure_app_properties_table,
)


APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME

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
AUTH_SESSION_TTL_DAYS = int(os.getenv("ROVE_APP_AUTH_SESSION_TTL_DAYS", "180"))
AUTH_ATTEMPT_WINDOW_SECONDS = 15 * 60
AUTH_ATTEMPT_LIMIT = 5
_auth_attempts: dict[str, list[float]] = {}
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
AUTH_SECRET = os.getenv("ROVE_APP_AUTH_SECRET", "").strip()
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
ACCOUNT_KEYS = frozenset(ACCOUNT_META)

app = Flask(__name__)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cors(resp):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
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
    return jsonify({"ok": True, "service": "rove-app-api"})


@app.route("/v1/auth/request-code", methods=["OPTIONS"])
@app.route("/v1/auth/verify-code", methods=["OPTIONS"])
@app.route("/v1/auth/me", methods=["OPTIONS"])
@app.route("/v1/auth/logout", methods=["OPTIONS"])
def auth_options():
    return ("", 204)


@app.route("/v1/expenses", methods=["OPTIONS"])
def expenses_options():
    return ("", 204)


@app.route("/v1/expenses/<int:expense_id>", methods=["OPTIONS"])
def expense_item_options(expense_id: int):
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
def accounts_options():
    return ("", 204)


@app.route("/v1/property", methods=["OPTIONS"])
def property_options():
    return ("", 204)


@app.route("/v1/investments", methods=["OPTIONS"])
def investments_options():
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


def token_from_request() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    data = request.get_json(silent=True) or {}
    return str(data.get("token") or "").strip()


def user_from_token(conn: sqlite3.Connection, token: str) -> int | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT user_id
             FROM app_state_links
            WHERE token = ?
              AND status = 'active'
              AND datetime(expires_at) >= datetime('now', 'localtime')""",
        (token,),
    ).fetchone()
    return int(row["user_id"]) if row else None


def clean_text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    return text[:80] if text else fallback


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


def auth_attempt_allowed(email: str) -> bool:
    ip = request.headers.get("X-Real-IP", request.remote_addr or "unknown")
    key = f"{ip}:{email.casefold()}"
    now = time.monotonic()
    attempts = [stamp for stamp in _auth_attempts.get(key, []) if now - stamp < AUTH_ATTEMPT_WINDOW_SECONDS]
    if len(attempts) >= AUTH_ATTEMPT_LIMIT:
        _auth_attempts[key] = attempts
        return False
    attempts.append(now)
    _auth_attempts[key] = attempts
    return True


def normalize_email(value: object) -> str:
    email = str(value or "").strip().casefold()
    return email if EMAIL_RE.match(email) and len(email) <= 254 else ""


def auth_secret() -> str:
    # Der Secret muss stabil bleiben, weil Codes und Sessions nur gehasht gespeichert werden.
    return AUTH_SECRET or BREVO_API_KEY


def keyed_hash(value: str) -> str:
    secret = auth_secret()
    if not secret:
        raise RuntimeError("app_auth_not_configured")
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_login_codes_email ON app_login_codes(email, consumed_at, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_app_sessions_hash ON app_sessions(token_hash, revoked_at, expires_at)")


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


def create_state_url_for_user(conn: sqlite3.Connection, user_id: int) -> str:
    if not PUBLIC_APP_STATE_BASE_URL:
        raise RuntimeError("app_state_not_configured")
    score_total, score_label = latest_score(conn, user_id)
    result = build_app_state(user_id, score_total, score_label)
    return str(result.get("url") or "")


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
            WHERE s.token_hash = ?
              AND s.revoked_at IS NULL
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
    if not email:
        return jsonify({"ok": False, "error": "valid_email_required"}), 400
    if not auth_attempt_allowed(email):
        return jsonify({"ok": False, "error": "too_many_login_attempts"}), 429

    try:
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = keyed_hash(f"{email}:{code}")
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    with db() as conn:
        ensure_auth_tables(conn)
        account = conn.execute("SELECT id FROM app_accounts WHERE email = ?", (email,)).fetchone()
        if not account:
            if not pairing_code:
                return jsonify({"ok": False, "error": "pairing_code_required"}), 409
            linked = conn.execute(
                """SELECT 1 FROM app_state_links
                   WHERE pairing_code = ?
                     AND status = 'active'
                     AND datetime(expires_at) >= datetime('now', 'localtime')""",
                (pairing_code,),
            ).fetchone()
            if not linked:
                return jsonify({"ok": False, "error": "invalid_or_expired_code"}), 401
        conn.execute(
            """INSERT INTO app_login_codes (email, code_hash, pairing_code, expires_at)
               VALUES (?, ?, ?, ?)""",
            (
                email,
                code_hash,
                pairing_code or None,
                (datetime.now() + timedelta(minutes=AUTH_CODE_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()

    try:
        send_login_email(email, code)
    except RuntimeError as exc:
        app.logger.warning("Login-Code an %s konnte nicht gesendet werden: %s", email, exc)
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify({"ok": True, "sent": True, "needsPairing": not bool(account)})


@app.route("/v1/auth/verify-code", methods=["POST"])
def verify_login_code():
    payload = request.get_json(silent=True) or {}
    email = normalize_email(payload.get("email"))
    code = re.sub(r"\D", "", str(payload.get("code") or ""))[:6]
    if not email or len(code) != 6:
        return jsonify({"ok": False, "error": "valid_email_and_code_required"}), 400

    try:
        code_hash = keyed_hash(f"{email}:{code}")
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    with db() as conn:
        ensure_auth_tables(conn)
        row = conn.execute(
            """SELECT id, pairing_code, attempts
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
        if account:
            account_id = int(account["id"])
            user_id = int(account["user_id"])
        else:
            pairing_code = str(row["pairing_code"] or "")
            linked = conn.execute(
                """SELECT user_id FROM app_state_links
                   WHERE pairing_code = ?
                     AND status = 'active'
                     AND datetime(expires_at) >= datetime('now', 'localtime')
                   ORDER BY datetime(created_at) DESC LIMIT 1""",
                (pairing_code,),
            ).fetchone()
            if not linked:
                return jsonify({"ok": False, "error": "pairing_code_required"}), 409
            user_id = int(linked["user_id"])
            cur = conn.execute(
                """INSERT INTO app_accounts (email, user_id, verified_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)""",
                (email, user_id),
            )
            account_id = int(cur.lastrowid)

        raw_session = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=AUTH_SESSION_TTL_DAYS)
        conn.execute(
            """INSERT INTO app_sessions (token_hash, account_id, expires_at)
               VALUES (?, ?, ?)""",
            (keyed_hash(raw_session), account_id, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.execute("UPDATE app_login_codes SET consumed_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
        conn.commit()
        state_url = create_state_url_for_user(conn, user_id)

    resp = make_response(jsonify({"ok": True, "state_url": state_url}))
    return set_session_cookie(resp, raw_session, expires_at)


@app.route("/v1/auth/me", methods=["GET"])
def auth_me():
    try:
        with db() as conn:
            ensure_auth_tables(conn)
            session = session_user_from_cookie(conn)
            if not session:
                return jsonify({"ok": False, "error": "not_logged_in"}), 401
            user_id, _session_id = session
            conn.commit()
            state_url = create_state_url_for_user(conn, user_id)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "state_url": state_url})


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
    """Spiegelt App-Verträge in Bot-Fixkosten und Monatsbudget."""
    user = conn.execute("SELECT fixed_costs_details FROM users WHERE user_id = ?", (user_id,)).fetchone()
    try:
        details = json.loads(user["fixed_costs_details"] or "{}") if user else {}
    except (json.JSONDecodeError, TypeError):
        details = {}
    rows = conn.execute(
        "SELECT detail_key, amount FROM app_contracts WHERE user_id = ?", (user_id,)
    ).fetchall()
    values = {str(row["detail_key"]): round(float(row["amount"] or 0), 2) for row in rows}
    if values:
        details["app_vertraege"] = values
    else:
        details.pop("app_vertraege", None)
    conn.execute(
        "UPDATE users SET fixed_costs_details = ?, fixed_costs = ? WHERE user_id = ?",
        (json.dumps(details, ensure_ascii=False), fixed_costs_total(details), user_id),
    )


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


@app.route("/v1/pair", methods=["POST"])
def pair_app():
    """Verbindet eine installierte PWA einmalig mit dem Telegram-App-Code."""
    if not PUBLIC_APP_STATE_BASE_URL:
        return jsonify({"ok": False, "error": "app_state_not_configured"}), 503

    payload = request.get_json(silent=True) or {}
    code = clean_pairing_code(payload.get("code"))
    if not code:
        return jsonify({"ok": False, "error": "invalid_code"}), 400
    if not pairing_attempt_allowed():
        return jsonify({"ok": False, "error": "too_many_pairing_attempts"}), 429

    with db() as conn:
        try:
            row = conn.execute(
                """SELECT token FROM app_state_links
                   WHERE pairing_code = ?
                     AND status = 'active'
                     AND datetime(expires_at) >= datetime('now', 'localtime')""",
                (code,),
            ).fetchone()
        except sqlite3.OperationalError:
            return jsonify({"ok": False, "error": "pairing_not_ready"}), 503

    if not row:
        return jsonify({"ok": False, "error": "invalid_or_expired_code"}), 401

    return jsonify({
        "ok": True,
        "state_url": f"{PUBLIC_APP_STATE_BASE_URL}/{row['token']}.json",
    })


@app.route("/v1/transactions", methods=["GET"])
def current_transactions():
    """Liest die aktuellen Monatsbuchungen fuer eine bereits gekoppelte App."""
    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        tx = _build_tx(conn, user_id)

    return jsonify({"ok": True, "tx": tx})


@app.route("/v1/state", methods=["GET"])
def current_app_state():
    """Aktualisiert die vom Bot gefuehrten Bereiche einer gekoppelten App."""
    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        state = build_live_app_data(conn, user_id)

    return jsonify({"ok": True, **state})


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
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_monthly_plan_table(conn)
        month_key = datetime.now().strftime("%Y-%m")
        field, status = field_by_action[action]
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
            already_confirmed = conn.execute(
                """SELECT 1 FROM investment_events
                     WHERE user_id = ?
                       AND source IN ('investiert_command', 'app_monthly_plan')
                       AND strftime('%Y-%m', created_at) = ?
                     LIMIT 1""",
                (user_id, month_key),
            ).fetchone()
            if not already_confirmed and (etf_savings > 0 or cash_savings > 0):
                new_investments = round(float(user["current_investments"] or 0) + etf_savings, 2)
                conn.execute(
                    "UPDATE users SET current_investments = ? WHERE user_id = ?",
                    (new_investments, user_id),
                )
                balances = app_cash_accounts(conn, user_id)
                balances["tagesgeld"] = round(balances["tagesgeld"] + cash_savings, 2)
                save_app_cash_accounts(conn, user_id, balances)
                if etf_savings > 0:
                    conn.execute(
                        """INSERT INTO investment_events
                           (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                           VALUES (?, ?, 'in', 'etf', 'ETF-Sparrate', 'recurring_plan', 'app_monthly_plan',
                                   'Monatliche ETF-Sparrate in der App bestätigt')""",
                        (user_id, etf_savings),
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


@app.route("/v1/goals", methods=["POST"])
def update_goals():
    """Verwaltet App-Ziele zentral, damit sie App-Neustarts und Geraetewechsel ueberleben."""
    payload = request.get_json(silent=True) or {}
    action = clean_text(payload.get("action")).lower()
    if action not in {"create", "assign", "set_target", "delete"}:
        return jsonify({"ok": False, "error": "valid_goal_action_required"}), 400

    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401
        ensure_app_goals_table(conn)

        if action == "create":
            name = clean_text(payload.get("name"))
            target = goal_amount(payload.get("target"))
            icon = clean_text(payload.get("icon"), "coins")
            tint = clean_text(payload.get("tint"), "#2AABEE")
            if not name or target is None or target <= 0:
                return jsonify({"ok": False, "error": "valid_goal_name_and_target_required"}), 400
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
                   (user_id, goal_id, name, target_amount, icon, tint)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, goal_id, name, target, icon, tint),
            )
        else:
            goal_id = clean_text(payload.get("goal_id"))
            if not goal_id:
                return jsonify({"ok": False, "error": "goal_id_required"}), 400
            goal = conn.execute(
                """SELECT target_amount, current_amount FROM app_goals
                     WHERE user_id = ? AND goal_id = ?""",
                (user_id, goal_id),
            ).fetchone()
            if not goal:
                return jsonify({"ok": False, "error": "goal_not_found"}), 404

            if action == "delete":
                conn.execute("DELETE FROM app_goals WHERE user_id = ? AND goal_id = ?", (user_id, goal_id))
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
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        balances = app_cash_accounts(conn, user_id)
        try:
            amount = round(abs(float(payload.get("amount") or 0)), 2)
        except (TypeError, ValueError):
            amount = 0.0

        if action == "transfer":
            source = clean_text(payload.get("from")).lower()
            target = clean_text(payload.get("to")).lower()
            if source not in ACCOUNT_KEYS or target not in ACCOUNT_KEYS or source == target:
                return jsonify({"ok": False, "error": "valid_transfer_accounts_required"}), 400
            if amount <= 0 or amount > balances[source]:
                return jsonify({"ok": False, "error": "transfer_amount_not_available"}), 400
            balances[source] = round(balances[source] - amount, 2)
            balances[target] = round(balances[target] + amount, 2)
            # Nur eine echte Abhebung wird als Buchungszeile gemerkt (die App schickt dafuer
            # log:"withdrawal"). Ein normales Umbuchen im Konten-Detail bleibt bewusst still —
            # sonst tauchten in der Buchungsliste ploetzlich Zeilen auf, die es dort nie gab.
            if clean_text(payload.get("log")).lower() == "withdrawal" and source == "giro" and target == "bargeld":
                ensure_app_cash_movements_table(conn)
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
        elif action == "adjust":
            account = clean_text(payload.get("account")).lower()
            direction = clean_text(payload.get("direction"), "add").lower()
            if account not in ACCOUNT_KEYS or direction not in {"add", "subtract"} or amount <= 0:
                return jsonify({"ok": False, "error": "valid_account_adjustment_required"}), 400
            delta = amount if direction == "add" else -amount
            if balances[account] + delta < 0 or balances[account] + delta > 10_000_000:
                return jsonify({"ok": False, "error": "account_adjustment_not_available"}), 400
            balances[account] = round(balances[account] + delta, 2)
        else:
            return jsonify({"ok": False, "error": "valid_account_action_required"}), 400

        save_app_cash_accounts(conn, user_id, balances)
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({"ok": True, "accounts": balances, **live_data})


def fixed_costs_total(details: dict) -> float:
    return round(sum(
        float(value or 0)
        for section in details.values() if isinstance(section, dict)
        for key, value in section.items()
        if key not in {"restschuld", "gesamtbetrag", "schulden_gesamt"}
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


@app.route("/v1/investments", methods=["POST"])
def update_investment_position():
    """Setzt manuelle Krypto- oder Aktienwerte, ohne Startvermoegen doppelt zu zaehlen."""
    payload = request.get_json(silent=True) or {}
    token = token_from_request()
    asset_type = clean_text(payload.get("asset_type"), "crypto").lower()
    asset_name = clean_text(payload.get("asset_name"))
    try:
        target_value = round(max(0.0, float(payload.get("value") or 0)), 2)
    except (TypeError, ValueError):
        target_value = -1.0
    if asset_type not in {"crypto", "stock"} or not asset_name or target_value < 0 or target_value > 100_000_000:
        return jsonify({"ok": False, "error": "valid_investment_position_required"}), 400

    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

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
                         FROM portfolio_holdings WHERE user_id = ?""",
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
    # Bar bezahlt ("30 Euro Doener mit Bargeld bezahlt", Furkan 25.07.): eine ganz normale
    # Ausgabe fuer Budget/Bot/Report — das Geld kommt aber aus dem Portemonnaie, nicht vom
    # Girokonto. Beides in EINEM Aufruf, damit Buchung und Bargeldstand nie halb gespeichert
    # sind und die App keine zweite Runde ueber /v1/accounts drehen muss.
    paid_cash = bool(payload.get("paid_cash"))

    token = token_from_request()
    with db() as conn:
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        cur = conn.execute(
            """INSERT INTO expenses (user_id, amount, category, merchant, description)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, amount, bot_category, merchant, description),
        )
        expense_id = cur.lastrowid
        # Jede App-Ausgabe senkt dauerhaft das Konto, aus dem sie bezahlt wurde. Ohne diese
        # Kontowirkung sprang der Girostand nach dem naechsten App-Refresh auf den alten Wert
        # zurueck, obwohl Ausgabe, Budget und Report die Buchung bereits kannten.
        ensure_app_cash_movements_table(conn)
        account_key = "bargeld" if paid_cash else "giro"
        movement_kind = "payment" if paid_cash else "card"
        balances = app_cash_accounts(conn, user_id)
        if paid_cash and amount > balances[account_key]:
            # Bargeld kann nicht ins Minus. Die Ausgabe wird gar nicht angelegt, damit
            # Buchung, Portemonnaie und Kontostand nicht auseinanderlaufen.
            return jsonify({"ok": False, "error": "cash_balance_insufficient"}), 400
        # Giro darf ins Minus gehen (z. B. 100 EUR Kontostand minus 600 EUR Ausgabe =
        # -500 EUR). Beim Loeschen wird exakt derselbe Betrag wieder gutgeschrieben.
        account_applied = round(amount, 2)
        balances[account_key] = round(balances[account_key] - account_applied, 2)
        save_app_cash_accounts(conn, user_id, balances)
        conn.execute(
            """INSERT INTO app_cash_movements (user_id, kind, amount, expense_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, movement_kind, account_applied, expense_id),
        )
        cash_applied = account_applied if paid_cash else 0.0
        giro_applied = 0.0 if paid_cash else account_applied
        conn.execute(
            "UPDATE users SET last_activity_date = ? WHERE user_id = ?",
            (datetime.now().strftime("%Y-%m-%d"), user_id),
        )
        live_data = build_live_app_data(conn, user_id)
        conn.commit()

    return jsonify({
        "ok": True,
        "id": expense_id,
        "user_id": user_id,
        "amount": round(amount, 2),
        "category": bot_category,
        "merchant": merchant,
        "paid_cash": paid_cash,
        "cash_applied": cash_applied,
        "giro_applied": giro_applied,
        "accounts": balances,
        "available": live_data["sts"]["available"],
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
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_cash_movements_table(conn)
        cash_movement = conn.execute(
            """SELECT id, kind, amount FROM app_cash_movements
                 WHERE user_id = ? AND kind IN ('payment', 'card') AND expense_id = ?""",
            (user_id, expense_id),
        ).fetchone()

        cur = conn.execute(
            "DELETE FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        )
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "expense_not_found"}), 404

        # Beim Löschen geht genau der damals abgezogene Betrag auf sein Ursprungskonto zurück.
        # Alte Bot-Ausgaben ohne App-Kontowirkung haben keine Bewegungszeile und erhalten
        # bewusst keine Gutschrift.
        refunded_cash = 0.0
        refunded_giro = 0.0
        if cash_movement:
            refund = round(max(0.0, float(cash_movement["amount"] or 0)), 2)
            target = "bargeld" if cash_movement["kind"] == "payment" else "giro"
            if refund > 0:
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
        user_id = user_from_token(conn, token)
        if not user_id:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 401

        ensure_app_cash_movements_table(conn)
        row = conn.execute(
            "SELECT id, kind, amount FROM app_cash_movements WHERE id = ? AND user_id = ?",
            (movement_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "cash_movement_not_found"}), 404
        if clean_text(row["kind"]).lower() != "withdrawal":
            # 'payment'-Zeilen haengen an einer Ausgabe und werden ueber
            # DELETE /v1/expenses/<id> mitgeloescht, nie einzeln.
            return jsonify({"ok": False, "error": "cash_movement_not_reversible"}), 400

        amount = round(abs(float(row["amount"] or 0)), 2)
        balances = app_cash_accounts(conn, user_id)
        if amount > balances["bargeld"]:
            # Das abgehobene Geld ist schon (teilweise) ausgegeben. Wir erfinden hier kein Geld
            # zurueck aufs Girokonto — die App zeigt das und laesst die Zeile stehen.
            return jsonify({"ok": False, "error": "cash_already_spent"}), 400

        balances["bargeld"] = round(balances["bargeld"] - amount, 2)
        balances["giro"] = round(balances["giro"] + amount, 2)
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
