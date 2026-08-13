#!/usr/bin/env python3
"""Laedt genau eine E-Mail-Adresse zur geschlossenen Rov.E-App-Beta ein."""

import argparse
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--db", default="clarity.db")
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    email = args.email.strip().casefold()
    if not EMAIL_RE.fullmatch(email):
        raise SystemExit("Ungueltige E-Mail-Adresse.")
    if not 1 <= args.days <= 90:
        raise SystemExit("Gueltigkeit muss zwischen 1 und 90 Tagen liegen.")

    db_path = Path(args.db).expanduser().resolve()
    expires_at = (datetime.now() + timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_invitations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL UNIQUE,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at  TEXT NOT NULL,
                consumed_at TEXT
            )"""
        )
        existing = conn.execute("SELECT 1 FROM app_accounts WHERE email = ?", (email,)).fetchone()
        if existing:
            raise SystemExit("Diese E-Mail-Adresse besitzt bereits ein Rov.E-Konto.")
        conn.execute(
            """INSERT INTO app_invitations (email, expires_at, consumed_at)
               VALUES (?, ?, NULL)
               ON CONFLICT(email) DO UPDATE SET
                   created_at = CURRENT_TIMESTAMP,
                   expires_at = excluded.expires_at,
                   consumed_at = NULL""",
            (email, expires_at),
        )
        conn.commit()

    print(f"Eingeladen: {email}")
    print(f"Gueltig bis: {expires_at}")


if __name__ == "__main__":
    main()
