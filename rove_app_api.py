"""
Rov.E App API v1

Kleine, getrennte Schreibschicht fuer die Web-App. Der Telegram-Bot bleibt unveraendert
und laeuft weiter. Authentifizierung erfolgt ueber den privaten /app-State-Token aus
app_state_links. v1 schreibt bewusst nur Ausgaben in die bestehende Bot-Datenbank.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request


APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME

ALLOWED_ORIGIN = os.getenv("ROVE_APP_ALLOWED_ORIGIN", "https://getrove.de")
PUBLIC_APP_STATE_BASE_URL = os.getenv("ROVE_APP_STATE_PUBLIC_BASE_URL", "").rstrip("/")

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

app = Flask(__name__)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Vary"] = "Origin"
    return resp


@app.after_request
def after_request(resp):
    return cors(resp)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "rove-app-api", "db": str(DB_PATH)})


@app.route("/v1/expenses", methods=["OPTIONS"])
def expenses_options():
    return ("", 204)


@app.route("/v1/pair", methods=["OPTIONS"])
def pair_options():
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


@app.route("/v1/pair", methods=["POST"])
def pair_app():
    """Verbindet eine installierte PWA einmalig mit dem Telegram-App-Code."""
    if not PUBLIC_APP_STATE_BASE_URL:
        return jsonify({"ok": False, "error": "app_state_not_configured"}), 503

    payload = request.get_json(silent=True) or {}
    code = clean_pairing_code(payload.get("code"))
    if not code:
        return jsonify({"ok": False, "error": "invalid_code"}), 400

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
        conn.execute(
            "UPDATE users SET last_activity_date = ? WHERE user_id = ?",
            (datetime.now().strftime("%Y-%m-%d"), user_id),
        )
        conn.commit()

    return jsonify({
        "ok": True,
        "id": cur.lastrowid,
        "user_id": user_id,
        "amount": round(amount, 2),
        "category": bot_category,
        "merchant": merchant,
    })


if __name__ == "__main__":
    port = int(os.getenv("ROVE_APP_API_PORT", "5057"))
    app.run(host="127.0.0.1", port=port)
