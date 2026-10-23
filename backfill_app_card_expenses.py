"""Zieht ältere Rov.E-App-Ausgaben einmalig und idempotent vom Girokonto ab.

Standardmäßig läuft das Skript nur als Vorschau. Erst --apply verändert die Datenbank.
Vor der Änderung wird automatisch ein konsistentes SQLite-Backup erstellt.
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from rove_app_state import (
    ACCOUNT_META,
    ensure_app_account_balances_table,
    ensure_app_cash_movements_table,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="clarity.db")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--since", default="2026-07-21 00:00:00")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def database_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"clarity_before_card_backfill_{stamp}.db"
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def load_balances(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    rows = conn.execute(
        "SELECT account_key, amount FROM app_account_balances WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    balances = {key: 0.0 for key in ACCOUNT_META}
    if rows:
        for row in rows:
            if row["account_key"] in balances:
                balances[row["account_key"]] = round(
                    max(0.0, float(row["amount"] or 0)), 2
                )
        return balances

    user = conn.execute(
        "SELECT current_cash FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not user:
        raise SystemExit(f"User {user_id} wurde nicht gefunden.")
    balances["giro"] = round(max(0.0, float(user["current_cash"] or 0)), 2)
    return balances


def save_balances(
    conn: sqlite3.Connection, user_id: int, balances: dict[str, float]
) -> None:
    for key in ACCOUNT_META:
        conn.execute(
            """INSERT INTO app_account_balances
               (user_id, account_key, amount, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, account_key)
               DO UPDATE SET amount = excluded.amount,
                             updated_at = CURRENT_TIMESTAMP""",
            (user_id, key, round(max(0.0, balances[key]), 2)),
        )
    conn.execute(
        "UPDATE users SET current_cash = ? WHERE user_id = ?",
        (round(sum(balances.values()), 2), user_id),
    )


def main():
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"Datenbank nicht gefunden: {db_path}")

    backup_path = database_backup(db_path) if args.apply else None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_app_account_balances_table(conn)
        ensure_app_cash_movements_table(conn)
        rows = conn.execute(
            """SELECT e.id, e.amount, e.merchant, e.created_at
                 FROM expenses e
                WHERE e.user_id = ?
                  AND datetime(e.created_at) >= datetime(?)
                  AND e.description LIKE 'Via Rov.E App%'
                  AND NOT EXISTS (
                        SELECT 1 FROM app_cash_movements m
                         WHERE m.user_id = e.user_id
                           AND m.expense_id = e.id
                  )
                ORDER BY datetime(e.created_at), e.id""",
            (args.user_id, args.since),
        ).fetchall()

        balances = load_balances(conn, args.user_id)
        giro_before = balances["giro"]
        planned = []
        for row in rows:
            amount = round(max(0.0, float(row["amount"] or 0)), 2)
            applied = round(min(amount, balances["giro"]), 2)
            balances["giro"] = round(balances["giro"] - applied, 2)
            planned.append((row, amount, applied))
            if args.apply:
                conn.execute(
                    """INSERT INTO app_cash_movements
                       (user_id, kind, amount, expense_id)
                       VALUES (?, 'card', ?, ?)""",
                    (args.user_id, applied, row["id"]),
                )

        total = round(sum(applied for _, _, applied in planned), 2)
        print(f"User: {args.user_id}")
        print(f"Seit: {args.since}")
        print(f"Giro vorher: {giro_before:.2f} EUR")
        print(f"Gefundene App-Ausgaben: {len(planned)}")
        for row, amount, applied in planned:
            print(
                f"- {row['created_at']} | {row['merchant']} | "
                f"{amount:.2f} EUR | Giro-Abzug {applied:.2f} EUR"
            )
        print(f"Gesamtabzug: {total:.2f} EUR")
        print(f"Giro danach: {balances['giro']:.2f} EUR")

        if args.apply:
            save_balances(conn, args.user_id, balances)
            conn.commit()
            print(f"Backup: {backup_path}")
            print("Nachbuchung gespeichert.")
        else:
            conn.rollback()
            print("Nur Vorschau. Es wurde nichts verändert.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
