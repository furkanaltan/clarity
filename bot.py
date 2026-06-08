import os
import sys
import time
import signal
import logging
import sqlite3
import json
import re
import random
import calendar
from datetime import datetime, date, timedelta
from contextlib import contextmanager
import telebot
import openai
from dotenv import load_dotenv

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:
    BackgroundScheduler = None
    CronTrigger = None

# ====================== KONFIGURATION ======================
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN or not OPENAI_API_KEY:
    raise ValueError("❌ TELEGRAM_TOKEN oder OPENAI_API_KEY fehlt in der .env Datei!")

openai.api_key = OPENAI_API_KEY
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS") or os.getenv("ADMIN_USER_ID") or ""
ADMIN_USER_IDS = {
    int(x.strip()) for x in ADMIN_USER_IDS_RAW.split(",")
    if x.strip().lstrip("-").isdigit()
}

REPORT_SEND_WINDOW_START_HOUR = int(os.getenv("REPORT_SEND_WINDOW_START_HOUR", "8"))
REPORT_SEND_WINDOW_END_HOUR = int(os.getenv("REPORT_SEND_WINDOW_END_HOUR", "14"))
REPORT_WORKER_BATCH_SIZE = int(os.getenv("REPORT_WORKER_BATCH_SIZE", "1"))
REPORT_WORKER_INTERVAL_SECONDS = int(os.getenv("REPORT_WORKER_INTERVAL_SECONDS", "10"))
REPORT_MAX_ATTEMPTS = int(os.getenv("REPORT_MAX_ATTEMPTS", "3"))
REPORT_RETRY_DELAY_MINUTES = int(os.getenv("REPORT_RETRY_DELAY_MINUTES", "15"))
REPORT_CREATION_MISFIRE_GRACE_SECONDS = int(os.getenv("REPORT_CREATION_MISFIRE_GRACE_SECONDS", "21600"))

# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ====================== STATE CONSTANTS ======================
(STEP_START, STEP_INCOME, STEP_OTHER_INCOME, STEP_FIXED_COSTS, STEP_GOAL_DESCRIPTION,
 STEP_GOAL_AMOUNT, STEP_CURRENT_INVESTMENTS, STEP_CURRENT_CASH,
 STEP_ETF_SAVINGS, STEP_CASH_SAVINGS, STEP_NORMAL) = range(11)

(STEP_ADAPT_HOUSING, STEP_ADAPT_MOBILITY, STEP_ADAPT_ABOS,
 STEP_ADAPT_INSURANCE, STEP_ADAPT_CREDITS) = range(11, 16)

ALLOWED_USER_FIELDS = {
    "income", "other_income", "fixed_costs", "goal_description", "goal_amount",
    "clarity_points", "onboarding_step", "fixed_costs_details", "last_activity_date",
    "etf_savings", "cash_savings", "current_investments", "current_cash",
    "portfolio_balance", "streak_days", "current_month"
}

# ====================== MERCHANT KEYWORDS (Hybrid Layer 1) ======================
MERCHANT_KEYWORDS = {
    "Lidl":       ["lidl"],
    "Rewe":       ["rewe"],
    "Aldi":       ["aldi"],
    "Edeka":      ["edeka"],
    "Kaufland":   ["kaufland"],
    "Penny":      ["penny"],
    "Netto":      ["netto"],
    "Aral":       ["aral"],
    "Shell":      ["shell"],
    "Agip":       ["agip", "eni"],
    "Esso":       ["esso"],
    "McDonalds":  ["mcdonalds", "mc donalds", "mcdonald"],
    "Burger King":["burger king", "burgerking"],
    "Subway":     ["subway"],
    "Amazon":     ["amazon"],
    "Netflix":    ["netflix"],
    "Spotify":    ["spotify"],
    "Disney+":    ["disney+", "disneyplus"],
    "Zalando":    ["zalando"],
    "DM":         ["dm drogerie", "dm-drogerie"],
    "Rossmann":   ["rossmann"],
}

CATEGORY_MAPPING = {
    "Lidl": "LEBENSMITTEL", "Rewe": "LEBENSMITTEL", "Aldi": "LEBENSMITTEL",
    "Edeka": "LEBENSMITTEL", "Kaufland": "LEBENSMITTEL", "Penny": "LEBENSMITTEL",
    "Netto": "LEBENSMITTEL", "Aral": "MOBILITAET", "Shell": "MOBILITAET",
    "Agip": "MOBILITAET", "Esso": "MOBILITAET", "McDonalds": "RESTAURANTS",
    "Burger King": "RESTAURANTS", "Subway": "RESTAURANTS", "Amazon": "SHOPPING",
    "Zalando": "SHOPPING", "Netflix": "ABOS", "Spotify": "ABOS", "Disney+": "ABOS",
    "DM": "DROGERIE", "Rossmann": "DROGERIE",
}

# ====================== CATEGORY KEYWORDS (Hybrid Layer 2) ======================
# Ermöglicht lokale Erkennung ohne KI: "Döner 8€", "Kino 15", "Tanken 60" etc.
CATEGORY_KEYWORDS = {
    "RESTAURANTS": [
        "döner", "pizza", "kebab", "sushi", "ramen", "restaurant", "lieferando",
        "lieferdienst", "takeaway", "fastfood", "bäcker", "bäckerei", "café",
        "coffee", "wok", "mensa", "imbiss", "currywurst", "pommes", "schnitzel",
        "sandwich", "wraps", "mittagessen", "abendessen", "frühstück", "brunch",
        "shawarma", "falafel", "burger", "wings", "nuggets", "asia", "thai",
        "griechisch", "türkisch", "italienisch",
    ],
    "FREIZEIT": [
        "kino", "cinema", "konzert", "theater", "oper", "museum", "bowling",
        "minigolf", "escape room", "escaperoom", "ticket", "eintritt",
        "schwimmbad", "freibad", "spa", "wellness", "festival", "event",
        "veranstaltung", "freizeitpark", "zoo", "ausstellung", "messe",
        "fußball", "basketball", "sport event", "handball",
    ],
    "MOBILITAET": [
        "tanken", "benzin", "diesel", "super", "e10", "e5", "parkhaus",
        "parken", "parkticket", "taxi", "uber", "bolt", "öpnv", "bahn",
        "bus", "tram", "autowäsche", "werkstatt", "reparatur", "reifen",
        "ölwechsel", "hauptuntersuchung", "hu", "tüv",
    ],
    "GESUNDHEIT": [
        "apotheke", "arzt", "zahnarzt", "medikament", "tabletten", "pille",
        "krankenhaus", "physiotherapie", "optiker", "brille", "kontaktlinsen",
        "therapie", "psychologe", "impfung",
    ],
    "SHOPPING": [
        "klamotten", "kleidung", "schuhe", "mode", "kaufhaus", "primark",
        "zara", "h&m", "outlet", "flohmarkt", "elektronik", "handy",
        "laptop", "kopfhörer", "gadget",
    ],
    "LEBENSMITTEL": [
        "supermarkt", "lebensmittel", "gemüse", "obst", "brot",
        "milch", "fleisch", "wurst", "käse", "einkaufen",
    ],
    "PFLEGE": [
        "friseur", "frisör", "frisoer", "barber", "haarschnitt", "haare",
        "kosmetik", "nagelstudio", "nägel", "naegel", "parfum", "körperpflege",
        "koerperpflege", "rasur", "bart", "waxing",
    ],
    "ABOS": [
        "abo", "abonnement", "mitgliedschaft", "gym", "fitnessstudio",
        "studio", "mcfit", "clever fit", "planet fitness",
    ],
}

CATEGORY_EMOJIS = {
    "LEBENSMITTEL": "🛒", "MOBILITAET": "🚗", "RESTAURANTS": "🍽️",
    "ABOS": "📱", "SHOPPING": "🛍️", "FREIZEIT": "🎮",
    "VERSICHERUNG": "🛡️", "MIETE": "🏠", "DROGERIE": "🧴",
    "GESUNDHEIT": "💊", "SONSTIGES": "📦",
}

# ====================== GAMIFICATION CONSTANTS ======================
RANKS = [
    (0,    "Rookie",           "🥚"),
    (50,   "Stratege",         "🔍"),
    (200,  "Controller",       "📊"),
    (500,  "Investor",         "🧱"),
    (1000, "Portfoliomanager", "🏗️"),
    (2500, "Kapitalist",       "🏛️"),
    (5000, "Clarity Elite",    "💎"),
]

