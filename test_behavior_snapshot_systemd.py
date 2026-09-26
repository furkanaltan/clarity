from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rove_behavior_snapshot as snapshot


ROOT = Path(__file__).resolve().parent
SYSTEMD_DIR = ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "rove-behavior-snapshot.service"
TIMER = SYSTEMD_DIR / "rove-behavior-snapshot.timer"


class SnapshotSystemdTests(unittest.TestCase):
    def test_service_uses_bounded_canonical_worker(self):
        text = SERVICE.read_text()
        self.assertIn("Type=oneshot", text)
        self.assertIn("User=root", text)
        self.assertIn("Group=root", text)
        self.assertIn("WorkingDirectory=/root/clarity", text)
        self.assertIn(
            "ExecStart=/root/rove-app-api-venv/bin/python "
            "/root/clarity/rove_behavior_snapshot.py "
                "/root/clarity/clarity.db --process-pending --limit 3",
            text,
        )
        self.assertIn("UMask=0077", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("TimeoutStartSec=30s", text)
        self.assertNotIn("EnvironmentFile=", text)

    def test_timer_is_persistent_and_runs_every_five_minutes(self):
        text = TIMER.read_text()
        self.assertIn("OnCalendar=*-*-* *:00/5:00", text)
        self.assertIn("Persistent=true", text)
        self.assertIn("AccuracySec=1min", text)
        self.assertIn("Unit=rove-behavior-snapshot.service", text)
        self.assertNotIn("RandomizedDelaySec=", text)

    def test_snapshot_has_no_second_scheduler_path(self):
        unit_files = list(SYSTEMD_DIR.glob("*behavior-snapshot.*"))
        self.assertEqual(
            sorted(path.name for path in unit_files),
            ["rove-behavior-snapshot.service", "rove-behavior-snapshot.timer"],
        )
        for path in SYSTEMD_DIR.glob("*.service"):
            if path != SERVICE:
                self.assertNotIn("rove_behavior_snapshot.py", path.read_text())

    def test_worker_summary_is_bounded_and_hides_user_ids(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as database:
            with sqlite3.connect(database.name) as conn:
                snapshot.ensure_behavior_snapshot_table(conn)
            with patch(
                "rove_behavior_snapshot.process_pending_behavior_snapshots",
                return_value=[{"user_id": 653187414, "status": "ready"}],
            ) as process:
                summary = snapshot.run_pending_snapshot_worker(database.name, limit=3)

            process.assert_called_once()
            self.assertEqual(process.call_args.kwargs["limit"], 3)
            self.assertEqual(summary["processed_count"], 1)
            self.assertEqual(summary["completed"], 1)
            self.assertNotIn("user_id", json.dumps(summary))

    def test_worker_result_error_returns_nonzero_and_bounded_output(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as database:
            with sqlite3.connect(database.name) as conn:
                snapshot.ensure_behavior_snapshot_table(conn)
            with patch(
                "rove_behavior_snapshot.process_pending_behavior_snapshots",
                return_value=[{"user_id": 653187414, "status": "error"}],
            ), patch("sys.stdout", new_callable=io.StringIO) as output:
                exit_code = snapshot.main([
                    database.name,
                    "--process-pending",
                    "--limit",
                    "3",
                ])

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["failed"], 1)
            self.assertNotIn("653187414", output.getvalue())

    def test_worker_exception_returns_nonzero_without_traceback_payload(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as database:
            with patch(
                "rove_behavior_snapshot.process_pending_behavior_snapshots",
                side_effect=RuntimeError("internal details must not be logged"),
            ), patch("sys.stdout", new_callable=io.StringIO) as output:
                exit_code = snapshot.main([
                    database.name,
                    "--process-pending",
                    "--limit",
                    "1",
                ])

            self.assertEqual(exit_code, 1)
            self.assertEqual(
                json.loads(output.getvalue()),
                {"error_class": "RuntimeError", "status": "error"},
            )


if __name__ == "__main__":
    unittest.main()
