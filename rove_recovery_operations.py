"""Scheduled orchestration of the unchanged collector, gate, crypto and witnesses."""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The deployment pointer may be a symlink; frozen validators must see the real
# immutable package path, not a symlinked module origin.
if __name__ == "__main__":
    sys.path[0] = str(Path(__file__).resolve().parent)

import rove_recovery_crypto as crypto
import rove_recovery_set as recovery
import rove_recovery_trust as trust
from rove_recovery_alerts import OperatorAlerts
from scripts.build_recovery_operations_release import APP_COMMIT, TRUST_COMMIT, FROZEN, RELEASE_FILES


PIPELINE_TIMER = "rove-recovery-offsite.timer"
CONFIG_FIELDS = {"version", "release_commit", "app_commit", "app_repo", "database", "ledger",
                 "inventory", "inventory_sha256", "policy", "public_key", "public_key_sha256",
                 "state_root", "work_root", "gpg_temp_parent", "gpg", "pins", "recipients"}


class OperationError(RuntimeError):
    """Fixed codes only; CLI never prints arbitrary exception or SDK contents."""


def require(value, code):
    if not value:
        raise OperationError(code)


# Only these reviewed codes can be persisted or printed; never serialize an exception.
OPERATION_CODES = frozenset("""alert_probe_failed alert_probe_not_current app_binding_invalid
configuration_invalid credential_invalid credential_name_invalid credential_permissions_invalid
external_verification_missing frozen_code_not_compatible frozen_input_paths_invalid input_hash_invalid
inventory_changed live_authority_code_not_compatible lock_file_invalid operations_paths_invalid
operator_alert_delivery_not_verified policy_binding_invalid private_directory_required private_file_required
production_pins_invalid public_key_changed_or_private public_key_or_gpg_path_invalid recipients_invalid
recovery_dependency_mismatch release_commit_invalid release_directory_not_immutable release_file_changed
release_file_inventory_invalid release_file_not_immutable release_manifest_not_immutable release_provenance_invalid
runtime_invalid safe_cleanup_unavailable success_record_invalid systemd_credentials_missing unexpected_release_file
upload_confirmation_invalid upload_source_changed upload_source_mismatch watch_state_invalid work_cleanup_outside_job
conditional_collision upload_failed_or_ambiguous job_already_running job_terminated""".split())
COLLECTION_CODES = frozenset("""path_invalid absolute_path_required symlink_not_allowed config_path_invalid
config_file_unavailable source_file_invalid source_changed_during_capture output_overlaps_source output_not_private
effective_systemd_inventory_unavailable effective_api_configuration_not_inventoried effective_environment_not_inventoried
effective_environment_file_missing configuration_link_changed runtime_changed_during_capture git_revision_mismatch
git_worktree_dirty tool_not_bound_to_git_revision git_changed_during_capture schema_fingerprint_mismatch
secret_dependency_missing report_layout_unknown report_directory_missing report_directory_invalid report_owner_invalid
pdf_owner_unknown report_local_dependency_unclassified provider_schema_invalid provider_key_version_invalid
ledger_invalid ledger_incomplete_record ledger_history_changed empty_ledger_history_not_proven
generation_before_recovery_cutoff generation_policy_mismatch generation_ledger_anchor_mismatch
generation_declared_unsafe generation_safety_unknown generation_receipt_missing generation_manifest_receipt_mismatch
runtime_revision_binding_missing runtime_revision_binding_invalid set_not_complete set_checksum_mismatch
integrity_check_failed foreign_key_check_failed""".split())
TRUST_CODES = frozenset("""lambda_endpoint_invalid policy_revision_invalid policy_contract_invalid
authority_identity_invalid authority_version_mismatch invoke_failed invocation_failed external_response_invalid
receipt_invalid receipt_binding_invalid response_schema_invalid object_version_required""".split())
AWS_CODES = frozenset("""AccessDenied AccessDeniedException InvalidAccessKeyId InvalidClientTokenId
UnrecognizedClientException SignatureDoesNotMatch ExpiredToken ExpiredTokenException NoSuchKey NoSuchVersion
ResourceNotFoundException TooManyRequestsException ThrottlingException PreconditionFailed
ConditionalRequestConflict ServiceUnavailable InternalError ServiceException""".split())


