"""Server-side foundation for in-app feature announcements.

Sprint 1 stores global definitions once and creates per-user state only after an
interaction. The browser may receive the prepared state payload but does not use
it yet, so the existing bell feed remains the source of visible activity.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from rove_financial_accounts import is_feature_enabled


ANNOUNCEMENT_PRIORITIES = frozenset({"security", "major", "minor"})
SAFE_DEEP_LINKS = frozenset({
    "talk", "score", "reports", "settings", "asset-krypto", "bell",
    "analysis", "analysis-merchants", "monthly-checkin", "goals", "contracts",
})
PROMINENT_DAYS = 90
_FEATURE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,79}")

# Releases are published explicitly by the deployment command, never while a
# user merely loads state. This keeps the release moment and audience clear.
DEFAULT_FEATURE_ANNOUNCEMENTS: tuple[dict[str, str], ...] = (
    {
        "feature_id": "rove_ai_v1",
        "version": "1",
        "title": "Rov.E AI",
        "short_message": "Frag Rov.E direkt zu deinen Finanzen.",
        "message": "Rov.E AI beantwortet Finanzfragen und hilft dir, deine Zahlen besser einzuordnen.",
        "priority": "major",
        "deep_link": "talk",
        "tutorial_type": "quick_examples",
    },
    {
        "feature_id": "crypto_tracking_v1",
        "version": "1",
        "title": "Crypto Tracking",
        "short_message": "Erfasse Kryptowerte per Suche oder Screenshot.",
        "message": "Lege deine Coins einzeln an und behalte ihre Werte in Rov.E im Blick.",
        "priority": "major",
        "deep_link": "asset-krypto",
        "tutorial_type": "steps",
    },
    {
        "feature_id": "top_merchants_v1",
        "version": "1",
        "title": "Top-Haendler",
        "short_message": "Sieh, wo du diesen Monat am meisten ausgegeben hast.",
        "message": "In der Analyse findest du deine wichtigsten Haendler und Ausgabenmuster.",
        "priority": "minor",
        "deep_link": "analysis-merchants",
        "tutorial_type": "none",
    },
    {
        "feature_id": "monthly_checkin_v1",
        "version": "1",
        "title": "Monatscheck",
        "short_message": "Behalte deine faelligen Monatsaktionen im Blick.",
        "message": "Rov.E erinnert dich nur dann an Einkommen, ETF-Ausfuehrungen oder den Monatsabschluss, wenn etwas wirklich faellig ist.",
        "priority": "minor",
        "deep_link": "monthly-checkin",
        "tutorial_type": "steps",
    },
)


def ensure_feature_announcement_tables(conn: sqlite3.Connection) -> None:
    """Create the additive, rerunnable announcement schema."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_feature_announcements (
            feature_id TEXT PRIMARY KEY,
            version TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            short_message TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL CHECK(priority IN ('security', 'major', 'minor')),
            deep_link TEXT,
            feature_flag TEXT,
            tutorial_type TEXT,
            always_relevant INTEGER NOT NULL DEFAULT 0 CHECK(always_relevant IN (0, 1)),
            published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            active_from TEXT,
            active_until TEXT,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_feature_announcement_state (
            user_id INTEGER NOT NULL,
            feature_id TEXT NOT NULL,
            seen_at TEXT,
            opened_at TEXT,
            dismissed_at TEXT,
            completed_at TEXT,
            coach_shown_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, feature_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(feature_id) REFERENCES app_feature_announcements(feature_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feature_announcement_state_user ON "
        "app_feature_announcement_state(user_id, feature_id)"
    )
    state_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(app_feature_announcement_state)")
    }
    if "coach_shown_at" not in state_columns:
        conn.execute(
            "ALTER TABLE app_feature_announcement_state ADD COLUMN coach_shown_at TEXT"
        )


