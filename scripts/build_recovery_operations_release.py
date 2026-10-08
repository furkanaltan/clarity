"""Export only committed recovery operations, without touching the app checkout."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


APP_COMMIT = "4e6c29074acb7f08f7908ea34f2d405051b4925b"
TRUST_COMMIT = "5a8cdc4b39110fc92f4fc1612d421e2f6b9df301"
FROZEN = ("rove_recovery_set.py", "backup_clarity_db.py", "reapply_account_delete_tombstones.py",
          "rove_recovery_crypto.py", "rove_recovery_trust.py")
RELEASE_FILES = (*FROZEN, "rove_recovery_operations.py", "rove_recovery_alerts.py", "requirements/recovery.txt",
    "scripts/build_recovery_operations_release.py", "docs/RECOVERY_OPERATIONS.md",
    "deploy/systemd/rove-recovery-offsite.service", "deploy/systemd/rove-recovery-offsite.timer",
    "deploy/systemd/rove-recovery-watch.service", "deploy/systemd/rove-recovery-watch.timer")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL, timeout=30)


def build(repo, commit, output, *, app_commit=APP_COMMIT, trust_commit=TRUST_COMMIT):
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or git(repo, "rev-parse", commit).decode().strip() != commit:
        raise RuntimeError("exact_commit_required")
    output = output.absolute()
    if output.exists() or output.is_symlink() or output == repo or repo in output.parents:
        raise RuntimeError("new_external_release_path_required")
    files = {name: git(repo, "show", f"{commit}:{name}") for name in RELEASE_FILES}
    frozen = {name: hashlib.sha256(git(repo, "show", f"{trust_commit if name == 'rove_recovery_trust.py' else app_commit}:{name}")).hexdigest()
              for name in FROZEN}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    if any(hashes[name] != frozen[name] for name in FROZEN):
        raise RuntimeError("frozen_code_changed")
    output.mkdir(mode=0o700)
    try:
        for relative, raw in files.items():
            path = output / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(raw)
            path.chmod(0o444)
        manifest = {"version": 1, "commit": commit, "app_commit": app_commit, "trust_commit": trust_commit,
                    "files": hashes, "frozen_sha256": frozen}
        with (output / "release.json").open("x") as handle:
            json.dump(manifest, handle, sort_keys=True, indent=2)
            handle.write("\n")
        (output / "release.json").chmod(0o444)
        for directory in sorted((p for p in output.rglob("*") if p.is_dir()), reverse=True):
            directory.chmod(0o555)
        output.chmod(0o555)
    except BaseException:
        # Failed packaging never publishes a runnable release.
        for directory in [output, *(p for p in output.rglob("*") if p.is_dir())]:
            directory.chmod(0o700)
        shutil.rmtree(output)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    if git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise SystemExit("STOP=source_worktree_not_clean")
    build(repo, args.commit, args.output)
    print("OPERATIONS_RELEASE=COMMITTED_EXPORT; FROZEN_CODE=HASH_IDENTICAL")


if __name__ == "__main__":
    main()
