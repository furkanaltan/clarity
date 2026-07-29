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
from rove_app_state import ensure_app_monthly_plan_table


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


def missing_plan_parts(row) -> list[str]:
    missing = []
    if row["income_status"] != "confirmed":
        missing.append("Einkommen")
    if row["fixed_costs_status"] != "confirmed":
        missing.append("Fixkosten")
    if row["savings_status"] != "confirmed":
        missing.append("Sparrate")
    return missing


def send_due_monthly_reminders(today: date | None = None) -> int:
    today = today or date.today()
    month_key = today.strftime("%Y-%m")

    with db() as conn:
        ensure_app_monthly_plan_table(conn)
        ensure_delivery_log(conn)
        rows = conn.execute(
            """SELECT u.user_id,
                      COALESCE(s.income_status, 'planned') AS income_status,
                      COALESCE(s.fixed_costs_status, 'planned') AS fixed_costs_status,
                      COALESCE(s.savings_status, 'planned') AS savings_status
                 FROM users u
                 LEFT JOIN app_monthly_plan_status s
                   ON s.user_id = u.user_id AND s.month_key = ?
                 LEFT JOIN app_push_delivery_log d
                   ON d.user_id = u.user_id
                  AND d.event_key = 'monthly_check'
                  AND d.month_key = ?
                WHERE u.onboarding_step >= 10
                  AND u.payday = ?
                  AND d.user_id IS NULL""",
            (month_key, month_key, today.day),
        ).fetchall()

        delivered = 0
        for row in rows:
            missing = missing_plan_parts(row)
            if not missing:
                continue
            sent = send_push_to_user(
                conn,
                int(row["user_id"]),
                "Dein Monatscheck ist bereit",
                "Heute ist dein Zahltag. Bitte pruefe noch: " + ", ".join(missing) + ".",
                tag=f"rove-monthly-check-{month_key}",
                url="./",
            )
            # Nur nach einer echten Zustellung sperren. Aktiviert jemand Push erst spaeter am
            # Zahltag, darf der naechste Timer-Lauf die Erinnerung noch senden.
            if sent:
                conn.execute(
                    """INSERT INTO app_push_delivery_log (user_id, event_key, month_key)
                       VALUES (?, 'monthly_check', ?)""",
                    (int(row["user_id"]), month_key),
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
