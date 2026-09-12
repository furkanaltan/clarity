"""Add the immutable Report Snapshot V2 table without touching old snapshots."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import report_engine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.dry_run == args.apply:
        parser.error("genau eines von --dry-run oder --apply angeben")

    db_path = args.db.resolve()
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_snapshots_v2'"
        ).fetchone() is not None
    backup = ""
    if args.apply:
        backup_dir = db_path.parent / "backups" / "report_snapshots_v2"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"clarity_before_report_snapshots_v2_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(db_path, backup_path)
        backup = str(backup_path)
        with sqlite3.connect(db_path) as conn:
            report_engine.ensure_report_snapshots_v2_table(conn)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
            if integrity != "ok" or foreign_keys:
                raise SystemExit(f"SQLite-Pruefung fehlgeschlagen: {integrity}, FK={len(foreign_keys)}")

    print({
        "mode": "apply" if args.apply else "dry-run",
        "database": str(db_path),
        "table_before": before,
        "table_after": True if args.apply else before,
        "backup": backup,
        "schema_version": report_engine.REPORT_SNAPSHOT_SCHEMA_VERSION,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
