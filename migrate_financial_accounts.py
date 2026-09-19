"""Dry-run-first migration for Rov.E Sprint 1 dynamic Cash accounts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rove_financial_accounts import (
    ACCOUNT_ROLES,
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    LEGACY_ACCOUNT_META,
    ensure_financial_accounts_schema,
    financial_accounts_total,
    get_legacy_financial_account,
    is_feature_enabled,
    roles_summary,
    table_exists,
)


CENT_TOLERANCE = 0.0049


@dataclass(frozen=True)
class UserSnapshot:
    user_id: int
    access_status: str
    current_cash: float
    giro: float | None
    tagesgeld: float | None
    bargeld: float | None
    legacy_sum: float
    has_legacy_rows: bool
    expenses: int
    cash_movements: int
    current_investments: float
    property_equity: float
    net_worth: float


def money(value: object) -> float:
    return round(float(value or 0.0), 2)


def count_user_rows(conn: sqlite3.Connection, table: str, user_id: int) -> int:
    if not table_exists(conn, table):
        return 0
    row = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE user_id = ?', (user_id,)).fetchone()
    return int(row[0] or 0)


def capture_user_snapshot(conn: sqlite3.Connection, user_id: int) -> UserSnapshot:
    user = conn.execute(
        "SELECT current_cash, current_investments FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not user:
        raise LookupError("user_not_found")

    legacy = {}
    if table_exists(conn, "app_account_balances"):
        rows = conn.execute(
            """SELECT account_key, amount FROM app_account_balances
                 WHERE user_id = ?""",
            (user_id,),
        ).fetchall()
        unknown_keys = sorted({str(row["account_key"]) for row in rows} - set(LEGACY_ACCOUNT_META))
        if unknown_keys:
            raise ValueError(f"unknown_legacy_account_keys:{','.join(unknown_keys)}")
        legacy = {str(row["account_key"]): money(row["amount"]) for row in rows}
    has_legacy_rows = bool(legacy)
    legacy_sum = money(sum(legacy.values())) if has_legacy_rows else money(user["current_cash"])

    property_equity = 0.0
    if table_exists(conn, "app_properties"):
        prop = conn.execute(
            """SELECT market_value, remaining_debt FROM app_properties
                 WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        if prop and float(prop["market_value"] or 0.0) > 0:
            property_equity = money(float(prop["market_value"] or 0.0) - float(prop["remaining_debt"] or 0.0))

    access_status = ""
    if table_exists(conn, "user_access"):
        access = conn.execute(
            "SELECT status FROM user_access WHERE user_id = ?", (user_id,)
        ).fetchone()
        access_status = str(access["status"] or "") if access else ""

    current_cash = money(user["current_cash"])
    investments = money(user["current_investments"])
    return UserSnapshot(
        user_id=user_id,
        access_status=access_status,
        current_cash=current_cash,
        giro=legacy.get("giro"),
        tagesgeld=legacy.get("tagesgeld"),
        bargeld=legacy.get("bargeld"),
        legacy_sum=legacy_sum,
        has_legacy_rows=has_legacy_rows,
        expenses=count_user_rows(conn, "expenses", user_id),
        cash_movements=count_user_rows(conn, "app_cash_movements", user_id),
        current_investments=investments,
        property_equity=property_equity,
        net_worth=money(current_cash + investments + property_equity),
    )


def invariant_error(snapshot: UserSnapshot) -> str | None:
    if snapshot.has_legacy_rows and abs(snapshot.current_cash - snapshot.legacy_sum) > CENT_TOLERANCE:
        return "current_cash_differs_from_legacy_sum"
    return None


def planned_legacy_accounts(snapshot: UserSnapshot) -> dict[str, float]:
    if not snapshot.has_legacy_rows:
        return {"giro": snapshot.current_cash}
    accounts = {
        key: value
        for key, value in (
            ("giro", snapshot.giro),
            ("tagesgeld", snapshot.tagesgeld),
            ("bargeld", snapshot.bargeld),
        )
        if value is not None
    }
    # Roles need a deterministic payment account. A missing Giro becomes a zero-balance
    # compatibility account; no money is moved and no savings account is guessed.
    accounts.setdefault("giro", 0.0)
    return accounts


