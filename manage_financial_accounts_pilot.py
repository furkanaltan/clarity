"""Safely inspect, enable, or disable the Sprint-2 pilot for one Rov.E user."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from migrate_financial_accounts import CENT_TOLERANCE, capture_user_snapshot, create_backup
from rove_financial_accounts import (
    ACCOUNT_ROLES,
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    ensure_financial_account_reference_schema,
    financial_accounts_total,
    is_feature_enabled,
    roles_summary,
    set_feature_enabled,
    table_exists,
)


def inspect_pilot(conn: sqlite3.Connection, user_id: int) -> dict:
    snapshot = capture_user_snapshot(conn, user_id)
    financial_total = financial_accounts_total(conn, user_id)
    roles = roles_summary(conn, user_id)
    invalid_roles = int(conn.execute(
        """SELECT COUNT(*) FROM app_financial_account_roles r
             LEFT JOIN app_financial_accounts a
               ON a.id = r.account_id AND a.user_id = r.user_id
            WHERE r.user_id = ? AND a.id IS NULL""",
        (user_id,),
    ).fetchone()[0] or 0)
    duplicate_legacy = int(conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT legacy_key FROM app_financial_accounts
                WHERE user_id = ? AND legacy_key IS NOT NULL
                GROUP BY legacy_key HAVING COUNT(*) > 1
           )""",
        (user_id,),
    ).fetchone()[0] or 0)
    checks = {
        "financial_matches_current_cash": abs(financial_total - snapshot.current_cash) <= CENT_TOLERANCE,
        "legacy_matches_current_cash": abs(snapshot.legacy_sum - snapshot.current_cash) <= CENT_TOLERANCE,
        "exactly_four_roles": set(roles) == set(ACCOUNT_ROLES) and len(roles) == 4,
        "role_accounts_belong_to_user": invalid_roles == 0,
        "no_duplicate_legacy_accounts": duplicate_legacy == 0,
    }
    return {
        "user_id": user_id,
        "feature_enabled": is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1),
        "snapshot": asdict(snapshot),
        "financial_total": financial_total,
        "roles": roles,
        "checks": checks,
        "ready": all(checks.values()),
    }


def write_private_snapshot(db_path: Path, report: dict) -> Path:
    directory = db_path.parent / "backups" / "financial_accounts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pilot_snapshot_{report['user_id']}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return path


def run(db_path: Path, user_id: int, action: str) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    mutating = action in {"enable", "disable"}
    backup = create_backup(db_path) if mutating else None
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE" if mutating else "BEGIN")
        if not table_exists(conn, "app_financial_accounts"):
            raise RuntimeError("financial_accounts_sprint1_missing")
        before = inspect_pilot(conn, user_id)
        if not before["ready"]:
            raise RuntimeError("pilot_invariants_failed")

        enabled_users = [int(row[0]) for row in conn.execute(
            """SELECT user_id FROM app_user_features
                 WHERE feature_key = ? AND enabled = 1 ORDER BY user_id""",
            (FEATURE_MULTI_CASH_ACCOUNTS_V1,),
        )]
        if action == "enable" and any(existing != user_id for existing in enabled_users):
            raise RuntimeError("another_pilot_is_already_enabled")

        snapshot_file = ""
        if mutating:
            ensure_financial_account_reference_schema(conn)
            snapshot_file = str(write_private_snapshot(db_path, before))
            set_feature_enabled(
                conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1, action == "enable"
            )
            conn.commit()
            conn.execute("BEGIN")

        after = inspect_pilot(conn, user_id)
        active_after = int(conn.execute(
            """SELECT COUNT(*) FROM app_user_features
                 WHERE feature_key = ? AND enabled = 1""",
            (FEATURE_MULTI_CASH_ACCOUNTS_V1,),
        ).fetchone()[0] or 0)
        conn.rollback()

    unchanged = {
        key: before["snapshot"][key] == after["snapshot"][key]
        for key in (
            "current_cash", "legacy_sum", "current_investments", "property_equity",
            "net_worth", "expenses", "cash_movements",
        )
    }
    if not all(unchanged.values()) or not after["ready"]:
        raise RuntimeError("pilot_activation_changed_financial_state")
    if action == "enable" and (not after["feature_enabled"] or active_after != 1):
        raise RuntimeError("pilot_enable_failed")
    if action == "disable" and after["feature_enabled"]:
        raise RuntimeError("pilot_disable_failed")
    return {
        "action": action,
        "database": str(db_path),
        "user_id": user_id,
        "backup": str(backup) if backup else "",
        "snapshot_file": snapshot_file,
        "active_pilots_after": active_after,
        "feature_enabled_after": after["feature_enabled"],
        "checks": after["checks"],
        "financial_state_unchanged": unchanged,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E Sprint-2 pilot control")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    parser.add_argument("--user-id", type=int, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--enable", action="store_true")
    modes.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    action = "enable" if args.enable else "disable" if args.disable else "status"
    result = run(args.db.resolve(), args.user_id, action)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
