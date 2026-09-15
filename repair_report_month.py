"""Requeue one report month after a verified report-truth correction.

The command changes report delivery state only. Financial records and historical
snapshots remain untouched; the current report schema creates a new snapshot.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME
REPORT_TIMEZONE = ZoneInfo("Europe/Berlin")


def month_is_valid(value: str) -> bool:
    return bool(re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value or ""))


def report_repair_counts(conn: sqlite3.Connection, report_month: str) -> dict:
    eligible_sql = """
        report_month = ?
        AND EXISTS (
            SELECT 1 FROM app_accounts aa
            WHERE aa.user_id = report_jobs.user_id
              AND TRIM(COALESCE(aa.verified_at, '')) != ''
        )
        AND COALESCE((
            SELECT ua.status FROM user_access ua
            WHERE ua.user_id = report_jobs.user_id LIMIT 1
        ), 'approved') IN ('approved', 'app_only')
    """
    jobs = int(conn.execute(
        f"SELECT COUNT(*) FROM report_jobs WHERE {eligible_sql}", (report_month,)
    ).fetchone()[0])
    processing = int(conn.execute(
        f"SELECT COUNT(*) FROM report_jobs WHERE {eligible_sql} AND status = 'processing'",
        (report_month,),
    ).fetchone()[0])
    links = int(conn.execute(
        """SELECT COUNT(*) FROM report_links rl
            WHERE rl.report_month = ? AND rl.status = 'active'
              AND EXISTS (
                  SELECT 1 FROM report_jobs rj
                  JOIN app_accounts aa ON aa.user_id = rj.user_id
                  LEFT JOIN user_access ua ON ua.user_id = rj.user_id
                  WHERE rj.user_id = rl.user_id AND rj.report_month = rl.report_month
                    AND TRIM(COALESCE(aa.verified_at, '')) != ''
                    AND COALESCE(ua.status, 'approved') IN ('approved', 'app_only')
              )""",
        (report_month,),
    ).fetchone()[0])
    snapshots = int(conn.execute(
        "SELECT COUNT(*) FROM report_snapshots_v2 WHERE report_month = ?",
        (report_month,),
    ).fetchone()[0])
    return {
        "eligible_jobs": jobs,
        "processing_jobs": processing,
        "active_links": links,
        "preserved_snapshots": snapshots,
    }


def repair_report_month(conn: sqlite3.Connection, report_month: str) -> dict:
    scheduled_at = datetime.now(REPORT_TIMEZONE).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("BEGIN IMMEDIATE")
    counts = report_repair_counts(conn, report_month)
    if counts["processing_jobs"]:
        conn.rollback()
        raise RuntimeError("report_worker_still_processing")
    if not counts["eligible_jobs"]:
        conn.rollback()
        raise RuntimeError("no_eligible_report_jobs")

    cursor = conn.execute(
        """UPDATE report_jobs
              SET status = 'pending', attempts = 0, last_error = '',
                  scheduled_at = ?, updated_at = ?
            WHERE report_month = ?
              AND EXISTS (
                  SELECT 1 FROM app_accounts aa
                  WHERE aa.user_id = report_jobs.user_id
                    AND TRIM(COALESCE(aa.verified_at, '')) != ''
              )
              AND COALESCE((
                  SELECT ua.status FROM user_access ua
                  WHERE ua.user_id = report_jobs.user_id LIMIT 1
              ), 'approved') IN ('approved', 'app_only')""",
        (scheduled_at, scheduled_at, report_month),
    )
    conn.commit()
    return {
        **counts,
        "requeued_jobs": int(cursor.rowcount),
        "scheduled_at": scheduled_at,
        "financial_rows_changed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Requeue a corrected Rov.E report month")
    parser.add_argument("--month", required=True, help="Report month in YYYY-MM format")
    parser.add_argument("--apply", action="store_true", help="Apply after reviewing the dry-run")
    args = parser.parse_args()
    if not month_is_valid(args.month):
        parser.error("--month must use YYYY-MM")

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        counts = report_repair_counts(conn, args.month)
        if not args.apply:
            print("mode=dry-run")
            for key, value in counts.items():
                print(f"{key}={value}")
            print("financial_rows_changed=0")
            return 0
        result = repair_report_month(conn, args.month)
        print("mode=applied")
        for key, value in result.items():
            print(f"{key}={value}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
