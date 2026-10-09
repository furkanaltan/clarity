"""Synthetic read-only tests for failed-candidate SQLite diagnostics."""

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rove_recovery_sqlite_diagnostics as diagnostics


SET_ID = "recovery_set_20261009T000000Z_" + "a" * 32
JOB_ID = "job-" + "B" * 32
SCHEMA_QUERY = diagnostics.SCHEMA_QUERY


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class ReadOnlyProxy:
    def __init__(self, connection, failing_sql=None, *, integrity=None, foreign=None,
                 schema=None, user_version=7):
        self.connection = connection
        self.failing_sql = failing_sql
        self.integrity = integrity if integrity is not None else [("ok",)]
        self.foreign = foreign if foreign is not None else []
        self.schema = schema if schema is not None else []
        self.user_version = user_version

    def execute(self, sql):
        if sql == self.failing_sql:
            raise sqlite3.OperationalError("synthetic-secret-detail")
        if sql == "PRAGMA integrity_check":
            return Cursor(self.integrity)
        if sql == "PRAGMA foreign_key_check":
            return Cursor(self.foreign)
        if sql == SCHEMA_QUERY:
            return Cursor(self.schema)
        if sql == "PRAGMA user_version":
            return Cursor([(self.user_version,)])
        raise AssertionError("unexpected_sql")

    def close(self):
        self.connection.close()


class FailedCandidateSQLiteDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rove-failed-sqlite-", dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.job = self.work / JOB_ID
        self.job.mkdir(mode=0o700)
        self.sets = self.job / "sets"
        self.sets.mkdir(mode=0o700)
        self.set_dir = self.sets / SET_ID
        self.set_dir.mkdir(mode=0o700)
        self.db_dir = self.set_dir / "database"
        self.db_dir.mkdir(mode=0o700)
        self.db = self.db_dir / "clarity.db"
        self._create_db()
        self.schema_rows, self.user_version = self._schema()
        self._write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def _create_db(self, *, orphan=False):
        with sqlite3.connect(self.db) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
            conn.execute("INSERT INTO parent(id) VALUES (1)")
            conn.execute("INSERT INTO child(parent_id) VALUES (?)", (999 if orphan else 1,))
            conn.execute("PRAGMA user_version=7")
        self.db.chmod(0o600)

    def _schema(self):
        with sqlite3.connect(self.db.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            objects = [list(row) for row in conn.execute(SCHEMA_QUERY).fetchall()]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        return objects, version

    def _manifest(self):
        size = self.db.stat().st_size
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        return {
            "manifest_version": 4,
            "tool_version": "1.3",
            "recovery_set_id": SET_ID,
            "status": "COMPLETE",
            "database": {
                "path": "database/clarity.db",
                "bytes": size,
                "sha256": digest,
                "integrity_check": "ok",
                "foreign_key_check": "ok",
                "snapshot_method": "sqlite_backup_api",
                "live_sidecars_included": False,
            },
            "runtime": {
                "schema_sha256": diagnostics._schema_hash(self.schema_rows, self.user_version),
                "sqlite_user_version": self.user_version,
            },
        }

    def _write_manifest(self, manifest=None):
        path = self.set_dir / "manifest.json"
        path.write_text(json.dumps(manifest or self._manifest(), sort_keys=True, indent=2) + "\n")
        path.chmod(0o600)

    def _diagnose(self):
        return diagnostics.diagnose_failed_candidate(
            self.set_dir, self.job, self.work, manifest_version=4, tool_version="1.3")

    def _tree_state(self):
        state = []
        for path in sorted(self.set_dir.rglob("*")):
            info = path.lstat()
            data_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            state.append((str(path.relative_to(self.set_dir)), info.st_mode, info.st_size,
                          info.st_mtime_ns, info.st_ctime_ns, data_hash))
        return state

    def test_pass_is_read_only_and_never_trusted_as_receipt(self):
        before = self._tree_state()
        result = self._diagnose()
        self.assertEqual(result, {
            "status": "CHECKS_PASS_AFTER_PRIMARY_FAILURE",
            "stage": "POST_FAILURE_READONLY_CHECKS",
            "code": "sqlite_checks_passed_after_primary_error",
            "manifest_trust": "UNVERIFIED",
        })
        self.assertEqual(before, self._tree_state())
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse((self.db_dir / ("clarity.db" + suffix)).exists())

    def test_manifest_database_hash_mismatch_is_rejected(self):
        self.db.write_bytes(self.db.read_bytes() + b"changed")
        result = self._diagnose()
        self.assertEqual((result["stage"], result["code"]),
                         ("DATABASE_HASH", "sqlite_database_manifest_mismatch"))

    def test_malformed_sqlite_is_rejected_at_integrity_check(self):
        self.db.write_bytes(b"not a sqlite database")
        self.db.chmod(0o600)
        self._write_manifest()
        result = self._diagnose()
        self.assertEqual((result["stage"], result["code"]),
                         ("INTEGRITY_CHECK", "sqlite_integrity_check_failed"))

    def test_each_sqlite_operation_has_a_fixed_safe_error_code(self):
        expected = {
            None: ("SQLITE_OPEN", "sqlite_open_failed"),
            "PRAGMA integrity_check": ("INTEGRITY_CHECK", "sqlite_integrity_check_failed"),
            "PRAGMA foreign_key_check": ("FOREIGN_KEY_CHECK", "sqlite_foreign_key_check_failed"),
            SCHEMA_QUERY: ("SCHEMA_QUERY", "sqlite_schema_query_failed"),
            "PRAGMA user_version": ("USER_VERSION", "sqlite_user_version_query_failed"),
        }
        original_connect = sqlite3.connect
        for failing_sql, failure in expected.items():
            with self.subTest(stage=failure[0]):
                if failing_sql is None:
                    connect = lambda *args, **kwargs: (_ for _ in ()).throw(
                        sqlite3.OperationalError("synthetic-secret-detail"))
                else:
                    def connect(*args, _target=failing_sql, **kwargs):
                        raw = original_connect(*args, **kwargs)
                        return ReadOnlyProxy(raw, _target, schema=self.schema_rows,
                                             user_version=self.user_version)
                with patch.object(diagnostics.sqlite3, "connect", side_effect=connect):
                    result = self._diagnose()
                self.assertEqual((result["stage"], result["code"]), failure)
                self.assertNotIn("synthetic-secret", str(result))

    def test_non_ok_integrity_and_foreign_keys_fail_closed_without_row_details(self):
        original_connect = sqlite3.connect
        cases = (
            ("PRAGMA integrity_check", {"integrity": [("private page detail",)]},
             ("INTEGRITY_CHECK", "integrity_check_not_ok")),
            ("PRAGMA foreign_key_check", {"foreign": [("private_table", 9, "private_parent", 0)]},
             ("FOREIGN_KEY_CHECK", "foreign_key_violation")),
        )
        for sql, options, expected in cases:
            with self.subTest(expected=expected):
                def connect(*args, **kwargs):
                    raw = original_connect(*args, **kwargs)
                    return ReadOnlyProxy(raw, schema=self.schema_rows,
                                         user_version=self.user_version, **options)
                with patch.object(diagnostics.sqlite3, "connect", side_effect=connect):
                    result = self._diagnose()
                self.assertEqual((result["stage"], result["code"]), expected)
                self.assertNotIn("private_", str(result))

    def test_schema_mismatch_and_sidecars_are_rejected(self):
        manifest = self._manifest()
        manifest["runtime"]["schema_sha256"] = "0" * 64
        self._write_manifest(manifest)
        result = self._diagnose()
        self.assertEqual((result["stage"], result["code"]),
                         ("MANIFEST_COMPARE", "sqlite_schema_manifest_mismatch"))
        (self.db_dir / "clarity.db-shm").write_bytes(b"synthetic")
        result = self._diagnose()
        self.assertEqual((result["stage"], result["code"]),
                         ("SNAPSHOT_SIDECARS", "sqlite_snapshot_sidecar_present"))

    def test_scope_escape_is_rejected(self):
        result = diagnostics.diagnose_failed_candidate(
            self.set_dir, self.root, self.root, manifest_version=4, tool_version="1.3")
        self.assertEqual((result["stage"], result["code"]),
                         ("DIAGNOSTIC_SCOPE", "sqlite_diagnostic_scope_invalid"))


if __name__ == "__main__":
    unittest.main()
