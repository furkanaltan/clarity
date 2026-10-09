"""Read-only SQLite diagnostics for a failed, isolated recovery-set candidate."""

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path


SET_ID = re.compile(r"recovery_set_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}\Z")
JOB_ID = re.compile(r"job-[0-9A-F]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DATABASE_RELATIVE_PATH = "database/clarity.db"
MANIFEST_LIMIT = 8 * 1024 * 1024
SCHEMA_QUERY = (
    "SELECT type, name, tbl_name, sql FROM sqlite_master "
    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
)


class DiagnosticFailure(Exception):
    def __init__(self, stage, code):
        self.stage = stage
        self.code = code


def _fail(stage, code):
    raise DiagnosticFailure(stage, code)


def _private_directory(path, stage, code):
    try:
        info = path.lstat()
    except OSError:
        _fail(stage, code)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        _fail(stage, code)
    return path.resolve(strict=True)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_private_file(path, *, limit, stage, code):
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600):
            _fail(stage, code)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except DiagnosticFailure:
        raise
    except OSError:
        _fail(stage, code)
    try:
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                _fail(stage, code)
            raw = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
    except OSError:
        _fail(stage, code)
    if len(raw) > limit or _identity(before) != _identity(after):
        _fail(stage, code)
    return raw


def _hash_private_file(path, *, stage, code):
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600):
            _fail(stage, code)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except DiagnosticFailure:
        raise
    except OSError:
        _fail(stage, code)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                _fail(stage, code)
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(handle.fileno())
    except OSError:
        _fail(stage, code)
    if size != before.st_size or _identity(before) != _identity(after):
        _fail(stage, code)
    return size, digest.hexdigest()


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_constant")))


def _schema_hash(objects, user_version):
    value = {"objects": objects, "sqlite_user_version": user_version}
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    return hashlib.sha256(raw).hexdigest()


