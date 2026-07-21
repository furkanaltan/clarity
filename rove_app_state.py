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
PUBLIC_APP_API_BASE_URL = os.getenv("ROVE_APP_API_BASE_URL", "").rstrip("/")

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
    "gym": "gym", "handy": "doc", "handyvertrag": "doc", "icloud": "doc", "abo": "doc",
    "strom": "bolt", "gas": "bolt",
    "auto": "car", "tanken": "car", "bahn": "bolt",
    "haftpflicht": "shield", "bu": "shield", "rechtsschutz": "doc",
    "autoversicherung": "car", "hausrat": "house", "krankenversicherung": "cross",
    "immobilie": "house", "hausgeld": "coins", "hausverwalter": "doc", "kredit": "bank",
}

# Farbe pro Sektion als Fallback, wenn ein Posten nicht in DETAIL_TINTS steht. Ohne "tint"
# fällt die App auf ihr Standard-Blaugrau zurück (index.html: v.tint||'#8FA8BC') — genau das
# fehlte hier komplett (Live-Bug 16.07., zweiter Teil desselben Berichts: Icons stimmten schon,
# aber jeder Posten sah trotzdem gleich blaugrau eingefärbt aus).
SECTION_TINTS = {"wohnen": "#5B6675", "mobilitaet": "#D07D00", "abos": "#8B7DF5",
                  "versicherungen": "#3E9C8F", "kredite": "#D8B66A"}
