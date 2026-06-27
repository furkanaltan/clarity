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
import fcntl
from datetime import datetime, date, timedelta
from contextlib import contextmanager
from pathlib import Path
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
USER_APPROVAL_ENABLED = os.getenv("CLARITY_USER_APPROVAL", "1").lower() not in {"0", "false", "no"}

REPORT_SEND_WINDOW_START_HOUR = int(os.getenv("REPORT_SEND_WINDOW_START_HOUR", "8"))
REPORT_SEND_WINDOW_END_HOUR = int(os.getenv("REPORT_SEND_WINDOW_END_HOUR", "14"))
REPORT_WORKER_BATCH_SIZE = int(os.getenv("REPORT_WORKER_BATCH_SIZE", "1"))
REPORT_WORKER_INTERVAL_SECONDS = int(os.getenv("REPORT_WORKER_INTERVAL_SECONDS", "10"))
REPORT_MAX_ATTEMPTS = int(os.getenv("REPORT_MAX_ATTEMPTS", "3"))
REPORT_RETRY_DELAY_MINUTES = int(os.getenv("REPORT_RETRY_DELAY_MINUTES", "15"))
REPORT_CREATION_MISFIRE_GRACE_SECONDS = int(os.getenv("REPORT_CREATION_MISFIRE_GRACE_SECONDS", "21600"))
BOT_LOCK_FILE = os.getenv("CLARITY_BOT_LOCK_FILE", "clarity_bot.lock")

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

RESET_USER_TABLES = [
    "expenses",
    "user_badges",
    "monthly_snapshots",
    "score_history",
    "investment_events",
    "portfolio_snapshots",
    "report_jobs",
    "users",
]

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

DIRECT_CATEGORY_INPUTS = {
    "essen": ("RESTAURANTS", "Essen"),
    "restaurant": ("RESTAURANTS", "Restaurant"),
    "resturant": ("RESTAURANTS", "Restaurant"),
    "restaurante": ("RESTAURANTS", "Restaurant"),
    "restaurants": ("RESTAURANTS", "Restaurant"),
    "lebensmittel": ("LEBENSMITTEL", "Lebensmittel"),
    "einkaufen": ("LEBENSMITTEL", "Einkaufen"),
    "supermarkt": ("LEBENSMITTEL", "Supermarkt"),
    "freizeit": ("FREIZEIT", "Freizeit"),
    "mobilität": ("MOBILITAET", "Mobilität"),
    "mobilitaet": ("MOBILITAET", "Mobilität"),
    "tanken": ("MOBILITAET", "Tanken"),
    "shopping": ("SHOPPING", "Shopping"),
    "pflege": ("PFLEGE", "Pflege"),
    "friseur": ("PFLEGE", "Friseur"),
    "frisör": ("PFLEGE", "Friseur"),
    "gesundheit": ("GESUNDHEIT", "Gesundheit"),
    "drogerie": ("DROGERIE", "Drogerie"),
    "abos": ("ABOS", "Abos"),
    "abo": ("ABOS", "Abo"),
    "fixkosten": ("FIXKOSTEN", "Fixkosten"),
}

INVESTMENT_INPUTS = {
    "etf", "etfs", "investment", "investments", "investieren",
    "sparplan", "depot", "aktien", "aktie", "stock", "stocks",
    "bitcoin", "btc", "ethereum", "eth", "crypto", "krypto",
    "fonds", "fond", "msci", "s&p", "sp500", "s&p500", "nasdaq",
    "world", "anlage", "wertpapier", "wertpapiere",
}

PORTFOLIO_SNAPSHOT_INPUTS = [
    "depotwert", "portfolio wert", "portfoliowert", "investmentstand",
    "investment stand", "depot stand", "depotstand", "mein depot",
    "mein portfolio", "aktueller depotwert", "aktuelles depot",
]

FASTFOOD_KEYWORDS = [
    "mcdonald", "mcdonalds", "mc donalds", "burger king", "burgerking",
    "subway", "kfc", "kentucky", "döner", "doener", "kebab", "imbiss",
    "fastfood", "fast food", "lieferando", "takeaway", "pizza", "pommes",
    "currywurst", "shawarma", "falafel",
]

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
    "GESUNDHEIT": "💊", "SONSTIGES": "📦", "PFLEGE": "💇",
    "FIXKOSTEN": "🏠",
}

# ====================== GAMIFICATION CONSTANTS ======================
RANKS = [
    (0,    "Rookie",           "🥚"),
    (50,   "Stratege",         "🔍"),
    (200,  "Controller",       "📊"),
    (500,  "Investor",         "🧱"),
    (1000, "Manager",          "🏗️"),
    (2500, "Kapitalist",       "🏛️"),
    (5000, "Clarity Elite",    "💎"),
]

SCORE_RANKS = [
    (0, 44, "Rookie", "🥚"),
    (45, 54, "Stratege", "🔍"),
    (55, 64, "Controller", "📊"),
    (65, 74, "Investor", "🧱"),
    (75, 84, "Manager", "🏗️"),
    (85, 92, "Kapitalist", "🏛️"),
    (93, 100, "Clarity Elite", "💎"),
]

