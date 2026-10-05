"""Deletion-ledger regressions using disposable files and synthetic accounts."""

import errno
import fcntl
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

import rove_account_delete_cleanup as cleanup
import rove_app_api as api
import rove_recovery_set as recovery
import test_contract_cancellation_v1 as fixtures


VALID_RECORD = b'{"user_id":99,"deleted_at":"2026-01-01T00:00:00+00:00"}\n'
INVALID_LEDGERS = (
    b"not-json\n",
    VALID_RECORD + b"broken\n",
    VALID_RECORD[:-1],
    VALID_RECORD + b'{"user_id":',
    b'{"user_id":1}\n',
    b'{"deleted_at":"2026-01-01T00:00:00Z"}\n',
    b'{"user_id":true,"deleted_at":"2026-01-01T00:00:00Z"}\n',
    b'{"user_id":"1","deleted_at":"2026-01-01T00:00:00Z"}\n',
    b'{"user_id":0,"deleted_at":"2026-01-01T00:00:00Z"}\n',
    b'{"user_id":9223372036854775808,"deleted_at":"2026-01-01T00:00:00Z"}\n',
    b'{"user_id":1,"deleted_at":"not-a-date"}\n',
    b'{"user_id":1,"deleted_at":"2026-01-01T00:00:00"}\n',
    b'{"user_id":1,"deleted_at":"2026-01-01T00:00:00+01:00"}\n',
    b'{"user_id":1,"deleted_at":"9999-01-01T00:00:00Z"}\n',
    b'{"user_id":1,"user_id":2,"deleted_at":"2026-01-01T00:00:00Z"}\n',
    VALID_RECORD[:-2] + b',"extra":"unexpected"}\n',
    b"[]\n",
    b"\xff\n",
    b"\n",
    b" \n",
    b"[" * 1100 + b"0" + b"]" * 1100 + b"\n",
)


class TombstoneAppendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ledger = Path(temp.name) / "ledger.jsonl"
        self.ledger.touch(mode=0o600)

    def test_valid_existing_ledger_is_preserved_and_append_matches_recovery_schema(self):
        self.ledger.write_bytes(VALID_RECORD)
        cleanup.record_delete_tombstone(42, self.ledger)
        raw = self.ledger.read_bytes()
        self.assertTrue(raw.startswith(VALID_RECORD))
        self.assertEqual(recovery.parse_ledger(raw), ({42, 99}, 2))
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(json.loads(raw.splitlines()[-1])), {"user_id", "deleted_at"})

    def test_existing_empty_ledger_is_structurally_valid_only(self):
        cleanup.record_delete_tombstone(1, self.ledger)
        self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), ({1}, 1))

    def test_missing_ledger_is_not_silently_recreated(self):
        self.ledger.unlink()
        with self.assertRaises(OSError):
            cleanup.record_delete_tombstone(1, self.ledger)
        self.assertFalse(self.ledger.exists())

    def test_invalid_ledgers_are_not_appended_repaired_or_fsynced(self):
        for raw in INVALID_LEDGERS:
            with self.subTest(raw=raw):
                self.ledger.write_bytes(raw)
                with patch.object(cleanup.os, "write") as write, \
                     patch.object(cleanup.os, "fsync") as fsync:
                    with self.assertRaises(cleanup.TombstoneLedgerError):
                        cleanup.record_delete_tombstone(1, self.ledger)
                write.assert_not_called()
                fsync.assert_not_called()
                self.assertEqual(self.ledger.read_bytes(), raw)

    def test_invalid_new_user_id_is_rejected_before_append(self):
        for owner in (True, None, "1", 0, -1, 9223372036854775808):
            with self.subTest(owner=owner):
                with self.assertRaises(cleanup.TombstoneLedgerError):
                    cleanup.record_delete_tombstone(owner, self.ledger)
                self.assertEqual(self.ledger.read_bytes(), b"")

    def test_partial_writes_continue_at_the_exact_remaining_offset(self):
        real_write, real_fsync = os.write, os.fsync
        remaining = []

        def short_write(fd, payload):
            remaining.append(payload)
            return real_write(fd, payload[:7])

        def durable(fd):
            self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), ({1}, 1))
            real_fsync(fd)

        with patch.object(cleanup.os, "write", side_effect=short_write), \
             patch.object(cleanup.os, "fsync", side_effect=durable) as fsync:
            cleanup.record_delete_tombstone(1, self.ledger)
        self.assertGreater(len(remaining), 1)
        for previous, current in zip(remaining, remaining[1:]):
            self.assertEqual(current, previous[7:])
        fsync.assert_called_once()

    def test_invalid_write_results_do_not_fsync(self):
        for result in (0, -1, None, True, 1.5, "1", 10000):
            with self.subTest(result=result):
                with patch.object(cleanup.os, "write", return_value=result), \
                     patch.object(cleanup.os, "fsync") as fsync:
                    with self.assertRaises(cleanup.TombstoneLedgerError):
                        cleanup.record_delete_tombstone(1, self.ledger)
                fsync.assert_not_called()
                self.assertEqual(self.ledger.read_bytes(), b"")

    def test_symlink_and_hardlink_are_rejected_without_modifying_the_ledger(self):
        alias = self.ledger.parent / "alias.jsonl"
        alias.symlink_to(self.ledger)
        with self.assertRaises(OSError):
            cleanup.record_delete_tombstone(1, alias)
        alias.unlink()
        os.link(self.ledger, alias)
        with self.assertRaises(cleanup.TombstoneLedgerError):
            cleanup.record_delete_tombstone(1, alias)
        self.assertEqual(self.ledger.read_bytes(), b"")

    def test_replaced_path_is_rejected_before_append(self):
        real_flock = fcntl.flock

        def replace_before_validation(fd, operation):
            real_flock(fd, operation)
            self.ledger.rename(self.ledger.with_suffix(".old"))
            self.ledger.touch(mode=0o600)

        with patch.object(cleanup.fcntl, "flock", side_effect=replace_before_validation), \
             patch.object(cleanup.os, "write") as write:
            with self.assertRaises(cleanup.TombstoneLedgerError):
                cleanup.record_delete_tombstone(1, self.ledger)
        write.assert_not_called()
        self.assertEqual(self.ledger.read_bytes(), b"")

    def test_non_regular_ledger_is_rejected_without_waiting_for_a_writer(self):
        self.ledger.unlink()
        os.mkfifo(self.ledger, 0o600)
        with self.assertRaises((OSError, cleanup.TombstoneLedgerError)):
            cleanup.record_delete_tombstone(1, self.ledger)

    def test_validation_write_and_fsync_hold_one_exclusive_lock(self):
        real_validate = cleanup._validate_tombstone_ledger
        real_write, real_fsync = os.write, os.fsync
        stages = []

        def locked(stage):
            stages.append(stage)
            other_fd = os.open(self.ledger, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(other_fd)

        def validate(raw):
            locked("validate")
            real_validate(raw)

        def write(fd, payload):
            locked("write")
            return real_write(fd, payload)

        def fsync(fd):
            locked("fsync")
            real_fsync(fd)

        with patch.object(cleanup, "_validate_tombstone_ledger", side_effect=validate), \
             patch.object(cleanup.os, "write", side_effect=write), \
             patch.object(cleanup.os, "fsync", side_effect=fsync):
            cleanup.record_delete_tombstone(1, self.ledger)
        self.assertEqual(stages, ["validate", "write", "fsync"])
        with self.ledger.open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_concurrent_thread_appends_are_valid_and_complete(self):
        owners = list(range(100, 132))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda owner: cleanup.record_delete_tombstone(owner, self.ledger), owners))
        self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), (set(owners), len(owners)))

    def test_concurrent_process_partial_appends_are_serialized(self):
        script = """
import os, sys, time
from pathlib import Path
import rove_account_delete_cleanup as cleanup
real_write = os.write
def short_write(fd, payload):
    count = real_write(fd, payload[:3])
    time.sleep(0.001)
    return count
cleanup.os.write = short_write
for owner in range(int(sys.argv[2]), int(sys.argv[2]) + 3):
    cleanup.record_delete_tombstone(owner, Path(sys.argv[1]))
"""
        processes = []
        try:
            for owner in range(100, 118, 3):
                processes.append(subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(self.ledger), str(owner)],
                    cwd=Path(__file__).parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                ))
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertEqual(stdout, b"")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), (set(range(100, 118)), 18))


class AccountDeleteLedgerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContractCancellationTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = Path(self.fixture.temp.name) / "ledger.jsonl"
        self.ledger.touch(mode=0o600)
        for guard in (
            patch.dict(os.environ, {"ROVE_ACCOUNT_DELETE_TOMBSTONES": str(self.ledger)}),
            patch.object(api, "account_delete_cleanup_paths", return_value=[]),
            patch.object(cleanup, "generated_report_paths", return_value=([], {})),
            patch.object(api, "remove_deleted_account_files", return_value=[]),
            patch.object(api, "retry_account_delete_file_cleanup"),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        with self.fixture.connection() as conn:
            conn.execute("""INSERT INTO app_account_delete_codes (user_id,code_hash,expires_at)
                VALUES (1,?,'2099-01-01 00:00:00')""", (api.keyed_hash("delete:1:123456"),))

    def delete(self):
        return self.fixture.request("DELETE", "/v1/account",
                                    {"confirmation": "LOESCHEN", "code": "123456"})

    def snapshot(self):
        with closing(sqlite3.connect(self.fixture.path.as_uri() + "?mode=ro", uri=True)) as conn:
            return "\n".join(conn.iterdump())

    def assert_blocked(self):
        before = self.snapshot()
        attempted_deletes = []
        original_db = api.db

        def guard_delete(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_DELETE:
                attempted_deletes.append(table)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        @contextmanager
        def guarded_db(**kwargs):
            with original_db(**kwargs) as conn:
                conn.set_authorizer(guard_delete)
                yield conn

        with patch.object(api, "db", guarded_db), \
             patch.object(cleanup, "queue_paths_in_conn") as queue:
            response = self.delete()
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(response.get_json(), {"ok": False, "error": "delete_protection_unavailable"})
        self.assertEqual(attempted_deletes, [])
        queue.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_malformed_or_truncated_ledger_blocks_delete_without_mutation(self):
        for raw in INVALID_LEDGERS:
            with self.subTest(raw=raw):
                self.ledger.write_bytes(raw)
                self.assert_blocked()
                self.assertEqual(self.ledger.read_bytes(), raw)

    def test_missing_ledger_blocks_delete_without_recreation(self):
        self.ledger.unlink()
        self.assert_blocked()
        self.assertFalse(self.ledger.exists())

    def test_invalid_write_result_blocks_delete_before_any_sql_delete(self):
        for result in (0, -1, None, True, 1.5, "1", 10000):
            with self.subTest(result=result):
                with patch.object(cleanup.os, "write", return_value=result):
                    self.assert_blocked()
                self.assertEqual(self.ledger.read_bytes(), b"")

    def test_write_exception_blocks_delete(self):
        with patch.object(cleanup.os, "write", side_effect=OSError(errno.ENOSPC, "Synthetic full disk")):
            self.assert_blocked()
        self.assertEqual(self.ledger.read_bytes(), b"")

    def test_partial_write_then_zero_blocks_delete_and_next_attempt(self):
        real_write = os.write
        calls = 0

        def stopped(fd, payload):
            nonlocal calls
            calls += 1
            return real_write(fd, payload[:7]) if calls == 1 else 0

        with patch.object(cleanup.os, "write", side_effect=stopped):
            self.assert_blocked()
        incomplete = self.ledger.read_bytes()
        self.assertTrue(incomplete)
        self.assert_blocked()
        self.assertEqual(self.ledger.read_bytes(), incomplete)

    def test_partial_write_then_exception_blocks_delete_and_next_attempt(self):
        real_write = os.write
        calls = 0

        def interrupted(fd, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(fd, payload[:7])
            raise OSError(errno.ENOSPC, "Synthetic full disk")

        with patch.object(cleanup.os, "write", side_effect=interrupted):
            self.assert_blocked()
        incomplete = self.ledger.read_bytes()
        self.assertTrue(incomplete)
        self.assert_blocked()
        self.assertEqual(self.ledger.read_bytes(), incomplete)

    def test_read_or_lock_failure_blocks_delete(self):
        for module, name in ((cleanup.os, "read"), (cleanup.fcntl, "flock")):
            with self.subTest(operation=name):
                with patch.object(module, name, side_effect=OSError(errno.EIO, "Synthetic I/O failure")):
                    self.assert_blocked()
                self.assertEqual(self.ledger.read_bytes(), b"")

    def test_fsync_exception_keeps_db_intact_even_if_intent_is_already_complete(self):
        with patch.object(cleanup.os, "fsync", side_effect=OSError(errno.EIO, "Synthetic fsync failure")):
            self.assert_blocked()
        self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), ({1}, 1))

    def test_path_replacement_during_append_blocks_delete(self):
        real_fsync = os.fsync

        def replace_after_fsync(fd):
            real_fsync(fd)
            self.ledger.rename(self.ledger.with_suffix(".old"))
            self.ledger.touch(mode=0o600)

        with patch.object(cleanup.os, "fsync", side_effect=replace_after_fsync):
            self.assert_blocked()
        self.assertEqual(self.ledger.read_bytes(), b"")

    def test_expired_pin_still_blocks_delete_without_touching_db_or_ledger(self):
        with self.fixture.connection() as conn:
            conn.execute("UPDATE app_session_pins SET last_activity_at='2000-01-01 00:00:00'")
        before = self.snapshot()
        with patch.object(cleanup, "record_delete_tombstone") as append:
            self.assertEqual(self.delete().status_code, 423)
        append.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.ledger.read_bytes(), b"")

    def test_missing_session_and_foreign_origin_still_block_delete(self):
        before = self.snapshot()
        payload = {"confirmation": "LOESCHEN", "code": "123456"}
        with patch.object(cleanup, "record_delete_tombstone") as append:
            self.assertEqual(self.fixture.request("DELETE", "/v1/account", payload, user=0).status_code, 401)
            self.assertEqual(self.fixture.request("DELETE", "/v1/account", payload,
                                                 origin="https://foreign.invalid").status_code, 403)
        append.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.ledger.read_bytes(), b"")

    def test_success_requires_complete_fsynced_intent_before_first_sql_delete(self):
        self.ledger.write_bytes(VALID_RECORD)
        real_write, real_fsync, original_db = os.write, os.fsync, api.db
        durable = False
        attempted_deletes = []

        def short_write(fd, payload):
            return real_write(fd, payload[:7])

        def fsync(fd):
            nonlocal durable
            self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), ({1, 99}, 2))
            real_fsync(fd)
            durable = True

        def guard_delete(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_DELETE:
                attempted_deletes.append(table)
                return sqlite3.SQLITE_OK if durable else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        @contextmanager
        def guarded_db(**kwargs):
            with original_db(**kwargs) as conn:
                conn.set_authorizer(guard_delete)
                yield conn

        with patch.object(cleanup.os, "write", side_effect=short_write), \
             patch.object(cleanup.os, "fsync", side_effect=fsync), \
             patch.object(api, "db", guarded_db):
            response = self.delete()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(durable)
        self.assertIn("app_accounts", attempted_deletes)
        self.assertIn("users", attempted_deletes)
        with self.fixture.connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM app_accounts WHERE user_id=1").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM app_accounts WHERE user_id=2").fetchone())

    def test_empty_ledger_success_and_repeated_delete_are_deterministic(self):
        self.assertEqual(self.delete().status_code, 200)
        original = self.ledger.read_bytes()
        self.assertEqual(recovery.parse_ledger(original), ({1}, 1))
        self.assertEqual(self.delete().status_code, 401)
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_parallel_delete_requests_record_one_intent_only(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(lambda _: self.delete().status_code, range(4)))
        self.assertEqual(statuses.count(200), 1)
        self.assertTrue(all(status in {200, 401, 423} for status in statuses))
        self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), ({1}, 1))

    def test_later_db_failure_rolls_back_but_never_removes_durable_intent(self):
        before = self.snapshot()
        with patch.object(api, "delete_financial_account_data", side_effect=RuntimeError("Synthetic DB failure")):
            with self.assertRaises(RuntimeError):
                self.delete()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(recovery.parse_ledger(self.ledger.read_bytes()), ({1}, 1))


if __name__ == "__main__":
    unittest.main()
