"""
Rov.E App-Bridge — baut aus der Bot-Datenbank ein JSON, das die Rov.E-Web-App direkt laden kann.

Rein additiv, rein lesend, schreibt in keine bestehende Bot-Tabelle. Folgt exakt demselben
Token/Public-Dir-Muster wie rove_web_report_renderer.py (Report-Weblinks) — eigene kleine
Link-Tabelle, ein Unterordner unter public/, Ablaufdatum.

WICHTIG: Dieses Modul importiert bewusst NICHTS aus bot.py. bot.py registriert Signal-Handler
(SIGINT/SIGTERM) auf Modulebene, außerhalb des `if __name__ == "__main__"`-Blocks — ein
`from bot import ...` hier würde beim Import ein ZWEITES bot-Modul ausführen und dabei die
Signal-Handler des laufenden Bots überschreiben (kaputter Shutdown beim nächsten
`systemctl restart`). Score-Werte werden deshalb vom Aufrufer (bot.py selbst, das die Funktionen
schon im eigenen Namespace hat) übergeben, nicht hier neu berechnet.

Bekannte v1-Lücken (bewusst nicht erfunden, siehe rove-app/DATENMODELL.md — "Offene
Migrations-Punkte"):
  - Bot kennt nur EIN Sparziel, keine Budget-Tabelle, keine feine Vermögensaufteilung
    (Girokonto/Tagesgeld/Immobilie/Bargeld/Sachwerte getrennt). Die App lässt diese Bereiche
    leer bzw. lokal manuell befüllbar (Furkans eigener Plan), statt sie zu erfinden.
  - Fixkosten-Abbuchungstage sind im Bot nicht pro Posten gespeichert, nur als Monatssumme —
    sts.fixRest wird deshalb konservativ als volle Monatssumme exportiert, nicht als "Rest bis
    heute" (die App-eigene computeFixRest()-Logik braucht Tagesangaben, die der Bot nicht hat).
  - Der Bot hat noch keine einfache Vermögens-Zeitreihe — v1 exportiert eine flache Linie auf
    Höhe des aktuellen Vermögens statt eine Kurve zu erfinden.
"""
import json
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME

PUBLIC_APP_STATE_DIR = Path(
    os.getenv("ROVE_APP_STATE_PUBLIC_DIR", str(APP_DIR / "public" / "app-state"))
)
PUBLIC_APP_STATE_BASE_URL = os.getenv("ROVE_APP_STATE_PUBLIC_BASE_URL", "").rstrip("/")
APP_STATE_LINK_TTL_DAYS = int(os.getenv("ROVE_APP_STATE_LINK_TTL_DAYS", "30"))

# 1:1-Kopie von bot.py CATEGORY_LABELS (reine Daten, keine Funktion — sicher zu duplizieren,
# siehe Modul-Docstring warum wir nicht aus bot.py importieren). Per Live-Test gefunden: die
# ursprüngliche eigene .title()-Regel hier machte aus "MOBILITAET" fälschlich "Mobilitaet"
# (ohne Umlaut) statt "Mobilität" — Kategorie landete dadurch in der falschen Farbe/Sonstiges-
# Sammelkategorie statt bei Mobilität.
BOT_CATEGORY_LABELS = {
    "LEBENSMITTEL": "Lebensmittel", "MOBILITAET": "Mobilität", "RESTAURANTS": "Restaurants",
    "ABOS": "Abos", "SHOPPING": "Shopping", "FREIZEIT": "Freizeit", "DROGERIE": "Drogerie",
    "GESUNDHEIT": "Gesundheit", "SONSTIGES": "Sonstiges", "PFLEGE": "Pflege",
}
# Bot nennt die Kategorie "Restaurants" (Plural), die App "Restaurant" (Singular) — sonst
# identische Namen.
CATEGORY_LABEL_FIX = {"Restaurants": "Restaurant"}

CATEGORY_COLORS = {
    "Lebensmittel": "#E2001A", "Restaurant": "#C4123B", "Shopping": "#232F3E",
    "Mobilität": "#F5A623", "Freizeit": "#4B3F8F", "Drogerie": "#00A5B5",
    "Gesundheit": "#3E9C8F", "Sonstiges": "#6E7B8C", "Pflege": "#D66BA0", "Abos": "#8B7DF5",
}