BADGES = {
    "streak_7":         ("⚡", "Erste Woche",          "7 Tage in Folge getrackt."),
    "streak_30":        ("🔥", "Eiserner Monat",        "30 Tage Streak. Eiserne Disziplin."),
    "first_investment": ("📈", "Erstes Investment",     "Erstes investiertes Kapital."),
    "thousand_club":    ("💰", "Tausender-Club",        "1.000€ Gesamtvermögen erreicht."),
    "emergency_fund":   ("🛡️", "Notgroschen",           "3 Monate Fixkosten als Reserve aufgebaut."),
    "savings_master":   ("🏆", "Spar-Meister",          "Sparquote über 20%."),
    "ten_k_club":       ("💎", "Fünfstellige Freiheit", "10.000€ Portfolio erreicht."),
    "no_fastfood_30":   ("🥗", "Fast-Food-Pause",       "30 Tage keine Fast-Food-Ausgaben."),
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

        conn.execute('''CREATE TABLE IF NOT EXISTS score_history (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id            INTEGER,
            recorded_date      TEXT,
            clarity_score      INTEGER DEFAULT 0,
            clarity_points     INTEGER DEFAULT 0,
            rank_name          TEXT    DEFAULT '',
            proof_days         INTEGER DEFAULT 0,
            budget_points      INTEGER DEFAULT 0,
            savings_points     INTEGER DEFAULT 0,
            consistency_points INTEGER DEFAULT 0,
            structure_points   INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_score_history_user_date
            ON score_history(user_id, recorded_date)
        ''')

        conn.execute('''CREATE TABLE IF NOT EXISTS investment_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      REAL    NOT NULL,
            direction   TEXT    NOT NULL DEFAULT 'in',
            asset_type  TEXT    NOT NULL DEFAULT 'investment',
            asset_name  TEXT    DEFAULT '',
            event_type  TEXT    NOT NULL DEFAULT 'one_time',
            source      TEXT    NOT NULL DEFAULT 'chat',
            note        TEXT    DEFAULT '',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_investment_events_user_date
            ON investment_events(user_id, created_at)
        ''')

        conn.execute('''CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      REAL    NOT NULL,
            scope       TEXT    NOT NULL DEFAULT 'investments',
            source      TEXT    NOT NULL DEFAULT 'chat',
            note        TEXT    DEFAULT '',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_date
            ON portfolio_snapshots(user_id, created_at)
        ''')

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

        conn.execute('''CREATE TABLE IF NOT EXISTS user_access (
            user_id      INTEGER PRIMARY KEY,
            status       TEXT    NOT NULL DEFAULT 'pending',
            requested_at TEXT    DEFAULT CURRENT_TIMESTAMP,
            approved_at  TEXT    DEFAULT '',
            approved_by  INTEGER DEFAULT 0,
            revoked_at   TEXT    DEFAULT '',
            display_name TEXT    DEFAULT '',
            username     TEXT    DEFAULT '',
            note         TEXT    DEFAULT ''
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_user_access_status
            ON user_access(status, requested_at)
        ''')

        conn.execute('''INSERT OR IGNORE INTO user_access (user_id, status, approved_at, note)
            SELECT user_id, 'approved', CURRENT_TIMESTAMP, 'Bestehender Nutzer vor Freigabesystem'
            FROM users
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


def reset_user_data(user_id: int) -> None:
    """Loescht alle Profildaten, behaelt aber die Freigabe im Testlauf."""
    with get_db() as conn:
        for table in RESET_USER_TABLES:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,)
            ).fetchone()
            if row:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.commit()

def update_user_field(user_id: int, field: str, value) -> bool:
    """Aktualisiert ein Nutzerfeld – Whitelist schützt vor SQL-Injection."""
    if field not in ALLOWED_USER_FIELDS:
        logger.error(f"Unerlaubtes Feld '{field}' – abgebrochen.")
        return False
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
    return True


def save_investment_event(user_id: int, amount: float, direction: str = "in",
                          asset_type: str = "investment", asset_name: str = "",
                          event_type: str = "one_time", source: str = "chat",
                          note: str = "") -> bool:
    if amount <= 0:
        return False
    with get_db() as conn:
        conn.execute(
            """INSERT INTO investment_events
               (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
        )
        conn.commit()
    return True


def replace_onboarding_investment_start(user_id: int, amount: float) -> None:
    with get_db() as conn:
        conn.execute(
            """DELETE FROM investment_events
               WHERE user_id = ?
               AND source = 'onboarding'
               AND event_type = 'manual_adjustment'
               AND note = 'Startwert Investments'""",
            (user_id,)
        )
        if amount > 0:
            conn.execute(
                """INSERT INTO investment_events
                   (user_id, amount, direction, asset_type, asset_name, event_type, source, note)
                   VALUES (?, ?, 'in', 'investment', 'Startwert', 'manual_adjustment', 'onboarding', 'Startwert Investments')""",
                (user_id, amount)
            )
        conn.commit()


def save_portfolio_snapshot(user_id: int, amount: float, scope: str = "investments",
                            source: str = "chat", note: str = "") -> bool:
    if amount < 0:
        return False
    with get_db() as conn:
        conn.execute(
            """INSERT INTO portfolio_snapshots (user_id, amount, scope, source, note)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, amount, scope, source, note)
        )
        conn.commit()
    return True


def replace_onboarding_portfolio_snapshots(user_id: int, investments: float, cash: float) -> None:
    net_worth = max(0.0, investments) + max(0.0, cash)
    with get_db() as conn:
        conn.execute(
            "DELETE FROM portfolio_snapshots WHERE user_id = ? AND source = 'onboarding'",
            (user_id,)
        )
        rows = [
            (user_id, max(0.0, investments), "investments", "onboarding", "Startwert Investments"),
            (user_id, max(0.0, cash), "cash", "onboarding", "Startwert Cash"),
            (user_id, net_worth, "net_worth", "onboarding", "Startwert Nettovermögen"),
        ]
        conn.executemany(
            """INSERT INTO portfolio_snapshots (user_id, amount, scope, source, note)
               VALUES (?, ?, ?, ?, ?)""",
            rows
        )
        conn.commit()


def detect_investment_asset(text_lower: str) -> tuple[str, str]:
    if any(word in text_lower for word in ["bitcoin", "btc"]):
        return "crypto", "Bitcoin"
    if any(word in text_lower for word in ["ethereum", "eth"]):
        return "crypto", "Ethereum"
    if any(word in text_lower for word in ["crypto", "krypto"]):
        return "crypto", "Crypto"
    if any(word in text_lower for word in ["aktie", "aktien", "stock", "stocks"]):
        return "stock", "Aktien"
    if any(word in text_lower for word in ["fonds", "fond"]):
        return "fund", "Fonds"
    if any(word in text_lower for word in ["etf", "msci", "s&p", "sp500", "s&p500", "nasdaq", "world"]):
        return "etf", "ETF"
    if "depot" in text_lower:
        return "investment", "Depot"
    return "investment", "Investment"


def is_portfolio_snapshot_input(text_lower: str) -> bool:
    if any(phrase in text_lower for phrase in PORTFOLIO_SNAPSHOT_INPUTS):
        return True
    if re.search(r"\b(depot|portfolio)\b", text_lower):
        event_words = ["gekauft", "kauf", "investiert", "investiere", "sparplan", "etf", "aktie", "aktien"]
        return not any(word in text_lower for word in event_words)
    return False


ONBOARDING_BACK_STEPS = {
    STEP_INCOME: None,
    STEP_OTHER_INCOME: STEP_INCOME,
    STEP_FIXED_COSTS: STEP_OTHER_INCOME,
    STEP_GOAL_DESCRIPTION: STEP_OTHER_INCOME,
    STEP_GOAL_AMOUNT: STEP_GOAL_DESCRIPTION,
    STEP_CURRENT_INVESTMENTS: STEP_GOAL_AMOUNT,
    STEP_CURRENT_CASH: STEP_CURRENT_INVESTMENTS,
    STEP_ETF_SAVINGS: STEP_CURRENT_CASH,
    STEP_CASH_SAVINGS: STEP_ETF_SAVINGS,
}

ONBOARDING_BACK_MESSAGES = {
    STEP_INCOME: "Schritt 1 von 8: Wie hoch ist dein monatliches Nettoeinkommen?\n(z.B. 2500)",
    STEP_OTHER_INCOME: "Schritt 2 von 8: Weitere Einkommen? (Falls keine, 0)",
    STEP_FIXED_COSTS: "Schritt 3 von 8: Dein Sparziel in Worten?",
    STEP_GOAL_DESCRIPTION: "Schritt 3 von 8: Dein Sparziel in Worten?",
    STEP_GOAL_AMOUNT: "Schritt 4 von 8: Welchen Betrag brauchst du?",
    STEP_CURRENT_INVESTMENTS: "Schritt 5 von 8: Aktuell investiertes Vermögen? (ETF/Aktien, 0 falls keines)",
    STEP_CURRENT_CASH: "Schritt 6 von 8: Cash-Reserven? (Tagesgeld/Giro)",
    STEP_ETF_SAVINGS: "Schritt 7 von 8: Monatliche ETF-Sparrate?",
    STEP_CASH_SAVINGS: "Schritt 8 von 8: Monatliche Cash-Sparrate?",
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
    STEP_ADAPT_MOBILITY: "Teil 2: Mobilität\nAuto, Tanken, Bahn?\n(z.B. 250 120 49)",
    STEP_ADAPT_ABOS: "Teil 3: Abos\nNetflix, Spotify, Prime, Disney?\n(z.B. 14 10 9 8)",
    STEP_ADAPT_INSURANCE: "Teil 4: Versicherungen\nHaftpflicht, BU, Rechtsschutz?\n(z.B. 6 45 25)",
    STEP_ADAPT_CREDITS: "Teil 5: Kredite\nImmobilie, Auto, Konsum?\n(Falls keine → 0)",
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
    token_match = re.search(r"\d+(?:[.,]\d+)?\s*k\b", text.lower())
    if token_match:
        cleaned_k = re.sub(r"[^\d,.-]", "", token_match.group(0)).replace(",", ".")
        try:
            return float(cleaned_k) * 1000
        except ValueError:
            return None

    cleaned = re.sub(r'[^\d,.-]', '', text).replace(',', '.')
    try:
        val = float(cleaned)
        return val if val >= 0 else None
    except ValueError:
        return None


def extract_amounts(text: str, exclude_years: bool = False) -> list[float]:
    amounts = []
    for match in re.finditer(r"\b\d+(?:[.,]\d+)?\s*k\b|\b\d+(?:[.,]\d{1,2})?\b", text.lower()):
        raw = match.group(0).strip()
        multiplier = 1000 if raw.endswith("k") else 1
        cleaned = re.sub(r"[^\d,.-]", "", raw).replace(",", ".")
        if exclude_years and multiplier == 1 and re.match(r"^(19|20)\d{2}$", cleaned.split(".")[0]):
            continue
        try:
            amounts.append(float(cleaned) * multiplier)
        except ValueError:
            continue
    return amounts


def parse_labeled_amounts(text: str, aliases: dict[str, str]) -> dict:
    normalized = text.lower()
    result: dict[str, float] = {}
    alias_pattern = "|".join(
        re.escape(alias)
        for alias in sorted(aliases, key=len, reverse=True)
    )
    if not alias_pattern:
        return result

    tokens = []
    for match in re.finditer(rf"\b(?P<label>{alias_pattern})\b", normalized):
        tokens.append({
            "type": "label",
            "start": match.start(),
            "end": match.end(),
            "key": aliases[match.group("label")],
        })
    for match in re.finditer(r"\d+(?:[.,]\d+)?", normalized):
        tokens.append({
            "type": "amount",
            "start": match.start(),
            "end": match.end(),
            "value": float(match.group(0).replace(",", ".")),
        })

    tokens.sort(key=lambda item: (item["start"], 0 if item["type"] == "label" else 1))
    for index, token in enumerate(tokens):
        if token["type"] != "amount":
            continue

        previous_label = None
        for prev_index in range(index - 1, -1, -1):
            if tokens[prev_index]["type"] == "label":
                previous_label = tokens[prev_index]
                break

        next_label = None
        for next_index in range(index + 1, len(tokens)):
            if tokens[next_index]["type"] == "label":
                next_label = tokens[next_index]
                break

        target = None
        if previous_label and previous_label["key"] not in result:
            target = previous_label
        elif next_label and next_label["key"] not in result:
            target = next_label
        elif previous_label:
            target = previous_label
        elif next_label:
            target = next_label

        if target:
            result[target["key"]] = token["value"]
    return result


def is_back_request(text_lower: str) -> bool:
    normalized = text_lower.strip().lstrip("/")
    return normalized in {"zurueck", "zurück", "back"} or normalized.startswith(("zurueck ", "zurück ", "back "))


def merge_number_defaults(parsed: dict, nums: list, keys: list) -> dict:
    if parsed:
        return parsed

    if len(nums) >= len(keys):
        return {key: nums[index] for index, key in enumerate(keys)}
    return {"gesamt": sum(nums)}


DETAIL_VALUE_ALIASES = {
    "miete": ("wohnen", "miete", "Miete"),
    "kaltmiete": ("wohnen", "miete", "Miete"),
    "warmmiete": ("wohnen", "miete", "Miete"),
    "strom": ("wohnen", "strom", "Strom"),
    "gas": ("wohnen", "gas", "Gas"),
    "heizung": ("wohnen", "gas", "Gas"),
    "hausgeld": ("wohnen", "hausgeld", "Hausgeld"),
    "nebenkosten": ("wohnen", "nebenkosten", "Nebenkosten"),
    "auto": ("mobilitaet", "auto", "Auto"),
    "leasing": ("mobilitaet", "auto", "Auto"),
    "bahn": ("mobilitaet", "bahn", "Bahn"),
    "ticket": ("mobilitaet", "bahn", "Bahn"),
    "tanken": ("mobilitaet", "tanken", "Tanken"),
    "netflix": ("abos", "netflix", "Netflix"),
    "spotify": ("abos", "spotify", "Spotify"),
    "prime": ("abos", "prime", "Prime"),
    "disney": ("abos", "disney", "Disney"),
    "gym": ("abos", "gym", "Gym"),
    "fitness": ("abos", "gym", "Gym"),
    "fitnessstudio": ("abos", "gym", "Fitnessstudio"),
    "fitnesstudio": ("abos", "gym", "Fitnessstudio"),
    "fittnesstudio": ("abos", "gym", "Fitnessstudio"),
    "fintesstudio": ("abos", "gym", "Fitnessstudio"),
    "fitnessabo": ("abos", "gym", "Fitnessstudio"),
    "handy": ("abos", "handy", "Handy"),
    "icloud": ("abos", "icloud", "iCloud"),
    "haftpflicht": ("versicherungen", "haftpflicht", "Haftpflicht"),
    "bu": ("versicherungen", "bu", "Berufsunfähigkeit"),
    "berufsunfähigkeit": ("versicherungen", "bu", "Berufsunfähigkeit"),
    "berufsunfaehigkeit": ("versicherungen", "bu", "Berufsunfähigkeit"),
    "rechtsschutz": ("versicherungen", "rechtsschutz", "Rechtsschutz"),
    "rechtschutz": ("versicherungen", "rechtsschutz", "Rechtsschutz"),
    "hausrat": ("versicherungen", "hausrat", "Hausrat"),
    "autoversicherung": ("versicherungen", "autoversicherung", "Autoversicherung"),
    "auto-versicherung": ("versicherungen", "autoversicherung", "Autoversicherung"),
    "kfzversicherung": ("versicherungen", "autoversicherung", "Autoversicherung"),
    "kfz-versicherung": ("versicherungen", "autoversicherung", "Autoversicherung"),
    "versicherung": ("versicherungen", "sonstige", "Versicherung"),
    "kredit": ("kredite", "kredit", "Kredit"),
    "kredite": ("kredite", "kredit", "Kredit"),
    "darlehen": ("kredite", "kredit", "Kredit"),
    "immobilie": ("kredite", "immobilie", "Immobilie"),
    "immobile": ("kredite", "immobilie", "Immobilie"),
    "immo": ("kredite", "immobilie", "Immobilie"),
    "hausverwalter": ("kredite", "hausverwalter", "Hausverwalter"),
    "verwaltung": ("kredite", "hausverwalter", "Hausverwalter"),
    "konsum": ("kredite", "konsum", "Konsum"),
}


def format_eur(value) -> str:
    return f"{float(value or 0):.2f}€"


def format_detail_summary(values: dict) -> str:
    if not values:
        return "Erkannt: keine Werte."
    parts = []
    for key, value in values.items():
        label = key.replace("_", " ").title()
        parts.append(f"{label}: {format_eur(value)}")
    return "Erkannt: " + ", ".join(parts)


def fixed_costs_total(details: dict) -> float:
    return sum(
        float(value)
        for section in details.values()
        if isinstance(section, dict)
        for value in section.values()
    )


def find_detail_alias_matches(text_lower: str) -> list:
    matches = []
    for alias, (section, key, label) in DETAIL_VALUE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text_lower):
            matches.append((len(alias), section, key, label))
    return matches


def is_profile_removal_request(text_lower: str) -> bool:
    removal_words = [
        "lösche", "loesche", "entferne", "streiche", "raus", "weg",
        "kündige", "kuendige", "gekündigt", "gekuendigt", "beendet",
        "abbezahlt", "abgezahlt", "fertig bezahlt", "läuft aus", "laeuft aus",
    ]
    if any(word in text_lower for word in removal_words):
        return True
    return "nicht mehr" in text_lower and any(
        re.search(rf"\b{re.escape(alias)}\b", text_lower)
        for alias in DETAIL_VALUE_ALIASES
    )


def looks_like_investment_update(text_lower: str) -> bool:
    investment_phrases = [
        "investiertes vermögen", "investiertes vermoegen", "aktuell investiert",
        "aktuelle investments", "meine investments", "mein depot", "depot",
        "portfolio", "cryptowährung", "cryptowaehrung", "kryptowährung",
        "kryptowaehrung", "crypto", "krypto",
    ]
    return any(word in text_lower for word in INVESTMENT_INPUTS) or any(
        phrase in text_lower for phrase in investment_phrases
    )


def maybe_apply_profile_correction(user_id: int, u: dict, text_lower: str) -> str:
    if any(question in text_lower for question in ["wie viel", "wieviel", "wie hoch", "was ist"]):
        return ""
    correction_words = [
        "ändere", "aendere", "änder", "aender", "korrigiere", "korrektur",
        "setze", "setz", "aktualisiere", "update", "füge", "fuege",
        "hinzu", "ergänze", "ergaenze", "nimm auf",
        "lösche", "loesche", "entferne", "streiche", "kündige", "kuendige",
        "gekündigt", "gekuendigt", "abbezahlt", "abgezahlt",
    ]
    has_correction_word = any(word in text_lower for word in correction_words)
    has_monthly_context = any(phrase in text_lower for phrase in ["im monat", "monatlich", "pro monat"])
    if not has_correction_word and not has_monthly_context:
        return ""

    alias_matches = find_detail_alias_matches(text_lower)
    if is_profile_removal_request(text_lower) and alias_matches:
        _length, section, key, label = max(alias_matches, key=lambda item: item[0])
        details = u.get("details", {})
        if not isinstance(details, dict):
            details = {}
        section_values = details.get(section, {})
        if not isinstance(section_values, dict):
            section_values = {}

        old_value = section_values.pop(key, None)
        if old_value is None and section == "kredite" and key == "kredit":
            if len(section_values) == 1:
                fallback_key, old_value = next(iter(section_values.items()))
                section_values.pop(fallback_key, None)
                key = fallback_key
                label = key.replace("_", " ").title()
            elif len(section_values) > 1:
                available = ", ".join(k.replace("_", " ").title() for k in section_values.keys())
                return (
                    "Ich sehe mehrere Kredit-Einträge.\n\n"
                    f"Meinst du: {available}?\n"
                    "Schreib zum Beispiel `lösche Immobilienkredit` oder `lösche Hausgeld`."
                )
        if not section_values and section in details:
            details.pop(section, None)
        else:
            details[section] = section_values

        update_user_field(user_id, "fixed_costs_details", json.dumps(details))
        total_fixed = fixed_costs_total(details)
        update_user_field(user_id, "fixed_costs", total_fixed)

        if old_value is None:
            return (
                "Ich habe dazu keinen bestehenden Eintrag gefunden.\n\n"
                f"{label} ist aktuell nicht in deinem Profil hinterlegt."
            )

        return (
            "Alles klar, ich habe das aus deinem Profil entfernt.\n\n"
            f"{label}: {format_eur(old_value)} rausgenommen\n"
            f"Fixkosten gesamt: {format_eur(total_fixed)}\n\n"
            "Ich nutze das ab jetzt für deine Auswertung."
        )

    numbers = re.findall(r"\d+(?:[.,]\d+)?", text_lower)
    if not numbers:
        return ""

    if alias_matches:
        _length, section, key, label = max(alias_matches, key=lambda item: item[0])
        value = float(numbers[-1].replace(",", "."))
        details = u.get("details", {})
        if not isinstance(details, dict):
            details = {}
        section_values = details.get(section, {})
        if not isinstance(section_values, dict):
            section_values = {}
        section_values[key] = value
        details[section] = section_values

        update_user_field(user_id, "fixed_costs_details", json.dumps(details))
        total_fixed = fixed_costs_total(details)
        if total_fixed > 0:
            update_user_field(user_id, "fixed_costs", total_fixed)

        return (
            "Alles klar, ich habe das aktualisiert.\n\n"
            f"{label}: {format_eur(value)}\n"
            f"Fixkosten gesamt: {format_eur(total_fixed)}\n\n"
            "Ich nutze das ab jetzt für deine Auswertung."
        )

    return ""


def looks_like_profile_correction(text_lower: str) -> bool:
    correction_words = {
        "ändere", "aendere", "änder", "aender", "korrigiere", "korrektur",
        "setze", "setz", "aktualisiere", "update", "füge", "fuege",
        "hinzu", "ergänze", "ergaenze", "nimm auf",
        "lösche", "loesche", "entferne", "streiche", "kündige", "kuendige",
        "gekündigt", "gekuendigt", "abbezahlt", "abgezahlt",
    }
    if any(word in text_lower for word in correction_words):
        return True
    if is_profile_removal_request(text_lower):
        return True
    if any(phrase in text_lower for phrase in ["im monat", "monatlich", "pro monat"]):
        return any(re.search(rf"\b{re.escape(alias)}\b", text_lower) for alias in DETAIL_VALUE_ALIASES)
    return False


def calculate_time_to_goal(goal_amount: float, etf_monthly: float, cash_monthly: float,
                           current_investments: float = 0.0, current_cash: float = 0.0) -> str:
    """Ziel-Prognose mit echten Startwerten und Zinseszins."""
    if goal_amount <= 0:
        return "Bitte hinterlege zuerst einen Zielbetrag."
    if current_investments + current_cash >= goal_amount:
        return "Ziel rechnerisch bereits erreicht."
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


ADMIN_COMMANDS = {
    "/admin", "/pending", "/approve", "/revoke", "/adminusers",
    "/health", "/reportjobs", "/backupnow",
}


def is_admin_id(user_id: int) -> bool:
    return bool(ADMIN_USER_IDS) and user_id in ADMIN_USER_IDS


def get_message_identity(message) -> tuple[str, str]:
    user = getattr(message, "from_user", None)
    if not user:
        return "", ""
    username = f"@{user.username}" if getattr(user, "username", None) else ""
    display_name = " ".join(
        part for part in [getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or ""]
        if part
    ).strip()
    return display_name, username


def require_admin(message) -> bool:
    actor_id = get_actor_id(message)
    if is_admin_id(actor_id):
        return True
    bot.send_message(
        message.chat.id,
        "Dieser Befehl ist nur für Admins. Sende /id und trage deine Telegram-ID in ADMIN_USER_ID ein."
    )
    return False


def build_access_action_markup(user_id: int):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Freigeben", callback_data=f"admin_approve:{user_id}"),
        telebot.types.InlineKeyboardButton("Ablehnen", callback_data=f"admin_revoke:{user_id}")
    )
    return markup


def notify_admins(text: str, reply_markup=None):
    for admin_id in ADMIN_USER_IDS:
        try:
            bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Admin-Benachrichtigung an {admin_id} fehlgeschlagen: {e}")


def get_access_status(user_id: int) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM user_access WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    return row["status"] if row else ""


def ensure_access_record(user_id: int, display_name: str = "", username: str = "") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM user_access WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE user_access
                   SET display_name = COALESCE(NULLIF(?, ''), display_name),
                       username = COALESCE(NULLIF(?, ''), username)
                   WHERE user_id = ?""",
                (display_name, username, user_id)
            )
            conn.commit()
            return row["status"]

        conn.execute(
            """INSERT INTO user_access (user_id, status, requested_at, display_name, username)
               VALUES (?, 'pending', ?, ?, ?)""",
            (user_id, now, display_name, username)
        )
        conn.commit()

    notify_admins(
        "Neue Clarity-Freigabe wartet:\n\n"
        f"ID: {user_id}\n"
        f"Name: {display_name or '-'} {username or ''}\n\n"
        f"Freigeben mit: /approve {user_id}"
    )
    return "pending"


def ensure_user_approved(message) -> bool:
    if not USER_APPROVAL_ENABLED:
        return True
    if not ADMIN_USER_IDS:
        logger.warning("CLARITY_USER_APPROVAL ist aktiv, aber ADMIN_USER_ID(S) fehlt. Zugang wird nicht blockiert.")
        return True

    actor_id = get_actor_id(message)
    if is_admin_id(actor_id):
        return True

    display_name, username = get_message_identity(message)
    status = ensure_access_record(actor_id, display_name, username)

    if status == "approved":
        return True

    if status == "revoked":
        bot.send_message(
            message.chat.id,
            "Dein Zugang zu Clarity ist aktuell nicht freigeschaltet. Bitte wende dich an den Support."
        )
        return False

    bot.send_message(
        message.chat.id,
        "Dein Zugang ist angefragt.\n\n"
        "Clarity ist aktuell im Testlauf. Sobald du freigegeben bist, kannst du direkt starten."
    )
    return False


def approve_user_access(user_id: int, admin_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO user_access (user_id, status, approved_at, approved_by)
               VALUES (?, 'approved', ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   status = 'approved',
                   approved_at = excluded.approved_at,
                   approved_by = excluded.approved_by,
                   revoked_at = ''""",
            (user_id, now, admin_id)
        )
        conn.commit()


def revoke_user_access(user_id: int, admin_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO user_access (user_id, status, revoked_at, approved_by)
               VALUES (?, 'revoked', ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   status = 'revoked',
                   revoked_at = excluded.revoked_at,
                   approved_by = excluded.approved_by""",
            (user_id, now, admin_id)
        )
        conn.commit()


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


MICRO_CONFIRMATIONS = [
    "Ist drin.",
    "Hab ich notiert.",
    "Erfasst.",
    "Ich hab's im Blick.",
]


def get_month_expense_count(user_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS c
               FROM expenses
               WHERE user_id = ?
               AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')""",
            (user_id,)
        ).fetchone()
    return int(row["c"] or 0)


def remember_monthly_moment(user_id: int, moment_key: str) -> bool:
    month_key = date.today().strftime("%Y_%m")
    badge_key = f"moment_{moment_key}_{month_key}"
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO user_badges (user_id, badge_key) VALUES (?, ?)",
                (user_id, badge_key)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_micro_confirmation(expense_count: int = 0) -> str:
    index = max(0, expense_count - 1) % len(MICRO_CONFIRMATIONS)
    return MICRO_CONFIRMATIONS[index]


def build_report_seed_moment(user_id: int, expense_count: int) -> str:
    if expense_count >= 7 and remember_monthly_moment(user_id, "report_seed"):
        return (
            "\n\nDu hast jetzt genug Daten, um ein erstes klares Bild zu bekommen.\n"
            "Ich halte das für deinen Report fest."
        )
    return ""


def format_expense_confirmation(items: list, cp_text: str, user_id: int = None) -> str:
    expense_count = get_month_expense_count(user_id) if user_id else 0
    confirmation = get_micro_confirmation(expense_count)
    report_moment = build_report_seed_moment(user_id, expense_count) if user_id else ""

    if len(items) == 1:
        item = items[0]
        emoji = CATEGORY_EMOJIS.get(item["category"], "")
        merchant = item["merchant"]
        if merchant.lower() == "unbekannt":
            merchant = item["category"].title()
        return (
            f"{confirmation}\n\n"
            f"{item['amount']:.2f} EUR - {merchant}\n"
            f"{emoji} {item['category']} - {cp_text}"
            f"{report_moment}"
        )

    lines = [f"{confirmation} {len(items)} Ausgaben sind festgehalten:", ""]
    for item in items:
        emoji = CATEGORY_EMOJIS.get(item["category"], "")
        merchant = item["merchant"]
        if merchant.lower() == "unbekannt":
            merchant = item["category"].title()
        lines.append(f"{emoji} {merchant} - {item['category']} - {item['amount']:.2f} EUR")
    lines.append("")
    lines.append(cp_text)
    if report_moment:
        lines.append(report_moment.strip())
    return "\n".join(lines)


DETAIL_SECTION_LABELS = {
    "wohnen": ("🏠", "Wohnen"),
    "mobilitaet": ("🚗", "Mobilität"),
    "abos": ("📱", "Abos"),
    "versicherungen": ("🛡️", "Versicherungen"),
    "kredite": ("💳", "Kredite"),
}


DETAIL_ITEM_LABELS = {
    "miete": "Miete",
    "strom": "Strom",
    "gas": "Gas",
    "nebenkosten": "Nebenkosten",
    "hausgeld": "Hausgeld",
    "auto": "Auto",
    "tanken": "Tanken",
    "bahn": "Bahn",
    "netflix": "Netflix",
    "spotify": "Spotify",
    "prime": "Prime",
    "disney": "Disney",
    "gym": "Gym",
    "handy": "Handy",
    "icloud": "iCloud",
    "haftpflicht": "Haftpflicht",
    "bu": "Berufsunfähigkeit",
    "rechtsschutz": "Rechtsschutz",
    "hausrat": "Hausrat",
    "autoversicherung": "Autoversicherung",
    "krankenversicherung": "Krankenversicherung",
    "sonstige": "Sonstige",
    "kredit": "Kredit",
    "immobilie": "Immobilie",
    "hausverwalter": "Hausverwalter",
    "konsum": "Konsum",
}


def format_fixed_cost_breakdown(u: dict) -> str:
    details = u.get("details", {})
    total = u.get("fixed_costs") or fixed_costs_total(details if isinstance(details, dict) else {})
    if not isinstance(details, dict) or not any(isinstance(v, dict) and v for v in details.values()):
        return f"Deine aktuellen Fixkosten liegen bei {total:.2f} EUR pro Monat."

    lines = ["*Deine Fixkosten*", ""]
    for section, values in details.items():
        if not isinstance(values, dict) or not values:
            continue
        emoji, section_label = DETAIL_SECTION_LABELS.get(section, ("•", section.replace("_", " ").title()))
        lines.append(f"{emoji} *{section_label}*")
        for key, value in values.items():
            label = DETAIL_ITEM_LABELS.get(key, key.replace("_", " ").title())
            lines.append(f"{label}: {format_eur(value)}")
        lines.append("")

    lines.append(f"*Gesamt: {format_eur(total)}*")
    return "\n".join(lines)


def maybe_answer_profile_finance(user_id: int, u: dict, text_lower: str) -> str:
    def eur(value: float) -> str:
        return f"{value:.2f} EUR"

    def detail_value(section: str, key: str):
        details = u.get("details", {})
        if not isinstance(details, dict):
            return None
        section_data = details.get(section, {})
        if not isinstance(section_data, dict):
            return None
        value = section_data.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    asks_fixed_breakdown = (
        "fixkosten" in text_lower
        and any(word in text_lower for word in [
            "zeig", "zeige", "aufschlüssel", "aufschluessel", "aufgeschlüsselt",
            "aufgeschluesselt", "liste", "auflisten", "überblick", "ueberblick",
            "details", "detail", "welche", "was sind",
        ])
    )
    if asks_fixed_breakdown:
        return format_fixed_cost_breakdown(u)

    detail_questions = [
        (["miete"], "wohnen", "miete", "Miete"),
        (["strom"], "wohnen", "strom", "Strom"),
        (["gas"], "wohnen", "gas", "Gas"),
        (["wohnen", "wohnkosten"], "wohnen", "gesamt", "Wohnkosten"),
        (["auto"], "mobilitaet", "auto", "Auto"),
        (["bahn", "ticket"], "mobilitaet", "bahn", "Bahn"),
        (["mobilität", "mobilitaet"], "mobilitaet", "gesamt", "Mobilität"),
        (["netflix"], "abos", "netflix", "Netflix"),
        (["spotify"], "abos", "spotify", "Spotify"),
        (["prime", "amazon prime"], "abos", "prime", "Prime"),
        (["disney"], "abos", "disney", "Disney"),
        (["abos", "abo"], "abos", "gesamt", "Abos"),
        (["haftpflicht"], "versicherungen", "haftpflicht", "Haftpflicht"),
        (["berufsunfähigkeit", "bu"], "versicherungen", "bu", "Berufsunfähigkeit"),
        (["rechtsschutz"], "versicherungen", "rechtsschutz", "Rechtsschutz"),
        (["versicherung", "versicherungen"], "versicherungen", "gesamt", "Versicherungen"),
        (["kredit", "kredite"], "kredite", "gesamt", "Kredite"),
    ]

    for triggers, section, key, label in detail_questions:
        if any(trigger in text_lower for trigger in triggers):
            value = detail_value(section, key)
            if value is None and key != "gesamt":
                value = detail_value(section, "gesamt")
            if value is not None:
                return f"{label}: {eur(value)} pro Monat."

    asks_largest_expense = (
        "größte ausgabe" in text_lower
        or "groesste ausgabe" in text_lower
        or "höchste ausgabe" in text_lower
        or "hoechste ausgabe" in text_lower
        or "teuerste ausgabe" in text_lower
    )
    if asks_largest_expense:
        period_sql, period_label = detect_period_sql(text_lower)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""SELECT amount, merchant, category, created_at
                    FROM expenses
                    WHERE user_id = ? AND {period_sql}
                    ORDER BY amount DESC, created_at DESC
                    LIMIT 1""",
                (user_id,)
            )
            row = cursor.fetchone()
        if not row:
            return f"Ich habe {period_label} noch keine Ausgaben gefunden."
        emoji = CATEGORY_EMOJIS.get(row["category"], "")
        return (
            f"Deine größte Ausgabe {period_label} war {eur(row['amount'])} "
            f"bei {row['merchant']}.\n{emoji} {row['category']}"
        )

    asks_top_category = (
        "größte kategorie" in text_lower
        or "groesste kategorie" in text_lower
        or "stärkste kategorie" in text_lower
        or "staerkste kategorie" in text_lower
        or "top kategorie" in text_lower
    )
    if asks_top_category:
        period_sql, period_label = detect_period_sql(text_lower)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""SELECT category, SUM(amount) AS total
                    FROM expenses
                    WHERE user_id = ? AND {period_sql}
                    GROUP BY category
                    ORDER BY total DESC
                    LIMIT 1""",
                (user_id,)
            )
            row = cursor.fetchone()
        if not row or not row["total"]:
            return f"Ich habe {period_label} noch keine Ausgaben gefunden."
        emoji = CATEGORY_EMOJIS.get(row["category"], "")
        return f"Deine größte Kategorie {period_label}: {emoji} {row['category']} mit {eur(row['total'])}."

    asks_total_money = (
        "wie viel geld habe ich insgesamt" in text_lower
        or "wieviel geld habe ich insgesamt" in text_lower
        or "wie hoch ist mein nettovermoegen" in text_lower
        or "wie hoch ist mein nettovermögen" in text_lower
        or "wie viel vermoegen habe ich" in text_lower
        or "wie viel vermögen habe ich" in text_lower
    )
    if asks_total_money:
        investments = u.get("current_investments") or 0.0
        cash = u.get("current_cash") or 0.0
        total = investments + cash
        return (
            f"Dein aktuelles Nettovermögen liegt bei {total:.2f} EUR.\n"
            f"Investments: {investments:.2f} EUR\n"
            f"Cash: {cash:.2f} EUR"
        )

    asks_savings = (
        "wie hoch ist meine sparrate" in text_lower
        or "wie hoch ist meine sparquote" in text_lower
        or "wie viel spare ich" in text_lower
    )
    if asks_savings:
        etf = u.get("etf_savings") or 0.0
        cash = u.get("cash_savings") or 0.0
        total = etf + cash
        income = (u.get("income") or 0.0) + (u.get("other_income") or 0.0)
        quote = (total / income * 100.0) if income > 0 else 0.0
        return (
            f"Deine monatliche Sparrate liegt bei {total:.2f} EUR.\n"
            f"ETF: {etf:.2f} EUR\n"
            f"Cash: {cash:.2f} EUR\n"
            f"Sparquote: {quote:.1f}%"
        )

    asks_fixed = (
        "wie hoch sind meine fixkosten" in text_lower
        or "wie viel fixkosten habe ich" in text_lower
        or "wieviel fixkosten habe ich" in text_lower
    )
    if asks_fixed:
        return format_fixed_cost_breakdown(u)

    asks_available = (
        "wie viel geld habe ich noch" in text_lower
        or "wie viel habe ich noch uebrig" in text_lower
        or "wie viel habe ich noch übrig" in text_lower
        or "wieviel habe ich noch uebrig" in text_lower
        or "wieviel habe ich noch übrig" in text_lower
    )
    if asks_available:
        remaining, total_expenses, income, fixed = calculate_remaining_budget(u, user_id)
        return (
            f"Dir bleiben diesen Monat aktuell {remaining:.2f} EUR.\n"
            f"Einnahmen: {income:.2f} EUR\n"
            f"Fixkosten: {fixed:.2f} EUR\n"
            f"Ausgaben bisher: {total_expenses:.2f} EUR"
        )

    return ""


def build_ai_user_context(user_id: int, u: dict) -> str:
    def eur(value) -> str:
        try:
            return f"{float(value or 0):.2f} EUR"
        except (TypeError, ValueError):
            return "0.00 EUR"

    income = (u.get("income") or 0.0) + (u.get("other_income") or 0.0)
    etf_savings = u.get("etf_savings") or 0.0
    cash_savings = u.get("cash_savings") or 0.0
    investments = u.get("current_investments") or 0.0
    cash = u.get("current_cash") or 0.0
    savings_total = etf_savings + cash_savings
    savings_rate = (savings_total / income * 100.0) if income > 0 else 0.0
    remaining, total_expenses, _, _ = calculate_remaining_budget(u, user_id)

    lines = [
        "Nutzerprofil aus der Clarity-Datenbank:",
        f"- Monatliches Nettoeinkommen: {eur(u.get('income'))}",
        f"- Weitere monatliche Einkommen: {eur(u.get('other_income'))}",
        f"- Monatliche Fixkosten gesamt: {eur(u.get('fixed_costs'))}",
        f"- Sparziel: {u.get('goal_description') or 'nicht hinterlegt'}",
        f"- Zielbetrag: {eur(u.get('goal_amount'))}",
        f"- Aktuelle Investments: {eur(investments)}",
        f"- Aktuelle Cash-Reserven: {eur(cash)}",
        f"- ETF-Sparrate: {eur(etf_savings)}",
        f"- Cash-Sparrate: {eur(cash_savings)}",
        f"- Gesamte monatliche Sparrate: {eur(savings_total)}",
        f"- Sparquote: {savings_rate:.1f}%",
        f"- Ausgaben diesen Monat: {eur(total_expenses)}",
        f"- Freies Restbudget diesen Monat: {eur(remaining)}",
    ]

    details = u.get("details", {})
    if isinstance(details, dict) and details:
        lines.append("- Verfeinertes Profil:")
        for section, values in details.items():
            if isinstance(values, dict):
                clean_values = ", ".join(f"{key}: {eur(value)}" for key, value in values.items())
                lines.append(f"  - {section}: {clean_values}")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT merchant, category, amount, created_at
               FROM expenses
               WHERE user_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT 5""",
            (user_id,)
        )
        latest_expenses = cursor.fetchall()

        cursor.execute(
            """SELECT category, SUM(amount) AS total
               FROM expenses
               WHERE user_id = ?
               AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')
               GROUP BY category
               ORDER BY total DESC
               LIMIT 5""",
            (user_id,)
        )
        top_categories = cursor.fetchall()

        cursor.execute(
            """SELECT amount, direction, asset_type, asset_name, event_type, source, created_at
               FROM investment_events
               WHERE user_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT 5""",
            (user_id,)
        )
        latest_investments = cursor.fetchall()

        cursor.execute(
            """SELECT amount, scope, source, created_at
               FROM portfolio_snapshots
               WHERE user_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT 3""",
            (user_id,)
        )
        latest_snapshots = cursor.fetchall()

    if latest_expenses:
        lines.append("- Letzte Ausgaben:")
        for row in latest_expenses:
            lines.append(f"  - {row['merchant']} ({row['category']}): {eur(row['amount'])}")

    if top_categories:
        lines.append("- Top-Kategorien diesen Monat:")
        for row in top_categories:
            lines.append(f"  - {row['category']}: {eur(row['total'])}")

    if latest_investments:
        lines.append("- Letzte Investment-Ereignisse:")
        for row in latest_investments:
            name = row["asset_name"] or row["asset_type"]
            lines.append(
                f"  - {row['event_type']} / {name}: {row['direction']} {eur(row['amount'])} ({row['source']})"
            )

    if latest_snapshots:
        lines.append("- Letzte Portfolio-Stände:")
        for row in latest_snapshots:
            lines.append(f"  - {row['scope']}: {eur(row['amount'])} ({row['source']})")

    return "\n".join(lines)


def is_hard_off_topic_request(text_lower: str) -> bool:
    hard_off_topic = [
        "schreib mir ein buch", "schreibe mir ein buch", "schreib ein buch",
        "schreibe ein buch", "rette die welt", "weltfrieden", "hausaufgabe",
        "aufsatz", "essay", "gedicht", "liebesbrief", "rezept",
    ]
    return any(phrase in text_lower for phrase in hard_off_topic)


def is_off_topic_request(text_lower: str) -> bool:
    if is_hard_off_topic_request(text_lower):
        return True

    finance_words = [
        "geld", "budget", "ausgabe", "ausgaben", "sparen", "sparrate", "score",
        "invest", "investment", "konto", "cash", "vermögen", "vermoegen", "report",
        "monat", "woche", "ziel", "fixkosten", "einkommen", "essen", "friseur",
        "etf", "fonds", "indexfonds", "aktie", "aktien", "msci", "ftse", "sparplan",
        "depot", "portfolio", "dividende", "rendite", "zins", "zinsen", "risiko",
        "altersvorsorge", "rente", "finanz", "finanzen", "vermögensaufbau",
        "vermoegensaufbau", "miete", "strom", "gas", "hausgeld", "hausverwalter",
        "tanken", "kredit", "versicherung",
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


def is_help_question(text_lower: str) -> bool:
    help_phrases = [
        "was kann ich", "was kann man", "wie benutze", "wie nutze",
        "wie funktioniert", "wie geht", "was mache ich", "was kann der bot",
        "was kannst du", "hilfe", "erklär", "erklaer", "anleitung",
    ]
    clarity_terms = ["clarity", "bot", "dich", "hier", "das"]
    return any(phrase in text_lower for phrase in help_phrases) and any(term in text_lower for term in clarity_terms)


def build_help_answer() -> str:
    return (
        "Ich kümmere mich um deine Übersicht.\n\n"
        "Du kannst mir Ausgaben einfach so schreiben, wie sie dir in den Kopf kommen:\n\n"
        "`Lidl 34€`\n"
        "`Tanken 60€`\n"
        "`Restaurant 20€`\n\n"
        "Ich ordne das automatisch ein und behalte den Überblick für dich.\n\n"
        "Wenn du wissen willst, wo du stehst, frag mich einfach:\n\n"
        "`Wie viel habe ich noch übrig?`\n"
        "`Was war meine größte Ausgabe?`\n"
        "`Wie weit bin ich von meinem Ziel entfernt?`\n\n"
        "Ich gebe dir klare Antworten - ohne dass du selbst rechnen musst.\n\n"
        "Dein Profil kannst du jederzeit anpassen:\n\n"
        "`Miete jetzt 800€`\n"
        "`Autoversicherung 105€ im Monat`\n"
        "`lösche Spotify`\n"
        "`Kredit ist abbezahlt`\n\n"
        "Oder du nutzt /verfeinern, wenn du es genauer einstellen willst.\n\n"
        "Für einen schnellen Überblick:\n\n"
        "/status\n"
        "/score\n"
        "/goal\n"
        "/stats\n\n"
        "Am Monatsanfang bekommst du automatisch deinen Report."
    )


def build_not_understood_answer() -> str:
    return (
        "Das habe ich nicht ganz verstanden.\n\n"
        "Schreib es einfach so, wie du es im Alltag sagen würdest.\n"
        "Zum Beispiel:\n\n"
        "`Lidl 34€`\n"
        "`Tanken 60€`\n\n"
        "Ich kümmere mich um den Rest."
    )


def build_start_intro() -> str:
    return (
        "Gut, dass du hier bist.\n\n"
        "Ich halte dein Geld für dich im Blick.\n"
        "Du musst nichts vorbereiten - wir gehen das Schritt für Schritt zusammen durch.\n\n"
        "Ich stelle dir ein paar Fragen, damit ich dein Profil sauber aufbauen kann.\n"
        "Danach kannst du mich einfach im Alltag nutzen.\n\n"
        "*Schritt 1 von 8:* Wie hoch ist dein monatliches Nettoeinkommen?\n"
        "_(z.B. 2500)_\n\n"
        "_Mit /zurueck oder 'zurück' gehst du einen Schritt zurück._"
    )


def is_score_info_question(text_lower: str) -> bool:
    if "score" not in text_lower:
        return False
    info_triggers = [
        "was ist", "was bedeutet", "erklär", "erklaer", "wie funktioniert",
        "wie wird", "wofür", "wofuer", "warum", "was sagt",
    ]
    return any(trigger in text_lower for trigger in info_triggers)


def build_score_info_answer() -> str:
    return (
        "Der *Clarity Score* zeigt, wie stabil dein finanzielles Verhalten gerade ist.\n\n"
        "*Er besteht aus 4 Bereichen:*\n"
        "1. Budget Control - wie viel freies Monatsbudget übrig bleibt.\n"
        "2. Savings Execution - ob du deine Sparrate wirklich umsetzt.\n"
        "3. Tracking Consistency - wie verlässlich deine Datenbasis ist.\n"
        "4. Financial Structure - Notgroschen, Sparquote und positives Budget.\n\n"
        "Hohe Scores entstehen nicht über Nacht. Sie werden über Zeit freigeschaltet, damit der Score wertvoll bleibt.\n\n"
        "Wichtig: Der Score ist kein Urteil. Er zeigt dir deinen nächsten klaren Hebel."
    )


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

def get_score_rank(score: int) -> tuple:
    for low, high, name, emoji in SCORE_RANKS:
        if low <= score <= high:
            return name, emoji
    return SCORE_RANKS[-1][2], SCORE_RANKS[-1][3]


def get_platform_days(user_id: int) -> int:
    """Plattformtage starten mit dem ersten echten Expense-Eintrag."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MIN(DATE(created_at)) AS first_day FROM expenses WHERE user_id = ?",
            (user_id,)
        )
        first_day = cursor.fetchone()["first_day"]
    if not first_day:
        return 0
    try:
        first_date = date.fromisoformat(first_day)
    except ValueError:
        return 0
    return max(1, (date.today() - first_date).days + 1)


def get_tracking_days_90(user_id: int) -> int:
    since = (date.today() - timedelta(days=89)).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT COUNT(DISTINCT DATE(created_at)) AS days
               FROM expenses
               WHERE user_id = ? AND DATE(created_at) >= DATE(?)""",
            (user_id, since)
        )
        return int(cursor.fetchone()["days"] or 0)


def has_confirmed_investment_for_month(user_id: int, month_key: str = None) -> bool:
    month_key = month_key or date.today().strftime("%Y-%m")
    badge_key = f"inv_{month_key.replace('-', '_')}"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_key = ?",
            (user_id, badge_key)
        )
        if cursor.fetchone():
            return True
        cursor.execute(
            """SELECT 1 FROM investment_events
               WHERE user_id = ?
               AND source = 'investiert_command'
               AND strftime('%Y-%m', created_at) = ?
               LIMIT 1""",
            (user_id, month_key)
        )
        return cursor.fetchone() is not None


def get_score_cap(platform_days: int) -> tuple:
    if platform_days < 30:
        return 59, max(0, 30 - platform_days), 60
    if platform_days < 60:
        return 69, 60 - platform_days, 70
    if platform_days < 90:
        return 79, 90 - platform_days, 80
    if platform_days < 180:
        return 85, 180 - platform_days, 86
    if platform_days < 365:
        return 92, 365 - platform_days, 93
    return 100, 0, 100


def calculate_start_score(u: dict) -> int:
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    fixed = u.get("fixed_costs") or 0
    savings = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
    investments = u.get("current_investments") or 0
    cash = u.get("current_cash") or 0
    savings_ratio = savings / income if income > 0 else 0

    score = 30
    if savings > 0:
        score += 5
    if savings_ratio >= 0.10:
        score += 5
    if savings_ratio >= 0.20:
        score += 5
    if investments > 0:
        score += 5
    if fixed > 0 and cash >= fixed * 3:
        score += 7
    elif fixed > 0 and cash >= fixed:
        score += 3
    if (
        u.get("onboarding_step") == STEP_NORMAL
        and income > 0
        and fixed >= 0
        and bool(u.get("goal_description"))
        and (u.get("goal_amount") or 0) > 0
    ):
        score += 5
    return min(score, 65)


def calculate_savings_execution_points(savings_ratio: float, confirmed: bool) -> int:
    if confirmed and savings_ratio >= 0.20:
        return 25
    if confirmed and savings_ratio >= 0.15:
        return 18
    if confirmed and savings_ratio >= 0.10:
        return 12
    if savings_ratio >= 0.20:
        return 10
    if savings_ratio >= 0.15:
        return 6
    if savings_ratio >= 0.10:
        return 3
    return 0


def calculate_clarity_score(user_id: int, u: dict, total_expenses: float, report_month: str = None) -> dict:
    """
    Clarity Score V2: schwerer Prestige-Score aus Budget, Umsetzung,
    90-Tage-Konstanz und finanzieller Struktur.
    """
    report_month = report_month or date.today().strftime("%Y-%m")
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    fixed = u.get("fixed_costs") or 0
    free_budget = income - fixed
    remaining = free_budget - total_expenses
    savings_amount = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
    savings_ratio = savings_amount / income if income > 0 else 0
    cash = u.get("current_cash") or 0

    budget_points = 0
    if free_budget > 0:
        remaining_ratio = remaining / free_budget
        budget_points = 25 if remaining_ratio >= 0.30 else max(0, int(25 * remaining_ratio / 0.30))

    confirmed = has_confirmed_investment_for_month(user_id, report_month)
    savings_points = calculate_savings_execution_points(savings_ratio, confirmed)

    tracking_days_90 = get_tracking_days_90(user_id)
    consistency_points = min(25, int(25 * min(tracking_days_90, 90) / 90))

    structure_points = 0
    if fixed > 0 and cash >= fixed * 3:
        structure_points += 10
    if savings_ratio >= 0.15:
        structure_points += 8
    if free_budget > 0:
        structure_points += 7

    raw_total = budget_points + savings_points + consistency_points + structure_points
    start_score = calculate_start_score(u)
    platform_days = get_platform_days(user_id)
    proof_days = platform_days
    cap, days_to_unlock, next_unlock_level = get_score_cap(platform_days)
    baseline = start_score if platform_days == 0 else min(start_score, cap)
    capped_total = min(max(raw_total, baseline), cap)
    rank_name, rank_emoji = get_score_rank(capped_total)

    if platform_days < 30:
        phase = "Aufbauphase"
    elif platform_days < 90:
        phase = "Proof-Phase"
    else:
        phase = "Verified"

    return {
        "total": capped_total,
        "raw_total": raw_total,
        "cap": cap,
        "budget": budget_points,
        "savings": savings_points,
        "consistency": consistency_points,
        "structure": structure_points,
        "start_score": start_score,
        "platform_days": platform_days,
        "proof_days": proof_days,
        "tracking_days_90": tracking_days_90,
        "savings_confirmed": confirmed,
        "savings_ratio": savings_ratio,
        "rank_name": rank_name,
        "rank_emoji": rank_emoji,
        "phase": phase,
        "days_to_unlock": days_to_unlock,
        "next_unlock_level": next_unlock_level,
    }


def record_score_history_if_needed(user_id: int, u: dict = None) -> None:
    today = date.today().isoformat()
    month_key = date.today().strftime("%Y-%m")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM score_history WHERE user_id = ? AND recorded_date = ?",
            (user_id, today)
        )
        if cursor.fetchone():
            return

    u = u or get_or_create_user(user_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND strftime('%Y-%m', created_at) = ?",
            (user_id, month_key)
        )
        total_expenses = cursor.fetchone()[0] or 0.0

    score_data = calculate_clarity_score(user_id, u, total_expenses, month_key)
    cp = u.get("clarity_points") or 0
    with get_db() as conn:
        conn.execute(
            """INSERT INTO score_history
               (user_id, recorded_date, clarity_score, clarity_points, rank_name,
                proof_days, budget_points, savings_points, consistency_points, structure_points)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, today, score_data["total"], cp, score_data["rank_name"],
                score_data["proof_days"], score_data["budget"], score_data["savings"],
                score_data["consistency"], score_data["structure"],
            )
        )
        conn.commit()

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
    """Prüft Fast-Food-Pause Badge. Gibt True zurück wenn neu vergeben."""
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        keyword_sql = " OR ".join(
            "LOWER(COALESCE(merchant, '') || ' ' || COALESCE(description, '')) LIKE ?"
            for _ in FASTFOOD_KEYWORDS
        )
        cursor.execute(
            f"""SELECT COUNT(id) as cnt FROM expenses
                WHERE user_id = ?
                AND DATE(created_at) >= ?
                AND ({keyword_sql})""",
            (user_id, thirty_ago, *[f"%{keyword}%" for keyword in FASTFOOD_KEYWORDS])
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
        record_score_history_if_needed(user_id)
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

    record_score_history_if_needed(user_id)
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

    score_data = calculate_clarity_score(user_id, u, old_expenses, stored_month)
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
        bonus_lines.append("Budget überschritten - kein Monats-Bonus.")

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
    rank_name, rank_emoji = score_data["rank_name"], score_data["rank_emoji"]

    bot_instance.send_message(
        user_id,
        f"*{stored_month} - Monatsabschluss*\n\n"
        f"Clarity Score: *{score_data['total']}/100*\n"
        f"Ausgaben: {old_expenses:.2f} EUR\n"
        f"Nettovermögen: {net_worth:.2f} EUR\n"
        f"{chr(10).join(bonus_lines)}\n\n"
        f"{rank_emoji} Score-Rang: {rank_name}",
        parse_mode="Markdown"
    )

# ====================== BOT INIT ======================
bot = telebot.TeleBot(TOKEN)
user_last_message: dict = {}
user_pending_actions: dict = {}
user_reset_pending: dict = {}

def setup_bot_menu():
    commands = [
        telebot.types.BotCommand("start",      "🚀 Start & Profil anlegen"),
        telebot.types.BotCommand("status",     "📊 Restbudget checken"),
        telebot.types.BotCommand("stats",      "📈 Ausgaben nach Kategorien"),
        telebot.types.BotCommand("score",      "🌟 Clarity Score & Rang"),
        telebot.types.BotCommand("scoreinfo",  "Clarity Score erklärt"),
        telebot.types.BotCommand("badges",     "🏆 Errungenschaften"),
        telebot.types.BotCommand("goal",       "🎯 Sparziel & Prognose"),
        telebot.types.BotCommand("investiert", "💰 Sparrate bestätigen (+20 CP)"),
        telebot.types.BotCommand("verfeinern", "⚙️ Fixkosten & Profil bearbeiten"),
        telebot.types.BotCommand("zurueck",    "Nur Onboarding: Schritt zurück"),
        telebot.types.BotCommand("undo",       "Letzte Ausgabe löschen"),
        telebot.types.BotCommand("editlast",   "Letzte Ausgabe ändern"),
        telebot.types.BotCommand("reset",      "🗑️ Alle Daten löschen"),
    ]
    bot.set_my_commands(commands)

    admin_commands = commands + [
        telebot.types.BotCommand("admin",      "Admin-Befehle anzeigen"),
        telebot.types.BotCommand("pending",    "Wartende Nutzer anzeigen"),
        telebot.types.BotCommand("approve",    "Nutzer freigeben"),
        telebot.types.BotCommand("revoke",     "Nutzer sperren"),
        telebot.types.BotCommand("adminusers", "Nutzerstatus anzeigen"),
        telebot.types.BotCommand("health",     "Bot-Status prüfen"),
        telebot.types.BotCommand("reportjobs", "Report-Jobs prüfen"),
        telebot.types.BotCommand("backupnow",  "Datenbank sichern"),
    ]
    for admin_id in ADMIN_USER_IDS:
        try:
            bot.set_my_commands(
                admin_commands,
                scope=telebot.types.BotCommandScopeChat(admin_id)
            )
        except Exception as e:
            logger.warning(f"Admin-Menü für {admin_id} konnte nicht gesetzt werden: {e}")

    logger.info("✅ Telegram Menü eingerichtet.")


def get_last_expense(user_id: int):
    with get_db() as conn:
        return conn.execute(
            "SELECT id, amount, merchant, category FROM expenses "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()


def start_edit_last_flow(user_id: int):
    last = get_last_expense(user_id)
    if not last:
        bot.send_message(user_id, "Ich finde noch keine Ausgabe, die ich ändern kann.")
        return

    user_pending_actions[user_id] = {
        "type": "edit_last_expense",
        "expense_id": last["id"],
    }
    bot.send_message(
        user_id,
        "Welche neue Summe soll ich eintragen?\n\n"
        f"Letzte Ausgabe: {last['merchant']} · {last['category']} · {format_eur(last['amount'])}\n\n"
        "Schick mir einfach den neuen Betrag, z.B. 24,50.\n"
        "Mit „abbrechen“ bleibt alles unverändert."
    )


def update_expense_amount(user_id: int, expense_id: int, new_amount: float):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, amount, merchant, category FROM expenses "
            "WHERE user_id = ? AND id = ?",
            (user_id, expense_id)
        )
        expense = cursor.fetchone()
        if not expense:
            return None

        cursor.execute(
            "UPDATE expenses SET amount = ? WHERE id = ? AND user_id = ?",
            (new_amount, expense_id, user_id)
        )
        conn.commit()
        return expense


def handle_pending_action(user_id: int, text_input: str, text_lower: str) -> bool:
    action = user_pending_actions.get(user_id)
    if not action:
        return False

    if text_lower in {"abbrechen", "stop", "cancel"}:
        user_pending_actions.pop(user_id, None)
        bot.send_message(user_id, "Alles klar, die letzte Ausgabe bleibt unverändert.")
        return True

    if action.get("type") == "edit_last_expense":
        new_amount = parse_currency(text_input)
        if new_amount is None:
            bot.send_message(
                user_id,
                "Bitte schick mir nur den neuen Betrag, z.B. 24,50. "
                "Oder schreibe „abbrechen“."
            )
            return True

        old_expense = update_expense_amount(user_id, action["expense_id"], new_amount)
        user_pending_actions.pop(user_id, None)
        if not old_expense:
            bot.send_message(user_id, "Diese Ausgabe existiert nicht mehr. Bitte versuche es erneut.")
            return True

        bot.send_message(
            user_id,
            "Letzte Ausgabe aktualisiert:\n"
            f"{old_expense['merchant']} · {old_expense['category']}\n"
            f"{format_eur(old_expense['amount'])} → {format_eur(new_amount)}"
        )
        return True

    return False

# ====================== CALLBACK HANDLER (Inline Buttons) ======================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.message.chat.id
    data = call.data
    actor_id = call.from_user.id if getattr(call, "from_user", None) else uid

    logger.info(f"Callback erhalten: user={uid}, actor={actor_id}, data={data}")
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.warning(f"Callback konnte nicht sofort bestaetigt werden: {e}")

    if data.startswith("admin_approve:") or data.startswith("admin_revoke:"):
        if not is_admin_id(actor_id):
            try:
                bot.answer_callback_query(call.id, "Nur Admins können das tun.")
            except Exception:
                pass
            return

        action, raw_target_id = data.split(":", 1)
        if not raw_target_id.strip().lstrip("-").isdigit():
            bot.send_message(uid, "Diese Freigabe konnte nicht gelesen werden. Bitte nutze /pending.")
            return

        target_id = int(raw_target_id.strip())
        if action == "admin_approve":
            approve_user_access(target_id, actor_id)
            admin_text = f"Nutzer freigegeben:\nID: {target_id}"
            user_text = "Du bist für Clarity freigeschaltet. Sende /start und leg los."
            callback_text = "Freigegeben."
        else:
            revoke_user_access(target_id, actor_id)
            admin_text = f"Nutzer abgelehnt/gesperrt:\nID: {target_id}"
            user_text = "Dein Zugang zu Clarity wurde aktuell nicht freigeschaltet."
            callback_text = "Abgelehnt."

        try:
            bot.edit_message_text(admin_text, uid, call.message.message_id)
        except Exception:
            bot.send_message(uid, admin_text)

        try:
            bot.send_message(target_id, user_text)
        except Exception as e:
            logger.info(f"Zugangs-Nachricht an {target_id} nicht gesendet: {e}")

        try:
            bot.answer_callback_query(call.id, callback_text)
        except Exception:
            pass
        return

    if USER_APPROVAL_ENABLED and not is_admin_id(actor_id) and get_access_status(actor_id) != "approved":
        try:
            bot.answer_callback_query(call.id, "Dein Zugang ist noch nicht freigegeben.")
        except Exception:
            pass
        return

    if data == "confirm_reset":
        try:
            bot.answer_callback_query(call.id, "Ich lösche deine Daten...")
        except Exception:
            pass
        try:
            reset_user_data(uid)
            user_reset_pending.pop(uid, None)
            bot.edit_message_text(
                "Alles klar.\n\nIch habe deine Daten gelöscht.\nWenn du wieder starten willst, bin ich hier.",
                uid, call.message.message_id
            )
            logger.info(f"User {uid} hat alle Daten gelöscht.")
        except Exception as e:
            logger.error(f"Reset fehlgeschlagen für User {uid}: {e}", exc_info=True)
            try:
                bot.edit_message_text(
                    "Reset konnte gerade nicht abgeschlossen werden. Bitte versuche es nochmal oder nutze /reset_confirm.",
                    uid, call.message.message_id
                )
            except Exception:
                bot.send_message(uid, "Reset konnte gerade nicht abgeschlossen werden. Bitte versuche es nochmal oder nutze /reset_confirm.")

    elif data == "cancel_reset":
        user_reset_pending.pop(uid, None)
        try:
            bot.answer_callback_query(call.id, "Abgebrochen.")
        except Exception:
            pass
        bot.edit_message_text("Abgebrochen.", uid, call.message.message_id)

    elif data == "start_refine":
        get_or_create_user(uid)
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
def parse_command_user_id(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        return None
    return int(parts[1].strip())


def build_health_report() -> str:
    with get_db() as conn:
        users_total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        expenses_total = conn.execute("SELECT COUNT(*) AS c FROM expenses").fetchone()["c"]
        access_rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM user_access GROUP BY status"
        ).fetchall()
        job_rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM report_jobs GROUP BY status"
        ).fetchall()
        last_error = conn.execute(
            """SELECT status, user_id, report_month, last_error
               FROM report_jobs
               WHERE COALESCE(last_error, '') != ''
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()

    db_size = Path(DB_NAME).stat().st_size if Path(DB_NAME).exists() else 0
    scheduler_state = "aktiv" if REPORT_SCHEDULER and getattr(REPORT_SCHEDULER, "running", False) else "nicht aktiv"
    access_text = ", ".join(f"{row['status']}: {row['c']}" for row in access_rows) or "keine"
    jobs_text = ", ".join(f"{row['status']}: {row['c']}" for row in job_rows) or "keine"
    error_text = "Keine aktuellen Report-Fehler."
    if last_error:
        error_text = (
            f"Letzter Hinweis: {last_error['status']} · User {last_error['user_id']} · "
            f"{last_error['report_month']} · {last_error['last_error'][:120]}"
        )

    return (
        "*Clarity Health*\n\n"
        f"Bot: läuft\n"
        f"Scheduler: {scheduler_state}\n"
        f"User: {users_total}\n"
        f"Ausgaben: {expenses_total}\n"
        f"Zugänge: {access_text}\n"
        f"Reports: {jobs_text}\n"
        f"Datenbank: {db_size / 1024:.1f} KB\n\n"
        f"{error_text}"
    )


def handle_admin_command(message, cmd: str) -> bool:
    uid = message.chat.id
    actor_id = get_actor_id(message)
    if cmd not in ADMIN_COMMANDS:
        return False
    if not require_admin(message):
        return True

    if cmd == "/admin":
        bot.send_message(
            uid,
            "*Admin-Befehle*\n\n"
            "/pending – wartende Freigaben\n"
            "/approve USER_ID – Nutzer freigeben\n"
            "/revoke USER_ID – Nutzer sperren\n"
            "/adminusers – Nutzerstatus anzeigen\n"
            "/health – Bot, DB und Reports prüfen\n"
            "/reportjobs – Report-Versandstatus\n"
            "/backupnow – Datenbank sichern",
            parse_mode="Markdown"
        )
        return True

    if cmd == "/pending":
        with get_db() as conn:
            rows = conn.execute(
                """SELECT user_id, display_name, username, requested_at
                   FROM user_access
                   WHERE status = 'pending'
                   ORDER BY requested_at ASC LIMIT 20"""
            ).fetchall()
        if not rows:
            bot.send_message(uid, "Keine wartenden Freigaben.")
            return True
        bot.send_message(uid, "*Wartende Freigaben:*", parse_mode="Markdown")
        for row in rows:
            name = " ".join(part for part in [row["display_name"], row["username"]] if part) or "-"
            bot.send_message(
                uid,
                f"ID: {row['user_id']}\n"
                f"Name: {name}\n"
                f"Angefragt: {row['requested_at']}\n\n"
                f"Freigeben: /approve {row['user_id']}\n"
                f"Sperren: /revoke {row['user_id']}"
            )
        return True

    if cmd == "/approve":
        target_id = parse_command_user_id(message)
        if target_id is None:
            bot.send_message(uid, "Bitte nutze: /approve USER_ID")
            return True
        approve_user_access(target_id, actor_id)
        bot.send_message(uid, f"Nutzer {target_id} ist freigegeben.")
        try:
            bot.send_message(target_id, "Du bist für Clarity freigeschaltet. Sende /start und leg los.")
        except Exception as e:
            logger.info(f"Freigabe-Nachricht an {target_id} nicht gesendet: {e}")
        return True

    if cmd == "/revoke":
        target_id = parse_command_user_id(message)
        if target_id is None:
            bot.send_message(uid, "Bitte nutze: /revoke USER_ID")
            return True
        revoke_user_access(target_id, actor_id)
        bot.send_message(uid, f"Nutzer {target_id} ist gesperrt.")
        return True

    if cmd == "/adminusers":
        with get_db() as conn:
            rows = conn.execute(
                """SELECT u.user_id,
                          COALESCE(a.status, 'approved') AS access_status,
                          u.onboarding_step,
                          u.last_activity_date,
                          (SELECT COUNT(*) FROM expenses e WHERE e.user_id = u.user_id) AS expenses_count,
                          (SELECT COUNT(DISTINCT DATE(e.created_at))
                           FROM expenses e
                           WHERE e.user_id = u.user_id
                           AND strftime('%Y-%m', e.created_at) = strftime('%Y-%m', 'now', 'localtime')) AS tracked_days
                   FROM users u
                   LEFT JOIN user_access a ON a.user_id = u.user_id
                   ORDER BY u.last_activity_date DESC, u.user_id DESC
                   LIMIT 20"""
            ).fetchall()
        if not rows:
            bot.send_message(uid, "Noch keine Nutzer in der Datenbank.")
            return True
        text = "*Nutzerübersicht:*\n\n"
        for row in rows:
            onboarding = "fertig" if row["onboarding_step"] == STEP_NORMAL else f"Step {row['onboarding_step']}"
            text += (
                f"{row['user_id']} · {row['access_status']} · {onboarding} · "
                f"{row['expenses_count']} Buchungen · {row['tracked_days']} Tracking-Tage\n"
            )
        bot.send_message(uid, text, parse_mode="Markdown")
        return True

    if cmd == "/health":
        bot.send_message(uid, build_health_report(), parse_mode="Markdown")
        return True

    if cmd == "/reportjobs":
        with get_db() as conn:
            summary = conn.execute(
                "SELECT status, COUNT(*) AS c FROM report_jobs GROUP BY status ORDER BY status"
            ).fetchall()
            recent = conn.execute(
                """SELECT user_id, report_month, status, attempts, scheduled_at, last_error
                   FROM report_jobs
                   ORDER BY updated_at DESC LIMIT 8"""
            ).fetchall()
        text = "*Report-Jobs*\n\n"
        text += "Status: " + (", ".join(f"{row['status']}: {row['c']}" for row in summary) or "keine") + "\n\n"
        for row in recent:
            hint = f" · {row['last_error'][:70]}" if row["last_error"] else ""
            text += f"{row['user_id']} · {row['report_month']} · {row['status']} · Versuch {row['attempts']}{hint}\n"
        bot.send_message(uid, text, parse_mode="Markdown")
        return True

    if cmd == "/backupnow":
        backups_dir = Path("backups")
        backups_dir.mkdir(exist_ok=True)
        backup_path = backups_dir / f"clarity_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        with sqlite3.connect(DB_NAME) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        bot.send_message(uid, f"Backup erstellt:\n{backup_path.resolve()}")
        return True

    return False


@bot.message_handler(commands=[
    'start', 'help', 'score', 'scoreinfo', 'badges', 'verfeinern', 'undo', 'editlast', 'id',
    'settings', 'goal', 'status', 'stats', 'reset', 'reset_confirm', 'investiert', 'testreport',
    'admin', 'pending', 'approve', 'revoke', 'adminusers', 'health', 'reportjobs', 'backupnow'
])
def handle_commands(message):
    uid = message.chat.id
    cmd = message.text.split()[0].lower()
    u = get_or_create_user(uid)

    if handle_admin_command(message, cmd):
        return

    if cmd == '/id':
        actor_id = get_actor_id(message)
        bot.send_message(uid, f"Deine Telegram-ID: {actor_id}\nChat-ID: {uid}")
        return

    if not ensure_user_approved(message):
        return

    if (u.get("onboarding_step") or 0) >= STEP_NORMAL:
        handle_month_transition(uid, u, bot)

    if cmd == '/start':
        if (u.get("onboarding_step") or 0) >= STEP_NORMAL:
            remaining, total_expenses, income, fixed = calculate_remaining_budget(u, uid)
            e = "🟢" if remaining > 200 else ("🟡" if remaining > 0 else "🔴")
            bot.send_message(
                uid,
                "*Clarity ist bereit.*\n\n"
                "Ich habe deinen aktuellen Stand für dich im Blick.\n\n"
                f"Einnahmen: {income:.2f}€\n"
                f"Fixkosten: {fixed:.2f}€\n"
                f"Ausgaben diesen Monat: {total_expenses:.2f}€\n"
                f"{e} *Restbudget: {remaining:.2f}€*\n\n"
                "Du kannst mir jetzt Ausgaben einfach schreiben, z.B. `Lidl 34€`.\n\n"
                "/status – Monatsstatus\n"
                "/verfeinern – Profil verfeinern\n"
                "/settings – Profil bewusst neu einrichten",
                parse_mode="Markdown"
            )
            return
        update_user_field(uid, "onboarding_step", STEP_INCOME)
        bot.send_message(uid, build_start_intro(), parse_mode="Markdown")

    elif cmd == '/help':
        bot.send_message(uid, build_help_answer(), parse_mode="Markdown")

    elif cmd == '/score':
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(amount) FROM expenses WHERE user_id = ? "
                "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')",
                (uid,)
            )
            total_exp = cursor.fetchone()[0] or 0.0


        score_data = calculate_clarity_score(uid, u, total_exp)
        record_score_history_if_needed(uid, u)
        cp = u.get("clarity_points") or 0
        cp_rank_name, cp_rank_emoji, pts_needed = get_rank(cp)
        cp_rank_line = (f"Noch *{pts_needed} CP* bis zum nächsten CP-Rang."
                        if pts_needed > 0 else "Höchster CP-Rang erreicht.")
        unlock_line = ""
        if score_data["days_to_unlock"] > 0:
            unlock_line = (
                f"\nNoch *{score_data['days_to_unlock']} Tage* bis "
                f"Score-Level {score_data['next_unlock_level']}+ freigeschaltet wird."
            )
        confirm_hint = (
            "Sparrate bestätigt"
            if score_data["savings_confirmed"]
            else "Bestätige deine Sparrate mit /investiert für mehr Savings-Punkte."
        )

        bot.send_message(
            uid,
            f"📊 *Clarity Score: {score_data['total']}/100*\n"
            f"{score_data['rank_emoji']} *{score_data['rank_name']}*\n"
            f"Status: {score_data['phase']} · Datenbasis: {score_data['proof_days']}/90 Tage"
            f"{unlock_line}\n\n"
            f"*Breakdown*\n"
            f"├ Budget Control:       {score_data['budget']}/25\n"
            f"├ Savings Execution:    {score_data['savings']}/25\n"
            f"├ Tracking Consistency: {score_data['consistency']}/25\n"
            f"└ Financial Structure:  {score_data['structure']}/25\n\n"
            f"*Nächster Hebel:*\n{confirm_hint}\n\n"
            f"{cp_rank_emoji} CP-Level: *{cp_rank_name}* · {cp} CP\n"
            f"{cp_rank_line}"
            f"\n\nDas ist dein aktueller Stand.\n"
            f"Wichtig ist, dass du dranbleibst.\n\n"
            f"Mehr Kontext: /scoreinfo",
            parse_mode="Markdown"
        ) 

    elif cmd == '/scoreinfo':
        bot.send_message(uid, build_score_info_answer(), parse_mode="Markdown")

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
            bot.send_message(uid, f"Investment-Bonus für {date.today().strftime('%B %Y')} bereits vergeben.")
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
                bot.send_message(uid, "Bonus für diesen Monat bereits vergeben.")
                return

        etf_savings = u.get("etf_savings") or 0
        cash_savings = u.get("cash_savings") or 0
        new_investments = (u.get("current_investments") or 0) + etf_savings
        new_cash = (u.get("current_cash") or 0) + cash_savings
        update_user_field(uid, "current_investments", new_investments)
        update_user_field(uid, "current_cash", new_cash)
        save_investment_event(
            uid, etf_savings, asset_type="etf", asset_name="ETF-Sparrate",
            event_type="recurring_plan", source="investiert_command",
            note="Monatliche ETF-Sparrate bestätigt"
        )
        save_investment_event(
            uid, cash_savings, asset_type="cash", asset_name="Cash-Sparrate",
            event_type="recurring_plan", source="investiert_command",
            note="Monatliche Cash-Sparrate bestätigt"
        )
        save_portfolio_snapshot(
            uid, new_investments, scope="investments",
            source="investiert_command", note="Stand nach Sparrate"
        )
        save_portfolio_snapshot(
            uid, new_cash, scope="cash",
            source="investiert_command", note="Stand nach Sparrate"
        )

        new_pts = add_cp(uid, 20)
        u_fresh = get_or_create_user(uid)
        record_score_history_if_needed(uid, u_fresh)
        new_badges = check_wealth_badges(uid, u_fresh)
        total_wealth = new_investments + new_cash

        bot.send_message(
            uid,
            f"Sparrate bestätigt - *+20 CP* - Gesamt: {new_pts} CP\n"
            f"ETF/Investments: +{etf_savings:.2f} EUR\n"
            f"Cash: +{cash_savings:.2f} EUR\n"
            f"Nettovermögen: *{total_wealth:.2f} EUR*",
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
            "Ich habe deinen aktuellen Stand für dich im Blick.\n\n"
            f"*Monatsstatus*\n\n"
            f"Einnahmen: {income:.2f}€\n"
            f"Fixkosten: {u.get('fixed_costs', 0):.2f}€\n"
            f"Ausgaben: {total_exp:.2f}€\n"
            f"{'─' * 20}\n"
            f"{e} *Restbudget: {remaining:.2f}€*\n\n"
            "Wenn du tiefer gehen willst, frag mich einfach nach deinem Monat.",
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
            bot.send_message(uid, "Noch keine Ausgaben diesen Monat. Sobald du etwas einträgst, halte ich es für dich fest.")
            return

        text = "*Ausgaben nach Kategorien:*\n\n"
        for row in rows:
            e = CATEGORY_EMOJIS.get(row["category"], "🔸")
            text += f"{e} {row['category']}: {row[1]:.2f}€ ({row[2]}x)\n"
        bot.send_message(uid, text, parse_mode="Markdown")

    elif cmd == '/testreport':
        actor_id = get_actor_id(message)
        if not ADMIN_USER_IDS or actor_id not in ADMIN_USER_IDS:
            bot.send_message(uid, "Dieser Befehl ist nur für Admins freigeschaltet. Sende /id und trage deine Telegram-ID in ADMIN_USER_ID ein.")
            return

        parts = message.text.split(maxsplit=1)
        report_month = parts[1].strip() if len(parts) > 1 else date.today().strftime("%Y-%m")
        if not re.match(r"^\d{4}-\d{2}$", report_month):
            bot.send_message(uid, "Bitte nutze das Format YYYY-MM, z.B. /testreport 2026-06")
            return

        bot.send_message(uid, f"Testreport für {report_month} wird vorbereitet...")

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
                bot.send_message(uid, "Dein Testreport ist fertig.")
            else:
                bot.send_message(uid, "Testreport konnte nicht generiert werden. Bitte prüfe die Logs.")
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
            "Du richtest dein Basisprofil jetzt neu ein.\n\n"
            "*Schritt 1 von 8:* Nettoeinkommen?\n\n"
            "_Mit /zurueck oder 'zurück' gehst du einen Schritt zurück._",
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
        user_reset_pending[uid] = time.time()
        bot.send_message(uid,
            "⚠️ Alle Daten werden unwiderruflich gelöscht.\n\n"
            "Wenn du sicher bist, schreibe *Ja*.\n"
            "Wenn nicht, schreibe *Abbrechen*.",
            parse_mode="Markdown"
        )

    elif cmd == '/editlast':
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            start_edit_last_flow(uid)
            return

        new_amount = parse_currency(parts[1])
        if new_amount is None:
            bot.send_message(uid, "Bitte gib einen gültigen Betrag an, z.B. /editlast 34")
            return

        last = get_last_expense(uid)
        if not last:
            bot.send_message(uid, "Keine Ausgabe gefunden, die ich bearbeiten kann.")
            return

        old_expense = update_expense_amount(uid, last["id"], new_amount)
        if not old_expense:
            bot.send_message(uid, "Diese Ausgabe existiert nicht mehr. Bitte versuche es erneut.")
            return

        bot.send_message(
            uid,
            f"Letzte Ausgabe aktualisiert:\n"
            f"{old_expense['merchant']} · {old_expense['category']}\n"
            f"{format_eur(old_expense['amount'])} → {format_eur(new_amount)}"
        )

    elif cmd == '/reset_confirm':
        # Fallback für direkte Text-Eingabe
        reset_user_data(uid)
        bot.send_message(uid, "Alles klar.\n\nIch habe deine Daten gelöscht.\nWenn du wieder starten willst, bin ich hier.")

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

    if not ensure_user_approved(message):
        return

    if uid in user_reset_pending:
        if text_lower.startswith("/") and text_lower not in {"/reset", "/reset_confirm"}:
            user_reset_pending.pop(uid, None)
            bot.send_message(uid, "Reset abgebrochen. Ich mache mit deinem neuen Befehl weiter.")
        else:
            if text_lower == "/reset_confirm":
                try:
                    reset_user_data(uid)
                    user_reset_pending.pop(uid, None)
                    bot.send_message(uid, "Alles klar.\n\nIch habe deine Daten gelöscht.\nWenn du wieder starten willst, bin ich hier.")
                    logger.info(f"User {uid} hat alle Daten per /reset_confirm gelöscht.")
                except Exception as e:
                    logger.error(f"Reset per /reset_confirm fehlgeschlagen für User {uid}: {e}", exc_info=True)
                    bot.send_message(uid, "Reset konnte gerade nicht abgeschlossen werden. Bitte versuche es nochmal.")
                return
            if time.time() - user_reset_pending[uid] > 300:
                user_reset_pending.pop(uid, None)
                bot.send_message(uid, "Reset abgelaufen. Bitte starte ihn bei Bedarf neu mit /reset.")
                return
            if text_lower in {"ja", "yes", "ja, alles löschen", "ja alles löschen", "alles löschen", "loeschen", "löschen"}:
                try:
                    reset_user_data(uid)
                    user_reset_pending.pop(uid, None)
                    bot.send_message(uid, "Alles klar.\n\nIch habe deine Daten gelöscht.\nWenn du wieder starten willst, bin ich hier.")
                    logger.info(f"User {uid} hat alle Daten per Text-Bestätigung gelöscht.")
                except Exception as e:
                    logger.error(f"Reset per Text fehlgeschlagen für User {uid}: {e}", exc_info=True)
                    bot.send_message(uid, "Reset konnte gerade nicht abgeschlossen werden. Bitte versuche es nochmal.")
                return
            if text_lower in {"abbrechen", "stop", "cancel", "nein"}:
                user_reset_pending.pop(uid, None)
                bot.send_message(uid, "Abgebrochen.")
                return
            bot.send_message(uid, "Bitte bestätige mit „Ja, alles löschen“ oder brich mit „Abbrechen“ ab.")
            return

    if is_back_request(text_lower):
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
        if step == STEP_START:
            bot.send_message(uid, "Du bist bereits am Anfang. Mit /start starten wir sauber von vorne.")
            return
        return

    if step == STEP_NORMAL and handle_pending_action(uid, text_input, text_lower):
        return

    if step == STEP_START:
        bot.send_message(uid, "Schreib /start, dann richten wir Clarity in Ruhe ein.")
        return

    if is_score_info_question(text_lower):
        bot.send_message(uid, build_score_info_answer(), parse_mode="Markdown")
        return

    if step == STEP_NORMAL and looks_like_profile_correction(text_lower) and not looks_like_investment_update(text_lower):
        correction_reply = maybe_apply_profile_correction(uid, u, text_lower)
        if correction_reply:
            bot.send_message(uid, correction_reply, parse_mode="Markdown")
        else:
            bot.send_message(
                uid,
                "Das konnte ich deinem Profil noch nicht sicher zuordnen.\n\n"
                "Schreib es kurz und klar, zum Beispiel:\n"
                "`ändere Miete auf 800€`\n"
                "`füge Autoversicherung 105€ hinzu`",
                parse_mode="Markdown"
            )
        return

    if is_help_question(text_lower):
        bot.send_message(uid, build_help_answer(), parse_mode="Markdown")
        return

    if is_hard_off_topic_request(text_lower):
        bot.send_message(
            uid,
            "Dabei kann ich dir nicht sinnvoll helfen.\n\n"
            "Ich halte für dich Ausgaben, Budget, Sparziele, Vermögen und Reports im Blick."
        )
        return
        
    if step == STEP_NORMAL:
        handle_month_transition(uid, u, bot)

        correction_reply = "" if looks_like_investment_update(text_lower) else maybe_apply_profile_correction(uid, u, text_lower)
        if correction_reply:
            bot.send_message(uid, correction_reply, parse_mode="Markdown")
            return

        weekly_reply = maybe_answer_weekly_budget(uid, u, text_lower)
        if weekly_reply:
            bot.send_message(uid, weekly_reply, parse_mode="Markdown")
            return

        category_reply = maybe_answer_category_spending(uid, text_lower)
        if category_reply:
            bot.send_message(uid, category_reply, parse_mode="Markdown")
            return

        profile_reply = maybe_answer_profile_finance(uid, u, text_lower)
        if profile_reply:
            bot.send_message(uid, profile_reply, parse_mode="Markdown")
            return


    # ─── ONBOARDING ─────────────────────────────────────────────────────
    if 0 < step < STEP_NORMAL:
        # Schritt 4: Ziel in Worten – bleibt Text (kein parse_currency)
        if step == STEP_GOAL_DESCRIPTION:
            if len(text_input) >= 2:
                update_user_field(uid, "goal_description", text_input)
                update_user_field(uid, "onboarding_step", STEP_GOAL_AMOUNT)
                bot.send_message(uid,
                    f"🎯 Ziel: *{text_input}*\n\n✅ *Schritt 4 von 8:* Welchen Betrag brauchst du?",
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
            STEP_INCOME:              ("income",              STEP_OTHER_INCOME,        "✅ *Schritt 2 von 8:* Weitere Einkommen? _(Falls keine, 0)_"),
            STEP_OTHER_INCOME:        ("other_income",        STEP_GOAL_DESCRIPTION,    "✅ *Schritt 3 von 8:* Dein Sparziel in Worten?"),
            STEP_FIXED_COSTS:         ("fixed_costs",         STEP_GOAL_DESCRIPTION,    "✅ *Schritt 3 von 8:* Dein Sparziel in Worten?"),
            STEP_GOAL_AMOUNT:         ("goal_amount",         STEP_CURRENT_INVESTMENTS, "✅ *Schritt 5 von 8:* Aktuell investiertes Vermögen? _(ETF/Aktien, 0 falls keines)_"),
            STEP_CURRENT_INVESTMENTS: ("current_investments", STEP_CURRENT_CASH,        "✅ *Schritt 6 von 8:* Cash-Reserven? _(Tagesgeld/Giro)_"),
            STEP_CURRENT_CASH:        ("current_cash",        STEP_ETF_SAVINGS,         "✅ *Schritt 7 von 8:* Monatliche ETF-Sparrate?"),
            STEP_ETF_SAVINGS:         ("etf_savings",         STEP_CASH_SAVINGS,        "✅ *Schritt 8 von 8:* Monatliche Cash-Sparrate?"),
        }

        if step in steps:
            field, next_step, msg = steps[step]
            update_user_field(uid, field, val)
            if field == "current_investments":
                replace_onboarding_investment_start(uid, val)
            elif field == "current_cash":
                u_after_cash = get_or_create_user(uid)
                replace_onboarding_portfolio_snapshots(
                    uid, u_after_cash.get("current_investments") or 0.0, val
                )
            update_user_field(uid, "onboarding_step", next_step)
            bot.send_message(uid, msg, parse_mode="Markdown")

        elif step == STEP_CASH_SAVINGS:
            update_user_field(uid, "cash_savings", val)
            update_user_field(uid, "onboarding_step", STEP_NORMAL)
            update_user_field(uid, "current_month", date.today().strftime("%Y-%m"))
            u_fresh = get_or_create_user(uid)
            new_badges = check_wealth_badges(uid, u_fresh)
            sparrate = (u_fresh.get("etf_savings") or 0) + val

            bot.send_message(uid,
                f"🎉 *Einrichtung abgeschlossen!*\n\n"
                f"Sparrate: {sparrate:.2f}€/Monat\n\n"
                "Als nächstes kannst du deine Fixkosten genauer hinterlegen.\n"
                "Schreib dafür einfach /verfeinern.",
                parse_mode="Markdown"
            )
            # Badges dezent als separate Zeilen falls vorhanden
            if new_badges:
                send_badge_summary(bot, uid, new_badges)
        return

    # ─── VERFEINERN-FLOW ────────────────────────────────────────────────
    if STEP_ADAPT_HOUSING <= step <= STEP_ADAPT_CREDITS:
        nums = [float(x.replace(',', '.')) for x in re.findall(r'\d+(?:[.,]\d+)?', text_input)]
        if not nums:
            bot.send_message(uid, "Das habe ich noch nicht sicher erkannt.\n\nSchreib die Werte bitte kurz, z.B. `Miete 800 Strom 60`.", parse_mode="Markdown")
            return

        details = u.get("details", {})

        if step == STEP_ADAPT_HOUSING:
            parsed = parse_labeled_amounts(text_input, {
                "miete": "miete",
                "kaltmiete": "miete",
                "warmmiete": "miete",
                "strom": "strom",
                "gas": "gas",
                "heizung": "gas",
                "hausgeld": "hausgeld",
                "nebenkosten": "nebenkosten",
            })
            details["wohnen"] = merge_number_defaults(parsed, nums, ["miete", "strom", "gas"])
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_MOBILITY)
            bot.send_message(uid, "🚗 *Teil 2: Mobilität*\nAuto, Tanken, Bahn?\n_(z.B. 250 120 49)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_MOBILITY:
            parsed = parse_labeled_amounts(text_input, {
                "auto": "auto",
                "leasing": "auto",
                "rate": "auto",
                "bahn": "bahn",
                "ticket": "bahn",
                "deutschlandticket": "bahn",
                "tanken": "tanken",
                "benzin": "tanken",
                "diesel": "tanken",
            })
            insurance_parsed = parse_labeled_amounts(text_input, {
                "autoversicherung": "autoversicherung",
                "auto-versicherung": "autoversicherung",
                "kfzversicherung": "autoversicherung",
                "kfz-versicherung": "autoversicherung",
            })
            if insurance_parsed:
                current_insurance = details.get("versicherungen", {})
                if not isinstance(current_insurance, dict):
                    current_insurance = {}
                current_insurance.update(insurance_parsed)
                details["versicherungen"] = current_insurance
            details["mobilitaet"] = merge_number_defaults(parsed, nums, ["auto", "tanken", "bahn"])
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_ABOS)
            bot.send_message(uid, "📺 *Teil 3: Abos*\nNetflix, Spotify, Prime, Disney?\n_(z.B. 14 10 9 8)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_ABOS:
            parsed = parse_labeled_amounts(text_input, {
                "netflix": "netflix",
                "spotify": "spotify",
                "prime": "prime",
                "amazon": "prime",
                "disney": "disney",
                "gym": "gym",
                "fitness": "gym",
                "fitnessstudio": "gym",
                "fitnesstudio": "gym",
                "fittnesstudio": "gym",
                "fintesstudio": "gym",
                "fitnessabo": "gym",
                "handy": "handy",
                "icloud": "icloud",
            })
            details["abos"] = merge_number_defaults(parsed, nums, ["netflix", "spotify", "prime", "disney"])
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_INSURANCE)
            bot.send_message(uid, "🛡️ *Teil 4: Versicherungen*\nHaftpflicht, BU, Rechtsschutz?\n_(z.B. 6 45 25)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_INSURANCE:
            parsed = parse_labeled_amounts(text_input, {
                "haftpflicht": "haftpflicht",
                "bu": "bu",
                "berufsunfähigkeit": "bu",
                "berufsunfaehigkeit": "bu",
                "rechtsschutz": "rechtsschutz",
                "rechtschutz": "rechtsschutz",
                "hausrat": "hausrat",
                "autoversicherung": "autoversicherung",
                "auto-versicherung": "autoversicherung",
                "kfzversicherung": "autoversicherung",
                "kfz-versicherung": "autoversicherung",
                "krankenversicherung": "krankenversicherung",
            })
            details["versicherungen"] = merge_number_defaults(parsed, nums, ["haftpflicht", "bu", "rechtsschutz", "autoversicherung"])
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_CREDITS)
            bot.send_message(uid, "💳 *Teil 5: Kredite*\nImmobilie, Auto, Konsum?\n_(Falls keine → 0)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_CREDITS:
            parsed = parse_labeled_amounts(text_input, {
                "kredit": "kredit",
                "kredite": "kredit",
                "darlehen": "kredit",
                "immobilie": "immobilie",
                "immobile": "immobilie",
                "immo": "immobilie",
                "hausgeld": "hausgeld",
                "hausverwalter": "hausverwalter",
                "verwaltung": "hausverwalter",
                "konsum": "konsum",
                "auto": "auto",
            })
            details["kredite"] = merge_number_defaults(parsed, nums, ["immobilie", "hausgeld", "hausverwalter"])
            total_fixed = sum(
                float(v) for cat in details.values()
                if isinstance(cat, dict) for v in cat.values()
            )
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "fixed_costs", total_fixed)
            update_user_field(uid, "onboarding_step", STEP_NORMAL)
            summary_lines = ["*Profil verfeinert*", ""]
            for section, values in details.items():
                if isinstance(values, dict) and values:
                    summary_lines.append(f"*{section.title()}*")
                    for key, value in values.items():
                        summary_lines.append(f"{key.title()}: {format_eur(value)}")
                    summary_lines.append("")
            bot.send_message(uid,
                "\n".join(summary_lines)
                + f"Fixkosten gesamt: *{total_fixed:.2f}€*\n"
                + "Wenn etwas nicht stimmt, schreib z.B. `ändere Miete auf 450`.",
                parse_mode="Markdown"
            )
        return

    # ─── HYBRID-TRACKER ──────────────────────────────────────────────────
    expense_amounts = extract_amounts(text_lower, exclude_years=True)

    if len(expense_amounts) == 1:
        amount_val = expense_amounts[0]
        merchant_found = None
        category_found = None
        direct_category_label = None

        if is_portfolio_snapshot_input(text_lower):
            update_user_field(uid, "current_investments", amount_val)
            save_portfolio_snapshot(
                uid, amount_val, scope="investments",
                source="chat", note="Manueller Depotstand"
            )
            cp_earned = handle_daily_activity(uid, bot)
            cp_str = "+1 CP" if cp_earned > 0 else "Tageslimit"
            total_wealth = amount_val + (u.get("current_cash") or 0)
            bot.send_message(
                uid,
                f"📊 Depotstand aktualisiert: *{amount_val:.2f}€*\n"
                f"Nettovermögen: *{total_wealth:.2f}€* · {cp_str}",
                parse_mode="Markdown"
            )
            return

        if any(word in text_lower for word in INVESTMENT_INPUTS):
            direction = "out" if any(word in text_lower for word in ["verkauft", "verkauf", "entnommen", "ausgezahlt"]) else "in"
            current_investments = u.get("current_investments") or 0
            new_investments = max(0.0, current_investments - amount_val) if direction == "out" else current_investments + amount_val
            update_user_field(uid, "current_investments", new_investments)
            asset_type, asset_name = detect_investment_asset(text_lower)
            event_type = "recurring_plan" if "sparplan" in text_lower else "one_time"
            save_investment_event(
                uid, amount_val, direction=direction, asset_type=asset_type,
                asset_name=asset_name, event_type=event_type, source="chat",
                note="Investment aus Chat erkannt"
            )
            save_portfolio_snapshot(
                uid, new_investments, scope="investments",
                source="chat", note="Stand nach Investment-Ereignis"
            )
            cp_earned = handle_daily_activity(uid, bot)
            u_fresh = get_or_create_user(uid)
            new_badges = check_wealth_badges(uid, u_fresh)
            cp_str = "+1 CP" if cp_earned > 0 else "Tageslimit"
            total_wealth = new_investments + (u.get("current_cash") or 0)
            verb = "Investment verkauft/entnommen" if direction == "out" else "Investment erfasst"
            bot.send_message(
                uid,
                f"📈 {verb}: *{amount_val:.2f}€*\n"
                f"Investments: {new_investments:.2f}€\n"
                f"Nettovermögen: *{total_wealth:.2f}€* · {cp_str}",
                parse_mode="Markdown"
            )
            send_badge_summary(bot, uid, new_badges)
            return

        for alias, (category, label) in DIRECT_CATEGORY_INPUTS.items():
            if re.search(rf"\b{re.escape(alias)}\b", text_lower):
                category_found = category
                merchant_found = label
                direct_category_label = label
                break

        # Layer 1: Bekannte Händler
        if not merchant_found:
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

            cp_str = "+1 CP" if cp_earned > 0 else "Tageslimit"
            headline = direct_category_label or merchant_found
            msg = format_expense_confirmation(
                [{"amount": amount_val, "category": category_found, "merchant": headline}],
                cp_str,
                user_id=uid
            )
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

    if is_help_question(text_lower):
        bot.send_message(uid, build_help_answer(), parse_mode="Markdown")
        return

    if is_off_topic_request(text_lower):
        bot.send_message(
            uid,
            "Dabei kann ich dir nicht sinnvoll helfen.\n\n"
            "Ich halte für dich Ausgaben, Budget, Sparziele, Score und Reports im Blick."
        )
        return

    # ─── KI-FALLBACK (Nur für komplexe/unbekannte Anfragen) ─────────────
    bot.send_chat_action(uid, 'typing')
    user_context = build_ai_user_context(uid, u)
    prompt = f"""Du bist Clarity, ein Finanz-Assistent. Antworte auf Deutsch.
Datum: {date.today().isoformat()}
Kategorien: LEBENSMITTEL, MOBILITAET, RESTAURANTS, ABOS, FREIZEIT, SHOPPING, VERSICHERUNG, MIETE, GESUNDHEIT, DROGERIE, PFLEGE, SONSTIGES

Du beantwortest Fragen zu persönlichen Finanzen, Ausgaben, Budget, Sparzielen, Vermögensaufbau, ETFs, Fonds, Sparplänen und Reports.
Nutze das Nutzerprofil unten aktiv, wenn es für die Frage relevant ist.
Wenn eine Information im Profil steht, behandle sie als bekannt und frage nicht erneut danach.
Erfinde keine Zahlen. Wenn eine Zahl nicht im Profil oder in den Ausgaben steht, sage kurz, dass sie noch nicht hinterlegt ist.

ETF- und Finanzbildungsfragen sind erlaubt. Erklaere ruhig, klar und hilfreich.
Keine Panik-Disclaimer. Keine konkreten Kauf-/Verkaufsempfehlungen für einzelne Produkte.
Blocke nur Off-Topic-Fragen, z.B. Buch schreiben, Weltpolitik, Hausaufgaben, Rezepte oder allgemeines Gelaber ohne Finanzbezug.

Antwortformat (reines JSON):
{{
  "expenses": [{{"amount": 12.5, "category": "LEBENSMITTEL", "merchant": "Rewe"}}],
  "reply_text": "Kurze Antwort auf Deutsch"
}}

{user_context}

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
            reply += format_expense_confirmation(booked_items, cp_str, user_id=uid) + "\n"

        if data.get("reply_text") and booked == 0:
            reply += data["reply_text"]

        if not reply.strip():
            reply = build_not_understood_answer()

        bot.send_message(uid, reply.strip(), parse_mode="Markdown")

    except json.JSONDecodeError as e:
        logger.error(f"KI JSON-Fehler User {uid}: {e}")
        bot.send_message(uid, build_not_understood_answer(), parse_mode="Markdown")
    except openai.RateLimitError:
        bot.send_message(uid, "Kurz warten – bitte in 10 Sekunden nochmal versuchen.")
    except Exception as e:
        logger.error(f"KI-Fehler User {uid}: {e}", exc_info=True)
        bot.send_message(uid, build_not_understood_answer(), parse_mode="Markdown")


# ====================== REPORT QUEUE ======================
def previous_month_key(today: date = None) -> str:
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def get_active_user_ids() -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT u.user_id
               FROM users u
               LEFT JOIN user_access a ON a.user_id = u.user_id
               WHERE u.onboarding_step = ?
               AND COALESCE(a.status, 'approved') = 'approved'""",
            (STEP_NORMAL,)
        )
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


def has_report_jobs_for_month(report_month: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM report_jobs WHERE report_month = ? LIMIT 1",
            (report_month,)
        ).fetchone()
    return row is not None


def ensure_monthly_report_jobs(today: date = None) -> int:
    today = today or date.today()
    if today.day not in {1, 2}:
        return 0

    report_month = previous_month_key(today)
    if has_report_jobs_for_month(report_month):
        return 0

    created = create_monthly_report_jobs(report_month)
    logger.warning(
        f"Report-Safety-Net hat fehlende Jobs fuer {report_month} nacherzeugt: {created}."
    )
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


def mark_report_job_skipped(job: dict, reason: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_reason = (reason or "Report übersprungen")[:1000]
    with get_db() as conn:
        conn.execute(
            """UPDATE report_jobs
               SET status = 'skipped', last_error = ?, updated_at = ?
               WHERE id = ?""",
            (clean_reason, now, job["id"])
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
        if type(e).__name__ == "ReportSkipped":
            logger.info(f"Report-Job {job.get('id')} übersprungen: {e}")
            mark_report_job_skipped(job, str(e))
            return
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
    scheduler.add_job(
        ensure_monthly_report_jobs,
        trigger=CronTrigger(day="1-2", minute=5),
        id="ensure_monthly_report_jobs",
        replace_existing=True,
        misfire_grace_time=REPORT_CREATION_MISFIRE_GRACE_SECONDS,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    ensure_monthly_report_jobs()
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


def acquire_bot_lock():
    lock_file = open(BOT_LOCK_FILE, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error(
            "Bot startet nicht: In diesem Projektordner läuft bereits eine Clarity-Instanz. "
            "Bitte das andere Terminal mit Ctrl+C beenden."
        )
        sys.exit(1)
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


# ====================== START ======================
if __name__ == "__main__":
    BOT_LOCK_HANDLE = acquire_bot_lock()
    init_db()
    setup_bot_menu()
    REPORT_SCHEDULER = setup_monthly_report_scheduler()
    logger.info("🚀 Project Clarity – Pro Edition gestartet")
    bot.delete_webhook(drop_pending_updates=True)
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        if "409" in str(e) and "getUpdates" in str(e):
            logger.error(
                "Telegram blockiert Polling: Es läuft noch eine zweite Bot-Instanz mit demselben Token. "
                "Stoppe die andere Instanz auf Mac, VS Code oder Server und starte dann neu."
            )
        else:
            logger.error(f"Polling-Fehler: {e}", exc_info=True)
