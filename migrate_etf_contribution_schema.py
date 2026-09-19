"""Explicit, backup-first migration for ETF contribution holding references."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from rove_investment_contributions import ensure_investment_contribution_schema


def has_holding_id(conn: sqlite3.Connection) -> bool:
    return "holding_id" in {
        str(row[1]) for row in conn.execute("PRAGMA table_info(investment_events)")
    }


def create_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups" / "etf_contributions"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{db_path.stem}_before_etf_contribution_schema_"
        f"{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    )
    with closing(sqlite3.connect(db_path)) as source:
        with closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
    backup_path.chmod(0o600)
    return backup_path


def run(db_path: Path, *, apply: bool) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    backup = None
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        before = has_holding_id(conn)
    if apply and not before:
        backup = create_backup(db_path)
        with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_investment_contribution_schema(conn)
            conn.commit()
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        after = has_holding_id(conn)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    return {
        "mode": "apply" if apply else "dry-run",
        "database": str(db_path),
        "backup": str(backup) if backup else "",
        "holding_id_before": before,
        "holding_id_after": after,
        "changed": bool(apply and not before and after),
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E ETF contribution schema migration")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args.db.resolve(), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    failed = result["integrity_check"] != "ok" or result["foreign_key_errors"]
    return 1 if failed or (args.apply and not result["holding_id_after"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
