"""Read-only cash inventory and explicit, single-user transition plans.

The CLI only reads. Applying a plan is deliberately a separate Python operation
requiring an operator-approved checksum inside the caller's write transaction.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from rove_financial_accounts import (
    ACCOUNT_ROLES, FEATURE_MULTI_CASH_ACCOUNTS_V1, LEGACY_ACCOUNT_META,
    TYPE_TO_LEGACY_KEY, CashReconciliationRequired, assert_cash_consistent,
    cash_amount, ensure_financial_accounts_schema, is_feature_enabled,
    list_financial_accounts, mirror_financial_accounts_to_legacy, roles_summary,
    set_account_role, set_feature_enabled, table_columns, table_exists,
)

FINANCIAL_TABLES = (
    "app_account_balances", "app_financial_accounts", "app_financial_account_roles",
    "app_financial_account_requests", "app_user_features", "expenses",
    "app_cash_movements", "category_budgets", "user_category_rules", "app_goals",
    "app_contracts", "app_contract_cancellations", "app_contract_cancellation_events",
    "app_properties", "app_valuables", "app_valuable_requests", "portfolio_holdings",
    "investment_events", "portfolio_snapshots", "app_etf_savings_plan",
    "app_etf_position_plans", "app_investment_contributions",
    "app_monthly_plan_status", "app_month_closures", "monthly_snapshots",
    "monthly_financial_snapshots", "report_snapshots_v2", "report_jobs",
    "app_consumer_debts", "app_consumer_debt_events", "app_onboarding_drafts",
)
USER_FINANCIAL_COLUMNS = (
    "current_cash", "current_investments", "income", "other_income", "fixed_costs",
    "fixed_costs_details", "etf_savings", "cash_savings", "onboarding_step",
    "debt_status", "current_month", "payday",
)
MODEL_TABLES = set(FINANCIAL_TABLES[:5])


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), default=lambda item: item.hex() if isinstance(item, bytes) else str(item)).encode()).hexdigest()


def user_checksum(conn, user_id, *, protected_only=False):
    """Hash financial rows only; never read passwords, sessions or verification codes."""
    columns = [col for col in USER_FINANCIAL_COLUMNS if col in table_columns(conn, "users")]
    user = conn.execute("SELECT " + ",".join(columns) + " FROM users WHERE user_id=?", (user_id,)).fetchone()
    data = {"users": dict(zip(columns, user)) if user else None}
    for table in FINANCIAL_TABLES:
        if protected_only and table in MODEL_TABLES:
            continue
        data[table] = []
        if "user_id" in table_columns(conn, table):
            rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" WHERE user_id=?', (user_id,))]
            data[table] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
    return digest(data)


def inspect_user(conn, user_id):
    user = conn.execute("SELECT current_cash FROM users WHERE user_id=?", (user_id,)).fetchone()
    if user is None:
        raise LookupError("user_not_found")
    errors, invalid = [], []
    current = None
    try:
        current = None if user["current_cash"] is None else cash_amount(user["current_cash"])
    except CashReconciliationRequired:
        invalid.append("INVALID_CURRENT_CASH")
    legacy = {}
    if table_exists(conn, "app_account_balances"):
        for row in conn.execute("SELECT account_key,amount FROM app_account_balances WHERE user_id=?", (user_id,)):
            try:
                value = cash_amount(row["amount"])
                key = str(row["account_key"])
                if key not in LEGACY_ACCOUNT_META or (key != "giro" and value < 0):
                    raise CashReconciliationRequired()
                legacy[key] = value
            except CashReconciliationRequired:
                invalid.append("INVALID_LEGACY_BALANCE")
    if current is None:
        errors.append("CURRENT_CASH_UNKNOWN")
    elif legacy and abs(sum(legacy.values()) - current) >= 0.005:
        errors.append("LEGACY_CURRENT_MISMATCH")
    accounts = list_financial_accounts(conn, user_id, include_archived=True)
    totals = {key: 0.0 for key in LEGACY_ACCOUNT_META}
    active = {}
    for row in accounts:
        key = TYPE_TO_LEGACY_KEY.get(str(row["account_type"]))
        try:
            value = cash_amount(row["balance"])
            if not key or row["currency"] != "EUR" or row["status"] not in {"active", "archived"} or (key != "giro" and value < 0):
                raise CashReconciliationRequired()
            if row["status"] == "archived":
                if value:
                    invalid.append("ARCHIVED_NONZERO_BALANCE")
                continue
            active[int(row["id"])] = row
            totals[key] = round(totals[key] + value, 2)
        except CashReconciliationRequired:
            invalid.append("INVALID_FINANCIAL_BALANCE")
    canonical = is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1)
    roles = roles_summary(conn, user_id)
    for role, account_id in roles.items():
        if role not in ACCOUNT_ROLES or account_id not in active or active[account_id]["account_type"] != "checking":
            invalid.append("INVALID_PAYMENT_ROLE")
    if canonical and set(roles) != set(ACCOUNT_ROLES):
        errors.append("MISSING_PAYMENT_ROLES")
    if accounts:
        if current is None or abs(sum(totals.values()) - current) >= 0.005:
            errors.append("FINANCIAL_CURRENT_MISMATCH")
        if legacy and any(abs(totals[key] - legacy.get(key, 0)) >= 0.005 for key in totals):
            errors.append("FINANCIAL_LEGACY_MISMATCH")
        if not canonical and any(row["source"] != "legacy" or not row["legacy_key"] or row["status"] != "active" for row in accounts):
            errors.append("UNCONFIRMED_FINANCIAL_ACCOUNT_ORIGIN")
    for table, columns in (("expenses", ("account_id",)),
            ("app_cash_movements", ("source_account_id", "target_account_id")),
            ("app_etf_savings_plan", ("source_account_id",)),
            ("app_etf_position_plans", ("source_account_id",))):
        for col in columns:
            if col not in table_columns(conn, table):
                continue
            for row in conn.execute(f'SELECT "{col}" FROM "{table}" WHERE user_id=? AND "{col}" IS NOT NULL', (user_id,)):
                found = next((account for account in accounts if account["id"] == row[0]), None)
                if not found or (table.startswith("app_etf") and found["status"] != "active"):
                    invalid.append("INVALID_ACCOUNT_REFERENCE")
    status = "BLOCKED_INTEGRITY" if invalid else ("REVIEW_REQUIRED" if errors else (
        "CONSISTENT_FINANCIAL" if canonical else "CONSISTENT_LEGACY"))
    return {"user_id": user_id, "status": status, "codes": sorted(set(errors + invalid)),
        "current_cash": current, "legacy_balances": legacy, "financial_balances": totals,
        "financial_accounts": [dict(row) for row in accounts], "roles": roles,
        "before_checksum": user_checksum(conn, user_id)}


def prepare_user_plan(conn, user_id):
    inspected = inspect_user(conn, user_id)
    missing_roles_only = inspected["codes"] == ["MISSING_PAYMENT_ROLES"]
    giro = next((row for row in inspected["financial_accounts"]
        if row["legacy_key"] == "giro" and row["status"] == "active" and row["account_type"] == "checking"), None)
    if not inspected["status"].startswith("CONSISTENT_") and not (missing_roles_only and giro):
        raise ValueError("operator_balance_decision_required")
    return {"version": 1, "user_id": user_id,
        "source": "financial" if inspected["status"] == "CONSISTENT_FINANCIAL" or missing_roles_only else "legacy",
        "before_checksum": inspected["before_checksum"],
        "cash": inspected["current_cash"],
        "legacy_balances": inspected["legacy_balances"],
        "account_ids": [row["id"] for row in inspected["financial_accounts"]],
        "roles": inspected["roles"],
        "roles_to_add": {role: giro["id"] for role in ACCOUNT_ROLES if giro and role not in inspected["roles"]}}


def apply_user_plan(conn, plan, *, operator_approved_checksum):
    """Apply exactly one reviewed consistent-user plan, never a balance conflict."""
    if not conn.in_transaction:
        raise RuntimeError("financial_account_write_requires_transaction")
    conn.execute("SAVEPOINT reviewed_user_transition")
    try:
        result = _apply_user_plan(conn, plan, operator_approved_checksum=operator_approved_checksum)
        conn.execute("RELEASE reviewed_user_transition")
        return result
    except Exception:
        conn.execute("ROLLBACK TO reviewed_user_transition")
        conn.execute("RELEASE reviewed_user_transition")
        raise


def _apply_user_plan(conn, plan, *, operator_approved_checksum):
    if operator_approved_checksum != plan["before_checksum"]:
        raise ValueError("operator_approval_required")
    user_id, plan_hash = int(plan["user_id"]), digest(plan)
    if table_exists(conn, "app_financial_account_migrations"):
        receipt = conn.execute("SELECT * FROM app_financial_account_migrations WHERE user_id=?", (user_id,)).fetchone()
        if receipt:
            if receipt["plan_hash"] == plan_hash and receipt["after_checksum"] == user_checksum(conn, user_id):
                return {"user_id": user_id, "idempotent_replay": True, "after_checksum": receipt["after_checksum"]}
            raise ValueError("transition_already_completed_or_changed")
    if user_checksum(conn, user_id) != plan["before_checksum"]:
        raise ValueError("stale_transition_plan")
    if prepare_user_plan(conn, user_id) != plan:
        raise ValueError("transition_plan_mismatch")
    protected_before = user_checksum(conn, user_id, protected_only=True)
    ensure_financial_accounts_schema(conn)
    if plan["source"] == "legacy":
        from migrate_financial_accounts import migrate_user
        migrate_user(conn, user_id)
    else:
        for role, account_id in plan["roles_to_add"].items():
            set_account_role(conn, user_id, role, account_id)
    mirror_financial_accounts_to_legacy(conn, user_id)
    set_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1, True)
    assert_cash_consistent(conn, user_id)
    if user_checksum(conn, user_id, protected_only=True) != protected_before:
        raise RuntimeError("protected_financial_rows_changed")
    after = user_checksum(conn, user_id)
    conn.execute("""INSERT INTO app_financial_account_migrations
        (user_id,source,plan_hash,before_checksum,after_checksum) VALUES(?,'reconciled',?,?,?)""",
        (user_id, plan_hash, plan["before_checksum"], after))
    return {"user_id": user_id, "before_checksum": plan["before_checksum"], "after_checksum": after,
        "protected_rows_unchanged": True, "idempotent_replay": False}


def inventory(db_path):
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        rows = [inspect_user(conn, int(row[0])) for row in conn.execute("SELECT user_id FROM users ORDER BY user_id")]
        access = dict(Counter(str(row[0]) for row in conn.execute("SELECT status FROM user_access"))) if table_exists(conn, "user_access") else {}
        return {"mode": "read-only", "users": len(rows),
            "access_status_counts": access,
            "classes": dict(Counter(row["status"] for row in rows)),
            "conflict_codes": dict(Counter(code for row in rows for code in row["codes"])),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(conn.execute("PRAGMA foreign_key_check").fetchall())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--plan-user", type=int)
    args = parser.parse_args()
    if args.plan_user is None:
        result = inventory(args.db)
    else:
        with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            result = prepare_user_plan(conn, args.plan_user)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
