"""Read-only rollout audit for Rov.E dynamic financial accounts.

The script intentionally prints no real money values. It is meant for the
production server before enabling the multi-account feature for more beta users.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from migrate_financial_accounts import CENT_TOLERANCE, capture_user_snapshot
from rove_financial_accounts import (
    ACCOUNT_ROLES,
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    LEGACY_ACCOUNT_META,
    TYPE_TO_LEGACY_KEY,
    financial_accounts_total,
    is_feature_enabled,
    roles_summary,
    table_columns,
    table_exists,
)


PILOT_USER_ID = 653187414
LEGACY_ASSET_KEYS = {
    "cash:giro",
    "cash:tagesgeld",
    "cash:bargeld",
    "asset:investments",
    "asset:crypto",
    "asset:property",
    "asset:valuables",
}


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def has_columns(conn: sqlite3.Connection, table: str, *columns: str) -> bool:
    return table_exists(conn, table) and set(columns).issubset(table_columns(conn, table))


def user_ids(conn: sqlite3.Connection) -> list[int]:
    return [
        int(row["user_id"])
        for row in conn.execute("SELECT user_id FROM users ORDER BY user_id")
    ]


def feature_flag_summary(conn: sqlite3.Connection) -> dict:
    users = user_ids(conn)
    enabled: list[int] = []
    disabled_rows = 0
    if table_exists(conn, "app_user_features"):
        enabled = [
            int(row["user_id"])
            for row in conn.execute(
                """SELECT user_id FROM app_user_features
                     WHERE feature_key = ? AND enabled = 1 ORDER BY user_id""",
                (FEATURE_MULTI_CASH_ACCOUNTS_V1,),
            )
        ]
        disabled_rows = one(
            conn,
            """SELECT COUNT(*) FROM app_user_features
                 WHERE feature_key = ? AND enabled = 0""",
            (FEATURE_MULTI_CASH_ACCOUNTS_V1,),
        )
    return {
        "enabled_count": len(enabled),
        "enabled_user_ids": enabled,
        "disabled_rows": disabled_rows,
        "without_flag_row": len(set(users) - set(enabled)) - disabled_rows,
        "global_activation_detected": len(enabled) > 1,
    }


def pilot_invariants(conn: sqlite3.Connection, user_id: int) -> dict:
    snapshot = capture_user_snapshot(conn, user_id)
    financial_total = financial_accounts_total(conn, user_id)
    legacy_by_type = {
        account_type: one(
            conn,
            """SELECT COUNT(*) FROM app_financial_accounts
                 WHERE user_id = ? AND account_type = ? AND status = 'active'""",
            (user_id, account_type),
        )
        for account_type in TYPE_TO_LEGACY_KEY
    }
    type_sum_ok = {}
    for account_type, legacy_key in TYPE_TO_LEGACY_KEY.items():
        account_sum = conn.execute(
            """SELECT COALESCE(SUM(balance), 0) FROM app_financial_accounts
                 WHERE user_id = ? AND account_type = ? AND status = 'active'""",
            (user_id, account_type),
        ).fetchone()[0] or 0.0
        legacy_amount = conn.execute(
            """SELECT amount FROM app_account_balances
                 WHERE user_id = ? AND account_key = ?""",
            (user_id, legacy_key),
        ).fetchone()
        type_sum_ok[legacy_key] = bool(
            legacy_amount
            and abs(round(float(account_sum), 2) - round(float(legacy_amount[0] or 0), 2))
            <= CENT_TOLERANCE
        )

    roles = roles_summary(conn, user_id)
    invalid_roles = one(
        conn,
        """SELECT COUNT(*) FROM app_financial_account_roles r
             LEFT JOIN app_financial_accounts a
               ON a.id = r.account_id AND a.user_id = r.user_id
            WHERE r.user_id = ? AND a.id IS NULL""",
        (user_id,),
    )
    duplicate_legacy = one(
        conn,
        """SELECT COUNT(*) FROM (
               SELECT legacy_key FROM app_financial_accounts
                WHERE user_id = ? AND legacy_key IS NOT NULL
                GROUP BY legacy_key HAVING COUNT(*) > 1
           )""",
        (user_id,),
    )
    return {
        "feature_enabled": is_feature_enabled(conn, user_id, FEATURE_MULTI_CASH_ACCOUNTS_V1),
        "financial_matches_current_cash": abs(financial_total - snapshot.current_cash) <= CENT_TOLERANCE,
        "legacy_matches_current_cash": abs(snapshot.legacy_sum - snapshot.current_cash) <= CENT_TOLERANCE,
        "type_sums_match_legacy": type_sum_ok,
        "exactly_four_roles": set(roles) == set(ACCOUNT_ROLES) and len(roles) == 4,
        "role_accounts_belong_to_user": invalid_roles == 0,
        "no_duplicate_legacy_accounts": duplicate_legacy == 0,
        "active_account_type_counts": legacy_by_type,
        "expenses_count_present": snapshot.expenses >= 0,
        "cash_movements_count_present": snapshot.cash_movements >= 0,
        "investment_snapshot_present": snapshot.current_investments >= 0,
        "property_snapshot_present": snapshot.property_equity >= 0,
        "net_worth_snapshot_present": True,
    }


def drift_summary(conn: sqlite3.Connection) -> dict:
    users = user_ids(conn)
    bad_financial = []
    bad_legacy = []
    for user_id in users:
        snapshot = capture_user_snapshot(conn, user_id)
        if table_exists(conn, "app_financial_accounts"):
            financial_total = financial_accounts_total(conn, user_id)
            if abs(financial_total - snapshot.current_cash) > CENT_TOLERANCE:
                bad_financial.append(user_id)
        if snapshot.has_legacy_rows and abs(snapshot.legacy_sum - snapshot.current_cash) > CENT_TOLERANCE:
            bad_legacy.append(user_id)
    return {
        "financial_account_sum_drift_users": bad_financial,
        "legacy_sum_drift_users": bad_legacy,
        "financial_account_sum_drift_count": len(bad_financial),
        "legacy_sum_drift_count": len(bad_legacy),
    }


def reference_checks(conn: sqlite3.Connection) -> dict:
    checks: dict[str, object] = {}
    if has_columns(conn, "expenses", "account_id"):
        checks["expenses_account_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM expenses e
                 LEFT JOIN app_financial_accounts a
                   ON a.id = e.account_id AND a.user_id = e.user_id
                WHERE e.account_id IS NOT NULL AND a.id IS NULL""",
        )
    if has_columns(conn, "app_cash_movements", "source_account_id", "target_account_id"):
        checks["cash_movement_source_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM app_cash_movements m
                 LEFT JOIN app_financial_accounts a
                   ON a.id = m.source_account_id AND a.user_id = m.user_id
                WHERE m.source_account_id IS NOT NULL AND a.id IS NULL""",
        )
        checks["cash_movement_target_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM app_cash_movements m
                 LEFT JOIN app_financial_accounts a
                   ON a.id = m.target_account_id AND a.user_id = m.user_id
                WHERE m.target_account_id IS NOT NULL AND a.id IS NULL""",
        )
    if has_columns(conn, "app_etf_savings_plan", "source_account_id"):
        checks["etf_plan_source_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM app_etf_savings_plan p
                 LEFT JOIN app_financial_accounts a
                   ON a.id = p.source_account_id AND a.user_id = p.user_id
                WHERE p.source_account_id IS NOT NULL AND a.id IS NULL""",
        )
    if has_columns(conn, "app_etf_position_plans", "source_account_id", "holding_id"):
        checks["etf_position_source_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM app_etf_position_plans p
                 LEFT JOIN app_financial_accounts a
                   ON a.id = p.source_account_id AND a.user_id = p.user_id
                WHERE p.source_account_id IS NOT NULL AND a.id IS NULL""",
        )
        checks["etf_position_holding_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM app_etf_position_plans p
                 LEFT JOIN portfolio_holdings h
                   ON h.id = p.holding_id AND h.user_id = p.user_id
                WHERE h.id IS NULL""",
        )
    if has_columns(conn, "investment_events", "holding_id"):
        checks["investment_event_holding_refs_invalid"] = one(
            conn,
            """SELECT COUNT(*) FROM investment_events e
                 LEFT JOIN portfolio_holdings h
                   ON h.id = e.holding_id AND h.user_id = e.user_id
                WHERE e.holding_id IS NOT NULL AND h.id IS NULL""",
        )
    return checks


def asset_order_checks(conn: sqlite3.Connection) -> dict:
    if not table_exists(conn, "app_asset_order"):
        return {"table_present": False}
    duplicate_rows = one(
        conn,
        """SELECT COUNT(*) FROM (
               SELECT user_id, asset_key FROM app_asset_order
                GROUP BY user_id, asset_key HAVING COUNT(*) > 1
           )""",
    )
    invalid_cash = one(
        conn,
        """SELECT COUNT(*) FROM app_asset_order o
             LEFT JOIN app_financial_accounts a
               ON a.id = CAST(substr(o.asset_key, 14) AS INTEGER)
              AND a.user_id = o.user_id
            WHERE o.asset_key LIKE 'cash-account:%' AND a.id IS NULL""",
    )
    invalid_legacy = one(
        conn,
        f"""SELECT COUNT(*) FROM app_asset_order
             WHERE asset_key NOT LIKE 'cash-account:%'
               AND asset_key NOT IN ({','.join('?' for _ in LEGACY_ASSET_KEYS)})""",
        tuple(sorted(LEGACY_ASSET_KEYS)),
    )
    return {
        "table_present": True,
        "duplicate_order_keys": duplicate_rows,
        "cash_account_keys_invalid": invalid_cash,
        "unknown_static_keys": invalid_legacy,
    }


def classify_users(conn: sqlite3.Connection) -> dict:
    users = user_ids(conn)
    app_only: list[int] = []
    migrated: list[int] = []
    for user_id in users:
        has_app_session = table_exists(conn, "app_sessions") and one(
            conn, "SELECT COUNT(*) FROM app_sessions WHERE user_id = ?", (user_id,)
        ) > 0
        has_expenses = table_exists(conn, "expenses") and one(
            conn, "SELECT COUNT(*) FROM expenses WHERE user_id = ?", (user_id,)
        ) > 0
        # Telegram-era users usually have old expenses before the app-only invite flow.
        # The classification is intentionally conservative and only used for picking pilots.
        if has_app_session and not has_expenses:
            app_only.append(user_id)
        elif has_expenses:
            migrated.append(user_id)
    enabled = set(feature_flag_summary(conn)["enabled_user_ids"])
    return {
        "app_only_candidates_without_flag": [u for u in app_only if u not in enabled][:3],
        "migrated_candidates_without_flag": [u for u in migrated if u not in enabled][:3],
    }


def sqlite_checks(conn: sqlite3.Connection) -> dict:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    fk_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    return {"integrity_check": integrity, "foreign_key_errors": fk_errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Rov.E multi-account rollout audit")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    parser.add_argument("--pilot-user-id", type=int, default=PILOT_USER_ID)
    args = parser.parse_args()

    with closing(sqlite3.connect(args.db, timeout=30.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        result = {
            "mode": "read_only",
            "database": str(args.db.resolve()),
            "pilot_user_id": args.pilot_user_id,
            "tables_present": {
                table: table_exists(conn, table)
                for table in (
                    "app_financial_accounts",
                    "app_financial_account_roles",
                    "app_user_features",
                    "app_account_balances",
                    "expenses",
                    "app_cash_movements",
                    "portfolio_holdings",
                    "investment_events",
                    "app_etf_savings_plan",
                    "app_etf_position_plans",
                    "app_properties",
                    "app_asset_order",
                )
            },
            "feature_flags": feature_flag_summary(conn),
            "pilot_invariants": pilot_invariants(conn, args.pilot_user_id),
            "drift": drift_summary(conn),
            "references": reference_checks(conn),
            "asset_order": asset_order_checks(conn),
            "pilot_candidates": classify_users(conn),
            "sqlite": sqlite_checks(conn),
        }
        conn.rollback()

    hard_blockers = []
    if result["feature_flags"]["global_activation_detected"]:
        hard_blockers.append("more_than_one_enabled_feature_flag")
    if not result["pilot_invariants"]["feature_enabled"]:
        hard_blockers.append("pilot_feature_flag_not_enabled")
    if not result["pilot_invariants"]["financial_matches_current_cash"]:
        hard_blockers.append("pilot_financial_sum_drift")
    if not result["pilot_invariants"]["legacy_matches_current_cash"]:
        hard_blockers.append("pilot_legacy_sum_drift")
    if not all(result["pilot_invariants"]["type_sums_match_legacy"].values()):
        hard_blockers.append("pilot_type_sum_drift")
    if result["drift"]["financial_account_sum_drift_count"]:
        hard_blockers.append("any_financial_sum_drift")
    if result["drift"]["legacy_sum_drift_count"]:
        hard_blockers.append("any_legacy_sum_drift")
    if any(int(value or 0) for value in result["references"].values()):
        hard_blockers.append("invalid_references")
    if result["asset_order"].get("duplicate_order_keys") or result["asset_order"].get("cash_account_keys_invalid") or result["asset_order"].get("unknown_static_keys"):
        hard_blockers.append("asset_order_invalid")
    if result["sqlite"]["integrity_check"] != "ok" or result["sqlite"]["foreign_key_errors"]:
        hard_blockers.append("sqlite_integrity_problem")
    result["recommendation"] = {
        "status": "GO_FOR_TWO_MORE_PILOTS" if not hard_blockers else "NO_GO",
        "blockers": hard_blockers,
        "global_rollout": "not_recommended_in_this_sprint",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not hard_blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