def failure_details(exc):
    def details(category, kind, code):
        return {"category": category, "type": kind, "code": code}
    if isinstance(exc, OperationError):
        code = str(exc)
        code = code if code in OPERATION_CODES else "operation_validation_failed"
        category = "CREDENTIAL_LOADING" if code.startswith("credential_") or code == "systemd_credentials_missing" else "LOCAL_VALIDATION"
        if code == "recovery_dependency_mismatch":
            category = "DEPENDENCY"
        return details(category, "OperationError", code)
    if isinstance(exc, (ImportError, importlib.metadata.PackageNotFoundError)):
        return details("DEPENDENCY", "ImportError", "dependency_import_failed")
    if isinstance(exc, OSError):
        code = {errno.ENOENT: "file_missing", errno.EACCES: "file_permission_denied",
                errno.EPERM: "file_permission_denied", errno.EROFS: "readonly_filesystem",
                errno.ENOSPC: "filesystem_full"}.get(exc.errno, "filesystem_io_failed")
        return details("LOCAL_PATH_PERMISSION", "OSError", code)
    if isinstance(exc, recovery.sqlite3.Error):
        return details("SQLITE", "SQLiteError", "sqlite_operation_failed")
    if isinstance(exc, recovery.RecoveryError):
        code = str(exc)
        category = "RECOVERY_VALIDATION"
        if code in {"path_invalid", "absolute_path_required", "symlink_not_allowed", "config_path_invalid",
                    "config_file_unavailable", "source_file_invalid", "output_overlaps_source", "output_not_private"}:
            category = "LOCAL_PATH_PERMISSION"
        elif code.startswith("generation_") or code.startswith("runtime_revision_binding_"):
            category = "RECEIPT_GATE"
        elif code in {"integrity_check_failed", "foreign_key_check_failed"}:
            category = "SQLITE"
        return details(category, "RecoveryError",
                       code if code in COLLECTION_CODES else "recovery_validation_failed")
    if isinstance(exc, recovery.BackupValidationError):
        code = str(exc)
        return details("SQLITE", "BackupValidationError",
                       code if code in COLLECTION_CODES else "backup_validation_failed")
    if isinstance(exc, trust.TrustError):
        code = str(exc)
        return details("RECEIPT_GATE", "TrustError", code if code in TRUST_CODES else "trust_verification_failed")
    if isinstance(exc, crypto.CryptoError):
        return details("ENCRYPTION", "CryptoError", "crypto_operation_failed")
    if type(exc).__module__ == "botocore.exceptions":
        if type(exc).__name__ == "ClientError":
            response = getattr(exc, "response", None)
            error = response.get("Error") if isinstance(response, dict) else None
            code = error.get("Code") if isinstance(error, dict) else None
            return details("AWS_NETWORK", "ClientError", code if isinstance(code, str) and code in AWS_CODES else "aws_client_failed")
        kind = type(exc).__name__
        if kind in {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError"}:
            return details("AWS_NETWORK", kind, "aws_network_failed")
        return details("AWS_NETWORK", "SDKError", "aws_sdk_failed")
    if isinstance(exc, subprocess.CalledProcessError):
        return details("LOCAL_PROGRAM", "CalledProcessError", "local_program_failed")
    if isinstance(exc, subprocess.TimeoutExpired):
        return details("LOCAL_PROGRAM", "TimeoutExpired", "local_program_timeout")
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return details("INTERRUPTED", "Interrupted", "job_interrupted")
    return details("OTHER", "Exception", "unclassified_local_failure")


def failed_collection_details(source, job, fallback):
    # Failed collector manifests contain a fixed code, but cleanup removes the manifest.
    try:
        source = recovery.absolute_path(source)
        require(source.is_relative_to(job), "work_cleanup_outside_job")
        manifest = recovery.strict_json(private_read(source / "manifest.json"))
        if (manifest.get("status") == "FAILED" and manifest.get("manifest_version") == recovery.MANIFEST_VERSION
                and manifest.get("tool_version") == recovery.TOOL_VERSION):
            code = manifest.get("error_code")
            if isinstance(code, str) and code in COLLECTION_CODES:
                return failure_details(recovery.RecoveryError(code))
            kinds = {"PermissionError": PermissionError(errno.EACCES, ""),
                     "FileNotFoundError": FileNotFoundError(errno.ENOENT, ""),
                     "OperationalError": recovery.sqlite3.OperationalError(),
                     "ImportError": ImportError(), "ModuleNotFoundError": ModuleNotFoundError()}
            if isinstance(code, str) and code in kinds:
                return failure_details(kinds[code])
    except Exception:
        pass
    return fallback


def report_failure(stage, step, details, *, secondary=False):
    record = {"stage": stage, "step": step, **details}
    if secondary:
        record["secondary"] = True
    print("RECOVERY_FAILURE=" + json.dumps(record, sort_keys=True), flush=True)


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat()


def private_dir(path):
    path = recovery.absolute_path(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700, "private_directory_required")
    return path


def private_read(path):
    raw, info = recovery.stable_bytes(recovery.absolute_path(path), limit=1024 * 1024)
    require(info["source_uid"] == os.geteuid() and info["source_mode"] == "0600", "private_file_required")
    return raw


def state_read(path):
    if not path.exists() and not path.is_symlink():
        return None
    return recovery.strict_json(private_read(path))


def atomic_state(path, value):
    private_dir(path.parent)
    if path.exists() or path.is_symlink():
        private_read(path)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(recovery.json_bytes(value))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def exclusive(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600, "lock_file_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OperationError("job_already_running") from None
        yield
    finally:
        os.close(fd)


def credential(name):
    require(name in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_TOKEN", "BREVO_API_KEY"},
            "credential_name_invalid")
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    require(directory, "systemd_credentials_missing")
    path = recovery.absolute_path(Path(directory) / name)
    raw, info = recovery.stable_bytes(path, limit=8192)
    require(info["source_uid"] == 0 and int(info["source_mode"], 8) & 0o077 == 0,
            "credential_permissions_invalid")
    value = raw.decode().strip()
    require(value and "\n" not in value and "\r" not in value, "credential_invalid")
    return value


def load_config(path):
    raw = private_read(path)
    config = recovery.strict_json(raw)
    require(isinstance(config, dict) and set(config) == CONFIG_FIELDS
            and type(config["version"]) is int and config["version"] == 1,
            "configuration_invalid")
    require(config["app_commit"] == APP_COMMIT and config["app_repo"] == "/root/clarity",
            "app_binding_invalid")
    require(re.fullmatch(r"[0-9a-f]{40}", config["release_commit"]), "release_commit_invalid")
    pins = trust.Pins(**config["pins"])
    for name in ("inventory_sha256", "public_key_sha256"):
        require(trust.HASH.fullmatch(config[name]), "input_hash_invalid")
    for name in ("app_repo", "database", "ledger", "inventory", "policy", "public_key", "state_root",
                 "work_root", "gpg_temp_parent", "gpg"):
        config[name] = recovery.absolute_path(config[name])
    require(config["state_root"] == Path("/var/lib/rove-recovery/state")
            and config["work_root"] == Path("/var/lib/rove-recovery/work")
            and config["gpg_temp_parent"] == Path("/run/rove-recovery-gpg"), "operations_paths_invalid")
    require(config["database"] == Path("/root/clarity/clarity.db")
            and config["ledger"] == Path("/root/account_delete_tombstones.jsonl")
            and config["inventory"].parent == config["policy"].parent == Path("/root/rove-recovery-control"),
            "frozen_input_paths_invalid")
    require(config["public_key"].parent == Path("/etc/rove-recovery") and config["gpg"] == Path("/usr/bin/gpg"),
            "public_key_or_gpg_path_invalid")
    require(set(config["recipients"]) == {"telegram_chat_id", "email", "from_email"}, "recipients_invalid")
    require(pins.account_id == "295356239462" and pins.recovery_bucket ==
            "rove-recovery-prod-2026-295356239462-eu-central-1-an", "production_pins_invalid")
    config["pins"] = pins
    config["config_sha256"] = trust.sha(raw)
    return config


def verify_release(config):
    root = Path(__file__).resolve().parent
    manifest = recovery.strict_json((root / "release.json").read_bytes())
    require(set(manifest) == {"version", "commit", "app_commit", "trust_commit", "files", "frozen_sha256"}
            and manifest["version"] == 1 and manifest["commit"] == config["release_commit"]
            and manifest["app_commit"] == APP_COMMIT and manifest["trust_commit"] == TRUST_COMMIT,
            "release_provenance_invalid")
    require(set(manifest["files"]) == set(RELEASE_FILES) and set(manifest["frozen_sha256"]) == set(FROZEN),
            "release_file_inventory_invalid")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
            == set(RELEASE_FILES) | {"release.json"}, "unexpected_release_file")
    for path in (root, *(p for p in root.rglob("*") if p.is_dir())):
        info = path.lstat()
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o555
                and not path.is_symlink(), "release_directory_not_immutable")
    info = (root / "release.json").lstat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o444 and info.st_nlink == 1,
            "release_manifest_not_immutable")
    for relative, digest in manifest["files"].items():
        path = recovery.absolute_path(root / relative)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o444, "release_file_not_immutable")
        require(trust.sha(path.read_bytes()) == digest, "release_file_changed")
    for name in FROZEN:
        if name == "rove_recovery_trust.py":
            require(manifest["frozen_sha256"][name] == manifest["files"][name] == config["pins"].release_sha256,
                    "live_authority_code_not_compatible")
            continue
        committed = subprocess.check_output(["git", "-C", str(config["app_repo"]), "show", f"{APP_COMMIT}:{name}"],
                                           stderr=subprocess.DEVNULL, timeout=30)
        require(manifest["frozen_sha256"][name] == manifest["files"][name] == trust.sha(committed),
                "frozen_code_not_compatible")
    for line in (root / "requirements/recovery.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            package, expected = line.split("==")
            require(importlib.metadata.version(package) == expected, "recovery_dependency_mismatch")


def validate_inputs(config):
    require(trust.sha(private_read(config["inventory"])) == config["inventory_sha256"], "inventory_changed")
    raw = private_read(config["policy"])
    policy = trust.validate_policy(recovery.strict_json(raw), config["pins"].policy_revision)
    require(trust.sha(raw) == config["pins"].policy_revision
            and policy["recovery_boundary"]["expected_git_sha"] == config["app_commit"]
            and policy["path"] == str(config["ledger"]), "policy_binding_invalid")
    key = private_read(config["public_key"])
    require(trust.sha(key) == config["public_key_sha256"] and b"PRIVATE KEY" not in key,
            "public_key_changed_or_private")
    return policy


def aws_clients(read_credential=credential):
    import boto3
    from botocore.config import Config
    session = boto3.Session(aws_access_key_id=read_credential("AWS_ACCESS_KEY_ID"),
                            aws_secret_access_key=read_credential("AWS_SECRET_ACCESS_KEY"), region_name="eu-central-1")
    options = Config(region_name="eu-central-1", connect_timeout=5, read_timeout=120,
                     retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True,
                     proxies={}, s3={"addressing_style": "virtual"})
    return session.client("s3", config=options), session.client("lambda", config=options)


def conditional_put(s3, pins, cipher, metadata):
    before = trust.cipher_metadata(cipher)
    require(before == {"sha256": metadata["ciphertext_sha256"], "bytes": metadata["ciphertext_bytes"]},
            "upload_source_mismatch")
    checksum = base64.b64encode(bytes.fromhex(before["sha256"])).decode()
    fd = os.open(cipher, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as body:
        start = crypto.identity(os.fstat(body.fileno()))
        try:
            response = s3.put_object(Bucket=pins.recovery_bucket,
                Key=f"recovery/{metadata['recovery_set_id']}.tar.gz.gpg", Body=body,
                ContentLength=before["bytes"], ContentType="application/octet-stream",
                ChecksumSHA256=checksum, IfNoneMatch="*", ExpectedBucketOwner=pins.account_id)
        except Exception as exc:
            code = trust.aws_code(exc)
            raise OperationError("conditional_collision" if code == "PreconditionFailed" else "upload_failed_or_ambiguous") from None
        require(start == crypto.identity(os.fstat(body.fileno())), "upload_source_changed")
    require(trust.cipher_metadata(cipher) == before, "upload_source_changed")
    require(response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200
            and response.get("ChecksumSHA256") == checksum, "upload_confirmation_invalid")
    return trust.version_id(response.get("VersionId"))


def freshness(success, config_hash, current=None):
    current = current or now()
    if not isinstance(success, dict) or success.get("status") != "SUCCESS" or success.get("config_sha256") != config_hash:
        return "CRITICAL"
    try:
        age = (current - trust.timestamp(success["verified_at_utc"])).total_seconds()
        require(age >= 0 and success["external_verifier"]["status"] == "PASS"
                and success["external_verifier"]["version_id"] == success["version_id"]
                and success["external_verifier"]["mode"] == "COMPLIANCE"
                and success["manifest_receipt"]["status"] == success["crypto_receipt"]["status"] == "PASS",
                "success_record_invalid")
    except (KeyError, TypeError, ValueError, trust.TrustError, OperationError):
        return "CRITICAL"
    return "CRITICAL" if age > 10 * 3600 else "WARNING" if age > 6 * 3600 else "HEALTHY"


def timer_active():
    result = subprocess.run(["systemctl", "show", PIPELINE_TIMER, "-p", "ActiveState", "-p", "NextElapseUSecRealtime"],
                            capture_output=True, text=True, timeout=15)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result.returncode == 0 and values.get("ActiveState") == "active" and bool(values.get("NextElapseUSecRealtime"))


class Operations:
    def __init__(self, config, alerts, *, clients=aws_clients, encrypt=crypto.encrypt_set,
                 collect=recovery.build_recovery_set, preflight=verify_release):
        self.config, self.alerts = config, alerts
        self.clients, self.encrypt, self.collect, self.preflight = clients, encrypt, collect, preflight
        self.state = private_dir(config["state_root"])
        self.work = private_dir(config["work_root"])

    def alert(self, level, stage, run_id):
        delivered = self.alerts.notify(level, stage, run_id)
        print("ALERT_TELEGRAM=" + ("ACCEPTED" if delivered["telegram"] else "FAILED"))
        print("ALERT_EMAIL=" + ("ACCEPTED" if delivered["email"] else "FAILED"))
        return all(delivered.values())

    def probe_alerts(self):
        with exclusive(self.state / "watch.lock"):
            identifier = uuid.uuid4().hex.upper()
            accepted = self.alert("TEST", "OPERATOR_DELIVERY_TEST", identifier)
            atomic_state(self.state / "alert-probe.json", {"probe_id": identifier, "at_utc": stamp(),
                "config_sha256": self.config["config_sha256"], "both_accepted": accepted})
            print("ALERT_PROBE_ID=" + identifier)
            print("ACTUAL_OPERATOR_RECEIPT=REQUIRES_CONFIRMATION")
            require(accepted, "alert_probe_failed")

    def confirm_alerts(self, identifier):
        with exclusive(self.state / "watch.lock"):
            probe = state_read(self.state / "alert-probe.json")
            require(probe and probe["probe_id"] == identifier and probe["both_accepted"] is True
                    and probe["config_sha256"] == self.config["config_sha256"]
                    and 0 <= (now() - trust.timestamp(probe["at_utc"])).total_seconds() <= 3600,
                    "alert_probe_not_current")
            atomic_state(self.state / "activation.json", {**probe, "both_received_operator_confirmed": True})

    def activation(self):
        proof = state_read(self.state / "activation.json")
        require(proof and proof.get("both_received_operator_confirmed") is True
                and proof.get("both_accepted") is True and proof.get("config_sha256") == self.config["config_sha256"],
                "operator_alert_delivery_not_verified")

    def clean_work(self, path):
        require(path.parent == self.work and re.fullmatch(r"job-[0-9A-F]{32}", path.name)
                and not path.is_symlink(), "work_cleanup_outside_job")
        private_dir(path)
        require(shutil.rmtree.avoids_symlink_attacks, "safe_cleanup_unavailable")
        shutil.rmtree(path)

    def record_failure(self, attempt, *, notify=True):
        # Secondary state/alert failures must not replace the primary pipeline error.
        if notify:
            report_failure(attempt["stage"], attempt["step"], attempt["error"])
        try:
            atomic_state(self.state / "attempt.json", attempt)
        except BaseException as exc:
            report_failure(attempt["stage"], "FAILURE_STATE", failure_details(exc), secondary=True)
        if notify:
            try:
                self.alert("FAILURE", attempt["stage"], attempt["run_id"])
            except BaseException as exc:
                report_failure(attempt["stage"], "FAILURE_ALERT", failure_details(exc), secondary=True)

    def run(self):
        with exclusive(self.state / "job.lock"):
            try:
                self.preflight(self.config)
                self.activation()
                policy = validate_inputs(self.config)
                # Workspace rejection is a failed attempt too, not a silent
                # exit while the previous success still looks healthy.
                for path in self.work.iterdir():
                    self.clean_work(path)
            except BaseException as exc:
                failed_id = uuid.uuid4().hex.upper()
                error = failure_details(exc)
                self.record_failure({"status": "FAILED", "stage": "PREFLIGHT",
                    "run_id": failed_id, "started_at_utc": stamp(), "completed_at_utc": stamp(),
                    "step": "PREFLIGHT", "error": error})
                raise
            interrupted = state_read(self.state / "attempt.json")
            if interrupted and interrupted.get("status") == "RUNNING":
                self.alert("FAILURE", "INTERRUPTED_JOB", interrupted["run_id"])
                atomic_state(self.state / "attempt.json", {**interrupted, "status": "ABORTED"})
            run_id = uuid.uuid4().hex.upper()
            job = private_dir(self.work / ("job-" + run_id))
            attempt = {"status": "RUNNING", "run_id": run_id, "stage": "COLLECTION", "started_at_utc": stamp(),
                       "step": "AWS_CLIENT_SETUP"}
            stage = "COLLECTION"
            source = None
            failed_attempt = None
            cleanup_attempted = False
            def checkpoint(next_stage):
                nonlocal stage
                stage = next_stage
                attempt["stage"] = stage
                attempt["step"] = stage
                atomic_state(self.state / "attempt.json", attempt)
            def collection_step(step):
                attempt["step"] = step
                atomic_state(self.state / "attempt.json", attempt)
            try:
                atomic_state(self.state / "attempt.json", attempt)
                s3, lambdas = self.clients()
                collection_step("TRUST_CLIENT_SETUP")
                client = trust.RecoveryTrustClient(self.config["pins"], policy, client=lambdas)
                collection_step("COLLECTOR")
                source = self.collect(db=self.config["database"], ledger=self.config["ledger"],
                    inventory_path=self.config["inventory"], repo=self.config["app_repo"],
                    expected_git_sha=self.config["app_commit"], output=job / "sets")
                collection_step("SET_VALIDATION")
                manifest = recovery.verify_recovery_set(source)
                attempt["generation_id"] = manifest["recovery_set_id"]
                checkpoint("MANIFEST_RECEIPT_GATE")
                with client.authorized_generation(policy_path=self.config["policy"], set_path=source) as approved:
                    checkpoint("ENCRYPTION")
                    envelope = job / "envelope"
                    self.encrypt(source, self.config["public_key"], self.config["pins"].public_key_fingerprint,
                                 envelope, gpg=str(self.config["gpg"]), gpg_temp_parent=self.config["gpg_temp_parent"])
                    checkpoint("CRYPTO_RECEIPT")
                    approved.bind_crypto(envelope)
                    metadata = trust.strict_json((envelope / "crypto.json").read_bytes())
                    checkpoint("CONDITIONAL_PUT")
                    version = conditional_put(s3, self.config["pins"],
                        envelope / (manifest["recovery_set_id"] + ".tar.gz.gpg"), metadata)
                    attempt["version_id"] = version
                    checkpoint("EXTERNAL_VERIFIER")
                    verified = approved.verify_uploaded(version)
                    require(approved.offsite_verified is True, "external_verification_missing")
                    validate_inputs(self.config)
                    checkpoint("DURABLE_SUCCESS")
                    success = {"status": "SUCCESS", "run_id": run_id, "verified_at_utc": stamp(),
                        "config_sha256": self.config["config_sha256"], "release_commit": self.config["release_commit"],
                        "app_commit": self.config["app_commit"], "generation_id": manifest["recovery_set_id"],
                        "manifest_sha256": trust.sha(approved.manifest_raw), "ciphertext_sha256": metadata["ciphertext_sha256"],
                        "ciphertext_bytes": metadata["ciphertext_bytes"], "version_id": version,
                        "manifest_receipt": approved.manifest_receipt, "crypto_receipt": approved.crypto_receipt,
                        "external_verifier": verified}
                # Publish success only after the authorized session and workspace cleanup finish.
                attempt["step"] = "CLEANUP"
                atomic_state(self.state / "attempt.json", attempt)
                cleanup_attempted = True
                self.clean_work(job)
                attempt["step"] = stage
                atomic_state(self.state / "success.json", success)
                atomic_state(self.state / "attempt.json", {**attempt, "status": "SUCCESS", "stage": stage,
                                                          "completed_at_utc": stamp()})
                print("OFFSITE_RUN=SUCCESS; EXTERNAL_RECEIPTS=VERIFIED; EXACT_VERSION=VERIFIED")
                print("GENERATION_ID=" + success["generation_id"])
                print("VERSION_ID=" + version)
                print("FRESHNESS=" + freshness(success, self.config["config_sha256"]))
                return success
            except BaseException as exc:
                if isinstance(exc, OperationError) and str(exc) == "conditional_collision":
                    stage = "CONDITIONAL_COLLISION"
                error = failure_details(exc)
                if attempt["step"] == "SET_VALIDATION" and source is not None:
                    error = failed_collection_details(source, job, error)
                failed_attempt = {**attempt, "status": "FAILED", "stage": stage,
                                  "completed_at_utc": stamp(), "error": error}
                self.record_failure(failed_attempt)
                raise
            finally:
                if not cleanup_attempted:
                    try:
                        self.clean_work(job)
                    except BaseException as exc:
                        if failed_attempt is None:
                            raise
                        cleanup_error = failure_details(exc)
                        report_failure(stage, "CLEANUP", cleanup_error, secondary=True)
                        failed_attempt["cleanup_error"] = cleanup_error
                        self.record_failure(failed_attempt, notify=False)

    def watch(self, *, check_timer=timer_active):
        with exclusive(self.state / "watch.lock"):
            try:
                success = state_read(self.state / "success.json")
            except Exception:
                success = None
            level = freshness(success, self.config["config_sha256"])
            try:
                active = check_timer()
            except Exception:
                active = False
            stage = "FRESHNESS" if active else "TIMER_STOPPED"
            if not active:
                level = "CRITICAL"
            try:
                attempt = state_read(self.state / "attempt.json")
                if attempt and (not success or trust.timestamp(attempt["started_at_utc"]) >
                                trust.timestamp(success["verified_at_utc"])):
                    if attempt["status"] in {"FAILED", "ABORTED"}:
                        level, stage = "CRITICAL", attempt["stage"]
                    elif attempt["status"] == "RUNNING" and (now() - trust.timestamp(attempt["started_at_utc"])
                                                              ).total_seconds() > 45 * 60:
                        level, stage = "CRITICAL", "JOB_TIMEOUT"
            except Exception:
                level, stage = "CRITICAL", "ATTEMPT_STATE_INVALID"
            if not isinstance(stage, str) or not re.fullmatch(r"[A-Z0-9_-]{1,64}", stage):
                level, stage = "CRITICAL", "ATTEMPT_STATE_INVALID"
            try:
                previous = state_read(self.state / "watch.json")
                require(previous is None or isinstance(previous, dict), "watch_state_invalid")
                if previous and previous.get("level") != "HEALTHY":
                    require(type(previous.get("both_accepted")) is bool
                            and trust.timestamp(previous["last_alert_utc"]) <= now(), "watch_state_invalid")
            except Exception:
                previous = None
            if level != "HEALTHY":
                due = not previous or (previous.get("level"), previous.get("stage")) != (level, stage)
                due = due or not previous.get("both_accepted") or (
                    now() - trust.timestamp(previous["last_alert_utc"])).total_seconds() >= 4 * 3600
                if due:
                    accepted = self.alert(level, stage, uuid.uuid4().hex.upper())
                    atomic_state(self.state / "watch.json", {"level": level, "stage": stage,
                        "last_alert_utc": stamp(), "both_accepted": accepted})
            elif previous and previous.get("level") != "HEALTHY":
                if self.alert("RECOVERY", "FRESHNESS_RESTORED", uuid.uuid4().hex.upper()):
                    atomic_state(self.state / "watch.json", {"level": "HEALTHY", "stage": "FRESHNESS"})
            print(f"FRESHNESS={level}; TIMER={'ACTIVE' if active else 'STOPPED'}")
            return level


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "watch", "test-alert", "confirm-alert"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--both-received", help="operator-confirmed probe ID, only after both real deliveries")
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0 and not sys.flags.optimize and sys.version_info[:2] == (3, 12), "runtime_invalid")
        os.umask(0o077)
        config = load_config(args.config)
        private_dir(config["gpg_temp_parent"])
        operations = Operations(config, OperatorAlerts(config["recipients"], credential))
        if args.action != "run":
            verify_release(config)
        def terminated(signum, frame):
            raise OperationError("job_terminated")
        signal.signal(signal.SIGTERM, terminated)
        if args.action == "run":
            operations.run()
        elif args.action == "watch":
            return 0 if operations.watch() == "HEALTHY" else 1
        elif args.action == "test-alert":
            operations.probe_alerts()
        else:
            operations.confirm_alerts(args.both_received)
        return 0
    except OperationError as exc:
        code = failure_details(exc)["code"]
        print("RECOVERY_OPERATIONS=STOP; CODE=" + code)
        return 75 if code == "job_already_running" else 1
    except BaseException as exc:
        print("RECOVERY_OPERATIONS=STOP; CODE=" + failure_details(exc)["code"])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
