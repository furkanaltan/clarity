"""Dry-run-first repair for unassigned legacy ETF savings-plan events."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from rove_investment_contributions import ensure_investment_contribution_schema


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def create_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups" / "etf_contributions"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{db_path.stem}_before_etf_contribution_repair_"
        f"{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    )
    with closing(sqlite3.connect(db_path)) as source:
        with closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
    backup_path.chmod(0o600)
    return backup_path


def repair_candidates(conn: sqlite3.Connection) -> list[dict]:
    if "holding_id" not in table_columns(conn, "investment_events"):
        return []
    events = conn.execute(
        """SELECT id, user_id, amount, asset_name, event_type, created_at,
                  strftime('%Y-%m', created_at) AS event_month
             FROM investment_events
            WHERE source = 'app_etf_plan' AND asset_type = 'etf'
              AND holding_id IS NULL AND direction = 'in'
              AND event_type = 'recurring_plan'
            ORDER BY id"""
    ).fetchall()
    result: list[dict] = []
    for event in events:
        plan_source = "position_plan"
        plans = conn.execute(
            """SELECT pp.holding_id, pp.monthly_amount, pp.start_month,
                      ph.instrument_label, COALESCE(ph.instrument_type, 'etf') AS instrument_type,
                      COALESCE(ph.valuation_enabled, 0) AS valuation_enabled,
                      ph.quantity, ph.price_symbol
                 FROM app_etf_position_plans pp
                 JOIN portfolio_holdings ph
                   ON ph.id = pp.holding_id AND ph.user_id = pp.user_id
                WHERE pp.user_id = ? AND pp.active = 1
                  AND ABS(pp.monthly_amount - ?) < 0.005
                  AND pp.start_month <= ?
                  AND LOWER(COALESCE(ph.instrument_type, 'etf')) = 'etf'
                ORDER BY pp.holding_id""",
            (int(event["user_id"]), float(event["amount"] or 0), str(event["event_month"])),
        ).fetchall()
        active_position_plans = int(conn.execute(
            "SELECT COUNT(*) FROM app_etf_position_plans WHERE user_id = ? AND active = 1",
            (int(event["user_id"]),),
        ).fetchone()[0])
        if not plans and active_position_plans == 0:
            # Alte Nutzer hatten nur einen globalen ETF-Plan. Wenn dessen Betrag
            # und der historische Holding-Sparbetrag exakt zu genau einem Live-ETF
            # passen, ist dieselbe Zuordnung belegbar und kann fuer kuenftige Monate
            # als echter Positionsplan uebernommen werden.
            plans = conn.execute(
                """SELECT ph.id AS holding_id, ph.monthly_contribution AS monthly_amount,
                          sp.start_month, ph.instrument_label,
                          COALESCE(ph.instrument_type, 'etf') AS instrument_type,
                          COALESCE(ph.valuation_enabled, 0) AS valuation_enabled,
                          ph.quantity, ph.price_symbol
                     FROM portfolio_holdings ph
                     JOIN users u ON u.user_id = ph.user_id
                     JOIN app_etf_savings_plan sp ON sp.user_id = ph.user_id
                    WHERE ph.user_id = ? AND sp.active = 1
                      AND sp.start_month <= ?
                      AND ABS(COALESCE(u.etf_savings, 0) - ?) < 0.005
                      AND ABS(COALESCE(ph.monthly_contribution, 0) - ?) < 0.005
                      AND LOWER(COALESCE(ph.instrument_type, 'etf')) = 'etf'
                    ORDER BY ph.id""",
                (
                    int(event["user_id"]), str(event["event_month"]),
                    float(event["amount"] or 0), float(event["amount"] or 0),
                ),
            ).fetchall()
            if plans:
                plan_source = "legacy_holding"
        status = "ready"
        reason = ""
        holding = plans[0] if len(plans) == 1 else None
        if not plans:
            status, reason = "skipped", "no_exact_position_plan"
        elif len(plans) > 1:
            status, reason = "blocked", "ambiguous_position_plans"
        elif not bool(
            holding["valuation_enabled"]
            and float(holding["quantity"] or 0) > 0
            and str(holding["price_symbol"] or "").strip()
        ):
            status, reason = "blocked", "manual_holding_requires_review"
        elif conn.execute(
            """SELECT 1 FROM investment_events
                 WHERE user_id = ? AND holding_id = ? AND source = 'app_etf_plan'
                   AND asset_type = 'etf'
                   AND event_type IN ('recurring_plan', 'recurring_plan_pending')
                   AND strftime('%Y-%m', created_at) = ? LIMIT 1""",
            (int(event["user_id"]), int(holding["holding_id"]), str(event["event_month"])),
        ).fetchone():
            status, reason = "blocked", "holding_month_already_assigned"
        result.append({
            "event_id": int(event["id"]),
            "user_id": int(event["user_id"]),
            "month": str(event["event_month"]),
            "amount": round(float(event["amount"] or 0), 2),
            "old_name": str(event["asset_name"] or ""),
            "holding_id": int(holding["holding_id"]) if holding else None,
            "holding_name": str(holding["instrument_label"]) if holding else "",
            "plan_source": plan_source if holding else "",
            "create_position_plan": bool(holding and plan_source == "legacy_holding"),
            "status": status,
            "reason": reason,
        })
    return result


def run(db_path: Path, *, apply: bool) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    backup = create_backup(db_path) if apply else None
    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        schema_missing = "holding_id" not in table_columns(conn, "investment_events")
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            ensure_investment_contribution_schema(conn)
            conn.commit()
        candidates = repair_candidates(conn)
        changed = 0
        plans_created = 0
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            for item in candidates:
                if item["status"] != "ready":
                    continue
                if item["create_position_plan"]:
                    global_plan = conn.execute(
                        """SELECT execution_day, source_account, source_account_id,
                                  mode, active, start_month
                             FROM app_etf_savings_plan
                            WHERE user_id = ? AND active = 1 LIMIT 1""",
                        (item["user_id"],),
                    ).fetchone()
                    if not global_plan:
                        raise RuntimeError("global_etf_plan_missing_during_repair")
                    cursor = conn.execute(
                        """INSERT INTO app_etf_position_plans
                               (user_id, holding_id, monthly_amount, execution_day,
                                source_account, source_account_id, mode, active, start_month,
                                updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(user_id, holding_id) DO NOTHING""",
                        (
                            item["user_id"], item["holding_id"], item["amount"],
                            int(global_plan["execution_day"]), str(global_plan["source_account"]),
                            global_plan["source_account_id"], str(global_plan["mode"]),
                            int(global_plan["active"]), str(global_plan["start_month"]),
                        ),
                    )
                    plans_created += int(cursor.rowcount)
                cursor = conn.execute(
                    """UPDATE investment_events
                          SET holding_id = ?, asset_name = ?,
                              event_type = 'recurring_plan_pending'
                        WHERE id = ? AND user_id = ? AND holding_id IS NULL
                          AND source = 'app_etf_plan' AND asset_type = 'etf'""",
                    (
                        item["holding_id"], item["holding_name"], item["event_id"],
                        item["user_id"],
                    ),
                )
                changed += int(cursor.rowcount)
            conn.commit()
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    return {
        "mode": "apply" if apply else "dry-run",
        "database": str(db_path),
        "backup": str(backup) if backup else "",
        "schema_missing": schema_missing,
        "summary": {
            "total": len(candidates),
            "ready": sum(item["status"] == "ready" for item in candidates),
            "blocked": sum(item["status"] == "blocked" for item in candidates),
            "skipped": sum(item["status"] == "skipped" for item in candidates),
            "changed": changed,
            "position_plans_created": plans_created,
        },
        "candidates": candidates,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E ETF contribution assignment repair")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "clarity.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args.db.resolve(), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["integrity_check"] != "ok" or result["foreign_key_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