def migrate_user(conn: sqlite3.Connection, user_id: int) -> dict:
    before = capture_user_snapshot(conn, user_id)
    blocked = invariant_error(before)
    if blocked:
        raise ValueError(blocked)

    specs = planned_legacy_accounts(before)
    for legacy_key, balance in specs.items():
        account_type, name = LEGACY_ACCOUNT_META[legacy_key]
        conn.execute(
            """INSERT OR IGNORE INTO app_financial_accounts
                   (user_id, account_type, name, currency, balance, legacy_key, source, status)
               VALUES (?, ?, ?, 'EUR', ?, ?, 'legacy', 'active')""",
            (user_id, account_type, name, balance, legacy_key),
        )
        account = get_legacy_financial_account(conn, user_id, legacy_key)
        if not account:
            raise RuntimeError(f"legacy_account_missing_after_insert:{legacy_key}")
        if str(account["account_type"]) != account_type:
            raise ValueError(f"legacy_account_type_mismatch:{legacy_key}")
        if abs(money(account["balance"]) - money(balance)) > CENT_TOLERANCE:
            raise ValueError(f"legacy_account_balance_mismatch:{legacy_key}")

    giro = get_legacy_financial_account(conn, user_id, "giro")
    if not giro:
        raise RuntimeError("legacy_giro_missing")
    for role in ACCOUNT_ROLES:
        conn.execute(
            """INSERT OR IGNORE INTO app_financial_account_roles
                   (user_id, role, account_id, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (user_id, role, int(giro["id"])),
        )

    for role, account_id in roles_summary(conn, user_id).items():
        account = conn.execute(
            """SELECT 1 FROM app_financial_accounts
                 WHERE id = ? AND user_id = ? AND status = 'active'""",
            (account_id, user_id),
        ).fetchone()
        if not account:
            raise ValueError(f"invalid_role_account:{role}")

    new_total = financial_accounts_total(conn, user_id)
    if abs(new_total - before.current_cash) > CENT_TOLERANCE:
        raise ValueError("financial_account_sum_differs_from_current_cash")

    after = capture_user_snapshot(conn, user_id)
    unchanged = {
        "current_cash": before.current_cash == after.current_cash,
        "legacy_sum": before.legacy_sum == after.legacy_sum,
        "current_investments": before.current_investments == after.current_investments,
        "property_equity": before.property_equity == after.property_equity,
        "expenses": before.expenses == after.expenses,
        "cash_movements": before.cash_movements == after.cash_movements,
        "net_worth": before.net_worth == after.net_worth,
    }
    if not all(unchanged.values()):
        raise RuntimeError("legacy_invariant_changed")

    return {
        "user_id": user_id,
        "status": "success",
        "access_status": before.access_status,
        "account_count": count_user_rows(conn, "app_financial_accounts", user_id),
        "new_sum": new_total,
        "current_cash": before.current_cash,
        "legacy_sum": before.legacy_sum,
        "roles": sorted(roles_summary(conn, user_id)),
        "feature_enabled": is_feature_enabled(
            conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1
        ),
        "unchanged": unchanged,
    }


def dry_run_user(conn: sqlite3.Connection, user_id: int) -> dict:
    snapshot = capture_user_snapshot(conn, user_id)
    blocked = invariant_error(snapshot)
    planned = planned_legacy_accounts(snapshot)
    existing_count = count_user_rows(conn, "app_financial_accounts", user_id)
    existing_sum = None
    if existing_count:
        existing_sum = financial_accounts_total(conn, user_id)
        if abs(existing_sum - snapshot.current_cash) > CENT_TOLERANCE:
            blocked = "financial_account_sum_differs_from_current_cash"
        if table_exists(conn, "app_financial_account_roles"):
            invalid_roles = conn.execute(
                """SELECT COUNT(*)
                     FROM app_financial_account_roles r
                     LEFT JOIN app_financial_accounts a
                       ON a.id = r.account_id AND a.user_id = r.user_id
                    WHERE r.user_id = ? AND a.id IS NULL""",
                (user_id,),
            ).fetchone()[0]
            if int(invalid_roles or 0):
                blocked = "financial_account_role_crosses_user_boundary"
    return {
        "user_id": user_id,
        "status": "blocked" if blocked else "ready",
        "reason": blocked or "",
        "access_status": snapshot.access_status,
        "current_cash": snapshot.current_cash,
        "legacy_sum": snapshot.legacy_sum,
        "legacy_accounts": len([value for value in (snapshot.giro, snapshot.tagesgeld, snapshot.bargeld) if value is not None]),
        "planned_accounts": len(planned),
        "existing_new_accounts": existing_count,
        "existing_new_sum": existing_sum,
        "expenses": snapshot.expenses,
        "cash_movements": snapshot.cash_movements,
        "current_investments": snapshot.current_investments,
        "property_equity": snapshot.property_equity,
        "net_worth": snapshot.net_worth,
        "feature_default": "off",
    }


def create_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups" / "financial_accounts"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{db_path.stem}_before_financial_accounts_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    )
    with closing(sqlite3.connect(db_path)) as source:
        with closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
    backup_path.chmod(0o600)
    return backup_path


def selected_user_ids(conn: sqlite3.Connection, user_id: int | None) -> list[int]:
    if user_id is not None:
        return [user_id]
    return [int(row[0]) for row in conn.execute("SELECT user_id FROM users ORDER BY user_id")]


def run(db_path: Path, *, apply: bool, user_id: int | None = None) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    backup_path = create_backup(db_path) if apply else None
    result = {
        "mode": "apply" if apply else "dry-run",
        "database": str(db_path),
        "backup": str(backup_path) if backup_path else "",
        "feature_key": FEATURE_MULTI_CASH_ACCOUNTS_V1,
        "feature_default": "off",
        "users": [],
    }
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        users = selected_user_ids(conn, user_id)
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            ensure_financial_accounts_schema(conn)
            conn.commit()
        for current_user_id in users:
            if not apply:
                try:
                    result["users"].append(dry_run_user(conn, current_user_id))
                except Exception as exc:
                    result["users"].append({
                        "user_id": current_user_id,
                        "status": "blocked",
                        "reason": str(exc),
                    })
                continue
            try:
                conn.execute("BEGIN IMMEDIATE")
                migrated = migrate_user(conn, current_user_id)
                conn.commit()
                result["users"].append(migrated)
            except Exception as exc:
                conn.rollback()
                result["users"].append({
                    "user_id": current_user_id,
                    "status": "blocked",
                    "reason": str(exc),
                })
    result["summary"] = {
        "total": len(result["users"]),
        "ready_or_success": sum(row.get("status") in {"ready", "success"} for row in result["users"]),
        "blocked": sum(row.get("status") == "blocked" for row in result["users"]),
    }
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rov.E financial accounts Sprint-1 migration")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect only; this is the default")
    mode.add_argument("--apply", action="store_true", help="Backup, create schema and migrate per user")
    parser.add_argument("--user-id", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = run(args.db.resolve(), apply=bool(args.apply), user_id=args.user_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["summary"]["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
