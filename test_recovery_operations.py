"""Synthetic orchestration, real canonical gates; never contact AWS/Production."""

import base64
import copy
import errno
import hashlib
import io
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
import types
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import rove_recovery_crypto as crypto
import rove_recovery_operations as ops
import rove_recovery_set as recovery
import rove_recovery_trust as trust
import test_recovery_trust as fixtures
from rove_recovery_alerts import AlertError, OperatorAlerts
from scripts.build_recovery_operations_release import build, RELEASE_FILES


class Alerts:
    def __init__(self):
        self.calls = []
        self.delivery = {"telegram": True, "email": True}

    def notify(self, *args):
        self.calls.append(args)
        return self.delivery.copy()


class Upload:
    def __init__(self, s3):
        self.s3 = s3
        self.calls = []
        self.error = None
        self.change = lambda response: response

    def put_object(self, **arguments):
        self.calls.append({key: value for key, value in arguments.items() if key != "Body"})
        if self.error:
            raise self.error
        raw = arguments["Body"].read()
        assert arguments["IfNoneMatch"] == "*"
        assert arguments["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(raw).digest()).decode()
        assert arguments["ContentLength"] == len(raw)
        modified = datetime.now(timezone.utc)
        self.s3.head = dict(VersionId="exact-version", ContentLength=len(raw), LastModified=modified,
            ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=modified + timedelta(days=30))
        self.s3.retention = dict(Mode="COMPLIANCE", RetainUntilDate=modified + timedelta(days=30))
        return self.change({"VersionId": "exact-version", "ChecksumSHA256": arguments["ChecksumSHA256"],
                            "ResponseMetadata": {"HTTPStatusCode": 200}})


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CanonicalGateAdapterTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture.fixture
        f.inventory_path.chmod(0o600)
        self.public = f.root / "public.asc"
        recovery.private_write(self.public, b"synthetic public key only")
        self.config = dict(app_commit=f.sha, app_repo=f.repo, database=f.db, ledger=f.ledger,
            inventory=f.inventory_path, inventory_sha256=trust.sha(f.inventory_path.read_bytes()),
            policy=self.fixture.policy_path, pins=self.fixture.pins, public_key=self.public,
            public_key_sha256=trust.sha(self.public.read_bytes()), gpg=Path("/synthetic/gpg"),
            gpg_temp_parent=f.root / "gpg", state_root=f.root / "state", work_root=f.root / "jobs",
            config_sha256="e" * 64, release_commit="f" * 40)
        self.alerts = Alerts()
        self.upload = Upload(self.fixture.s3)
        self.job = ops.Operations(self.config, self.alerts,
            clients=lambda: (self.upload, self.fixture.sdk), encrypt=self.encrypt,
            preflight=lambda config: None)
        self.activate()
        self.inputs_before = {path: path.read_bytes() for path in
                              (f.db, f.ledger, f.inventory_path, self.fixture.policy_path)}

    def activate(self):
        ops.atomic_state(self.job.state / "activation.json", {"both_received_operator_confirmed": True,
            "both_accepted": True, "config_sha256": self.config["config_sha256"]})

    def encrypt(self, source, public, fingerprint, envelope, **kwargs):
        # Deterministic transport fixture. The separate real-GPG E2E test below does not use this.
        envelope.mkdir(mode=0o700)
        cipher = b"synthetic encrypted bytes"
        manifest = recovery.verify_recovery_set(source)
        metadata = dict(wrapper_version="1.0", recovery_set_id=source.name,
            git_sha=manifest["software"]["git_sha"], manifest_sha256=trust.sha((source / "manifest.json").read_bytes()),
            archive_sha256="c" * 64, ciphertext_sha256=trust.sha(cipher), ciphertext_bytes=len(cipher),
            encrypted_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), public_key_fingerprint=fingerprint,
            encryption_key_fingerprint=fingerprint)
        recovery.private_write(envelope / "crypto.json", trust.json_bytes(metadata))
        recovery.private_write(envelope / (source.name + ".tar.gz.gpg"), cipher)
        return metadata

    def blocked(self):
        with self.assertRaises(BaseException):
            self.job.run()
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertEqual(list(self.job.work.iterdir()), [])
        self.assertTrue(self.alerts.calls)
        for path, original in self.inputs_before.items():
            self.assertEqual(path.read_bytes(), original)

    def test_complete_pipeline_durable_success_after_exact_verifier(self):
        success = self.job.run()
        self.assertEqual(ops.state_read(self.job.state / "success.json"), success)
        self.assertEqual(ops.freshness(success, self.config["config_sha256"]), "HEALTHY")
        self.assertEqual(success["manifest_receipt"]["receipt"]["request"]["operation"], "manifest")
        self.assertEqual(success["crypto_receipt"]["receipt"]["request"]["operation"], "crypto")
        self.assertEqual(len(self.upload.calls), 1)
        self.assertEqual(list(self.job.work.iterdir()), [])
        for path, original in self.inputs_before.items():
            self.assertEqual(path.read_bytes(), original)

    def test_success_record_not_written_before_verifier(self):
        original = self.fixture.sdk.mutate
        def verify(value):
            self.assertFalse((self.job.state / "success.json").exists())
            return original(value)
        self.fixture.sdk.mutate = verify
        self.job.run()

    def test_missing_operator_delivery_proof_blocks_before_collection(self):
        (self.job.state / "activation.json").unlink()
        self.blocked()
        self.assertFalse(self.upload.calls)

    def test_missing_manifest_receipt(self):
        self.fixture.sdk.mutate = lambda data: {"response_version": 1, "status": "BLOCK"}
        self.blocked()
        self.assertFalse(self.upload.calls)

    def test_manipulated_manifest_receipt(self):
        def mutate(data):
            data["receipt"]["request"]["manifest_sha256"] = "0" * 64
            return data
        self.fixture.sdk.mutate = mutate
        self.blocked()

    def test_wrong_policy_revision(self):
        self.config["pins"] = replace(self.config["pins"], policy_revision="0" * 64)
        self.blocked()
        self.assertFalse(self.fixture.sdk.calls)

    def test_wrong_published_authority_version(self):
        self.fixture.sdk.executed_version = "99"
        self.blocked()

    def test_canonical_gate_blocked_after_authentic_receipt(self):
        with patch.object(recovery, "check_account_restore_generation", side_effect=recovery.RecoveryError("blocked")):
            self.blocked()
        self.assertFalse(self.upload.calls)

    def test_generation_failure_alert_and_no_success(self):
        self.job.collect = lambda **kwargs: (_ for _ in ()).throw(recovery.RecoveryError("ledger_history_changed"))
        self.blocked()
        self.assertEqual(self.alerts.calls[-1][1], "COLLECTION")

    def test_gpg_failure_never_uploads(self):
        self.job.encrypt = lambda *args, **kwargs: (_ for _ in ()).throw(crypto.CryptoError("encryption_failed"))
        self.blocked()
        self.assertFalse(self.upload.calls)
        self.assertEqual(self.alerts.calls[-1][1], "ENCRYPTION")

    def test_missing_crypto_receipt_never_uploads(self):
        def mutate(data):
            if data.get("receipt", {}).get("request", {}).get("operation") == "crypto":
                return {"response_version": 1, "status": "BLOCK"}
            return data
        self.fixture.sdk.mutate = mutate
        self.blocked()
        self.assertFalse(self.upload.calls)

    def test_conditional_collision_no_retry_or_alternate_key(self):
        self.upload.error = fixtures.ClientError("PreconditionFailed")
        self.blocked()
        self.assertEqual(len(self.upload.calls), 1)
        self.assertEqual(self.alerts.calls[-1][1], "CONDITIONAL_COLLISION")

    def test_s3_network_or_access_failure_no_success(self):
        self.upload.error = fixtures.ClientError("AccessDenied")
        self.blocked()

    def test_upload_missing_version_never_latest_fallback(self):
        self.upload.change = lambda response: {**response, "VersionId": "null"}
        self.blocked()

    def test_upload_checksum_confirmation_required(self):
        self.upload.change = lambda response: {**response, "ChecksumSHA256": "wrong"}
        self.blocked()

    def test_metadata_verifier_failure(self):
        self.fixture.sdk.mutate = lambda data: ({"response_version": 1, "status": "BLOCK"}
                                               if "object_key" in data else data)
        self.blocked()

    def test_verifier_wrong_version(self):
        self.fixture.sdk.mutate = lambda data: ({**data, "version_id": "other-version"}
                                               if "object_key" in data else data)
        self.blocked()

    def test_verifier_retention_missing(self):
        self.fixture.sdk.mutate = lambda data: ({**data, "mode": "GOVERNANCE"}
                                               if "object_key" in data else data)
        self.blocked()

    def test_latest_ledger_rewrite_before_success_fails(self):
        original = self.upload.put_object
        def change(**arguments):
            response = original(**arguments)
            self.fixture.fixture.ledger.write_bytes(b"invalid ledger")
            return response
        self.upload.put_object = change
        with self.assertRaises(recovery.RecoveryError):
            self.job.run()
        self.assertFalse((self.job.state / "success.json").exists())

    def test_parallel_start_does_not_replace_attempt_or_success(self):
        with ops.exclusive(self.job.state / "job.lock"):
            with self.assertRaisesRegex(ops.OperationError, "job_already_running"):
                self.job.run()
        self.assertFalse((self.job.state / "attempt.json").exists())

    def test_interrupted_job_reclaimed_under_lock_on_restart(self):
        abandoned = ops.private_dir(self.job.work / ("job-" + "A" * 32))
        (abandoned / "synthetic-secret").write_text("synthetic only")
        ops.atomic_state(self.job.state / "attempt.json", {"status": "RUNNING", "run_id": "A" * 32,
            "stage": "ENCRYPTION", "started_at_utc": ops.stamp()})
        self.job.run()
        self.assertFalse(abandoned.exists())
        self.assertIn(("FAILURE", "INTERRUPTED_JOB", "A" * 32), self.alerts.calls)

    def test_process_abort_no_success_and_workspace_removed(self):
        self.job.encrypt = lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
        self.blocked()
        self.assertEqual(ops.state_read(self.job.state / "attempt.json")["status"], "FAILED")

    def test_sigkill_releases_lock_and_restart_reclaims_plaintext(self):
        ready = self.fixture.fixture.root / "child-ready"
        def block(*args, **kwargs):
            ready.write_text("ready")
            time.sleep(120)
        original = self.job.encrypt
        self.job.encrypt = block
        process = multiprocessing.get_context("fork").Process(target=self.job.run)
        process.start()
        self.addCleanup(lambda: process.kill() if process.is_alive() else None)
        deadline = time.monotonic() + 20
        while not ready.exists() and process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "child must reach isolated encryption stage")
        process.kill()
        process.join(10)
        self.assertLess(process.exitcode, 0)
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertEqual(ops.state_read(self.job.state / "attempt.json")["status"], "RUNNING")
        self.assertTrue(list(self.job.work.iterdir()))
        self.job.encrypt = original
        self.job.run()
        self.assertEqual(list(self.job.work.iterdir()), [])

    def test_failed_later_run_preserves_last_verified_success(self):
        self.job.run()
        original = (self.job.state / "success.json").read_bytes()
        self.upload.error = fixtures.ClientError("AccessDenied")
        with self.assertRaises(ops.OperationError):
            self.job.run()
        self.assertEqual((self.job.state / "success.json").read_bytes(), original)
        self.assertEqual(self.job.watch(check_timer=lambda: True), "CRITICAL")

    def test_warning_critical_boundaries_and_missing_success(self):
        success = self.job.run()
        at = trust.timestamp(success["verified_at_utc"])
        for seconds, expected in ((0, "HEALTHY"), (21600, "HEALTHY"), (21601, "WARNING"),
                                   (36000, "WARNING"), (36001, "CRITICAL"), (-1, "CRITICAL")):
            self.assertEqual(ops.freshness(success, self.config["config_sha256"], at + timedelta(seconds=seconds)), expected)
        self.assertEqual(ops.freshness(None, self.config["config_sha256"]), "CRITICAL")
        self.assertEqual(ops.freshness(success, "0" * 64), "CRITICAL")

    def test_stopped_timer_detected_even_with_fresh_success(self):
        self.job.run()
        self.assertEqual(self.job.watch(check_timer=lambda: False), "CRITICAL")
        self.assertEqual(self.alerts.calls[-1][1], "TIMER_STOPPED")

    def test_alert_delivery_failure_retried_not_marked_delivered(self):
        self.alerts.delivery["email"] = False
        self.job.watch(check_timer=lambda: True)
        before = len(self.alerts.calls)
        self.job.watch(check_timer=lambda: True)
        self.assertEqual(len(self.alerts.calls), before + 1)
        self.assertFalse(ops.state_read(self.job.state / "watch.json")["both_accepted"])

    def test_alert_probe_requires_both_channels_and_explicit_confirmation(self):
        (self.job.state / "activation.json").unlink()
        self.job.probe_alerts()
        self.assertFalse((self.job.state / "activation.json").exists())
        probe = ops.state_read(self.job.state / "alert-probe.json")
        self.job.confirm_alerts(probe["probe_id"])
        self.job.activation()

    def test_failed_alert_probe_cannot_authorize_activation(self):
        self.alerts.delivery["telegram"] = False
        with self.assertRaises(ops.OperationError):
            self.job.probe_alerts()
        probe = ops.state_read(self.job.state / "alert-probe.json")
        with self.assertRaises(ops.OperationError):
            self.job.confirm_alerts(probe["probe_id"])

    def test_cleanup_rejects_frozen_or_unknown_path(self):
        with self.assertRaises(ops.OperationError):
            self.job.clean_work(self.fixture.source)
        with self.assertRaises(ops.OperationError):
            self.job.clean_work(self.job.work / "unknown")

    def test_unknown_workspace_fails_and_alerts_despite_previous_success(self):
        self.job.run()
        unknown = self.job.work / "not-owned"
        unknown.mkdir(mode=0o700)
        with self.assertRaises(ops.OperationError):
            self.job.run()
        self.assertTrue(unknown.exists())
        self.assertEqual(ops.state_read(self.job.state / "attempt.json")["status"], "FAILED")
        self.assertEqual(self.alerts.calls[-1][1], "PREFLIGHT")
        self.assertEqual(self.job.watch(check_timer=lambda: True), "CRITICAL")

    def test_future_or_missing_watch_timestamp_cannot_suppress_alerts(self):
        for value in ({}, {"last_alert_utc": (ops.now() + timedelta(days=1)).isoformat()}):
            ops.atomic_state(self.job.state / "watch.json", {"level": "CRITICAL", "stage": "FRESHNESS",
                "both_accepted": True, **value})
            previous = len(self.alerts.calls)
            self.assertEqual(self.job.watch(check_timer=lambda: True), "CRITICAL")
            self.assertEqual(len(self.alerts.calls), previous + 1)

    def test_cli_run_release_failure_uses_alarm_aware_preflight(self):
        self.config["recipients"] = {"telegram_chat_id": "123", "email": "operator@example.invalid",
                                     "from_email": "sender@example.invalid"}
        self.job.preflight = lambda config: (_ for _ in ()).throw(ops.OperationError("release_file_changed"))
        owner, context_check = os.geteuid(), iter([0])
        with patch.object(ops, "load_config", return_value=self.config), \
             patch.object(ops, "Operations", return_value=self.job), \
             patch.object(ops, "verify_release", side_effect=AssertionError("preflight must be inside run")), \
             patch.object(ops.os, "geteuid", side_effect=lambda: next(context_check, owner)), \
             patch.object(ops.sys, "argv", ["operations", "run", "--config", "synthetic.json"]):
            self.assertEqual(ops.main(), 1)
        self.assertEqual(self.alerts.calls[-1][1], "PREFLIGHT")
        self.assertFalse((self.job.state / "success.json").exists())

    def test_state_symlink_rejected_without_target_mutation(self):
        target = self.fixture.fixture.root / "untouched"
        target.write_bytes(b"original")
        (self.job.state / "watch.json").symlink_to(target)
        with self.assertRaises(recovery.RecoveryError):
            self.job.watch(check_timer=lambda: True)
        self.assertEqual(target.read_bytes(), b"original")

    def test_damaged_success_state_is_critical_and_alerted(self):
        recovery.private_write(self.job.state / "success.json", b"{broken JSON")
        self.assertEqual(self.job.watch(check_timer=lambda: True), "CRITICAL")
        self.assertEqual(self.alerts.calls[-1][0], "CRITICAL")

    def test_systemd_check_failure_is_not_healthy(self):
        self.job.run()
        def unavailable():
            raise RuntimeError("synthetic systemd failure")
        self.assertEqual(self.job.watch(check_timer=unavailable), "CRITICAL")
        self.assertEqual(self.alerts.calls[-1][1], "TIMER_STOPPED")