def seed_default_feature_announcements(conn: sqlite3.Connection) -> list[str]:
    """Publish the initial release set once without creating user state rows."""
    ensure_feature_announcement_tables(conn)
    published: list[str] = []
    for announcement in DEFAULT_FEATURE_ANNOUNCEMENTS:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO app_feature_announcements (
                   feature_id, version, title, short_message, message, priority,
                   deep_link, tutorial_type, published_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                announcement["feature_id"],
                announcement["version"],
                announcement["title"],
                announcement["short_message"],
                announcement["message"],
                announcement["priority"],
                announcement["deep_link"],
                announcement["tutorial_type"],
            ),
        )
        if cursor.rowcount:
            published.append(announcement["feature_id"])
    return published


def _account_created_at(conn: sqlite3.Connection, user_id: int) -> str:
    row = conn.execute(
        """SELECT MIN(created_at) FROM app_accounts
             WHERE user_id = ? AND TRIM(COALESCE(verified_at, '')) <> ''""",
        (user_id,),
    ).fetchone()
    return str(row[0] or "") if row else ""


def _safe_deep_link(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if candidate in SAFE_DEEP_LINKS else None


def _usage_completed(conn: sqlite3.Connection, user_id: int, feature_id: str) -> bool:
    """Only boolean usage signals; no financial values enter announcement state."""
    feature = feature_id.casefold()
    if feature.startswith("rove_ai"):
        try:
            return bool(conn.execute(
                "SELECT 1 FROM app_ai_conversation_messages WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone())
        except sqlite3.OperationalError:
            return False
    if feature.startswith("crypto"):
        try:
            return bool(conn.execute(
                """SELECT 1 FROM portfolio_holdings
                     WHERE user_id = ? AND LOWER(COALESCE(instrument_type, '')) = 'crypto'
                     LIMIT 1""",
                (user_id,),
            ).fetchone())
        except sqlite3.OperationalError:
            return False
    if feature.startswith("report"):
        try:
            return bool(conn.execute(
                "SELECT 1 FROM report_jobs WHERE user_id = ? AND status = 'sent' LIMIT 1",
                (user_id,),
            ).fetchone())
        except sqlite3.OperationalError:
            return False
    return False


def _eligible_rows(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    account_created_at = _account_created_at(conn, user_id)
    rows = conn.execute(
        """SELECT a.*, s.seen_at, s.opened_at, s.dismissed_at, s.completed_at,
                  s.coach_shown_at
             FROM app_feature_announcements a
             LEFT JOIN app_feature_announcement_state s
               ON s.feature_id = a.feature_id AND s.user_id = ?
            WHERE a.is_active = 1
              AND datetime(COALESCE(NULLIF(a.active_from, ''), a.published_at)) <= datetime('now', 'localtime')
              AND (NULLIF(a.active_until, '') IS NULL OR datetime(a.active_until) >= datetime('now', 'localtime'))
            ORDER BY CASE a.priority WHEN 'security' THEN 0 WHEN 'major' THEN 1 ELSE 2 END,
                     datetime(a.published_at) DESC, a.feature_id ASC""",
        (user_id,),
    ).fetchall()
    eligible: list[sqlite3.Row] = []
    for row in rows:
        feature_flag = str(row["feature_flag"] or "").strip()
        if feature_flag and not is_feature_enabled(conn, user_id, feature_flag):
            continue
        if not bool(row["always_relevant"]) and account_created_at and str(row["published_at"] or "") < account_created_at:
            continue
        eligible.append(row)
    return eligible


def _payload(row: sqlite3.Row, *, usage_completed: bool) -> dict[str, Any]:
    completed = bool(row["completed_at"]) or usage_completed
    return {
        "feature_id": str(row["feature_id"]),
        "version": str(row["version"] or ""),
        "title": str(row["title"]),
        "short_message": str(row["short_message"] or ""),
        "message": str(row["message"] or ""),
        "priority": str(row["priority"]),
        "deep_link": _safe_deep_link(row["deep_link"]),
        "tutorial_type": str(row["tutorial_type"] or "") or None,
        "published_at": str(row["published_at"] or ""),
        "state": {
            "seen": bool(row["seen_at"]),
            "opened": bool(row["opened_at"]),
            "dismissed": bool(row["dismissed_at"]),
            "completed": completed,
            "coach_shown": bool(row["coach_shown_at"]),
        },
    }


def get_feature_announcements_for_user(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """Returns user-scoped prepared state without creating any state rows."""
    ensure_feature_announcement_tables(conn)
    prominent: list[dict[str, Any]] = []
    archive: list[dict[str, Any]] = []
    for row in _eligible_rows(conn, user_id):
        usage_completed = _usage_completed(conn, user_id, str(row["feature_id"]))
        item = _payload(row, usage_completed=usage_completed)
        archive.append(item)
        is_recent = bool(conn.execute(
            "SELECT datetime(?) >= datetime('now', 'localtime', ?) ",
            (row["published_at"], f"-{PROMINENT_DAYS} days"),
        ).fetchone()[0])
        if is_recent and not item["state"]["dismissed"] and not item["state"]["completed"]:
            prominent.append(item)
    unseen_count = sum(1 for item in prominent if not item["state"]["seen"])
    return {"unseen_count": unseen_count, "eligible": prominent, "archive": archive}


def _find_eligible_feature(conn: sqlite3.Connection, user_id: int, feature_id: str) -> sqlite3.Row | None:
    if not _FEATURE_ID_RE.fullmatch(feature_id or ""):
        return None
    return next((row for row in _eligible_rows(conn, user_id) if row["feature_id"] == feature_id), None)


def claim_coach_announcement(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    finance_action_due: bool,
) -> dict[str, Any] | None:
    """Claim at most one new security/major announcement for this user's coach."""
    ensure_feature_announcement_tables(conn)
    if finance_action_due:
        return None
    for row in _eligible_rows(conn, user_id):
        if str(row["priority"]) not in {"security", "major"}:
            continue
        if any((row["seen_at"], row["opened_at"], row["dismissed_at"], row["completed_at"], row["coach_shown_at"])):
            continue
        feature_id = str(row["feature_id"])
        if _usage_completed(conn, user_id, feature_id):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO app_feature_announcement_state (user_id, feature_id) VALUES (?, ?)",
            (user_id, feature_id),
        )
        updated = conn.execute(
            """UPDATE app_feature_announcement_state
                  SET coach_shown_at = CURRENT_TIMESTAMP,
                      updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND feature_id = ? AND coach_shown_at IS NULL""",
            (user_id, feature_id),
        )
        if not updated.rowcount:
            continue
        item = _payload(row, usage_completed=False)
        item["state"]["coach_shown"] = True
        return item
    return None


def mark_feature_announcement(conn: sqlite3.Connection, user_id: int, feature_id: str, action: str) -> bool:
    """Creates one lazy state row and preserves each action's earliest timestamp."""
    ensure_feature_announcement_tables(conn)
    if _find_eligible_feature(conn, user_id, feature_id) is None:
        return False
    actions = {
        "seen": ("seen_at",),
        "opened": ("seen_at", "opened_at"),
        "dismissed": ("dismissed_at",),
        "completed": ("seen_at", "opened_at", "completed_at"),
        "coach_shown": ("coach_shown_at",),
    }
    columns = actions.get(action)
    if not columns:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO app_feature_announcement_state (user_id, feature_id) VALUES (?, ?)",
        (user_id, feature_id),
    )
    assignments = ", ".join(f"{column} = COALESCE({column}, CURRENT_TIMESTAMP)" for column in columns)
    conn.execute(
        f"UPDATE app_feature_announcement_state SET {assignments}, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ? AND feature_id = ?",
        (user_id, feature_id),
    )
    return True
