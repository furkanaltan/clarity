"""Synthetic orchestration, real canonical gates; never contact AWS/Production."""

import base64
import copy
import errno
import hashlib
import io
import json
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


def in_process_cli(module=ops):
    # The real CLI exits its process; these tests must not leak its umask to other tests.
    previous = os.umask(0o077)
    try:
        return module.main()
    finally:
        os.umask(previous)


class CLIIsolationTests(unittest.TestCase):
    def test_cli_hardens_its_umask_without_leaking_to_subsequent_tests(self):
        original = os.umask(0o022)
        self.addCleanup(os.umask, original)
        for mask in (0o022, 0o077):
            with self.subTest(umask=oct(mask)):
                os.umask(mask)
                def rejected_config(path):
                    self.assertEqual(os.umask(0o077), 0o077)
                    raise ops.OperationError("configuration_invalid")
                with patch.object(ops, "load_config", side_effect=rejected_config), \
                     patch.object(ops.os, "geteuid", return_value=0), \
                     patch.object(ops.sys, "argv", ["ops", "run", "--config", "synthetic.json"]), \
                     patch("sys.stdout", io.StringIO()):
                    self.assertEqual(in_process_cli(), 1)
                self.assertEqual(os.umask(mask), mask)


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
            preflight=lambda config: None, credentials_binding=lambda: "d" * 64)
        self.activate()
        self.inputs_before = {path: path.read_bytes() for path in
                              (f.db, f.ledger, f.inventory_path, self.fixture.policy_path)}

    def activate(self):
        with patch("sys.stdout", io.StringIO()):
            self.job.confirm_alerts(self.job.probe_alerts())
        self.alerts.calls.clear()

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
        (self.job.state / "alert-history-head.json").unlink()
        self.blocked()
        self.assertFalse(self.upload.calls)

    def test_changed_credential_binding_after_verifier_never_publishes_success(self):
        original = self.fixture.sdk.mutate
        def verify(value):
            if value.get("status") == "PASS" and "version_id" in value:
                self.job.credentials_binding = lambda: "0" * 64
            return original(value)
        self.fixture.sdk.mutate = verify
        self.blocked()
        self.assertEqual(ops.state_read(self.job.state / "attempt.json")["stage"], "EXTERNAL_VERIFIER")

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
        identifier = self.job.probe_alerts()
        _, probes, _ = self.job.alert_history()
        self.assertIsNone(probes[identifier]["confirmation"])
        self.job.confirm_alerts(identifier)
        self.job.activation()

    def test_failed_alert_probe_cannot_authorize_activation(self):
        self.alerts.delivery["telegram"] = False
        with self.assertRaises(ops.OperationError):
            self.job.probe_alerts()
        _, probes, _ = self.job.alert_history()
        identifier = next(reversed(probes))
        with self.assertRaises(ops.OperationError):
            self.job.confirm_alerts(identifier)

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
            self.assertEqual(in_process_cli(), 1)
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


class AlertRenewalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rva-", dir="/private/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.credentials = ops.private_dir(self.root / "credentials")
        os.chown(self.credentials, -1, os.getegid())
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_TOKEN", "BREVO_API_KEY"):
            recovery.private_write(self.credentials / name, b"synthetic-secret-never-log")
        self.config = {"state_root": self.root / "state", "work_root": self.root / "work",
                       "config_sha256": "a" * 64, "release_commit": "b" * 40}
        self.alerts = Alerts()
        self.job = ops.Operations(self.config, self.alerts,
                                  credentials_binding=lambda: ops.credential_binding(self.credentials))
        self.output = io.StringIO()
        self.stdout = patch("sys.stdout", self.output)
        self.stdout.start()
        self.addCleanup(self.stdout.stop)

    def activate(self):
        identifier = self.job.probe_alerts()
        self.job.confirm_alerts(identifier)
        self.job.activation()
        return identifier

    def evidence(self):
        return {path.relative_to(self.job.state): path.read_bytes()
                for path in self.job.state.rglob("*") if path.is_file() and path.name != "watch.lock"}

    def unchanged_evidence(self, before):
        after = self.evidence()
        for name, raw in before.items():
            if name != Path("alert-history-head.json"):
                self.assertEqual(after[name], raw)

    def test_first_activation_requires_provider_and_operator_receipt(self):
        with self.assertRaisesRegex(ops.OperationError, "operator_alert_delivery_not_verified"):
            self.job.activation()
        identifier = self.job.probe_alerts()
        with self.assertRaises(ops.OperationError):
            self.job.activation()
        self.job.confirm_alerts(identifier)
        self.job.activation()
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertFalse((self.job.state / "activation.json").exists())

    def test_renewal_preserves_history_and_selects_confirmed_probe(self):
        old = self.activate()
        before = self.evidence()
        new = self.job.probe_alerts()
        self.assertNotEqual(old, new)
        self.assertEqual(self.job.alert_history()[2]["request"]["probe_id"], old)
        self.job.confirm_alerts(new)
        self.assertEqual(self.job.alert_history()[2]["request"]["probe_id"], new)
        self.unchanged_evidence(before)
        self.assertEqual(len(self.alerts.calls), 2)

    def test_legacy_evidence_retained_exactly_without_implicit_activation(self):
        for name in ("alert-probe.json", "activation.json"):
            recovery.private_write(self.job.state / name, b'{"legacy": "unchanged"}\n')
        before = self.evidence()
        with self.assertRaises(ops.OperationError):
            self.job.activation()
        self.activate()
        self.unchanged_evidence(before)
        self.assertEqual(self.job.alert_history()[0][0]["value"], self.job.legacy_alert_hashes())

    def test_changed_or_missing_legacy_evidence_blocks_without_sending(self):
        path = self.job.state / "activation.json"
        recovery.private_write(path, b"original")
        self.activate()
        path.unlink()
        before = len(self.alerts.calls)
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.probe_alerts()
        self.assertEqual(len(self.alerts.calls), before)

    def test_confirmed_activation_survives_normal_four_hour_timer_interval(self):
        when = ops.now()
        with patch.object(ops, "now", return_value=when):
            self.activate()
        with patch.object(ops, "now", return_value=when + timedelta(hours=4)):
            self.job.activation()

    def test_expired_probe_rejected_and_new_probe_allowed(self):
        when = ops.now()
        with patch.object(ops, "now", return_value=when):
            identifier = self.job.probe_alerts()
        before = self.evidence()
        with patch.object(ops, "now", return_value=when + timedelta(seconds=3601)):
            with self.assertRaisesRegex(ops.OperationError, "alert_probe_not_current"):
                self.job.confirm_alerts(identifier)
            self.activate()
        self.unchanged_evidence(before)

    def test_future_probe_cannot_be_confirmed(self):
        when = ops.now()
        with patch.object(ops, "now", return_value=when + timedelta(seconds=1)):
            identifier = self.job.probe_alerts()
        with patch.object(ops, "now", return_value=when), self.assertRaises(ops.OperationError):
            self.job.confirm_alerts(identifier)

    def test_double_confirmation_is_rejected_without_writes(self):
        identifier = self.activate()
        before = self.evidence()
        with self.assertRaisesRegex(ops.OperationError, "alert_probe_not_current"):
            self.job.confirm_alerts(identifier)
        self.assertEqual(self.evidence(), before)

    def test_wrong_probe_id_is_rejected_without_writes(self):
        self.job.probe_alerts()
        before = self.evidence()
        for identifier in ("0" * 32, "../activation", "short", None):
            with self.subTest(identifier=identifier), self.assertRaises(ops.OperationError):
                self.job.confirm_alerts(identifier)
        self.assertEqual(self.evidence(), before)

    def test_config_and_release_changes_invalidate_both_confirmation_and_activation(self):
        self.activate()
        identifier = self.job.probe_alerts()
        before = self.evidence()
        for key, changed in (("config_sha256", "0" * 64), ("release_commit", "0" * 40)):
            with self.subTest(key=key), patch.dict(self.config, {key: changed}):
                with self.assertRaises(ops.OperationError):
                    self.job.confirm_alerts(identifier)
                with self.assertRaises(ops.OperationError):
                    self.job.activation()
        self.assertEqual(self.evidence(), before)

    def test_each_credential_change_invalidates_activation_without_secret_reads(self):
        self.activate()
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_TOKEN", "BREVO_API_KEY"):
            with self.subTest(name=name):
                path = self.credentials / name
                before = path.stat()
                path.write_bytes(b"x" * before.st_size)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual(path.stat().st_size, before.st_size)
                self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                with patch.object(Path, "read_bytes", side_effect=AssertionError("no secret reads")):
                    ops.credential_binding(self.credentials)
                with self.assertRaises(ops.OperationError):
                    self.job.activation()
                self.activate()
        self.assertNotIn("synthetic-secret", self.output.getvalue() + str(self.evidence()))
        self.assertNotIn("changed-secret", self.output.getvalue() + str(self.evidence()))

    def test_changed_credentials_before_confirmation_are_rejected(self):
        identifier = self.job.probe_alerts()
        (self.credentials / "TELEGRAM_TOKEN").write_bytes(b"changed")
        with self.assertRaisesRegex(ops.OperationError, "alert_probe_not_current"):
            self.job.confirm_alerts(identifier)
        with self.assertRaises(ops.OperationError):
            self.job.activation()

    def test_changed_credentials_during_send_cannot_produce_confirmation(self):
        def changed(*args):
            (self.credentials / "BREVO_API_KEY").write_bytes(b"changed")
            return {"telegram": True, "email": True}
        self.alerts.notify = changed
        with self.assertRaisesRegex(ops.OperationError, "credential_binding_changed"):
            self.job.probe_alerts()
        _, probes, proof = self.job.alert_history()
        self.assertIsNone(proof)
        self.assertTrue(all(probe["result"] is None for probe in probes.values()))

    def test_failed_delivery_preserves_previous_activation_but_never_confirms_new_probe(self):
        old = self.activate()
        before = self.evidence()
        self.alerts.delivery["email"] = False
        with self.assertRaisesRegex(ops.OperationError, "alert_probe_failed"):
            self.job.probe_alerts()
        _, probes, proof = self.job.alert_history()
        identifier = next(reversed(probes))
        self.assertEqual(proof["request"]["probe_id"], old)
        with self.assertRaises(ops.OperationError):
            self.job.confirm_alerts(identifier)
        self.job.activation()
        self.unchanged_evidence(before)

    def test_duplicate_probe_id_never_sends_twice(self):
        identifier = self.activate()
        before = self.evidence()
        with patch.object(ops.uuid, "uuid4", return_value=types.SimpleNamespace(hex=identifier.lower())):
            with self.assertRaisesRegex(ops.OperationError, "alert_evidence_exists"):
                self.job.probe_alerts()
        self.assertEqual(len(self.alerts.calls), 1)
        self.assertEqual(self.evidence(), before)

    def test_new_probe_after_failed_delivery_is_confirmable_without_reset(self):
        self.activate()
        self.alerts.delivery["telegram"] = False
        with self.assertRaises(ops.OperationError):
            self.job.probe_alerts()
        before = self.evidence()
        self.alerts.delivery["telegram"] = True
        self.activate()
        self.unchanged_evidence(before)

    def test_interrupted_publish_before_link_preserves_valid_activation(self):
        self.activate()
        before = self.evidence()
        with patch.object(ops.os, "link", side_effect=OSError("synthetic-secret")):
            with self.assertRaises(OSError):
                self.job.probe_alerts()
        self.assertEqual(self.evidence(), before)
        self.job.activation()
        self.assertEqual(len(self.alerts.calls), 1)

    def test_interrupted_head_write_after_publication_fails_closed(self):
        self.activate()
        before = self.evidence()
        with patch.object(ops, "atomic_state", side_effect=OSError("synthetic-secret")):
            with self.assertRaises(OSError):
                self.job.probe_alerts()
        self.unchanged_evidence(before)
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.activation()
        self.assertEqual(len(self.alerts.calls), 1)

    def test_interrupted_confirmation_cannot_fake_activation(self):
        identifier = self.job.probe_alerts()
        with patch.object(ops.os, "link", side_effect=OSError("synthetic-secret")):
            with self.assertRaises(OSError):
                self.job.confirm_alerts(identifier)
        with self.assertRaises(ops.OperationError):
            self.job.activation()
        self.assertFalse((self.job.state / "success.json").exists())

    def test_deleted_history_record_invalidates_activation(self):
        self.activate()
        (self.job.state / "alert-history" / "00000002.json").unlink()
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.activation()

    def test_deleted_head_invalidates_activation_without_reinitialization(self):
        self.activate()
        (self.job.state / "alert-history-head.json").unlink()
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.probe_alerts()
        self.assertEqual(len(self.alerts.calls), 1)

    def test_null_head_is_not_treated_as_a_new_history(self):
        recovery.private_write(self.job.state / "alert-history-head.json", b"null\n")
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.probe_alerts()
        self.assertFalse(self.alerts.calls)

    def test_tampered_record_invalidates_activation(self):
        self.activate()
        path = self.job.state / "alert-history" / "00000002.json"
        value = json.loads(path.read_bytes())
        value["value"]["config_sha256"] = "c" * 64
        path.write_bytes(recovery.json_bytes(value))
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.activation()

    def test_tampered_confirmation_semantics_rejected_even_with_matching_head_hash(self):
        self.activate()
        directory = self.job.state / "alert-history"
        path = directory / "00000004.json"
        value = json.loads(path.read_bytes())
        value["value"]["both_received_operator_confirmed"] = False
        path.write_bytes(recovery.json_bytes(value))
        ops.atomic_state(self.job.state / "alert-history-head.json",
                         {"version": 1, "sequence": 4, "sha256": trust.sha(path.read_bytes())})
        with self.assertRaisesRegex(ops.OperationError, "alert_history_invalid"):
            self.job.activation()

    def test_unsafe_history_files_and_directories_rejected(self):
        self.activate()
        directory = self.job.state / "alert-history"
        directory.chmod(0o755)
        with self.assertRaises(ops.OperationError):
            self.job.activation()
        directory.chmod(0o700)
        path = directory / "00000002.json"
        path.chmod(0o644)
        with self.assertRaises(ops.OperationError):
            self.job.activation()
        path.chmod(0o600)
        original = path.read_bytes()
        path.unlink()
        target = self.root / "target.json"
        recovery.private_write(target, original)
        path.symlink_to(target)
        with self.assertRaises(recovery.RecoveryError):
            self.job.activation()
        self.assertEqual(target.read_bytes(), original)

    def test_unsafe_missing_symlinked_credentials_rejected(self):
        path = self.credentials / "TELEGRAM_TOKEN"
        path.chmod(0o644)
        with self.assertRaises(ops.OperationError):
            self.job.probe_alerts()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.job.probe_alerts()
        target = self.root / "unread-secret"
        recovery.private_write(target, b"secret")
        path.symlink_to(target)
        with self.assertRaises(recovery.RecoveryError):
            self.job.probe_alerts()
        self.assertFalse(self.alerts.calls)

    def test_concurrent_watch_lock_blocks_send_and_confirmation(self):
        identifier = self.job.probe_alerts()
        before = self.evidence()
        with ops.exclusive(self.job.state / "watch.lock"):
            with self.assertRaisesRegex(ops.OperationError, "job_already_running"):
                self.job.probe_alerts()
            with self.assertRaisesRegex(ops.OperationError, "job_already_running"):
                self.job.confirm_alerts(identifier)
        self.assertEqual(self.evidence(), before)
        self.assertEqual(len(self.alerts.calls), 1)

    def test_evidence_publication_refuses_existing_file_without_replacement(self):
        path = self.job.state / "evidence.json"
        ops.append_evidence(path, {"original": True})
        raw = path.read_bytes()
        with self.assertRaisesRegex(ops.OperationError, "alert_evidence_exists"):
            ops.append_evidence(path, {"original": False})
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_alarm_history_never_writes_success_or_invokes_pipeline_or_timers(self):
        self.job.clients = self.job.collect = self.job.encrypt = lambda *a, **k: self.fail("pipeline called")
        with patch.object(ops.subprocess, "run", side_effect=AssertionError("no service or timer commands")):
            self.activate()
            self.activate()
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertEqual(list(self.job.work.iterdir()), [])


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

    def failed_candidate_collector(self):
        collect = self.job.collect

        def make_failed_candidate(**kwargs):
            source = collect(**kwargs)
            manifest_path = source / "manifest.json"
            manifest = recovery.strict_json(manifest_path.read_bytes())
            manifest["status"] = "FAILED"
            manifest["error_code"] = "OperationalError"
            manifest["account_restore_safety"] = {
                "status": "UNSAFE_FOR_ACCOUNT_RESTORE", "reason": "OperationalError",
                "gate_version": recovery.GENERATION_GATE_VERSION,
            }
            manifest_path.unlink()
            recovery.private_write(manifest_path, recovery.json_bytes(manifest))
            failed = source.with_name(source.name + ".failed")
            source.rename(failed)
            return failed

        return make_failed_candidate

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
        # The historical implementation needs its historical evidence format.
        ops.atomic_state(job.state / "activation.json", {"both_received_operator_confirmed": True,
            "both_accepted": True, "config_sha256": self.case.config["config_sha256"]})
        owner, first = os.geteuid(), iter([0])
        output = io.StringIO()
        with patch.object(old, "load_config", return_value=self.case.config), \
             patch.object(old, "Operations", return_value=job), \
             patch.object(old.os, "geteuid", side_effect=lambda: next(first, owner)), \
             patch.object(old.sys, "argv", ["ops", "run", "--config", "synthetic.json"]), \
             patch("sys.stdout", output):
            self.assertEqual(in_process_cli(old), 1)
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

    def test_sqlite_diagnostic_runs_before_cleanup_and_cannot_create_success(self):
        events = []

        original_verify = recovery.verify_recovery_set

        def reject_failed_candidate(path, **kwargs):
            if path.name.endswith(".failed"):
                raise recovery.sqlite3.OperationalError("synthetic-secret-sqlite-detail")
            return original_verify(path, **kwargs)

        diagnose = ops.diagnose_failed_sqlite_candidate
        clean = self.job.clean_work

        def diagnostic(*args):
            events.append("diagnostic")
            return diagnose(*args)

        def cleanup(path):
            events.append("cleanup")
            return clean(path)

        output = io.StringIO()
        with patch.object(self.job, "collect", side_effect=self.failed_candidate_collector()), \
             patch.object(recovery, "verify_recovery_set", side_effect=reject_failed_candidate), \
             patch.object(ops, "diagnose_failed_sqlite_candidate", side_effect=diagnostic), \
             patch.object(self.job, "clean_work", side_effect=cleanup), \
             patch("sys.stdout", output), self.assertRaises(recovery.sqlite3.OperationalError):
            self.job.run()

        attempt = ops.state_read(self.job.state / "attempt.json")
        self.assertEqual(attempt["status"], "FAILED")
        self.assertEqual((attempt["stage"], attempt["step"]), ("COLLECTION", "SET_VALIDATION"))
        self.assertEqual(attempt["error"]["code"], "sqlite_operation_failed")
        self.assertEqual(attempt["error"]["sqlite_diagnostic"], {
            "status": "CHECKS_PASS_AFTER_PRIMARY_FAILURE",
            "stage": "POST_FAILURE_READONLY_CHECKS",
            "code": "sqlite_checks_passed_after_primary_error",
            "manifest_trust": "UNVERIFIED",
        })
        self.assertEqual(events, ["diagnostic", "cleanup"])
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertFalse(self.case.upload.calls or self.case.fixture.sdk.calls)
        self.assertEqual(list(self.job.work.iterdir()), [])
        self.assertNotIn("synthetic-secret", output.getvalue() + str(attempt))

    def test_sqlite_diagnostic_failure_is_sanitized_and_primary_error_survives_cleanup_error(self):
        original_verify = recovery.verify_recovery_set

        def reject_failed_candidate(path, **kwargs):
            if path.name.endswith(".failed"):
                raise recovery.sqlite3.OperationalError("synthetic-secret-primary")
            return original_verify(path, **kwargs)

        output = io.StringIO()
        with patch.object(self.job, "collect", side_effect=self.failed_candidate_collector()), \
             patch.object(recovery, "verify_recovery_set", side_effect=reject_failed_candidate), \
             patch.object(ops.sqlite_diagnostics, "diagnose_failed_candidate",
                          side_effect=RuntimeError("synthetic-secret-diagnostic")), \
             patch.object(self.job, "clean_work", side_effect=OSError(errno.EROFS, "synthetic-secret-cleanup")), \
             patch("sys.stdout", output), self.assertRaises(recovery.sqlite3.OperationalError):
            self.job.run()

        attempt = ops.state_read(self.job.state / "attempt.json")
        self.assertEqual(attempt["error"]["code"], "sqlite_operation_failed")
        self.assertEqual(attempt["error"]["sqlite_diagnostic"]["code"], "sqlite_diagnostic_unavailable")
        self.assertEqual(attempt["cleanup_error"]["code"], "readonly_filesystem")
        self.assertEqual((attempt["stage"], attempt["step"]), ("COLLECTION", "SET_VALIDATION"))
        self.assertFalse((self.job.state / "success.json").exists())
        self.assertFalse(self.case.upload.calls or self.case.fixture.sdk.calls)
        self.assertNotIn("synthetic-secret", output.getvalue() + str(attempt))

    def test_non_sqlite_set_validation_failure_does_not_run_sqlite_diagnostics(self):
        with patch.object(ops, "diagnose_failed_sqlite_candidate") as diagnostic, \
             patch.object(recovery, "create_verified_backup",
                          side_effect=PermissionError(errno.EACCES, "synthetic-secret")):
            attempt, _ = self.failure("file_permission_denied", "SET_VALIDATION")
        diagnostic.assert_not_called()
        self.assertNotIn("sqlite_diagnostic", attempt["error"])

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
            self.assertEqual(in_process_cli(), 1)
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
        ops.atomic_state(job.state / "activation.json", {"both_received_operator_confirmed": True,
            "both_accepted": True, "config_sha256": self.case.config["config_sha256"]})
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
            self.assertEqual(in_process_cli(), 1)
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