class FailureClassificationTests(unittest.TestCase):
    def test_local_permission_missing_readonly_and_disk_full(self):
        for number, code in ((errno.EACCES, "file_permission_denied"), (errno.ENOENT, "file_missing"),
                             (errno.EROFS, "readonly_filesystem"), (errno.ENOSPC, "filesystem_full")):
            with self.subTest(number=number):
                result = ops.failure_details(OSError(number, "synthetic-secret", "/private/synthetic-recipient"))
                self.assertEqual(result["category"], "LOCAL_PATH_PERMISSION")
                self.assertEqual(result["code"], code)
                self.assertNotIn("synthetic", str(result))

    def test_dependency_import_classification_never_prints_module_or_message(self):
        result = ops.failure_details(ModuleNotFoundError("synthetic-secret-module"))
        self.assertEqual(result["category"], "DEPENDENCY")
        self.assertEqual(result["code"], "dependency_import_failed")
        self.assertNotIn("synthetic", str(result))

    def test_credential_loading_separate_from_dependency(self):
        for code in ("systemd_credentials_missing", "credential_permissions_invalid", "credential_invalid"):
            self.assertEqual(ops.failure_details(ops.OperationError(code))["category"], "CREDENTIAL_LOADING")

    def test_validation_code_allowlists_reject_secret_looking_codes(self):
        for kind in (ops.OperationError, recovery.RecoveryError, recovery.BackupValidationError,
                     crypto.CryptoError, trust.TrustError):
            with self.subTest(kind=kind):
                result = ops.failure_details(kind("synthetic_secret_looks_like_a_code"))
                self.assertNotIn("synthetic", str(result))

    def test_known_collector_and_gate_codes_preserved(self):
        for code in ("schema_fingerprint_mismatch", "generation_declared_unsafe", "generation_safety_unknown",
                     "ledger_history_changed", "source_changed_during_capture"):
            self.assertEqual(ops.failure_details(recovery.RecoveryError(code))["code"], code)

    def test_crypto_and_sqlite_messages_not_serialized(self):
        for exc, category in ((crypto.CryptoError("token-bearing gpg diagnostic"), "ENCRYPTION"),
                              (recovery.sqlite3.OperationalError("private SQL row"), "SQLITE")):
            result = ops.failure_details(exc)
            self.assertEqual(result["category"], category)
            self.assertNotIn(str(exc), str(result))

    def test_aws_code_only_no_sensitive_response_or_operation(self):
        from botocore.exceptions import ClientError
        for code, expected in (("AccessDenied", "AccessDenied"), ("synthetic-secret", "aws_client_failed")):
            exc = ClientError({"Error": {"Code": code, "Message": "synthetic-secret-response"},
                               "private": "synthetic-secret-body"}, "synthetic-secret-operation")
            result = ops.failure_details(exc)
            self.assertEqual(result["category"], "AWS_NETWORK")
            self.assertEqual(result["code"], expected)
            self.assertNotIn("synthetic", str(result))

    def test_network_exception_endpoint_not_printed(self):
        from botocore.exceptions import EndpointConnectionError
        result = ops.failure_details(EndpointConnectionError(endpoint_url="https://synthetic-secret.invalid"))
        self.assertEqual(result["type"], "EndpointConnectionError")
        self.assertEqual(result["code"], "aws_network_failed")
        self.assertNotIn("synthetic", str(result))

    def test_local_program_stdout_stderr_arguments_not_serialized(self):
        exc = subprocess.CalledProcessError(1, ["synthetic-secret-argument"], output="synthetic-secret-stdout",
                                            stderr="synthetic-secret-stderr")
        self.assertEqual(ops.failure_details(exc), {"category": "LOCAL_PROGRAM", "type": "CalledProcessError",
                                                   "code": "local_program_failed"})

    def test_arbitrary_exception_class_name_and_payload_not_serialized(self):
        class SyntheticSecretException(RuntimeError):
            pass
        result = ops.failure_details(SyntheticSecretException("synthetic-secret-message"))
        self.assertEqual(result, {"category": "OTHER", "type": "Exception", "code": "unclassified_local_failure"})

    def test_interrupt_remains_fail_closed(self):
        self.assertEqual(ops.failure_details(KeyboardInterrupt())["code"], "job_interrupted")

    def test_existing_operation_error_codes_all_reviewed(self):
        import ast
        source = ast.parse(Path(ops.__file__).read_bytes())
        for node in ast.walk(source):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "require"
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)):
                self.assertIn(node.args[1].value, ops.OPERATION_CODES)


