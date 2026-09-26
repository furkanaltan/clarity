#!/usr/bin/env python3
"""Create a verified SQLite backup of the Rov.E production database.

The SQLite backup API creates a consistent snapshot while Bot and App API keep
running. Only backups created by this script are subject to retention cleanup.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rov.E SQLite-Backup erstellen")
    parser.add_argument(
        "--db",
        default=os.getenv("CLARITY_DB_NAME", "clarity.db"),
        help="SQLite-Datei; relativ zum Projektordner oder absolut",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(APP_DIR / "backups" / "automatic"),
        help="Zielordner fuer automatische Backups",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=30,
        help="Automatische Backups aelter als diese Anzahl Tage entfernen",
    )
    return parser.parse_args()


def resolve_db_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else APP_DIR / path


def verify_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise RuntimeError(f"SQLite-Integritaetscheck fehlgeschlagen: {row[0] if row else 'keine Antwort'}")


def cleanup_old_backups(backup_dir: Path, keep_days: int) -> int:
    if keep_days < 1:
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for path in backup_dir.glob("clarity_auto_*.db"):
        modified_at = datetime.fromtimestamp(path.stat().st_mtime)
        if modified_at < cutoff:
            path.unlink()
            removed += 1
    return removed


def main() -> int:
    args = parse_args()
    if args.keep_days < 1:
        raise SystemExit("--keep-days muss mindestens 1 sein")

    db_path = resolve_db_path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"Datenbank nicht gefunden: {db_path}")

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"clarity_auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

    try:
        # SQLite erstellt dabei einen konsistenten Snapshot, auch bei laufenden Writes.
        with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        verify_database(backup_path)
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise

    removed = cleanup_old_backups(backup_dir, args.keep_days)
    print(f"Backup OK: {backup_path}")
    print(f"Aufbewahrung: {args.keep_days} Tage | Alte Backups entfernt: {removed}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Backup fehlgeschlagen: {exc}", file=sys.stderr)
        raise SystemExit(1)
