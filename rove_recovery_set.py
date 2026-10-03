"""Build private, version-bound recovery sets; never restore or contact transports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import uuid
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from backup_clarity_db import BackupValidationError, create_verified_backup, verify_database


TOOL_VERSION = "1.2"
MANIFEST_VERSION = 3
GENERATION_GATE_VERSION = "1"
SAFE_FOR_ACCOUNT_RESTORE = "SAFE_FOR_ACCOUNT_RESTORE"
UNSAFE_FOR_ACCOUNT_RESTORE = "UNSAFE_FOR_ACCOUNT_RESTORE"
UNKNOWN = "UNKNOWN"
HASH = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
PDF = re.compile(r"rove_report_([1-9][0-9]*)_[0-9]{4}-(?:0[1-9]|1[0-2])\.pdf(?:\.gz)?\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
REFERENCE = re.compile(r"(?:escrow|vault|offline):[A-Za-z0-9_.:/-]{1,160}\Z")
CONFIG_ROLES = {"api_service", "api_dropin", "nginx_site", "nginx_headers"}
REQUIRED_CONFIG_ROLES = CONFIG_ROLES - {"api_dropin"}


class RecoveryError(RuntimeError):
    """A safe error code, never a SQL row, credential, or exception payload."""


@dataclass(frozen=True)
class GenerationApproval:
    """In-process proof bound to the exact source and the validated latest ledger."""

    database_path: Path
    database_sha256: str
    ledger_path: Path
    ledger_sha256: str
    policy_path: Path
    policy_sha256: str
    user_ids: frozenset[int] = field(repr=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(raw: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RecoveryError("duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise RecoveryError("invalid_json") from exc


def fields(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise RecoveryError("inventory_fields_invalid")
    return value


def absolute_path(value: object) -> Path:
    if not isinstance(value, (str, Path)):
        raise RecoveryError("path_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RecoveryError("absolute_path_required")
    # Do not resolve away a symlink before deciding whether it is allowed.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RecoveryError("symlink_not_allowed")
    return path


def configuration_path(value: object) -> Path:
    """Resolve configuration links only; DB/ledger/artifact links remain forbidden."""
    if not isinstance(value, str):
        raise RecoveryError("config_path_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RecoveryError("config_path_invalid")
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RecoveryError("config_file_unavailable") from exc
    return absolute_path(target)


def systemd_api_configuration() -> dict:
    """Read effective paths, not Environment values or command arguments."""
    try:
        output = subprocess.check_output(
            ["systemctl", "show", "--no-pager", "--property=LoadState",
             "--property=FragmentPath", "--property=DropInPaths", "--property=EnvironmentFiles",
             "rove-app-api.service"], stderr=subprocess.DEVNULL, timeout=10,
        ).decode("utf-8")
        values = {}
        environments = []
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError
            if key == "EnvironmentFiles":
                matches = re.findall(r"(\S+) \(ignore_errors=(yes|no)\)", value)
                if value and " ".join(f"{p} (ignore_errors={flag})" for p, flag in matches) != value:
                    raise ValueError
                environments.extend(matches)
            elif key in {"LoadState", "FragmentPath", "DropInPaths"} and key not in values:
                values[key] = value
            else:
                raise ValueError
        if values.get("LoadState") != "loaded" or not values.get("FragmentPath"):
            raise ValueError
        dropins = shlex.split(values["DropInPaths"])
        paths = [values["FragmentPath"], *dropins, *(p for p, _ in environments)]
        if any(not Path(p).is_absolute() or ".." in Path(p).parts or "\\" in p for p in paths):
            raise ValueError
        present, absent = [], []
        for path, optional in environments:
            if Path(path).exists():
                present.append(path)
            elif optional == "yes" and not Path(path).is_symlink():
                absent.append(path)
            else:
                raise RecoveryError("effective_environment_file_missing")
        if (len(dropins) != len(set(dropins))
                or len(environments) != len({path for path, _ in environments})):
            raise ValueError
        return {"fragment_path": values["FragmentPath"], "drop_in_paths": sorted(dropins),
                "environment_files": sorted(present), "absent_optional_environment_files": sorted(absent)}
    except RecoveryError:
        raise
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError) as exc:
        raise RecoveryError("effective_systemd_inventory_unavailable") from exc


def credential_names(raw: bytes, *, unit: bool = False) -> set[str]:
    names = set()
    text = raw.decode("utf-8")
    if unit:
        text = re.sub(r"\\\r?\n", " ", text)
    for line in text.splitlines():
        if unit:
            assignment = re.fullmatch(r"\s*Environment\s*=\s*(.*)", line)
            if assignment is None:
                continue
            try:
                entries = shlex.split(assignment[1])
            except ValueError as exc:
                raise RecoveryError("unit_environment_invalid") from exc
        else:
            entries = [line.strip().removeprefix("export ")]
        for entry in entries:
            name, separator, value = entry.partition("=")
            if (separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
                    and re.search(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE", name)
                    and not name.endswith("KEY_VERSION") and "PUBLIC_KEY" not in name
                    and value.strip() not in {"", "''", '""'}):
                names.add(name)
    return names


def stable_bytes(path: Path, *, limit: int = 64 * 1024 * 1024) -> tuple[bytes, dict]:
    path = absolute_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise RecoveryError("source_file_invalid")
        raw = source.read(limit + 1)
        after = os.fstat(source.fileno())
    current = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(current) or len(raw) != after.st_size:
        raise RecoveryError("source_changed_during_capture")
    return raw, {
        "source_path": str(path), "source_mode": f"{stat.S_IMODE(before.st_mode):04o}",
        "source_uid": before.st_uid, "source_gid": before.st_gid,
        "bytes": len(raw), "sha256": digest(raw),
    }


def file_metadata(path: Path, *, target: Path | None = None) -> dict:
    """Hash/copy large snapshots and artifacts without buffering them in memory."""
    path = absolute_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    hasher = hashlib.sha256()
    size = 0
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RecoveryError("source_file_invalid")
        if target is not None:
            private_directory(target.parent)
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            destination = os.fdopen(target_fd, "wb")
        else:
            destination = nullcontext()
        with destination as dest:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
                size += len(chunk)
                if dest is not None:
                    dest.write(chunk)
            if dest is not None:
                dest.flush()
                os.fsync(dest.fileno())
        after = os.fstat(source.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(path.stat()) or size != after.st_size:
        raise RecoveryError("source_changed_during_capture")
    return {"source_path": str(path), "source_mode": f"{stat.S_IMODE(before.st_mode):04o}",
            "source_uid": before.st_uid, "source_gid": before.st_gid, "bytes": size, "sha256": hasher.hexdigest()}


def private_write(path: Path, data: bytes) -> None:
    private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def private_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RecoveryError("set_directory_not_private")
        return
    private_directory(path.parent)
    path.mkdir(mode=0o700)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def parse_ledger(raw: bytes) -> tuple[set[int], int]:
    if raw and not raw.endswith(b"\n"):
        raise RecoveryError("ledger_incomplete_record")
    owners = set()
    count = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = fields(strict_json(line), {"user_id", "deleted_at"})
            owner = row["user_id"]
            timestamp = datetime.fromisoformat(row["deleted_at"])
            if type(owner) is not int or not 0 < owner <= 9223372036854775807 or timestamp.utcoffset() is None:
                raise ValueError
            if timestamp.utcoffset().total_seconds() != 0 or timestamp > datetime.now(timezone.utc):
                raise ValueError
        except (RecoveryError, ValueError, TypeError, AttributeError) as exc:
            raise RecoveryError("ledger_invalid") from exc
        owners.add(owner)
        count += 1
    return owners, count


def check_anchor(raw: bytes, anchor: dict) -> None:
    fields(anchor, {"bytes", "sha256"})
    length = anchor["bytes"]
    if type(length) is not int or length < 0 or length > len(raw) or not HASH.fullmatch(str(anchor["sha256"])):
        raise RecoveryError("ledger_anchor_invalid")
    if length and raw[length - 1:length] != b"\n":
        raise RecoveryError("ledger_anchor_invalid")
    if digest(raw[:length]) != anchor["sha256"]:
        raise RecoveryError("ledger_history_changed")


def utc_timestamp(value: object) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError
        return timestamp
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecoveryError("recovery_timestamp_invalid") from exc


def ledger_coverage(declared: object) -> dict:
    """An explicit boundary is not a claim that an empty historical ledger is complete."""
    if not isinstance(declared, dict):
        raise RecoveryError("ledger_coverage_invalid")
    expected = {"path", "history_confirmed_complete", "audit_reference", "anchor"}
    if "recovery_boundary" in declared:
        expected.add("recovery_boundary")
    fields(declared, expected)
    absolute_path(declared["path"])
    if not REFERENCE.fullmatch(str(declared["audit_reference"])):
        raise RecoveryError("ledger_audit_reference_invalid")
    anchor = fields(declared["anchor"], {"bytes", "sha256"})
    if type(anchor["bytes"]) is not int or anchor["bytes"] < 0 or not HASH.fullmatch(str(anchor["sha256"])):
        raise RecoveryError("ledger_anchor_invalid")
    if declared["history_confirmed_complete"] is True and "recovery_boundary" not in declared:
        if anchor["bytes"] == 0:
            raise RecoveryError("empty_ledger_history_not_proven")
        return {"mode": "historical", "cutoff_at_utc": None, "baseline_database_sha256": None,
                "evidence_ref": declared["audit_reference"]}
    if declared["history_confirmed_complete"] is not False or "recovery_boundary" not in declared:
        raise RecoveryError("ledger_history_not_attested")
    boundary = fields(declared["recovery_boundary"],
                      {"cutoff_at_utc", "baseline_database_sha256", "evidence_ref", "forward_coverage_confirmed"})
    cutoff = utc_timestamp(boundary["cutoff_at_utc"])
    if (cutoff > datetime.now(timezone.utc)
            or not HASH.fullmatch(str(boundary["baseline_database_sha256"]))
            or not REFERENCE.fullmatch(str(boundary["evidence_ref"]))
            or boundary["forward_coverage_confirmed"] is not True):
        raise RecoveryError("recovery_boundary_unverified")
    return {"mode": "cutoff", "cutoff_at_utc": cutoff.isoformat(),
            "baseline_database_sha256": boundary["baseline_database_sha256"],
            "evidence_ref": boundary["evidence_ref"]}


def check_generation_boundary(manifest: dict, coverage: dict) -> None:
    if manifest.get("account_restore_coverage") != coverage:
        raise RecoveryError("generation_policy_mismatch")
    started = utc_timestamp(manifest.get("created_at_utc"))
    completed = utc_timestamp(manifest.get("snapshot_completed_at_utc"))
    if completed < started or completed > datetime.now(timezone.utc):
        raise RecoveryError("generation_timestamp_invalid")
    if coverage["mode"] == "cutoff" and started < utc_timestamp(coverage["cutoff_at_utc"]):
        raise RecoveryError("generation_before_recovery_cutoff")


def generation_manifest_safety(manifest: dict) -> dict:
    """Bind eligibility metadata to the verified generation, not just DB integrity."""
    coverage = manifest["account_restore_coverage"]
    check_generation_boundary(manifest, coverage)
    if not SHA.fullmatch(str(manifest["software"]["git_sha"])):
        raise RecoveryError("generation_git_sha_invalid")
    return {
        "status": SAFE_FOR_ACCOUNT_RESTORE,
        "reason": ("historical_coverage_attested" if coverage["mode"] == "historical"
                   else "after_verified_recovery_cutoff"),
        "gate_version": GENERATION_GATE_VERSION,
        "recovery_set_id": manifest["recovery_set_id"],
        "created_at_utc": manifest["created_at_utc"],
        "snapshot_completed_at_utc": manifest["snapshot_completed_at_utc"],
        "ledger_anchor": manifest["ledger"]["anchor"],
        "coverage": coverage,
        "git_sha": manifest["software"]["git_sha"],
        "manifest_version": MANIFEST_VERSION,
        "schema_sha256": manifest["runtime"]["schema_sha256"],
        "sqlite_user_version": manifest["runtime"]["sqlite_user_version"],
        "database_sha256": manifest["database"]["sha256"],
    }


def check_account_restore_generation(*, policy_path: Path, set_path: Path | None = None,
                                     database_path: Path | None = None) -> GenerationApproval:
    """Read-only eligibility gate. It never replays a ledger or starts/restores anything."""
    if (set_path is None) == (database_path is None):
        raise RecoveryError("generation_target_invalid")
    policy_path = absolute_path(policy_path)
    if set_path is not None:
        set_path = absolute_path(set_path)
        if policy_path.is_relative_to(set_path):
            raise RecoveryError("generation_policy_must_be_independent")
    raw, _ = stable_bytes(policy_path, limit=1024 * 1024)
    policy_sha256 = digest(raw)
    declared = strict_json(raw)
    coverage = ledger_coverage(declared)
    latest, _ = stable_bytes(absolute_path(declared["path"]))
    _, record_count = parse_ledger(latest)
    if coverage["mode"] == "historical" and record_count == 0:
        raise RecoveryError("empty_ledger_history_not_proven")
    check_anchor(latest, declared["anchor"])
    if database_path is not None:
        database_path = absolute_path(database_path)
        info = file_metadata(database_path)
        # A legacy file's mtime/name is not snapshot provenance. Only the independently
        # approved exact baseline can pass; every other bare DB remains unclassified.
        if coverage["mode"] != "cutoff" or info["sha256"] != coverage["baseline_database_sha256"]:
            raise RecoveryError("legacy_generation_not_approved")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(database_path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise RecoveryError("baseline_not_sealed")
        verify_database(database_path, immutable=True)
        source_database = database_path
        source_sha256 = info["sha256"]
        captured = latest
    else:
        raw_manifest, _ = stable_bytes(set_path / "manifest.json", limit=8 * 1024 * 1024)
        candidate = strict_json(raw_manifest)
        safety = candidate.get("account_restore_safety") if isinstance(candidate, dict) else None
        if isinstance(safety, dict) and safety.get("status") == UNSAFE_FOR_ACCOUNT_RESTORE:
            raise RecoveryError("generation_declared_unsafe")
        if isinstance(safety, dict) and safety.get("status") == UNKNOWN:
            raise RecoveryError("generation_safety_unknown")
        manifest = verify_recovery_set(set_path)
        check_generation_boundary(manifest, coverage)
        if (manifest["ledger"]["anchor"] != declared["anchor"]
                or manifest["ledger"]["audit_reference"] != declared["audit_reference"]):
            raise RecoveryError("generation_ledger_anchor_mismatch")
        captured, _ = stable_bytes(set_path / manifest["ledger"]["path"])
        source_database = set_path / manifest["database"]["path"]
        source_sha256 = manifest["database"]["sha256"]
    # Re-read after verification; replay uses these validated bytes, not a weaker parser.
    latest, _ = stable_bytes(absolute_path(declared["path"]))
    owners, _ = parse_ledger(latest)
    check_anchor(latest, declared["anchor"])
    if not latest.startswith(captured):
        raise RecoveryError("latest_ledger_history_changed")
    return GenerationApproval(source_database, source_sha256, Path(declared["path"]),
                              digest(latest), policy_path, policy_sha256, frozenset(owners))


def classify_account_restore_generation(**kwargs) -> dict:
    """Read-only classification; UNKNOWN is blocked just as strictly as UNSAFE."""
    try:
        check_account_restore_generation(**kwargs)
        return {"status": SAFE_FOR_ACCOUNT_RESTORE, "reason": "generation_and_latest_ledger_verified"}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (RecoveryError, BackupValidationError)) else type(exc).__name__
        unsafe = {"generation_before_recovery_cutoff", "legacy_generation_not_approved",
                  "generation_declared_unsafe", "generation_policy_mismatch", "set_checksum_mismatch",
                  "ledger_history_changed", "latest_ledger_history_changed", "generation_ledger_anchor_mismatch"}
        return {"status": UNSAFE_FOR_ACCOUNT_RESTORE if reason in unsafe else UNKNOWN, "reason": reason}


def schema_metadata(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    objects = [list(row) for row in rows]
    schema = {"objects": objects, "sqlite_user_version": conn.execute("PRAGMA user_version").fetchone()[0]}
    return {**schema, "sha256": digest(json_bytes(schema))}


def git_metadata(repo: Path, expected_sha: str, environment_files: list[str]) -> dict:
    if not SHA.fullmatch(expected_sha):
        raise RecoveryError("expected_git_sha_invalid")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
    if git("rev-parse", "--show-toplevel") != str(repo) or git("rev-parse", "HEAD") != expected_sha:
        raise RecoveryError("git_revision_mismatch")
    changes = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"], stderr=subprocess.DEVNULL,
    ).decode().split("\0")
    allowed = {".rove-app-api.env", ".rove-leeway.env", ".rove-market-data.env"}
    untracked_env = []
    for change in filter(None, changes):
        name = change[3:]
        if change[:3] != "?? " or name not in allowed or str(repo / name) not in environment_files:
            raise RecoveryError("git_worktree_dirty")
        untracked_env.append(name)
    tools = {}
    for name in ("rove_recovery_set.py", "backup_clarity_db.py", "reapply_account_delete_tombstones.py"):
        committed = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{expected_sha}:{name}"], stderr=subprocess.DEVNULL,
        )
        installed, _ = stable_bytes(Path(__file__).parent / name)
        if digest(installed) != digest(committed):
            raise RecoveryError("tool_not_bound_to_git_revision")
        tools[name] = digest(installed)
    branch = git("branch", "--show-current")
    return {"git_sha": expected_sha, "branch": branch or None, "detached_head": not bool(branch),
            "worktree_clean": not untracked_env, "allowed_untracked_environment_files": untracked_env,
            "tools_sha256": tools}


def load_inventory(path: Path, db: Path, ledger: Path) -> dict:
    raw, _ = stable_bytes(path, limit=1024 * 1024)
    data = fields(strict_json(raw), {"inventory_version", "database", "ledger", "artifact_roots",
                                   "expected_schema_sha256", "runtime", "secret_dependencies"})
    if type(data["inventory_version"]) is not int or data["inventory_version"] != 1:
        raise RecoveryError("inventory_version_invalid")
    if absolute_path(data["database"]) != db:
        raise RecoveryError("inventory_database_mismatch")
    ledger_coverage(data["ledger"])
    if absolute_path(data["ledger"]["path"]) != ledger:
        raise RecoveryError("ledger_path_mismatch")
    if not HASH.fullmatch(str(data["expected_schema_sha256"])):
        raise RecoveryError("expected_schema_invalid")
    roots = fields(data["artifact_roots"], {"public_reports", "reports"})
    root_paths = [absolute_path(value) for value in roots.values()]
    if any(not root.is_dir() for root in root_paths) or root_paths[0] == root_paths[1]:
        raise RecoveryError("artifact_root_invalid")
    runtime = fields(data["runtime"], {"python_version", "sqlite_version", "gunicorn_version", "entrypoint",
                                      "worker_class", "workers", "threads", "bind", "timezone", "environment_files", "config_files"})
    if (not re.fullmatch(r"3\.12\.[0-9]+", str(runtime["python_version"]))
            or not re.fullmatch(r"3\.[0-9]+\.[0-9]+", str(runtime["sqlite_version"]))
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(runtime["gunicorn_version"]))
            or runtime["entrypoint"] != "rove_app_wsgi:app" or runtime["worker_class"] != "gthread"
            or type(runtime["workers"]) is not int or runtime["workers"] != 1
            or type(runtime["threads"]) is not int or runtime["threads"] != 4
            or runtime["bind"] != "127.0.0.1:5057"):
        raise RecoveryError("runtime_inventory_invalid")
    try:
        ZoneInfo(runtime["timezone"])
    except (ValueError, TypeError, KeyError) as exc:
        raise RecoveryError("runtime_timezone_invalid") from exc
    if not isinstance(runtime["environment_files"], list) or not runtime["environment_files"]:
        raise RecoveryError("environment_inventory_missing")
    for value in runtime["environment_files"]:
        absolute_path(value)
    if not isinstance(runtime["config_files"], list):
        raise RecoveryError("config_inventory_missing")
    roles = []
    configured_paths = set()
    for item in runtime["config_files"]:
        fields(item, {"role", "path", "custody_ref"})
        if item["role"] not in CONFIG_ROLES or not REFERENCE.fullmatch(str(item["custody_ref"])):
            raise RecoveryError("config_inventory_invalid")
        configuration_path(item["path"])
        if item["path"] in configured_paths:
            raise RecoveryError("config_inventory_duplicate")
        configured_paths.add(item["path"])
        roles.append(item["role"])
    if any(roles.count(role) != 1 for role in REQUIRED_CONFIG_ROLES):
        raise RecoveryError("config_inventory_incomplete")
    if not isinstance(data["secret_dependencies"], list):
        raise RecoveryError("secret_inventory_invalid")
    names = set()
    for item in data["secret_dependencies"]:
        fields(item, {"name", "version", "custody_ref"})
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", str(item["name"]))
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(item["version"]))
                or not REFERENCE.fullmatch(str(item["custody_ref"])) or item["name"] in names):
            raise RecoveryError("secret_inventory_invalid")
        names.add(item["name"])
    return data


def runtime_metadata(inventory: dict) -> tuple[dict, set[str]]:
    runtime = inventory["runtime"]
    metadata = {key: value for key, value in runtime.items() if key not in {"config_files", "environment_files"}}
    metadata["config_files"] = []
    metadata["environment_files"] = []
    required = set()
    effective = systemd_api_configuration()
    services = [item["path"] for item in runtime["config_files"] if item["role"] == "api_service"]
    dropins = sorted(item["path"] for item in runtime["config_files"] if item["role"] == "api_dropin")
    if services != [effective["fragment_path"]] or dropins != effective["drop_in_paths"]:
        raise RecoveryError("effective_api_configuration_not_inventoried")
    if sorted(runtime["environment_files"]) != effective["environment_files"]:
        raise RecoveryError("effective_environment_not_inventoried")
    metadata["effective_api_configuration"] = effective
    for item in sorted(runtime["config_files"], key=lambda item: (item["role"], item["path"])):
        target = configuration_path(item["path"])
        raw, info = stable_bytes(target)
        if target != configuration_path(item["path"]):
            raise RecoveryError("configuration_link_changed")
        metadata["config_files"].append({**info, "configured_path": item["path"],
                                         "symlink_resolved": target != Path(item["path"]),
                                         "role": item["role"], "custody_ref": item["custody_ref"]})
        if item["role"] in {"api_service", "api_dropin"}:
            required |= credential_names(raw, unit=True)
    for value in sorted(runtime["environment_files"]):
        raw, info = stable_bytes(Path(value))
        metadata["environment_files"].append(info)
        required |= credential_names(raw)
    return metadata, required


def provider_key_dependencies(conn: sqlite3.Connection) -> set[str]:
    names = set()
    for table, column in (("app_provider_secrets", "key_version"), ("app_provider_accounts", "iban_key_version")):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if column not in columns:
            raise RecoveryError("provider_schema_invalid")
        for (version,) in conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
            if type(version) is not int or version < 1:
                raise RecoveryError("provider_key_version_invalid")
            names.add(f"ROVE_OPEN_BANKING_VAULT_KEY_V{version}")
    return names


def capture_file(source: Path, target: Path, relative: str, **attributes) -> dict:
    info = file_metadata(source, target=target)
    return {**info, "path": relative, "sensitive": True, "set_mode": "0600", **attributes}


def local_report_assets(raw: bytes) -> set[str]:
    """Allow the one existing shared script, not arbitrary paths from HTML."""
    class Resources(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []
            self.css = []
            self.in_style = False

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "style":
                self.in_style = True
            if tag == "base" and values.get("href"):
                raise RecoveryError("report_local_dependency_unclassified")
            for attribute in ("style", "fill", "filter"):
                if values.get(attribute):
                    self.css.append(values[attribute])
            if tag in {"script", "img", "source", "video", "audio", "iframe"}:
                self.urls.append(values.get("src") or "")
            if tag == "link" and values.get("rel", "").lower() in {"stylesheet", "icon", "preload", "modulepreload"}:
                self.urls.append(values.get("href") or "")
            if values.get("srcset"):
                raise RecoveryError("report_local_dependency_unclassified")

        def handle_endtag(self, tag):
            if tag == "style":
                self.in_style = False

        def handle_data(self, data):
            if self.in_style:
                self.css.append(data)

    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise RecoveryError("report_encoding_invalid") from exc
    parser = Resources()
    parser.feed(text)
    urls = list(parser.urls)
    for css in parser.css:
        urls += [match[1].strip(" \"'") for match in re.finditer(r"url\((.*?)\)", css, re.IGNORECASE)]
        urls += re.findall(r"@import\s+['\"]([^'\"]+)['\"]", css, re.IGNORECASE)
    assets = set()
    for url in urls:
        if not url or url.startswith(("#", "data:")):
            continue
        parsed = urlsplit(url)
        if parsed.scheme in {"https", "http"} or parsed.netloc:
            if parsed.hostname not in {"getrove.de", "www.getrove.de"}:
                continue
            resource_path = parsed.path
        else:
            resource_path = urlsplit(urljoin("https://getrove.de/reports/report-token/", url)).path
        if resource_path != "/reports/support.js":
            raise RecoveryError("report_local_dependency_unclassified")
        assets.add("support.js")
    return assets


def artifact_metadata(conn: sqlite3.Connection, inventory: dict, staging: Path, deleted: set[int], captured_at: str) -> list[dict]:
    roots = inventory["artifact_roots"]
    public = Path(roots["public_reports"])
    reports = Path(roots["reports"])
    users = {row[0] for row in conn.execute("SELECT user_id FROM users")}
    artifacts = []
    shared_assets = set()
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "report_links" not in tables:
        raise RecoveryError("report_links_schema_missing")
    rows = conn.execute("SELECT token, user_id, html_path, expires_at FROM report_links WHERE status='active'").fetchall()
    snapshot_now = datetime.fromisoformat(captured_at)
    for token, user_id, path, expires_at in rows:
        if user_id not in users:
            raise RecoveryError("report_owner_invalid")
        try:
            expiry = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(inventory["runtime"]["timezone"]))
        except (TypeError, ValueError) as exc:
            raise RecoveryError("report_expiry_invalid") from exc
        if expiry <= snapshot_now or user_id in deleted:
            continue
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise RecoveryError("report_token_invalid")
        source = absolute_path(path)
        if source != public / token / "index.html":
            raise RecoveryError("report_path_outside_root")
        relative = f"artifacts/public_reports/{token}/index.html"
        artifacts.append(capture_file(source, staging / relative, relative, category="active_public_report",
                                      required=True, regenerable=False, owner_user_id=user_id,
                                      expires_at=expires_at, reason="preserve_existing_report_url_and_document"))
        raw, _ = stable_bytes(staging / relative)
        shared_assets |= local_report_assets(raw)
    for name in sorted(shared_assets):
        relative = f"artifacts/public_reports/{name}"
        artifacts.append(capture_file(public / name, staging / relative, relative, category="shared_report_asset",
                                      required=True, regenerable=False,
                                      reason="existing_local_dependency_of_active_public_report"))
    for root in (reports, reports / "archive"):
        if not root.exists():
            if root == reports:
                raise RecoveryError("report_directory_missing")
            continue
        absolute_path(root)
        if not root.is_dir():
            raise RecoveryError("report_directory_invalid")
        for source in sorted(root.iterdir()):
            match = PDF.fullmatch(source.name)
            if not match:
                if source.is_dir() and source.name == "archive" and root == reports:
                    continue
                if source.name == ".DS_Store":
                    continue
                raise RecoveryError("report_layout_unknown")
            owner = int(match[1])
            if owner in deleted:
                continue
            if owner not in users:
                raise RecoveryError("pdf_owner_unknown")
            relative = "artifacts/reports/" + source.relative_to(reports).as_posix()
            artifacts.append(capture_file(source, staging / relative, relative, category="retained_report_pdf",
                                          required=True, regenerable=True, owner_user_id=owner,
                                          reason="preserve_retained_document_bytes_not_regenerated_today"))
    return artifacts


def prepare_output(output: Path, protected: list[Path]) -> None:
    for source in protected:
        if output == source or output.is_relative_to(source) or source.is_relative_to(output):
            raise RecoveryError("output_overlaps_source")
    output.mkdir(mode=0o700, exist_ok=True)
    info = output.stat()
    if not output.is_dir() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RecoveryError("output_not_private")


def build_recovery_set(*, db: Path, ledger: Path, inventory_path: Path, repo: Path,
                       expected_git_sha: str, output: Path) -> Path:
    db, ledger, inventory_path, repo, output = map(absolute_path, (db, ledger, inventory_path, repo, output))
    inventory = load_inventory(inventory_path, db, ledger)
    protected = [db, ledger, inventory_path, repo]
    protected += [Path(p) for p in inventory["artifact_roots"].values()]
    protected += [Path(p) for p in inventory["runtime"]["environment_files"]]
    protected += [Path(item["path"]) for item in inventory["runtime"]["config_files"]]
    protected += [configuration_path(item["path"]) for item in inventory["runtime"]["config_files"]]
    prepare_output(output, protected)
    set_id = "recovery_set_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex
    staging = output / ("." + set_id + ".pending")
    staging.mkdir(mode=0o700)
    manifest = {"manifest_version": MANIFEST_VERSION, "tool_version": TOOL_VERSION,
                "recovery_set_id": set_id, "created_at_utc": utc_now(), "status": "FAILED",
                "encryption": "NOT_ENCRYPTED_LOCAL_PRIVATE_ONLY", "offsite": "NOT_CONFIGURED",
                "restore_verified": False, "software": None,
                "account_restore_safety": {"status": UNKNOWN, "reason": "collection_not_verified",
                                           "gate_version": GENERATION_GATE_VERSION},
                "database": {"path": "database/clarity.db", "source_path": str(db), "bytes": None,
                             "sha256": None, "integrity_check": "NOT_RUN", "foreign_key_check": "NOT_RUN"},
                "ledger": {"path": "ledger/account_delete_tombstones.jsonl", "source_path": str(ledger),
                           "status": "NOT_CAPTURED", "sha256": None}, "artifacts": [],
                "runtime": {"path": "runtime/metadata.json", "expected_python": inventory["runtime"]["python_version"],
                            "schema_sha256": inventory["expected_schema_sha256"]}}
    try:
        coverage = ledger_coverage(inventory["ledger"])
        manifest["account_restore_coverage"] = coverage
        manifest["software"] = git_metadata(repo, expected_git_sha, inventory["runtime"]["environment_files"])
        metadata, required_secrets = runtime_metadata(inventory)
        manifest["configuration"] = metadata["config_files"]
        db_target = staging / "database" / "clarity.db"
        db_target.parent.mkdir(mode=0o700)
        db_before = db.stat()
        create_verified_backup(db, db_target)
        manifest["snapshot_completed_at_utc"] = utc_now()
        check_generation_boundary(manifest, coverage)
        db_info = file_metadata(db_target)
        source_stat = db.stat()
        if (source_stat.st_dev, source_stat.st_ino) != (db_before.st_dev, db_before.st_ino):
            raise RecoveryError("database_source_replaced")
        manifest["database"] = {"path": "database/clarity.db", "source_path": str(db),
                                "source_mode": f"{stat.S_IMODE(source_stat.st_mode):04o}",
                                "source_uid": source_stat.st_uid, "source_gid": source_stat.st_gid,
                                "bytes": db_info["bytes"], "sha256": db_info["sha256"],
                                "integrity_check": "ok", "foreign_key_check": "ok",
                                "snapshot_method": "sqlite_backup_api", "live_sidecars_included": False}
        with closing(sqlite3.connect(db_target.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            schema = schema_metadata(conn)
            if schema["sha256"] != inventory["expected_schema_sha256"]:
                raise RecoveryError("schema_fingerprint_mismatch")
            required_secrets |= provider_key_dependencies(conn)
            if required_secrets - {item["name"] for item in inventory["secret_dependencies"]}:
                raise RecoveryError("secret_dependency_missing")
            raw, ledger_info = stable_bytes(ledger)
            deleted, count = parse_ledger(raw)
            if coverage["mode"] == "historical" and count == 0:
                raise RecoveryError("empty_ledger_history_not_proven")
            check_anchor(raw, inventory["ledger"]["anchor"])
            artifacts = artifact_metadata(conn, inventory, staging, deleted, manifest["snapshot_completed_at_utc"])
        # Capture the latest append-only ledger again, after copying referenced files.
        latest, ledger_info = stable_bytes(ledger)
        if not latest.startswith(raw):
            raise RecoveryError("ledger_history_changed")
        deleted, count = parse_ledger(latest)
        check_anchor(latest, inventory["ledger"]["anchor"])
        ledger_relative = "ledger/account_delete_tombstones.jsonl"
        private_write(staging / ledger_relative, latest)
        manifest["ledger"] = {**ledger_info, "path": ledger_relative, "status": "VALID",
                              "records": count, "captured_at_utc": utc_now(),
                              "completeness": ("OPERATOR_ATTESTED_WITH_PREFIX_ANCHOR"
                                               if coverage["mode"] == "historical" else "FROM_VERIFIED_CUTOFF_ONLY"),
                              "audit_reference": inventory["ledger"]["audit_reference"],
                              "anchor": inventory["ledger"]["anchor"], "replay_before_runtime_required": True}
        manifest["artifacts"] = artifacts
        current_metadata, _ = runtime_metadata(inventory)
        if current_metadata != metadata:
            raise RecoveryError("runtime_changed_during_capture")
        if git_metadata(repo, expected_git_sha, inventory["runtime"]["environment_files"]) != manifest["software"]:
            raise RecoveryError("git_changed_during_capture")
        runtime_relative = "runtime/metadata.json"
        runtime_raw = json_bytes({"expected_runtime": metadata, "schema": schema,
                                  "collector_python": sys.version.split()[0], "collector_sqlite": sqlite3.sqlite_version})
        private_write(staging / runtime_relative, runtime_raw)
        manifest["runtime"] = {"path": runtime_relative, "bytes": len(runtime_raw), "sha256": digest(runtime_raw),
                               "expected_python": metadata["python_version"], "schema_sha256": schema["sha256"],
                               "sqlite_user_version": schema["sqlite_user_version"]}
        manifest["secret_dependencies"] = inventory["secret_dependencies"]
        manifest["external_custody_verified"] = False
        manifest["runtime_inventory_provenance"] = "OPERATOR_ATTESTED"
        manifest["restore_preconditions"] = ["latest_complete_ledger_and_file_scrub", "compatible_code_and_schema",
                                             "independent_config_and_secret_recovery", "isolated_no_egress_validation",
                                             "independent_generation_gate_required"]
        manifest["status"] = "COMPLETE"
        manifest["completed_at_utc"] = utc_now()
        manifest["account_restore_safety"] = generation_manifest_safety(manifest)
        private_write(staging / "manifest.json", json_bytes(manifest))
        verify_recovery_set(staging, pending=True)
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["error_code"] = str(exc) if isinstance(exc, (RecoveryError, BackupValidationError)) else type(exc).__name__
        manifest["account_restore_safety"] = {
            "status": UNSAFE_FOR_ACCOUNT_RESTORE, "reason": manifest["error_code"],
            "gate_version": GENERATION_GATE_VERSION,
        }
        if isinstance(exc, BackupValidationError):
            check = "integrity_check" if str(exc) == "integrity_check_failed" else "foreign_key_check"
            manifest["database"][check] = "FAILED"
        manifest_path = staging / "manifest.json"
        # Only replace our own unpublished manifest; never touch an input or old set.
        if manifest_path.exists():
            manifest_path.unlink()
        private_write(manifest_path, json_bytes(manifest))
    final = output / (set_id if manifest["status"] == "COMPLETE" else set_id + ".failed")
    for directory in sorted([staging, *(p for p in staging.rglob("*") if p.is_dir())], key=lambda p: len(p.parts), reverse=True):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    os.rename(staging, final)
    fd = os.open(output, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return final


def verify_recovery_set(path: Path, *, pending: bool = False) -> dict:
    path = absolute_path(path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RecoveryError("set_not_private")
    raw, _ = stable_bytes(path / "manifest.json", limit=8 * 1024 * 1024)
    manifest = strict_json(raw)
    if (not isinstance(manifest, dict) or manifest.get("status") != "COMPLETE"
            or manifest.get("manifest_version") != MANIFEST_VERSION
            or manifest.get("tool_version") != TOOL_VERSION):
        raise RecoveryError("set_not_complete")
    if not pending and path.name != manifest.get("recovery_set_id"):
        raise RecoveryError("set_id_mismatch")
    coverage = fields(manifest.get("account_restore_coverage"),
                      {"mode", "cutoff_at_utc", "baseline_database_sha256", "evidence_ref"})
    if (coverage["mode"] not in {"historical", "cutoff"}
            or not REFERENCE.fullmatch(str(coverage["evidence_ref"]))):
        raise RecoveryError("generation_coverage_invalid")
    if coverage["mode"] == "historical":
        if coverage["cutoff_at_utc"] is not None or coverage["baseline_database_sha256"] is not None:
            raise RecoveryError("generation_coverage_invalid")
    elif not HASH.fullmatch(str(coverage["baseline_database_sha256"])):
        raise RecoveryError("generation_coverage_invalid")
    check_generation_boundary(manifest, coverage)
    records = [manifest["database"], manifest["ledger"], manifest["runtime"], *manifest["artifacts"]]
    expected = {"manifest.json"}
    for item in records:
        relative = item["path"]
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts or relative in expected:
            raise RecoveryError("manifest_path_invalid")
        expected.add(relative)
        info = file_metadata(path / relative)
        if info["sha256"] != item["sha256"] or info["bytes"] != item["bytes"]:
            raise RecoveryError("set_checksum_mismatch")
    actual = set()
    for entry in path.rglob("*"):
        absolute_path(entry)
        mode = stat.S_IMODE(entry.stat().st_mode)
        if mode & 0o077:
            raise RecoveryError("set_not_private")
        if entry.is_file():
            actual.add(entry.relative_to(path).as_posix())
        elif not entry.is_dir():
            raise RecoveryError("set_file_type_invalid")
    if expected != actual:
        raise RecoveryError("set_inventory_mismatch")
    runtime_raw, _ = stable_bytes(path / manifest["runtime"]["path"])
    runtime = strict_json(runtime_raw)
    if manifest.get("configuration") != runtime["expected_runtime"]["config_files"]:
        raise RecoveryError("set_configuration_metadata_mismatch")
    # These are sealed copies with an exact inventory, never live WAL databases.
    verify_database(path / manifest["database"]["path"], immutable=True)
    ledger_raw, _ = stable_bytes(path / manifest["ledger"]["path"])
    parse_ledger(ledger_raw)
    check_anchor(ledger_raw, manifest["ledger"]["anchor"])
    if coverage["mode"] == "historical" and manifest["ledger"]["anchor"]["bytes"] == 0:
        raise RecoveryError("empty_ledger_history_not_proven")
    if manifest.get("account_restore_safety") != generation_manifest_safety(manifest):
        raise RecoveryError("generation_safety_binding_invalid")
    with closing(sqlite3.connect((path / manifest["database"]["path"]).as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        schema = schema_metadata(conn)
        if (schema["sha256"] != manifest["runtime"]["schema_sha256"]
                or type(manifest["runtime"]["sqlite_user_version"]) is not int
                or schema["sqlite_user_version"] != manifest["runtime"]["sqlite_user_version"]):
            raise RecoveryError("set_schema_mismatch")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Private Rov.E Recovery-Sets, ohne Restore/Versand")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    for name in ("db", "ledger", "inventory", "repo", "output-dir"):
        create.add_argument("--" + name, type=Path, required=True)
    create.add_argument("--expected-git-sha", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("set_path", type=Path)
    gate = commands.add_parser("generation-gate")
    gate.add_argument("--policy", type=Path, required=True)
    target = gate.add_mutually_exclusive_group(required=True)
    target.add_argument("--set", dest="set_path", type=Path)
    target.add_argument("--db", dest="database_path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify_recovery_set(args.set_path)
            print("RECOVERY_SET_VERIFY=PASS")
            print("ACCOUNT_RESTORE=NOT_AUTHORIZED_USE_GENERATION_GATE")
            return 0
        if args.command == "generation-gate":
            result = classify_account_restore_generation(policy_path=args.policy, set_path=args.set_path,
                                                        database_path=args.database_path)
            print("ACCOUNT_RESTORE_GATE=" + result["status"])
            print("GATE_REASON=" + result["reason"])
            print("RESTORE=NOT_RUN; FILE_SCRUB_AND_INDEPENDENT_KEYS_REQUIRED")
            return 0 if result["status"] == SAFE_FOR_ACCOUNT_RESTORE else 1
        result = build_recovery_set(db=args.db, ledger=args.ledger, inventory_path=args.inventory,
                                    repo=args.repo, expected_git_sha=args.expected_git_sha, output=args.output_dir)
        manifest = strict_json((result / "manifest.json").read_bytes())
        print("RECOVERY_SET_STATUS=" + manifest["status"])
        print("RECOVERY_SET_ID=" + manifest["recovery_set_id"])
        if manifest["status"] == "FAILED":
            print("ERROR_CODE=" + manifest["error_code"])
        return 0 if manifest["status"] == "COMPLETE" else 1
    except Exception as exc:
        print("ACCOUNT_RESTORE_GATE=UNSAFE FOR ACCOUNT-RESTORE" if args.command == "generation-gate"
              else "RECOVERY_SET_STATUS=FAILED", file=sys.stderr)
        print("ERROR_CODE=" + (str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