DETAIL_TINTS = {
    "strom": "#FFD000", "gas": "#FFD000",
    "netflix": "#E50914", "spotify": "#1DB954", "prime": "#00A8E1", "disney": "#113CCF",
    "gym": "#E8622C", "handy": "#8B7DF5", "handyvertrag": "#8B7DF5", "icloud": "#2AABEE",
    "auto": "#D07D00", "tanken": "#D07D00", "bahn": "#2AABEE",
    "haftpflicht": "#35D07F", "bu": "#8B7DF5", "rechtsschutz": "#2AABEE",
    "autoversicherung": "#3E9C8F", "hausrat": "#D8B66A", "krankenversicherung": "#3E9C8F",
    "immobilie": "#D8B66A", "hausgeld": "#D8B66A", "hausverwalter": "#8FA8BC", "kredit": "#D8B66A",
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
                status     TEXT NOT NULL DEFAULT 'active',
                pairing_code TEXT
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(app_state_links)")}
        if "pairing_code" not in columns:
            conn.execute("ALTER TABLE app_state_links ADD COLUMN pairing_code TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_app_state_links_expiry ON app_state_links(status, expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_app_state_links_pairing ON app_state_links(pairing_code, status)"
        )
        conn.commit()


PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _new_pairing_code(conn: sqlite3.Connection) -> str:
    """Erzeugt einen gut lesbaren, aber praktisch nicht erratbaren 8-Zeichen-App-Code."""
    for _ in range(20):
        raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        exists = conn.execute(
            "SELECT 1 FROM app_state_links WHERE pairing_code = ? AND status = 'active'", (code,)
        ).fetchone()
        if not exists:
            return code
    raise RuntimeError("Konnte keinen eindeutigen App-Code erzeugen")


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


def _build_budgets(conn: sqlite3.Connection, user_id: int) -> list:
    """Liefert die vom Nutzer im Bot gesetzten Monatsrahmen fuer die App.

    Die App darf bei einer gekoppelten Sitzung keine eigenen Limits aus vergangenen
    Buchungen ableiten. Sonst unterscheiden sich App und Bot trotz derselben DB.
    """
    try:
        rows = conn.execute(
            """SELECT category, monthly_limit, source
                 FROM category_budgets
                WHERE user_id = ?
                  AND active_month = strftime('%Y-%m', 'now', 'localtime')
                ORDER BY category""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    budgets = []
    for row in rows:
        category = _category_label(row["category"])
        budgets.append({
            "cat": category,
            "limit": round(float(row["monthly_limit"] or 0), 2),
            "tint": CATEGORY_COLORS.get(category, "#6E7B8C"),
            "auto": row["source"] == "suggested",
            "source": "bot",
        })
    return budgets


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
                "source": "bot",
                "icon": DETAIL_ICONS.get(key, SECTION_ICONS.get(section, "euro")),
                "tint": DETAIL_TINTS.get(key, SECTION_TINTS.get(section, "#8FA8BC")),
                # Bot speichert keinen Abbuchungstag pro Posten (offener Migrationspunkt,
                # siehe DATENMODELL.md) — "1." ist eine bewusste, dokumentierte Näherung.
                "date": "1.",
                "a": round(amount, 2),
                "cancel": section in CANCELABLE_SECTIONS,
            })
        if items:
            groups.append({"cat": SECTION_LABELS.get(section, section.title()), "items": items})
    return groups


def build_live_app_data(conn: sqlite3.Connection, user_id: int) -> dict:
    """Liefert die Bot-Felder, die eine bereits gekoppelte App sicher aktualisieren kann.

    Lokale App-Ergänzungen wie Sachwerte oder ein manuell gepflegter Immobilienwert gehören
    absichtlich nicht hierher: Die App führt sie beim Aktualisieren weiter, statt sie zu
    überschreiben. Der Bot ist derzeit nur Quelle für Cash, Investments, Fixkosten, Ziele und
    Monatsbuchungen.
    """
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
    monthly_expenses = float(conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM expenses
             WHERE user_id = ?
               AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')""",
        (user_id,),
    ).fetchone()["total"] or 0)
    income = float(u.get("income") or 0) + float(u.get("other_income") or 0)
    available = income - fixed_costs - sparraten - monthly_expenses

    crypto = min(investments, _crypto_holdings_value(conn, user_id))
    etf = investments - crypto
    crypto_positions = _crypto_positions(conn, user_id) if crypto else []
    crypto_sub = (f"{len(crypto_positions)} Position" + ("" if len(crypto_positions) == 1 else "en")
                  if crypto_positions else "aus dem Bot")
    etf_positions = _etf_positions(conn, user_id) if etf else []
    etf_sub = (f"{len(etf_positions)} Position" + ("" if len(etf_positions) == 1 else "en")
               if etf_positions else "aus dem Bot")

    net_worth_k = round(net_worth / 1000, 3)
    return {
        "netWorth": round(net_worth, 2),
        "series": {r: [net_worth_k, net_worth_k] for r in ("1W", "1M", "6M", "1J", "Max")},
        "assets": [a for a in (
            {"name": "Girokonto", "source": "bot", "icon": "bank", "tint": "#2AABEE",
             "value": round(cash, 2), "sub": "aus dem Bot"} if cash else None,
            {"name": "ETF & Investments", "source": "bot", "icon": "chart", "tint": "#8B7DF5",
             "value": round(etf, 2), "sub": etf_sub,
             **({"positions": etf_positions} if etf_positions else {})} if etf else None,
            {"name": "Krypto", "source": "bot", "icon": "bitcoin", "tint": "#F7931A",
             "value": round(crypto, 2), "sub": crypto_sub,
             **({"positions": crypto_positions} if crypto_positions else {})} if crypto else None,
        ) if a],
        "tx": _build_tx(conn, user_id),
        "budgets": _build_budgets(conn, user_id),
        "sts": {
            "konto": round(cash, 2),
            "fixRest": round(fixed_costs, 2),
            "sparraten": round(sparraten, 2),
            "income": round(income, 2),
            # Der Bot ist die Quelle der Wahrheit fuer das freie Monatsbudget. Die App nutzt
            # diesen Wert statt Fixkosten und Ausgaben ein zweites Mal anders zu kombinieren.
            "available": round(available, 2),
            "monthExpenses": round(monthly_expenses, 2),
        },
        "vertraege": _build_vertraege(details),
        "goals": ([{
            "t": u.get("goal_description"),
            "icon": "coins", "tint": "#2AABEE",
            "cur": round(sparraten, 2),
            "tar": round(float(u.get("goal_amount") or 0), 2) or 1,
            "source": "bot",
        }] if (u.get("goal_description") or "").strip() else []),
    }


