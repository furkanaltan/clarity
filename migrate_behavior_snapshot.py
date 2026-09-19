"""Explicit migration for the additive Coach V4 behavior snapshot schema."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from rove_behavior_snapshot import (
    SNAPSHOT_METRIC_COLUMNS,
    SNAPSHOT_TABLE,
    ensure_behavior_snapshot_table,
)


SNAPSHOT_QUEUE_INDEX = "idx_app_behavior_snapshot_queue"
SNAPSHOT_METRIC_DEFINITIONS = {
    "metrics_invalidations_received": "INTEGER NOT NULL DEFAULT 0",
    "metrics_invalidations_coalesced": "INTEGER NOT NULL DEFAULT 0",
    "metrics_recomputes_avoided_by_coalescing": "INTEGER NOT NULL DEFAULT 0",
    "metrics_recomputes_started": "INTEGER NOT NULL DEFAULT 0",
    "metrics_recomputes_completed": "INTEGER NOT NULL DEFAULT 0",
    "metrics_recomputes_failed": "INTEGER NOT NULL DEFAULT 0",
    "metrics_recompute_duration_total_ms": "REAL NOT NULL DEFAULT 0",
    "metrics_recompute_duration_count": "INTEGER NOT NULL DEFAULT 0",
    "metrics_max_recompute_duration_ms": "REAL NOT NULL DEFAULT 0",
}


def _has_object(conn: sqlite3.Connection, object_type: str, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
        (object_type, name),
    ).fetchone())


def _schema_state(conn: sqlite3.Connection) -> dict[str, bool]:
    columns = {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{SNAPSHOT_TABLE}")')
    } if _has_object(conn, "table", SNAPSHOT_TABLE) else set()
    return {
        "table": _has_object(conn, "table", SNAPSHOT_TABLE),
        "queue_index": _has_object(conn, "index", SNAPSHOT_QUEUE_INDEX),
        "metrics": all(column in columns for column in SNAPSHOT_METRIC_COLUMNS),
    }


def _add_metric_columns(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{SNAPSHOT_TABLE}")')
    }
    for name, definition in SNAPSHOT_METRIC_DEFINITIONS.items():
        if name not in columns:
            conn.execute(
                f'ALTER TABLE "{SNAPSHOT_TABLE}" ADD COLUMN "{name}" {definition}'
            )


def create_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups" / "behavior_snapshot"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{db_path.stem}_before_behavior_snapshot_"
        f"{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    )
    with closing(sqlite3.connect(db_path)) as source:
        with closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
    backup_path.chmod(0o600)
    return backup_path


def run(db_path: Path, *, apply: bool) -> dict:
    """Inspect or apply the additive snapshot schema to one database."""
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        before = _schema_state(conn)

    backup = None
    changed = False
    if apply and not (before["table"] and before["queue_index"] and before["metrics"]):
        backup = create_backup(db_path)
        with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                conn.execute("BEGIN IMMEDIATE")
                ensure_behavior_snapshot_table(conn)
                _add_metric_columns(conn)
                integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
                foreign_key_errors = list(conn.execute("PRAGMA foreign_key_check"))
                if integrity != "ok" or foreign_key_errors:
                    raise RuntimeError(
                        f"snapshot_schema_validation_failed:{integrity}:"
                        f"fk={len(foreign_key_errors)}"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        changed = True

    with closing(sqlite3.connect(db_path, timeout=30.0)) as conn:
        after = _schema_state(conn)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())

    return {
        "mode": "apply" if apply else "dry-run",
        "database": str(db_path),
        "backup": str(backup) if backup else "",
        "table_before": before["table"],
        "queue_index_before": before["queue_index"],
        "metrics_before": before["metrics"],
        "table_after": after["table"],
        "queue_index_after": after["queue_index"],
        "metrics_after": after["metrics"],
        "changed": changed,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
        "schema_version": 1,
    }


def result_is_valid(result: dict, *, apply: bool) -> bool:
    """Validate either a dry-run inspection or an applied schema result."""
    valid = (
        result["integrity_check"] == "ok"
        and result["foreign_key_errors"] == 0
    )
    if apply:
        valid = valid and result["table_after"] and result["queue_index_after"] and result["metrics_after"]
    return bool(valid)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E Coach V4 snapshot schema migration")
    parser.add_argument("--db", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args.db, apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result_is_valid(result, apply=bool(args.apply)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
