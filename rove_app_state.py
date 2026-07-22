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
REPORTS_DIR = Path(os.getenv("CLARITY_REPORTS_DIR", str(APP_DIR / "reports")))
REPORTS_ARCHIVE_DIR = REPORTS_DIR / "archive"

MONTH_NAMES_DE = (
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)

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


def _report_month_label(month_key: str) -> str:
    try:
        year, month = (int(part) for part in month_key.split("-", 1))
        return f"{MONTH_NAMES_DE[month]} {year}"
    except (TypeError, ValueError, IndexError):
        return month_key or "Monatsreport"


def _build_reports(conn: sqlite3.Connection, user_id: int) -> list:
    """Echtes App-Archiv: laufender Monat plus tatsaechlich versendete Reports.

    Weblinks sind nur innerhalb ihrer TTL sichtbar. Das PDF bleibt als Archiv erhalten;
    nach der serverseitigen Komprimierung liegt es als .pdf.gz vor und wird von der API
    bei Bedarf wieder ausgeliefert.
    """
    current_month = datetime.now().strftime("%Y-%m")
    reports = [{
        "month": current_month,
        "m": _report_month_label(current_month),
        "status": "running",
        "pdfAvailable": False,
        "webUrl": "",
        "webExpiresAt": "",
    }]
    try:
        jobs = conn.execute(
            """SELECT report_month
                 FROM report_jobs
                WHERE user_id = ? AND status = 'sent' AND report_month < ?
                GROUP BY report_month
                ORDER BY report_month DESC""",
            (user_id, current_month),
        ).fetchall()
    except sqlite3.OperationalError:
        return reports

    for job in jobs:
        month_key = str(job["report_month"] or "").strip()
        if not month_key:
            continue
        web_url = ""
        web_expires_at = ""
        try:
            link = conn.execute(
                """SELECT public_url, expires_at
                     FROM report_links
                    WHERE user_id = ? AND report_month = ? AND status = 'active'
                      AND datetime(expires_at) > datetime('now', 'localtime')
                    ORDER BY datetime(created_at) DESC LIMIT 1""",
                (user_id, month_key),
            ).fetchone()
            if link:
                web_url = str(link["public_url"] or "").strip()
                web_expires_at = str(link["expires_at"] or "").strip()
        except sqlite3.OperationalError:
            pass

        pdf_name = f"rove_report_{user_id}_{month_key}.pdf"
        pdf_available = (REPORTS_DIR / pdf_name).is_file() or (REPORTS_ARCHIVE_DIR / f"{pdf_name}.gz").is_file()
        snapshot = None
        try:
            snapshot = conn.execute(
                """SELECT net_worth, total_expenses, clarity_score
                     FROM monthly_snapshots
                    WHERE user_id = ? AND month = ?
                    ORDER BY id DESC LIMIT 1""",
                (user_id, month_key),
            ).fetchone()
        except sqlite3.OperationalError:
            pass

        report = {
            "month": month_key,
            "m": _report_month_label(month_key),
            "status": "ready",
            "pdfAvailable": bool(pdf_available),
            "webUrl": web_url,
            "webExpiresAt": web_expires_at,
        }
        if snapshot:
            report["stats"] = {
                "netWorth": round(float(snapshot["net_worth"] or 0), 2),
                "expenses": round(float(snapshot["total_expenses"] or 0), 2),
                "score": int(snapshot["clarity_score"] or 0),
            }
        reports.append(report)
    return reports


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


ACCOUNT_META = {
    "giro": {"name": "Girokonto", "icon": "bank", "tint": "#2AABEE"},
    "tagesgeld": {"name": "Tagesgeld", "icon": "coins", "tint": "#35D07F"},
    "bargeld": {"name": "Bargeld", "icon": "wallet", "tint": "#B08D57"},
}