class FailureDiagnosticsPipelineTests(unittest.TestCase):
    def setUp(self):
        self.case = OperationsTests(methodName="runTest")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.job = self.case.job

    def failure(self, code, step):
        output = io.StringIO()
        with patch("sys.stdout", output), self.assertRaises(BaseException):
            self.job.run()
        attempt = ops.state_read(self.job.state / "attempt.json")
        self.assertEqual(attempt["status"], "FAILED")
        self.assertEqual(attempt["step"], step)
        self.assertEqual(attempt["error"]["code"], code)
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertFalse(self.case.upload.calls)
        self.assertEqual(list(self.job.work.iterdir()), [])
        self.assertNotIn("synthetic-secret", output.getvalue() + str(attempt))
        return attempt, output.getvalue()

    def test_reproduce_old_generic_error_and_lost_failure_details(self):
        root = Path(__file__).resolve().parent
        raw = subprocess.check_output(["git", "-C", str(root), "show",
            "91c5ad2cd8127b5936b96cdf230883feb4162b00:rove_recovery_operations.py"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "ce909dbf5d65e0aab37a2daaaf14643733fdca70b98a37d255afe33c774f2652")
        old = types.ModuleType("synthetic_previous_operations")
        old.__file__ = ops.__file__
        exec(compile(raw, "synthetic_previous_operations", "exec"), old.__dict__)
        def failure():
            raise PermissionError(errno.EACCES, "synthetic-secret-cause")
        self.case.config["recipients"] = {"telegram_chat_id": "123", "email": "operator@example.invalid",
                                         "from_email": "sender@example.invalid"}
        job = old.Operations(self.case.config, self.case.alerts, clients=failure, preflight=lambda config: None)
        owner, first = os.geteuid(), iter([0])
        output = io.StringIO()
        with patch.object(old, "load_config", return_value=self.case.config), \
             patch.object(old, "Operations", return_value=job), \
             patch.object(old.os, "geteuid", side_effect=lambda: next(first, owner)), \
             patch.object(old.sys, "argv", ["ops", "run", "--config", "synthetic.json"]), \
             patch("sys.stdout", output):
            self.assertEqual(old.main(), 1)
        self.assertIn("CODE=local_or_dependency_failure", output.getvalue())
        attempt = old.state_read(job.state / "attempt.json")
        self.assertEqual(attempt["stage"], "COLLECTION")
        self.assertNotIn("error", attempt)
        self.assertNotIn("step", attempt)
        self.assertEqual(list(job.work.iterdir()), [])
        self.assertFalse((job.state / "success.json").exists())

    def test_sdk_import_failure_classified_before_collector_or_external_requests(self):
        def unavailable():
            raise ModuleNotFoundError("synthetic-secret-module")
        self.job.clients = unavailable
        attempt, _ = self.failure("dependency_import_failed", "AWS_CLIENT_SETUP")
        self.assertEqual(attempt["error"]["category"], "DEPENDENCY")
        self.assertFalse(self.case.fixture.sdk.calls)

    def test_credential_failure_classified_before_collector(self):
        self.job.clients = lambda: (_ for _ in ()).throw(ops.OperationError("systemd_credentials_missing"))
        attempt, _ = self.failure("systemd_credentials_missing", "AWS_CLIENT_SETUP")
        self.assertEqual(attempt["error"]["category"], "CREDENTIAL_LOADING")

    def test_trust_client_endpoint_failure_has_own_step(self):
        self.case.fixture.sdk.meta.endpoint_url = "https://synthetic.invalid"
        self.failure("lambda_endpoint_invalid", "TRUST_CLIENT_SETUP")
        self.assertFalse(self.case.fixture.sdk.calls)

    def test_direct_collector_permission_failure_retained_after_cleanup(self):
        def denied(**kwargs):
            raise PermissionError(errno.EACCES, "synthetic-secret-path")
        self.job.collect = denied
        self.failure("file_permission_denied", "COLLECTOR")

    def test_failed_manifest_original_code_retained_before_cleanup(self):
        with patch.object(recovery, "create_verified_backup", side_effect=PermissionError(errno.EACCES, "synthetic-secret-path")):
            attempt, output = self.failure("file_permission_denied", "SET_VALIDATION")
        self.assertEqual(attempt["error"]["category"], "LOCAL_PATH_PERMISSION")
        self.assertIn('"code": "file_permission_denied"', output)

    def test_failed_manifest_secret_payload_not_logged(self):
        def rejected(**kwargs):
            path = ops.private_dir(kwargs["output"].parent / "synthetic-failed")
            recovery.private_write(path / "manifest.json", recovery.json_bytes({"status": "FAILED",
                "manifest_version": recovery.MANIFEST_VERSION, "tool_version": recovery.TOOL_VERSION,
                "error_code": "synthetic-secret-token"}))
            return path
        self.job.collect = rejected
        self.failure("set_not_complete", "SET_VALIDATION")

    def test_unknown_error_remains_unknown_and_never_successful(self):
        self.job.clients = lambda: (_ for _ in ()).throw(RuntimeError("synthetic-secret-payload"))
        self.failure("unclassified_local_failure", "AWS_CLIENT_SETUP")

    def test_cli_reports_specific_code_not_the_old_aggregate(self):
        self.case.config["recipients"] = {"telegram_chat_id": "123", "email": "operator@example.invalid",
                                         "from_email": "sender@example.invalid"}
        self.job.clients = lambda: (_ for _ in ()).throw(ModuleNotFoundError("synthetic-secret-module"))
        owner, first = os.geteuid(), iter([0])
        output = io.StringIO()
        with patch.object(ops, "load_config", return_value=self.case.config), \
             patch.object(ops, "Operations", return_value=self.job), \
             patch.object(ops.os, "geteuid", side_effect=lambda: next(first, owner)), \
             patch.object(ops.sys, "argv", ["ops", "run", "--config", "synthetic.json"]), \
             patch("sys.stdout", output):
            self.assertEqual(ops.main(), 1)
        self.assertIn("CODE=dependency_import_failed", output.getvalue())
        self.assertNotIn("local_or_dependency_failure", output.getvalue())
        self.assertNotIn("synthetic-secret", output.getvalue())
        self.assertFalse((self.job.state / "success.json").exists())

    def test_real_gate_rejection_no_crypto_no_put(self):
        with patch.object(recovery, "check_account_restore_generation", side_effect=recovery.RecoveryError("generation_declared_unsafe")):
            self.failure("generation_declared_unsafe", "MANIFEST_RECEIPT_GATE")

    def test_error_state_persisted_before_alert_transport_can_fail(self):
        self.job.clients = lambda: (_ for _ in ()).throw(ModuleNotFoundError("synthetic-secret-module"))
        self.case.alerts.notify = lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic-secret-provider-response"))
        self.failure("dependency_import_failed", "AWS_CLIENT_SETUP")


class CleanupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.case = OperationsTests(methodName="runTest")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.job = self.case.job

    def records(self, output):
        self.assertNotIn("synthetic-secret", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("OFFSITE_RUN=SUCCESS", output)
        return [trust.strict_json(line.split("=", 1)[1].encode()) for line in output.splitlines()
                if line.startswith("RECOVERY_FAILURE=")]

    def test_old_finally_cleanup_replaces_primary_exception(self):
        root = Path(__file__).resolve().parent
        raw = subprocess.check_output(["git", "-C", str(root), "show",
            "91c5ad2cd8127b5936b96cdf230883feb4162b00:rove_recovery_operations.py"])
        old = types.ModuleType("synthetic_old_cleanup")
        exec(compile(raw, "synthetic_old_cleanup", "exec"), old.__dict__)
        primary = ModuleNotFoundError("synthetic-secret-primary")
        secondary = PermissionError(errno.EACCES, "synthetic-secret-cleanup")
        job = old.Operations(self.case.config, self.case.alerts,
            clients=lambda: (_ for _ in ()).throw(primary), preflight=lambda config: None)
        with patch.object(job, "clean_work", side_effect=secondary), patch("sys.stdout", io.StringIO()), \
             self.assertRaises(PermissionError) as caught:
            job.run()
        self.assertIs(caught.exception, secondary)
        self.assertIs(caught.exception.__context__, primary)

    def test_primary_code_stage_and_exception_survive_cleanup_failure(self):
        primary = ModuleNotFoundError("synthetic-secret-primary")
        self.job.clients = lambda: (_ for _ in ()).throw(primary)
        output = io.StringIO()
        with patch.object(self.job, "clean_work", side_effect=OSError(errno.EROFS, "synthetic-secret-cleanup")) as clean, \
             patch("sys.stdout", output), self.assertRaises(ModuleNotFoundError) as caught:
            self.job.run()
        self.assertIs(caught.exception, primary)
        self.assertEqual(clean.call_count, 1)
        attempt = ops.state_read(self.job.state / "attempt.json")
        self.assertEqual((attempt["status"], attempt["stage"], attempt["step"]),
                         ("FAILED", "COLLECTION", "AWS_CLIENT_SETUP"))
        self.assertEqual(attempt["error"]["code"], "dependency_import_failed")
        self.assertEqual(attempt["cleanup_error"]["code"], "readonly_filesystem")
        records = self.records(output.getvalue())
        self.assertEqual(records[0]["code"], "dependency_import_failed")
        self.assertEqual((records[1]["step"], records[1]["secondary"]), ("CLEANUP", True))
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertFalse(self.case.upload.calls or self.case.fixture.sdk.calls)

    def test_failed_collector_manifest_code_survives_cleanup_failure(self):
        output = io.StringIO()
        with patch.object(recovery, "create_verified_backup", side_effect=PermissionError(errno.EACCES, "synthetic-secret")), \
             patch.object(self.job, "clean_work", side_effect=OSError(errno.ENOSPC, "synthetic-secret")), \
             patch("sys.stdout", output), self.assertRaises(recovery.RecoveryError):
            self.job.run()
        attempt = ops.state_read(self.job.state / "attempt.json")
        self.assertEqual(attempt["step"], "SET_VALIDATION")
        self.assertEqual(attempt["error"]["code"], "file_permission_denied")
        self.assertEqual(attempt["cleanup_error"]["code"], "filesystem_full")
        self.assertEqual(self.records(output.getvalue())[0]["code"], "file_permission_denied")
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertFalse(self.case.upload.calls)

    def test_state_and_alert_failures_cannot_mask_primary_or_cleanup_diagnostic(self):
        primary = ModuleNotFoundError("synthetic-secret-primary")
        self.job.clients = lambda: (_ for _ in ()).throw(primary)
        self.case.alerts.notify = lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic-secret-response"))
        write = ops.atomic_state
        def denied(path, value):
            if value.get("status") == "FAILED":
                raise PermissionError(errno.EACCES, "synthetic-secret-state")
            return write(path, value)
        output = io.StringIO()
        with patch.object(ops, "atomic_state", side_effect=denied), \
             patch.object(self.job, "clean_work", side_effect=OSError(errno.EROFS, "synthetic-secret-cleanup")), \
             patch("sys.stdout", output), self.assertRaises(ModuleNotFoundError) as caught:
            self.job.run()
        self.assertIs(caught.exception, primary)
        records = self.records(output.getvalue())
        self.assertEqual(records[0]["code"], "dependency_import_failed")
        self.assertEqual([r["step"] for r in records],
                         ["AWS_CLIENT_SETUP", "FAILURE_STATE", "FAILURE_ALERT", "CLEANUP", "FAILURE_STATE"])
        self.assertTrue(all(r["secondary"] for r in records[1:]))
        self.assertFalse((self.job.state / "success.json").exists())

    def test_initial_attempt_write_failure_is_fail_closed_and_work_is_cleaned(self):
        output = io.StringIO()
        with patch.object(ops, "atomic_state", side_effect=OSError(errno.ENOSPC, "synthetic-secret")), \
             patch("sys.stdout", output), self.assertRaises(OSError):
            self.job.run()
        records = self.records(output.getvalue())
        self.assertEqual(records[0]["code"], "filesystem_full")
        self.assertEqual(records[0]["step"], "AWS_CLIENT_SETUP")
        self.assertEqual(list(self.job.work.iterdir()), [])
        self.assertFalse(self.case.upload.calls or self.case.fixture.sdk.calls)
        self.assertFalse((self.job.state / "success.json").exists())

    def test_preflight_primary_survives_state_and_alert_errors(self):
        primary = ops.OperationError("operator_alert_delivery_not_verified")
        self.job.preflight = lambda config: (_ for _ in ()).throw(primary)
        self.case.alerts.notify = lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic-secret-response"))
        output = io.StringIO()
        with patch.object(ops, "atomic_state", side_effect=OSError(errno.ENOSPC, "synthetic-secret-state")), \
             patch("sys.stdout", output), self.assertRaises(ops.OperationError) as caught:
            self.job.run()
        self.assertIs(caught.exception, primary)
        records = self.records(output.getvalue())
        self.assertEqual((records[0]["stage"], records[0]["code"]),
                         ("PREFLIGHT", "operator_alert_delivery_not_verified"))
        self.assertFalse(self.case.upload.calls or self.case.fixture.sdk.calls)
        self.assertEqual(list(self.job.work.iterdir()), [])

    def test_cleanup_failure_after_verification_never_publishes_success_or_retries(self):
        output = io.StringIO()
        with patch.object(self.job, "clean_work", side_effect=PermissionError(errno.EACCES, "synthetic-secret")) as clean, \
             patch("sys.stdout", output), self.assertRaises(PermissionError):
            self.job.run()
        self.assertEqual(clean.call_count, 1)
        self.assertEqual(len(self.case.upload.calls), 1)
        attempt = ops.state_read(self.job.state / "attempt.json")
        self.assertEqual((attempt["status"], attempt["stage"], attempt["step"]),
                         ("FAILED", "DURABLE_SUCCESS", "CLEANUP"))
        self.assertEqual(self.records(output.getvalue())[0]["code"], "file_permission_denied")
        self.assertFalse((self.job.state / "success.json").exists())
        for path, original in self.case.inputs_before.items():
            self.assertEqual(path.read_bytes(), original)

    def test_previous_success_is_not_replaced_by_cleanup_failed_run(self):
        with patch("sys.stdout", io.StringIO()):
            self.job.run()
        original = (self.job.state / "success.json").read_bytes()
        output = io.StringIO()
        with patch.object(self.job, "clean_work", side_effect=PermissionError(errno.EACCES, "synthetic-secret")), \
             patch("sys.stdout", output), self.assertRaises(PermissionError):
            self.job.run()
        self.records(output.getvalue())
        self.assertEqual((self.job.state / "success.json").read_bytes(), original)
        self.assertEqual(self.job.watch(check_timer=lambda: True), "CRITICAL")

    def test_success_is_published_only_after_cleanup_with_same_authority_proofs(self):
        clean = self.job.clean_work
        def checked(path):
            self.assertFalse((self.job.state / "success.json").exists())
            self.assertEqual(ops.state_read(self.job.state / "attempt.json")["step"], "CLEANUP")
            self.assertEqual(len(self.case.upload.calls), 1)
            return clean(path)
        with patch.object(self.job, "clean_work", side_effect=checked) as observed, patch("sys.stdout", io.StringIO()):
            success = self.job.run()
        self.assertEqual(observed.call_count, 1)
        self.assertEqual(success, ops.state_read(self.job.state / "success.json"))
        self.assertEqual(ops.state_read(self.job.state / "attempt.json")["status"], "SUCCESS")
        self.assertTrue(success["manifest_receipt"] and success["crypto_receipt"] and success["external_verifier"])
        self.assertEqual(list(self.job.work.iterdir()), [])

    def test_cli_primary_code_not_overridden_by_secondary_cleanup_error(self):
        self.case.config["recipients"] = {"telegram_chat_id": "123", "email": "operator@example.invalid",
                                         "from_email": "sender@example.invalid"}
        self.job.clients = lambda: (_ for _ in ()).throw(ModuleNotFoundError("synthetic-secret-module"))
        owner, first = os.geteuid(), iter([0])
        output = io.StringIO()
        with patch.object(ops, "load_config", return_value=self.case.config), \
             patch.object(ops, "Operations", return_value=self.job), \
             patch.object(self.job, "clean_work", side_effect=OSError(errno.EROFS, "synthetic-secret")), \
             patch.object(ops.os, "geteuid", side_effect=lambda: next(first, owner)), \
             patch.object(ops.sys, "argv", ["ops", "run", "--config", "synthetic.json"]), patch("sys.stdout", output):
            self.assertEqual(ops.main(), 1)
        self.records(output.getvalue())
        self.assertTrue(output.getvalue().rstrip().endswith("RECOVERY_OPERATIONS=STOP; CODE=dependency_import_failed"))

    def test_unsafe_and_unknown_gate_rejections_remain_fail_closed_even_if_cleanup_fails(self):
        for code in ("generation_declared_unsafe", "generation_safety_unknown"):
            output = io.StringIO()
            with self.subTest(code=code), \
                 patch.object(recovery, "check_account_restore_generation", side_effect=recovery.RecoveryError(code)), \
                 patch.object(self.job, "clean_work", side_effect=OSError(errno.EROFS, "synthetic-secret")), \
                 patch("sys.stdout", output), self.assertRaises(recovery.RecoveryError):
                self.job.run()
            attempt = ops.state_read(self.job.state / "attempt.json")
            self.assertEqual((attempt["stage"], attempt["error"]["code"]), ("MANIFEST_RECEIPT_GATE", code))
            self.records(output.getvalue())
            self.assertFalse(self.case.upload.calls)
            self.assertFalse((self.job.state / "success.json").exists())
            for path in self.job.work.iterdir():
                self.job.clean_work(path)

    def test_non_diagnostic_security_functions_are_ast_identical_to_old_release(self):
        import ast
        root = Path(__file__).resolve().parent
        raw = subprocess.check_output(["git", "-C", str(root), "show",
            "91c5ad2cd8127b5936b96cdf230883feb4162b00:rove_recovery_operations.py"])
        def functions(source):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef)}
        old, current = functions(raw), functions(Path(ops.__file__).read_bytes())
        for name in ("credential", "aws_clients", "load_config", "verify_release", "validate_inputs",
                     "conditional_put", "freshness", "timer_active"):
            with self.subTest(name=name):
                self.assertEqual(current[name], old[name])


