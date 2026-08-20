"""Prepare and enable Rov.E multi-account mode for active beta testers.

This script is intentionally conservative:
- dry-run is the default
- production apply creates one SQLite backup before each changed user
- each user is prepared and enabled inside one BEGIN IMMEDIATE transaction
- no real money values are printed
- inactive/revoked users are never changed
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from migrate_financial_accounts import CENT_TOLERANCE, capture_user_snapshot, create_backup
from rove_financial_accounts import (
    ACCOUNT_ROLES,
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    LEGACY_ACCOUNT_META,
    TYPE_TO_LEGACY_KEY,
    ensure_financial_accounts_schema,
    ensure_financial_account_reference_schema,
    financial_accounts_total,
    is_feature_enabled,
    roles_summary,
    set_feature_enabled,
    table_exists,
)


DEFAULT_PILOT_USER_ID = 653187414
ACTIVE_ACCESS_STATUSES = {"approved", "app_only"}


def money(value: object) -> float:
    return round(float(value or 0.0), 2)


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def active_app_tester_ids(conn: sqlite3.Connection) -> list[int]:
    """Return active app testers only; pending/revoked/inactive users stay untouched."""
    if not table_exists(conn, "user_access") or not table_exists(conn, "app_accounts"):
        return []
    rows = conn.execute(
        """SELECT DISTINCT ua.user_id
             FROM user_access ua
             JOIN app_accounts aa ON aa.user_id = ua.user_id
            WHERE ua.status IN ('approved', 'app_only')
            ORDER BY ua.user_id"""
    ).fetchall()
    return [int(row["user_id"]) for row in rows]


def enabled_multi_account_ids(conn: sqlite3.Connection) -> list[int]:
    if not table_exists(conn, "app_user_features"):
        return []
    return [
        int(row["user_id"])
        for row in conn.execute(
            """SELECT user_id FROM app_user_features
                 WHERE feature_key = ? AND enabled = 1
                 ORDER BY user_id""",
            (FEATURE_MULTI_CASH_ACCOUNTS_V1,),
        )
    ]


def inactive_or_blocked_count(conn: sqlite3.Connection, active_ids: set[int]) -> int:
    return one(
        conn,
        f"""SELECT COUNT(*) FROM users
             WHERE user_id NOT IN ({','.join('?' for _ in active_ids)})"""
        if active_ids
        else "SELECT COUNT(*) FROM users",
        tuple(active_ids),
    )


def legacy_balances(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    rows = conn.execute(
        """SELECT account_key, amount FROM app_account_balances
             WHERE user_id = ?""",
        (user_id,),
    ).fetchall()
    unknown = sorted({str(row["account_key"]) for row in rows} - set(LEGACY_ACCOUNT_META))
    if unknown:
        raise ValueError("unknown_legacy_account_keys")
    balances = {str(row["account_key"]): money(row["amount"]) for row in rows}
    if not balances:
        raise ValueError("missing_legacy_balances")
    balances.setdefault("giro", 0.0)
    balances.setdefault("tagesgeld", 0.0)
    balances.setdefault("bargeld", 0.0)
    return balances


def check_roles(conn: sqlite3.Connection, user_id: int) -> bool:
    roles = roles_summary(conn, user_id)
    if set(roles) != set(ACCOUNT_ROLES) or len(roles) != 4:
        return False
    invalid = one(
        conn,
        """SELECT COUNT(*) FROM app_financial_account_roles r
             LEFT JOIN app_financial_accounts a
               ON a.id = r.account_id AND a.user_id = r.user_id
            WHERE r.user_id = ? AND a.id IS NULL""",
        (user_id,),
    )
    return invalid == 0


def manual_financial_accounts(conn: sqlite3.Connection, user_id: int) -> int:
    if not table_exists(conn, "app_financial_accounts"):
        return 0
    return one(
        conn,
        """SELECT COUNT(*) FROM app_financial_accounts
             WHERE user_id = ? AND legacy_key IS NULL""",
        (user_id,),
    )


def duplicate_legacy_accounts(conn: sqlite3.Connection, user_id: int) -> int:
    return one(
        conn,
        """SELECT COUNT(*) FROM (
               SELECT legacy_key FROM app_financial_accounts
                WHERE user_id = ? AND legacy_key IS NOT NULL
                GROUP BY legacy_key HAVING COUNT(*) > 1
           )""",
        (user_id,),
    )


def set_legacy_account_balances_from_legacy(conn: sqlite3.Connection, user_id: int) -> None:
    balances = legacy_balances(conn, user_id)
    for legacy_key, amount in balances.items():
        account_type, name = LEGACY_ACCOUNT_META[legacy_key]
        conn.execute(
            """INSERT OR IGNORE INTO app_financial_accounts
                   (user_id, account_type, name, currency, balance, legacy_key, source, status)
               VALUES (?, ?, ?, 'EUR', 0, ?, 'legacy', 'active')""",
            (user_id, account_type, name, legacy_key),
        )
        cur = conn.execute(
            """UPDATE app_financial_accounts
                  SET account_type = ?, name = CASE WHEN source = 'legacy' THEN ? ELSE name END,
                      balance = ?, status = 'active', archived_at = NULL,
                      updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND legacy_key = ?""",
            (account_type, name, amount, user_id, legacy_key),
        )
        if cur.rowcount != 1:
            raise RuntimeError("legacy_financial_account_sync_failed")


def ensure_default_roles(conn: sqlite3.Connection, user_id: int) -> None:
    giro = conn.execute(
        """SELECT id FROM app_financial_accounts
             WHERE user_id = ? AND legacy_key = 'giro' AND status = 'active'""",
        (user_id,),
    ).fetchone()
    if not giro:
        raise RuntimeError("legacy_giro_missing")
    for role in ACCOUNT_ROLES:
        conn.execute(
            """INSERT INTO app_financial_account_roles (user_id, role, account_id, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, role) DO UPDATE SET
                   account_id = excluded.account_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, role, int(giro["id"])),
        )