def ensure_app_account_balances_table(conn: sqlite3.Connection) -> None:
    """Speichert die Cash-Aufteilung der App, ohne das bestehende Bot-Modell zu brechen."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_account_balances (
            user_id     INTEGER NOT NULL,
            account_key TEXT NOT NULL,
            amount      REAL NOT NULL DEFAULT 0.0,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, account_key),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def ensure_app_monthly_plan_table(conn: sqlite3.Connection) -> None:
    """Speichert nur Bestaetigungen zum Monatsplan, keine erfundenen Buchungen."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_monthly_plan_status (
            user_id            INTEGER NOT NULL,
            month_key          TEXT NOT NULL,
            income_status      TEXT NOT NULL DEFAULT 'planned',
            fixed_costs_status TEXT NOT NULL DEFAULT 'planned',
            updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, month_key)
        )"""
    )


def get_app_monthly_plan(conn: sqlite3.Connection, user_id: int, income: float,
                         fixed_costs: float, sparraten: float) -> dict:
    """Liefert Planung und explizite Bestaetigungen getrennt von echten Buchungen."""
    ensure_app_monthly_plan_table(conn)
    month_key = date.today().strftime("%Y-%m")
    row = conn.execute(
        """SELECT income_status, fixed_costs_status
             FROM app_monthly_plan_status
            WHERE user_id = ? AND month_key = ?""",
        (user_id, month_key),
    ).fetchone()
    return {
        "month": month_key,
        "income": round(income, 2),
        "fixedCosts": round(fixed_costs, 2),
        "savings": round(sparraten, 2),
        "incomeStatus": row["income_status"] if row else "planned",
        "fixedCostsStatus": row["fixed_costs_status"] if row else "planned",
    }


def ensure_app_properties_table(conn: sqlite3.Connection) -> None:
    """Speichert Immobilienwerte zentral, damit sie keinen App-Neustart verlieren."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_properties (
            user_id        INTEGER PRIMARY KEY,
            market_value   REAL NOT NULL DEFAULT 0.0,
            remaining_debt REAL NOT NULL DEFAULT 0.0,
            monthly_rate   REAL NOT NULL DEFAULT 0.0,
            house_fee      REAL NOT NULL DEFAULT 0.0,
            management_fee REAL NOT NULL DEFAULT 0.0,
            updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def get_app_property(conn: sqlite3.Connection, user_id: int) -> dict | None:
    try:
        row = conn.execute(
            """SELECT market_value, remaining_debt, monthly_rate,
                      house_fee, management_fee
                 FROM app_properties WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or float(row["market_value"] or 0) <= 0:
        return None
    market_value = round(max(0.0, float(row["market_value"] or 0)), 2)
    remaining_debt = round(max(0.0, float(row["remaining_debt"] or 0)), 2)
    return {
        "market_value": market_value,
        "remaining_debt": remaining_debt,
        "equity": round(market_value - remaining_debt, 2),
        "monthly_rate": round(max(0.0, float(row["monthly_rate"] or 0)), 2),
        "house_fee": round(max(0.0, float(row["house_fee"] or 0)), 2),
        "management_fee": round(max(0.0, float(row["management_fee"] or 0)), 2),
    }


def get_app_cash_accounts(
    conn: sqlite3.Connection, user_id: int, bot_cash: float
) -> tuple[dict[str, float], bool]:
    """Liest die getrennten Cash-Konten oder faellt sicher auf den Bot-Startwert zurueck.

    Alte Telegram-Profile kennen nur `current_cash`. Bis der Nutzer die Aufteilung einmal
    vorgenommen hat, wird dieser Wert deshalb vollstaendig dem Girokonto zugeordnet.
    """
    try:
        rows = conn.execute(
            """SELECT account_key, amount FROM app_account_balances
                 WHERE user_id = ?""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    if not rows:
        return {"giro": round(max(0.0, bot_cash), 2), "tagesgeld": 0.0, "bargeld": 0.0}, False

    balances = {key: 0.0 for key in ACCOUNT_META}
    for row in rows:
        key = row["account_key"]
        if key in balances:
            balances[key] = round(max(0.0, float(row["amount"] or 0)), 2)

    # Der Bot kann zwischen zwei App-Aufrufen neue Cash-Sparraten bestaetigen. Dieser neue
    # Betrag gehoert zunaechst ins Girokonto, damit Gesamtvermögen und Bot niemals driften.
    delta = round(float(bot_cash) - sum(balances.values()), 2)
    if abs(delta) >= 0.01:
        balances["giro"] = round(max(0.0, balances["giro"] + delta), 2)
    return balances, True


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

    bot_cash = float(u.get("current_cash") or 0)
    cash_accounts, has_cash_accounts = get_app_cash_accounts(conn, user_id, bot_cash)
    cash = round(sum(cash_accounts.values()), 2)
    investments = float(u.get("current_investments") or 0)
    property_data = get_app_property(conn, user_id)
    property_equity = float(property_data["equity"] if property_data else 0)
    net_worth = cash + investments + property_equity
    etf_savings = round(float(u.get("etf_savings") or 0), 2)
    cash_savings = round(float(u.get("cash_savings") or 0), 2)
    sparraten = etf_savings + cash_savings
    fixed_costs = float(u.get("fixed_costs") or 0)
    monthly_expenses = float(conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM expenses
             WHERE user_id = ?
               AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')""",
        (user_id,),
    ).fetchone()["total"] or 0)
    income = float(u.get("income") or 0) + float(u.get("other_income") or 0)
    available = income - fixed_costs - sparraten - monthly_expenses
    monthly_plan = get_app_monthly_plan(conn, user_id, income, fixed_costs, sparraten)

    crypto = min(investments, _crypto_holdings_value(conn, user_id))
    etf = investments - crypto
    crypto_positions = _crypto_positions(conn, user_id) if crypto else []
    crypto_sub = (f"{len(crypto_positions)} Position" + ("" if len(crypto_positions) == 1 else "en")
                  if crypto_positions else "aus dem Bot")
    etf_positions = _etf_positions(conn, user_id) if etf else []
    assigned_investments = round(sum(float(position.get("v") or 0) for position in etf_positions), 2)
    unassigned_investments = round(max(0.0, etf - assigned_investments), 2)
    if unassigned_investments >= 0.01:
        etf_positions.append({
            "n": "Noch nicht zugeordnet",
            "v": unassigned_investments,
            "unassigned": True,
            "editable": True,
        })
    etf_sub = (f"{len(etf_positions)} Position" + ("" if len(etf_positions) == 1 else "en")
               if etf_positions else "aus dem Bot")

    net_worth_k = round(net_worth / 1000, 3)
    return {
        "netWorth": round(net_worth, 2),
        "series": {r: [net_worth_k, net_worth_k] for r in ("1W", "1M", "6M", "1J", "Max")},
        "assets": [a for a in (
            {"name": "Girokonto", "source": "bot", "icon": "bank", "tint": "#2AABEE",
             "value": cash_accounts["giro"],
             "sub": "verfuegbar" if has_cash_accounts else "aus dem Bot"} if (cash_accounts["giro"] or has_cash_accounts) else None,
            {"name": "Tagesgeld", "source": "bot", "icon": "coins", "tint": "#35D07F",
             "value": cash_accounts["tagesgeld"], "sub": "Rücklage"} if (cash_accounts["tagesgeld"] or has_cash_accounts) else None,
            {"name": "Bargeld", "source": "bot", "icon": "wallet", "tint": "#B08D57",
             "value": cash_accounts["bargeld"], "sub": "im Portemonnaie"} if (cash_accounts["bargeld"] or has_cash_accounts) else None,
            {"name": "ETF & Investments", "source": "bot", "icon": "chart", "tint": "#8B7DF5",
             "value": round(etf, 2), "sub": etf_sub,
             **({"positions": etf_positions} if etf_positions else {})} if etf else None,
            {"name": "Krypto", "source": "bot", "icon": "bitcoin", "tint": "#F7931A",
             "value": round(crypto, 2), "sub": crypto_sub,
             **({"positions": crypto_positions} if crypto_positions else {})} if crypto else None,
            {"name": "Immobilie", "source": "app", "icon": "house", "tint": "#D8B66A",
             "value": property_data["equity"], "sub": "Eigenkapital",
             "real": {
                 "marktwert": property_data["market_value"],
                 "restschuld": property_data["remaining_debt"],
                 "eigenkapital": property_data["equity"],
                 "rate": property_data["monthly_rate"],
                 "hausgeld": property_data["house_fee"],
                 "verwaltung": property_data["management_fee"],
                 "wert": "—",
             }} if property_data else None,
        ) if a],
        "tx": _build_tx(conn, user_id),
        "budgets": _build_budgets(conn, user_id),
        "reports": _build_reports(conn, user_id),
        "monthlyPlan": monthly_plan,
        "sts": {
            "konto": round(cash, 2),
            "fixRest": round(fixed_costs, 2),
            "sparraten": round(sparraten, 2),
            "etfSparrate": etf_savings,
            "cashSparrate": cash_savings,
            "sparratenParts": {"etf": etf_savings, "cash": cash_savings},
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
    """ETF- und Aktienpositionen fuer die gemeinsame Investment-Schublade.

    Kuratierte ETFs kommen aus portfolio_holdings. Manuell in der App benannte Aktien
    liegen als manual_adjustment in investment_events. Beide Tabellen sind nur die
    Aufschluesselung; users.current_investments bleibt die verbindliche Gesamtsumme.

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
        pos = {
            "n": r["instrument_label"],
            "v": round(float(r["total_invested"] or 0), 2),
            "editable": False,
        }
        sp, lp = r["start_price"], r["last_price"]
        if sp and lp:
            pct = (float(lp) - float(sp)) / float(sp) * 100.0
            pos["chg"] = f"{'+' if pct >= 0 else '−'}{abs(pct):.1f} %".replace(".", ",")
        out.append(pos)

    try:
        stock_rows = conn.execute(
            """SELECT COALESCE(NULLIF(TRIM(asset_name), ''), 'Aktie') AS name,
                      COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0) AS net
                 FROM investment_events
                WHERE user_id = ? AND asset_type = 'stock'
                GROUP BY LOWER(TRIM(asset_name))
                ORDER BY net DESC, name""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        stock_rows = []
    out.extend(
        {
            "n": row["name"],
            "v": round(float(row["net"]), 2),
            "editable": True,
        }
        for row in stock_rows
        if row["net"] is not None and float(row["net"]) > 0
    )
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
