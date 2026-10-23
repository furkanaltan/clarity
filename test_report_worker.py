import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import rove_report_worker as worker


class ReportWorkerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "report-worker.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE app_accounts (user_id INTEGER, verified_at TEXT);
            CREATE TABLE report_jobs (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                report_month TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT INTO app_accounts VALUES (1, '2026-01-01');
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def insert_job(self, job_id, attempts, status="processing", updated_at="2026-09-10 08:00:00"):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO report_jobs
               (id, user_id, report_month, scheduled_at, status, attempts, last_error, updated_at)
               VALUES (?, 1, '2026-08', '2026-09-10 08:00:00', ?, ?, '', ?)""",
            (job_id, status, attempts, updated_at),
        )
        conn.commit()
        conn.close()

    def job(self, job_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT status, attempts, last_error FROM report_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        conn.close()
        return row

    def test_stale_attempts_one_and_two_are_pending_again(self):
        self.insert_job(1, 1)
        self.insert_job(2, 2)
        now = datetime(2026, 9, 10, 9, 0, 0)
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(worker.recover_stale_jobs(conn, now), 2)
        conn.commit()
        conn.close()

        self.assertEqual(self.job(1)[0], "pending")
        self.assertEqual(self.job(2)[0], "pending")
        with patch.object(worker, "DB_PATH", self.db_path), patch.object(worker, "report_now", return_value=now):
            claimed = worker.claim_due_jobs(limit=2)
        self.assertEqual({job["id"] for job in claimed}, {1, 2})
        self.assertEqual([job["attempts"] for job in claimed], [2, 3])

    def test_stale_max_attempt_is_terminal_failed_and_not_a_zombie(self):
        self.insert_job(3, worker.REPORT_MAX_ATTEMPTS)
        now = datetime(2026, 9, 10, 9, 0, 0)
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(worker.recover_stale_jobs(conn, now), 1)
        conn.commit()
        conn.close()

        status, attempts, error = self.job(3)
        self.assertEqual(status, "failed")
        self.assertEqual(attempts, worker.REPORT_MAX_ATTEMPTS)
        self.assertIn("Retry-Limit", error)
        with patch.object(worker, "DB_PATH", self.db_path), patch.object(worker, "report_now", return_value=now):
            self.assertEqual(worker.claim_due_jobs(), [])

    def test_fresh_processing_job_is_not_recovered_and_sent_state_is_unchanged(self):
        self.insert_job(4, 3, updated_at="2026-09-10 08:50:00")
        self.insert_job(5, 3, status="sent", updated_at="2026-09-10 08:00:00")
        now = datetime(2026, 9, 10, 9, 0, 0)
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(worker.recover_stale_jobs(conn, now), 0)
        conn.commit()
        conn.close()

        self.assertEqual(self.job(4)[0], "processing")
        self.assertEqual(self.job(5)[0], "sent")

    def test_success_and_explicit_failure_paths_keep_existing_semantics(self):
        self.insert_job(6, 1)
        self.insert_job(7, worker.REPORT_MAX_ATTEMPTS)
        with patch.object(worker, "DB_PATH", self.db_path):
            self.assertFalse(worker.mark_failed({"id": 6, "attempts": 1}, "temporary"))
            self.assertTrue(worker.mark_failed({"id": 7, "attempts": worker.REPORT_MAX_ATTEMPTS}, "final"))
            self.insert_job(8, 1)
            worker.mark_sent(8)
        self.assertEqual(self.job(6)[0], "pending")
        self.assertEqual(self.job(7)[0], "failed")
        self.assertEqual(self.job(8)[0], "sent")

    def test_second_worker_cannot_claim_job_already_claimed(self):
        self.insert_job(9, 0, status="pending", updated_at="2026-09-10 08:00:00")
        now = datetime(2026, 9, 10, 9, 0, 0)
        with patch.object(worker, "DB_PATH", self.db_path), patch.object(worker, "report_now", return_value=now):
            first = worker.claim_due_jobs()
            second = worker.claim_due_jobs()
        self.assertEqual([job["id"] for job in first], [9])
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
