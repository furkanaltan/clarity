#!/usr/bin/env python3
"""Apply the additive Open-Banking Phase-1 schema to one Rov.E SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from rove_provider_data import ensure_provider_schema


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Rov.E Open-Banking Phase-1 schema")
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit("database_not_found")
    with sqlite3.connect(args.db, timeout=30.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            ensure_provider_schema(conn)
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("provider_schema_foreign_key_check_failed")
        except Exception:
            conn.rollback()
            raise
    print("open_banking_phase1_schema=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
