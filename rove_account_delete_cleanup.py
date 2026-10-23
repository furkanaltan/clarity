"""Shared, dependency-free cleanup retries for deleted Rov.E accounts."""

from __future__ import annotations

import os
import json
import hashlib
import fcntl
import re
import secrets
import shutil
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


class TombstoneLedgerError(RuntimeError):
    """The deletion ledger cannot be trusted for deletion or restore."""


PREVIEW_OWNER_RE = re.compile(r"<!-- rove-preview-owner:(\d+) -->")
GENERATED_REPORT_RE_TEMPLATE = r"clarity_report_{user_id}_\d{{4}}-(?:0[1-9]|1[0-2])\.html"
GENERATED_REPORT_OWNER_RE = re.compile(r"clarity_report_(\d+)_\d{4}-(?:0[1-9]|1[0-2])\.html")
AI_USAGE_RETENTION_DAYS = 30
CASH_RECEIPT_RETENTION_DAYS = 30


def configured_roots(app_dir: Path) -> tuple[Path, ...]:
    reports_dir = Path(os.getenv("CLARITY_REPORTS_DIR", str(app_dir / "reports")))
    return (
        Path(os.getenv("ROVE_APP_STATE_PUBLIC_DIR", str(app_dir / "public" / "app-state"))),
        Path(os.getenv("ROVE_REPORT_PUBLIC_DIR", "/var/www/reports")),
        reports_dir,
        reports_dir / "archive",
        app_dir / "report_html" / "report-main" / "generated",
    )


def generated_report_paths(
    generated_dir: Path, user_id: int
) -> tuple[list[Path], dict[Path, tuple[int, str | None]]]:
    """Return only validated per-user report HTML and a provably owned preview."""
    if user_id <= 0:
        return [], {}
    try:
        root = generated_dir.resolve()
        if not root.is_dir():
            return [], {}
        user_pattern = re.compile(GENERATED_REPORT_RE_TEMPLATE.format(user_id=int(user_id)))
        owned = [
            path for path in root.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.resolve().parent == root
            and user_pattern.fullmatch(path.name)
        ]
        preview = root / "latest_preview.html"
        if not preview.is_file() or preview.is_symlink() or preview.resolve().parent != root:
            return owned, {}

        marker_owner = preview_owner(preview)
        if marker_owner == int(user_id):
            owned.append(preview)
            return owned, {preview: (int(user_id), None)}
        if marker_owner is not None:
            return owned, {}

        try:
            preview_bytes = preview.read_bytes()
        except OSError:
            return owned, {}
        matching_owners: set[int] = set()
        for path in root.iterdir():
            report_match = GENERATED_REPORT_OWNER_RE.fullmatch(path.name)
            if (
                not report_match
                or not path.is_file()
                or path.is_symlink()
                or path.resolve().parent != root
            ):
                continue
            try:
                if path.read_bytes() == preview_bytes:
                    matching_owners.add(int(report_match.group(1)))
            except OSError:
                continue
        if matching_owners == {int(user_id)}:
            owned.append(preview)
            expected_hash = hashlib.sha256(preview_bytes).hexdigest()
            return owned, {preview: (int(user_id), expected_hash)}
        return owned, {}
    except OSError:
        return [], {}


def preview_owner(path: Path) -> int | None:
    try:
        match = PREVIEW_OWNER_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def tombstone_path(app_dir: Path | None = None) -> Path:
    """Return the deletion ledger path, deliberately outside the SQLite backup."""
    default_dir = app_dir or Path(__file__).resolve().parent.parent
    return Path(os.getenv("ROVE_ACCOUNT_DELETE_TOMBSTONES", str(default_dir / "account_delete_tombstones.jsonl")))