def _crypto_holdings_value(conn: sqlite3.Connection, user_id: int) -> float:
    """Netto-Krypto-Wert (Zugänge minus Abgänge) aus investment_events.

    Der Bot wirft ETF/Krypto/Aktien alle in EINE Summe (users.current_investments), merkt sich
    den Asset-Typ aber pro Ereignis in investment_events (asset_type='crypto' bei Bitcoin/
    Ethereum/Crypto, siehe bot.py detect_investment_asset). Live-Bug (16.07.): die App zeigte
    deshalb nur „ETF & Investments" und nie Krypto separat, obwohl im Bot eingetragen.

    Wir carven NUR die Krypto-Summe heraus und lassen den Rest als ETF stehen — dadurch bleibt
    ETF + Krypto == current_investments exakt erhalten (keine Doppelzählung, kein Drift). Fehlt
    die Tabelle (alte DB) oder gibt es keine Krypto-Events, kommt 0 zurück (Fallback = altes
    Verhalten, alles unter ETF)."""
    try:
        row = conn.execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
               FROM investment_events
               WHERE user_id = ? AND asset_type = 'crypto'""",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    net = float(row["net"] if row and row["net"] is not None else 0.0)
    return max(0.0, net)


def _crypto_positions(conn: sqlite3.Connection, user_id: int) -> list:
    """Krypto-Positionen pro Coin (Bitcoin/Ethereum/Solana/XRP …) als [{n, v}], netto Zu-/Abgänge.

    Gruppiert investment_events nach asset_name für asset_type='crypto'. Kein „chg"-Feld: der Bot
    trackt für Krypto keinen Kurs (nur die drei CURATED-ETFs haben Kursdaten) — die App zeigt ohne
    chg sauber nur den Wert (index.html openAssetDetail). Positionen mit Netto <= 0 (komplett wieder
    verkauft) fallen raus. Nach Wert absteigend sortiert."""
    try:
        rows = conn.execute(
            """SELECT
                   COALESCE(NULLIF(TRIM(asset_name), ''), 'Krypto') AS name,
                   COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
               FROM investment_events
               WHERE user_id = ? AND asset_type = 'crypto'
               GROUP BY name""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    positions = [
        {"n": r["name"], "v": round(float(r["net"]), 2)}
        for r in rows
        if r["net"] is not None and float(r["net"]) > 0
    ]
    positions.sort(key=lambda p: p["v"], reverse=True)
    return positions


def _etf_positions(conn: sqlite3.Connection, user_id: int) -> list:
    """ETF-Positionen aus portfolio_holdings (reine Anzeige-Schublade, wird nirgends mit
    current_investments verrechnet — Kernanforderung des Features, siehe bot.py).
    v = total_invested (eingezahlte Summe), chg = Kursentwicklung seit Start (nur wenn beide
    Kurse da sind; sonst zeigt die App ehrlich nur den Betrag). Fehlende Tabelle → []."""
    try:
        rows = conn.execute(
            """SELECT instrument_label, total_invested, start_price, last_price
               FROM portfolio_holdings
               WHERE user_id = ?
               ORDER BY COALESCE(total_invested, 0) DESC, instrument_label""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        pos = {"n": r["instrument_label"], "v": round(float(r["total_invested"] or 0), 2)}
        sp, lp = r["start_price"], r["last_price"]
        if sp and lp:
            pct = (float(lp) - float(sp)) / float(sp) * 100.0
            pos["chg"] = f"{'+' if pct >= 0 else '−'}{abs(pct):.1f} %".replace(".", ",")
        out.append(pos)
    return out


def build_app_state(user_id: int, score_total: int = 0, score_label: str = "—") -> dict:
    """Baut das App-State-JSON für user_id und schreibt es nach public/app-state/<token>.json.
    score_total/score_label kommen vom Aufrufer (bot.py hat calculate_clarity_score() schon im
    eigenen Namespace, siehe Modul-Docstring, warum das hier nicht selbst berechnet wird).
    Gibt {token, pairing_code, path, url, expires_at} zurück."""
    ensure_app_state_links_table()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        state_data = build_live_app_data(conn, user_id)

        access = conn.execute(
            "SELECT display_name, username FROM user_access WHERE user_id = ?", (user_id,)
        ).fetchone()
        display_name = ((access["display_name"] if access else "") or
                         (access["username"] if access else "") or "")

        token = secrets.token_urlsafe(24)
        pairing_code = _new_pairing_code(conn)
        expires_at = datetime.now() + timedelta(days=APP_STATE_LINK_TTL_DAYS)

        state = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "user_id": user_id,
            "display_name": display_name,
            "api": {
                "baseUrl": PUBLIC_APP_API_BASE_URL,
                "token": token,
            } if PUBLIC_APP_API_BASE_URL else None,
            **state_data,
            "score": {
                "value": int(score_total or 0),
                "label": score_label or "—",
            },
        }
    finally:
        conn.close()

    PUBLIC_APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PUBLIC_APP_STATE_DIR / f"{token}.json"
    output_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    public_url = f"{PUBLIC_APP_STATE_BASE_URL}/{token}.json" if PUBLIC_APP_STATE_BASE_URL else ""
    with sqlite3.connect(DB_PATH) as db_conn:
        db_conn.execute(
            """INSERT INTO app_state_links (token, user_id, expires_at, status, pairing_code)
               VALUES (?, ?, ?, 'active', ?)""",
            (token, user_id, expires_at.strftime("%Y-%m-%d %H:%M:%S"), pairing_code),
        )
        db_conn.commit()

    return {
        "token": token,
        "pairing_code": pairing_code,
        "path": output_path,
        "url": public_url,
        "expires_at": expires_at,
    }
