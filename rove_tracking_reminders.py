"""Dezente taegliche Tracking-Erinnerung ueber Rov.Es bestehenden Web-Push.

Der Timer darf beliebig oft laufen. Pro lokalem Kalendertag reserviert der Worker vor dem
Versand genau einen Event-Key, damit Neustarts oder parallele Aufrufe keinen Doppelpush erzeugen.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rove_app_api import (
    db,
    ensure_push_preferences_table,
    ensure_push_table,
    push_available,
    send_push_to_user,
)


FALLBACK_TIMEZONE = "Europe/Berlin"
EVENT_KEY = "tracking_reminder"


def ensure_delivery_log(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_push_delivery_log (
            user_id   INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            month_key TEXT NOT NULL,
            sent_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, event_key, month_key)
        )"""
    )


def user_timezone(value: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or FALLBACK_TIMEZONE))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(FALLBACK_TIMEZONE)


def utc_bounds_for_local_day(local_now: datetime) -> tuple[str, str]:
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return (
        start_utc.strftime("%Y-%m-%d %H:%M:%S"),
        end_utc.strftime("%Y-%m-%d %H:%M:%S"),
    )


def first_name(value: object) -> str:
    name = " ".join(str(value or "").strip().split())
    return name.split(" ", 1)[0][:40] if name else ""


def send_due_tracking_reminders(now_utc: datetime | None = None) -> dict[str, int]:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    stats = {"checked": 0, "due": 0, "skipped_tracked": 0, "sent": 0}
    if not push_available():
        return stats

    with db() as conn:
        ensure_push_table(conn)
        ensure_push_preferences_table(conn)
        ensure_delivery_log(conn)
        rows = conn.execute(
            """SELECT p.user_id, p.timezone,
                      COALESCE(NULLIF(TRIM(aa.display_name), ''),
                               NULLIF(TRIM(ua.display_name), ''), '') AS display_name
                 FROM app_push_preferences p
                 JOIN users u ON u.user_id = p.user_id
                 LEFT JOIN user_access ua ON ua.user_id = p.user_id
                 LEFT JOIN app_accounts aa ON aa.id = (
                     SELECT id FROM app_accounts
                      WHERE user_id = p.user_id ORDER BY id DESC LIMIT 1
                 )
                WHERE p.tracking_reminder_enabled = 1
                  AND u.onboarding_step >= 10
                  AND COALESCE(ua.status, 'approved') IN ('approved', 'app_only')
                  AND EXISTS (
                      SELECT 1 FROM app_push_subscriptions s WHERE s.user_id = p.user_id
                  )"""
        ).fetchall()

        for row in rows:
            stats["checked"] += 1
            local_now = now_utc.astimezone(user_timezone(row["timezone"]))
            if local_now.hour != 20:
                continue
            stats["due"] += 1
            local_date = local_now.date().isoformat()
            start_utc, end_utc = utc_bounds_for_local_day(local_now)
            tracked = conn.execute(
                """SELECT 1 FROM expenses
                    WHERE user_id = ? AND datetime(created_at) >= datetime(?)
                      AND datetime(created_at) < datetime(?) LIMIT 1""",
                (int(row["user_id"]), start_utc, end_utc),
            ).fetchone()
            if tracked:
                stats["skipped_tracked"] += 1
                continue

            # Vor dem externen Push reservieren: maximal einmal ist wichtiger als ein Retry nach
            # einem unklaren Netzwerkabbruch, der sonst zwei sichtbare Nachrichten erzeugen kann.
            inserted = conn.execute(
                """INSERT OR IGNORE INTO app_push_delivery_log
                       (user_id, event_key, month_key)
                   VALUES (?, ?, ?)""",
                (int(row["user_id"]), EVENT_KEY, local_date),
            ).rowcount
            conn.commit()
            if not inserted:
                continue

            name = first_name(row["display_name"])
            sent = send_push_to_user(
                conn,
                int(row["user_id"]),
                f"Hi {name} 👋" if name else "Kurzer Rov.E Check 👋",
                "Schon deine Ausgaben von heute getrackt?",
                tag=f"rove-tracking-{local_date}",
                url="./#add",
            )
            conn.commit()
            if sent:
                stats["sent"] += sent
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sende faellige Rov.E Tracking-Erinnerungen.")
    parser.add_argument("--at", help="Testzeitpunkt als ISO-8601, z. B. 2026-08-14T18:05:00+00:00")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    chosen = datetime.fromisoformat(args.at) if args.at else None
    print(send_due_tracking_reminders(chosen))
