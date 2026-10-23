"""Controlled retirement of legacy public Rov.E app-state files.

Run without --apply for an aggregate inventory. The apply path first copies only known
state-link files to a timestamped sibling backup directory, revokes their database rows,
and then removes the public copies. It never follows paths supplied by the database.
"""

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def legacy_state_files(conn: sqlite3.Connection, state_dir: Path) -> list[Path]:
    rows = conn.execute("SELECT token FROM app_state_links").fetchall()
    candidates = []
    for row in rows:
        token = str(row[0] or "")
        if not token or Path(token).name != token:
            continue
        path = state_dir / f"{token}.json"
        if path.is_file() and path.resolve().parent == state_dir.resolve():
            candidates.append(path)
    return candidates


def inventory(db_path: Path, state_dir: Path) -> dict:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        files = legacy_state_files(conn, state_dir)
        active_rows = conn.execute(
            "SELECT COUNT(*) FROM app_state_links WHERE status = 'active'"
        ).fetchone()[0]
    return {"files": len(files), "active_rows": int(active_rows or 0)}


def apply(db_path: Path, state_dir: Path) -> dict:
    backup_dir = state_dir.parent / f"app-state-retired-backup-{datetime.now():%Y%m%d%H%M%S}"
    with sqlite3.connect(db_path) as conn:
        files = legacy_state_files(conn, state_dir)
        backup_dir.mkdir(mode=0o700)
        for path in files:
            shutil.copy2(path, backup_dir / path.name)
        conn.execute("UPDATE app_state_links SET status = 'revoked' WHERE status = 'active'")
        conn.commit()
    removed = 0
    for path in files:
        path.unlink(missing_ok=True)
        removed += 1
    return {"files_removed": removed, "backup": str(backup_dir)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = apply(args.db, args.state_dir) if args.apply else inventory(args.db, args.state_dir)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