def diagnose_failed_candidate(set_dir, job_dir, work_root, *, manifest_version, tool_version):
    """Recheck SQLite only after a failed validation; results never authorize a set."""
    try:
        work_real = _private_directory(Path(work_root), "DIAGNOSTIC_SCOPE", "sqlite_diagnostic_scope_invalid")
        job_real = _private_directory(Path(job_dir), "DIAGNOSTIC_SCOPE", "sqlite_diagnostic_scope_invalid")
        set_real = _private_directory(Path(set_dir), "DIAGNOSTIC_SCOPE", "sqlite_diagnostic_scope_invalid")
        if (not JOB_ID.fullmatch(job_real.name) or job_real.parent != work_real
                or set_real.parent.name != "sets" or set_real.parent.parent != job_real
                or not SET_ID.fullmatch(set_real.name)):
            _fail("DIAGNOSTIC_SCOPE", "sqlite_diagnostic_scope_invalid")
        _private_directory(set_real.parent, "DIAGNOSTIC_SCOPE", "sqlite_diagnostic_scope_invalid")

        manifest_path = set_real / "manifest.json"
        manifest_raw = _read_private_file(manifest_path, limit=MANIFEST_LIMIT,
                                          stage="MANIFEST_READ", code="sqlite_manifest_unavailable")
        manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
        try:
            manifest = _strict_json(manifest_raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _fail("MANIFEST_READ", "sqlite_manifest_invalid")
        if (not isinstance(manifest, dict) or manifest.get("status") != "COMPLETE"
                or manifest.get("manifest_version") != manifest_version
                or manifest.get("tool_version") != tool_version
                or manifest.get("recovery_set_id") != set_real.name):
            _fail("MANIFEST_BINDING", "sqlite_manifest_binding_invalid")
        database = manifest.get("database")
        runtime = manifest.get("runtime")
        if (not isinstance(database, dict) or database.get("path") != DATABASE_RELATIVE_PATH
                or database.get("snapshot_method") != "sqlite_backup_api"
                or database.get("live_sidecars_included") is not False
                or database.get("integrity_check") != "ok"
                or database.get("foreign_key_check") != "ok"
                or not isinstance(runtime, dict)
                or not isinstance(runtime.get("schema_sha256"), str)
                or not SHA256.fullmatch(runtime["schema_sha256"])
                or type(runtime.get("sqlite_user_version")) is not int):
            _fail("MANIFEST_BINDING", "sqlite_manifest_binding_invalid")

        database_dir = _private_directory(set_real / "database", "DATABASE_PATH", "sqlite_database_unavailable")
        database_path = database_dir / "clarity.db"
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = database_dir / ("clarity.db" + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _fail("SNAPSHOT_SIDECARS", "sqlite_snapshot_sidecar_present")
        size, database_hash = _hash_private_file(
            database_path, stage="DATABASE_HASH", code="sqlite_database_unavailable")
        if (type(database.get("bytes")) is not int or size != database["bytes"]
                or database_hash != database.get("sha256")):
            _fail("DATABASE_HASH", "sqlite_database_manifest_mismatch")

        try:
            connection = sqlite3.connect(database_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        except sqlite3.Error:
            _fail("SQLITE_OPEN", "sqlite_open_failed")
        stage = "SQLITE_CLOSE"
        try:
            with closing(connection) as conn:
                stage = "INTEGRITY_CHECK"
                try:
                    integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
                except sqlite3.Error:
                    _fail(stage, "sqlite_integrity_check_failed")
                if integrity_rows != [("ok",)]:
                    _fail(stage, "integrity_check_not_ok")

                stage = "FOREIGN_KEY_CHECK"
                try:
                    foreign_key_row = conn.execute("PRAGMA foreign_key_check").fetchone()
                except sqlite3.Error:
                    _fail(stage, "sqlite_foreign_key_check_failed")
                if foreign_key_row is not None:
                    _fail(stage, "foreign_key_violation")

                stage = "SCHEMA_QUERY"
                try:
                    objects = [list(row) for row in conn.execute(SCHEMA_QUERY).fetchall()]
                except sqlite3.Error:
                    _fail(stage, "sqlite_schema_query_failed")
                except (TypeError, ValueError):
                    _fail(stage, "sqlite_schema_result_invalid")

                stage = "USER_VERSION"
                try:
                    version_row = conn.execute("PRAGMA user_version").fetchone()
                except sqlite3.Error:
                    _fail(stage, "sqlite_user_version_query_failed")
                if not version_row or type(version_row[0]) is not int:
                    _fail(stage, "sqlite_user_version_result_invalid")
                user_version = version_row[0]
        except DiagnosticFailure:
            raise
        except sqlite3.Error:
            _fail(stage, "sqlite_close_failed")

        if (_schema_hash(objects, user_version) != runtime["schema_sha256"]
                or user_version != runtime["sqlite_user_version"]):
            _fail("MANIFEST_COMPARE", "sqlite_schema_manifest_mismatch")
        if _hash_private_file(database_path, stage="DATABASE_POSTCHECK",
                              code="sqlite_database_unavailable") != (size, database_hash):
            _fail("DATABASE_POSTCHECK", "sqlite_database_changed_during_diagnosis")
        if hashlib.sha256(_read_private_file(
                manifest_path, limit=MANIFEST_LIMIT, stage="MANIFEST_POSTCHECK",
                code="sqlite_manifest_unavailable")).hexdigest() != manifest_hash:
            _fail("MANIFEST_POSTCHECK", "sqlite_manifest_changed_during_diagnosis")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = database_dir / ("clarity.db" + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _fail("SNAPSHOT_POSTCHECK", "sqlite_snapshot_sidecar_present")
        return {"status": "CHECKS_PASS_AFTER_PRIMARY_FAILURE", "stage": "POST_FAILURE_READONLY_CHECKS",
                "code": "sqlite_checks_passed_after_primary_error", "manifest_trust": "UNVERIFIED"}
    except DiagnosticFailure as exc:
        return {"status": "DIAGNOSTIC_FAILED", "stage": exc.stage,
                "code": exc.code, "manifest_trust": "UNVERIFIED"}
    except BaseException:
        return {"status": "DIAGNOSTIC_UNAVAILABLE", "stage": "DIAGNOSTIC",
                "code": "sqlite_diagnostic_unavailable", "manifest_trust": "UNVERIFIED"}
