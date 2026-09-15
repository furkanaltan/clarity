"""Render a structured Story V2 preview from one finalized snapshot only."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from report_story_v2 import story_from_snapshot_data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--month", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    db_path = args.db.resolve()
    if not db_path.is_file():
        raise SystemExit("database_not_found")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT id, schema_version, status, data_hash, report_data_json
                 FROM report_snapshots_v2
                WHERE user_id = ? AND report_month = ? AND schema_version = 2
                ORDER BY id DESC LIMIT 1""",
            (args.user_id, args.month),
        ).fetchone()
    if not row or row["status"] != "finalized":
        raise SystemExit("finalized_snapshot_not_found")

    story = story_from_snapshot_data(json.loads(row["report_data_json"]))
    payload = {
        "snapshot": {
            "id": int(row["id"]),
            "schema_version": int(row["schema_version"]),
            "status": row["status"],
            "data_hash": row["data_hash"],
        },
        "story": story,
    }
    if args.summary:
        payload["story"] = {
            "story_version": story["story_version"],
            "page_count": story["page_count"],
            "pages": {
                key: {
                    "title": page["title"],
                    "primary_metric": page["primary_metric"],
                    "text": page["text"],
                    "available": page["available"],
                    "empty_state": page["empty_state"],
                }
                for key, page in story["pages"].items()
            },
            "selected_insight": story["insight_engine"]["selected"],
            "next_steps": story["next_month_engine"]["steps"],
            "quality": story["quality"],
        }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(str(args.output))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