class OperatorTransportTests(unittest.TestCase):
    def alerts(self, send):
        return OperatorAlerts({"telegram_chat_id": "123", "email": "operator@example.invalid",
            "from_email": "sender@example.invalid"}, lambda name: "123:synthetic" if name == "TELEGRAM_TOKEN" else "synthetic", send=send)

    def test_both_provider_acceptances_required(self):
        def send(url, body, headers):
            return {"ok": True, "result": {"message_id": 1, "chat": {"id": 123}}} if "telegram" in url else {"messageId": "synthetic"}
        self.assertEqual(self.alerts(send).notify("TEST", "DELIVERY", "A"), {"telegram": True, "email": True})

    def test_first_channel_error_does_not_suppress_second_or_leak(self):
        calls = []
        def send(url, body, headers):
            calls.append(url)
            if "telegram" in url:
                raise RuntimeError("secret token-bearing URL")
            return {"messageId": "synthetic"}
        self.assertEqual(self.alerts(send).notify("FAILURE", "ENCRYPTION", "A"), {"telegram": False, "email": True})
        self.assertEqual(len(calls), 2)

    def test_arbitrary_exception_text_cannot_enter_alert(self):
        with self.assertRaises(AlertError):
            self.alerts(lambda *args: {}).notify("FAILURE", "secret=unsafe value", "A")

    def test_telegram_wrong_chat_or_missing_message_is_not_accepted(self):
        for message in ({}, {"message_id": 1, "chat": {"id": 999}}):
            def send(url, body, headers):
                return {"ok": True, "result": message} if "telegram" in url else {"messageId": "synthetic"}
            self.assertFalse(self.alerts(send).notify("TEST", "DELIVERY", "A")["telegram"])


