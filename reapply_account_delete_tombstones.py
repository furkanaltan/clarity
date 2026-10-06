#!/usr/bin/env python3
"""Gate a staged recovery generation before reapplying external account deletions."""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path

import rove_recovery_set as recovery


def check_replay_target(database: Path, approval: recovery.GenerationApproval, policy: Path,
                        recovery_set: Path | None) -> Path:
    database = recovery.absolute_path(database)
    if database.exists():
        info = database.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise recovery.RecoveryError("replay_target_not_private")
    protected = {approval.database_path, approval.ledger_path, policy,
                 Path("/root/clarity/clarity.db"), Path(__file__).resolve().parent / "clarity.db"}
    if database in protected or (recovery_set is not None and database.is_relative_to(recovery_set)):
        raise recovery.RecoveryError("replay_requires_separate_staging_database")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise recovery.RecoveryError("replay_target_not_sealed")
    if recovery.file_metadata(database)["sha256"] != approval.database_sha256:
        raise recovery.RecoveryError("replay_generation_database_mismatch")
    return database


def replay_approved_generation(*, database: Path, policy: Path, recovery_set: Path | None = None,
                               baseline: Path | None = None, ledger: Path | None = None,
                               check_only: bool = False) -> int:
    # Eligibility must pass before importing app code or opening a writable connection.
    approval = recovery.check_account_restore_generation(policy_path=policy, set_path=recovery_set,
                                                         database_path=baseline)
    if ledger is not None and recovery.absolute_path(ledger) != approval.ledger_path:
        raise recovery.RecoveryError("replay_ledger_policy_mismatch")
    database = check_replay_target(database, approval, policy, recovery_set)
    if check_only:
        return 0
    import rove_app_api as api

    if database == api.DB_PATH.resolve():
        raise recovery.RecoveryError("replay_requires_separate_staging_database")
    identity = database.stat().st_dev, database.stat().st_ino
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=30.0)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            check_replay_target(database, approval, policy, recovery_set)
            for user_id in sorted(approval.user_ids):
                api.delete_user_rows_for_tombstone(conn, user_id)
            latest, _ = recovery.stable_bytes(approval.ledger_path)
            if recovery.digest(latest) != approval.ledger_sha256:
                raise recovery.RecoveryError("ledger_changed_during_replay")
            policy_raw, _ = recovery.stable_bytes(approval.policy_path, limit=1024 * 1024)
            if recovery.digest(policy_raw) != approval.policy_sha256:
                raise recovery.RecoveryError("policy_changed_during_replay")
            if identity != (database.stat().st_dev, database.stat().st_ino):
                raise recovery.RecoveryError("replay_database_replaced")
    return len(approval.user_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rov.E Account-Loesch-Tombstones erneut anwenden")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--policy", type=Path)
    generation = parser.add_mutually_exclusive_group()
    generation.add_argument("--recovery-set", type=Path)
    generation.add_argument("--baseline", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.policy is None or (args.recovery_set is None and args.baseline is None):
            raise recovery.RecoveryError("generation_policy_and_source_required")
        count = replay_approved_generation(database=args.db, policy=args.policy,
                                           recovery_set=args.recovery_set, baseline=args.baseline,
                                           ledger=args.ledger, check_only=args.check_only)
    except Exception as exc:
        print("ACCOUNT_RESTORE=BLOCKED", file=sys.stderr)
        print("ERROR_CODE=" + (str(exc) if isinstance(exc, recovery.RecoveryError)
                               else type(exc).__name__), file=sys.stderr)
        return 2
    print("ACCOUNT_RESTORE_GATE=SAFE_FOR_ACCOUNT_RESTORE")
    print("REPLAY=NOT_RUN" if args.check_only else f"reapplied={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