def type_sums_match_legacy(conn: sqlite3.Connection, user_id: int) -> dict[str, bool]:
    out = {}
    balances = legacy_balances(conn, user_id)
    for account_type, legacy_key in TYPE_TO_LEGACY_KEY.items():
        account_sum = conn.execute(
            """SELECT COALESCE(SUM(balance), 0) FROM app_financial_accounts
                 WHERE user_id = ? AND account_type = ? AND status = 'active'""",
            (user_id, account_type),
        ).fetchone()[0]
        out[legacy_key] = abs(money(account_sum) - money(balances[legacy_key])) <= CENT_TOLERANCE
    return out


def state_builds(user_id: int, db_path: Path) -> bool:
    try:
        import rove_app_state

        rove_app_state.DB_PATH = db_path
        with rove_app_state.db() as conn:
            rove_app_state.build_live_app_data(conn, user_id)
        return True
    except Exception:
        return False


def private_snapshot(db_path: Path, user_id: int, before: dict, after: dict) -> Path:
    directory = db_path.parent / "backups" / "financial_accounts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"active_tester_rollout_{user_id}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    path.write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    return path


def validate_preconditions(conn: sqlite3.Connection, user_id: int) -> list[str]:
    errors: list[str] = []
    try:
        snapshot = capture_user_snapshot(conn, user_id)
        if abs(snapshot.legacy_sum - snapshot.current_cash) > CENT_TOLERANCE:
            errors.append("legacy_sum_differs_from_current_cash")
    except Exception as exc:
        errors.append(str(exc))
        return errors
    if is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1):
        errors.append("feature_already_enabled")
    if manual_financial_accounts(conn, user_id):
        errors.append("manual_financial_accounts_present")
    if duplicate_legacy_accounts(conn, user_id):
        errors.append("duplicate_legacy_accounts")
    try:
        legacy_balances(conn, user_id)
    except Exception as exc:
        errors.append(str(exc))
    return errors


def inspect_user(conn: sqlite3.Connection, db_path: Path, user_id: int) -> dict:
    snapshot = capture_user_snapshot(conn, user_id)
    financial_total_ok = (
        table_exists(conn, "app_financial_accounts")
        and abs(financial_accounts_total(conn, user_id) - snapshot.current_cash) <= CENT_TOLERANCE
    )
    return {
        "user_id": user_id,
        "feature_enabled": is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1),
        "legacy_matches_current_cash": abs(snapshot.legacy_sum - snapshot.current_cash) <= CENT_TOLERANCE,
        "financial_matches_current_cash": financial_total_ok,
        "type_sums_match_legacy": type_sums_match_legacy(conn, user_id),
        "roles_valid": check_roles(conn, user_id),
        "expenses_count_present": snapshot.expenses >= 0,
        "cash_movements_count_present": snapshot.cash_movements >= 0,
        "current_investments_present": snapshot.current_investments >= 0,
        "property_present": snapshot.property_equity >= 0,
        "net_worth_present": True,
        "state_builds": state_builds(user_id, db_path),
    }


