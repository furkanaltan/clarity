#!/usr/bin/env python3
"""Reapply externally recorded account deletions after a SQLite restore."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import rove_account_delete_cleanup as cleanup
from rove_app_api import delete_user_rows_for_tombstone


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E Account-Loesch-Tombstones erneut anwenden")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--ledger", type=Path, default=None)
    args = parser.parse_args()
    user_ids = cleanup.read_delete_tombstones(args.ledger)
    with sqlite3.connect(args.db, timeout=30.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        for user_id in sorted(user_ids):
            delete_user_rows_for_tombstone(conn, user_id)
    print(f"reapplied={len(user_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