def _validate_tombstone_ledger(raw: bytes) -> None:
    # An existing empty file is structurally valid, not proof of complete history.
    if raw and not raw.endswith(b"\n"):
        raise TombstoneLedgerError("ledger_incomplete_record")

    def unique_fields(pairs):
        row = {}
        for key, value in pairs:
            if key in row:
                raise ValueError("duplicate_field")
            row[key] = value
        return row

    now = datetime.now(timezone.utc)
    for line_number, line in enumerate(raw.split(b"\n")[:-1], start=1):
        try:
            row = json.loads(line.decode("utf-8"), object_pairs_hook=unique_fields)
            if not isinstance(row, dict) or set(row) != {"user_id", "deleted_at"}:
                raise ValueError("invalid_fields")
            owner = row["user_id"]
            timestamp = datetime.fromisoformat(row["deleted_at"])
            if type(owner) is not int or not 0 < owner <= 9223372036854775807:
                raise ValueError("invalid_user_id")
            if timestamp.utcoffset() != timedelta(0) or timestamp > now:
                raise ValueError("invalid_timestamp")
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise TombstoneLedgerError(f"ledger_invalid_line:{line_number}") from exc


def _check_tombstone_file(fd: int, target: Path) -> os.stat_result:
    opened = os.fstat(fd)
    named = target.lstat()
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise TombstoneLedgerError("ledger_file_changed_or_invalid")
    return opened


