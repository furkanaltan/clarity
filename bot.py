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
import urllib.request
from datetime import datetime, date, timedelta
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo
import telebot
import openai
from dotenv import load_dotenv
from rove_score import calculate_score as calculate_live_score

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
APP_DIR = Path(__file__).resolve().parent
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
REPORT_TIMEZONE = ZoneInfo("Europe/Berlin")
BOT_LOCK_FILE = os.getenv("CLARITY_BOT_LOCK_FILE", "clarity_bot.lock")
APP_DISPLAY_NAME = "Rov.E"
SCORE_DISPLAY_NAME = "Rov.E Score"

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
    "category_budgets",
    "user_category_rules",
    "users",
]

# ====================== MERCHANT KEYWORDS (Hybrid Layer 1) ======================
MERCHANT_KEYWORDS = {
    "Lidl":       ["lidl"],
    "Rewe":       ["rewe"],
    "Aldi":       ["aldi"],
    "Edeka":      ["edeka"],
    "Kaufland":   ["kaufland"],
    "Globus":     ["globus"],
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
    "Hostinger":  ["hostinger"],
    "Zalando":    ["zalando"],
    "DM":         ["dm", "dm drogerie", "dm-drogerie"],
    "Rossmann":   ["rossmann"],
    "Müller":     ["müller", "mueller"],
}

CATEGORY_MAPPING = {
    "Lidl": "LEBENSMITTEL", "Rewe": "LEBENSMITTEL", "Aldi": "LEBENSMITTEL",
    "Edeka": "LEBENSMITTEL", "Kaufland": "LEBENSMITTEL", "Globus": "LEBENSMITTEL", "Penny": "LEBENSMITTEL",
    "Netto": "LEBENSMITTEL", "Aral": "MOBILITAET", "Shell": "MOBILITAET",
    "Agip": "MOBILITAET", "Esso": "MOBILITAET", "McDonalds": "RESTAURANTS",
    "Burger King": "RESTAURANTS", "Subway": "RESTAURANTS", "Amazon": "SHOPPING",
    "Zalando": "SHOPPING", "Netflix": "ABOS", "Spotify": "ABOS", "Disney+": "ABOS",
    "Hostinger": "SONSTIGES",
    "DM": "DROGERIE", "Rossmann": "DROGERIE", "Müller": "DROGERIE",
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
    "tankstelle": ("MOBILITAET", "Tankstelle"),
    "tankstellen": ("MOBILITAET", "Tankstelle"),
    "shopping": ("SHOPPING", "Shopping"),
    "online einkauf": ("SHOPPING", "Online-Einkauf"),
    "internet einkauf": ("SHOPPING", "Online-Einkauf"),
    "einkauf im internet": ("SHOPPING", "Online-Einkauf"),
    "pflege": ("PFLEGE", "Pflege"),
    "friseur": ("PFLEGE", "Friseur"),
    "frisör": ("PFLEGE", "Friseur"),
    "frisoer": ("PFLEGE", "Friseur"),
    "gesundheit": ("GESUNDHEIT", "Gesundheit"),
    "drogerie": ("DROGERIE", "Drogerie"),
    "abos": ("ABOS", "Abos"),
    "abo": ("ABOS", "Abo"),
    "fixkosten": ("FIXKOSTEN", "Fixkosten"),
}

# Krypto-Coins, die der Bot namentlich erkennt (Reihenfolge in detect_investment_asset:
# spezifische Coins zuerst, dann der generische "crypto"/"krypto"-Fallback). Aliase sind
# Substring-Treffer (gleiche lockere Logik, die bisher schon fuer btc/eth galt) — bewusst nur
# einigermassen eindeutige Tokens, keine riskanten 2-3-Buchstaben-Kuerzel wie "ada"/"dot", die
# in Alltagswoertern vorkommen koennten. So kann die Rov.E-App die Coins einzeln aufschluesseln.
CRYPTO_COINS = [
    ("Bitcoin",   ["bitcoin", "btc"]),
    ("Ethereum",  ["ethereum", "eth"]),
    ("Solana",    ["solana", "sol"]),
    ("XRP",       ["xrp", "ripple"]),
    ("Cardano",   ["cardano"]),
    ("Dogecoin",  ["dogecoin", "doge"]),
    ("Polkadot",  ["polkadot"]),
    ("Litecoin",  ["litecoin", "ltc"]),
    ("Avalanche", ["avalanche", "avax"]),
    ("Polygon",   ["polygon", "matic"]),
]
CRYPTO_ALIASES = [alias for _name, aliases in CRYPTO_COINS for alias in aliases]

INVESTMENT_INPUTS = {
    "etf", "etfs", "investment", "investments", "investieren",
    "sparplan", "depot", "aktien", "aktie", "stock", "stocks",
    "crypto", "krypto",
    "fonds", "fond", "msci", "s&p", "sp500", "s&p500", "nasdaq",
    "world", "anlage", "wertpapier", "wertpapiere",
    *CRYPTO_ALIASES,
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
        "döner", "doener", "pizza", "kebab", "sushi", "ramen", "restaurant", "lieferando",
        "lieferdienst", "takeaway", "fastfood", "bäcker", "baecker", "bäckerei", "baeckerei",
        "café", "cafe", "coffee", "wok", "mensa", "imbiss", "currywurst", "pommes", "schnitzel",
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
        "tanken", "tankstelle", "tankstellen", "benzin", "diesel", "sprit", "super", "e10", "e5", "parkhaus",
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
        "laptop", "kopfhörer", "gadget", "online einkauf", "internet einkauf",
        "einkauf im internet", "onlinekauf", "internetkauf", "onlineshop",
    ],
    "LEBENSMITTEL": [
        "supermarkt", "globus", "lebensmittel", "gemüse", "obst", "brot",
        "milch", "fleisch", "wurst", "käse", "einkaufen",
        "pesto", "nudeln", "pasta", "reis", "joghurt", "quark",
        "eier", "butter", "wasser", "saft", "kaffee", "tee",
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
    "SONSTIGES": [
        "hostinger", "hosting", "webhosting", "domain", "server", "internet",
    ],
}

CATEGORY_EMOJIS = {
    "LEBENSMITTEL": "🛒", "MOBILITAET": "🚗", "RESTAURANTS": "🍽️",
    "ABOS": "📱", "SHOPPING": "🛍️", "FREIZEIT": "🎮",
    "VERSICHERUNG": "🛡️", "MIETE": "🏠", "DROGERIE": "🧴",
    "GESUNDHEIT": "💊", "SONSTIGES": "📦", "PFLEGE": "💇",
    "FIXKOSTEN": "🏠",
}

CATEGORY_LABELS = {
    "LEBENSMITTEL": "Lebensmittel",
    "MOBILITAET": "Mobilität",
    "RESTAURANTS": "Restaurants",
    "ABOS": "Abos",
    "SHOPPING": "Shopping",
    "FREIZEIT": "Freizeit",
    "DROGERIE": "Drogerie",
    "GESUNDHEIT": "Gesundheit",
    "SONSTIGES": "Sonstiges",
    "PFLEGE": "Pflege",
}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.title())


CATEGORY_NAME_ALIASES = {
    "lebensmittel": "LEBENSMITTEL",
    "supermarkt": "LEBENSMITTEL",
    "essen zuhause": "LEBENSMITTEL",
    "restaurants": "RESTAURANTS",
    "restaurant": "RESTAURANTS",
    "resturants": "RESTAURANTS",
    "resturant": "RESTAURANTS",
    "essen gehen": "RESTAURANTS",
    "freizeit": "FREIZEIT",
    "shopping": "SHOPPING",
    "mobilität": "MOBILITAET",
    "mobilitaet": "MOBILITAET",
    "tanken": "MOBILITAET",
    "drogerie": "DROGERIE",
    "pflege": "PFLEGE",
    "gesundheit": "GESUNDHEIT",
    "abos": "ABOS",
    "abo": "ABOS",
    "sonstiges": "SONSTIGES",
}


def normalize_category_name(text_lower: str) -> str:
    for alias, category in CATEGORY_NAME_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text_lower):
            return category
    return ""


def normalize_rule_alias(alias: str) -> str:
    alias = alias.lower().strip()
    alias = re.sub(r"\b(das|der|die|den|dem|mein|meine|meinen|meiner|bitte|künftig|kuenftig|zukünftig|zukunftig)\b", " ", alias)
    alias = re.sub(r"[^a-z0-9äöüß ]+", " ", alias)
    alias = re.sub(r"\s+", " ", alias).strip()
    return alias[:80]


def save_user_category_rule(user_id: int, alias: str, category: str, label: str = "") -> None:
    alias = normalize_rule_alias(alias)
    if not alias or category not in CATEGORY_EMOJIS:
        return
    label = (label or alias.title()).strip()[:80]
    with get_db() as conn:
        conn.execute(
            """INSERT INTO user_category_rules
               (user_id, alias, category, label, usage_count, updated_at)
               VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, alias)
               DO UPDATE SET category = excluded.category,
                             label = excluded.label,
                             updated_at = CURRENT_TIMESTAMP""",
            (user_id, alias, category, label)
        )
        conn.commit()


def find_user_category_rule(user_id: int, text_lower: str):
    if not user_id:
        return None
    normalized = normalize_rule_alias(text_lower)
    if not normalized:
        return None
    with get_db() as conn:
        rows = conn.execute(
            """SELECT alias, category, label
               FROM user_category_rules
               WHERE user_id = ?
               ORDER BY LENGTH(alias) DESC""",
            (user_id,)
        ).fetchall()
        for row in rows:
            alias = row["alias"]
            if re.search(rf"\b{re.escape(alias)}\b", normalized):
                conn.execute(
                    """UPDATE user_category_rules
                       SET usage_count = usage_count + 1,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE user_id = ? AND alias = ?""",
                    (user_id, alias)
                )
                conn.commit()
                return row["category"], row["label"] or alias.title(), alias
    return None


def update_latest_expense_for_rule(user_id: int, alias: str, category: str) -> bool:
    alias = normalize_rule_alias(alias)
    if not alias:
        return False
    with get_db() as conn:
        row = conn.execute(
            """SELECT id FROM expenses
               WHERE user_id = ?
                 AND LOWER(COALESCE(merchant, '')) LIKE ?
               ORDER BY id DESC
               LIMIT 1""",
            (user_id, f"%{alias}%")
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE expenses SET category = ? WHERE id = ? AND user_id = ?",
            (category, row["id"], user_id)
        )
        conn.commit()
        return True


def maybe_apply_category_rule(user_id: int, text_lower: str) -> str:
    if extract_amounts(text_lower, exclude_years=True):
        return ""

    category = normalize_category_name(text_lower)
    if not category:
        return ""

    patterns = [
        r"^(.+?)\s+(?:ist|sind|war|waren|gehört zu|gehoert zu|zählt zu|zaehlt zu)\s+(.+)$",
        r"^(.+?)\s+(?:als|zu)\s+(.+?)\s+(?:speichern|merken|einordnen|kategorisieren)$",
        r"^(?:speicher|merk|ordne)\s+(.+?)\s+(?:als|zu)\s+(.+)$",
    ]
    alias = ""
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if not match:
            continue
        left, right = match.group(1), match.group(2)
        if normalize_category_name(right) == category:
            alias = left
            break

    if not alias:
        return ""

    alias = normalize_rule_alias(alias)
    if not alias or len(alias) < 2:
        return ""

    label = alias.title()
    save_user_category_rule(user_id, alias, category, label)
    updated = update_latest_expense_for_rule(user_id, alias, category)
    emoji = CATEGORY_EMOJIS.get(category, "")
    reply = f"Alles klar. Ich merke mir: *{label}* → {emoji} *{category_label(category)}*."
    if updated:
        reply += "\n\nDie letzte passende Ausgabe habe ich direkt aktualisiert."
    return reply

# ====================== GAMIFICATION CONSTANTS ======================
RANKS = [
    (0,    "Rookie",           "🥚"),
    (50,   "Stratege",         "🔍"),
    (200,  "Controller",       "📊"),
    (500,  "Investor",         "🧱"),
    (1000, "Manager",          "🏗️"),
    (2500, "Kapitalist",       "🏛️"),
    (5000, "Rov.E Elite",       "💎"),
]

