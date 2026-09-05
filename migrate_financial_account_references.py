"""Explicit Sprint-2 schema migration for nullable Financial Account references."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from migrate_financial_accounts import create_backup
from rove_financial_accounts import (
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    ensure_financial_account_reference_schema,
    table_columns,
    table_exists,
)


EXPECTED = {
    "expenses": {"account_id"},
    "app_cash_movements": {"source_account_id", "target_account_id"},
    "app_etf_savings_plan": {"source_account_id"},
    "app_etf_position_plans": {"source_account_id"},
}


def inspect(conn: sqlite3.Connection) -> dict:
    tables = {}
    for table, expected in EXPECTED.items():
        exists = table_exists(conn, table)
        columns = table_columns(conn, table)
        tables[table] = {
            "exists": exists,
            "present": sorted(expected & columns),
            "missing": sorted(expected - columns) if exists else sorted(expected),
        }
    enabled = 0
    if table_exists(conn, "app_user_features"):
        enabled = int(conn.execute(
            """SELECT COUNT(*) FROM app_user_features
                 WHERE feature_key = ? AND enabled = 1""",
            (FEATURE_MULTI_CASH_ACCOUNTS_V1,),
        ).fetchone()[0] or 0)
    return {"tables": tables, "enabled_flags": enabled}


def run(db_path: Path, *, apply: bool) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    backup = create_backup(db_path) if apply else None
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        before = inspect(conn)
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            ensure_financial_account_reference_schema(conn)
            conn.commit()
        after = inspect(conn)
    return {
        "mode": "apply" if apply else "dry-run",
        "database": str(db_path),
        "backup": str(backup) if backup else "",
        "before": before,
        "after": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E Financial Accounts Sprint-2 schema")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args.db.resolve(), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    missing_after = sum(
        len(row["missing"])
        for row in result["after"]["tables"].values()
        if row["exists"]
    )
    migration_incomplete = bool(args.apply and missing_after)
    return 1 if migration_incomplete or result["after"]["enabled_flags"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