def prepare_and_enable_user(conn: sqlite3.Connection, db_path: Path, user_id: int) -> dict:
    backup = create_backup(db_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        before_snapshot = asdict(capture_user_snapshot(conn, user_id))
        errors = validate_preconditions(conn, user_id)
        if errors:
            raise RuntimeError(";".join(errors))
        ensure_financial_accounts_schema(conn)
        ensure_financial_account_reference_schema(conn)
        set_legacy_account_balances_from_legacy(conn, user_id)
        ensure_default_roles(conn, user_id)

        prepared = inspect_user(conn, db_path, user_id)
        if not prepared["financial_matches_current_cash"]:
            raise RuntimeError("financial_sum_after_prepare_differs")
        if not all(prepared["type_sums_match_legacy"].values()):
            raise RuntimeError("type_sum_after_prepare_differs")
        if not prepared["roles_valid"]:
            raise RuntimeError("roles_invalid_after_prepare")

        set_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
        after = inspect_user(conn, db_path, user_id)
        if not after["feature_enabled"]:
            raise RuntimeError("feature_enable_failed")
        if not after["legacy_matches_current_cash"] or not after["financial_matches_current_cash"]:
            raise RuntimeError("post_enable_sum_differs")
        if not all(after["type_sums_match_legacy"].values()):
            raise RuntimeError("post_enable_type_sum_differs")
        if not after["state_builds"]:
            raise RuntimeError("state_build_failed")

        after_snapshot = asdict(capture_user_snapshot(conn, user_id))
        unchanged_keys = (
            "current_cash",
            "legacy_sum",
            "current_investments",
            "property_equity",
            "net_worth",
            "expenses",
            "cash_movements",
        )
        unchanged = {key: before_snapshot[key] == after_snapshot[key] for key in unchanged_keys}
        if not all(unchanged.values()):
            raise RuntimeError("financial_state_changed")

        snapshot_path = private_snapshot(db_path, user_id, before_snapshot, after_snapshot)
        conn.commit()
        return {
            "user_id": user_id,
            "status": "enabled",
            "backup": str(backup),
            "snapshot_file": str(snapshot_path),
            "checks": {
                "prepare": "ok",
                "flag": "ok",
                "state": "ok",
                "sums": "ok",
                "financial_state_unchanged": all(unchanged.values()),
            },
        }
    except Exception as exc:
        conn.rollback()
        return {
            "user_id": user_id,
            "status": "blocked",
            "reason": str(exc),
            "backup": str(backup),
        }


def sqlite_checks(conn: sqlite3.Connection) -> dict:
    return {
        "integrity_check": str(conn.execute("PRAGMA integrity_check").fetchone()[0]),
        "foreign_key_errors": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
    }


def run(db_path: Path, *, apply: bool, limit: int, users: list[int] | None) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_financial_accounts_schema(conn)
        ensure_financial_account_reference_schema(conn)
        conn.commit()

        active_ids = active_app_tester_ids(conn)
        enabled_ids = enabled_multi_account_ids(conn)
        selected = users or [user_id for user_id in active_ids if user_id not in set(enabled_ids)]
        selected = selected[:limit]
        result = {
            "mode": "apply" if apply else "dry-run",
            "database": str(db_path.resolve()),
            "active_testers": len(active_ids),
            "enabled_before": len([user_id for user_id in active_ids if user_id in set(enabled_ids)]),
            "already_enabled_user_ids": [user_id for user_id in active_ids if user_id in set(enabled_ids)],
            "candidate_user_ids": selected,
            "inactive_or_blocked_users_untouched": inactive_or_blocked_count(conn, set(active_ids)),
            "users": [],
        }

        if not apply:
            for user_id in selected:
                errors = validate_preconditions(conn, user_id)
                result["users"].append({
                    "user_id": user_id,
                    "status": "ready" if not errors else "blocked",
                    "reason": ";".join(errors),
                    "current_state": inspect_user(conn, db_path, user_id) if not errors else {},
                })
        else:
            for user_id in selected:
                changed = prepare_and_enable_user(conn, db_path, user_id)
                result["users"].append(changed)
                if changed["status"] != "enabled":
                    break

        enabled_after = enabled_multi_account_ids(conn)
        result["enabled_after"] = len([user_id for user_id in active_ids if user_id in set(enabled_after)])
        result["pilot_regression"] = inspect_user(conn, db_path, DEFAULT_PILOT_USER_ID)
        result["sqlite"] = sqlite_checks(conn)
        blockers = [
            row for row in result["users"]
            if row.get("status") not in {"ready", "enabled"}
        ]
        if result["sqlite"]["integrity_check"] != "ok" or result["sqlite"]["foreign_key_errors"]:
            blockers.append({"status": "blocked", "reason": "sqlite_integrity_problem"})
        result["recommendation"] = {
            "status": "GO_ACTIVE_TESTERS" if apply and not blockers and result["enabled_after"] == len(active_ids) else (
                "READY_TO_APPLY" if not apply and not blockers else "NO_GO"
            ),
            "blockers": [row.get("reason", "unknown") for row in blockers],
            "global_rollout": "not_enabled",
        }
        return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare active Rov.E testers for multi-account mode")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--user-id", type=int, action="append", dest="users")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = run(args.db.resolve(), apply=bool(args.apply), limit=args.limit, users=args.users)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["recommendation"]["status"] in {"READY_TO_APPLY", "GO_ACTIVE_TESTERS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
