"""Shared, dependency-free cleanup retries for deleted Rov.E accounts."""

from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
from pathlib import Path
from typing import Iterable


def configured_roots(app_dir: Path) -> tuple[Path, Path, Path, Path]:
    reports_dir = Path(os.getenv("CLARITY_REPORTS_DIR", str(app_dir / "reports")))
    return (
        Path(os.getenv("ROVE_APP_STATE_PUBLIC_DIR", str(app_dir / "public" / "app-state"))),
        Path(os.getenv("ROVE_REPORT_PUBLIC_DIR", str(app_dir / "public" / "reports"))),
        reports_dir,
        reports_dir / "archive",
    )


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS account_delete_file_cleanup (
        id INTEGER PRIMARY KEY AUTOINCREMENT, opaque_cleanup_id TEXT NOT NULL UNIQUE,
        internal_path TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, completed_at TEXT
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_delete_cleanup_path ON account_delete_file_cleanup(internal_path)")


def path_allowed(path: Path, roots: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve(strict=False)
        return any(resolved.is_relative_to(root.resolve()) for root in roots)
    except OSError:
        return False


def remove_path(path: Path, roots: Iterable[Path]) -> str | None:
    if not path_allowed(path, roots):
        return "path_not_allowed"
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except OSError as exc:
        return type(exc).__name__
    return None


def queue_paths(db_path: Path, roots: Iterable[Path], paths: Iterable[Path]) -> None:
    allowed_paths = [path for path in paths if path_allowed(path, roots)]
    if not allowed_paths:
        return
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_table(conn)
        for path in allowed_paths:
            conn.execute("""INSERT OR IGNORE INTO account_delete_file_cleanup
                (opaque_cleanup_id, internal_path) VALUES (?, ?)""", (secrets.token_urlsafe(18), str(path)))


def retry_paths(db_path: Path, roots: Iterable[Path], limit: int = 20) -> int:
    completed = 0
    batch_limit = max(1, min(int(limit), 20))
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        ensure_table(conn)
        rows = conn.execute(
            """SELECT id, internal_path FROM account_delete_file_cleanup
                 WHERE completed_at IS NULL ORDER BY id LIMIT ?""",
            (batch_limit,),
        ).fetchall()
        for row in rows:
            error = remove_path(Path(str(row["internal_path"])), roots)
            if error is None:
                conn.execute("UPDATE account_delete_file_cleanup SET completed_at=CURRENT_TIMESTAMP, attempts=attempts+1, last_error=NULL WHERE id=?", (row["id"],))
                completed += 1
            else:
                conn.execute("UPDATE account_delete_file_cleanup SET attempts=attempts+1, last_error=? WHERE id=?", (error, row["id"]))
    return completed