BADGES = {
    "streak_7":         ("⚡", "Erste Woche",          "7 Tage in Folge getrackt."),
    "streak_30":        ("🔥", "Eiserner Monat",        "30 Tage Streak. Eiserne Disziplin."),
    "first_investment": ("📈", "Erstes Investment",     "Erstes investiertes Kapital."),
    "thousand_club":    ("💰", "Tausender-Club",        "1.000€ Gesamtvermögen erreicht."),
    "emergency_fund":   ("🛡️", "Notgroschen",           "3 Monate Fixkosten als Reserve aufgebaut."),
    "savings_master":   ("🏆", "Spar-Meister",          "Sparquote über 20%."),
    "ten_k_club":       ("💎", "Fünfstellige Freiheit", "10.000€ Portfolio erreicht."),
    "no_fastfood_30":   ("🥗", "Fast Food Fasten",      "30 Tage keine Restaurant-Ausgaben."),
    "month_win":        ("🏅", "Monats-Sieg",           "Monat im grünen Budget abgeschlossen."),
}

# ====================== DATABASE ======================
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id             INTEGER PRIMARY KEY,
            income              REAL    DEFAULT 0.0,
            other_income        REAL    DEFAULT 0.0,
            fixed_costs         REAL    DEFAULT 0.0,
            goal_description    TEXT    DEFAULT '',
            goal_amount         REAL    DEFAULT 0.0,
            clarity_points      INTEGER DEFAULT 0,
            onboarding_step     INTEGER DEFAULT 0,
            fixed_costs_details TEXT    DEFAULT '{}',
            last_activity_date  TEXT    DEFAULT '',
            etf_savings         REAL    DEFAULT 0.0,
            cash_savings        REAL    DEFAULT 0.0,
            current_investments REAL    DEFAULT 0.0,
            current_cash        REAL    DEFAULT 0.0,
            portfolio_balance   REAL    DEFAULT 0.0,
            streak_days         INTEGER DEFAULT 0,
            current_month       TEXT    DEFAULT ''
        )''')

        conn.execute('''CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            amount      REAL,
            category    TEXT,
            merchant    TEXT,
            description TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE TABLE IF NOT EXISTS user_badges (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            badge_key TEXT,
            earned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, badge_key),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE TABLE IF NOT EXISTS monthly_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER,
            month          TEXT,
            clarity_score  INTEGER DEFAULT 0,
            total_expenses REAL    DEFAULT 0.0,
            budget_ok      INTEGER DEFAULT 0,
            net_worth      REAL    DEFAULT 0.0,
            UNIQUE(user_id, month),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE TABLE IF NOT EXISTS report_jobs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            report_month  TEXT    NOT NULL,
            scheduled_at  TEXT    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'pending',
            attempts      INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT    DEFAULT '',
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
            updated_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, report_month),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_report_jobs_due
            ON report_jobs(status, scheduled_at)
        ''')

        # Sichere Migration fuer bestehende Datenbanken
        for col_name, col_def in [
            ("streak_days", "INTEGER DEFAULT 0"),
            ("current_month", "TEXT DEFAULT ''"),
            ("clarity_points", "INTEGER DEFAULT 0"),
            ("last_activity_date", "TEXT DEFAULT ''"),
            ("etf_savings", "REAL DEFAULT 0.0"),
            ("cash_savings", "REAL DEFAULT 0.0"),
            ("current_investments", "REAL DEFAULT 0.0"),
            ("current_cash", "REAL DEFAULT 0.0"),
            ("portfolio_balance", "REAL DEFAULT 0.0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                logger.info(f"Migration: '{col_name}' hinzugefuegt.")
            except sqlite3.OperationalError:
                pass
            except Exception as e:
                logger.error(f"Migration-Fehler '{col_name}': {e}")

        try:
            conn.execute("ALTER TABLE monthly_snapshots ADD COLUMN net_worth REAL DEFAULT 0.0")
            logger.info("Migration: 'net_worth' zu monthly_snapshots hinzugefuegt.")
        except sqlite3.OperationalError:
            pass
        except Exception as e:
            logger.error(f"Migration-Fehler 'monthly_snapshots.net_worth': {e}")

        conn.commit()
    logger.info("✅ Datenbank initialisiert.")

def get_or_create_user(user_id: int) -> dict:
    """Holt oder erstellt User – eine einzige DB-Abfrage."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT INTO users (user_id, onboarding_step) VALUES (?, ?)",
                (user_id, STEP_START)
            )
            conn.commit()
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()

        try:
            details = json.loads(row["fixed_costs_details"] or "{}")
        except (json.JSONDecodeError, TypeError):
            details = {}
            logger.warning(f"fixed_costs_details für User {user_id} nicht parsbar.")

        u = dict(row)
        u["details"] = details
        return u

def update_user_field(user_id: int, field: str, value) -> bool:
    """Aktualisiert ein Nutzerfeld – Whitelist schützt vor SQL-Injection."""
    if field not in ALLOWED_USER_FIELDS:
        logger.error(f"Unerlaubtes Feld '{field}' – abgebrochen.")
        return False
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
    return True

ONBOARDING_BACK_STEPS = {
    STEP_INCOME: None,
    STEP_OTHER_INCOME: STEP_INCOME,
    STEP_FIXED_COSTS: STEP_OTHER_INCOME,
    STEP_GOAL_DESCRIPTION: STEP_FIXED_COSTS,
    STEP_GOAL_AMOUNT: STEP_GOAL_DESCRIPTION,
    STEP_CURRENT_INVESTMENTS: STEP_GOAL_AMOUNT,
    STEP_CURRENT_CASH: STEP_CURRENT_INVESTMENTS,
    STEP_ETF_SAVINGS: STEP_CURRENT_CASH,
    STEP_CASH_SAVINGS: STEP_ETF_SAVINGS,
}

ONBOARDING_BACK_MESSAGES = {
    STEP_INCOME: "Schritt 1 von 9: Wie hoch ist dein monatliches Nettoeinkommen?\n(z.B. 2500)",
    STEP_OTHER_INCOME: "Schritt 2 von 9: Weitere Einkommen? (Falls keine, 0)",
    STEP_FIXED_COSTS: "Schritt 3 von 9: Fixkosten gesamt?",
    STEP_GOAL_DESCRIPTION: "Schritt 4 von 9: Dein Sparziel in Worten?",
    STEP_GOAL_AMOUNT: "Schritt 5 von 9: Welchen Betrag brauchst du?",
    STEP_CURRENT_INVESTMENTS: "Schritt 6 von 9: Aktuell investiertes Vermoegen? (ETF/Aktien, 0 falls keines)",
    STEP_CURRENT_CASH: "Schritt 7 von 9: Cash-Reserven? (Tagesgeld/Giro)",
    STEP_ETF_SAVINGS: "Schritt 8 von 9: Monatliche ETF-Sparrate?",
    STEP_CASH_SAVINGS: "Schritt 9 von 9: Monatliche Cash-Sparrate?",
}

REFINE_BACK_STEPS = {
    STEP_ADAPT_HOUSING: None,
    STEP_ADAPT_MOBILITY: STEP_ADAPT_HOUSING,
    STEP_ADAPT_ABOS: STEP_ADAPT_MOBILITY,
    STEP_ADAPT_INSURANCE: STEP_ADAPT_ABOS,
    STEP_ADAPT_CREDITS: STEP_ADAPT_INSURANCE,
}

REFINE_BACK_MESSAGES = {
    STEP_ADAPT_HOUSING: "Profil verfeinern - Teil 1: Wohnen\n\nMiete, Strom, Gas?\n(z.B. 800 60 40)",
    STEP_ADAPT_MOBILITY: "Teil 2: Mobilitaet\nAuto, Versicherung, Bahn?\n(z.B. 200 80 49)",
    STEP_ADAPT_ABOS: "Teil 3: Abos\nNetflix, Spotify, Prime, Disney?\n(z.B. 14 10 9 8)",
    STEP_ADAPT_INSURANCE: "Teil 4: Versicherungen\nHaftpflicht, BU, Rechtsschutz?\n(z.B. 6 45 25)",
    STEP_ADAPT_CREDITS: "Teil 5: Kredite\nImmobilie, Auto, Konsum?\n(Falls keine -> 0)",
}

def add_cp(user_id: int, points: int) -> int:
    """Fügt CP hinzu, gibt neuen Stand zurück."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET clarity_points = clarity_points + ? WHERE user_id = ?",
            (points, user_id)
        )
        conn.commit()
        cursor.execute("SELECT clarity_points FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()["clarity_points"]

def parse_currency(text: str):
    """Wandelt Text in float um. Gibt None zurück bei ungültigem Input."""
    cleaned = re.sub(r'[^\d,.-]', '', text).replace(',', '.')
    try:
        val = float(cleaned)
        return val if val >= 0 else None
    except ValueError:
        return None

def calculate_time_to_goal(goal_amount: float, etf_monthly: float, cash_monthly: float,
                           current_investments: float = 0.0, current_cash: float = 0.0) -> str:
    """Ziel-Prognose mit echten Startwerten und Zinseszins."""
    if etf_monthly + cash_monthly <= 0:
        return "Sparrate ist 0 – Ziel nicht erreichbar."
    etf_bal, cash_bal, months = current_investments, current_cash, 0
    while (etf_bal + cash_bal) < goal_amount:
        etf_bal = (etf_bal + etf_monthly) * (1 + 0.07 / 12)
        cash_bal = (cash_bal + cash_monthly) * (1 + 0.02 / 12)
        months += 1
        if months > 1200:
            return "Über 100 Jahre – erhöhe deine Sparrate."
    jahre, monate = divmod(months, 12)
    return f"{jahre} Jahre und {monate} Monate" if jahre > 0 else f"{monate} Monate"

def extract_merchant_name(text_input: str) -> str:
    """Extrahiert den ersten sinnvollen Begriff als Händlernamen."""
    words = re.findall(r'[a-zA-ZäöüÄÖÜß]+', text_input)
    return words[0].capitalize() if words else "Unbekannt"


def get_actor_id(message) -> int:
    """User-ID fuer Admin-Pruefungen; in Gruppen ist chat.id nicht zwingend die User-ID."""
    return message.from_user.id if getattr(message, "from_user", None) else message.chat.id


def days_left_in_month(today: date = None) -> int:
    today = today or date.today()
    total_days = calendar.monthrange(today.year, today.month)[1]
    return max(1, total_days - today.day + 1)


def get_month_expenses(user_id: int) -> float:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT SUM(amount) FROM expenses
               WHERE user_id = ?
               AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')""",
            (user_id,)
        )
        return cursor.fetchone()[0] or 0.0


def calculate_remaining_budget(u: dict, user_id: int) -> tuple:
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    fixed = u.get("fixed_costs") or 0
    total_expenses = get_month_expenses(user_id)
    remaining = income - fixed - total_expenses
    return remaining, total_expenses, income, fixed


CATEGORY_ALIASES = {
    "essen": ["LEBENSMITTEL", "RESTAURANTS"],
    "lebensmittel": ["LEBENSMITTEL"],
    "einkaufen": ["LEBENSMITTEL"],
    "supermarkt": ["LEBENSMITTEL"],
    "restaurant": ["RESTAURANTS"],
    "restaurants": ["RESTAURANTS"],
    "doener": ["RESTAURANTS"],
    "döner": ["RESTAURANTS"],
    "friseur": ["PFLEGE"],
    "frisör": ["PFLEGE"],
    "barber": ["PFLEGE"],
    "pflege": ["PFLEGE"],
    "shopping": ["SHOPPING"],
    "tanken": ["MOBILITAET"],
    "mobilität": ["MOBILITAET"],
    "mobilitaet": ["MOBILITAET"],
    "freizeit": ["FREIZEIT"],
    "drogerie": ["DROGERIE"],
    "gesundheit": ["GESUNDHEIT"],
    "abos": ["ABOS"],
}


def detect_category_alias(text_lower: str):
    for alias, categories in CATEGORY_ALIASES.items():
        if alias in text_lower:
            return alias, categories
    return None, []


def detect_period_sql(text_lower: str) -> tuple:
    if "woche" in text_lower or "7 tage" in text_lower:
        return "DATE(created_at) >= DATE('now', '-6 days', 'localtime')", "in den letzten 7 Tagen"
    return "strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')", "diesen Monat"


def maybe_answer_category_spending(user_id: int, text_lower: str) -> str:
    asks_amount = any(w in text_lower for w in ["wie viel", "wieviel", "ausgegeben", "geld", "kosten"])
    alias, categories = detect_category_alias(text_lower)
    if not asks_amount or not categories:
        return ""

    period_sql, period_label = detect_period_sql(text_lower)
    placeholders = ",".join("?" for _ in categories)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT category, SUM(amount) AS total
                FROM expenses
                WHERE user_id = ?
                  AND category IN ({placeholders})
                  AND {period_sql}
                GROUP BY category
                ORDER BY total DESC""",
            (user_id, *categories)
        )
        rows = cursor.fetchall()

    totals = {row["category"]: row["total"] or 0.0 for row in rows}
    total = sum(totals.values())
    if total <= 0:
        return f"Dazu habe ich {period_label} noch keine Ausgaben gefunden."

    if len(categories) == 1:
        cat = categories[0]
        emoji = CATEGORY_EMOJIS.get(cat, "")
        return f"{emoji} {cat}: {total:.2f} EUR {period_label}."

    lines = [f"Essen gesamt {period_label}: {total:.2f} EUR"]
    for cat in categories:
        emoji = CATEGORY_EMOJIS.get(cat, "")
        lines.append(f"{emoji} {cat}: {totals.get(cat, 0.0):.2f} EUR")
    return "\n".join(lines)


def maybe_answer_weekly_budget(user_id: int, u: dict, text_lower: str) -> str:
    if "wochenbudget" not in text_lower and "wochen budget" not in text_lower:
        return ""
    remaining, total_expenses, income, fixed = calculate_remaining_budget(u, user_id)
    left_days = days_left_in_month()
    daily = remaining / left_days
    weekly = daily * 7
    return (
        f"Dein Restbudget diesen Monat: {remaining:.2f} EUR\n"
        f"Noch {left_days} Tage im Monat.\n"
        f"Tagesbudget: ca. {daily:.2f} EUR\n"
        f"Wochenbudget: ca. {weekly:.2f} EUR"
    )


def format_expense_confirmation(items: list, cp_text: str) -> str:
    if len(items) == 1:
        item = items[0]
        emoji = CATEGORY_EMOJIS.get(item["category"], "")
        return f"OK: {item['amount']:.2f} EUR - {item['merchant']}\n{emoji} {item['category']} - {cp_text}"

    lines = [f"{len(items)} Ausgaben verbucht:"]
    for item in items:
        emoji = CATEGORY_EMOJIS.get(item["category"], "")
        lines.append(f"{emoji} {item['merchant']} - {item['category']} - {item['amount']:.2f} EUR")
    lines.append(cp_text)
    return "\n".join(lines)


def is_off_topic_request(text_lower: str) -> bool:
    finance_words = [
        "geld", "budget", "ausgabe", "ausgaben", "sparen", "sparrate", "score",
        "invest", "investment", "konto", "cash", "vermögen", "vermoegen", "report",
        "monat", "woche", "ziel", "fixkosten", "einkommen", "essen", "friseur",
    ]
    if any(w in text_lower for w in finance_words):
        return False
    if re.search(r"\d+(?:[.,]\d{1,2})?", text_lower):
        return False

    off_topic_words = [
        "weltfrieden", "buch", "aufsatz", "essay", "geschichte", "gedicht",
        "liebesbrief", "programmier", "python", "rezept", "hausaufgabe", "politik",
        "religion", "philosophie", "film", "spiel", "reiseplan",
    ]
    question_words = ["wie", "warum", "was", "wer", "wo", "wann", "kannst du", "schreib"]
    return any(w in text_lower for w in off_topic_words) or any(text_lower.startswith(w) for w in question_words)


# ====================== GAMIFICATION ENGINE ======================

def get_rank(points: int) -> tuple:
    """Gibt (name, emoji, punkte_bis_nächster_rang) zurück."""
    name, emoji = RANKS[0][1], RANKS[0][2]
    next_threshold = None
    for threshold, r_name, r_emoji in RANKS:
        if points >= threshold:
            name, emoji = r_name, r_emoji
        elif next_threshold is None:
            next_threshold = threshold
    return name, emoji, (next_threshold - points) if next_threshold else 0

def calculate_clarity_score(u: dict, total_expenses: float) -> dict:
    """
    Clarity Score (0–100) aus 4 Säulen:
    1. Budgetkontrolle (25) – Wie viel freies Budget ist übrig?
    2. Sparquote (25)        – Liegt Sparrate über 15% des Einkommens?
    3. Konsistenz (25)       – Wie viele Tage im Monat wurde getrackt?
    4. Spareffizienz (25)    – Reicht verbleibendes Budget für Sparrate?
    """
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    fixed = u.get("fixed_costs") or 0
    free_budget = income - fixed
    remaining = free_budget - total_expenses
    savings_rate = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)

    s1 = 0
    if free_budget > 0:
        ratio = remaining / free_budget
        s1 = 25 if ratio >= 0.2 else max(0, int(25 * ratio / 0.2))

    s2 = 0
    if income > 0:
        ratio = savings_rate / income
        s2 = 25 if ratio >= 0.15 else max(0, int(25 * ratio / 0.15))

    days_elapsed = max(date.today().day, 1)
    streak = min(u.get("streak_days") or 0, days_elapsed)
    s3 = int(25 * streak / days_elapsed)

    s4 = 0
    if savings_rate > 0:
        s4 = 25 if remaining >= savings_rate else max(0, int(25 * remaining / savings_rate))
    elif remaining > 0:
        s4 = 25

    return {
        "total": min(100, s1 + s2 + s3 + s4),
        "budget": s1, "savings": s2, "consistency": s3, "efficiency": s4
    }

def award_badge(user_id: int, badge_key: str) -> bool:
    """
    Vergibt Badge falls noch nicht vorhanden.
    Gibt True zurück wenn neu vergeben – sendet KEINE Nachricht.
    """
    if badge_key not in BADGES:
        return False
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO user_badges (user_id, badge_key) VALUES (?, ?)",
                (user_id, badge_key)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def badge_line(badge_key: str) -> str:
    """Gibt eine dezente einzeilige Badge-Zeile zurück (Apple-Stil)."""
    if badge_key not in BADGES:
        return ""
    emoji, name, _ = BADGES[badge_key]
    return f"∙ {emoji} {name}"

def send_badge_summary(bot_instance, user_id: int, badge_keys: list):
    """Sendet neue Badges mit Kontext, statt nur nackte Badge-Namen auszugeben."""
    clean_lines = [badge_line(k) for k in badge_keys if badge_line(k)]
    if not clean_lines:
        return
    bot_instance.send_message(
        user_id,
        "Neue Erfolge freigeschaltet:\n\n" + "\n".join(clean_lines),
        parse_mode="Markdown"
    )


def has_badge(user_id: int, badge_key: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM user_badges WHERE user_id = ? AND badge_key = ?",
            (user_id, badge_key)
        )
        return cursor.fetchone() is not None

def check_wealth_badges(user_id: int, u: dict) -> list:
    """
    Prüft alle vermögensbasierten Badges.
    Gibt Liste neu vergebener Badge-Keys zurück (keine Messages).
    """
    new_badges = []
    investments = u.get("current_investments") or 0
    cash = u.get("current_cash") or 0
    total_wealth = investments + cash
    fixed = u.get("fixed_costs") or 0
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    savings_rate = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)

    checks = [
        (investments > 0,                                          "first_investment"),
        (total_wealth >= 1000,                                     "thousand_club"),
        (total_wealth >= 10000,                                    "ten_k_club"),
        (fixed > 0 and cash >= fixed * 3,                          "emergency_fund"),
        (income > 0 and savings_rate / max(income, 1) >= 0.20,    "savings_master"),
    ]
    for condition, key in checks:
        if condition and award_badge(user_id, key):
            new_badges.append(key)
    return new_badges

def check_fastfood_badge(user_id: int) -> bool:
    """Prüft Fast-Food-Fasten Badge. Gibt True zurück wenn neu vergeben."""
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(id) as cnt FROM expenses "
            "WHERE user_id = ? AND category = 'RESTAURANTS' AND DATE(created_at) >= ?",
            (user_id, thirty_ago)
        )
        if cursor.fetchone()["cnt"] == 0:
            return award_badge(user_id, "no_fastfood_30")
    return False

def handle_daily_activity(user_id: int, bot_instance) -> int:
    """
    Max. 1 CP pro Tag. Liest frisch aus DB (Race-Condition-safe).
    Streak-Nachrichten sind subtil und minimal.
    Gibt vergabene CP zurück (0 wenn Tageslimit bereits erreicht).
    """
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_activity_date, streak_days FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return 0
        last_date = row["last_activity_date"] or ""
        streak = row["streak_days"] or 0

    if last_date == today:
        return 0

    streak = streak + 1 if last_date == yesterday else 1
    update_user_field(user_id, "last_activity_date", today)
    update_user_field(user_id, "streak_days", streak)
    new_pts = add_cp(user_id, 1)

    # Streak-Meilensteine – dezent, ohne Exzess
    if streak == 7:
        if award_badge(user_id, "streak_7"):
            new_pts = add_cp(user_id, 10)
            bot_instance.send_message(user_id,
                f"7-Tage Streak · +10 CP · Gesamt: {new_pts} CP\n∙ ⚡ Erste Woche",
                parse_mode="Markdown"
            )
    elif streak == 30:
        if award_badge(user_id, "streak_30"):
            new_pts = add_cp(user_id, 30)
            bot_instance.send_message(user_id,
                f"30-Tage Streak · +30 CP · Gesamt: {new_pts} CP\n∙ 🔥 Eiserner Monat",
                parse_mode="Markdown"
            )
    elif streak > 7 and streak % 7 == 0:
        new_pts = add_cp(user_id, 5)
        bot_instance.send_message(user_id, f"{streak}-Tage Streak · +5 CP")

    return 1

def handle_month_transition(user_id: int, u: dict, bot_instance):
    """
    Erkennt Monatswechsel beim ersten Kontakt im neuen Monat.
    Speichert den Monatsabschluss inklusive Nettovermoegen fuer die Report-Kurve.
    """
    current_month = date.today().strftime("%Y-%m")
    stored_month = u.get("current_month") or ""

    if stored_month == "":
        update_user_field(user_id, "current_month", current_month)
        return
    if stored_month == current_month:
        return

    logger.info(f"Monatswechsel User {user_id}: {stored_month} -> {current_month}")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND strftime('%Y-%m', created_at) = ?",
            (user_id, stored_month)
        )
        old_expenses = cursor.fetchone()[0] or 0.0

    score_data = calculate_clarity_score(u, old_expenses)
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    free_budget = income - (u.get("fixed_costs") or 0)
    budget_ok = free_budget > 0 and old_expenses <= free_budget
    net_worth = (u.get("current_investments") or 0) + (u.get("current_cash") or 0)

    bonus_lines = []
    latest_points = u.get("clarity_points") or 0
    if budget_ok:
        if award_badge(user_id, "month_win"):
            bonus_lines.append("Monats-Sieg freigeschaltet")
        latest_points = add_cp(user_id, 50)
        bonus_lines.append(f"Budget eingehalten - +50 CP - Gesamt: {latest_points} CP")
    else:
        bonus_lines.append("Budget ueberschritten - kein Monats-Bonus.")

    with get_db() as conn:
        try:
            conn.execute(
                """INSERT INTO monthly_snapshots
                   (user_id, month, clarity_score, total_expenses, budget_ok, net_worth)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, month) DO UPDATE SET
                       clarity_score = excluded.clarity_score,
                       total_expenses = excluded.total_expenses,
                       budget_ok = excluded.budget_ok,
                       net_worth = excluded.net_worth""",
                (user_id, stored_month, score_data["total"], old_expenses, int(budget_ok), net_worth)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Snapshot-Fehler User {user_id}: {e}")

    update_user_field(user_id, "current_month", current_month)
    rank_name, rank_emoji, _ = get_rank(latest_points)

    bot_instance.send_message(
        user_id,
        f"*{stored_month} - Monatsabschluss*\n\n"
        f"Clarity Score: *{score_data['total']}/100*\n"
        f"Ausgaben: {old_expenses:.2f} EUR\n"
        f"Nettovermoegen: {net_worth:.2f} EUR\n"
        f"{chr(10).join(bonus_lines)}\n\n"
        f"{rank_emoji} {rank_name}",
        parse_mode="Markdown"
    )

# ====================== BOT INIT ======================
bot = telebot.TeleBot(TOKEN)
user_last_message: dict = {}

def setup_bot_menu():
    commands = [
        telebot.types.BotCommand("start",      "🚀 Start & Profil anlegen"),
        telebot.types.BotCommand("status",     "📊 Restbudget checken"),
        telebot.types.BotCommand("stats",      "📈 Ausgaben nach Kategorien"),
        telebot.types.BotCommand("score",      "🌟 Clarity Score & Rang"),
        telebot.types.BotCommand("scoreinfo",  "Clarity Score erklaert"),
        telebot.types.BotCommand("badges",     "🏆 Errungenschaften"),
        telebot.types.BotCommand("goal",       "🎯 Sparziel & Prognose"),
        telebot.types.BotCommand("investiert", "💰 Sparrate bestätigen (+20 CP)"),
        telebot.types.BotCommand("verfeinern", "⚙️ Profil verfeinern"),
        telebot.types.BotCommand("undo",       "↩️ Letzte Ausgabe löschen"),
        telebot.types.BotCommand("reset",      "🗑️ Alle Daten löschen"),
    ]
    bot.set_my_commands(commands)
    logger.info("✅ Telegram Menü eingerichtet.")

# ====================== CALLBACK HANDLER (Inline Buttons) ======================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.message.chat.id
    data = call.data

    if data == "confirm_reset":
        with get_db() as conn:
            for table in ["expenses", "user_badges", "monthly_snapshots", "report_jobs", "users"]:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
            conn.commit()
        bot.edit_message_text(
            "Alle Daten gelöscht. /start für Neuanfang.",
            uid, call.message.message_id
        )
        logger.info(f"User {uid} hat alle Daten gelöscht.")

    elif data == "cancel_reset":
        bot.edit_message_text("Abgebrochen.", uid, call.message.message_id)

    elif data == "start_refine":
        update_user_field(uid, "onboarding_step", STEP_ADAPT_HOUSING)
        try:
            bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(uid,
            "⚙️ *Profil verfeinern – Teil 1: Wohnen*\n\nMiete, Strom, Gas?\n_(z.B. 800 60 40)_",
            parse_mode="Markdown"
        )

    elif data == "skip_refine":
        bot.edit_message_text(
            "Kein Problem. Du kannst dein Profil jederzeit mit /verfeinern anpassen.",
            uid, call.message.message_id
        )

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

# ====================== COMMAND HANDLER ======================
@bot.message_handler(commands=[
    'start', 'help', 'score', 'scoreinfo', 'badges', 'verfeinern', 'undo', 'id',
    'settings', 'goal', 'status', 'stats', 'reset', 'reset_confirm', 'investiert', 'testreport'
])
def handle_commands(message):
    uid = message.chat.id
    cmd = message.text.split()[0].lower()
    u = get_or_create_user(uid)

    if (u.get("onboarding_step") or 0) >= STEP_NORMAL:
        handle_month_transition(uid, u, bot)

    if cmd == '/start':
        update_user_field(uid, "onboarding_step", STEP_INCOME)
        bot.send_message(
            uid,
            "👋 Willkommen bei *Clarity*.\n\n"
            "📝 *Schritt 1 von 9:* Wie hoch ist dein monatliches Nettoeinkommen?\n_(z.B. 2500)_\n\n"
            "_Mit 'zurueck' gehst du einen Schritt zurueck._",
            parse_mode="Markdown"
       )

    elif cmd == '/help':
        bot.send_message(uid,
            "🤖 *Clarity – Befehle:*\n\n"
            "💬 Ausgaben: `Lidl 34€` · `Döner 8€` · `Kino 15` · `Tanken 60`\n\n"
            "/status – Restbudget\n"
            "/stats – Ausgaben nach Kategorien\n"
            "/scoreinfo – Score verstehen\n"
            "/score – Clarity Score & Rang\n"
            "/badges – Errungenschaften\n"
            "/goal – Sparziel & Prognose\n"
            "/investiert – Sparrate bestätigen (+20 CP)\n"
            "/verfeinern – Profil verfeinern\n"
            "/undo – Letzte Ausgabe löschen\n"
            "/settings – Profil neu einrichten",
            parse_mode="Markdown"
        )

    elif cmd == '/id':
        actor_id = get_actor_id(message)
        bot.send_message(uid, f"Deine Telegram-ID: {actor_id}\nChat-ID: {uid}")

    elif cmd == '/score':
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(amount) FROM expenses WHERE user_id = ? "
                "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')",
                (uid,)
            )
            total_exp = cursor.fetchone()[0] or 0.0


        score_data = calculate_clarity_score(u, total_exp)
        cp = u.get("clarity_points") or 0
        rank_name, rank_emoji, pts_needed = get_rank(cp)
        rank_line = (f"Noch *{pts_needed} CP* bis zum nächsten Rang."
                     if pts_needed > 0 else "Höchster Rang erreicht.")


        bot.send_message(
            uid,
            f"📊 *Finanz-Gesundheit: {score_data['total']}/100*\n"
            f"├ Budgetkontrolle:  {score_data['budget']}/25\n"
            f"├ Sparquote:        {score_data['savings']}/25\n"
            f"├ Konsistenz:       {score_data['consistency']}/25\n"
            f"└ Spareffizienz:    {score_data['efficiency']}/25\n\n"
            f"{rank_emoji} *{rank_name}* · {cp} CP\n"
            f"{rank_line}",
            f"\n\nMehr Kontext: /scoreinfo",
            parse_mode="Markdown"
        )

    elif cmd == '/scoreinfo':
        bot.send_message(
            uid,
            "Der *Clarity Score* zeigt dir, wie gesund dein Geldsystem gerade aufgestellt ist.\n\n"
            "*Die 4 Bereiche:*\n"
            "1. Budgetkontrolle - Wie gut du dein freies Monatsbudget im Griff hast.\n"
            "2. Sparquote - Wie viel du fuer Vermoegensaufbau zuruecklegst.\n"
            "3. Konsistenz - Wie regelmaessig du deine Ausgaben trackst.\n"
            "4. Spareffizienz - Wie sinnvoll dein uebriges Geld fuer deine Ziele arbeitet.\n\n"
            "*Wichtig:* Der Score ist kein Urteil ueber dich. Er ist ein Kompass, der dir zeigt, wo dein groesster Hebel liegt.",
            parse_mode="Markdown"
        )

    elif cmd == '/badges':
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT badge_key, earned_at FROM user_badges "
                "WHERE user_id = ? AND badge_key NOT LIKE 'inv_%' ORDER BY earned_at",
                (uid,)
            )
            earned = cursor.fetchall()

        if not earned:
            bot.send_message(uid,
                "Noch keine Errungenschaften.\n\nTracke täglich um dein erstes zu verdienen."
            )
            return

        text = "*Errungenschaften:*\n\n"
        for row in earned:
            key = row["badge_key"]
            if key in BADGES:
                emoji, name, _ = BADGES[key]
                earned_date = row["earned_at"][:10]
                text += f"{emoji} {name} · _{earned_date}_\n"
        bot.send_message(uid, text, parse_mode="Markdown")

    elif cmd == '/investiert':
        month_key = f"inv_{date.today().strftime('%Y_%m')}"
        if has_badge(uid, month_key):
            bot.send_message(uid, f"Investment-Bonus fuer {date.today().strftime('%B %Y')} bereits vergeben.")
            return

        with get_db() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO user_badges (user_id, badge_key) VALUES (?, ?)",
                    (uid, month_key)
                )
                conn.commit()
            except sqlite3.IntegrityError:
                bot.send_message(uid, "Bonus fuer diesen Monat bereits vergeben.")
                return

        etf_savings = u.get("etf_savings") or 0
        cash_savings = u.get("cash_savings") or 0
        new_investments = (u.get("current_investments") or 0) + etf_savings
        new_cash = (u.get("current_cash") or 0) + cash_savings
        update_user_field(uid, "current_investments", new_investments)
        update_user_field(uid, "current_cash", new_cash)

        new_pts = add_cp(uid, 20)
        u_fresh = get_or_create_user(uid)
        new_badges = check_wealth_badges(uid, u_fresh)
        total_wealth = new_investments + new_cash

        bot.send_message(
            uid,
            f"Sparrate bestaetigt - *+20 CP* - Gesamt: {new_pts} CP\n"
            f"ETF/Investments: +{etf_savings:.2f} EUR\n"
            f"Cash: +{cash_savings:.2f} EUR\n"
            f"Nettovermoegen: *{total_wealth:.2f} EUR*",
            parse_mode="Markdown"
        )
        send_badge_summary(bot, uid, new_badges)

    elif cmd == '/goal':
        if not u.get('goal_description'):
            bot.send_message(uid, "Noch kein Sparziel. Tippe /settings.")
            return
        sparrate = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
        prognose = calculate_time_to_goal(
            u.get("goal_amount") or 0, u.get("etf_savings") or 0,
            u.get("cash_savings") or 0, u.get("current_investments") or 0,
            u.get("current_cash") or 0
        )
        bot.send_message(uid,
            f"🎯 *{u['goal_description']}*\n\n"
            f"Zielbetrag: {u.get('goal_amount', 0):.2f}€\n"
            f"Sparrate: {sparrate:.2f}€/Monat\n"
            f"Prognose: *{prognose}*\n\n"
            f"_(7% ETF · 2% Tagesgeld)_",
            parse_mode="Markdown"
        )

    elif cmd == '/status':
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(amount) FROM expenses WHERE user_id = ? "
                "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')",
                (uid,)
            )
            total_exp = cursor.fetchone()[0] or 0.0

        income = (u.get("income") or 0) + (u.get("other_income") or 0)
        remaining = income - (u.get("fixed_costs") or 0) - total_exp
        e = "🟢" if remaining > 200 else ("🟡" if remaining > 0 else "🔴")

        bot.send_message(uid,
            f"📊 *Monatsstatus*\n\n"
            f"Einnahmen: {income:.2f}€\n"
            f"Fixkosten: {u.get('fixed_costs', 0):.2f}€\n"
            f"Ausgaben: {total_exp:.2f}€\n"
            f"{'─' * 20}\n"
            f"{e} *Restbudget: {remaining:.2f}€*",
            parse_mode="Markdown"
        )

    elif cmd == '/stats':
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT category, SUM(amount), COUNT(id) FROM expenses "
                "WHERE user_id = ? "
                "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime') "
                "GROUP BY category ORDER BY SUM(amount) DESC",
                (uid,)
            )
            rows = cursor.fetchall()

        if not rows:
            bot.send_message(uid, "Noch keine Ausgaben diesen Monat.")
            return

        text = "*Ausgaben nach Kategorien:*\n\n"
        for row in rows:
            e = CATEGORY_EMOJIS.get(row["category"], "🔸")
            text += f"{e} {row['category']}: {row[1]:.2f}€ ({row[2]}x)\n"
        bot.send_message(uid, text, parse_mode="Markdown")

    elif cmd == '/testreport':
        actor_id = get_actor_id(message)
        if not ADMIN_USER_IDS or actor_id not in ADMIN_USER_IDS:
            bot.send_message(uid, "Dieser Befehl ist nur fuer Admins freigeschaltet. Sende /id und trage deine Telegram-ID in ADMIN_USER_ID ein.")
            return

        parts = message.text.split(maxsplit=1)
        report_month = parts[1].strip() if len(parts) > 1 else date.today().strftime("%Y-%m")
        if not re.match(r"^\d{4}-\d{2}$", report_month):
            bot.send_message(uid, "Bitte nutze das Format YYYY-MM, z.B. /testreport 2026-06")
            return

        bot.send_message(uid, f"Testreport fuer {report_month} wird vorbereitet...")

        try:
            import report_engine
        except Exception as e:
            logger.error(f"report_engine Import fehlgeschlagen: {e}", exc_info=True)
            bot.send_message(uid, "report_engine.py konnte nicht geladen werden. Die Datei muss im gleichen Ordner wie der Bot liegen.")
            return

        old_min_days = getattr(report_engine, "MIN_TRACKING_DAYS", 14)
        try:
            report_engine.ensure_net_worth_column()
            report_engine.MIN_TRACKING_DAYS = 0
            ok = report_engine.send_report_to_user(uid, report_month, bot)
            if ok:
                bot.send_message(uid, "Testreport abgeschlossen.")
            else:
                bot.send_message(uid, "Testreport konnte nicht generiert werden. Bitte pruefe die Logs.")
        except Exception as e:
            logger.error(f"Testreport-Fehler User {uid}: {e}", exc_info=True)
            bot.send_message(uid, f"Testreport fehlgeschlagen: {type(e).__name__}")
        finally:
            report_engine.MIN_TRACKING_DAYS = old_min_days

    elif cmd == '/verfeinern':
        update_user_field(uid, "onboarding_step", STEP_ADAPT_HOUSING)
        bot.send_message(
            uid,
            "⚙️ *Profil verfeinern – Teil 1: Wohnen*\n\nMiete, Strom, Gas?\n_(z.B. 800 60 40)_",
            parse_mode="Markdown"
        )

    elif cmd == '/settings':
        update_user_field(uid, "onboarding_step", STEP_INCOME)
        bot.send_message(
            uid,
            "*Schritt 1 von 9:* Nettoeinkommen?\n\n_Mit 'zurueck' gehst du einen Schritt zurueck._",
            parse_mode="Markdown"
        )

    elif cmd == '/undo':
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, amount, merchant FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (uid,)
            )
            last = cursor.fetchone()
            if not last:
                bot.send_message(uid, "Nichts zum Löschen.")
                return
            cursor.execute("DELETE FROM expenses WHERE id = ?", (last["id"],))
            conn.commit()
        bot.send_message(uid, f"↩️ {last['amount']:.2f}€ bei {last['merchant']} gelöscht.")

    elif cmd == '/reset':
        # FIX: Echter tippbarer Button statt Text-Bestätigung
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton("Ja, alles löschen", callback_data="confirm_reset"),
            telebot.types.InlineKeyboardButton("Abbrechen", callback_data="cancel_reset")
        )
        bot.send_message(uid,
            "⚠️ Alle Daten werden unwiderruflich gelöscht.",
            reply_markup=markup
        )

    elif cmd == '/reset_confirm':
        # Fallback für direkte Text-Eingabe
        with get_db() as conn:
            for table in ["expenses", "user_badges", "monthly_snapshots", "report_jobs", "users"]:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
            conn.commit()
        bot.send_message(uid, "Alle Daten gelöscht. /start für Neuanfang.")

# ====================== MAIN MESSAGE HANDLER ======================
@bot.message_handler(func=lambda message: True)
def handle_msg(message):
    uid = message.chat.id
    if message.text is None:
        return

    now = time.time()
    if now - user_last_message.get(uid, 0) < 1.0:
        return
    user_last_message[uid] = now

    text_input = message.text.strip()
    text_lower = text_input.lower()

    u = get_or_create_user(uid)
    step = u.get("onboarding_step") or STEP_START

    if step >= STEP_NORMAL:
        handle_month_transition(uid, u, bot)

        weekly_reply = maybe_answer_weekly_budget(uid, u, text_lower)
        if weekly_reply:
            bot.send_message(uid, weekly_reply, parse_mode="Markdown")
            return

        category_reply = maybe_answer_category_spending(uid, text_lower)
        if category_reply:
            bot.send_message(uid, category_reply, parse_mode="Markdown")
            return

        if text_lower in {"zurueck", "zurück"}:
            if 0 < step < STEP_NORMAL:
                prev_step = ONBOARDING_BACK_STEPS.get(step)
                if prev_step is None:
                    bot.send_message(uid, "Du bist bereits beim ersten Onboarding-Schritt.")
                    return
                update_user_field(uid, "onboarding_step", prev_step)
                bot.send_message(uid, ONBOARDING_BACK_MESSAGES[prev_step])
                return

            if STEP_ADAPT_HOUSING <= step <= STEP_ADAPT_CREDITS:
                prev_step = REFINE_BACK_STEPS.get(step)
                if prev_step is None:
                    bot.send_message(uid, "Du bist bereits beim ersten Verfeinern-Schritt.")
                    return
                update_user_field(uid, "onboarding_step", prev_step)
                bot.send_message(uid, REFINE_BACK_MESSAGES[prev_step])
                return

    # ─── ONBOARDING ─────────────────────────────────────────────────────
    if 0 < step < STEP_NORMAL:
        # Schritt 4: Ziel in Worten – bleibt Text (kein parse_currency)
        if step == STEP_GOAL_DESCRIPTION:
            if len(text_input) >= 2:
                update_user_field(uid, "goal_description", text_input)
                update_user_field(uid, "onboarding_step", STEP_GOAL_AMOUNT)
                bot.send_message(uid,
                    f"🎯 Ziel: *{text_input}*\n\n✅ *Schritt 5 von 9:* Welchen Betrag brauchst du?",
                    parse_mode="Markdown"
                )
            else:
                bot.send_message(uid, "Bitte beschreibe dein Ziel etwas genauer.")
            return

        val = parse_currency(text_input)
        if val is None:
            bot.send_message(uid, "⚠️ Keine gültige Zahl. _(z.B. 2500)_", parse_mode="Markdown")
            return

        # Schritt-Map: step → (field, next_step, message)
        steps = {
            STEP_INCOME:              ("income",              STEP_OTHER_INCOME,        "✅ *Schritt 2 von 9:* Weitere Einkommen? _(Falls keine, 0)_"),
            STEP_OTHER_INCOME:        ("other_income",        STEP_FIXED_COSTS,         "✅ *Schritt 3 von 9:* Fixkosten gesamt?"),
            STEP_FIXED_COSTS:         ("fixed_costs",         STEP_GOAL_DESCRIPTION,    "✅ *Schritt 4 von 9:* Dein Sparziel in Worten?"),
            STEP_GOAL_AMOUNT:         ("goal_amount",         STEP_CURRENT_INVESTMENTS, "✅ *Schritt 6 von 9:* Aktuell investiertes Vermögen? _(ETF/Aktien, 0 falls keines)_"),
            STEP_CURRENT_INVESTMENTS: ("current_investments", STEP_CURRENT_CASH,        "✅ *Schritt 7 von 9:* Cash-Reserven? _(Tagesgeld/Giro)_"),
            STEP_CURRENT_CASH:        ("current_cash",        STEP_ETF_SAVINGS,         "✅ *Schritt 8 von 9:* Monatliche ETF-Sparrate?"),
            STEP_ETF_SAVINGS:         ("etf_savings",         STEP_CASH_SAVINGS,        "✅ *Schritt 9 von 9:* Monatliche Cash-Sparrate?"),
        }

        if step in steps:
            field, next_step, msg = steps[step]
            update_user_field(uid, field, val)
            update_user_field(uid, "onboarding_step", next_step)
            bot.send_message(uid, msg, parse_mode="Markdown")

        elif step == STEP_CASH_SAVINGS:
            update_user_field(uid, "cash_savings", val)
            update_user_field(uid, "onboarding_step", STEP_NORMAL)
            update_user_field(uid, "current_month", date.today().strftime("%Y-%m"))
            u_fresh = get_or_create_user(uid)
            new_badges = check_wealth_badges(uid, u_fresh)
            sparrate = (u_fresh.get("etf_savings") or 0) + val

            # Post-Onboarding: Profil verfeinern anbieten
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton("Profil verfeinern", callback_data="start_refine"),
                telebot.types.InlineKeyboardButton("Später", callback_data="skip_refine")
            )
            bot.send_message(uid,
                f"🎉 *Einrichtung abgeschlossen!*\n\n"
                f"Sparrate: {sparrate:.2f}€/Monat\n\n"
                f"Verfeinere dein Profil für maßgeschneiderte Clarity Reports.",
                parse_mode="Markdown",
                reply_markup=markup
            )
            # Badges dezent als separate Zeilen falls vorhanden
            if new_badges:
                send_badge_summary(bot, uid, new_badges)
        return

    # ─── VERFEINERN-FLOW ────────────────────────────────────────────────
    if STEP_ADAPT_HOUSING <= step <= STEP_ADAPT_CREDITS:
        nums = [float(x.replace(',', '.')) for x in re.findall(r'\d+(?:[.,]\d+)?', text_input)]
        if not nums:
            bot.send_message(uid, "Bitte gib die Zahlen ein _(z.B. 800 60 40)_", parse_mode="Markdown")
            return

        details = u.get("details", {})

        if step == STEP_ADAPT_HOUSING:
            details["wohnen"] = ({"miete": nums[0], "strom": nums[1], "gas": nums[2]}
                                 if len(nums) >= 3 else {"gesamt": sum(nums)})
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_MOBILITY)
            bot.send_message(uid, "🚗 *Teil 2: Mobilität*\nAuto, Versicherung, Bahn?\n_(z.B. 200 80 49)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_MOBILITY:
            details["mobilitaet"] = ({"auto": nums[0], "vers": nums[1], "bahn": nums[2]}
                                     if len(nums) >= 3 else {"gesamt": sum(nums)})
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_ABOS)
            bot.send_message(uid, "📺 *Teil 3: Abos*\nNetflix, Spotify, Prime, Disney?\n_(z.B. 14 10 9 8)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_ABOS:
            details["abos"] = ({"netflix": nums[0], "spotify": nums[1], "prime": nums[2], "disney": nums[3]}
                               if len(nums) >= 4 else {"gesamt": sum(nums)})
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_INSURANCE)
            bot.send_message(uid, "🛡️ *Teil 4: Versicherungen*\nHaftpflicht, BU, Rechtsschutz?\n_(z.B. 6 45 25)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_INSURANCE:
            details["versicherungen"] = ({"haftpflicht": nums[0], "bu": nums[1], "rechtsschutz": nums[2]}
                                         if len(nums) >= 3 else {"gesamt": sum(nums)})
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_CREDITS)
            bot.send_message(uid, "💳 *Teil 5: Kredite*\nImmobilie, Auto, Konsum?\n_(Falls keine → 0)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_CREDITS:
            details["kredite"] = ({"immo": nums[0], "konsum": nums[1]}
                                  if len(nums) >= 2 else {"gesamt": sum(nums)})
            total_fixed = sum(
                float(v) for cat in details.values()
                if isinstance(cat, dict) for v in cat.values()
            )
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "fixed_costs", total_fixed)
            update_user_field(uid, "onboarding_step", STEP_NORMAL)
            bot.send_message(uid,
                f"✅ Profil verfeinert · Fixkosten: *{total_fixed:.2f}€*",
                parse_mode="Markdown"
            )
            u_fresh = get_or_create_user(uid)
            new_badges = check_wealth_badges(uid, u_fresh)
            if new_badges:
                send_badge_summary(bot, uid, new_badges)
        return

    # ─── HYBRID-TRACKER ──────────────────────────────────────────────────
    numbers = re.findall(r'\b\d+(?:[.,]\d{1,2})?\b', text_lower)
    # FIX: Besserer Jahresfilter – schließt 4-stellige Zahlen (1900–2099) aus
    expense_nums = [
        n for n in numbers
        if not re.match(r'^(19|20)\d{2}$', n.replace(',', '.').split('.')[0])
    ]

    if len(expense_nums) == 1:
        amount_val = float(expense_nums[0].replace(',', '.'))
        merchant_found = None
        category_found = None

        # Layer 1: Bekannte Händler
        for m, keys in MERCHANT_KEYWORDS.items():
            if any(k in text_lower for k in keys):
                merchant_found = m
                category_found = CATEGORY_MAPPING.get(m, "SONSTIGES")
                break

        # Layer 2: Kategorie-Keywords (Döner, Kino, Tanken etc.)
        if not merchant_found:
            for cat, keywords in CATEGORY_KEYWORDS.items():
                if any(k in text_lower for k in keywords):
                    category_found = cat
                    merchant_found = extract_merchant_name(text_input)
                    break

        if merchant_found and category_found:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO expenses (user_id, amount, category, merchant, description) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uid, amount_val, category_found, merchant_found, "Via Hybrid")
                )
                conn.commit()

            cp_earned = handle_daily_activity(uid, bot)

            # Badge-Checks – dezent inline, kein Extra-Message
            new_badge_lines = []
            if check_fastfood_badge(uid):
                new_badge_lines.append(badge_line("no_fastfood_30"))

            emoji = CATEGORY_EMOJIS.get(category_found, "🔸")
            cp_str = "+1 CP" if cp_earned > 0 else "Tageslimit"
            msg = f"✅ *{amount_val:.2f}€ · {merchant_found}*\n{emoji} {category_found} · {cp_str}"
            if new_badge_lines:
                msg += "\n" + "\n".join(new_badge_lines)
            bot.send_message(uid, msg, parse_mode="Markdown")
            return

    # ─── PROGNOSE (Ohne KI) ──────────────────────────────────────────────
    if any(w in text_lower for w in ["wie lange", "wann erreiche", "dauer", "prognose"]):
        sparrate = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
        if sparrate <= 0:
            bot.send_message(uid, "Sparrate ist 0€. Bitte unter /settings einrichten.")
        else:
            erg = calculate_time_to_goal(
                u.get("goal_amount") or 0, u.get("etf_savings") or 0,
                u.get("cash_savings") or 0, u.get("current_investments") or 0,
                u.get("current_cash") or 0
            )
            bot.send_message(uid,
                f"🎯 *{u.get('goal_description', 'Dein Ziel')}*\n\n"
                f"Sparrate: {sparrate:.2f}€/Monat\n"
                f"Prognose: *{erg}*",
                parse_mode="Markdown"
            )
        return

    # ─── BUDGET-CHECK (Ohne KI) ──────────────────────────────────────────
    # FIX: "rest" entfernt → fängt nicht mehr "restaurant" ab
    if any(w in text_lower for w in ["restbudget", "budget", "übrig", "wieviel habe", "wie viel habe"]):
        handle_daily_activity(uid, bot)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(amount) FROM expenses WHERE user_id = ? "
                "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')",
                (uid,)
            )
            total_exp = cursor.fetchone()[0] or 0.0

        frei = ((u.get("income") or 0) + (u.get("other_income") or 0)
                - (u.get("fixed_costs") or 0) - total_exp)
        e = "🟢" if frei > 200 else ("🟡" if frei > 0 else "🔴")
        bot.send_message(uid,
            f"💸 *Budget-Check*\n\n{e} Noch *{frei:.2f}€* zur freien Verfügung.",
            parse_mode="Markdown"
        )
        return

    if is_off_topic_request(text_lower):
        bot.send_message(
            uid,
            "Ich bleibe bei Clarity: Ausgaben, Budget, Sparziele, Score und Reports. "
            "Dabei helfe ich dir gern konkret weiter."
        )
        return

    # ─── KI-FALLBACK (Nur für komplexe/unbekannte Anfragen) ─────────────
    bot.send_chat_action(uid, 'typing')
    prompt = f"""Du bist Clarity, ein Finanz-Assistent. Antworte auf Deutsch.
Datum: {date.today().isoformat()}
Kategorien: LEBENSMITTEL, MOBILITAET, RESTAURANTS, ABOS, FREIZEIT, SHOPPING, VERSICHERUNG, MIETE, GESUNDHEIT, DROGERIE, PFLEGE, SONSTIGES

Bleibe strikt bei persoenlichen Finanzen, Ausgaben, Budget, Sparzielen und Reports. Bei Off-Topic-Fragen keine langen Antworten schreiben.

Antwortformat (reines JSON):
{{
  "expenses": [{{"amount": 12.5, "category": "LEBENSMITTEL", "merchant": "Rewe"}}],
  "reply_text": "Kurze Antwort auf Deutsch"
}}

Nutzereingabe: {text_input}"""

    try:
        res = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=350
        )
        data = json.loads(res.choices[0].message.content.strip())

        booked = 0
        booked_items = []
        for exp in data.get("expenses", []):
            try:
                amt = float(exp.get("amount", 0))
                if amt > 0:
                    category = exp.get("category", "SONSTIGES") or "SONSTIGES"
                    merchant = exp.get("merchant", "Unbekannt") or "Unbekannt"
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO expenses (user_id, amount, category, merchant, description) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (uid, amt, category, merchant, "Via KI")
                        )
                        conn.commit()
                    booked += 1
                    booked_items.append({"amount": amt, "category": category, "merchant": merchant})
            except (ValueError, TypeError) as e:
                logger.warning(f"KI-Ausgabe konnte nicht verbucht werden: {e}")

        reply = ""
        if booked > 0:
            cp_earned = handle_daily_activity(uid, bot)
            cp_str = "+1 CP" if cp_earned > 0 else "Tageslimit"
            reply += format_expense_confirmation(booked_items, cp_str) + "\n"
            if check_fastfood_badge(uid):
                reply += badge_line("no_fastfood_30") + "\n"

        if data.get("reply_text") and booked == 0:
            reply += data["reply_text"]

        if not reply.strip():
            reply = "Das habe ich nicht verstanden. Bitte nochmal versuchen."

        bot.send_message(uid, reply.strip(), parse_mode="Markdown")

    except json.JSONDecodeError as e:
        logger.error(f"KI JSON-Fehler User {uid}: {e}")
        bot.send_message(uid, "Unerwartete Antwort. Bitte nochmal versuchen.")
    except openai.RateLimitError:
        bot.send_message(uid, "Kurz warten – bitte in 10 Sekunden nochmal versuchen.")
    except Exception as e:
        logger.error(f"KI-Fehler User {uid}: {e}", exc_info=True)
        bot.send_message(uid, "Das habe ich nicht verstanden. Bitte nochmal versuchen.")


# ====================== REPORT QUEUE ======================
def previous_month_key(today: date = None) -> str:
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def get_active_user_ids() -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE onboarding_step = ?", (STEP_NORMAL,))
        return [row["user_id"] for row in cursor.fetchall()]


def random_report_time_for_today() -> str:
    now = datetime.now()
    start = datetime(now.year, now.month, now.day, REPORT_SEND_WINDOW_START_HOUR, 0, 0)
    end = datetime(now.year, now.month, now.day, REPORT_SEND_WINDOW_END_HOUR, 0, 0)
    if now > start:
        start = now + timedelta(minutes=1)
    if start >= end:
        end = start + timedelta(hours=6)
    seconds = max(1, int((end - start).total_seconds()))
    scheduled = start + timedelta(seconds=random.randint(0, seconds))
    return scheduled.strftime("%Y-%m-%d %H:%M:%S")


def create_monthly_report_jobs(report_month: str = None) -> int:
    report_month = report_month or previous_month_key()
    users = get_active_user_ids()
    created = 0
    with get_db() as conn:
        cursor = conn.cursor()
        for user_id in users:
            cursor.execute(
                """INSERT OR IGNORE INTO report_jobs
                   (user_id, report_month, scheduled_at, status, attempts, last_error)
                   VALUES (?, ?, ?, 'pending', 0, '')""",
                (user_id, report_month, random_report_time_for_today())
            )
            created += cursor.rowcount
        conn.commit()
    logger.info(f"Report-Jobs fuer {report_month}: {created} neu, {len(users) - created} bereits vorhanden.")
    return created


def claim_due_report_jobs(limit: int = None) -> list:
    limit = limit or REPORT_WORKER_BATCH_SIZE
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cursor = conn.cursor()
        conn.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """SELECT * FROM report_jobs
               WHERE status = 'pending' AND scheduled_at <= ? AND attempts < ?
               ORDER BY scheduled_at ASC LIMIT ?""",
            (now, REPORT_MAX_ATTEMPTS, limit)
        )
        jobs = [dict(row) for row in cursor.fetchall()]
        for job in jobs:
            cursor.execute(
                """UPDATE report_jobs
                   SET status = 'processing', attempts = attempts + 1, updated_at = ?
                   WHERE id = ?""",
                (now, job["id"])
            )
            job["attempts"] = (job.get("attempts") or 0) + 1
        conn.commit()
    return jobs


def mark_report_job_sent(job_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute("UPDATE report_jobs SET status = 'sent', last_error = '', updated_at = ? WHERE id = ?", (now, job_id))
        conn.commit()


def mark_report_job_failed(job: dict, error: str):
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    attempts = job.get("attempts") or 0
    final_failed = attempts >= REPORT_MAX_ATTEMPTS
    next_status = "failed" if final_failed else "pending"
    next_time = now if final_failed else (now_dt + timedelta(minutes=REPORT_RETRY_DELAY_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    clean_error = (error or "Unbekannter Fehler")[:1000]
    with get_db() as conn:
        conn.execute(
            """UPDATE report_jobs
               SET status = ?, scheduled_at = ?, last_error = ?, updated_at = ?
               WHERE id = ?""",
            (next_status, next_time, clean_error, now, job["id"])
        )
        conn.commit()


def process_report_job(job: dict):
    try:
        import report_engine
        report_engine.ensure_net_worth_column()
        ok = report_engine.send_report_to_user(job["user_id"], job["report_month"], bot)
        if ok:
            mark_report_job_sent(job["id"])
        else:
            mark_report_job_failed(job, "send_report_to_user returned False")
    except Exception as e:
        logger.error(f"Report-Job-Fehler {job.get('id')}: {e}", exc_info=True)
        mark_report_job_failed(job, f"{type(e).__name__}: {e}")


def process_due_report_jobs():
    jobs = claim_due_report_jobs(REPORT_WORKER_BATCH_SIZE)
    if not jobs:
        return
    logger.info(f"Report-Worker verarbeitet {len(jobs)} Job(s).")
    for job in jobs:
        process_report_job(job)

# ====================== REPORT SCHEDULER ======================
REPORT_SCHEDULER = None

def setup_monthly_report_scheduler():
    """Startet Queue-Erzeugung und Worker fuer Monatsreports."""
    if BackgroundScheduler is None or CronTrigger is None:
        logger.warning("APScheduler ist nicht installiert. Monatliche Reports werden nicht automatisch versendet.")
        return None

    scheduler = BackgroundScheduler(timezone="Europe/Berlin")
    scheduler.add_job(
        create_monthly_report_jobs,
        trigger=CronTrigger(day=1, hour=7, minute=55),
        id="create_monthly_report_jobs",
        replace_existing=True,
        misfire_grace_time=REPORT_CREATION_MISFIRE_GRACE_SECONDS,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        process_due_report_jobs,
        trigger="interval",
        seconds=REPORT_WORKER_INTERVAL_SECONDS,
        id="process_due_report_jobs",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info(
        "Report-Queue aktiv: Jobs am 1. um 07:55, Versandfenster "
        f"{REPORT_SEND_WINDOW_START_HOUR}:00-{REPORT_SEND_WINDOW_END_HOUR}:00, "
        f"Worker alle {REPORT_WORKER_INTERVAL_SECONDS}s, Batch {REPORT_WORKER_BATCH_SIZE}."
    )
    return scheduler

# ====================== GRACEFUL SHUTDOWN ======================
def signal_handler(sig, frame):
    logger.info("Bot wird sicher heruntergefahren...")
    if REPORT_SCHEDULER is not None:
        try:
            REPORT_SCHEDULER.shutdown(wait=False)
        except Exception as e:
            logger.warning(f"Scheduler konnte nicht sauber beendet werden: {e}")
    bot.stop_polling()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ====================== START ======================
if __name__ == "__main__":
    init_db()
    setup_bot_menu()
    REPORT_SCHEDULER = setup_monthly_report_scheduler()
    logger.info("🚀 Project Clarity – Pro Edition gestartet")
    bot.delete_webhook(drop_pending_updates=True)
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        logger.error(f"Polling-Fehler: {e}", exc_info=True)