# details-Struktur aus bot.py (fixed_costs_details, siehe /verfeinern) — flache Zahlen pro
# Unterschlüssel, kein Abbuchungstag, keine Kündbarkeit. Labels hier nur fürs Anzeigen.
DETAIL_LABELS = {
    "wohnen": {"miete": "Miete", "strom": "Strom", "gas": "Gas"},
    "mobilitaet": {"auto": "Auto", "tanken": "Tanken", "bahn": "Bahn/ÖPNV"},
    "abos": {"netflix": "Netflix", "spotify": "Spotify", "prime": "Amazon Prime", "disney": "Disney+",
             "gym": "Fitnessstudio", "handy": "Handy", "handyvertrag": "Handyvertrag",
             "icloud": "iCloud", "abo": "Abo"},
    "versicherungen": {"haftpflicht": "Haftpflicht", "bu": "Berufsunfähigkeit",
                        "rechtsschutz": "Rechtsschutz", "autoversicherung": "Autoversicherung",
                        "hausrat": "Hausrat", "krankenversicherung": "Krankenversicherung"},
    "kredite": {"immobilie": "Immobilienkredit", "hausgeld": "Hausgeld",
                "hausverwalter": "Hausverwaltung", "kredit": "Kredit"},
}
SECTION_LABELS = {
    "wohnen": "Wohnen", "mobilitaet": "Mobilität", "abos": "Abos",
    "versicherungen": "Versicherungen", "kredite": "Kredite & Wohneigentum",
}
SECTION_ICONS = {"wohnen": "house", "mobilitaet": "bolt", "abos": "film",
                  "versicherungen": "shield", "kredite": "house"}

# Icon pro einzelnem Posten (statt nur pro Sektion) — sonst kriegen z.B. Netflix UND
# Fitnessstudio dasselbe "abos"-Sektions-Icon (Live-Bug, per App-Bridge-Test 16.07. gefunden:
# beide zeigten das Film-Quadrat). Fällt auf SECTION_ICONS zurück, wenn ein Key hier fehlt.
DETAIL_ICONS = {
    "netflix": "film", "prime": "film", "disney": "film",
    "spotify": "music",
    "gym": "gym",
    "strom": "bolt", "gas": "bolt",
    "haftpflicht": "shield", "bu": "shield", "rechtsschutz": "shield",
    "autoversicherung": "shield", "hausrat": "shield", "krankenversicherung": "cross",
}

# Welche Sektionen enthalten grundsätzlich kündbare Posten (Abos, Versicherungen) statt
# Basis-Verträge (Miete/Strom/Immobilienkredit)? Live-Bug (16.07.): _build_vertraege setzte
# "cancel": False fest für JEDEN Posten, dadurch zeigte die App auch Spotify/Fitnessstudio
# fälschlich als "Basis-Vertrag" statt kündbar.
CANCELABLE_SECTIONS = {"abos", "versicherungen"}

# Bestandsgrößen innerhalb einer Sektion, keine monatlichen Fixkosten-Zeilen.
DETAIL_SKIP_KEYS = {"restschuld", "gesamtbetrag", "schulden_gesamt"}


def ensure_app_state_links_table() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_state_links (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'active'
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_app_state_links_expiry ON app_state_links(status, expires_at)"
        )
        conn.commit()


def _category_label(raw: str) -> str:
    stripped = (raw or "").strip()
    mapped = BOT_CATEGORY_LABELS.get(stripped.upper())
    label = mapped or (stripped.title() if stripped else "Sonstiges")
    return CATEGORY_LABEL_FIX.get(label, label)


def _build_tx(conn: sqlite3.Connection, user_id: int) -> list:
    rows = conn.execute(
        """SELECT amount, category, merchant, description, created_at FROM expenses
           WHERE user_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')
           ORDER BY created_at DESC""",
        (user_id,),
    ).fetchall()
    days: dict[str, list] = {}
    order: list[str] = []
    today_iso = date.today().isoformat()
    for r in rows:
        cat = _category_label(r["category"])
        name = (r["merchant"] or r["description"] or cat).strip() or cat
        created = r["created_at"] or ""
        day_key = created[:10]
        day_label = "Heute" if day_key == today_iso else (day_key or "Unbekannt")
        if day_label not in days:
            days[day_label] = []
            order.append(day_label)
        days[day_label].append({
            "n": name,
            "cat": cat,
            "a": -abs(float(r["amount"] or 0)),
            "c": CATEGORY_COLORS.get(cat, "#6E7B8C"),
            "i": (name[:1] or "?").upper(),
        })
    return [{"d": d, "items": days[d]} for d in order]


