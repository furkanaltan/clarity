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

from rove_score import calculate_score
from rove_market_data import ensure_market_tracking_schema

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

# Bargeld ist keine Ausgaben-Kategorie, sondern das Portemonnaie selbst — gleiche Farbe wie das
# Bargeld-Asset (ACCOUNT_META) und wie BARGELD_TINT in der App.
CASH_TINT = "#B08D57"

# Einnahmen sind ebenfalls keine Ausgaben-Kategorie. Gleicher Farbwert, den die App fuer
# "Einnahme" verwendet — sonst haette dieselbe Buchung im Feed und im Detail zwei Farben.
INCOME_TINT = "#155681"

# Fixkosten-Abbuchung (Monatscheck). Ruhiges Grau — sie ist keine Konsum-Ausgabe, sondern eine
# planmaessige Belastung, die im Budget laengst beruecksichtigt ist.
FIXED_TINT = "#5B6675"

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
    "app_vertraege": "Weitere Verträge",
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
                  "versicherungen": "#3E9C8F", "kredite": "#D8B66A", "app_vertraege": "#8FA8BC"}
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
APP_CONTRACT_SECTION = "app_vertraege"

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


def _cash_movements_for_month(conn: sqlite3.Connection, user_id: int, month_key: str) -> list:
    """Liest Bargeld-Bewegungen eines Monats, ohne zu scheitern, wenn es noch keine gibt.

    Dieselbe defensive Leseart wie get_app_cash_accounts(): die Tabelle entsteht erst beim
    ersten Schreibvorgang der App (ensure_app_cash_movements_table), das Lesen darf davor
    keinen 500er ausloesen.

    Bewusst `SELECT *` statt einer Spaltenliste: eine Datenbank, in der `label` noch fehlt,
    wuerde bei `SELECT ... label ...` einen OperationalError werfen — und der Except-Zweig
    unten haette dann ALLE Bewegungen des Monats verschluckt, also auch die Abhebungen.
    """
    try:
        return conn.execute(
            """SELECT * FROM app_cash_movements
                 WHERE user_id = ? AND strftime('%Y-%m', created_at) = ?
                 ORDER BY created_at DESC""",
            (user_id, month_key),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _movement_label(row, fallback: str) -> str:
    """Liest `label` auch aus Zeilen, die noch aus der Zeit vor der Spalte stammen."""
    try:
        value = row["label"] if "label" in row.keys() else None
    except (IndexError, KeyError):
        value = None
    return (str(value).strip() or fallback) if value else fallback


def _build_tx(conn: sqlite3.Connection, user_id: int, month_key: str | None = None) -> list:
    """Baut die Buchungsliste fuer den angefragten Monat.

    Der Cashflow kann damit vergangene Monate anzeigen, ohne sie editierbar zu machen.
    Fehlt month_key, bleibt das bisherige Verhalten erhalten und liefert den laufenden Monat.
    """
    month_key = month_key or date.today().strftime("%Y-%m")
    rows = conn.execute(
        """SELECT id, amount, category, merchant, description, created_at FROM expenses
           WHERE user_id = ? AND strftime('%Y-%m', created_at) = ?
           ORDER BY created_at DESC""",
        (user_id, month_key),
    ).fetchall()
    # Bargeld-Bewegungen liegen NICHT in expenses (siehe ensure_app_cash_movements_table):
    # eine Abhebung ist keine Ausgabe, und ob eine Ausgabe bar bezahlt wurde, kann die
    # Bot-Tabelle nicht abbilden. Beides kommt hier dazu, damit die Buchungsliste den
    # Refresh ueberlebt (Bug 25.07.: "Bargeld abgehoben" verschwand nach 45 Sekunden).
    movements = _cash_movements_for_month(conn, user_id, month_key)
    cash_paid_expense_ids = {
        int(m["expense_id"])
        for m in movements
        if m["kind"] == "payment" and m["expense_id"] is not None
    }
    entries: list[tuple[str, dict]] = []
    for r in rows:
        cat = _category_label(r["category"])
        name = (r["merchant"] or r["description"] or cat).strip() or cat
        item = {
            # "sid" = Server-ID der Zeile in expenses. Bewusst NICHT "id": die App vergibt
            # ihre eigenen lokalen IDs (TXID) und wuerde eine mitgelieferte "id" ueberschreiben.
            # Ohne sid kann die App eine Buchung nur im Browser-RAM loeschen — der 45s-Refresh
            # holt sie danach aus der DB zurueck (Bug 25.07.).
            "sid": r["id"],
            "n": name,
            # Unsichtbares Suchfeld fuer den Cashflow. Die Darstellung bleibt bewusst beim
            # vertrauten Haendlernamen; Verwendungszweck/Beschreibung ist nur auffindbar.
            "desc": (r["description"] or "").strip(),
            "cat": cat,
            "a": -abs(float(r["amount"] or 0)),
            "c": CATEGORY_COLORS.get(cat, "#6E7B8C"),
            "i": (name[:1] or "?").upper(),
        }
        if r["id"] in cash_paid_expense_ids:
            # Ohne dieses Flag waere nach einem Refresh nicht mehr erkennbar, dass die Ausgabe
            # aus dem Portemonnaie kam — die App wuerde sie beim Loeschen dem Girokonto
            # zurueckgeben statt dem Bargeld.
            item["bar"] = True
        entries.append((r["created_at"] or "", item))
    for m in movements:
        if m["kind"] == "income":
            # Einnahmen tragen wie Abhebungen eine `csid`, keine `sid` — sie stehen nicht in
            # `expenses`. Ein `sid` wuerde die App beim Loeschen auf DELETE /v1/expenses/<id>
            # schicken und dort eine fremde Ausgabe mit derselben Nummer treffen.
            entries.append((m["created_at"] or "", {
                "csid": m["id"],
                "n": _movement_label(m, "Einnahme"),
                "cat": "Einnahme",
                "a": abs(float(m["amount"] or 0)),
                "c": INCOME_TINT,
                "i": "€",
            }))
            continue
        if m["kind"] == "fixed":
            # Fixkosten-Abbuchung, im Monatscheck bestaetigt (27.07.). Bewusst NICHT in `expenses`:
            # das Budget rechnet `verfuegbar = Einnahmen - Fixkosten - Sparraten - Ausgaben`, die
            # Fixkosten sind dort also schon abgezogen. Stuenden sie zusaetzlich in `expenses`,
            # wuerden sie doppelt zaehlen und Budget, Bot und Report verfaelschen. Hier bewegen sie
            # nur das Konto — und werden sichtbar, damit der Kontostand nachvollziehbar bleibt.
            entries.append((m["created_at"] or "", {
                "csid": m["id"],
                "n": _movement_label(m, "Fixkosten"),
                "cat": "Fixkosten",
                "a": -abs(float(m["amount"] or 0)),
                "c": FIXED_TINT,
                "i": "F",
            }))
            continue
        if m["kind"] != "withdrawal":
            continue
        entries.append((m["created_at"] or "", {
            # "csid" statt "sid": diese Zeile steht in app_cash_movements, nicht in expenses.
            # Ein "sid" hier waere gefaehrlich — die App wuerde beim Loeschen
            # DELETE /v1/expenses/<id> aufrufen und damit eine fremde Ausgabe mit derselben
            # Nummer treffen. Abhebungen gehen ueber DELETE /v1/cash-movements/<id>.
            "csid": m["id"],
            "n": "Bargeld abgehoben",
            "cat": "Bargeld",
            "a": -abs(float(m["amount"] or 0)),
            "c": CASH_TINT,
            "i": "B",
            "transfer": True,
        }))
    entries.sort(key=lambda entry: entry[0], reverse=True)

    days: dict[str, list] = {}
    order: list[str] = []
    today_iso = date.today().isoformat()
    for created, item in entries:
        day_key = (created or "")[:10]
        if day_key == today_iso:
            day_label = "Heute"
        elif len(day_key) == 10:
            day_label = f"{day_key[8:10]}.{day_key[5:7]}."
        else:
            day_label = day_key or "Unbekannt"
        if day_label not in days:
            days[day_label] = []
            order.append(day_label)
        days[day_label].append(item)
    return [{"d": d, "items": days[d]} for d in order]


def _build_budgets(
    conn: sqlite3.Connection, user_id: int, month_key: str | None = None
) -> list:
    """Liefert die gesetzten Monatsrahmen fuer genau einen Monat.

    Die App darf bei einer gekoppelten Sitzung keine eigenen Limits aus vergangenen
    Buchungen ableiten. Vergangene Monate sind reine Historie; ein Rahmen wird nie
    automatisch in den Folgemonat kopiert.
    """
    month_key = month_key or date.today().strftime("%Y-%m")
    try:
        rows = conn.execute(
            """SELECT category, monthly_limit, source
                 FROM category_budgets
                WHERE user_id = ?
                  AND active_month = ?
                ORDER BY category""",
            (user_id, month_key),
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


def _previous_month_keys(count: int = 3) -> list[str]:
    """Liefert die letzten abgeschlossenen Monats-Schluessel, neuester zuerst."""
    cursor = date.today().replace(day=1)
    months: list[str] = []
    for _ in range(count):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        months.append(cursor.strftime("%Y-%m"))
    return months


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
        if section == APP_CONTRACT_SECTION:
            continue
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


def build_app_contract_groups(conn: sqlite3.Connection, user_id: int, details: dict) -> list:
    """Ergaenzt Bot-Fixkosten um zentral gespeicherte, in der App angelegte Verträge."""
    groups = _build_vertraege(details)
    by_category = {group["cat"]: group for group in groups}
    for contract in get_app_contracts(conn, user_id):
        category = contract.pop("category")
        group = by_category.get(category)
        if not group:
            group = {"cat": category, "items": []}
            groups.append(group)
            by_category[category] = group
        group["items"].append(contract)
    return groups


ACCOUNT_META = {
    "giro": {"name": "Girokonto", "icon": "bank", "tint": "#2AABEE"},
    "tagesgeld": {"name": "Tagesgeld", "icon": "coins", "tint": "#35D07F"},
    "bargeld": {"name": "Bargeld", "icon": "wallet", "tint": "#B08D57"},
}

# Stabile Schluessel fuer die rein visuelle Reihenfolge der Vermoegenskacheln.
# Namen sind Texte fuer die UI und koennen sich aendern; diese Keys bleiben dauerhaft stabil.
ASSET_ORDER_KEYS = (
    "cash:giro",
    "cash:tagesgeld",
    "cash:bargeld",
    "asset:investments",
    "asset:crypto",
    "asset:property",
    "asset:valuables",
)


def ensure_app_asset_order_table(conn: sqlite3.Connection) -> None:
    """Speichert nur die Darstellungspraeferenz, niemals Finanzwerte."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_asset_order (
            user_id       INTEGER NOT NULL,
            asset_key     TEXT NOT NULL,
            sort_position INTEGER NOT NULL,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, asset_key),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def get_app_asset_order(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """Liest eine gueltige Reihenfolge; ohne Praeferenz bleibt die bisherige Sortierung aktiv."""
    ensure_app_asset_order_table(conn)
    rows = conn.execute(
        """SELECT asset_key FROM app_asset_order
             WHERE user_id = ?
             ORDER BY sort_position, asset_key""",
        (user_id,),
    ).fetchall()
    allowed = set(ASSET_ORDER_KEYS)
    result: list[str] = []
    for row in rows:
        key = str(row["asset_key"] or "")
        if key in allowed and key not in result:
            result.append(key)
    return result


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


def ensure_app_cash_movements_table(conn: sqlite3.Connection) -> None:
    """Merkt die zwei Bargeld-Faelle, die die Bot-Tabelle `expenses` nicht abbilden kann.

    - `withdrawal`: Abhebung Girokonto → Portemonnaie. Das ist KEINE Ausgabe und steht deshalb
      nicht in expenses. Ohne diese Zeile verschwand "Bargeld abgehoben" beim naechsten
      Refresh wieder aus der Buchungsliste, obwohl der Betrag stimmte (Bug 25.07.).
    - `payment`: eine echte Ausgabe, die aus dem Portemonnaie bezahlt wurde. Die Ausgabe selbst
      bleibt ganz normal in expenses (Budget, Bot, Report rechnen unveraendert damit); hier
      steht nur, DASS bar gezahlt wurde und WELCHER Betrag dem Bargeld abgezogen wurde. Beim
      Loeschen der Ausgabe geht genau dieser Betrag ins Portemonnaie zurueck — nie mehr.
    - `card`: eine App-Ausgabe vom Girokonto. Sie erscheint nicht als eigene Buchungszeile,
      merkt aber den wirklich abgezogenen Girobetrag, damit Refresh und Loeschen symmetrisch sind.
    - `income`: eine Einnahme, die der Nutzer in der App erfasst hat ("Gehalt 2450"). Sie gehoert
      NICHT in `expenses` — das wuerde Budget, Bot und Report als Ausgabe verrechnen. Bis 26.07.
      wurde sie ueberhaupt nicht gespeichert: das Girokonto stieg nur lokal, und der 45s-Refresh
      hat Geld UND Buchungszeile kommentarlos wieder entfernt (Furkan-Bug 26.07.). Da Ausgaben
      das Konto dauerhaft senken, driftete der Kontostand systematisch nach unten.

    Bewusst eine eigene App-Tabelle: an `expenses` wird nichts geaendert, der Bot bleibt
    unberuehrt. Betraege sind immer positiv, die Richtung steckt in `kind`.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_cash_movements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            amount     REAL NOT NULL DEFAULT 0.0,
            expense_id INTEGER,
            label      TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    # Bestandsdatenbanken kennen `label` noch nicht. Ohne diese Nachruestung wuerde jede
    # Einnahme als "Einnahme" ohne Namen in der Liste stehen.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(app_cash_movements)")}
    if "label" not in columns:
        conn.execute("ALTER TABLE app_cash_movements ADD COLUMN label TEXT")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_app_cash_movements_user
             ON app_cash_movements (user_id, created_at)"""
    )


def ensure_app_monthly_plan_table(conn: sqlite3.Connection) -> None:
    """Speichert nur Bestaetigungen zum Monatsplan, keine erfundenen Buchungen."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_monthly_plan_status (
            user_id            INTEGER NOT NULL,
            month_key          TEXT NOT NULL,
            income_status      TEXT NOT NULL DEFAULT 'planned',
            fixed_costs_status TEXT NOT NULL DEFAULT 'planned',
            savings_status     TEXT NOT NULL DEFAULT 'planned',
            updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, month_key)
        )"""
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(app_monthly_plan_status)")}
    if "savings_status" not in columns:
        conn.execute(
            "ALTER TABLE app_monthly_plan_status "
            "ADD COLUMN savings_status TEXT NOT NULL DEFAULT 'planned'"
        )


def ensure_app_scheduled_savings_table(conn: sqlite3.Connection) -> None:
    """Merkt eine Sparraten-Aenderung fuer den naechsten Monatsplan vor.

    Die laufende Monats-Sparrate kann bereits echtes Geld zwischen Giro, ETF und
    Tagesgeld verschoben haben. Deshalb wird sie nie rueckwirkend ueberschrieben.
    Eine Vormerkung ist dagegen reine Planung und wird erst im Folgemonat zum
    neuen Standardwert.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_scheduled_savings (
            user_id         INTEGER PRIMARY KEY,
            effective_month TEXT NOT NULL,
            etf_savings     REAL NOT NULL DEFAULT 0.0,
            cash_savings    REAL NOT NULL DEFAULT 0.0,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def apply_due_scheduled_savings(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Aktiviert eine fällige Vormerkung genau einmal und gibt sie zurueck."""
    ensure_app_scheduled_savings_table(conn)
    month_key = date.today().strftime("%Y-%m")
    row = conn.execute(
        """SELECT effective_month, etf_savings, cash_savings
             FROM app_scheduled_savings WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    if not row or str(row["effective_month"]) > month_key:
        return None
    etf = round(max(0.0, float(row["etf_savings"] or 0)), 2)
    cash = round(max(0.0, float(row["cash_savings"] or 0)), 2)
    conn.execute(
        "UPDATE users SET etf_savings = ?, cash_savings = ? WHERE user_id = ?",
        (etf, cash, user_id),
    )
    conn.execute("DELETE FROM app_scheduled_savings WHERE user_id = ?", (user_id,))
    return {"effectiveMonth": month_key, "etf": etf, "cash": cash}


def get_app_scheduled_savings(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Liefert eine noch nicht aktive Sparrate fuer die transparente App-Anzeige."""
    ensure_app_scheduled_savings_table(conn)
    month_key = date.today().strftime("%Y-%m")
    row = conn.execute(
        """SELECT effective_month, etf_savings, cash_savings
             FROM app_scheduled_savings
            WHERE user_id = ? AND effective_month > ?""",
        (user_id, month_key),
    ).fetchone()
    if not row:
        return None
    return {
        "effectiveMonth": str(row["effective_month"]),
        "etf": round(max(0.0, float(row["etf_savings"] or 0)), 2),
        "cash": round(max(0.0, float(row["cash_savings"] or 0)), 2),
    }


def ensure_app_etf_savings_plan_table(conn: sqlite3.Connection) -> None:
    """Speichert den Ausfuehrungsrhythmus eines ETF-Sparplans pro App-Konto.

    Der Plan ist bewusst von der flexiblen Cash-Sparrate getrennt: ETF kann regelmaessig
    laufen, waehrend jemand seine Tagesgeld-Ruecklage in einem Monat aussetzt.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_etf_savings_plan (
            user_id        INTEGER PRIMARY KEY,
            execution_day  INTEGER NOT NULL,
            source_account TEXT NOT NULL CHECK(source_account IN ('giro', 'tagesgeld')),
            mode           TEXT NOT NULL CHECK(mode IN ('auto', 'confirm')),
            active         INTEGER NOT NULL DEFAULT 1,
            start_month    TEXT NOT NULL,
            updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def ensure_app_etf_position_plans_table(conn: sqlite3.Connection) -> None:
    """Speichert die geplante Rate getrennt je ETF-Depotposition.

    Die Tabelle ist in Phase 1 reine Planung. Sie loest noch keine Kontobewegung
    aus, damit mehrere ETF-Raten zuerst ohne finanzielles Risiko getestet werden.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_etf_position_plans (
            user_id        INTEGER NOT NULL,
            holding_id     INTEGER NOT NULL,
            monthly_amount REAL NOT NULL DEFAULT 0.0,
            execution_day  INTEGER NOT NULL,
            source_account TEXT NOT NULL CHECK(source_account IN ('giro', 'tagesgeld')),
            mode           TEXT NOT NULL CHECK(mode IN ('auto', 'confirm')),
            active         INTEGER NOT NULL DEFAULT 1,
            start_month    TEXT NOT NULL,
            updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, holding_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(holding_id) REFERENCES portfolio_holdings(id) ON DELETE CASCADE
        )"""
    )


def get_app_etf_savings_plan(conn: sqlite3.Connection, user_id: int, etf_savings: float) -> dict:
    """Liefert nur den Planstatus; die echte Buchung passiert in der App-API."""
    ensure_app_etf_savings_plan_table(conn)
    month_key = date.today().strftime("%Y-%m")
    row = conn.execute(
        """SELECT execution_day, source_account, mode, active, start_month
             FROM app_etf_savings_plan WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    executed = conn.execute(
        """SELECT 1 FROM investment_events
             WHERE user_id = ? AND source = 'app_etf_plan'
               AND asset_type = 'etf'
               AND strftime('%Y-%m', created_at) = ? LIMIT 1""",
        (user_id, month_key),
    ).fetchone() is not None
    if not row:
        return {
            "configured": False,
            "setupRequired": bool(etf_savings > 0),
            "amount": round(float(etf_savings or 0), 2),
            "executedThisMonth": executed,
        }
    return {
        "configured": True,
        "setupRequired": False,
        "amount": round(float(etf_savings or 0), 2),
        "executionDay": int(row["execution_day"]),
        "sourceAccount": str(row["source_account"]),
        "mode": str(row["mode"]),
        "active": bool(row["active"]),
        "startMonth": str(row["start_month"]),
        "executedThisMonth": executed,
    }


def ensure_app_goals_table(conn: sqlite3.Connection) -> None:
    """Speichert zusaetzliche App-Ziele zentral neben dem Telegram-Hauptziel."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_goals (
            user_id       INTEGER NOT NULL,
            goal_id       TEXT NOT NULL,
            name          TEXT NOT NULL,
            target_amount REAL NOT NULL,
            current_amount REAL NOT NULL DEFAULT 0.0,
            icon          TEXT NOT NULL DEFAULT 'coins',
            tint          TEXT NOT NULL DEFAULT '#2AABEE',
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, goal_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def ensure_app_primary_goal_progress_table(conn: sqlite3.Connection) -> None:
    """Speichert die Zweckbindung des einen bestehenden Bot-Hauptziels zentral."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_primary_goal_progress (
            user_id        INTEGER PRIMARY KEY,
            current_amount REAL NOT NULL DEFAULT 0.0,
            updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def get_app_primary_goal_progress(conn: sqlite3.Connection, user_id: int, target: float) -> float:
    ensure_app_primary_goal_progress_table(conn)
    row = conn.execute(
        "SELECT current_amount FROM app_primary_goal_progress WHERE user_id = ?", (user_id,)
    ).fetchone()
    return round(min(max(0.0, float(row["current_amount"] or 0)), max(0.0, target)), 2) if row else 0.0


def ensure_app_contracts_table(conn: sqlite3.Connection) -> None:
    """Speichert aus der App angelegte laufende Verträge zentral und eindeutig."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_contracts (
            user_id     INTEGER NOT NULL,
            contract_id TEXT NOT NULL,
            detail_key  TEXT NOT NULL,
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,
            amount      REAL NOT NULL,
            icon        TEXT NOT NULL DEFAULT 'doc',
            tint        TEXT NOT NULL DEFAULT '#8FA8BC',
            debit_day   TEXT NOT NULL DEFAULT '1.',
            cancelable  INTEGER NOT NULL DEFAULT 1,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, contract_id),
            UNIQUE (user_id, detail_key),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )


def get_app_contracts(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    ensure_app_contracts_table(conn)
    rows = conn.execute(
        """SELECT contract_id, name, category, amount, icon, tint, debit_day, cancelable
             FROM app_contracts WHERE user_id = ? ORDER BY datetime(created_at), contract_id""",
        (user_id,),
    ).fetchall()
    return [{
        "id": str(row["contract_id"]), "n": str(row["name"]),
        "a": round(max(0.0, float(row["amount"] or 0)), 2),
        "icon": str(row["icon"] or "doc"), "tint": str(row["tint"] or "#8FA8BC"),
        "date": str(row["debit_day"] or "1."), "cancel": bool(row["cancelable"]),
        "source": "app", "category": str(row["category"]),
    } for row in rows]


def get_app_goals(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Liest nur Ziele, die in der App angelegt wurden, ohne Bot-Ziele zu duplizieren."""
    ensure_app_goals_table(conn)
    rows = conn.execute(
        """SELECT goal_id, name, target_amount, current_amount, icon, tint
             FROM app_goals WHERE user_id = ? ORDER BY datetime(created_at), goal_id""",
        (user_id,),
    ).fetchall()
    return [{
        "id": str(row["goal_id"]),
        "t": str(row["name"]),
        "tar": round(max(0.0, float(row["target_amount"] or 0)), 2),
        "cur": round(max(0.0, float(row["current_amount"] or 0)), 2),
        "icon": str(row["icon"] or "coins"),
        "tint": str(row["tint"] or "#2AABEE"),
        "source": "app",
    } for row in rows]


def get_app_monthly_plan(conn: sqlite3.Connection, user_id: int, income: float,
                         fixed_costs: float, sparraten: float) -> dict:
    """Liefert Planung und explizite Bestaetigungen getrennt von echten Buchungen."""
    ensure_app_monthly_plan_table(conn)
    month_key = date.today().strftime("%Y-%m")
    row = conn.execute(
        """SELECT income_status, fixed_costs_status, savings_status
             FROM app_monthly_plan_status
            WHERE user_id = ? AND month_key = ?""",
        (user_id, month_key),
    ).fetchone()
    confirmed_savings = conn.execute(
        """SELECT 1 FROM investment_events
             WHERE user_id = ?
               AND source IN ('investiert_command', 'app_monthly_plan')
               AND strftime('%Y-%m', created_at) = ?
             LIMIT 1""",
        (user_id, month_key),
    ).fetchone() is not None
    return {
        "month": month_key,
        "income": round(income, 2),
        "fixedCosts": round(fixed_costs, 2),
        "savings": round(sparraten, 2),
        "incomeStatus": row["income_status"] if row else "planned",
        "fixedCostsStatus": row["fixed_costs_status"] if row else "planned",
        "savingsStatus": "confirmed" if confirmed_savings else (row["savings_status"] if row else "planned"),
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
            value = float(row["amount"] or 0)
            # Das Girokonto darf ueberzogen sein — genau wie beim Schreiben in
            # rove_app_api.app_cash_accounts(). Bis 26.07. stand hier ein max(0.0, …) ueber
            # ALLE Konten: die Datenbank hielt -500 EUR, /v1/state lieferte 0 EUR. Die App
            # zeigte direkt nach der Buchung -500 (aus der POST-Antwort) und 45 Sekunden
            # spaeter 0 — die Ueberziehung war unsichtbar und das Gesamtvermoegen zu hoch.
            # Tagesgeld und Bargeld bleiben echte Guthabenkonten.
            balances[key] = round(value if key == "giro" else max(0.0, value), 2)

    # Der Bot kann zwischen zwei App-Aufrufen neue Cash-Sparraten bestaetigen. Dieser neue
    # Betrag gehoert zunaechst ins Girokonto, damit Gesamtvermögen und Bot niemals driften.
    delta = round(float(bot_cash) - sum(balances.values()), 2)
    if abs(delta) >= 0.01:
        balances["giro"] = round(balances["giro"] + delta, 2)
    return balances, True


# ===================== VERMOEGENSVERLAUF FUER DEN CHART =====================
# Zeitraeume exakt so, wie die App sie beschriftet: seriesDates() in index.html verteilt die Punkte
# gleichmaessig rueckwaerts ueber die Spanne. Anzahl und Schrittweite muessen dazu passen, sonst
# stehen unter der Kurve falsche Daten. Zusaetzlich schicken wir die Labels gleich mit (histDates),
# damit die App gar nicht erst raten muss.
NET_SERIES_RANGES = {
    "1W": (7, 1),      # (Spanne in Tagen, Schrittweite in Tagen)
    "1M": (30, 1),
    "6M": (182, 7),
    "1J": (365, 30),
    "Max": (730, 60),
}
_MONATE_KURZ = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
_WOCHENTAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _series_label(tag: date, bereich: str) -> str:
    """Gleiche Schreibweise wie die App sie sonst selbst erzeugt (de-DE, kurze Monatsnamen)."""
    if bereich == "1W":
        return f"{_WOCHENTAGE_KURZ[tag.weekday()]}, {tag.day}. {_MONATE_KURZ[tag.month - 1]}"
    if bereich == "1M":
        return f"{tag.day}. {_MONATE_KURZ[tag.month - 1]}"
    if bereich == "Max":
        return str(tag.year)
    return f"{_MONATE_KURZ[tag.month - 1]} {tag.year}"


def _daily_net_deltas(conn: sqlite3.Connection, user_id: int, tage: int) -> dict:
    """Taegliche Veraenderung des Vermoegens aus den echten Buchungen.

    Ausgaben senken es, Einnahmen heben es. Bewusst NICHT mitgezaehlt werden die uebrigen
    `app_cash_movements`: `withdrawal` ist neutral (Geld wechselt nur vom Giro ins Portemonnaie),
    und `payment`/`card` sind reine Buchhaltung zu einer Ausgabe, die bereits in `expenses` steht —
    beides wuerde die Bewegung doppelt zaehlen.
    """
    grenze = (date.today() - timedelta(days=tage + 1)).isoformat()
    deltas: dict = {}
    for row in conn.execute(
        """SELECT date(created_at) AS d, SUM(amount) AS s FROM expenses
             WHERE user_id = ? AND date(created_at) >= ? GROUP BY d""",
        (user_id, grenze),
    ).fetchall():
        deltas[row["d"]] = deltas.get(row["d"], 0.0) - float(row["s"] or 0)
    try:
        for row in conn.execute(
            """SELECT date(created_at) AS d, kind, SUM(amount) AS s FROM app_cash_movements
                 WHERE user_id = ? AND kind IN ('income', 'fixed') AND date(created_at) >= ?
                 GROUP BY d, kind""",
            (user_id, grenze),
        ).fetchall():
            betrag = float(row["s"] or 0)
            # `fixed` (Fixkosten-Abbuchung) senkt das Konto, steht aber bewusst NICHT in `expenses`
            # (sonst doppelte Budget-Verrechnung). Ohne diese Zeile fehlte sie in der Kurve und der
            # Verlauf haette an dem Tag einen Sprung gemacht, den es nie gab.
            deltas[row["d"]] = deltas.get(row["d"], 0.0) + (
                betrag if row["kind"] == "income" else -betrag
            )
    except sqlite3.OperationalError:
        pass          # Tabelle entsteht erst beim ersten Schreibvorgang der App (gleiche
                      # defensive Leseart wie get_app_cash_accounts)
    try:
        for row in conn.execute(
            """SELECT date(created_at) AS d,
                      SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END) AS s
                 FROM investment_events
                WHERE user_id = ? AND event_type = 'market_valuation'
                  AND date(created_at) >= ?
                GROUP BY d""",
            (user_id, grenze),
        ).fetchall():
            deltas[row["d"]] = deltas.get(row["d"], 0.0) + float(row["s"] or 0)
    except sqlite3.OperationalError:
        pass
    return deltas


def _net_worth_series(conn: sqlite3.Connection, user_id: int, net_worth: float):
    """Rekonstruiert den Vermoegensverlauf rueckwaerts aus den echten Buchungen.

    Bis 27.07. stand hier ein Platzhalter — derselbe Wert zweimal, fuer jeden Zeitraum. Die Kurve
    war dadurch eine Waagerechte und die Zeile darunter (`letzter - erster`) immer exakt 0, egal
    wie viel der Nutzer ausgegeben hatte (Furkan-Fund 27.07.: "die bewegen sich nicht").

    Das Verfahren braucht keine neue Tabelle: das heutige Vermoegen ist bekannt und jede Buchung
    seit einem Zeitpunkt auch, also ist der Stand von damals rechenbar —
    `Wert(gestern) = Wert(heute) - Veraenderung(heute)`. Damit ist der Verlauf sofort echt, rueckwirkend
    so weit, wie der Bot Buchungen hat, statt erst ab dem naechsten Tages-Snapshot zu wachsen.

    ⚠️ Grenze der Genauigkeit, bewusst so: rekonstruiert werden Ausgaben und Einnahmen. Kurs-
    bewegungen von ETF/Krypto, Aenderungen am Immobilienwert und von Hand korrigierte Kontostaende
    lassen sich rueckwaerts nicht trennen — die wirken so, als haetten sie immer den heutigen Wert
    gehabt. Fuer 1W und 1M ist die Kurve damit auf den Cent genau; fuer 1J zeigt sie die Spar- und
    Ausgabenbewegung, nicht die Kursentwicklung.
    """
    max_tage = max(spanne for spanne, _ in NET_SERIES_RANGES.values())
    deltas = _daily_net_deltas(conn, user_id, max_tage)
    heute = date.today()

    # werte[i] = Vermoegen vor i Tagen. Rueckwaerts: den Tagesdelta wieder herausrechnen.
    werte = [float(net_worth)]
    for i in range(max_tage):
        tag = (heute - timedelta(days=i)).isoformat()
        werte.append(werte[-1] - deltas.get(tag, 0.0))

    series: dict = {}
    hist_dates: dict = {}
    for name, (spanne, schritt) in NET_SERIES_RANGES.items():
        offsets = sorted({*range(spanne, -1, -schritt), 0}, reverse=True)
        series[name] = [round(werte[min(o, len(werte) - 1)] / 1000, 3) for o in offsets]
        hist_dates[name] = [
            "Heute" if o == 0 else _series_label(heute - timedelta(days=o), name)
            for o in offsets
        ]
    return series, hist_dates


def build_live_app_data(conn: sqlite3.Connection, user_id: int) -> dict:
    """Liefert die Bot-Felder, die eine bereits gekoppelte App sicher aktualisieren kann.

    Lokale App-Ergänzungen wie Sachwerte oder ein manuell gepflegter Immobilienwert gehören
    absichtlich nicht hierher: Die App führt sie beim Aktualisieren weiter, statt sie zu
    überschreiben. Der Bot ist derzeit nur Quelle für Cash, Investments, Fixkosten, Ziele und
    Monatsbuchungen.
    """
    # Ein geplanter Wechsel wird beim ersten Zugriff im neuen Monat aktiv. Er ist
    # nur eine neue Vorgabe fuer den Monatsplan, keine automatische Geldbewegung.
    apply_due_scheduled_savings(conn, user_id)
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
    etf_plan = get_app_etf_savings_plan(conn, user_id, etf_savings)
    scheduled_savings = get_app_scheduled_savings(conn, user_id)
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
    score = calculate_score(conn, user_id, u, monthly_expenses)

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

    net_series, net_hist_dates = _net_worth_series(conn, user_id, net_worth)
    tx = _build_tx(conn, user_id)
    # Die App-Navigation kann bis zu drei abgeschlossene Monate zurueckgehen. Diese
    # Buchungen sind bewusst nur lesbar: Der laufende Monat bleibt die einzige Stelle,
    # an der der Nutzer etwas aendern kann.
    tx_history = {
        month_key: history
        for month_key in _previous_month_keys()
        if (history := _build_tx(conn, user_id, month_key))
    }
    budget_history = {
        month_key: history
        for month_key in _previous_month_keys()
        if (history := _build_budgets(conn, user_id, month_key))
    }
    return {
        "onboardingRequired": int(u.get("onboarding_step") or 0) < 10,
        "netWorth": round(net_worth, 2),
        "series": net_series,
        "histDates": net_hist_dates,
        "identity": _identity(conn, user_id),
        "payday": _payday_block(conn, user_id, u, income),
        "assetOrder": get_app_asset_order(conn, user_id),
        "assets": [a for a in (
            {"assetKey": "cash:giro", "name": "Girokonto", "source": "bot", "icon": "bank", "tint": "#2AABEE",
             "value": cash_accounts["giro"],
             "sub": "verfuegbar" if has_cash_accounts else "aus dem Bot"} if (cash_accounts["giro"] or has_cash_accounts) else None,
            {"assetKey": "cash:tagesgeld", "name": "Tagesgeld", "source": "bot", "icon": "coins", "tint": "#35D07F",
             "value": cash_accounts["tagesgeld"], "sub": "Rücklage"} if (cash_accounts["tagesgeld"] or has_cash_accounts) else None,
            {"assetKey": "cash:bargeld", "name": "Bargeld", "source": "bot", "icon": "wallet", "tint": "#B08D57",
             "value": cash_accounts["bargeld"], "sub": "im Portemonnaie"} if (cash_accounts["bargeld"] or has_cash_accounts) else None,
            {"assetKey": "asset:investments", "name": "ETF & Investments", "source": "bot", "icon": "chart", "tint": "#8B7DF5",
             "value": round(etf, 2), "sub": etf_sub,
             **({"positions": etf_positions} if etf_positions else {})} if etf else None,
            {"assetKey": "asset:crypto", "name": "Krypto", "source": "bot", "icon": "bitcoin", "tint": "#F7931A",
             "value": round(crypto, 2), "sub": crypto_sub,
             **({"positions": crypto_positions} if crypto_positions else {})} if crypto else None,
            {"assetKey": "asset:property", "name": "Immobilie", "source": "app", "icon": "house", "tint": "#D8B66A",
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
        "tx": tx,
        "txHistory": tx_history,
        "budgetHistory": budget_history,
        "budgets": _build_budgets(conn, user_id),
        "reports": _build_reports(conn, user_id),
        "monthlyPlan": monthly_plan,
        "etfPlan": etf_plan,
        "scheduledSavings": scheduled_savings,
        "score": score,
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
        "vertraege": build_app_contract_groups(conn, user_id, details),
        "goals": ([{
            "id": "primary",
            "t": u.get("goal_description"),
            "icon": "coins", "tint": "#2AABEE",
            "cur": get_app_primary_goal_progress(conn, user_id, float(u.get("goal_amount") or 0)),
            "tar": round(float(u.get("goal_amount") or 0), 2) or 1,
            # Wird weiterhin als Bot-Hauptziel markiert, damit lokale Snapshots es niemals
            # wiederbeleben. Bearbeiten und Löschen übernimmt jetzt trotzdem die App-API.
            "source": "bot",
        }] if (u.get("goal_description") or "").strip() else []) + [
            goal for goal in get_app_goals(conn, user_id)
            if str(goal["t"]).casefold() != str(u.get("goal_description") or "").strip().casefold()
        ],
    }


def _salary_booked_this_month(conn: sqlite3.Connection, user_id: int, erwartet: float) -> bool:
    """Wurde das Gehalt diesen Monat schon verbucht?

    Bewusst aus den Bewegungen abgeleitet statt in einem Merker-Feld gemerkt: ein Merker koennte
    von der Wahrheit abweichen (Nutzer loescht die Buchung wieder), die Bewegungen koennen das
    nicht. Erkannt wird eine Einnahme, deren Bezeichnung nach Gehalt klingt ODER die mindestens
    die Haelfte des erwarteten Gehalts ausmacht — eine kleine Nebeneinnahme soll die Zahltag-Frage
    nicht unterdruecken, das echte Gehalt unter anderem Namen aber schon.
    """
    try:
        rows = conn.execute(
            """SELECT amount, COALESCE(label, '') AS label FROM app_cash_movements
                 WHERE user_id = ? AND kind = 'income'
                   AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return False
    schwelle = max(1.0, erwartet * 0.5)
    for row in rows:
        text = str(row["label"]).casefold()
        if "gehalt" in text or "lohn" in text:
            return True
        if float(row["amount"] or 0) >= schwelle:
            return True
    return False


def _payday_block(conn: sqlite3.Connection, user_id: int, u: dict, erwartet: float) -> dict:
    """Zahltag des Nutzers — damit die App zum richtigen Termin fragt statt pauschal am 1.

    Vorher oeffnete sich der Monatscheck immer am 1., unabhaengig davon, wann das Gehalt kommt
    (Furkan selbst bekommt am 15.). Der Tag wird einmal in der App abgefragt und hier gespeichert;
    `day = 0` heisst „noch nie gesetzt" und ist fuer die App das Signal, danach zu fragen.

    Kein Zahltag = keine Faelligkeit. Wir raten nichts — lieber fragt die App einmal nach, als dass
    Rov.E einen Termin erfindet und zur falschen Zeit eine Gehaltsbuchung vorschlaegt.
    """
    try:
        day = int(u.get("payday") or 0)
    except (TypeError, ValueError):
        day = 0
    if not 1 <= day <= 31:
        day = 0

    heute = date.today()
    # Zahltag am 31. in einem kuerzeren Monat: der letzte Monatstag zaehlt als erreicht.
    letzter = (heute.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return {
        "day": day,
        "faellig": bool(day and heute.day >= min(day, letzter.day)),
        "gebucht": _salary_booked_this_month(conn, user_id, erwartet),
    }


def _identity(conn: sqlite3.Connection, user_id: int) -> dict:
    """Name und Login-Adresse des angemeldeten Nutzers.

    Bis 27.07. lieferte der Server hier gar nichts, und in `index.html` standen „Furkan" und
    „project-clarity@outlook.com" fest im HTML. `applyProfileIdentity()` haette das ueberschrieben,
    lief aber nur im Profil-Modus — im App-Modus sah also JEDER Beta-Tester Furkans Namen und
    Furkans E-Mail-Adresse in den Einstellungen (Furkan-Fund 27.07., erste eingeladene Tester).

    Fallback fuer den Namen ist der Teil vor dem @ der eigenen Login-Adresse — nie ein fremder
    Name, lieber gar keiner.
    """
    try:
        row = conn.execute(
            """SELECT email, display_name FROM app_accounts
                 WHERE user_id = ? ORDER BY verified_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return {"email": "", "name": "", "isAdmin": False}
    if not row:
        return {"email": "", "name": "", "isAdmin": False}

    email = str(row["email"] or "").strip()
    try:
        name = str(row["display_name"] or "").strip()
    except (IndexError, KeyError):
        name = ""              # Spalte existiert in aelteren Datenbanken noch nicht
    if not name and "@" in email:
        name = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip().title()
    admin_ids = {
        int(value.strip())
        for value in os.getenv("ROVE_ADMIN_USER_IDS", "").split(",")
        if value.strip().isdigit()
    }
    return {"email": email, "name": name, "isAdmin": user_id in admin_ids}


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

    v = automatischer Marktwert, sobald eine Position mit Stueckzahl konfiguriert ist;
    sonst bleibt total_invested der manuell gepflegte Stand. Fehlende Tabelle → []."""
    try:
        ensure_market_tracking_schema(conn)
        ensure_app_etf_position_plans_table(conn)
        rows = conn.execute(
            """SELECT ph.id, ph.instrument_label, ph.instrument_type,
                      ph.total_invested, ph.market_value, ph.start_price, ph.last_price,
                      ph.price_symbol, ph.quantity, ph.quote_currency,
                      ph.valuation_enabled, ph.market_value_updated_at, ph.market_data_provider,
                      pp.monthly_amount AS plan_amount, pp.execution_day AS plan_day,
                      pp.source_account AS plan_source, pp.mode AS plan_mode,
                      pp.active AS plan_active, pp.start_month AS plan_start_month
               FROM portfolio_holdings ph
               LEFT JOIN app_etf_position_plans pp
                 ON pp.user_id = ph.user_id AND pp.holding_id = ph.id
               WHERE ph.user_id = ?
               ORDER BY COALESCE(ph.market_value, ph.total_invested, 0) DESC, ph.instrument_label""",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        pos = {
            "n": r["instrument_label"],
            "v": round(float(r["market_value"] if r["market_value"] is not None else r["total_invested"] or 0), 2),
            # Ohne Broker-Anbindung ist der angezeigte Depotwert ein manuell
            # gepflegter Wert. Er darf deshalb direkt in der App korrigiert werden.
            "editable": True,
            "holding": True,
            "holdingId": int(r["id"]),
            "assetType": r["instrument_type"] or "etf",
            "live": bool(r["valuation_enabled"] and r["quantity"] and r["price_symbol"]),
        }
        if r["plan_day"] is not None:
            pos["positionPlan"] = {
                "configured": True,
                "amount": round(max(0.0, float(r["plan_amount"] or 0)), 2),
                "executionDay": int(r["plan_day"]),
                "sourceAccount": str(r["plan_source"]),
                "mode": str(r["plan_mode"]),
                "active": bool(r["plan_active"]),
                "startMonth": str(r["plan_start_month"]),
            }
        if pos["live"]:
            pos.update({
                "quantity": round(float(r["quantity"]), 8),
                "symbol": r["price_symbol"],
                "currency": r["quote_currency"] or "EUR",
                "updatedAt": r["market_value_updated_at"],
                "provider": r["market_data_provider"] or "twelve_data",
            })
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
                  AND NOT EXISTS (
                      SELECT 1 FROM portfolio_holdings ph
                       WHERE ph.user_id = investment_events.user_id
                         AND LOWER(TRIM(ph.instrument_label)) = LOWER(TRIM(investment_events.asset_name))
                  )
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
            "assetType": "stock",
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
        }
        # score_total/score_label bleiben nur fuer alte Aufrufer im Funktionskopf. Der
        # State enthaelt jetzt immer den live berechneten Score inklusive Faktoren und RP.
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
