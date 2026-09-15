"""Idempotently normalise legacy Telegram fixed-cost contracts into app_contracts."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from rove_app_state import DETAIL_SKIP_KEYS, DETAIL_LABELS, _legacy_contract_candidates, normalize_legacy_contracts


def _legacy_metrics(conn: sqlite3.Connection) -> dict:
    """Counts operational legacy contracts separately from summary and empty fields."""
    metrics = {"operational": 0, "metadata": 0, "empty_slots": 0, "invalid": 0}
    for row in conn.execute("SELECT fixed_costs_details FROM users"):
        try:
            details = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            metrics["invalid"] += 1
            continue
        if not isinstance(details, dict):
            metrics["invalid"] += 1
            continue
        metrics["operational"] += len(_legacy_contract_candidates(details))
        for section, values in details.items():
            if section not in DETAIL_LABELS or not isinstance(values, dict):
                continue
            for key, value in values.items():
                if key in DETAIL_SKIP_KEYS:
                    metrics["metadata"] += 1
                    continue
                try:
                    amount = float(value)
                except (TypeError, ValueError):
                    metrics["invalid"] += 1
                    continue
                if amount == 0:
                    metrics["empty_slots"] += 1
                elif amount < 0:
                    metrics["invalid"] += 1
    return metrics


def _contract_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM app_contracts").fetchone()[0])


def run(database: Path, apply: bool) -> dict:
    if not database.is_file():
        return {"status": "NO-GO", "error": "database_not_found"}

    backup = ""
    if apply:
        backup_dir = database.parent / "backups" / "legacy_contracts"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"clarity_before_legacy_contracts_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(database, backup_path)
        backup = str(backup_path)

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        users = [int(row[0]) for row in conn.execute("SELECT user_id FROM users ORDER BY user_id")]
        before_legacy = _legacy_metrics(conn)
        before_app_contracts = _contract_count(conn) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_contracts'"
        ).fetchone() else 0
        fixed_before = {
            int(row[0]): round(float(row[1] or 0), 2)
            for row in conn.execute("SELECT user_id, fixed_costs FROM users")
        }
        results = []
        for user_id in users:
            result = normalize_legacy_contracts(conn, user_id)
            if any(result.values()):
                results.append({"user_id": user_id, **result})
        summary = {
            key: sum(item[key] for item in results)
            for key in ("created", "already_normalized", "exact_duplicates", "uncertain", "invalid")
        }
        after_legacy = _legacy_metrics(conn)
        after_app_contracts = _contract_count(conn)
        fixed_after = {
            int(row[0]): round(float(row[1] or 0), 2)
            for row in conn.execute("SELECT user_id, fixed_costs FROM users")
        }
        unexpected_fixed_cost_delta_users = sum(
            fixed_before[user_id] != fixed_after[user_id]
            for user_id in fixed_before
        )
        second_run_new_contracts = 0
        if not apply:
            for user_id in users:
                second_run_new_contracts += normalize_legacy_contracts(conn, user_id)["created"]
        operational_before = before_app_contracts + before_legacy["operational"]
        operational_after = after_app_contracts + after_legacy["operational"]
        gate = {
            "users_to_migrate": sum(1 for item in results if item["created"] or item["exact_duplicates"]),
            "operational_legacy_contracts": before_legacy["operational"],
            "contracts_to_normalize": summary["created"] + summary["exact_duplicates"],
            "metadata_skipped": before_legacy["metadata"],
            "obsolete_empty_slots_skipped": before_legacy["empty_slots"],
            "invalid_contracts_skipped": before_legacy["invalid"],
            "duplicates_skipped": summary["exact_duplicates"],
            "expected_app_contracts_after": after_app_contracts,
            "users_with_unexpected_fixed_cost_delta": unexpected_fixed_cost_delta_users,
            "operational_contracts_before": operational_before,
            "operational_contracts_after": operational_after,
            "contract_count_preserved": operational_before == operational_after,
            "expected_operational_legacy_contracts_remaining": after_legacy["operational"],
            "second_run_new_contracts": second_run_new_contracts,
        }
        if apply:
            conn.commit()
        else:
            conn.rollback()
        return {
            "status": "OK",
            "mode": "apply" if apply else "dry-run",
            "database": str(database),
            "backup": backup,
            "users_changed": len(results),
            "summary": summary,
            "gate": gate,
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.db, args.apply), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
