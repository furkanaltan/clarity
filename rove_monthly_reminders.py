"""Dezente Monatscheck-Pushes fuer die installierte Rov.E-App.

Der Job wird einmal taeglich per systemd-Timer gestartet. Er schickt nur am individuellen
Zahltag genau eine Erinnerung und nur, wenn Einkommen, Fixkosten oder Sparrate noch offen sind.
Der Versand selbst bleibt im App-Server, damit VAPID-Schluessel und Push-Bibliothek nicht im Bot
landen.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from rove_app_api import db, send_push_to_user
from rove_app_state import get_monthly_checkin_actions


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


def send_due_monthly_reminders(today: date | None = None) -> int:
    today = today or date.today()
    month_key = today.strftime("%Y-%m")

    with db() as conn:
        ensure_delivery_log(conn)
        rows = conn.execute(
            "SELECT * FROM users WHERE onboarding_step >= 10",
        ).fetchall()

        delivered = 0
        for row in rows:
            for action in get_monthly_checkin_actions(conn, int(row["user_id"]), dict(row)):
                # Month close is deliberately surfaced on the first app start in
                # the following month, not pushed by the daily background timer.
                if action["kind"] == "month_close" or action.get("dueDate") != today.isoformat():
                    continue
                event_key = f"monthly_check:{action['id']}"
                exists = conn.execute(
                    "SELECT 1 FROM app_push_delivery_log WHERE user_id = ? AND event_key = ? AND month_key = ?",
                    (int(row["user_id"]), event_key, month_key),
                ).fetchone()
                if exists:
                    continue
                sent = send_push_to_user(
                    conn, int(row["user_id"]), action["title"],
                    action.get("detail") or "Öffne deinen Monatscheck.",
                    tag=f"rove-monthly-check-{action['id']}-{month_key}", url="./",
                )
                if sent:
                    conn.execute(
                        """INSERT INTO app_push_delivery_log (user_id, event_key, month_key)
                           VALUES (?, ?, ?)""", (int(row["user_id"]), event_key, month_key),
                    )
                    delivered += sent
        conn.commit()
    return delivered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sende fällige Rov.E-Monatscheck-Pushes.")
    parser.add_argument("--date", help="Testdatum im Format YYYY-MM-DD")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    chosen = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    print(f"Monatscheck-Pushes gesendet: {send_due_monthly_reminders(chosen)}")
