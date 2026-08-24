"""App-native monthly report queue, delivery and archive maintenance."""

from __future__ import annotations

import argparse
import logging
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import rove_account_delete_cleanup as account_delete_cleanup


APP_DIR = Path(__file__).resolve().parent
DB_NAME = os.getenv("CLARITY_DB_NAME", "clarity.db")
DB_PATH = Path(DB_NAME) if Path(DB_NAME).is_absolute() else APP_DIR / DB_NAME
REPORT_SEND_WINDOW_START_HOUR = int(os.getenv("REPORT_SEND_WINDOW_START_HOUR", "8"))
REPORT_SEND_WINDOW_END_HOUR = int(os.getenv("REPORT_SEND_WINDOW_END_HOUR", "14"))
REPORT_WORKER_BATCH_SIZE = int(os.getenv("REPORT_WORKER_BATCH_SIZE", "1"))
REPORT_MAX_ATTEMPTS = int(os.getenv("REPORT_MAX_ATTEMPTS", "3"))
REPORT_RETRY_DELAY_MINUTES = int(os.getenv("REPORT_RETRY_DELAY_MINUTES", "15"))
REPORT_PROCESSING_TIMEOUT_MINUTES = int(os.getenv("REPORT_PROCESSING_TIMEOUT_MINUTES", "45"))
REPORT_TIMEZONE = ZoneInfo("Europe/Berlin")
STEP_NORMAL = 10
ACCOUNT_DELETE_CLEANUP_BATCH_SIZE = 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("rove-report-worker")


def report_engine_module():
    """Defers optional report dependencies until a worker command needs them."""
    import report_engine

    return report_engine


def report_renderer_module():
    import rove_web_report_renderer

    return rove_web_report_renderer


def account_delete_cleanup_roots() -> tuple[Path, Path, Path, Path]:
    return account_delete_cleanup.configured_roots(APP_DIR)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def report_now() -> datetime:
    return datetime.now(REPORT_TIMEZONE).replace(tzinfo=None)


def previous_month_key(today: date | None = None) -> str:
    today = today or report_now().date()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def random_report_time_for_today() -> str:
    now = report_now()
    start = datetime(now.year, now.month, now.day, REPORT_SEND_WINDOW_START_HOUR)
    end = datetime(now.year, now.month, now.day, REPORT_SEND_WINDOW_END_HOUR)
    if now > start:
        start = now + timedelta(minutes=1)
    if start >= end:
        end = start + timedelta(hours=6)
    scheduled = start + timedelta(seconds=random.randint(0, max(1, int((end - start).total_seconds()))))
    return scheduled.strftime("%Y-%m-%d %H:%M:%S")