def record_delete_tombstone(user_id: int, path: Path | None = None) -> None:
    """Validate and durably append intent before the caller deletes account rows.

    Require an existing ledger: silently creating a missing one could hide lost
    history. All appenders must hold this inode's lock; never replace the ledger.
    """
    if type(user_id) is not int or not 0 < user_id <= 9223372036854775807:
        raise TombstoneLedgerError("ledger_invalid_user_id")
    target = path or tombstone_path()
    line = json.dumps({"user_id": user_id, "deleted_at": datetime.now(timezone.utc).isoformat()}) + "\n"
    payload = line.encode("utf-8")
    fd = os.open(target, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        original = _check_tombstone_file(fd, target)
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        raw = b"".join(chunks)
        if len(raw) != original.st_size:
            raise TombstoneLedgerError("ledger_changed_during_read")
        _validate_tombstone_ledger(raw)

        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if type(written) is not int or not 0 < written <= len(payload) - offset:
                raise TombstoneLedgerError("ledger_incomplete_write")
            offset += written
        os.fsync(fd)
        if _check_tombstone_file(fd, target).st_size != len(raw) + len(payload):
            raise TombstoneLedgerError("ledger_changed_during_append")
    finally:
        os.close(fd)


def read_delete_tombstones(path: Path | None = None) -> set[int]:
    target = path or tombstone_path()
    if not target.is_file():
        raise TombstoneLedgerError("ledger_missing")
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TombstoneLedgerError("ledger_unreadable") from exc
    result: set[int] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            value = payload.get("user_id") if isinstance(payload, dict) else None
            if value is None or isinstance(value, bool):
                raise ValueError("missing_user_id")
            user_id = int(value)
            if user_id <= 0:
                raise ValueError("invalid_user_id")
            result.add(user_id)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TombstoneLedgerError(f"ledger_invalid_line:{line_number}") from exc
    return result


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS account_delete_file_cleanup (
        id INTEGER PRIMARY KEY AUTOINCREMENT, opaque_cleanup_id TEXT NOT NULL UNIQUE,
        internal_path TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, completed_at TEXT
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_delete_cleanup_path ON account_delete_file_cleanup(internal_path)")
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(account_delete_file_cleanup)")}
    if "created_at" not in columns:
        conn.execute("ALTER TABLE account_delete_file_cleanup ADD COLUMN created_at TEXT")
    if "attempts" not in columns:
        conn.execute("ALTER TABLE account_delete_file_cleanup ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if "last_error" not in columns:
        conn.execute("ALTER TABLE account_delete_file_cleanup ADD COLUMN last_error TEXT")
    if "expected_owner_user_id" not in columns:
        conn.execute("ALTER TABLE account_delete_file_cleanup ADD COLUMN expected_owner_user_id INTEGER")
    if "expected_sha256" not in columns:
        conn.execute("ALTER TABLE account_delete_file_cleanup ADD COLUMN expected_sha256 TEXT")


def path_allowed(path: Path, roots: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve(strict=False)
        return any(resolved.is_relative_to(root.resolve()) for root in roots)
    except OSError:
        return False


def remove_path(
    path: Path,
    roots: Iterable[Path],
    *,
    expected_owner_user_id: int | None = None,
    expected_sha256: str | None = None,
) -> str | None:
    if not path_allowed(path, roots):
        return "path_not_allowed"
    if not path.exists():
        return None
    if expected_owner_user_id is not None:
        if path.name != "latest_preview.html" or path.parent.name != "generated":
            return "conditional_owner_not_supported"
        owner = preview_owner(path)
        if owner is not None:
            if owner != int(expected_owner_user_id):
                return None
        elif expected_sha256:
            if _sha256_file(path) != expected_sha256:
                return None
        else:
            return "preview_owner_unverifiable"
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except OSError as exc:
        return type(exc).__name__
    return None


def queue_paths_in_conn(
    conn: sqlite3.Connection,
    roots: Iterable[Path],
    paths: Iterable[Path],
    *,
    conditional_owner_by_path: dict[Path, tuple[int, str | None]] | None = None,
) -> None:
    """Persist allowlisted cleanup paths inside the caller's active transaction."""
    allowed_paths = [path for path in paths if path_allowed(path, roots)]
    if not allowed_paths:
        return
    ensure_table(conn)
    for path in allowed_paths:
        expected_owner = None
        expected_sha256 = None
        if conditional_owner_by_path and path in conditional_owner_by_path:
            expected_owner, expected_sha256 = conditional_owner_by_path[path]
            expected_owner = int(expected_owner)
            if preview_owner(path) is None and not expected_sha256:
                continue
        conn.execute(
            """INSERT INTO account_delete_file_cleanup
               (opaque_cleanup_id, internal_path, expected_owner_user_id, expected_sha256)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(internal_path) DO UPDATE SET
                   expected_owner_user_id=excluded.expected_owner_user_id,
                   expected_sha256=excluded.expected_sha256,
                   attempts=0, last_error=NULL, completed_at=NULL""",
            (secrets.token_urlsafe(18), str(path), expected_owner, expected_sha256),
        )


def queue_paths(db_path: Path, roots: Iterable[Path], paths: Iterable[Path]) -> None:
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        queue_paths_in_conn(conn, roots, paths)


def retry_paths(db_path: Path, roots: Iterable[Path], limit: int = 20) -> int:
    completed = 0
    batch_limit = max(1, min(int(limit), 20))
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        ensure_table(conn)
        rows = conn.execute(
            """SELECT id, internal_path, expected_owner_user_id, expected_sha256
                 FROM account_delete_file_cleanup
                 WHERE completed_at IS NULL ORDER BY id LIMIT ?""",
            (batch_limit,),
        ).fetchall()
        for row in rows:
            error = remove_path(
                Path(str(row["internal_path"])),
                roots,
                expected_owner_user_id=row["expected_owner_user_id"],
                expected_sha256=row["expected_sha256"],
            )
            if error is None:
                conn.execute("UPDATE account_delete_file_cleanup SET completed_at=CURRENT_TIMESTAMP, attempts=attempts+1, last_error=NULL WHERE id=?", (row["id"],))
                completed += 1
            else:
                conn.execute("UPDATE account_delete_file_cleanup SET attempts=attempts+1, last_error=? WHERE id=?", (error, row["id"]))
    return completed


def cleanup_ai_chat_data(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, int]:
    """Apply the existing chat and aggregate-metric retention rules idempotently."""
    def exists(table: str) -> bool:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone())

    cutoff = now.strftime("%Y-%m-%d %H:%M:%S") if now else None
    conversation_count = message_count = usage_count = 0
    if exists("app_ai_conversations"):
        expired = conn.execute(
            "SELECT conversation_id FROM app_ai_conversations WHERE "
            + ("datetime(expires_at) < datetime(?)" if cutoff else "datetime(expires_at) < datetime('now', 'localtime')"),
            (cutoff,) if cutoff else (),
        ).fetchall()
        for row in expired:
            cursor = conn.execute(
                "DELETE FROM app_ai_conversation_messages WHERE conversation_id = ?",
                (row[0],),
            ) if exists("app_ai_conversation_messages") else None
            message_count += int(cursor.rowcount or 0) if cursor else 0
            conversation_count += int(conn.execute(
                "DELETE FROM app_ai_conversations WHERE conversation_id = ?", (row[0],)
            ).rowcount or 0)
    if exists("app_ai_usage"):
        usage_cutoff = (
            now - timedelta(days=AI_USAGE_RETENTION_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S") if now else None
        cursor = conn.execute(
            "DELETE FROM app_ai_usage WHERE "
            + ("datetime(created_at) < datetime(?)" if usage_cutoff else "datetime(created_at) < datetime('now', 'localtime', '-30 days')"),
            (usage_cutoff,) if usage_cutoff else (),
        )
        usage_count = int(cursor.rowcount or 0)
    return {"conversations": conversation_count, "messages": message_count, "usage": usage_count}


def ensure_cash_request_receipts_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS app_cash_request_receipts (
        user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        request_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        payload TEXT NOT NULL,
        response TEXT,
        created_at TEXT,
        expired_at TEXT,
        PRIMARY KEY(user_id, request_id)
    )""")
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(app_cash_request_receipts)")}
    if "created_at" not in columns:
        conn.execute("ALTER TABLE app_cash_request_receipts ADD COLUMN created_at TEXT")
    if "expired_at" not in columns:
        conn.execute("ALTER TABLE app_cash_request_receipts ADD COLUMN expired_at TEXT")
    # Existing rows had no trustworthy creation timestamp. Start their retention clock
    # at this migration rather than guessing an earlier date.
    conn.execute(
        "UPDATE app_cash_request_receipts SET created_at=CURRENT_TIMESTAMP "
        "WHERE created_at IS NULL OR TRIM(created_at) = ''"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cash_receipts_retention "
        "ON app_cash_request_receipts(expired_at, created_at)"
    )


def cleanup_cash_request_receipts(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_cash_request_receipts'"
    ).fetchone()
    if not exists:
        return 0
    ensure_cash_request_receipts_schema(conn)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    cutoff = (now - timedelta(days=CASH_RECEIPT_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    expired_at = now.strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """UPDATE app_cash_request_receipts
              SET operation='', payload='', response=NULL, expired_at=?
            WHERE expired_at IS NULL AND datetime(created_at) < datetime(?)""",
        (expired_at, cutoff),
    )
    return int(cursor.rowcount or 0)


def queue_expired_legacy_state_files(
    conn: sqlite3.Connection,
    roots: Iterable[Path],
    retention_grace_days: int = 30,
) -> int:
    """Queue exact token-owned JSON files before expiring their database mapping."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_state_links'"
    ).fetchone()
    if not exists:
        return 0
    roots = tuple(roots)
    state_root = roots[0] if roots else None
    if state_root is None:
        return 0
    cutoff = f"-{max(0, int(retention_grace_days))} days"
    rows = conn.execute(
        """SELECT token FROM app_state_links
             WHERE datetime(COALESCE(expires_at, created_at))
                   < datetime('now', 'localtime', ?)""",
        (cutoff,),
    ).fetchall()
    tokens = [str(row[0]) for row in rows if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(row[0] or ""))]
    paths = [state_root / f"{token}.json" for token in tokens]
    queue_paths_in_conn(conn, roots, paths)
    return len(paths)