SCORE_RANKS = [
    (0, 44, "Rookie", "🥚"),
    (45, 54, "Stratege", "🔍"),
    (55, 64, "Controller", "📊"),
    (65, 74, "Investor", "🧱"),
    (75, 84, "Manager", "🏗️"),
    (85, 92, "Kapitalist", "🏛️"),
    (93, 100, "Rov.E Elite",    "💎"),
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

        conn.execute('''CREATE TABLE IF NOT EXISTS report_links (
            token        TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            report_month TEXT    NOT NULL,
            html_path    TEXT    NOT NULL,
            public_url   TEXT    DEFAULT '',
            expires_at   TEXT    NOT NULL,
            created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
            status       TEXT    NOT NULL DEFAULT 'active',
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_report_links_expiry
            ON report_links(status, expires_at)
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

        conn.execute('''CREATE TABLE IF NOT EXISTS category_budgets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            category      TEXT    NOT NULL,
            monthly_limit REAL    NOT NULL,
            source        TEXT    NOT NULL DEFAULT 'manual',
            active_month  TEXT    NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, category, active_month),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_category_budgets_user_month
            ON category_budgets(user_id, active_month)
        ''')

        conn.execute('''CREATE TABLE IF NOT EXISTS user_category_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            alias       TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            label       TEXT    DEFAULT '',
            usage_count INTEGER DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, alias),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_user_category_rules_user
            ON user_category_rules(user_id, alias)
        ''')

        conn.execute('''CREATE TABLE IF NOT EXISTS portfolio_holdings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL,
            instrument_key      TEXT    NOT NULL,
            instrument_label    TEXT    NOT NULL,
            isin                TEXT    NOT NULL,
            price_symbol        TEXT,
            monthly_contribution REAL   NOT NULL DEFAULT 0.0,
            total_invested      REAL,
            start_price         REAL,
            last_price          REAL,
            last_checked_at     DATETIME,
            started_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, instrument_key),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )''')

        conn.execute('''CREATE INDEX IF NOT EXISTS idx_portfolio_holdings_user
            ON portfolio_holdings(user_id)
        ''')

        # Migration fuer bereits bestehende portfolio_holdings-Tabellen (falls schon deployed
        # ohne diese Spalten) - sicher/idempotent, ueberspringt bereits vorhandene Spalten.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_holdings)").fetchall()}
        for col, ddl in [
            ("price_symbol", "ALTER TABLE portfolio_holdings ADD COLUMN price_symbol TEXT"),
            ("start_price", "ALTER TABLE portfolio_holdings ADD COLUMN start_price REAL"),
            ("last_price", "ALTER TABLE portfolio_holdings ADD COLUMN last_price REAL"),
            ("last_checked_at", "ALTER TABLE portfolio_holdings ADD COLUMN last_checked_at DATETIME"),
            ("total_invested", "ALTER TABLE portfolio_holdings ADD COLUMN total_invested REAL"),
        ]:
            if col not in existing_cols:
                conn.execute(ddl)

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
    for coin_name, aliases in CRYPTO_COINS:
        if any(alias in text_lower for alias in aliases):
            return "crypto", coin_name
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


def parse_percent(text: str):
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
        return value if value >= 0 else None
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


def normalize_recurring_amount(text_lower: str, amount: float) -> tuple[float, str]:
    annual_markers = [
        "jährlich", "jaehrlich", "pro jahr", "im jahr", "jahr",
        "jahresabo", "jahresbeitrag", "jahresgebühr", "jahresgebuehr",
        "jährliche", "jaehrliche",
    ]
    monthly_markers = ["im monat", "monatlich", "pro monat"]
    if any(marker in text_lower for marker in annual_markers) and not any(marker in text_lower for marker in monthly_markers):
        return amount / 12, f"{format_eur(amount)} jährlich umgerechnet."
    return amount, ""


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
    normalized = text_lower.strip().lstrip("/").strip()
    return normalized in {"zurueck", "zurück", "back"} or normalized.startswith(("zurueck ", "zurück ", "back "))


def merge_number_defaults(parsed: dict, nums: list, keys: list) -> dict:
    if parsed:
        return parsed

    if len(nums) >= len(keys):
        return {key: nums[index] for index, key in enumerate(keys)}
    return {"gesamt": sum(nums)}


def route_cross_step_fixed_cost(text_input: str, text_lower: str, nums: list, details: dict) -> bool:
    if not nums:
        return False

    credit_parsed = parse_labeled_amounts(text_input, {
        "schuld": "kredit",
        "schulden": "kredit",
        "kredit": "kredit",
        "kreditrate": "kredit",
        "kredite": "kredit",
        "darlehen": "kredit",
    })
    if credit_parsed:
        current = details.get("kredite", {})
        if not isinstance(current, dict):
            current = {}
        monthly_debt, total_debt = parse_debt_values(text_lower)
        if monthly_debt is not None:
            credit_parsed["kredit"] = monthly_debt
        if total_debt is not None:
            current["restschuld"] = total_debt
        current.update(credit_parsed)
        details["kredite"] = current
        return True

    return False


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
    "handyvertrag": ("abos", "handyvertrag", "Handyvertrag"),
    "handyvertag": ("abos", "handyvertrag", "Handyvertrag"),
    "mobilfunkvertrag": ("abos", "handyvertrag", "Handyvertrag"),
    "icloud": ("abos", "icloud", "iCloud"),
    "abo": ("abos", "abo", "Abo"),
    "abos": ("abos", "abo", "Abo"),
    "abonnement": ("abos", "abo", "Abo"),
    "jahresabo": ("abos", "abo", "Abo"),
    "mitgliedschaft": ("abos", "abo", "Mitgliedschaft"),
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
    "kreditrate": ("kredite", "kredit", "Kreditrate"),
    "kredite": ("kredite", "kredit", "Kredit"),
    "schuld": ("kredite", "kredit", "Schulden"),
    "schulden": ("kredite", "kredit", "Schulden"),
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
        for key, value in section.items()
        if key not in {"restschuld", "gesamtbetrag", "schulden_gesamt"}
    )


def parse_debt_values(text_lower: str) -> tuple:
    monthly = None
    total = None

    total_patterns = [
        r"(?:gesamtbetrag|gesamtschuld|restschuld|offene schuld|offen|gesamt)\D{0,20}(\d+(?:[.,]\d+)?\s*k?)",
        r"(\d+(?:[.,]\d+)?\s*k?)\D{0,20}(?:gesamtbetrag|gesamtschuld|restschuld|offen)",
    ]
    monthly_patterns = [
        r"(?:monatlich|jeden monat|pro monat|rate|kreditrate|kostet monatlich)\D{0,20}(\d+(?:[.,]\d+)?\s*k?)",
        r"(\d+(?:[.,]\d+)?\s*k?)\D{0,20}(?:monatlich|jeden monat|pro monat|rate|kreditrate)",
    ]

    def parse_token(token: str):
        return parse_currency(token)

    for pattern in total_patterns:
        match = re.search(pattern, text_lower)
        if match:
            total = parse_token(match.group(1))
            break

    for pattern in monthly_patterns:
        match = re.search(pattern, text_lower)
        if match:
            monthly = parse_token(match.group(1))
            break

    return monthly, total


def find_detail_alias_matches(text_lower: str) -> list:
    matches = []
    for alias, (section, key, label) in DETAIL_VALUE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text_lower):
            matches.append((len(alias), section, key, label))
    return matches


def unique_detail_key(existing: dict, base_key: str) -> str:
    if base_key not in existing:
        return base_key
    index = 2
    while f"{base_key}_{index}" in existing:
        index += 1
    return f"{base_key}_{index}"


def derive_generic_abo_key_label(text_lower: str, existing: dict) -> tuple[str, str]:
    """Macht aus 'Anthropic 20€ als Abo' einen eigenen Abo-Eintrag."""
    cleaned = re.sub(r"\d+(?:[.,]\d+)?\s*(?:€|eur|euro)?", " ", text_lower)
    stop_words = {
        "füge", "fuege", "hinzu", "noch", "neue", "neuer", "neues", "neu",
        "ergänze", "ergaenze", "nimm", "auf", "setze", "setz",
        "im", "in", "pro", "monat", "monatlich", "als", "zu", "meinen",
        "mein", "meine", "abo", "abos", "abonnement", "jahresabo",
        "vertrag", "abgeschlossen", "kostet",
    }
    words = [
        word for word in re.findall(r"[a-zäöüßA-ZÄÖÜ0-9]+", cleaned)
        if word.lower() not in stop_words and len(word) > 1
    ]
    if not words:
        return unique_detail_key(existing, "abo"), "Abo"

    raw_name = "_".join(words[:3]).lower()
    key = re.sub(r"[^a-z0-9äöüß_]+", "", raw_name).strip("_") or "abo"
    label = " ".join(words[:3]).strip().title()
    label = label.replace("Handyvertag", "Handyvertrag")
    return unique_detail_key(existing, key), label


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


def is_investment_outflow_request(text_lower: str) -> bool:
    outflow_words = [
        "verkauft", "verkauf", "verkaufe", "entnommen", "entnehme",
        "ausgezahlt", "auszahlung", "abgezogen", "abziehen",
        "lösche", "loesche", "entferne", "streiche", "korrigiere",
        "rückgängig", "rueckgaengig", "zurücknehmen", "zuruecknehmen",
    ]
    return any(word in text_lower for word in outflow_words)


def is_investment_asset_update_request(text_lower: str) -> bool:
    explicit_update_words = [
        "vergessen", "nachtrag", "nachtragen", "nachgetragen",
        "bestandskorrektur", "bestand", "bestehend", "bestehendes",
        "bereits", "schon gehabt", "hatte ich schon",
        "zu investments", "zu meinen investments", "bei investments",
        "zu meinem depot", "bei meinem depot", "aktuell investiertes vermögen",
        "aktuell investiertes vermoegen",
    ]
    if any(word in text_lower for word in explicit_update_words):
        return True

    existing_asset_phrases = [
        "ich habe noch", "ich hab noch", "ich besitze noch",
        "liegt noch", "liegen noch", "bei mir sind noch",
    ]
    new_month_phrases = [
        "diesen monat", "heute", "gerade", "neu gekauft",
        "gekauft", "kauf", "nachgekauft", "sparplan",
    ]
    if any(phrase in text_lower for phrase in existing_asset_phrases):
        return not any(phrase in text_lower for phrase in new_month_phrases)

    return False


def is_clear_new_investment_request(text_lower: str) -> bool:
    new_month_phrases = [
        "diesen monat", "heute", "gerade", "neu gekauft", "gekauft",
        "nachgekauft", "kauf", "sparplan", "rate", "monatlich",
    ]
    return any(phrase in text_lower for phrase in new_month_phrases)


def should_confirm_investment_classification(text_lower: str, amount: float, direction: str) -> bool:
    if direction != "in" or amount < 1000:
        return False
    if is_investment_asset_update_request(text_lower) or is_clear_new_investment_request(text_lower):
        return False
    return True


def investment_classification_choice(text_lower: str):
    existing_words = [
        "bestand", "bestehend", "bestehendes", "nachtrag", "nachgetragen",
        "vergessen", "korrektur", "startwert", "hatte ich schon", "schon gehabt",
        "nur erfassen", "nur nachtragen",
    ]
    new_words = [
        "neu", "monatsfortschritt", "diesen monat", "gekauft", "nachgekauft",
        "investiert", "kauf", "frisch",
    ]
    if any(word in text_lower for word in existing_words):
        return "asset_update"
    if any(word in text_lower for word in new_words):
        return "one_time"
    return None


def apply_investment_change(uid: int, u: dict, amount_val: float, text_lower: str,
                            direction: str, event_type=None):
    current_investments = u.get("current_investments") or 0
    new_investments = max(0.0, current_investments - amount_val) if direction == "out" else current_investments + amount_val
    update_user_field(uid, "current_investments", new_investments)
    asset_type, asset_name = detect_investment_asset(text_lower)
    if event_type is None:
        is_asset_update = is_investment_asset_update_request(text_lower)
        is_correction = direction == "out" and any(
            word in text_lower
            for word in ["lösche", "loesche", "entferne", "streiche", "korrigiere", "rückgängig", "rueckgaengig"]
        )
        if is_correction:
            event_type = "correction"
        elif is_asset_update:
            event_type = "asset_update"
        else:
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
    cp_str = build_tracking_note(cp_earned)
    cp_suffix = f" · {cp_str}" if cp_str else ""
    total_wealth = new_investments + (u.get("current_cash") or 0)
    if event_type == "asset_update":
        verb = "Bestand ergänzt"
    elif event_type == "correction":
        verb = "Investment korrigiert"
    else:
        verb = "Investment verkauft/entnommen" if direction == "out" else "Investment erfasst"
    message = (
        f"📈 {verb}: *{amount_val:.2f}€*\n"
        f"Investments: {new_investments:.2f}€\n"
        f"Nettovermögen: *{total_wealth:.2f}€*{cp_suffix}"
    )
    return message, new_badges


def is_profile_info_question(text_lower: str) -> bool:
    """Erkennt kurze Alltagsfragen zum Profil, bevor sie als Änderung landen."""
    normalized = re.sub(r"\s+", " ", text_lower.strip())
    if re.search(r"\b(wv|wieviel|wie viel|wieviele|wie viele)\b", normalized):
        return True
    question_markers = [
        "wie viel", "wieviel", "wie viele", "wieviele", "wie hoch", "was ist", "was zahle",
        "was zahl", "wieviel zahle", "wieviel zahl", "wie viel zahle",
        "wie viel zahl", "was kostet", "kosten meine", "zahle ich",
        "zahl ich", "bezahle ich", "was habe ich", "habe ich",
        "wv ", "wv.", "wv?", "w v ",
    ]
    return any(marker in normalized for marker in question_markers)


def maybe_apply_profile_correction(user_id: int, u: dict, text_lower: str) -> str:
    if is_profile_info_question(text_lower):
        return ""
    correction_words = [
        "ändere", "aendere", "änder", "aender", "korrigiere", "korrektur",
        "setze", "setz", "aktualisiere", "update", "füge", "fuege",
        "hinzu", "ergänze", "ergaenze", "nimm auf",
        "neu", "neue", "neuer", "neues",
        "lösche", "loesche", "entferne", "streiche", "kündige", "kuendige",
        "gekündigt", "gekuendigt", "abbezahlt", "abgezahlt",
    ]
    has_correction_word = any(word in text_lower for word in correction_words)
    has_recurring_context = any(phrase in text_lower for phrase in [
        "im monat", "monatlich", "pro monat", "jährlich", "jaehrlich",
        "pro jahr", "im jahr", "jahresabo", "jahresbeitrag",
    ])
    if not has_correction_word and not has_recurring_context:
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

    amounts = extract_amounts(text_lower, exclude_years=True)
    if not amounts:
        return ""

    if alias_matches:
        _length, section, key, label = max(alias_matches, key=lambda item: item[0])
        raw_value = float(amounts[-1])
        value, recurrence_note = normalize_recurring_amount(text_lower, raw_value)
        details = u.get("details", {})
        if not isinstance(details, dict):
            details = {}
        section_values = details.get(section, {})
        if not isinstance(section_values, dict):
            section_values = {}
        if section == "kredite":
            monthly_debt, total_debt = parse_debt_values(text_lower)
            if monthly_debt is not None:
                value = monthly_debt
            if total_debt is not None:
                section_values["restschuld"] = total_debt
        if section == "abos" and key == "abo":
            key, label = derive_generic_abo_key_label(text_lower, section_values)
        section_values[key] = value
        details[section] = section_values

        update_user_field(user_id, "fixed_costs_details", json.dumps(details))
        total_fixed = fixed_costs_total(details)
        if total_fixed > 0:
            update_user_field(user_id, "fixed_costs", total_fixed)

        return (
            "Alles klar, ich habe das aktualisiert.\n\n"
            f"{label}: {format_eur(value)} pro Monat\n"
            f"{'Restschuld: ' + format_eur(section_values['restschuld']) + chr(10) if section == 'kredite' and section_values.get('restschuld') is not None else ''}"
            f"{recurrence_note + chr(10) if recurrence_note else ''}"
            f"Fixkosten gesamt: {format_eur(total_fixed)}\n\n"
            "Ich nutze das ab jetzt für deine Auswertung."
        )

    return ""


def detect_future_effective_month(text_lower: str) -> str:
    if any(word in text_lower for word in ["ab jetzt", "sofort", "direkt", "ab sofort"]):
        return ""

    relative_patterns = [
        "nächsten monat", "naechsten monat", "nächste monat", "naechste monat",
        "kommenden monat", "naechste monat", "ab nächsten", "ab naechsten",
    ]
    if any(pattern in text_lower for pattern in relative_patterns):
        return "nächsten Monat"

    month_aliases = {
        "januar": "Januar",
        "februar": "Februar",
        "märz": "März",
        "maerz": "März",
        "april": "April",
        "mai": "Mai",
        "juni": "Juni",
        "juli": "Juli",
        "august": "August",
        "september": "September",
        "oktober": "Oktober",
        "november": "November",
        "dezember": "Dezember",
    }
    for token, label in month_aliases.items():
        if re.search(rf"\b(ab|für|fuer|zum|ab dem)\s+{token}\b", text_lower):
            return label

    return ""


def maybe_apply_savings_correction(user_id: int, u: dict, text_lower: str) -> str:
    if not any(word in text_lower for word in ["ändere", "aendere", "setze", "aktualisiere", "update", "korrigiere", "jetzt"]):
        return ""
    has_savings_context = any(word in text_lower for word in ["sparrate", "sparplan", "sparen"])
    has_direct_etf_context = bool(re.search(r"\betf\b", text_lower)) and not looks_like_investment_update(text_lower.replace("etf", ""))
    has_direct_cash_context = any(word in text_lower for word in ["cash", "tagesgeld", "rücklage", "ruecklage"])
    if not (has_savings_context or has_direct_etf_context or has_direct_cash_context):
        return ""

    target = None
    label = ""
    if any(word in text_lower for word in ["etf", "investment", "investments", "depot", "sparplan"]):
        target = "etf_savings"
        label = "ETF-Sparrate"
    elif any(word in text_lower for word in ["cash", "tagesgeld", "rücklage", "ruecklage"]):
        target = "cash_savings"
        label = "Cash-Sparrate"

    if target is None:
        return (
            "Sag mir kurz, welche Sparrate ich ändern soll.\n\n"
            "Zum Beispiel:\n"
            "`ändere ETF-Sparrate auf 10%`\n"
            "`ändere Cash-Sparrate auf 200€`"
        )

    future_month = detect_future_effective_month(text_lower)
    if future_month:
        return (
            f"Ich habe verstanden: {label} ab {future_month}.\n\n"
            "Wichtig: Zukunftsänderungen merke ich aktuell noch nicht automatisch vor.\n"
            "Deshalb ändere ich deinen aktuellen Monat nicht heimlich.\n\n"
            "Wenn es wirklich ab jetzt gelten soll, schreib zum Beispiel:\n"
            f"`{label} ab jetzt 300€`"
        )

    percent_value = parse_percent(text_lower)
    if percent_value is not None:
        income_base = (u.get("income") or 0) + (u.get("other_income") or 0)
        if income_base <= 0:
            return "Ich brauche dafür dein Einkommen. Schreib die Sparrate bitte als Euro-Betrag."
        value = round(income_base * percent_value / 100, 2)
        note = f"{percent_value:g}% entsprechen {format_eur(value)} pro Monat."
    else:
        value = parse_currency(text_lower)
        if value is None:
            return ""
        note = f"{format_eur(value)} pro Monat."

    update_user_field(user_id, target, value)
    return (
        "Alles klar, ich habe das aktualisiert.\n\n"
        f"{label}: {note}\n\n"
        "Ich nutze das ab jetzt für deine Auswertung."
    )


def maybe_apply_income_correction(user_id: int, u: dict, text_lower: str) -> str:
    has_income_change_word = any(word in text_lower for word in [
        "ändere", "aendere", "setze", "aktualisiere", "update", "korrigiere",
        "lohnerhöhung", "lohnerhoehung", "neues", "neue", "jetzt", "beträgt", "betraegt",
    ])
    if not has_income_change_word:
        return ""
    if not any(word in text_lower for word in ["gehalt", "lohn", "netto", "einkommen", "nebeneinkommen", "nebenverdienst"]):
        return ""

    value = parse_currency(text_lower)
    if value is None:
        return ""

    if any(word in text_lower for word in ["nebeneinkommen", "nebenverdienst", "nebenjob"]):
        update_user_field(user_id, "other_income", value)
        label = "Nebeneinkommen"
    else:
        update_user_field(user_id, "income", value)
        label = "Nettoeinkommen"

    return (
        "Alles klar, ich habe das aktualisiert.\n\n"
        f"{label}: {format_eur(value)} pro Monat\n\n"
        "Ich nutze das ab jetzt für deine Auswertung."
    )


def looks_like_profile_correction(text_lower: str) -> bool:
    if is_profile_info_question(text_lower):
        return False
    correction_words = {
        "ändere", "aendere", "änder", "aender", "korrigiere", "korrektur",
        "setze", "setz", "aktualisiere", "update", "füge", "fuege",
        "hinzu", "ergänze", "ergaenze", "nimm auf",
        "neu", "neue", "neuer", "neues",
        "lösche", "loesche", "entferne", "streiche", "kündige", "kuendige",
        "gekündigt", "gekuendigt", "abbezahlt", "abgezahlt",
    }
    if any(word in text_lower for word in correction_words):
        return True
    if is_profile_removal_request(text_lower):
        return True
    if any(phrase in text_lower for phrase in [
        "im monat", "monatlich", "pro monat", "jährlich", "jaehrlich",
        "pro jahr", "im jahr", "jahresabo", "jahresbeitrag",
    ]):
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
    return format_month_duration(months)


def format_month_duration(months: int) -> str:
    months = int(months or 0)
    if months <= 0:
        return "0 Monate"
    if months < 12:
        return "1 Monat" if months == 1 else f"{months} Monate"
    years, rest = divmod(months, 12)
    year_text = "1 Jahr" if years == 1 else f"{years} Jahre"
    if rest == 0:
        return year_text
    month_text = "1 Monat" if rest == 1 else f"{rest} Monate"
    return f"{year_text} und {month_text}"

def extract_merchant_name(text_input: str) -> str:
    """Extrahiert den ersten sinnvollen Begriff als Händlernamen."""
    words = re.findall(r'[a-zA-ZäöüÄÖÜß]+', text_input)
    return words[0].capitalize() if words else "Unbekannt"


def detect_expense_label(text_input: str, text_lower: str, user_id: int = None) -> tuple[str, str, str]:
    personal_rule = find_user_category_rule(user_id, text_lower) if user_id else None
    if personal_rule:
        category, label, _alias = personal_rule
        return category, label, ""

    # Konkrete Händler schlagen generische Wörter wie "Essen" oder "Einkaufen".
    # Beispiel: "11 Euro Essen Lidl" muss Lebensmittel/Lidl werden, nicht Restaurant/Essen.
    for merchant, keys in MERCHANT_KEYWORDS.items():
        if any(key in text_lower for key in keys):
            return CATEGORY_MAPPING.get(merchant, "SONSTIGES"), merchant, ""

    for alias, (category, label) in DIRECT_CATEGORY_INPUTS.items():
        if re.search(rf"\b{re.escape(alias)}\b", text_lower):
            return category, label, label

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text_lower for keyword in keywords):
            return category, extract_merchant_name(text_input), ""

    return "", "", ""


def looks_like_known_expense(text_input: str, text_lower: str, user_id: int = None) -> bool:
    amounts = extract_amounts(text_lower, exclude_years=True)
    if len(amounts) != 1:
        return False
    category, merchant, _direct_label = detect_expense_label(text_input, text_lower, user_id=user_id)
    return bool(category and merchant)


def find_deletable_expense_match(user_id: int, amount: float, category: str):
    """Sucht eine kuerzlich geloggte Ausgabe, die zu einem Loesch-Wunsch passt.
    Bevorzugt einen Treffer mit passender Kategorie, sonst den juengsten Betrags-Treffer."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, amount, category, merchant FROM expenses "
            "WHERE user_id = ? AND amount = ? ORDER BY id DESC LIMIT 5",
            (user_id, amount)
        )
        candidates = cursor.fetchall()
    if not candidates:
        return None
    for row in candidates:
        if category and row["category"] == category:
            return row
    return candidates[0]


def maybe_delete_logged_expense(user_id: int, text_input: str, text_lower: str) -> str:
    """Erkennt 'lösche <Ausgabe> <Betrag>€' und entfernt die passende Buchung.

    Greift bewusst NICHT, wenn ein echter Fixkosten-/Abo-Treffer (find_detail_alias_matches),
    eine Investment-Aktion oder eine Budget-Aenderung gemeint ist - diese haben eigene,
    bestehende Wege und muessen Vorrang behalten."""
    delete_words = ["lösche", "loesche", "entferne", "streiche", "storniere", "stornier"]
    if not any(word in text_lower for word in delete_words):
        return ""
    if "budget" in text_lower or "budgets" in text_lower:
        return ""
    if find_detail_alias_matches(text_lower):
        return ""
    if looks_like_investment_update(text_lower):
        return ""

    amounts = extract_amounts(text_lower, exclude_years=True)
    if len(amounts) != 1:
        return ""
    amount = float(amounts[0])

    category, merchant, _direct_label = detect_expense_label(text_input, text_lower, user_id=user_id)
    if not (category and merchant):
        return ""

    match = find_deletable_expense_match(user_id, amount, category)
    if not match:
        return (
            "Ich konnte keine passende Ausgabe zum Löschen finden.\n\n"
            "Nutze `/undo` für deine letzte Ausgabe, oder nenne Betrag und Händler genauer."
        )

    with get_db() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (match["id"],))
        reverse_app_paid_expense(conn, user_id, match["id"])
        conn.commit()

    return f"🗑️ Gelöscht: {match['merchant']} {match['amount']:.2f}€ · {match['category']}."


def parse_hybrid_expense_items(text_input: str, amounts: list[float], user_id: int = None) -> list[dict]:
    if not amounts:
        return []

    amount_matches = list(re.finditer(r"\b\d+(?:[.,]\d+)?\s*k\b|\b\d+(?:[.,]\d{1,2})?\b", text_input.lower()))
    if len(amount_matches) != len(amounts):
        return []

    items = []
    used_context_spans = set()
    for index, match in enumerate(amount_matches):
        prev_end = amount_matches[index - 1].end() if index > 0 else 0
        next_start = amount_matches[index + 1].start() if index + 1 < len(amount_matches) else len(text_input)
        context_options = [
            (prev_end, match.start(), text_input[prev_end:match.start()].strip(" ,;+-")),
            (match.end(), next_start, text_input[match.end():next_start].strip(" ,;+-")),
        ]
        selected = None
        for start, end, context in context_options:
            if not context or (start, end) in used_context_spans:
                continue
            category, merchant, direct_label = detect_expense_label(context, context.lower(), user_id=user_id)
            if category and merchant:
                selected = (start, end, category, merchant, direct_label)
                break
        if not selected:
            return []

        start, end, category, merchant, direct_label = selected
        used_context_spans.add((start, end))
        items.append({
            "amount": amounts[index],
            "category": category,
            "merchant": direct_label or merchant,
        })
    return items


def build_unclear_amount_answer(amounts: list[float]) -> str:
    if not amounts:
        return build_not_understood_answer()
    amount_text = ", ".join(format_eur(amount) for amount in amounts[:3])
    return (
        "Ich sehe den Betrag, aber mir fehlt noch, wofür er war.\n\n"
        f"Erkannt: {amount_text}\n\n"
        "Schreib es kurz mit Händler oder Kategorie, zum Beispiel:\n"
        "`Lidl 34€`\n"
        "`Tanken 60€`\n"
        "`Restaurant 20€`"
    )


def looks_like_unclear_expense_attempt(text_lower: str, amounts: list[float]) -> bool:
    if not amounts:
        return False
    if text_lower.startswith(("wie ", "was ", "warum ", "wann ", "wo ", "wer ", "kannst du")):
        return False
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]+", text_lower)
    filler_words = {
        "euro", "eur", "ausgabe", "ausgaben", "bezahlt", "gekauft",
        "für", "fuer", "bei", "im", "in", "am", "an", "der", "die", "das",
        "ich", "habe", "hab", "eine", "einen", "ein", "und",
    }
    meaningful_words = [word for word in words if word not in filler_words]

    # Nur echte Betragsfetzen blocken. Sobald ein sinnvoller Begriff dabei ist,
    # darf die KI später als Kategorisierer helfen.
    return len(meaningful_words) == 0


def get_actor_id(message) -> int:
    """User-ID fuer Admin-Pruefungen; in Gruppen ist chat.id nicht zwingend die User-ID."""
    return message.from_user.id if getattr(message, "from_user", None) else message.chat.id


ADMIN_COMMANDS = {
    "/admin", "/pending", "/approve", "/revoke", "/adminusers",
    "/health", "/reportjobs", "/backupnow", "/testreport", "/nudge_inactive", "/testrecap",
    "/announce_rename",
}

BETA_NUDGE_TEXT = (
    "Kurzer Beta-Check 🙏\n\n"
    "Viele lesen am Anfang nicht alles, deshalb ganz kurz:\n\n"
    "Du musst nicht perfekt tracken.\n"
    "Schreib Rov.E einfach 2–3 Ausgaben am Tag, z.B.:\n\n"
    "Lidl 12€\n"
    "Tanken 40€\n"
    "Döner 8€\n\n"
    "Das reicht schon, damit der Monatsreport später Sinn ergibt.\n\n"
    "Meine Bitte:\n"
    "Teste Rov.E heute einmal mit 2 echten Ausgaben.\n\n"
    "Wenn irgendwas nervt oder unklar ist, schick mir einfach Screenshot.\n"
    "Genau dafür ist die Beta da."
)

RENAME_ANNOUNCEMENT_TEXT = (
    "Kurze Info:\n\n"
    "Clarity heißt ab jetzt *Rov.E*.\n\n"
    "Für dich ändert sich nichts an der Nutzung.\n"
    "Du kannst weiter wie gewohnt Ausgaben schreiben, Fragen stellen und deinen Report bekommen.\n\n"
    "Nur Name und Profilbild werden Schritt für Schritt angepasst."
)


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


def format_admin_identity(row) -> str:
    display_name = (row["display_name"] if "display_name" in row.keys() else "") or ""
    username = (row["username"] if "username" in row.keys() else "") or ""
    if display_name and username:
        return f"{display_name} ({username})"
    return display_name or username or "Unbekannt"


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
        "Neue Rov.E-Freigabe wartet:\n\n"
        f"Name: {display_name or 'Unbekannt'}\n"
        f"Username: {username or '-'}\n"
        f"ID: {user_id}\n\n"
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
            "Dein Zugang zu Rov.E ist aktuell nicht freigeschaltet. Bitte wende dich an den Support."
        )
        return False

    bot.send_message(
        message.chat.id,
        "Dein Zugang ist angefragt.\n\n"
        "Rov.E ist aktuell im Testlauf. Sobald du freigegeben bist, kannst du direkt starten."
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
    savings = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
    total_expenses = get_month_expenses(user_id)
    remaining = income - fixed - savings - total_expenses
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
    asks_amount = any(w in text_lower for w in [
        "wie viel", "wieviel", "ausgegeben", "ausgaben", "geld", "kosten",
        "summe", "gesamt", "bisher", "stand",
    ])
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


def is_expense_overview_request(text_lower: str) -> bool:
    """Erkennt den Wunsch nach einer echten Buchungsliste vor dem KI-Fallback."""
    has_expense_context = any(word in text_lower for word in [
        "ausgabe", "ausgaben", "buchung", "buchungen", "eingetragen",
    ])
    asks_overview = any(word in text_lower for word in [
        "zeig", "zeige", "übersicht", "uebersicht", "auflistung", "auflisten",
        "aufstellung", "alle", "detaill", "detail",
    ])
    # Eine direkte Folgefrage wie "Ich will eine detaillierte Übersicht" soll ebenfalls
    # funktionieren, auch wenn der Nutzer das Wort Ausgaben nicht erneut schreibt.
    standalone_detail = "übersicht" in text_lower and "detaill" in text_lower
    return (has_expense_context and asks_overview) or standalone_detail


def build_expense_overview(user_id: int, text_lower: str) -> str:
    """Gibt eine vollständige, DB-basierte Ausgabenübersicht statt eines KI-Auszuges aus."""
    lifetime_markers = ["alle eingetragen", "komplett", "insgesamt", "seit start", "seitdem"]
    is_lifetime = any(marker in text_lower for marker in lifetime_markers) or (
        "alle" in text_lower and "eingetragen" in text_lower
    )
    where_period = "" if is_lifetime else (
        "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')"
    )
    period_label = "seit deinem Start" if is_lifetime else "diesen Monat"

    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT amount, category, merchant, created_at
                  FROM expenses
                 WHERE user_id = ? {where_period}
                 ORDER BY created_at DESC, id DESC
                 LIMIT 100""",
            (user_id,),
        ).fetchall()

    if not rows:
        return f"Ich habe {period_label} noch keine Ausgaben gefunden."

    total = sum(float(row["amount"] or 0) for row in rows)
    categories: dict[str, float] = {}
    for row in rows:
        category = row["category"] or "SONSTIGES"
        categories[category] = categories.get(category, 0.0) + float(row["amount"] or 0)

    title = "Deine Ausgaben seit deinem Start" if is_lifetime else "Deine Ausgaben diesen Monat"
    lines = [f"*{title}*", f"{len(rows)} Buchungen · {format_eur(total)}", ""]
    for category, amount in sorted(categories.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"{CATEGORY_EMOJIS.get(category, '•')} {category_label(category)}: {format_eur(amount)}")

    lines.extend(["", "*Alle Buchungen*"])
    for row in rows:
        created = str(row["created_at"] or "")[:10]
        date_label = created[8:10] + "." + created[5:7] + "." if len(created) == 10 else ""
        merchant = (row["merchant"] or category_label(row["category"] or "SONSTIGES")).strip()
        line = f"• {date_label} {merchant}: {format_eur(row['amount'])}"
        # Telegram erlaubt zwar lange Nachrichten, aber eine Übersicht soll lesbar bleiben.
        if len("\n".join(lines + [line])) > 3600:
            lines.append("… weitere Buchungen sind vorhanden.")
            break
        lines.append(line)
    return "\n".join(lines)


BUDGET_CORE_CATEGORIES = ["LEBENSMITTEL", "RESTAURANTS", "FREIZEIT", "SHOPPING", "MOBILITAET"]


def current_budget_month() -> str:
    return date.today().strftime("%Y-%m")


def budget_marker_key(action: str) -> str:
    month_key = date.today().strftime("%Y_%m")
    return f"budget_{action}_{month_key}"


def remember_budget_marker(user_id: int, action: str) -> bool:
    marker = budget_marker_key(action)
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO user_badges (user_id, badge_key) VALUES (?, ?)",
                (user_id, marker)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def has_budget_marker(user_id: int, action: str) -> bool:
    return has_badge(user_id, budget_marker_key(action))


def normalize_budget_categories(text_lower: str) -> list:
    alias, categories = detect_category_alias(text_lower)
    if categories:
        return categories

    category = normalize_category_name(text_lower)
    if category:
        return [category]
    return []


def save_category_budget(user_id: int, category: str, monthly_limit: float, source: str = "manual") -> None:
    month = current_budget_month()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO category_budgets
               (user_id, category, monthly_limit, source, active_month)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, category, active_month)
               DO UPDATE SET monthly_limit = excluded.monthly_limit,
                             source = excluded.source,
                             created_at = CURRENT_TIMESTAMP""",
            (user_id, category, monthly_limit, source, month)
        )
        conn.commit()


def get_category_budgets(user_id: int, month: str = None) -> list:
    month = month or current_budget_month()
    with get_db() as conn:
        return conn.execute(
            """SELECT category, monthly_limit, source
               FROM category_budgets
               WHERE user_id = ? AND active_month = ?
               ORDER BY category""",
            (user_id, month)
        ).fetchall()


def delete_category_budgets(user_id: int, categories: list = None, month: str = None) -> int:
    month = month or current_budget_month()
    with get_db() as conn:
        if categories:
            placeholders = ",".join("?" for _ in categories)
            cursor = conn.execute(
                f"""DELETE FROM category_budgets
                    WHERE user_id = ? AND active_month = ? AND category IN ({placeholders})""",
                (user_id, month, *categories)
            )
        else:
            cursor = conn.execute(
                """DELETE FROM category_budgets
                   WHERE user_id = ? AND active_month = ?""",
                (user_id, month)
            )
        conn.commit()
        return cursor.rowcount or 0


def has_active_budgets(user_id: int) -> bool:
    return bool(get_category_budgets(user_id))


def get_category_spending(user_id: int, categories: list, month: str = None) -> dict:
    if not categories:
        return {}
    month = month or current_budget_month()
    placeholders = ",".join("?" for _ in categories)
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT category, SUM(amount) AS total
                FROM expenses
                WHERE user_id = ?
                  AND category IN ({placeholders})
                  AND strftime('%Y-%m', created_at) = ?
                GROUP BY category""",
            (user_id, *categories, month)
        ).fetchall()
    return {row["category"]: float(row["total"] or 0) for row in rows}


def build_budget_frame(user_id: int, u: dict = None) -> dict:
    u = u or get_or_create_user(user_id)
    budgets = get_category_budgets(user_id)
    categories = [row["category"] for row in budgets]
    spending = get_category_spending(user_id, categories)
    free_month = (
        (u.get("income") or 0)
        + (u.get("other_income") or 0)
        - (u.get("fixed_costs") or 0)
        - (u.get("etf_savings") or 0)
        - (u.get("cash_savings") or 0)
    )
    total_expenses = get_month_expenses(user_id)
    free_remaining = free_month - total_expenses

    items = []
    allocated_open = 0.0
    total_limits = 0.0
    for row in budgets:
        category = row["category"]
        limit = float(row["monthly_limit"] or 0)
        used = spending.get(category, 0.0)
        left = limit - used
        allocated_open += max(0.0, left)
        total_limits += limit
        items.append({
            "category": category,
            "limit": limit,
            "used": used,
            "left": left,
        })

    planned_buffer = free_month - total_limits
    current_buffer = free_remaining - allocated_open
    return {
        "items": items,
        "free_month": free_month,
        "free_remaining": free_remaining,
        "planned_buffer": planned_buffer,
        "current_buffer": current_buffer,
    }


def build_budget_invite_message() -> str:
    return (
        "Du hast jetzt 7 aktive Tracking-Tage mit Rov.E gesammelt.\n\n"
        "Ich kann daraus einen ersten Budgetrahmen für deinen Monat ableiten.\n"
        "Nicht als harte Grenze, sondern als Frühwarnsystem.\n\n"
        "Wenn du möchtest, schlage ich dir Budgets für die wichtigsten Bereiche vor:\n"
        "Lebensmittel, Restaurants, Freizeit, Shopping und Mobilität.\n\n"
        "Schreib einfach:\n"
        "`Ja` - wenn Rov.E dir einen Vorschlag machen soll\n"
        "`Selbst` - wenn du deine Budgets selbst setzen willst\n"
        "`Nein` - wenn du erstmal ohne Budgets weitermachen willst"
    )


def maybe_send_budget_invite(user_id: int, bot_instance) -> None:
    if has_budget_marker(user_id, "invite_sent") or has_budget_marker(user_id, "resolved"):
        return
    if has_active_budgets(user_id):
        remember_budget_marker(user_id, "resolved")
        return
    if get_tracking_days_90(user_id) < 7:
        return
    if remember_budget_marker(user_id, "invite_sent"):
        bot_instance.send_message(user_id, build_budget_invite_message(), parse_mode="Markdown")


def parse_budget_setup_intent(text_lower: str) -> str:
    if any(phrase in text_lower for phrase in [
        "selbst", "selber", "eigene budget", "eigene budgets", "ich setze",
        "ich mach", "ich mache", "manuell",
    ]):
        return "self"
    if any(phrase in text_lower for phrase in [
        "nein", "nee", "ne ", "erstmal nicht", "nicht jetzt", "später",
        "spaeter", "kein budget", "ohne budget",
    ]):
        return "no"
    if any(phrase in text_lower for phrase in [
        "ja", "ja bitte", "ja klar", "mach mal", "schlag vor",
        "vorschlag", "setz du", "erstell", "mach du", "rov.e soll",
        "du kannst", "gerne",
    ]):
        return "yes"
    return ""


def calculate_suggested_budgets(u: dict) -> tuple:
    income = (u.get("income") or 0) + (u.get("other_income") or 0)
    fixed = u.get("fixed_costs") or 0
    savings = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
    free_month = max(0.0, income - fixed - savings)
    allocatable = free_month * 0.85
    weights = {
        "LEBENSMITTEL": 0.38,
        "RESTAURANTS": 0.18,
        "FREIZEIT": 0.15,
        "SHOPPING": 0.17,
        "MOBILITAET": 0.12,
    }
    budgets = {}
    for category, weight in weights.items():
        budgets[category] = round((allocatable * weight) / 10) * 10
    buffer_amount = max(0.0, free_month - sum(budgets.values()))
    return budgets, free_month, buffer_amount


def apply_suggested_budgets(user_id: int, u: dict) -> str:
    budgets, free_month, buffer_amount = calculate_suggested_budgets(u)
    if free_month <= 0:
        remember_budget_marker(user_id, "resolved")
        return (
            "Ich kann dir gerade keinen sauberen Budgetrahmen vorschlagen, weil dein freies Monatsbudget rechnerisch bei 0€ oder darunter liegt.\n\n"
            "Frag mich am besten zuerst nach deinem Restbudget oder prüfe deine Fixkosten und Sparrate."
        )

    for category, amount in budgets.items():
        if amount > 0:
            save_category_budget(user_id, category, amount, source="suggested")
    remember_budget_marker(user_id, "resolved")

    lines = [
        "Ich habe dir einen ersten Budgetrahmen gesetzt.",
        "",
        "Ich rechne dafür:",
        "Einkommen - Fixkosten - Sparrate = freies Monatsbudget",
        "",
        f"Freies Monatsbudget: {format_eur(free_month)}",
        "",
        "Mein Vorschlag:",
    ]
    for category in BUDGET_CORE_CATEGORIES:
        emoji = CATEGORY_EMOJIS.get(category, "")
        lines.append(f"{emoji} {category}: {format_eur(budgets.get(category, 0))}")
    lines.extend([
        f"Reservierter Puffer: {format_eur(buffer_amount)}",
        "",
        "Das ist kein starres Limit.",
        "Es ist ein Frühwarnsystem für deinen Monat.",
        "",
        "Wenn sich eine Kategorie falsch anfühlt, pass sie einfach an.",
        "Du musst nicht alles übernehmen.",
        "",
        "Du kannst jederzeit schreiben:",
        "`Setz Restaurants auf 150€`",
        "`Shopping lieber 200€`",
        "`Wie viel Budget habe ich noch für Essen?`",
    ])
    return "\n".join(lines)


def build_manual_budget_help() -> str:
    return (
        "Alles klar. Du kannst deine Budgets selbst setzen.\n\n"
        "Schreib zum Beispiel:\n"
        "`Setz Lebensmittel auf 300€`\n"
        "`Setz Restaurants auf 150€`\n"
        "`Setz Freizeit auf 100€`\n\n"
        "Danach kannst du jederzeit fragen:\n"
        "`Wie viel Budget habe ich noch für Essen?`\n"
        "`Zeig meine Budgets`"
    )


def maybe_handle_budget_setup_response(user_id: int, u: dict, text_lower: str) -> str:
    if not has_budget_marker(user_id, "invite_sent") or has_budget_marker(user_id, "resolved"):
        return ""

    intent = parse_budget_setup_intent(text_lower)
    if intent == "yes":
        return apply_suggested_budgets(user_id, u)
    if intent == "self":
        remember_budget_marker(user_id, "resolved")
        return build_manual_budget_help()
    if intent == "no":
        remember_budget_marker(user_id, "resolved")
        return "Alles klar. Dann laufen wir erstmal ohne feste Budgets weiter."
    return ""


def maybe_apply_manual_budget(user_id: int, text_lower: str) -> str:
    budget_adjust_words = [
        "budget", "setz", "setze", "lieber", "bitte", "auf",
        "mach", "ändere", "aendere", "erhöhe", "erhoehe", "senk", "reduzier",
    ]
    if not any(word in text_lower for word in budget_adjust_words):
        return ""
    amount = parse_currency(text_lower)
    if amount is None or amount <= 0:
        return ""
    categories = normalize_budget_categories(text_lower)
    if not categories:
        return ""

    for category in categories:
        save_category_budget(user_id, category, amount, source="manual")
    remember_budget_marker(user_id, "resolved")

    if len(categories) == 1:
        emoji = CATEGORY_EMOJIS.get(categories[0], "")
        return f"Alles klar. {emoji} {categories[0]} ist jetzt auf {format_eur(amount)} pro Monat gesetzt."

    return f"Alles klar. Ich habe das Essensbudget auf {format_eur(amount)} pro Monat gesetzt."


def maybe_delete_budget(user_id: int, text_lower: str) -> str:
    if "budget" not in text_lower and "budgets" not in text_lower:
        return ""
    delete_words = [
        "lösch", "loesch", "lösche", "loesche", "entfern", "streiche",
        "weg", "kein", "keine", "nicht mehr", "doch kein",
    ]
    if not any(word in text_lower for word in delete_words):
        return ""

    categories = normalize_budget_categories(text_lower)
    if categories:
        deleted = delete_category_budgets(user_id, categories)
        if deleted <= 0:
            return "Für diese Kategorie war kein Budget gesetzt."
        if len(categories) == 1:
            emoji = CATEGORY_EMOJIS.get(categories[0], "")
            return f"Alles klar. Ich habe das Budget für {emoji} {categories[0]} entfernt."
        return "Alles klar. Ich habe die ausgewählten Budgets entfernt."

    deleted = delete_category_budgets(user_id)
    if deleted <= 0:
        return "Du hast aktuell keine Budgets für diesen Monat gesetzt."
    return (
        "Alles klar. Ich habe deine Budgets für diesen Monat entfernt.\n\n"
        "Wir laufen erstmal ohne feste Budgetrahmen weiter."
    )


def maybe_answer_budget_status(user_id: int, text_lower: str, u: dict = None) -> str:
    asks_budget = "budget" in text_lower or "budgets" in text_lower
    if not asks_budget:
        return ""

    frame = build_budget_frame(user_id, u)
    if not frame["items"]:
        return (
            "Du hast noch keine Kategorie-Budgets gesetzt.\n\n"
            "Du kannst zum Beispiel schreiben:\n"
            "`Setz Lebensmittel auf 300€`\n"
            "`Setz Restaurants auf 150€`"
        )

    if "puffer" in text_lower:
        buffer_value = frame["current_buffer"]
        return (
            f"Dein Budget-Puffer liegt aktuell bei *{format_eur(buffer_value)}*.\n\n"
            "Das ist der Teil deines freien Monatsbudgets, der nicht fest an eine Kategorie gebunden ist.\n\n"
            "Wenn du z.B. Lebensmittel von 520€ auf 400€ senkst, wandert die Differenz automatisch in diesen Puffer."
        )

    categories = normalize_budget_categories(text_lower)
    if categories:
        relevant = [item for item in frame["items"] if item["category"] in categories]
        if not relevant:
            return "Für diese Kategorie hast du noch kein Budget gesetzt."
        limit = sum(item["limit"] for item in relevant)
        used = sum(item["used"] for item in relevant)
        left = limit - used
        label = "Essen" if len(categories) > 1 else category_label(categories[0])
        return (
            f"{label}\n\n"
            f"Frei: *{format_eur(left)}*\n"
            f"Rahmen: {format_eur(limit)}\n"
            f"Bisher genutzt: {format_eur(used)}"
        )

    if any(phrase in text_lower for phrase in ["zeig", "zeige", "übersicht", "uebersicht", "meine budgets", "alle budgets"]):
        lines = ["*Deine Budgetrahmen diesen Monat*", ""]
        for item in frame["items"]:
            category = item["category"]
            emoji = CATEGORY_EMOJIS.get(category, "")
            lines.append(f"{emoji} *{category_label(category)}*")
            lines.append(f"Frei: {format_eur(item['left'])} · Rahmen: {format_eur(item['limit'])}")
            if item["used"] > 0:
                lines.append(f"Genutzt: {format_eur(item['used'])}")
            lines.append("")
        lines.append(f"Freier Puffer: *{format_eur(frame['current_buffer'])}*")
        lines.append("")
        lines.append("Der Puffer ist nicht fest verplant. Wenn du eine Kategorie senkst, wird dieser Puffer größer.")
        return "\n".join(lines)

    return ""


def maybe_answer_weekly_budget(user_id: int, u: dict, text_lower: str) -> str:
    if any(phrase in text_lower for phrase in [
        "cp-limit", "cp limit", "rp-limit", "rp limit", "clarity-punkt", "clarity punkt",
        "clarity-punkte", "clarity punkte", "rov.e-punkt", "rov.e punkt",
        "rov.e-punkte", "rov.e punkte", "rove-punkt", "rove punkt",
        "rove-punkte", "rove punkte", "punkte limit", "punktelimit",
    ]):
        return (
            "RP-Limit heißt nur: Du bekommst pro Tag einen Rov.E-Punkt fürs Tracken.\n\n"
            "Deine Ausgaben werden trotzdem ganz normal gespeichert.\n"
            "Ich begrenze nur die Punkte, damit niemand den Score durch viele kleine Eingaben künstlich hochzieht."
        )

    if not any(phrase in text_lower for phrase in [
        "wochenbudget", "wochen budget", "wochenlimit", "wochen limit",
        "tagesbudget", "tages budget", "tageslimit", "tages limit",
        "wie viel kann ich pro tag", "wie viel kann ich diese woche",
    ]):
        return ""
    remaining, total_expenses, income, fixed = calculate_remaining_budget(u, user_id)
    left_days = days_left_in_month()
    daily = remaining / left_days
    weekly = daily * 7
    intro = ""
    if "tageslimit" in text_lower or "tages limit" in text_lower:
        intro = (
            "Ein festes Tageslimit ist meistens nicht besonders sinnvoll, weil Ausgaben nicht jeden Tag gleichmäßig kommen.\n"
            "Besser ist ein Wochenbudget als Orientierung.\n\n"
        )
    return (
        f"{intro}"
        f"Dein freies Restbudget diesen Monat: {remaining:.2f} EUR\n"
        f"Noch {left_days} Tage im Monat.\n"
        f"Tagesbudget: ca. {daily:.2f} EUR\n"
        f"Wochenbudget: ca. {weekly:.2f} EUR\n\n"
        "Ich ziehe dafür Einkommen, Fixkosten, Sparrate und bisherige Ausgaben zusammen."
    )


def is_affordability_question(text_lower: str) -> bool:
    return (
        any(phrase in text_lower for phrase in [
            "kann ich mir", "koennte ich mir", "könnte ich mir",
            "kann ich", "leisten", "drin", "passt das noch",
        ])
        and any(word in text_lower for word in ["leisten", "kaufen", "ausgeben", "ausgabe", "drin"])
    )


def maybe_answer_affordability(user_id: int, u: dict, text_lower: str) -> str:
    if not is_affordability_question(text_lower):
        return ""
    amounts = extract_amounts(text_lower, exclude_years=True)
    if not amounts:
        return ""

    planned = float(amounts[-1])
    remaining, total_expenses, income, fixed = calculate_remaining_budget(u, user_id)
    after_purchase = remaining - planned
    left_days = days_left_in_month()
    daily_after = after_purchase / left_days if left_days > 0 else after_purchase

    if after_purchase >= 200:
        verdict = "Ja, das wirkt aktuell machbar."
        note = "Dein Restbudget bleibt danach noch entspannt positiv."
    elif after_purchase >= 0:
        verdict = "Ja, aber eher bewusst."
        note = "Es passt noch rein, aber dein Puffer wird kleiner."
    else:
        verdict = "Ich wäre vorsichtig."
        note = "Damit würdest du dein aktuelles Restbudget überziehen."

    return (
        f"{verdict}\n\n"
        f"Geplante Ausgabe: {format_eur(planned)}\n"
        f"Aktuelles freies Restbudget: {format_eur(remaining)}\n"
        f"Danach übrig: {format_eur(after_purchase)}\n"
        f"Tagesbudget danach: ca. {format_eur(daily_after)}\n\n"
        f"{note}"
    )


MICRO_CONFIRMATIONS = [
    "Ist drin.",
    "Hab ich notiert.",
    "Erfasst.",
    "Ich hab's im Blick.",
    "✓ Notiert.",
    "Steht drin.",
    "Passt, ist gespeichert.",
    "✓ Läuft.",
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
    if expense_count >= 5 and remember_monthly_moment(user_id, "report_seed"):
        return (
            "\n\nStark - 5 Ausgaben sind drin.\n\n"
            "Jetzt entsteht langsam dein erster Rov.E Snapshot.\n\n"
            "Wenn du bis Sonntag weitertrackst, kann ich dir zeigen, wo dein Geld diese Woche hingegangen ist."
        )
    return ""


def build_first_expense_moment(user_id: int, expense_count: int) -> str:
    if expense_count == 1 and remember_monthly_moment(user_id, "first_expense"):
        return (
            "\n\nDas ist dein erster Baustein für deinen Monatsreport.\n"
            "Ab jetzt entsteht Schritt für Schritt dein echtes Monatsbild."
        )
    return ""


def build_early_pattern_moment(user_id: int, expense_count: int) -> str:
    if expense_count == 3 and remember_monthly_moment(user_id, "early_pattern"):
        return (
            "\n\nDu hast jetzt 3 Ausgaben eingetragen.\n"
            "Noch zu früh für ein Urteil - aber ich erkenne bereits die ersten Muster.\n"
            "Tracke weiter, dann entsteht daraus dein erster echter Überblick."
        )
    return ""


def build_smart_spending_hint(user_id: int, items: list) -> str:
    if not user_id or not any(item.get("category") == "RESTAURANTS" for item in items):
        return ""
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS c, SUM(amount) AS total
               FROM expenses
               WHERE user_id = ?
               AND category = 'RESTAURANTS'
               AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', 'localtime')""",
            (user_id,)
        ).fetchone()
    restaurant_count = int(row["c"] or 0)
    restaurant_total = float(row["total"] or 0)
    if restaurant_count >= 3 and restaurant_total >= 40 and remember_monthly_moment(user_id, "restaurant_pattern"):
        return (
            "\n\nRestaurants fallen diesen Monat etwas stärker ins Gewicht.\n"
            "Ich halte das für deinen Report im Blick."
        )
    return ""


def build_tracking_note(cp_earned: int) -> str:
    return "Tracking-Tag gesichert." if cp_earned > 0 else ""


def build_category_learning_hint(items: list) -> str:
    for item in items:
        if item.get("category") == "SONSTIGES":
            merchant = str(item.get("merchant") or "").strip()
            if not merchant or merchant.lower() == "unbekannt":
                merchant = "das"
            return (
                "Wenn die Kategorie nicht passt, schreib kurz:\n"
                f"`{merchant} ist Lebensmittel`\n"
                "Dann merke ich mir das für dich."
            )
    return ""


def format_expense_confirmation(items: list, cp_text: str, user_id: int = None) -> str:
    expense_count = get_month_expense_count(user_id) if user_id else 0
    first_expense_moment = build_first_expense_moment(user_id, expense_count) if user_id else ""
    early_pattern_moment = build_early_pattern_moment(user_id, expense_count) if user_id else ""
    report_moment = build_report_seed_moment(user_id, expense_count) if user_id else ""
    smart_hint = build_smart_spending_hint(user_id, items) if user_id else ""
    learning_hint = build_category_learning_hint(items)

    if len(items) == 1:
        item = items[0]
        category = item["category"]
        merchant = item["merchant"]
        if merchant.lower() == "unbekannt":
            merchant = category_label(category)

        lines = [f"Gespeichert: {merchant} {item['amount']:.2f}€ · {category_label(category)}."]
        if cp_text:
            lines.append(cp_text)
        if first_expense_moment:
            lines.append(first_expense_moment.strip())
        if early_pattern_moment:
            lines.append(early_pattern_moment.strip())
        if report_moment:
            lines.append(report_moment.strip())
        if smart_hint:
            lines.append(smart_hint.strip())
        if learning_hint:
            lines.append(learning_hint.strip())
        return "\n\n".join(lines)

    lines = [f"Gespeichert: {len(items)} Ausgaben.", ""]
    for item in items:
        category = item["category"]
        merchant = item["merchant"]
        if merchant.lower() == "unbekannt":
            merchant = category_label(category)
        lines.append(f"{merchant} {item['amount']:.2f}€ · {category_label(category)}")
    if cp_text:
        lines.append("")
        lines.append(cp_text)
    if first_expense_moment:
        lines.append(first_expense_moment.strip())
    if early_pattern_moment:
        lines.append(early_pattern_moment.strip())
    if report_moment:
        lines.append(report_moment.strip())
    if smart_hint:
        lines.append(smart_hint.strip())
    if learning_hint:
        lines.append(learning_hint.strip())
    return "\n".join(lines)


DETAIL_SECTION_LABELS = {
    "wohnen": ("🏠", "Wohnen"),
    "mobilitaet": ("🚗", "Mobilität"),
    "abos": ("📱", "Abos"),
    "versicherungen": ("🛡️", "Versicherungen"),
    "kredite": ("💳", "Kredite"),
    "app_vertraege": ("📄", "Weitere Verträge"),
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
    "abo": "Abo",
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
    "restschuld": "Restschuld",
    "gesamtbetrag": "Gesamtbetrag",
    "schulden_gesamt": "Schulden gesamt",
}


def get_app_contract_display_names(user_id: int | None) -> dict[str, str]:
    """Löst App-Vertrags-IDs für die Bot-Ausgabe wieder in echte Namen auf."""
    if not user_id:
        return {}
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT detail_key, name FROM app_contracts WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {str(row["detail_key"]): str(row["name"]) for row in rows}
    except sqlite3.OperationalError:
        # Alte Datenbanken kennen die neue App-Tabelle noch nicht. Die Fixkosten-Ausgabe
        # bleibt trotzdem verfügbar und fällt nur auf den technischen Fallback zurück.
        return {}


def format_fixed_cost_breakdown(u: dict) -> str:
    details = u.get("details", {})
    total = u.get("fixed_costs") or fixed_costs_total(details if isinstance(details, dict) else {})
    if not isinstance(details, dict) or not any(isinstance(v, dict) and v for v in details.values()):
        return f"Deine aktuellen Fixkosten liegen bei {total:.2f} EUR pro Monat."

    app_contract_names = get_app_contract_display_names(u.get("user_id"))
    lines = ["*Deine Fixkosten*", ""]
    for section, values in details.items():
        if not isinstance(values, dict) or not values:
            continue
        emoji, section_label = DETAIL_SECTION_LABELS.get(section, ("•", section.replace("_", " ").title()))
        lines.append(f"{emoji} *{section_label}*")
        for key, value in values.items():
            label = app_contract_names.get(key, DETAIL_ITEM_LABELS.get(key, key.replace("_", " ").title()))
            if key in {"restschuld", "gesamtbetrag", "schulden_gesamt"}:
                lines.append(f"{label}: {format_eur(value)} offen")
            else:
                lines.append(f"{label}: {format_eur(value)}")
        lines.append("")

    lines.append(f"*Gesamt: {format_eur(total)}*")
    return "\n".join(lines)


def format_fixed_cost_section(u: dict, section: str, title: str) -> str:
    details = u.get("details", {})
    if not isinstance(details, dict):
        return f"{title}: noch nicht hinterlegt."
    section_data = details.get(section, {})
    if not isinstance(section_data, dict) or not section_data:
        return f"{title}: noch nicht hinterlegt."

    total = 0.0
    lines = [f"*{title}*", ""]
    for key, value in section_data.items():
        if key in {"restschuld", "gesamtbetrag", "schulden_gesamt"}:
            continue
        try:
            amount = float(value or 0)
        except (TypeError, ValueError):
            continue
        total += amount
        label = DETAIL_ITEM_LABELS.get(key, key.replace("_", " ").title())
        lines.append(f"{label}: {format_eur(amount)}")

    if not lines[2:]:
        return f"{title}: noch nicht hinterlegt."
    lines.append("")
    lines.append(f"*Gesamt: {format_eur(total)} pro Monat*")
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

    asks_abo_breakdown = (
        any(word in text_lower for word in ["abo", "abos", "abonnement", "abonnements"])
        and (
            is_profile_info_question(text_lower)
            or any(word in text_lower for word in [
                "zeig", "zeige", "liste", "auflisten", "übersicht", "uebersicht",
                "überblick", "ueberblick", "aufstellung", "anzeigen",
            ])
        )
    )
    if asks_abo_breakdown:
        return format_fixed_cost_section(u, "abos", "Deine Abos")

    has_amount = bool(extract_amounts(text_lower, exclude_years=True))
    if has_amount and not is_profile_info_question(text_lower):
        return ""

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
        "Nutzerprofil aus der Rov.E-Datenbank:",
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
    clarity_terms = ["rov.e", "rove", "clarity", "bot", "dich", "hier", "das"]
    return any(phrase in text_lower for phrase in help_phrases) and any(term in text_lower for term in clarity_terms)


def is_tageslimit_question(text_lower: str) -> bool:
    return "tageslimit" in text_lower and any(
        word in text_lower for word in ["was", "heißt", "heisst", "bedeutet", "warum", "?"]
    )


def build_tageslimit_answer() -> str:
    return (
        "Ein festes Tageslimit ist meistens nicht besonders sinnvoll, weil Ausgaben nicht jeden Tag gleichmäßig kommen.\n\n"
        "Besser ist ein Wochenbudget als Orientierung.\n"
        "Ich rechne dafür dein Einkommen minus Fixkosten, Sparrate und bisherige Ausgaben.\n\n"
        "Frag mich einfach: `Wie hoch ist mein Wochenbudget?`"
    )


def build_help_answer() -> str:
    return (
        "Ich kümmere mich um deine Übersicht.\n\n"
        "Du kannst mir Ausgaben einfach so schreiben, wie sie dir in den Kopf kommen:\n\n"
        "`Lidl 34€`\n"
        "`Tanken 60€`\n"
        "`Restaurant 20€`\n\n"
        "Ich ordne das automatisch ein und behalte den Überblick für dich.\n\n"
        "Du musst nicht alles perfekt eintragen.\n"
        "Ein paar ehrliche Einträge reichen schon, damit dein Monatsbild klarer wird.\n\n"
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
        "Hi, ich bin Rov.E.\n"
        "Schön, dass du da bist.\n\n"
        "Ich halte dein Geld für dich im Blick.\n"
        "Wir gehen das Schritt für Schritt zusammen durch.\n\n"
        "Du musst nichts vorbereiten.\n"
        "Ein paar ehrliche Einträge reichen schon, damit ich dein Monatsbild klarer machen kann.\n\n"
        "Ich stelle dir jetzt ein paar Fragen, damit ich dein Profil sauber aufbauen kann.\n"
        "Danach kannst du mich einfach im Alltag nutzen.\n\n"
        "*Schritt 1 von 8:* Wie hoch ist dein monatliches Nettoeinkommen?\n"
        "_(z.B. 2500)_\n\n"
        "_Schreib zurück oder nutze den Menüpunkt Zurück, um einen Schritt zurückzugehen._"
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
        "Der *Rov.E Score* zeigt, wie stabil dein finanzielles Verhalten gerade ist.\n\n"
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
               AND source IN ('investiert_command', 'app_monthly_plan')
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
    """Use one score formula for the remaining bot, reports and the App."""
    with get_db() as conn:
        return calculate_live_score(conn, user_id, u, total_expenses, report_month)


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
                f"7-Tage Streak · +10 RP · Gesamt: {new_pts} RP\n∙ ⚡ Erste Woche",
                parse_mode="Markdown"
            )
    elif streak == 30:
        if award_badge(user_id, "streak_30"):
            new_pts = add_cp(user_id, 30)
            bot_instance.send_message(user_id,
                f"30-Tage Streak · +30 RP · Gesamt: {new_pts} RP\n∙ 🔥 Eiserner Monat",
                parse_mode="Markdown"
            )
    elif streak > 7 and streak % 7 == 0:
        new_pts = add_cp(user_id, 5)
        bot_instance.send_message(user_id, f"{streak}-Tage Streak · +5 RP")

    record_score_history_if_needed(user_id)
    maybe_send_budget_invite(user_id, bot_instance)
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
    property_equity = 0.0
    with get_db() as conn:
        try:
            property_row = conn.execute(
                """SELECT market_value, remaining_debt
                     FROM app_properties WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
            if property_row:
                property_equity = max(
                    0.0,
                    float(property_row["market_value"] or 0) - float(property_row["remaining_debt"] or 0),
                )
        except sqlite3.OperationalError:
            # Bot-only users may not have opened the App yet.
            pass

    net_worth = (u.get("current_investments") or 0) + (u.get("current_cash") or 0) + property_equity

    bonus_lines = []
    latest_points = u.get("clarity_points") or 0
    if budget_ok:
        if award_badge(user_id, "month_win"):
            bonus_lines.append("Monats-Sieg freigeschaltet")
        latest_points = add_cp(user_id, 50)
        bonus_lines.append(f"Budget eingehalten - +50 RP - Gesamt: {latest_points} RP")
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
        f"Rov.E Score: *{score_data['total']}/100*\n"
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

YES_WORDS = {"ja", "yes", "jap", "jo", "klar", "gerne", "ja bitte", "mach", "mach mal", "start", "starte"}
NO_WORDS = {"nein", "no", "nee", "später", "spaeter", "nicht jetzt", "überspringen", "ueberspringen", "skip"}

def setup_bot_menu():
    commands = [
        telebot.types.BotCommand("start",      "🚀 Start & Profil anlegen"),
        telebot.types.BotCommand("status",     "📊 Restbudget checken"),
        telebot.types.BotCommand("stats",      "📈 Ausgaben nach Kategorien"),
        telebot.types.BotCommand("score",      "🌟 Rov.E Score & Rang"),
        telebot.types.BotCommand("scoreinfo",  "Rov.E Score erklärt"),
        telebot.types.BotCommand("badges",     "🏆 Errungenschaften"),
        telebot.types.BotCommand("goal",       "🎯 Sparziel & Prognose"),
        telebot.types.BotCommand("investiert", "💰 Sparrate bestätigen (+20 RP)"),
        telebot.types.BotCommand("verfeinern", "⚙️ Fixkosten & Profil bearbeiten"),
        telebot.types.BotCommand("portfolio",  "📈 ETF täglich tracken lassen"),
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
        telebot.types.BotCommand("nudge_inactive", "Inaktive Beta-Tester erinnern"),
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


def start_refinement_flow(user_id: int):
    update_user_field(user_id, "onboarding_step", STEP_ADAPT_HOUSING)
    bot.send_message(
        user_id,
        "⚙️ *Profil verfeinern – Teil 1: Wohnen*\n\nMiete, Strom, Gas?\n_(z.B. 800 60 40)_",
        parse_mode="Markdown"
    )


def ask_refinement_after_onboarding(user_id: int):
    user_pending_actions[user_id] = {"type": "refine_after_onboarding"}
    bot.send_message(
        user_id,
        "Möchtest du deine Fixkosten jetzt genauer hinterlegen?\n\n"
        "Das hilft mir, dein Monatsbild sauberer zu berechnen.\n\n"
        "Schreib einfach:\n"
        "`Ja` - wir machen es direkt zusammen\n"
        "`Nein` - du kannst später starten",
        parse_mode="Markdown"
    )


def ask_first_expense_after_onboarding(user_id: int):
    user_pending_actions[user_id] = {"type": "first_expense_after_onboarding"}
    bot.send_message(
        user_id,
        "Ein letzter kleiner Schritt, damit du direkt spürst, wie Rov.E funktioniert.\n\n"
        "Schreib mir jetzt deine letzte echte Ausgabe.\n\n"
        "Zum Beispiel:\n"
        "`Lidl 12€`\n"
        "`Tanken 40€`\n"
        "`Döner 8€`\n\n"
        "Wenn du zuerst deine Fixkosten genauer hinterlegen willst, schreib `Verfeinern`.\n"
        "Wenn du später starten willst, schreib `Später`.",
        parse_mode="Markdown"
    )


def is_refinement_text_request(text_lower: str) -> bool:
    normalized = text_lower.strip().lstrip("/")
    direct = {
        "verfeinern", "profil verfeinern", "fixkosten verfeinern",
        "profil bearbeiten", "fixkosten bearbeiten"
    }
    if normalized in direct:
        return True
    return "verfeinern" in normalized and len(normalized) <= 40


# ====================== PORTFOLIO-TRACKING (eigenständig, ohne Onboarding/Verfeinern anzufassen) ======================
# WICHTIG: Diese Funktionen duerfen NIE etf_savings, current_investments oder fixed_costs
# beschreiben - das waere die Dopplung, die explizit vermieden werden soll. Portfolio-Holdings
# sind eine rein informative Zusatz-Ebene fuer Kurs-/Wertentwicklung einzelner Instrumente.

# (key, label, isin, price_symbol) - price_symbol ist ein US-gelisteter Index-Proxy,
# weil europaeische Boersenplaetze (Xetra/LSE) im kostenlosen Twelve-Data-Tarif gesperrt
# sind. Gleicher Index, nur in USD statt EUR notiert (kleine FX-Abweichung moeglich,
# fuer eine grobe Performance-Anzeige aber voellig ausreichend).
CURATED_INSTRUMENTS = {
    "msci world": ("msci_world", "MSCI World", "IE00B4L5Y983", "URTH"),
    "msci": ("msci_world", "MSCI World", "IE00B4L5Y983", "URTH"),
    "s&p 500": ("sp500", "S&P 500", "IE00B5BMR087", "SPY"),
    "sp500": ("sp500", "S&P 500", "IE00B5BMR087", "SPY"),
    "s&p500": ("sp500", "S&P 500", "IE00B5BMR087", "SPY"),
    "nasdaq 100": ("nasdaq100", "Nasdaq 100", "IE0032077012", "QQQ"),
    "nasdaq100": ("nasdaq100", "Nasdaq 100", "IE0032077012", "QQQ"),
    "nasdaq": ("nasdaq100", "Nasdaq 100", "IE0032077012", "QQQ"),
}


def is_portfolio_tracking_request(text_lower: str) -> bool:
    normalized = text_lower.strip().lstrip("/")
    direct = {
        "portfolio", "portfolio tracken", "etf tracken", "aktie tracken",
        "etf tracking", "portfolio einrichten", "investment tracken",
    }
    if normalized in direct:
        return True
    return len(normalized) <= 40 and any(
        word in normalized for word in ("portfolio tracken", "etf tracken", "aktie tracken")
    )


def build_portfolio_curated_list_message() -> str:
    return (
        "📈 *ETF-Tracking*\n\n"
        "Welchen ETF soll ich für dich verfolgen?\n\n"
        "MSCI World · S&P 500 · Nasdaq 100\n\n"
        "Schreib z.B.: `MSCI World 200€ im Monat`\n\n"
        "_Bitte ein ETF pro Nachricht - hast du mehrere, schreib einfach danach nochmal `Portfolio`._\n\n"
        "Hast du einen anderen ETF? Schreib die ISIN direkt dazu, "
        "z.B. `IE00B4L5Y983 200€ im Monat` - ich speichere es, aktuell kann ich dafür "
        "aber noch keine Kursdaten abrufen (nur für die drei oben).\n\n"
        "_Ich aktualisiere den Kurs einmal täglich - kein Live-Ticker._"
    )


def start_portfolio_tracking_flow(user_id: int):
    user_pending_actions[user_id] = {"type": "portfolio_setup"}
    bot.send_message(user_id, build_portfolio_curated_list_message(), parse_mode="Markdown")


def parse_portfolio_registration(text_input: str, text_lower: str):
    """Erkennt '<Instrument> <Betrag>€' und liefert (instrument_key, label, isin, price_symbol, amount) oder None.
    price_symbol ist None, wenn nur eine rohe ISIN erkannt wurde (dafuer gibt es aktuell keine Kursdaten).

    Wichtig: Manche Instrumentnamen enthalten selbst Zahlen (S&P "500", Nasdaq "100").
    Deshalb wird der Instrument-Alias ZUERST erkannt und aus dem Text entfernt, bevor
    nach dem eigentlichen Euro-Betrag gesucht wird - sonst wuerde z.B. "500" aus
    "S&P 500" faelschlich als Betrag statt als Teil des Namens erkannt."""
    matched_alias, matched = None, None
    for alias, (key, label, isin, price_symbol) in CURATED_INSTRUMENTS.items():
        if alias in text_lower and (matched_alias is None or len(alias) > len(matched_alias)):
            matched_alias, matched = alias, (key, label, isin, price_symbol)

    remainder = text_lower.replace(matched_alias, " ", 1) if matched_alias else text_lower

    amounts = extract_amounts(remainder, exclude_years=True)
    if len(amounts) != 1:
        return None
    amount = float(amounts[0])
    if amount <= 0:
        return None

    if matched:
        key, label, isin, price_symbol = matched
        return key, label, isin, price_symbol, amount

    isin_match = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b", text_input.upper())
    if isin_match:
        isin = isin_match.group(1)
        return isin.lower(), isin, isin, None, amount

    return None


def fetch_price_quote(symbol: str):
    """Holt einen aktuellen Kurs von Twelve Data. Gibt None zurueck bei jedem Fehler
    (fehlender Key, Netzwerkproblem, ungueltiges Symbol) - darf den Bot nie zum Absturz bringen."""
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        return None
    try:
        url = f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={api_key}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        close = data.get("close")
        if close is None:
            return None
        return {"close": float(close), "percent_change": float(data.get("percent_change") or 0.0)}
    except Exception as e:
        logger.warning(f"Twelve-Data-Abfrage fehlgeschlagen fuer {symbol}: {e}")
        return None


def save_portfolio_holding(user_id: int, instrument_key: str, label: str, isin: str,
                          price_symbol, amount: float) -> dict:
    """Legt ein Holding an oder aktualisiert es (UNIQUE-Constraint verhindert Dopplung).
    Bei Neuanlage wird sofort ein Startkurs geholt (falls Kursdaten verfuegbar sind).
    Gibt zurueck, ob es neu war und was der vorherige Beitrag war (fuer die Bestaetigungs-Nachricht)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT monthly_contribution FROM portfolio_holdings WHERE user_id = ? AND instrument_key = ?",
            (user_id, instrument_key)
        )
        existing = cursor.fetchone()
        was_new = existing is None
        old_amount = existing["monthly_contribution"] if existing else None

        start_price = None
        if was_new and price_symbol:
            quote = fetch_price_quote(price_symbol)
            if quote:
                start_price = quote["close"]

        conn.execute(
            """INSERT INTO portfolio_holdings
                   (user_id, instrument_key, instrument_label, isin, price_symbol,
                    monthly_contribution, start_price, last_price, last_checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, instrument_key) DO UPDATE SET
                   monthly_contribution = excluded.monthly_contribution,
                   instrument_label = excluded.instrument_label,
                   isin = excluded.isin,
                   price_symbol = excluded.price_symbol,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, instrument_key, label, isin, price_symbol, amount, start_price, start_price)
        )
        conn.commit()

    return {"was_new": was_new, "old_amount": old_amount, "has_price_data": start_price is not None}


def get_portfolio_holdings(user_id: int) -> list:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT instrument_key, instrument_label, isin, price_symbol, monthly_contribution, "
            "total_invested, start_price, last_price, last_checked_at, started_at "
            "FROM portfolio_holdings WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        )
        return cursor.fetchall()


def save_portfolio_total_invested(user_id: int, instrument_key: str, amount: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE portfolio_holdings SET total_invested = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ? AND instrument_key = ?",
            (amount, user_id, instrument_key)
        )
        conn.commit()


def update_all_portfolio_prices() -> int:
    """Taeglicher Job: holt fuer jedes Holding mit price_symbol den aktuellen Kurs.
    Ueberspringt Holdings ohne price_symbol (z.B. rohe ISIN-Eintraege) stillschweigend."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, price_symbol FROM portfolio_holdings WHERE price_symbol IS NOT NULL"
        )
        rows = cursor.fetchall()

    updated = 0
    for row in rows:
        quote = fetch_price_quote(row["price_symbol"])
        if not quote:
            continue
        with get_db() as conn:
            conn.execute(
                "UPDATE portfolio_holdings SET last_price = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
                (quote["close"], row["id"])
            )
            conn.commit()
        updated += 1
    return updated


def build_portfolio_performance_answer(user_id: int) -> str:
    holdings = get_portfolio_holdings(user_id)
    if not holdings:
        return (
            "Du trackst aktuell kein Investment.\n\n"
            "Schreib `Portfolio`, um eins einzurichten."
        )

    lines = ["📈 *Dein Portfolio*\n"]
    for h in holdings:
        label = h["instrument_label"]
        contribution = h["monthly_contribution"]
        if h["price_symbol"] is None:
            lines.append(f"*{label}* - {contribution:.2f}€/Monat _(noch keine Kursdaten verfügbar)_")
            continue
        if h["last_price"] is None or h["start_price"] is None:
            lines.append(f"*{label}* - {contribution:.2f}€/Monat _(Kurs wird beim nächsten täglichen Update abgerufen)_")
            continue
        change_pct = (h["last_price"] - h["start_price"]) / h["start_price"] * 100
        arrow = "📈" if change_pct >= 0 else "📉"
        total = h["total_invested"]
        if total:
            estimated_value = total * (1 + change_pct / 100)
            lines.append(
                f"{arrow} *{label}* - {contribution:.2f}€/Monat\n"
                f"   {format_eur(total)} → ca. {format_eur(estimated_value)} ({change_pct:+.1f}% seit Einrichtung)"
            )
        else:
            lines.append(
                f"{arrow} *{label}* - {contribution:.2f}€/Monat\n"
                f"   Seit Einrichtung: {change_pct:+.1f}%"
            )
    return "\n".join(lines)


def is_portfolio_performance_question(text_lower: str) -> bool:
    triggers = [
        "wie läuft mein", "wie laeuft mein", "wie läuft meine", "wie laeuft meine",
        "wie steht mein etf", "wie steht meine aktie", "mein portfolio",
    ]
    return any(t in text_lower for t in triggers)


def parse_portfolio_total_update(text_lower: str):
    """Erkennt '<kuratiertes Instrument> <Betrag>€ gesamt/investiert' - zum Nachtragen
    der Gesamtsumme bei einem BEREITS bestehenden Holding (z.B. vor Einfuehrung dieser
    Frage registriert). Erfordert das Wort 'gesamt' oder 'investiert' zur Abgrenzung
    von '<Instrument> <Betrag>€ im Monat' (monatlicher Beitrag)."""
    if not any(w in text_lower for w in ("gesamt", "investiert")):
        return None
    matched_alias, matched_key = None, None
    for alias, (key, label, isin, price_symbol) in CURATED_INSTRUMENTS.items():
        if alias in text_lower and (matched_alias is None or len(alias) > len(matched_alias)):
            matched_alias, matched_key = alias, key
    if not matched_key:
        return None
    remainder = text_lower.replace(matched_alias, " ", 1)
    amounts = extract_amounts(remainder, exclude_years=True)
    if len(amounts) != 1 or amounts[0] <= 0:
        return None
    return matched_key, float(amounts[0])


def maybe_update_portfolio_total(user_id: int, text_lower: str) -> str:
    """Traegt eine Gesamtsumme fuer ein BEREITS bestehendes Holding nach. Gibt '' zurueck,
    wenn nichts passt oder kein passendes Holding existiert - dann uebernehmen andere Pfade."""
    parsed = parse_portfolio_total_update(text_lower)
    if not parsed:
        return ""
    key, amount = parsed
    existing = {h["instrument_key"]: h for h in get_portfolio_holdings(user_id)}
    if key not in existing:
        return ""
    label = existing[key]["instrument_label"]
    save_portfolio_total_invested(user_id, key, amount)
    return (
        f"✅ Notiert: {format_eur(amount)} im {label}.\n\n"
        f"Frag mich jederzeit „wie läuft mein {label}?\n\n"
        "_Noch ein ETF? Schreib einfach nochmal `Portfolio`._"
    )


def maybe_delete_portfolio_holding(user_id: int, text_lower: str) -> str:
    """Erkennt 'lösche <ETF>' fuer ein BEREITS getracktes Portfolio-Holding und entfernt es.

    Bewusst NICHT durch looks_like_investment_update() blockiert: ETF-Vokabular
    (etf/sparplan/msci/world/...) ueberschneidet sich mit INVESTMENT_INPUTS, das eigentlich
    fuer die current_investments-Korrektur gedacht ist. Das praezisere, sicherere Signal
    hier ist: existiert ueberhaupt ein passendes portfolio_holdings-Holding? Wenn nein,
    ist diese Funktion nicht zustaendig und andere Pfade uebernehmen unveraendert."""
    delete_words = ["lösche", "loesche", "entferne", "streiche", "storniere", "stornier"]
    if not any(word in text_lower for word in delete_words):
        return ""
    if find_detail_alias_matches(text_lower):
        return ""  # echter Fixkosten-/Abo-Treffer hat Vorrang

    matched_alias, matched_key = None, None
    for alias, (key, label, isin, price_symbol) in CURATED_INSTRUMENTS.items():
        if alias in text_lower and (matched_alias is None or len(alias) > len(matched_alias)):
            matched_alias, matched_key = alias, key
    if not matched_key:
        return ""

    existing = {h["instrument_key"]: h for h in get_portfolio_holdings(user_id)}
    if matched_key not in existing:
        return ""  # kein getracktes Holding fuer dieses Instrument -> nicht zustaendig

    label = existing[matched_key]["instrument_label"]
    with get_db() as conn:
        conn.execute(
            "DELETE FROM portfolio_holdings WHERE user_id = ? AND instrument_key = ?",
            (user_id, matched_key)
        )
        conn.commit()
    return f"🗑️ *{label}* wird nicht mehr getrackt.\n\nSchreib `Portfolio`, um es (oder ein anderes ETF) wieder einzurichten."


def handle_portfolio_setup_reply(user_id: int, text_input: str, text_lower: str) -> bool:
    if text_lower in NO_WORDS or text_lower in {"abbrechen", "stop", "cancel"}:
        user_pending_actions.pop(user_id, None)
        bot.send_message(user_id, "Alles klar, kein Problem. Du kannst jederzeit `Portfolio` schreiben.")
        return True

    parsed = parse_portfolio_registration(text_input, text_lower)
    if not parsed:
        bot.send_message(
            user_id,
            "Das konnte ich noch nicht zuordnen.\n\n" + build_portfolio_curated_list_message(),
            parse_mode="Markdown"
        )
        return True

    key, label, isin, price_symbol, amount = parsed
    result = save_portfolio_holding(user_id, key, label, isin, price_symbol, amount)

    if result["was_new"]:
        if result["has_price_data"]:
            user_pending_actions[user_id] = {"type": "portfolio_total_amount", "instrument_key": key, "label": label}
            bot.send_message(
                user_id,
                f"✅ Eingerichtet: *{label}*, {amount:.2f}€/Monat.\n\n"
                f"Wie hoch ist deine aktuelle Position im {label}? (z.B. `9000€`)\n"
                "Dann rechne ich die Entwicklung ab jetzt für dich in Euro um.\n\n"
                "_Weißt du es nicht genau? Schreib `überspringen`._",
                parse_mode="Markdown"
            )
        else:
            user_pending_actions.pop(user_id, None)
            bot.send_message(
                user_id,
                f"✅ Eingerichtet: *{label}*, {amount:.2f}€/Monat.\n\n"
                "Für dieses Instrument kann ich aktuell noch keine Kursdaten abrufen - der Beitrag ist aber gespeichert.\n\n"
                "_Noch ein ETF? Schreib einfach nochmal `Portfolio`._",
                parse_mode="Markdown"
            )
    else:
        user_pending_actions.pop(user_id, None)
        old = result["old_amount"] or 0.0
        bot.send_message(
            user_id,
            f"🔄 Aktualisiert: *{label}* {old:.2f}€ → {amount:.2f}€/Monat.\n\n"
            "Kein doppelter Eintrag - dein bestehendes Holding wurde angepasst.",
            parse_mode="Markdown"
        )
    return True


def handle_portfolio_total_amount_reply(user_id: int, action: dict, text_lower: str) -> bool:
    label = action.get("label", "dein Investment")
    if text_lower in {"überspringen", "ueberspringen", "skip", "weiß nicht", "weiss nicht", "keine ahnung"} or text_lower in NO_WORDS:
        user_pending_actions.pop(user_id, None)
        bot.send_message(
            user_id,
            f"Kein Problem, ich zeig dir dann nur die reine {label}-Entwicklung in Prozent.\n\n"
            "_Noch ein ETF? Schreib einfach nochmal `Portfolio`._"
        )
        return True

    amounts = extract_amounts(text_lower, exclude_years=True)
    if len(amounts) != 1 or amounts[0] <= 0:
        bot.send_message(
            user_id,
            f"Das konnte ich nicht als Betrag lesen. Schreib z.B. `9000€`, oder `überspringen`."
        )
        return True

    save_portfolio_total_invested(user_id, action["instrument_key"], float(amounts[0]))
    user_pending_actions.pop(user_id, None)
    bot.send_message(
        user_id,
        f"✅ Notiert: {format_eur(amounts[0])} im {label}.\n\n"
        f"Frag mich jederzeit „wie läuft mein {label}?\n\n"
        "_Noch ein ETF? Schreib einfach nochmal `Portfolio`._",
    )
    return True


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

        sync_app_paid_expense_amount(conn, user_id, expense_id, new_amount)
        cursor.execute(
            "UPDATE expenses SET amount = ? WHERE id = ? AND user_id = ?",
            (new_amount, expense_id, user_id)
        )
        conn.commit()
        return expense


def _app_payment_movement(conn, user_id: int, expense_id: int):
    """Findet die Kontowirkung einer App-Ausgabe, falls die neue App-Tabelle schon existiert."""
    try:
        return conn.execute(
            """SELECT id, kind, amount FROM app_cash_movements
                 WHERE user_id = ? AND expense_id = ?
                   AND kind IN ('payment', 'card')
                 ORDER BY id DESC LIMIT 1""",
            (user_id, expense_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _set_app_account_amount(conn, user_id: int, account_key: str, amount: float) -> None:
    conn.execute(
        """INSERT INTO app_account_balances (user_id, account_key, amount, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, account_key)
           DO UPDATE SET amount = excluded.amount, updated_at = CURRENT_TIMESTAMP""",
        (user_id, account_key, round(max(0.0, amount), 2)),
    )
    conn.execute(
        """UPDATE users
              SET current_cash = COALESCE((
                    SELECT SUM(amount) FROM app_account_balances WHERE user_id = ?
                  ), current_cash)
            WHERE user_id = ?""",
        (user_id, user_id),
    )


def reverse_app_paid_expense(conn, user_id: int, expense_id: int) -> float:
    """Gibt bei einer App-Ausgabe den wirklich abgezogenen Betrag auf ihr Ursprungskonto zurück."""
    movement = _app_payment_movement(conn, user_id, expense_id)
    if not movement:
        return 0.0

    account_key = "bargeld" if movement["kind"] == "payment" else "giro"
    row = conn.execute(
        """SELECT amount FROM app_account_balances
             WHERE user_id = ? AND account_key = ?""",
        (user_id, account_key),
    ).fetchone()
    current = float(row["amount"] or 0) if row else 0.0
    refund = round(max(0.0, float(movement["amount"] or 0)), 2)
    _set_app_account_amount(conn, user_id, account_key, current + refund)
    conn.execute(
        "DELETE FROM app_cash_movements WHERE user_id = ? AND expense_id = ?",
        (user_id, expense_id),
    )
    return refund


def sync_app_paid_expense_amount(
    conn, user_id: int, expense_id: int, new_amount: float
) -> None:
    """Hält das Portemonnaie korrekt, wenn /editlast eine bar bezahlte App-Ausgabe ändert."""
    movement = _app_payment_movement(conn, user_id, expense_id)
    if not movement:
        return

    account_key = "bargeld" if movement["kind"] == "payment" else "giro"
    row = conn.execute(
        """SELECT amount FROM app_account_balances
             WHERE user_id = ? AND account_key = ?""",
        (user_id, account_key),
    ).fetchone()
    current = float(row["amount"] or 0) if row else 0.0
    old_applied = round(max(0.0, float(movement["amount"] or 0)), 2)
    funds_before_expense = round(current + old_applied, 2)
    new_applied = round(min(max(0.0, float(new_amount)), funds_before_expense), 2)
    _set_app_account_amount(
        conn, user_id, account_key, funds_before_expense - new_applied
    )
    conn.execute(
        """UPDATE app_cash_movements SET amount = ?
             WHERE id = ? AND user_id = ?""",
        (new_applied, movement["id"], user_id),
    )


def handle_pending_action(user_id: int, text_input: str, text_lower: str) -> bool:
    action = user_pending_actions.get(user_id)
    if not action:
        return False

    if action.get("type") == "first_expense_after_onboarding":
        if text_lower in NO_WORDS or text_lower in {"abbrechen", "stop", "cancel"}:
            user_pending_actions.pop(user_id, None)
            bot.send_message(
                user_id,
                "Alles klar.\n\n"
                "Du kannst jederzeit einfach eine Ausgabe schreiben, z.B. `Lidl 12€`.\n"
                "Wenn du dein Profil genauer machen willst, schreib `Verfeinern`.",
                parse_mode="Markdown"
            )
            return True

        if is_refinement_text_request(text_lower):
            user_pending_actions.pop(user_id, None)
            start_refinement_flow(user_id)
            return True

        if is_portfolio_tracking_request(text_lower):
            user_pending_actions.pop(user_id, None)
            start_portfolio_tracking_flow(user_id)
            return True

        amounts = extract_amounts(text_lower, exclude_years=True)
        if len(amounts) == 1 and abs(amounts[0]) > 0.01:
            user_pending_actions.pop(user_id, None)
            return False

        bot.send_message(
            user_id,
            "Fast.\n\n"
            "Schreib bitte eine echte Ausgabe mit Betrag, zum Beispiel:\n"
            "`Lidl 12€`\n"
            "`Tanken 40€`\n\n"
            "Oder schreib `Verfeinern`, wenn du zuerst deine Fixkosten eintragen willst.",
            parse_mode="Markdown"
        )
        return True

    if action.get("type") == "refine_after_onboarding":
        if text_lower in NO_WORDS or text_lower in {"abbrechen", "stop", "cancel"}:
            user_pending_actions.pop(user_id, None)
            bot.send_message(
                user_id,
                "Alles klar.\n\n"
                "Du kannst jederzeit später `Verfeinern` schreiben, wenn du deine Fixkosten genauer hinterlegen willst.",
                parse_mode="Markdown"
            )
            return True

        if text_lower in YES_WORDS or is_refinement_text_request(text_lower):
            user_pending_actions.pop(user_id, None)
            start_refinement_flow(user_id)
            return True

        bot.send_message(
            user_id,
            "Kurz eine Entscheidung, damit nichts durcheinandergerät:\n\n"
            "`Ja` - Fixkosten jetzt genauer hinterlegen\n"
            "`Nein` - erstmal überspringen",
            parse_mode="Markdown"
        )
        return True

    if action.get("type") == "confirm_investment_classification":
        if text_lower in {"abbrechen", "stop", "cancel"}:
            user_pending_actions.pop(user_id, None)
            bot.send_message(user_id, "Alles klar, ich habe daran nichts geändert.")
            return True

        event_type = investment_classification_choice(text_lower)
        if event_type is None:
            bot.send_message(
                user_id,
                "Kurz zur Einordnung:\n\n"
                "`Neu` - zählt als Monatsfortschritt im Report\n"
                "`Bestand` - war schon da und wird nur nachgetragen\n\n"
                "Schreib einfach `Neu` oder `Bestand`.",
                parse_mode="Markdown"
            )
            return True

        user_pending_actions.pop(user_id, None)
        u = get_or_create_user(user_id)
        message, new_badges = apply_investment_change(
            user_id,
            u,
            float(action["amount"]),
            action["text_lower"],
            action.get("direction", "in"),
            event_type=event_type,
        )
        bot.send_message(user_id, message, parse_mode="Markdown")
        send_badge_summary(bot, user_id, new_badges)
        return True

    if action.get("type") == "edit_last_expense":
        if text_lower in {"abbrechen", "stop", "cancel"}:
            user_pending_actions.pop(user_id, None)
            bot.send_message(user_id, "Alles klar, die letzte Ausgabe bleibt unverändert.")
            return True

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

    if action.get("type") == "portfolio_setup":
        return handle_portfolio_setup_reply(user_id, text_input, text_lower)

    if action.get("type") == "portfolio_total_amount":
        return handle_portfolio_total_amount_reply(user_id, action, text_lower)

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
            user_text = "Du bist für Rov.E freigeschaltet. Sende /start und leg los."
            callback_text = "Freigegeben."
        else:
            revoke_user_access(target_id, actor_id)
            admin_text = f"Nutzer abgelehnt/gesperrt:\nID: {target_id}"
            user_text = "Dein Zugang zu Rov.E wurde aktuell nicht freigeschaltet."
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
        try:
            bot.edit_message_reply_markup(uid, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        start_refinement_flow(uid)

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
        "*Rov.E Health*\n\n"
        f"Bot: läuft\n"
        f"Scheduler: {scheduler_state}\n"
        f"User: {users_total}\n"
        f"Ausgaben: {expenses_total}\n"
        f"Zugänge: {access_text}\n"
        f"Reports: {jobs_text}\n"
        f"Datenbank: {db_size / 1024:.1f} KB\n\n"
        f"{error_text}"
    )


def get_inactive_nudge_candidates() -> list:
    with get_db() as conn:
        return conn.execute(
            """SELECT u.user_id,
                      a.display_name,
                      a.username,
                      u.last_activity_date,
                      (SELECT COUNT(*) FROM expenses e WHERE e.user_id = u.user_id) AS expenses_count
               FROM users u
               LEFT JOIN user_access a ON a.user_id = u.user_id
               WHERE COALESCE(a.status, 'approved') = 'approved'
                 AND u.onboarding_step = ?
                 AND (
                    (SELECT COUNT(*) FROM expenses e WHERE e.user_id = u.user_id) = 0
                    OR COALESCE(u.last_activity_date, '') = ''
                    OR DATE(u.last_activity_date) <= DATE('now', '-2 days', 'localtime')
                 )
               ORDER BY u.last_activity_date ASC, u.user_id ASC
               LIMIT 50""",
            (STEP_NORMAL,)
        ).fetchall()


def get_approved_users_for_announcement() -> list:
    with get_db() as conn:
        return conn.execute(
            """SELECT u.user_id, a.display_name, a.username
               FROM users u
               LEFT JOIN user_access a ON a.user_id = u.user_id
               WHERE COALESCE(a.status, 'approved') = 'approved'
               ORDER BY u.user_id ASC"""
        ).fetchall()


def build_announce_rename_preview(rows: list) -> str:
    if not rows:
        return "Keine freigegebenen Nutzer gefunden. Es würde niemand angeschrieben."
    return (
        f"Ich würde {len(rows)} freigegebene Nutzer anschreiben mit:\n\n"
        f"{RENAME_ANNOUNCEMENT_TEXT}\n\n"
        "Zum Senden:\n"
        "`/announce_rename send`"
    )


def build_nudge_preview(rows: list) -> str:
    if not rows:
        return "Keine passenden inaktiven Tester gefunden."
    lines = [
        f"Ich würde {len(rows)} Tester anschreiben:",
        "",
    ]
    for row in rows[:20]:
        identity = format_admin_identity(row)
        last_activity = row["last_activity_date"] or "-"
        lines.append(
            f"{identity}\n"
            f"ID: {row['user_id']} · Buchungen: {row['expenses_count']} · Aktivität: {last_activity}"
        )
    if len(rows) > 20:
        lines.append(f"... und {len(rows) - 20} weitere.")
    lines.extend([
        "",
        "Zum Senden:",
        "/nudge_inactive send",
    ])
    return "\n".join(lines)


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
            "/backupnow – Datenbank sichern\n"
            "/nudge_inactive – inaktive Tester anzeigen\n"
            "/nudge_inactive send – Beta-Check senden\n"
            "/announce_rename – Vorschau der Rov.E-Ankündigung\n"
            "/announce_rename send – Ankündigung an alle freigegebenen Nutzer senden\n"
            "/testrecap – Abend-Recap an dich testen\n"
            "/testreport YYYY-MM – Testreport erstellen",
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
        bot.send_message(uid, "Wartende Freigaben:")
        for row in rows:
            identity = format_admin_identity(row)
            bot.send_message(
                uid,
                f"Name: {identity}\n"
                f"ID: {row['user_id']}\n"
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
            bot.send_message(target_id, "Du bist für Rov.E freigeschaltet. Sende /start und leg los.")
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
                          a.display_name,
                          a.username,
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
        text = "Nutzerübersicht:\n\n"
        for row in rows:
            onboarding = "fertig" if row["onboarding_step"] == STEP_NORMAL else f"Step {row['onboarding_step']}"
            identity = format_admin_identity(row)
            last_activity = row["last_activity_date"] or "-"
            text += (
                f"{identity}\n"
                f"ID: {row['user_id']}\n"
                f"Status: {row['access_status']} · Onboarding: {onboarding}\n"
                f"Aktivität: {last_activity} · Buchungen: {row['expenses_count']} · "
                f"Tracking-Tage: {row['tracked_days']}\n\n"
            )
        bot.send_message(uid, text)
        return True

    if cmd == "/nudge_inactive":
        rows = get_inactive_nudge_candidates()
        parts = message.text.split(maxsplit=1)
        should_send = len(parts) > 1 and parts[1].strip().lower() == "send"

        if not should_send:
            bot.send_message(uid, build_nudge_preview(rows))
            return True

        if not rows:
            bot.send_message(uid, "Keine passenden inaktiven Tester gefunden. Es wurde nichts gesendet.")
            return True

        sent = 0
        failed = 0
        for row in rows:
            try:
                bot.send_message(row["user_id"], BETA_NUDGE_TEXT)
                sent += 1
            except Exception as e:
                failed += 1
                logger.info(f"Beta-Nudge an {row['user_id']} nicht gesendet: {e}")

        bot.send_message(
            uid,
            f"Beta-Check versendet.\n\nGesendet: {sent}\nFehlgeschlagen: {failed}"
        )
        return True

    if cmd == "/announce_rename":
        rows = get_approved_users_for_announcement()
        parts = message.text.split(maxsplit=1)
        should_send = len(parts) > 1 and parts[1].strip().lower() == "send"

        if not should_send:
            bot.send_message(uid, build_announce_rename_preview(rows), parse_mode="Markdown")
            return True

        if not rows:
            bot.send_message(uid, "Keine freigegebenen Nutzer gefunden. Es wurde nichts gesendet.")
            return True

        sent = 0
        failed = 0
        for row in rows:
            try:
                bot.send_message(row["user_id"], RENAME_ANNOUNCEMENT_TEXT, parse_mode="Markdown")
                sent += 1
            except Exception as e:
                failed += 1
                logger.info(f"Rename-Ankündigung an {row['user_id']} nicht gesendet: {e}")

        bot.send_message(
            uid,
            f"Rename-Ankündigung versendet.\n\nGesendet: {sent}\nFehlgeschlagen: {failed}"
        )
        return True

    if cmd == "/testrecap":
        text = build_evening_recap(actor_id)
        if not text:
            bot.send_message(
                uid,
                "Für heute gibt es noch keinen Abend-Recap. Trage kurz eine Ausgabe ein und teste dann nochmal."
            )
            return True
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
        db_path = Path(DB_NAME)
        if not db_path.is_absolute():
            db_path = APP_DIR / db_path
        backups_dir = APP_DIR / "backups"
        backups_dir.mkdir(exist_ok=True)
        backup_path = backups_dir / f"clarity_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        bot.send_message(uid, f"Backup erstellt:\n{backup_path.resolve()}")
        return True

    if cmd == "/testreport":
        return False

    return False


@bot.message_handler(commands=[
    'start', 'help', 'score', 'scoreinfo', 'badges', 'verfeinern', 'portfolio', 'undo', 'editlast', 'id',
    'settings', 'goal', 'status', 'stats', 'reset', 'reset_confirm', 'investiert', 'testreport',
    'admin', 'pending', 'approve', 'revoke', 'adminusers', 'health', 'reportjobs', 'backupnow',
    'nudge_inactive', 'testrecap', 'ruhe', 'announce_rename', 'app'
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

    if cmd == '/app':
        try:
            # rove_app_state importiert bewusst NICHTS aus bot.py (siehe Docstring dort) —
            # Score wird deshalb hier berechnet (Funktionen sind eh schon im Namespace) und nur
            # als fertiger Wert übergeben.
            from rove_app_state import build_app_state, APP_STATE_LINK_TTL_DAYS
            total_expenses = get_month_expenses(uid)
            score_result = calculate_clarity_score(uid, u, total_expenses)
            result = build_app_state(uid, score_result.get("total") or 0, score_result.get("rank_name") or "—")
        except Exception:
            logger.exception(f"App-State-Export fehlgeschlagen für User {uid}")
            bot.send_message(uid, "Konnte deinen App-Zugang gerade nicht vorbereiten — versuch's gleich nochmal.")
            return
        if result.get("url"):
            from urllib.parse import quote
            param = f"?state={quote(result['url'], safe='')}"
            app_url = f"https://getrove.de/app/{param}"
            pairing_code = result.get("pairing_code") or "—"
            bot.send_message(
                uid,
                "*Dein Rov.E-App-Zugang ist bereit.*\n\n"
                f"[App direkt öffnen]({app_url})\n\n"
                "Für dein Homescreen-Icon:\n"
                "Öffne Rov.E und tippe auf „Mit Telegram verbinden“.\n"
                f"Dein App-Code: `{pairing_code}`\n\n"
                f"Code und Link gelten {APP_STATE_LINK_TTL_DAYS} Tage. Mit /app bekommst du jederzeit einen neuen Zugang.",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        else:
            bot.send_message(
                uid,
                "App-Zugang ist auf dem Server noch nicht fertig eingerichtet "
                "(ROVE_APP_STATE_PUBLIC_BASE_URL fehlt)."
            )
        return

    if (u.get("onboarding_step") or 0) >= STEP_NORMAL:
        handle_month_transition(uid, u, bot)

    if cmd == '/start':
        if (u.get("onboarding_step") or 0) >= STEP_NORMAL:
            remaining, total_expenses, income, fixed = calculate_remaining_budget(u, uid)
            e = "🟢" if remaining > 200 else ("🟡" if remaining > 0 else "🔴")
            bot.send_message(
                uid,
                "*Rov.E ist bereit.*\n\n"
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

    elif cmd == '/ruhe':
        now_off = toggle_recap_muted(uid)
        if now_off:
            bot.send_message(
                uid,
                "Alles klar — kein Abend-Update mehr.\n\n"
                "Wieder einschalten: /ruhe"
            )
        else:
            bot.send_message(
                uid,
                "Abend-Update ist wieder an.\n"
                "Du bekommst abends eine kurze Zusammenfassung, wenn du getrackt hast."
            )

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
        cp_rank_line = (f"Noch *{pts_needed} RP* bis zum nächsten RP-Rang."
                        if pts_needed > 0 else "Höchster RP-Rang erreicht.")
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
            f"📊 *Rov.E Score: {score_data['total']}/100*\n"
            f"{score_data['rank_emoji']} *{score_data['rank_name']}*\n"
            f"Status: {score_data['phase']} · Proof: {score_data['proof_days']}/90 Tage"
            f"{unlock_line}\n\n"
            f"*Breakdown*\n"
            f"├ Budget Control:       {score_data['budget']}/25\n"
            f"├ Savings Execution:    {score_data['savings']}/25\n"
            f"├ Tracking Consistency: {score_data['tracking_days_90']} aktive Tage · {score_data['consistency']}/25\n"
            f"└ Financial Structure:  {score_data['structure']}/25\n\n"
            f"*Nächster Hebel:*\n{confirm_hint}\n\n"
            f"{cp_rank_emoji} RP-Level: *{cp_rank_name}* · {cp} RP\n"
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
        etf_savings = u.get("etf_savings") or 0
        cash_savings = u.get("cash_savings") or 0
        if etf_savings + cash_savings <= 0:
            bot.send_message(
                uid,
                "Ich sehe noch keine hinterlegte Sparrate.\n\n"
                "Leg sie zuerst im Onboarding oder über /settings fest. Danach kannst du sie mit /investiert bestätigen."
            )
            return

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

        new_investments = (u.get("current_investments") or 0) + etf_savings
        new_cash = (u.get("current_cash") or 0) + cash_savings
        update_user_field(uid, "current_investments", new_investments)
        update_user_field(uid, "current_cash", new_cash)
        if etf_savings > 0:
            save_investment_event(
                uid, etf_savings, asset_type="etf", asset_name="ETF-Sparrate",
                event_type="recurring_plan", source="investiert_command",
                note="Monatliche ETF-Sparrate bestätigt"
            )
        if cash_savings > 0:
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
            f"Sparrate bestätigt - *+20 RP* - Gesamt: {new_pts} RP\n"
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
        remaining, total_exp, income, fixed = calculate_remaining_budget(u, uid)
        savings = (u.get("etf_savings") or 0) + (u.get("cash_savings") or 0)
        e = "🟢" if remaining > 200 else ("🟡" if remaining > 0 else "🔴")

        bot.send_message(uid,
            "Ich habe deinen aktuellen Stand für dich im Blick.\n\n"
            f"*Monatsstatus*\n\n"
            f"Einnahmen: {income:.2f}€\n"
            f"Fixkosten: {fixed:.2f}€\n"
            f"Sparrate: {savings:.2f}€\n"
            f"Ausgaben: {total_exp:.2f}€\n"
            f"{'─' * 20}\n"
            f"{e} *Freies Restbudget: {remaining:.2f}€*\n\n"
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
        start_refinement_flow(uid)

    elif cmd == '/portfolio':
        start_portfolio_tracking_flow(uid)

    elif cmd == '/settings':
        update_user_field(uid, "onboarding_step", STEP_INCOME)
        bot.send_message(
            uid,
            "Du richtest dein Basisprofil jetzt neu ein.\n\n"
            "*Schritt 1 von 8:* Nettoeinkommen?\n\n"
            "_Mit zurück oder dem Menüpunkt Zurück gehst du einen Schritt zurück._",
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
            reverse_app_paid_expense(conn, uid, last["id"])
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

    # ─── ALLTAGSSPRACHE: Befehle ohne Slash verstehen ────────────────────
    if step >= STEP_NORMAL:
        COMMAND_ALIASES = {
            "hilfe": "/help", "help": "/help", "was kannst du": "/help",
            "score": "/score", "mein score": "/score", "punkte": "/score",
            "status": "/status", "monatsstatus": "/status",
            "stats": "/stats", "statistik": "/stats",
            "undo": "/undo", "rückgängig": "/undo", "ruckgangig": "/undo",
            "letzte löschen": "/undo",
            "badges": "/badges", "erfolge": "/badges",
            "ziel": "/goal", "mein ziel": "/goal", "sparziel": "/goal",
            "ruhe": "/ruhe",
        }
        alias_cmd = COMMAND_ALIASES.get(text_lower.rstrip("!?."))
        if alias_cmd:
            message.text = alias_cmd
            handle_commands(message)
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

    if step == STEP_NORMAL and is_refinement_text_request(text_lower):
        start_refinement_flow(uid)
        return

    if step == STEP_NORMAL and is_portfolio_tracking_request(text_lower):
        start_portfolio_tracking_flow(uid)
        return

    if step == STEP_NORMAL:
        budget_setup_reply = maybe_handle_budget_setup_response(uid, u, text_lower)
        if budget_setup_reply:
            bot.send_message(uid, budget_setup_reply, parse_mode="Markdown")
            return

        delete_budget_reply = maybe_delete_budget(uid, text_lower)
        if delete_budget_reply:
            bot.send_message(uid, delete_budget_reply, parse_mode="Markdown")
            return

        manual_budget_reply = maybe_apply_manual_budget(uid, text_lower)
        if manual_budget_reply:
            bot.send_message(uid, manual_budget_reply, parse_mode="Markdown")
            return

        budget_status_reply = maybe_answer_budget_status(uid, text_lower, u)
        if budget_status_reply:
            bot.send_message(uid, budget_status_reply, parse_mode="Markdown")
            return

    if step == STEP_START:
        bot.send_message(uid, "Schreib /start, dann richten wir Rov.E in Ruhe ein.")
        return

    if is_score_info_question(text_lower):
        bot.send_message(uid, build_score_info_answer(), parse_mode="Markdown")
        return

    if step == STEP_NORMAL:
        savings_reply = maybe_apply_savings_correction(uid, u, text_lower)
        if savings_reply:
            bot.send_message(uid, savings_reply, parse_mode="Markdown")
            return
        income_reply = maybe_apply_income_correction(uid, u, text_lower)
        if income_reply:
            bot.send_message(uid, income_reply, parse_mode="Markdown")
            return

    if (
        step == STEP_NORMAL
        and looks_like_profile_correction(text_lower)
        and not looks_like_investment_update(text_lower)
        and not looks_like_known_expense(text_input, text_lower, user_id=uid)
    ):
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

        category_rule_reply = maybe_apply_category_rule(uid, text_lower)
        if category_rule_reply:
            bot.send_message(uid, category_rule_reply, parse_mode="Markdown")
            return

        delete_budget_reply = maybe_delete_budget(uid, text_lower)
        if delete_budget_reply:
            bot.send_message(uid, delete_budget_reply, parse_mode="Markdown")
            return

        manual_budget_reply = maybe_apply_manual_budget(uid, text_lower)
        if manual_budget_reply:
            bot.send_message(uid, manual_budget_reply, parse_mode="Markdown")
            return

        budget_status_reply = maybe_answer_budget_status(uid, text_lower, u)
        if budget_status_reply:
            bot.send_message(uid, budget_status_reply, parse_mode="Markdown")
            return

        weekly_reply = maybe_answer_weekly_budget(uid, u, text_lower)
        if weekly_reply:
            bot.send_message(uid, weekly_reply, parse_mode="Markdown")
            return

        affordability_reply = maybe_answer_affordability(uid, u, text_lower)
        if affordability_reply:
            bot.send_message(uid, affordability_reply, parse_mode="Markdown")
            return

        if is_expense_overview_request(text_lower):
            bot.send_message(uid, build_expense_overview(uid, text_lower), parse_mode="Markdown")
            return

        profile_reply = maybe_answer_profile_finance(uid, u, text_lower)
        if profile_reply:
            bot.send_message(uid, profile_reply, parse_mode="Markdown")
            return

        category_reply = maybe_answer_category_spending(uid, text_lower)
        if category_reply:
            bot.send_message(uid, category_reply, parse_mode="Markdown")
            return

        if is_portfolio_performance_question(text_lower):
            bot.send_message(uid, build_portfolio_performance_answer(uid), parse_mode="Markdown")
            return

        portfolio_total_reply = maybe_update_portfolio_total(uid, text_lower)
        if portfolio_total_reply:
            bot.send_message(uid, portfolio_total_reply, parse_mode="Markdown")
            return

        portfolio_delete_reply = maybe_delete_portfolio_holding(uid, text_lower)
        if portfolio_delete_reply:
            bot.send_message(uid, portfolio_delete_reply, parse_mode="Markdown")
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

        percent_value = parse_percent(text_input)
        percent_note = ""
        if step in {STEP_ETF_SAVINGS, STEP_CASH_SAVINGS} and percent_value is not None:
            income_base = (u.get("income") or 0) + (u.get("other_income") or 0)
            if income_base <= 0:
                bot.send_message(uid, "Ich brauche dafür zuerst dein Einkommen. Schreib die Sparrate bitte als Euro-Betrag.")
                return
            val = round(income_base * percent_value / 100, 2)
            percent_note = f"{percent_value:g}% entsprechen {val:.2f}€/Monat.\n\n"
        else:
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
            bot.send_message(uid, percent_note + msg, parse_mode="Markdown")

        elif step == STEP_CASH_SAVINGS:
            update_user_field(uid, "cash_savings", val)
            update_user_field(uid, "onboarding_step", STEP_NORMAL)
            update_user_field(uid, "current_month", date.today().strftime("%Y-%m"))
            u_fresh = get_or_create_user(uid)
            new_badges = check_wealth_badges(uid, u_fresh)
            sparrate = (u_fresh.get("etf_savings") or 0) + val

            bot.send_message(uid,
                f"🎉 *Einrichtung abgeschlossen!*\n\n"
                f"{percent_note}"
                f"Sparrate: {sparrate:.2f}€/Monat\n\n"
                "Dein Profil steht. Jetzt machen wir direkt den ersten echten Test.\n\n"
                "_Übrigens: Sparst du in ETFs? Schreib jederzeit `Portfolio`, dann tracke ich den Kurs für dich mit._",
                parse_mode="Markdown"
            )
            # Badges dezent als separate Zeilen falls vorhanden
            if new_badges:
                send_badge_summary(bot, uid, new_badges)
            ask_first_expense_after_onboarding(uid)
        return

    # ─── VERFEINERN-FLOW ────────────────────────────────────────────────
    if STEP_ADAPT_HOUSING <= step <= STEP_ADAPT_CREDITS:
        nums = [float(x.replace(',', '.')) for x in re.findall(r'\d+(?:[.,]\d+)?', text_input)]
        if not nums:
            bot.send_message(uid, "Das habe ich noch nicht sicher erkannt.\n\nSchreib die Werte bitte kurz, z.B. `Miete 800 Strom 60`.", parse_mode="Markdown")
            return

        details = u.get("details", {})
        routed_cross_step = route_cross_step_fixed_cost(text_input, text_lower, nums, details)

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
            if not routed_cross_step:
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
            if not routed_cross_step:
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
                "abo": "abo",
                "abos": "abo",
                "abonnement": "abo",
                "jahresabo": "abo",
                "mitgliedschaft": "abo",
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
            if not routed_cross_step:
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
            if not routed_cross_step:
                details["versicherungen"] = merge_number_defaults(parsed, nums, ["haftpflicht", "bu", "rechtsschutz", "autoversicherung"])
            update_user_field(uid, "fixed_costs_details", json.dumps(details))
            update_user_field(uid, "onboarding_step", STEP_ADAPT_CREDITS)
            bot.send_message(uid, "💳 *Teil 5: Kredite*\nImmobilie, Auto, Konsum?\n_(Falls keine → 0)_", parse_mode="Markdown")

        elif step == STEP_ADAPT_CREDITS:
            parsed = parse_labeled_amounts(text_input, {
                "kredit": "kredit",
                "kreditrate": "kredit",
                "kredite": "kredit",
                "schuld": "kredit",
                "schulden": "kredit",
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
            credit_values = merge_number_defaults(parsed, nums, ["immobilie", "hausgeld", "hausverwalter"])
            monthly_debt, total_debt = parse_debt_values(text_lower)
            if monthly_debt is not None:
                credit_values["kredit"] = monthly_debt
            if total_debt is not None:
                credit_values["restschuld"] = total_debt
            details["kredite"] = credit_values
            total_fixed = sum(
                float(v) for cat in details.values()
                if isinstance(cat, dict)
                for key, v in cat.items()
                if key not in {"restschuld", "gesamtbetrag", "schulden_gesamt"}
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

    if step == STEP_NORMAL:
        delete_expense_reply = maybe_delete_logged_expense(uid, text_input, text_lower)
        if delete_expense_reply:
            bot.send_message(uid, delete_expense_reply, parse_mode="Markdown")
            return

    # ─── HYBRID-TRACKER ──────────────────────────────────────────────────
    expense_amounts = extract_amounts(text_lower, exclude_years=True)

    if step == STEP_NORMAL and len(expense_amounts) == 1 and abs(expense_amounts[0]) < 0.01:
        bot.send_message(
            uid,
            "0€ speichere ich nicht als Ausgabe.\n\n"
            "Wenn du etwas überspringen willst, schreib einfach `Nein` oder `Abbrechen`.\n"
            "Wenn du Fixkosten hinterlegen willst, schreib `Verfeinern`.",
            parse_mode="Markdown"
        )
        return

    if len(expense_amounts) > 1 and not looks_like_investment_update(text_lower) and not is_portfolio_snapshot_input(text_lower):
        parsed_items = parse_hybrid_expense_items(text_input, expense_amounts, user_id=uid)
        if parsed_items:
            with get_db() as conn:
                for item in parsed_items:
                    conn.execute(
                        "INSERT INTO expenses (user_id, amount, category, merchant, description) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (uid, item["amount"], item["category"], item["merchant"], "Via Hybrid Multi")
                    )
                conn.commit()

            cp_earned = handle_daily_activity(uid, bot)
            cp_str = build_tracking_note(cp_earned)
            bot.send_message(
                uid,
                format_expense_confirmation(parsed_items, cp_str, user_id=uid),
                parse_mode="Markdown"
            )
            return

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
            cp_str = build_tracking_note(cp_earned)
            cp_suffix = f" · {cp_str}" if cp_str else ""
            total_wealth = amount_val + (u.get("current_cash") or 0)
            bot.send_message(
                uid,
                f"📊 Depotstand aktualisiert: *{amount_val:.2f}€*\n"
                f"Nettovermögen: *{total_wealth:.2f}€*{cp_suffix}",
                parse_mode="Markdown"
            )
            return

        if any(word in text_lower for word in INVESTMENT_INPUTS):
            direction = "out" if is_investment_outflow_request(text_lower) else "in"
            if should_confirm_investment_classification(text_lower, amount_val, direction):
                user_pending_actions[uid] = {
                    "type": "confirm_investment_classification",
                    "amount": amount_val,
                    "direction": direction,
                    "text_lower": text_lower,
                }
                bot.send_message(
                    uid,
                    "Kurze Rückfrage, damit dein Report sauber bleibt:\n\n"
                    f"Sind die *{amount_val:.2f}€* neu in diesem Monat investiert worden "
                    "oder war das Vermögen schon da und du trägst es nur nach?\n\n"
                    "`Neu` - zählt als Monatsfortschritt\n"
                    "`Bestand` - erhöht dein Vermögen, zählt aber nicht als Monatsleistung",
                    parse_mode="Markdown"
                )
                return

            message, new_badges = apply_investment_change(
                uid, u, amount_val, text_lower, direction
            )
            bot.send_message(uid, message, parse_mode="Markdown")
            send_badge_summary(bot, uid, new_badges)
            return

        category_found, merchant_found, direct_category_label = detect_expense_label(text_input, text_lower, user_id=uid)

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

            cp_str = build_tracking_note(cp_earned)
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

    if looks_like_investment_update(text_lower) and is_investment_outflow_request(text_lower):
        bot.send_message(
            uid,
            "Sag mir kurz den Betrag, den ich aus deinen Investments herausnehmen soll.\n\n"
            "Zum Beispiel:\n"
            "`Lösche 10.000€ Investment Krypto`\n"
            "`Entferne 500€ ETF`\n\n"
            "Wenn nur der aktuelle Stand falsch ist, schreib:\n"
            "`Depotstand 19.250€`",
            parse_mode="Markdown"
        )
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
        frei, total_exp, income, fixed = calculate_remaining_budget(u, uid)
        e = "🟢" if frei > 200 else ("🟡" if frei > 0 else "🔴")
        bot.send_message(uid,
            f"💸 *Budget-Check*\n\n{e} Noch *{frei:.2f}€* frei verfügbar.\n\n"
            "Sparrate und Fixkosten sind dabei schon abgezogen.",
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

    if looks_like_unclear_expense_attempt(text_lower, expense_amounts):
        bot.send_message(uid, build_unclear_amount_answer(expense_amounts), parse_mode="Markdown")
        return

    if not expense_amounts:
        label_category, label_merchant, label_direct = detect_expense_label(text_input, text_lower, user_id=uid)
        if label_category and label_merchant:
            bot.send_message(
                uid,
                f"Welchen Betrag soll ich für {label_direct or label_merchant} erfassen?\n\n"
                f"Zum Beispiel:\n`{label_direct or label_merchant} 12,50€`",
                parse_mode="Markdown"
            )
            return

    # ─── KI-FALLBACK (Nur für komplexe/unbekannte Anfragen) ─────────────
    bot.send_chat_action(uid, 'typing')
    user_context = build_ai_user_context(uid, u)
    prompt = f"""Du bist Rov.E, ein Finanz-Assistent. Antworte auf Deutsch.
Datum: {date.today().isoformat()}
Kategorien: LEBENSMITTEL, MOBILITAET, RESTAURANTS, ABOS, FREIZEIT, SHOPPING, VERSICHERUNG, MIETE, GESUNDHEIT, DROGERIE, PFLEGE, SONSTIGES

Du beantwortest Fragen zu persönlichen Finanzen, Ausgaben, Budget, Sparzielen, Vermögensaufbau, ETFs, Fonds, Sparplänen und Reports.
Nutze das Nutzerprofil unten aktiv, wenn es für die Frage relevant ist.
Wenn eine Information im Profil steht, behandle sie als bekannt und frage nicht erneut danach.
Erfinde keine Zahlen. Wenn eine Zahl nicht im Profil oder in den Ausgaben steht, sage kurz, dass sie noch nicht hinterlegt ist.

ETF- und Finanzbildungsfragen sind erlaubt. Erklaere ruhig, klar und hilfreich.
Keine Panik-Disclaimer. Keine konkreten Kauf-/Verkaufsempfehlungen für einzelne Produkte.
Blocke nur Off-Topic-Fragen, z.B. Buch schreiben, Weltpolitik, Hausaufgaben, Rezepte oder allgemeines Gelaber ohne Finanzbezug.

Wenn die Nutzereingabe wie eine Ausgabe aussieht und einen Betrag enthält, kategorisiere sie.
Beispiele für unbekannte Händler oder Aktivitäten:
- Paddelbootfahren -> FREIZEIT
- Kletterhalle -> FREIZEIT
- Copyshop -> SONSTIGES
- Hostinger oder Domain -> SONSTIGES
- Online-Einkauf -> SHOPPING
- Pesto, Brot, Nudeln -> LEBENSMITTEL

Wichtig:
- Nutze nur Beträge, die exakt in der Nutzereingabe vorkommen.
- Buche niemals Ausgaben, wenn der Nutzer nur eine Frage stellt, z.B. "Kann ich mir 100€ leisten?"
- Wenn du unsicher bist, nutze SONSTIGES und einen kurzen Händlernamen aus der Eingabe.

Antwortformat (reines JSON):
{{
  "expenses": [],
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
        allowed_categories = {
            "LEBENSMITTEL", "MOBILITAET", "RESTAURANTS", "ABOS", "FREIZEIT",
            "SHOPPING", "VERSICHERUNG", "MIETE", "GESUNDHEIT", "DROGERIE",
            "PFLEGE", "SONSTIGES"
        }
        question_like_input = text_lower.startswith((
            "wie ", "was ", "warum ", "wann ", "wo ", "wer ",
            "kann ", "kannst ", "könnte ", "koennte ", "soll "
        ))
        allow_ai_booking = bool(expense_amounts) and not question_like_input
        for exp in (data.get("expenses", []) if allow_ai_booking else []):
            try:
                amt = float(exp.get("amount", 0))
                if amt > 0 and any(abs(amt - known_amount) <= 0.01 for known_amount in expense_amounts):
                    category = (exp.get("category", "SONSTIGES") or "SONSTIGES").upper()
                    if category not in allowed_categories:
                        category = "SONSTIGES"
                    merchant = exp.get("merchant", "Unbekannt") or "Unbekannt"
                    if str(merchant).strip().lower() == "unbekannt":
                        merchant = extract_merchant_name(text_input)
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
            cp_str = build_tracking_note(cp_earned)
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


def report_now() -> datetime:
    """Keep every report-queue timestamp in the scheduler's Berlin timezone."""
    return datetime.now(REPORT_TIMEZONE).replace(tzinfo=None)


def random_report_time_for_today() -> str:
    now = report_now()
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
    now = report_now().strftime("%Y-%m-%d %H:%M:%S")
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
    now = report_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute("UPDATE report_jobs SET status = 'sent', last_error = '', updated_at = ? WHERE id = ?", (now, job_id))
        conn.commit()


def mark_report_job_failed(job: dict, error: str):
    now_dt = report_now()
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
    now = report_now().strftime("%Y-%m-%d %H:%M:%S")
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


def cleanup_expired_web_reports():
    try:
        import rove_web_report_renderer
        removed = rove_web_report_renderer.cleanup_expired_reports()
        if removed:
            logger.info(f"Abgelaufene Web-Reports entfernt: {removed}")
    except Exception as e:
        logger.warning(f"Web-Report-Cleanup fehlgeschlagen: {e}")


def archive_old_pdf_reports():
    """Komprimiert alte PDF-Reports (reports/archive/*.pdf.gz), loescht nichts inhaltlich."""
    try:
        import report_engine
        archived = report_engine.archive_old_reports()
        if archived:
            logger.info(f"PDF-Reports archiviert: {archived}")
    except Exception as e:
        logger.warning(f"PDF-Report-Archivierung fehlgeschlagen: {e}")


def update_portfolio_prices_job():
    """Taeglicher Kurs-Update fuer alle Portfolio-Holdings. Voll fallback-gesichert -
    ein Fehler hier darf niemals den Bot oder andere Scheduler-Jobs beeintraechtigen."""
    try:
        updated = update_all_portfolio_prices()
        if updated:
            logger.info(f"Portfolio-Kurse aktualisiert: {updated}")
    except Exception as e:
        logger.warning(f"Portfolio-Kurs-Update fehlgeschlagen: {e}")

# ====================== REPORT SCHEDULER ======================
REPORT_SCHEDULER = None

# ====================== ABEND-RECAP ======================
RECAP_OFF_BADGE = "setting_recap_off"


def is_recap_muted(user_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_key = ?",
            (user_id, RECAP_OFF_BADGE)
        ).fetchone()
    return row is not None


def toggle_recap_muted(user_id: int) -> bool:
    """Schaltet den Abend-Recap um. Gibt True zurueck, wenn er jetzt AUS ist."""
    with get_db() as conn:
        if conn.execute(
            "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_key = ?",
            (user_id, RECAP_OFF_BADGE)
        ).fetchone():
            conn.execute(
                "DELETE FROM user_badges WHERE user_id = ? AND badge_key = ?",
                (user_id, RECAP_OFF_BADGE)
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO user_badges (user_id, badge_key) VALUES (?, ?)",
            (user_id, RECAP_OFF_BADGE)
        )
        conn.commit()
        return True


def get_evening_recap_candidates() -> list:
    """Nur User, die HEUTE getrackt haben. Inaktive werden bewusst nicht angeschrieben."""
    with get_db() as conn:
        return conn.execute(
            """SELECT u.user_id
               FROM users u
               LEFT JOIN user_access a ON a.user_id = u.user_id
               WHERE COALESCE(a.status, 'approved') = 'approved'
                 AND u.onboarding_step >= ?
                 AND EXISTS (
                    SELECT 1 FROM expenses e
                    WHERE e.user_id = u.user_id
                      AND DATE(e.created_at) = DATE('now', 'localtime')
                 )
                 AND NOT EXISTS (
                    SELECT 1 FROM user_badges b
                    WHERE b.user_id = u.user_id AND b.badge_key = ?
                 )""",
            (STEP_NORMAL, RECAP_OFF_BADGE)
        ).fetchall()


def build_evening_recap(user_id: int) -> str:
    with get_db() as conn:
        today_rows = conn.execute(
            """SELECT category, COUNT(*) AS cnt, SUM(amount) AS total
               FROM expenses
               WHERE user_id = ? AND DATE(created_at) = DATE('now', 'localtime')
               GROUP BY category ORDER BY total DESC""",
            (user_id,)
        ).fetchall()
        week_row = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS total
               FROM expenses
               WHERE user_id = ?
                 AND DATE(created_at) >= DATE('now', 'localtime', 'weekday 0', '-6 days')""",
            (user_id,)
        ).fetchone()
        user_row = conn.execute(
            "SELECT streak_days FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

    if not today_rows:
        return ""

    day_total = sum(r["total"] or 0 for r in today_rows)
    day_count = sum(r["cnt"] or 0 for r in today_rows)
    top = today_rows[0]
    top_emoji = CATEGORY_EMOJIS.get(top["category"], "")
    week_total = week_row["total"] or 0
    streak = (user_row["streak_days"] or 0) if user_row else 0

    lines = [
        f"*Dein Tag:* {day_count} {'Ausgabe' if day_count == 1 else 'Ausgaben'} · {day_total:.2f}€"
    ]
    if len(today_rows) > 1:
        lines.append(f"Größter Posten: {top_emoji} {top['category'].title()} ({top['total']:.2f}€)")
    lines.append(f"Diese Woche: {week_total:.2f}€")
    if streak >= 2:
        lines.append(f"Streak: {streak} Tage 🔥")
    lines.append("_(Abend-Update abschaltbar mit /ruhe)_")
    return "\n".join(lines)


def send_evening_recaps():
    candidates = get_evening_recap_candidates()
    sent = 0
    for row in candidates:
        uid = row["user_id"]
        try:
            text = build_evening_recap(uid)
            if text:
                bot.send_message(uid, text, parse_mode="Markdown")
                sent += 1
        except Exception as e:
            logger.warning(f"Abend-Recap an {uid} fehlgeschlagen: {e}")
    logger.info(f"Abend-Recap: {sent} gesendet, {len(candidates)} Kandidaten.")


def setup_monthly_report_scheduler():
    """Startet Queue-Erzeugung und Worker fuer Monatsreports."""
    if BackgroundScheduler is None or CronTrigger is None:
        logger.warning("APScheduler ist nicht installiert. Monatliche Reports werden nicht automatisch versendet.")
        return None

    scheduler = BackgroundScheduler(timezone="Europe/Berlin")
    scheduler.add_job(
        create_monthly_report_jobs,
        trigger=CronTrigger(day=1, hour=7, minute=55, timezone="Europe/Berlin"),
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
        trigger=CronTrigger(day="1-2", minute=5, timezone="Europe/Berlin"),
        id="ensure_monthly_report_jobs",
        replace_existing=True,
        misfire_grace_time=REPORT_CREATION_MISFIRE_GRACE_SECONDS,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        send_evening_recaps,
        trigger=CronTrigger(hour=20, minute=30, timezone="Europe/Berlin"),
        id="send_evening_recaps",
        replace_existing=True,
        misfire_grace_time=1800,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        cleanup_expired_web_reports,
        trigger=CronTrigger(hour=3, minute=10, timezone="Europe/Berlin"),
        id="cleanup_expired_web_reports",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        archive_old_pdf_reports,
        trigger=CronTrigger(hour=3, minute=20, timezone="Europe/Berlin"),
        id="archive_old_pdf_reports",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        update_portfolio_prices_job,
        trigger=CronTrigger(hour=22, minute=30, timezone="Europe/Berlin"),
        id="update_portfolio_prices",
        replace_existing=True,
        misfire_grace_time=3600,
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
