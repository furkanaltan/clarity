"""Explicit, idempotent publisher for Rov.E's initial feature announcements."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from rove_feature_announcements import seed_default_feature_announcements


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Rov.E feature announcements once.")
    parser.add_argument("--db", required=True, help="Path to the Rov.E SQLite database")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        raise SystemExit(f"Database does not exist: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        before_states = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='app_feature_announcement_state'"
        ).fetchone()[0]
        state_rows_before = (
            conn.execute("SELECT COUNT(*) FROM app_feature_announcement_state").fetchone()[0]
            if before_states
            else 0
        )
        conn.execute("BEGIN IMMEDIATE")
        published = seed_default_feature_announcements(conn)
        active = conn.execute(
            "SELECT COUNT(*) FROM app_feature_announcements WHERE is_active = 1"
        ).fetchone()[0]
        state_rows_after = conn.execute(
            "SELECT COUNT(*) FROM app_feature_announcement_state"
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    print(json.dumps({
        "published": published,
        "active_definitions": active,
        "user_state_rows_created": state_rows_after - state_rows_before,
    }, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