def _build_vertraege(details: dict) -> list:
    groups = []
    for section, values in (details or {}).items():
        if not isinstance(values, dict):
            continue
        labels = DETAIL_LABELS.get(section, {})
        items = []
        for key, raw_amount in values.items():
            if key in DETAIL_SKIP_KEYS:
                continue
            try:
                amount = float(raw_amount or 0)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            items.append({
                "n": labels.get(key, key.replace("_", " ").title()),
                "icon": DETAIL_ICONS.get(key, SECTION_ICONS.get(section, "euro")),
                # Bot speichert keinen Abbuchungstag pro Posten (offener Migrationspunkt,
                # siehe DATENMODELL.md) — "1." ist eine bewusste, dokumentierte Näherung.
                "date": "1.",
                "a": round(amount, 2),
                "cancel": section in CANCELABLE_SECTIONS,
            })
        if items:
            groups.append({"cat": SECTION_LABELS.get(section, section.title()), "items": items})
    return groups


def build_app_state(user_id: int, score_total: int = 0, score_label: str = "—") -> dict:
    """Baut das App-State-JSON für user_id und schreibt es nach public/app-state/<token>.json.
    score_total/score_label kommen vom Aufrufer (bot.py hat calculate_clarity_score() schon im
    eigenen Namespace, siehe Modul-Docstring, warum das hier nicht selbst berechnet wird).
    Gibt {token, path, url, expires_at} zurück."""
    ensure_app_state_links_table()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError(f"Kein User {user_id} in der Bot-Datenbank gefunden")
        u = dict(row)
        try:
            details = json.loads(u.get("fixed_costs_details") or "{}")
        except (json.JSONDecodeError, TypeError):
            details = {}

        cash = float(u.get("current_cash") or 0)
        investments = float(u.get("current_investments") or 0)
        net_worth = cash + investments
        sparraten = float(u.get("etf_savings") or 0) + float(u.get("cash_savings") or 0)
        fixed_costs = float(u.get("fixed_costs") or 0)

        tx = _build_tx(conn, user_id)
        vertraege = _build_vertraege(details)

        access = conn.execute(
            "SELECT display_name, username FROM user_access WHERE user_id = ?", (user_id,)
        ).fetchone()
        display_name = ((access["display_name"] if access else "") or
                         (access["username"] if access else "") or "")

        net_worth_k = round(net_worth / 1000, 3)
        state = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "user_id": user_id,
            "display_name": display_name,
            "netWorth": round(net_worth, 2),
            "series": {r: [net_worth_k, net_worth_k] for r in ("1W", "1M", "6M", "1J", "Max")},
            "assets": [a for a in (
                {"name": "Girokonto", "icon": "bank", "tint": "#2AABEE",
                 "value": round(cash, 2), "sub": "aus dem Bot"} if cash else None,
                {"name": "ETF & Investments", "icon": "chart", "tint": "#8B7DF5",
                 "value": round(investments, 2), "sub": "aus dem Bot"} if investments else None,
            ) if a],
            "tx": tx,
            "sts": {
                "konto": round(cash, 2),
                "fixRest": round(fixed_costs, 2),
                "sparraten": round(sparraten, 2),
            },
            "vertraege": vertraege,
            "goals": ([{
                "t": u.get("goal_description"),
                "icon": "coins", "tint": "#2AABEE",
                "cur": round(sparraten, 2),
                "tar": round(float(u.get("goal_amount") or 0), 2) or 1,
            }] if (u.get("goal_description") or "").strip() else []),
            "score": {
                "value": int(score_total or 0),
                "label": score_label or "—",
            },
        }
    finally:
        conn.close()

    token = secrets.token_urlsafe(24)
    expires_at = datetime.now() + timedelta(days=APP_STATE_LINK_TTL_DAYS)
    PUBLIC_APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PUBLIC_APP_STATE_DIR / f"{token}.json"
    output_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    public_url = f"{PUBLIC_APP_STATE_BASE_URL}/{token}.json" if PUBLIC_APP_STATE_BASE_URL else ""
    with sqlite3.connect(DB_PATH) as db_conn:
        db_conn.execute(
            "INSERT INTO app_state_links (token, user_id, expires_at, status) VALUES (?, ?, ?, 'active')",
            (token, user_id, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        db_conn.commit()

    return {"token": token, "path": output_path, "url": public_url, "expires_at": expires_at}