class ReleaseTests(unittest.TestCase):
    def test_export_exact_commit_and_frozen_hash_parity(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            for relative in RELEASE_FILES:
                dest = repo / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, dest)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q"); git("config", "user.name", "Synthetic"); git("config", "user.email", "test@example.invalid")
            git("add", "."); git("commit", "-qm", "synthetic release")
            commit = git("rev-parse", "HEAD")
            output = Path(directory) / "release"
            manifest = build(repo, commit, output, app_commit=commit, trust_commit=commit)
            try:
                self.assertEqual(set(manifest["files"]), set(RELEASE_FILES))
                self.assertEqual((output / "rove_recovery_operations.py").stat().st_mode & 0o777, 0o444)
                self.assertEqual(output.stat().st_mode & 0o777, 0o555)
                (repo / "rove_recovery_trust.py").write_bytes(b"modified")
                git("add", "."); git("commit", "-qm", "incompatible")
                with self.assertRaisesRegex(RuntimeError, "frozen_code_changed"):
                    build(repo, git("rev-parse", "HEAD"), Path(directory) / "rejected", app_commit=commit, trust_commit=commit)
                with self.assertRaises(RuntimeError):
                    build(repo, commit, output, app_commit=commit, trust_commit=commit)
            finally:
                for path in [output, *(p for p in output.rglob("*") if p.is_dir())]:
                    path.chmod(0o700)

    def test_six_utc_runs_and_separate_watchdog_without_api_restart(self):
        root = Path(__file__).resolve().parent / "deploy/systemd"
        timer = (root / "rove-recovery-offsite.timer").read_text()
        self.assertIn("00,04,08,12,16,20:20:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("OnUnitInactiveSec=5min", (root / "rove-recovery-watch.timer").read_text())
        for name in ("rove-recovery-offsite.service", "rove-recovery-watch.service"):
            unit = (root / name).read_text()
            self.assertIn("ProtectSystem=strict", unit)
            self.assertIn("LoadCredential=BREVO_API_KEY", unit)
            self.assertNotIn("systemctl restart", unit)
            self.assertNotIn("EnvironmentFile=/root/clarity", unit)

    def test_actual_sdk_put_model_and_stubbed_conditional_request(self):
        import boto3
        from botocore.stub import Stubber, ANY
        pins, _ = fixtures.configuration()
        client = boto3.client("s3", region_name="eu-central-1", aws_access_key_id="synthetic",
                              aws_secret_access_key="synthetic")
        self.addCleanup(client.close)
        raw = b"synthetic encrypted bytes"
        checksum = base64.b64encode(hashlib.sha256(raw).digest()).decode()
        generation = "recovery_set_20261008T000000Z_" + "1" * 32
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / (generation + ".tar.gz.gpg")
            path.write_bytes(raw)
            expected = dict(Bucket=pins.recovery_bucket, Key=f"recovery/{generation}.tar.gz.gpg", Body=ANY,
                ContentLength=len(raw), ContentType="application/octet-stream", ChecksumSHA256=checksum,
                IfNoneMatch="*", ExpectedBucketOwner=pins.account_id)
            with Stubber(client) as stub:
                stub.add_response("put_object", {"VersionId": "exact-version", "ChecksumSHA256": checksum,
                    "ResponseMetadata": {"HTTPStatusCode": 200}}, expected)
                self.assertEqual(ops.conditional_put(client, pins, path, {"recovery_set_id": generation,
                    "ciphertext_sha256": trust.sha(raw), "ciphertext_bytes": len(raw)}), "exact-version")
                stub.assert_no_pending_responses()


class RealGPGOperationsTests(unittest.TestCase):
    def test_real_synthetic_collection_receipts_encryption_put_verifier_and_cleanup(self):
        fixture = OperationsTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gpg = shutil.which("gpg")
        self.assertIsNotNone(gpg, "Real GPG mandatory; no skip or fallback")
        with tempfile.TemporaryDirectory(prefix="rvo-", dir="/private/tmp") as directory:
            home = Path(directory)
            with crypto.gpg_session(gpg, temp_parent=home) as client:
                client.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0", "--quick-generate-key",
                    "Synthetic Operations <operations@example.invalid>", "rsa2048", "encr", "1d"],
                    secret=b"synthetic-test-only\n", agent=True)
                listing = client.run(["--with-colons", "--list-keys"])
                fingerprint = next(row.split(b":")[9].decode() for row in listing.splitlines() if row.startswith(b"fpr:"))
                fixture.public.write_bytes(client.run(["--armor", "--export", fingerprint]))
            pins = replace(fixture.config["pins"], public_key_fingerprint=fingerprint)
            fixture.config.update(pins=pins, public_key_sha256=trust.sha(fixture.public.read_bytes()),
                                  gpg=Path(gpg), gpg_temp_parent=home)
            s3 = fixtures.FakeS3(pins)
            sdk = fixtures.FakeLambda(pins, fixture.fixture.policy, s3)
            uploader = Upload(s3)
            fixture.job.clients = lambda: (uploader, sdk)
            fixture.job.encrypt = crypto.encrypt_set
            success = fixture.job.run()
            self.assertEqual(success["status"], "SUCCESS")
            self.assertGreater(success["ciphertext_bytes"], 0)
            self.assertEqual(success["external_verifier"]["version_id"], "exact-version")
            self.assertEqual(list(fixture.job.work.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