def active_app_user_ids() -> list[int]:
    """Every verified App account qualifies, including migrated Telegram users."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT u.user_id
                 FROM users u
                 JOIN app_accounts aa ON aa.user_id = u.user_id
                 LEFT JOIN user_access ua ON ua.user_id = u.user_id
                WHERE u.onboarding_step >= ?
                  AND TRIM(COALESCE(aa.verified_at, '')) != ''
                  AND COALESCE(ua.status, 'approved') IN ('approved', 'app_only')
                ORDER BY u.user_id""",
            (STEP_NORMAL,),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def enqueue_month(report_month: str | None = None) -> dict:
    month = report_month or previous_month_key()
    users = active_app_user_ids()
    created = 0
    with get_db() as conn:
        for user_id in users:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO report_jobs
                   (user_id, report_month, scheduled_at, status, attempts, last_error)
                   VALUES (?, ?, ?, 'pending', 0, '')""",
                (user_id, month, random_report_time_for_today()),
            )
            created += cursor.rowcount
        conn.commit()
    result = {"month": month, "eligible": len(users), "created": created, "existing": len(users) - created}
    logger.info("Report-Queue: %s", result)
    return result


def recover_stale_jobs(conn: sqlite3.Connection, now: datetime) -> int:
    cutoff = (now - timedelta(minutes=REPORT_PROCESSING_TIMEOUT_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """UPDATE report_jobs
              SET status = 'pending', scheduled_at = ?,
                  last_error = 'Worker-Neustart: haengenden Job erneut eingeplant', updated_at = ?
            WHERE status = 'processing' AND datetime(updated_at) < datetime(?)""",
        (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), cutoff),
    )
    return cursor.rowcount


def claim_due_jobs(limit: int = REPORT_WORKER_BATCH_SIZE) -> list[dict]:
    now_dt = report_now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        recovered = recover_stale_jobs(conn, now_dt)
        rows = conn.execute(
            """SELECT rj.*
                 FROM report_jobs rj
                WHERE rj.status = 'pending' AND datetime(rj.scheduled_at) <= datetime(?)
                  AND rj.attempts < ?
                  AND EXISTS (
                      SELECT 1 FROM app_accounts aa
                       WHERE aa.user_id = rj.user_id
                         AND TRIM(COALESCE(aa.verified_at, '')) != ''
                  )
                ORDER BY datetime(rj.scheduled_at), rj.id
                LIMIT ?""",
            (now, REPORT_MAX_ATTEMPTS, limit),
        ).fetchall()
        jobs = [dict(row) for row in rows]
        for job in jobs:
            conn.execute(
                """UPDATE report_jobs
                      SET status = 'processing', attempts = attempts + 1, updated_at = ?
                    WHERE id = ? AND status = 'pending'""",
                (now, job["id"]),
            )
            job["attempts"] = int(job.get("attempts") or 0) + 1
        conn.commit()
    if recovered:
        logger.warning("Haengende Report-Jobs wieder eingeplant: %s", recovered)
    return jobs


def mark_sent(job_id: int) -> None:
    now = report_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "UPDATE report_jobs SET status = 'sent', last_error = '', updated_at = ? WHERE id = ?",
            (now, job_id),
        )
        conn.commit()


def mark_skipped(job_id: int, reason: str) -> None:
    now = report_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "UPDATE report_jobs SET status = 'skipped', last_error = ?, updated_at = ? WHERE id = ?",
            ((reason or "Report uebersprungen")[:1000], now, job_id),
        )
        conn.commit()


def mark_failed(job: dict, error: str) -> bool:
    now_dt = report_now()
    final = int(job.get("attempts") or 0) >= REPORT_MAX_ATTEMPTS
    status = "failed" if final else "pending"
    scheduled = now_dt if final else now_dt + timedelta(minutes=REPORT_RETRY_DELAY_MINUTES)
    with get_db() as conn:
        conn.execute(
            """UPDATE report_jobs
                  SET status = ?, scheduled_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?""",
            (
                status,
                scheduled.strftime("%Y-%m-%d %H:%M:%S"),
                (error or "Unbekannter Fehler")[:1000],
                now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                job["id"],
            ),
        )
        conn.commit()
    return final


def process_due_jobs() -> dict:
    report_engine = report_engine_module()
    jobs = claim_due_jobs()
    result = {"claimed": len(jobs), "sent": 0, "skipped": 0, "retry": 0, "failed": 0}
    for job in jobs:
        try:
            report_engine.ensure_net_worth_column()
            if report_engine.send_report_to_user(job["user_id"], job["report_month"], bot=None):
                mark_sent(job["id"])
                result["sent"] += 1
            else:
                final = mark_failed(job, "send_report_to_user returned False")
                result["failed" if final else "retry"] += 1
        except report_engine.ReportSkipped as exc:
            mark_skipped(job["id"], str(exc))
            result["skipped"] += 1
        except Exception as exc:
            logger.exception("Report-Job %s fehlgeschlagen", job.get("id"))
            final = mark_failed(job, f"{type(exc).__name__}: {exc}")
            result["failed" if final else "retry"] += 1
    logger.info("Report-Worker: %s", result)
    return result


def maintain_archives() -> dict:
    # Run the bounded recovery first so report archive errors cannot defer it.
    cleanup_completed = account_delete_cleanup.retry_paths(
        DB_PATH, account_delete_cleanup_roots(), ACCOUNT_DELETE_CLEANUP_BATCH_SIZE
    )
    rove_web_report_renderer = report_renderer_module()
    report_engine = report_engine_module()
    removed = rove_web_report_renderer.cleanup_expired_reports()
    archived = report_engine.archive_old_reports()
    result = {
        "web_reports_removed": int(removed or 0),
        "pdf_reports_archived": int(archived or 0),
        "account_delete_cleanup_completed": int(cleanup_completed or 0),
    }
    logger.info("Report-Pflege: %s", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E app-native report worker")
    parser.add_argument("command", choices=("enqueue", "process", "maintain"))
    parser.add_argument("--month", help="Optional YYYY-MM override for enqueue")
    args = parser.parse_args()

    if args.command == "enqueue":
        enqueue_month(args.month)
        return 0
    if args.command == "maintain":
        maintain_archives()
        return 0
    result = process_due_jobs()
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
