"""Recovery collection tests use only disposable repositories and synthetic data."""

from __future__ import annotations

import contextlib
import builtins
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import backup_clarity_db as backup
import rove_recovery_set as recovery
import reapply_account_delete_tombstones as reapply


class RecoverySetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rove-recovery-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for name in ("rove_recovery_set.py", "backup_clarity_db.py", "reapply_account_delete_tombstones.py"):
            shutil.copyfile(Path(__file__).parent / name, self.repo / name)
        self.git("init", "-q", "-b", "recovery-test")
        self.git("config", "user.name", "Recovery Test")
        self.git("config", "user.email", "recovery-test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic recovery fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.db = self.root / "synthetic.db"
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("""
                CREATE TABLE users(user_id INTEGER PRIMARY KEY, cash REAL NOT NULL);
                INSERT INTO users VALUES (1, 125.5), (99, 0);
                CREATE TABLE report_links(
                    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
                    html_path TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE app_cash_request_receipts(id TEXT PRIMARY KEY, user_id INTEGER,
                    created_at TEXT, expired_at TEXT);
            """)
        self.ledger = self.root / "account_delete_tombstones.jsonl"
        self.ledger.write_bytes(self.ledger_line(99))
        self.ledger.chmod(0o600)
        self.public = self.root / "public_reports"
        self.reports = self.root / "reports"
        self.public.mkdir()
        self.reports.mkdir()
        self.env = self.root / ".rove-app-api.env"
        self.secret = "SYNTHETIC-DO-NOT-COPY-THIS-CREDENTIAL"
        self.env.write_text("ROVE_APP_AUTH_SECRET=" + self.secret + "\nBREVO_API_KEY=" + self.secret + "\n")
        self.env.chmod(0o600)
        configurations = []
        for role in sorted(recovery.CONFIG_ROLES):
            path = self.root / (role + ".conf")
            path.write_text("# synthetic " + role + "\n")
            configurations.append({"role": role, "path": str(path), "custody_ref": "escrow:runtime-fixture",
                                  "external_content_revision": "fixture-config-v1"})
        self.inventory = {
            "inventory_version": recovery.INVENTORY_VERSION, "database": str(self.db),
            "ledger": {"path": str(self.ledger), "history_confirmed_complete": True,
                       "audit_reference": "offline:synthetic-ledger-audit",
                       "anchor": {"bytes": self.ledger.stat().st_size,
                                  "sha256": hashlib.sha256(self.ledger.read_bytes()).hexdigest()}},
            "artifact_roots": {"public_reports": str(self.public), "reports": str(self.reports)},
            "expected_schema_sha256": self.schema_hash(),
            "runtime": {"python_version": "3.12.3", "sqlite_version": sqlite3.sqlite_version,
                        "gunicorn_version": "26.2.2", "entrypoint": "rove_app_wsgi:app",
                        "worker_class": "gthread", "workers": 1, "threads": 4,
                        "bind": "127.0.0.1:5057", "timezone": "UTC",
                        "environment_files": [{"path": str(self.env), "custody_ref": "vault:runtime-config",
                                               "external_content_revision": "fixture-env-v1"}],
                        "config_files": configurations},
            "secret_dependencies": [
                {"name": name, "version": "fixture-v1", "custody_ref": "offline:fixture-keys"}
                for name in ("ROVE_APP_AUTH_SECRET", "BREVO_API_KEY")
            ],
        }
        self.inventory_path = self.root / "inventory.json"
        self.output = self.root / "sets"
        self.effective_api = {
            "fragment_path": str(self.root / "api_service.conf"),
            "drop_in_paths": [str(self.root / "api_dropin.conf")],
            "environment_files": [str(self.env)], "absent_optional_environment_files": [],
        }
        discovery = patch.object(recovery, "systemd_api_configuration", side_effect=lambda: dict(self.effective_api))
        discovery.start()
        self.addCleanup(discovery.stop)
        self.save_inventory()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode()

    def ledger_line(self, user_id):
        return (json.dumps({"user_id": user_id,
                            "deleted_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}) + "\n").encode()

    def schema_hash(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            return recovery.schema_metadata(conn)["sha256"]

    def save_inventory(self):
        self.inventory_path.write_text(json.dumps(self.inventory))

    def cutoff_policy(self):
        declared = json.loads(json.dumps(self.inventory["ledger"]))
        declared["history_confirmed_complete"] = False
        declared["recovery_boundary"] = {
            "cutoff_at_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "baseline_database_sha256": recovery.digest(self.db.read_bytes()),
            "evidence_ref": "offline:synthetic-baseline-approval",
            "forward_coverage_confirmed": True,
        }
        return declared

    def save_policy(self, declared):
        path = self.root / "generation-policy.json"
        path.write_text(json.dumps(declared))
        return path

    def prospective_policy(self):
        declared = self.cutoff_policy()
        boundary = declared["recovery_boundary"]
        del boundary["baseline_database_sha256"]
        boundary.update(admission_mode="prospective", policy_version=recovery.PROSPECTIVE_POLICY_VERSION,
                        gate_version=recovery.PROSPECTIVE_GATE_VERSION,
                        ledger_anchor_id="ledger_anchor_" + "1" * 32,
                        expected_git_sha=self.sha, expected_schema_sha256=self.schema_hash())
        return declared

    def prospective_fixture(self):
        declared = self.prospective_policy()
        self.inventory["ledger"] = declared
        self.save_inventory()
        generation = self.collect()
        self.assertEqual(self.manifest(generation)["status"], "COMPLETE")
        declared["generation_receipts"] = {
            generation.name: recovery.digest((generation / "manifest.json").read_bytes())}
        return generation, declared, self.save_policy(declared)

    def changed_created_at(self, manifest):
        timestamp = recovery.utc_timestamp(manifest["created_at_utc"])
        return timestamp.replace(microsecond=(timestamp.microsecond + 1) % 1000000).isoformat()

    def collect(self, **kwargs):
        arguments = dict(db=self.db, ledger=self.ledger, inventory_path=self.inventory_path,
                         repo=self.repo, expected_git_sha=self.sha, output=self.output)
        arguments.update(kwargs)
        return recovery.build_recovery_set(**arguments)

    def manifest(self, path):
        return json.loads((path / "manifest.json").read_text())

    def add_public(self, *, owner=1, expired=False):
        token = "synthetic_token_123456"
        source = self.public / token / "index.html"
        source.parent.mkdir()
        source.write_text("<!doctype html><html>synthetic report</html>")
        expiry = datetime.now(timezone.utc) + timedelta(days=-1 if expired else 1)
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO report_links VALUES (?, ?, ?, ?, 'active')",
                         (token, owner, str(source), expiry.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        return source

    def test_complete_set_has_required_manifest_and_checksums(self):
        self.add_public()
        (self.reports / "rove_report_1_2026-09.pdf").write_bytes(b"%PDF-1.4\nsynthetic fixture")
        result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["software"]["git_sha"], self.sha)
        self.assertEqual(manifest["software"]["branch"], "recovery-test")
        self.assertEqual(manifest["database"]["integrity_check"], "ok")
        self.assertEqual(manifest["database"]["foreign_key_check"], "ok")
        self.assertEqual(manifest["ledger"]["status"], "VALID")
        self.assertEqual(len(manifest["artifacts"]), 2)
        self.assertEqual(recovery.verify_recovery_set(result), manifest)
        for record in [manifest["database"], manifest["ledger"], manifest["runtime"], *manifest["artifacts"]]:
            raw = (result / record["path"]).read_bytes()
            self.assertEqual(record["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(record["bytes"], len(raw))
        self.assertIsNotNone(datetime.fromisoformat(manifest["created_at_utc"]).tzinfo)
        self.assertFalse(manifest["restore_verified"])
        self.assertFalse(manifest["external_custody_verified"])
        self.assertEqual(manifest["offsite"], "NOT_CONFIGURED")

    def test_missing_database_fails_without_creating_source(self):
        self.db.unlink()
        result = self.collect()
        self.assertEqual(self.manifest(result)["status"], "FAILED")
        self.assertFalse(self.db.exists())
        self.assertFalse((result / "database/clarity.db").exists())

    def test_corrupt_database_fails_closed(self):
        self.db.write_bytes(b"not a SQLite database")
        result = self.collect()
        self.assertEqual(self.manifest(result)["status"], "FAILED")

    def test_integrity_check_failure_fails_closed(self):
        with patch.object(backup, "verify_database", side_effect=RuntimeError("synthetic integrity failure")):
            result = self.collect()
        self.assertEqual(self.manifest(result)["status"], "FAILED")
        self.assertFalse((result / "database/clarity.db").exists())

    def test_foreign_key_failure_fails_closed(self):
        source = self.add_public(owner=777)
        result = self.collect()
        self.assertEqual(self.manifest(result)["status"], "FAILED")
        self.assertTrue(source.exists())
        self.assertFalse((result / "database/clarity.db").exists())

    def test_missing_ledger_fails_closed(self):
        self.ledger.unlink()
        self.assertEqual(self.manifest(self.collect())["status"], "FAILED")
        self.assertFalse(self.ledger.exists())

    def test_malformed_ledger_fails_closed(self):
        for raw in (b"not json\n", b'{"user_id": true, "deleted_at":"2026-01-01T00:00:00Z"}\n',
                    b'{"user_id": 1, "deleted_at":"2026-01-01T00:00:00"}\n',
                    b'{"user_id": 1, "deleted_at":"invalid"}\n', self.ledger_line(99).rstrip(b"\n")):
            with self.subTest(raw=raw):
                self.ledger.write_bytes(raw)
                self.assertEqual(self.manifest(self.collect())["status"], "FAILED")

    def test_missing_required_public_artifact_fails(self):
        source = self.add_public()
        source.unlink()
        self.assertEqual(self.manifest(self.collect())["status"], "FAILED")

    def test_referenced_shared_report_script_is_required_and_copied(self):
        source = self.add_public()
        source.write_text('<!doctype html><script src="../support.js"></script>')
        self.assertEqual(self.manifest(self.collect())["status"], "FAILED")
        shared = self.public / "support.js"
        shared.write_text("// synthetic public support asset\n")
        result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(len(manifest["artifacts"]), 2)
        self.assertEqual((result / "artifacts/public_reports/support.js").read_bytes(), shared.read_bytes())

    def test_unknown_local_report_resources_cannot_be_silently_omitted(self):
        source = self.add_public()
        source.write_text('<!doctype html><img src="private-picture.png">')
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["error_code"], "report_local_dependency_unclassified")

    def test_external_fonts_inline_svg_and_data_images_need_no_local_copy(self):
        source = self.add_public()
        source.write_text('<!doctype html><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Test">'
                          '<style>.svg{fill:url(#inline-gradient)}</style><img src="data:image/png;base64,c3ludGhldGlj">')
        self.assertEqual(self.manifest(self.collect())["status"], "COMPLETE")

    def test_user_text_is_not_misinterpreted_as_css_but_local_import_is_checked(self):
        source = self.add_public()
        source.write_text('<!doctype html><p>Merchant URL(example) is normal text.</p>')
        self.assertEqual(self.manifest(self.collect())["status"], "COMPLETE")
        source.write_text('<!doctype html><style>@import "missing-local-style.css";</style>')
        self.assertEqual(self.manifest(self.collect())["error_code"], "report_local_dependency_unclassified")

    def test_expired_and_deleted_user_artifacts_are_not_required(self):
        source = self.add_public(expired=True)
        source.unlink()
        (self.reports / "rove_report_99_2026-09.pdf").write_bytes(b"deleted account document")
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["artifacts"], [])

    def test_snapshot_copies_no_live_wal_or_shm_and_keeps_committed_wal_rows(self):
        writer = sqlite3.connect(self.db)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE users SET cash=876.25 WHERE user_id=1")
        writer.commit()
        before = writer.execute("SELECT * FROM users ORDER BY user_id").fetchall()
        wal_hash = hashlib.sha256(Path(str(self.db) + "-wal").read_bytes()).hexdigest()
        result = self.collect()
        self.assertEqual(self.manifest(result)["status"], "COMPLETE")
        with contextlib.closing(sqlite3.connect(result / "database/clarity.db")) as restored:
            self.assertEqual(restored.execute("SELECT * FROM users ORDER BY user_id").fetchall(), before)
        self.assertEqual(writer.execute("SELECT * FROM users ORDER BY user_id").fetchall(), before)
        self.assertEqual(hashlib.sha256(Path(str(self.db) + "-wal").read_bytes()).hexdigest(), wal_hash)
        self.assertFalse(list(result.rglob("*-wal")))
        self.assertFalse(list(result.rglob("*-shm")))

    def test_source_database_ledger_config_and_artifacts_unchanged(self):
        artifact = self.add_public()
        sources = [self.db, self.ledger, self.env, self.inventory_path, artifact]
        before = {path: path.read_bytes() for path in sources}
        self.collect()
        self.assertEqual(before, {path: path.read_bytes() for path in sources})

    def test_secret_values_and_raw_environment_files_never_copied(self):
        result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        for path in result.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret.encode(), path.read_bytes())
        self.assertEqual({item["name"] for item in manifest["secret_dependencies"]},
                         {"ROVE_APP_AUTH_SECRET", "BREVO_API_KEY"})
        self.assertFalse(list(result.rglob("*.env")))

    def test_verified_environment_and_config_revisions_are_bound(self):
        inventory = recovery.load_inventory(self.inventory_path, self.db, self.ledger)
        self.assertEqual(inventory["runtime"]["environment_files"][0]["external_content_revision"],
                         "fixture-env-v1")
        metadata, _ = recovery.runtime_metadata(inventory)
        self.assertEqual(metadata["environment_files"][0]["external_content_revision"], "fixture-env-v1")
        self.assertEqual({item["external_content_revision"] for item in metadata["config_files"]},
                         {"fixture-config-v1"})
        self.assertEqual(recovery.runtime_revision_binding(metadata)["status"], "VERIFIED")

    def test_legacy_inventory_v1_with_historical_ledger_is_complete_but_never_safe(self):
        self.inventory["inventory_version"] = recovery.LEGACY_INVENTORY_VERSION
        self.inventory["runtime"]["environment_files"] = [str(self.env)]
        for item in self.inventory["runtime"]["config_files"]:
            item.pop("external_content_revision")
        self.save_inventory()
        generation = self.collect()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["account_restore_coverage"]["mode"], "historical")
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "UNVERIFIED")
        self.assertEqual(manifest["account_restore_safety"]["status"], recovery.UNKNOWN)
        recovery.verify_recovery_set(generation)
        result = recovery.classify_account_restore_generation(
            policy_path=self.save_policy(self.inventory["ledger"]), set_path=generation)
        self.assertEqual(result, {"status": recovery.UNKNOWN, "reason": "generation_safety_unknown"})

    def test_inventory_v2_unverified_revisions_block_historical_generation(self):
        marker = recovery.EXTERNAL_CONTENT_REVISION_UNVERIFIED
        self.inventory["runtime"]["environment_files"][0]["external_content_revision"] = marker
        for item in self.inventory["runtime"]["config_files"]:
            item["external_content_revision"] = marker
        self.save_inventory()
        generation = self.collect()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "UNVERIFIED")
        self.assertEqual(manifest["account_restore_safety"]["status"], recovery.UNKNOWN)
        result = recovery.classify_account_restore_generation(
            policy_path=self.save_policy(self.inventory["ledger"]), set_path=generation)
        self.assertEqual(result["status"], recovery.UNKNOWN)

    def test_runtime_revision_binding_status_must_be_exact_and_present(self):
        generation = self.collect()
        policy = self.save_policy(self.inventory["ledger"])
        original = self.manifest(generation)
        self.assertEqual(original["runtime_revision_binding"]["status"], "VERIFIED")
        for status in ("UNVERIFIED", "UNKNOWN", "CONFIRMED", None):
            with self.subTest(status=status):
                manifest = json.loads(json.dumps(original))
                if status is None:
                    del manifest["runtime_revision_binding"]["status"]
                else:
                    manifest["runtime_revision_binding"]["status"] = status
                (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
                result = recovery.classify_account_restore_generation(
                    policy_path=policy, set_path=generation)
                self.assertEqual(result["status"], recovery.UNKNOWN)
        manifest = json.loads(json.dumps(original))
        del manifest["runtime_revision_binding"]
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result["status"], recovery.UNKNOWN)

    def test_verified_to_unverified_manifest_tampering_is_blocked(self):
        generation = self.collect()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "VERIFIED")
        manifest["runtime_revision_binding"]["status"] = "UNVERIFIED"
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        result = recovery.classify_account_restore_generation(
            policy_path=self.save_policy(self.inventory["ledger"]), set_path=generation)
        self.assertNotEqual(result["status"], recovery.SAFE_FOR_ACCOUNT_RESTORE)

    def test_unverified_historical_generation_blocks_cli_and_replay(self):
        marker = recovery.EXTERNAL_CONTENT_REVISION_UNVERIFIED
        self.inventory["runtime"]["environment_files"][0]["external_content_revision"] = marker
        for item in self.inventory["runtime"]["config_files"]:
            item["external_content_revision"] = marker
        self.save_inventory()
        generation = self.collect()
        policy = self.save_policy(self.inventory["ledger"])
        target = self.root / "staged-unverified-historical.db"
        shutil.copyfile(generation / "database/clarity.db", target)
        target.chmod(0o600)
        result = subprocess.run(
            [sys.executable, "-B", str(Path(recovery.__file__)), "generation-gate",
             "--policy", str(policy), "--set", str(generation)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ACCOUNT_RESTORE_GATE=UNKNOWN", result.stdout)
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_exact_approved_bare_baseline_is_blocked_without_runtime_manifest(self):
        policy = self.save_policy(self.cutoff_policy())
        before = self.db.read_bytes(), self.ledger.read_bytes()
        result = recovery.classify_account_restore_generation(policy_path=policy, database_path=self.db)
        self.assertEqual(result, {"status": recovery.UNKNOWN,
                                  "reason": "runtime_revision_binding_missing"})
        self.assertEqual(before, (self.db.read_bytes(), self.ledger.read_bytes()))
        target = self.root / "staged-baseline.db"
        shutil.copyfile(self.db, target)
        target.chmod(0o600)
        self.assert_replay_blocked(
            ["reapply_account_delete_tombstones.py", "--db", str(target), "--policy", str(policy),
             "--baseline", str(self.db)], target)

    def test_unverified_revisions_are_manifest_bound_but_not_restore_safe_in_prospective_mode(self):
        marker = recovery.EXTERNAL_CONTENT_REVISION_UNVERIFIED
        self.inventory["runtime"]["environment_files"][0]["external_content_revision"] = marker
        self.inventory["runtime"]["config_files"][0]["external_content_revision"] = marker
        generation, declared, policy = self.prospective_fixture()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "UNVERIFIED")
        self.assertEqual(manifest["account_restore_safety"]["status"], recovery.UNKNOWN)
        runtime = json.loads((generation / manifest["runtime"]["path"]).read_text())
        self.assertEqual(runtime["expected_runtime"]["environment_files"][0]["external_content_revision"], marker)
        self.assertEqual(runtime["expected_runtime"]["config_files"][0]["external_content_revision"], marker)
        self.assertEqual(recovery.digest((generation / "manifest.json").read_bytes()),
                         declared["generation_receipts"][generation.name])
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result, {"status": recovery.UNKNOWN, "reason": "generation_safety_unknown"})
        target = self.root / "staged-unverified.db"
        shutil.copyfile(generation / "database/clarity.db", target)
        target.chmod(0o600)
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_legacy_inventory_without_revisions_is_explicitly_unverified_and_blocked_prospectively(self):
        self.inventory["inventory_version"] = recovery.LEGACY_INVENTORY_VERSION
        self.inventory["runtime"]["environment_files"] = [str(self.env)]
        for item in self.inventory["runtime"]["config_files"]:
            item.pop("external_content_revision")
        generation, _, policy = self.prospective_fixture()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "UNVERIFIED")
        self.assertEqual(manifest["account_restore_safety"]["status"], recovery.UNKNOWN)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["status"], recovery.UNKNOWN)

    def test_inventory_revision_changes_change_bound_metadata_hash(self):
        first = self.collect()
        first_manifest = self.manifest(first)
        self.inventory["runtime"]["environment_files"][0]["external_content_revision"] = "fixture-env-v2"
        self.save_inventory()
        env_changed = self.manifest(self.collect())
        self.assertNotEqual(first_manifest["runtime"]["sha256"], env_changed["runtime"]["sha256"])
        self.assertNotEqual(first_manifest["runtime_revision_binding"]["sha256"],
                            env_changed["runtime_revision_binding"]["sha256"])
        self.inventory["runtime"]["environment_files"][0]["external_content_revision"] = "fixture-env-v1"
        self.inventory["runtime"]["config_files"][0]["external_content_revision"] = "fixture-config-v2"
        self.save_inventory()
        config_changed = self.manifest(self.collect())
        self.assertNotEqual(first_manifest["runtime"]["sha256"], config_changed["runtime"]["sha256"])
        self.assertNotEqual(first_manifest["runtime_revision_binding"]["sha256"],
                            config_changed["runtime_revision_binding"]["sha256"])

    def test_post_collection_revision_tampering_is_blocked_even_if_member_hash_is_recomputed(self):
        generation = self.collect()
        manifest = self.manifest(generation)
        runtime_path = generation / manifest["runtime"]["path"]
        runtime = json.loads(runtime_path.read_text())
        runtime["expected_runtime"]["environment_files"][0]["external_content_revision"] = "tampered-v2"
        runtime_raw = recovery.json_bytes(runtime)
        runtime_path.write_bytes(runtime_raw)
        manifest["runtime"]["bytes"] = len(runtime_raw)
        manifest["runtime"]["sha256"] = recovery.digest(runtime_raw)
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        with self.assertRaisesRegex(recovery.RecoveryError, "runtime_revision_binding_mismatch"):
            recovery.verify_recovery_set(generation)

    def test_secret_revision_stays_separate_from_runtime_file_revisions(self):
        first = self.collect()
        first_manifest = self.manifest(first)
        self.inventory["secret_dependencies"][0]["version"] = "runtime-secret-20261006"
        self.save_inventory()
        second = self.collect()
        second_manifest = self.manifest(second)
        self.assertEqual(first_manifest["runtime_revision_binding"], second_manifest["runtime_revision_binding"])
        self.assertEqual(first_manifest["runtime"]["sha256"], second_manifest["runtime"]["sha256"])
        self.assertNotEqual(first_manifest["secret_dependencies"], second_manifest["secret_dependencies"])

    def test_new_inventory_requires_revision_fields_and_rejects_unknown_fields(self):
        baseline = json.loads(json.dumps(self.inventory))
        for mutate, code in (
            (lambda: self.inventory["runtime"]["environment_files"][0].pop("external_content_revision"),
             "inventory_fields_invalid"),
            (lambda: self.inventory["runtime"]["config_files"][0].pop("external_content_revision"),
             "inventory_fields_invalid"),
            (lambda: self.inventory["runtime"]["environment_files"][0].update(unexpected="x"),
             "inventory_fields_invalid"),
            (lambda: self.inventory["runtime"]["config_files"][0].update(unexpected="x"),
             "inventory_fields_invalid"),
        ):
            with self.subTest(code=code):
                self.inventory = json.loads(json.dumps(baseline))
                mutate()
                self.save_inventory()
                with self.assertRaisesRegex(recovery.RecoveryError, code):
                    recovery.load_inventory(self.inventory_path, self.db, self.ledger)

    def test_unverified_marker_is_accepted_but_invalid_revision_values_fail_closed(self):
        self.inventory["runtime"]["environment_files"][0]["external_content_revision"] = \
            recovery.EXTERNAL_CONTENT_REVISION_UNVERIFIED
        self.inventory["runtime"]["config_files"][0]["external_content_revision"] = \
            recovery.EXTERNAL_CONTENT_REVISION_UNVERIFIED
        self.save_inventory()
        self.assertEqual(recovery.load_inventory(self.inventory_path, self.db, self.ledger)["runtime"]
                         ["environment_files"][0]["external_content_revision"],
                         recovery.EXTERNAL_CONTENT_REVISION_UNVERIFIED)
        self.inventory["runtime"]["config_files"][0]["external_content_revision"] = " "
        self.save_inventory()
        with self.assertRaisesRegex(recovery.RecoveryError, "config_inventory_invalid"):
            recovery.load_inventory(self.inventory_path, self.db, self.ledger)

    def test_missing_secret_dependency_fails(self):
        self.inventory["secret_dependencies"] = []
        self.save_inventory()
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["status"], "FAILED")
        self.assertEqual(manifest["error_code"], "secret_dependency_missing")

    def test_all_provider_key_versions_required_even_if_not_active(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("CREATE TABLE app_provider_secrets(key_version INTEGER);"
                               "INSERT INTO app_provider_secrets VALUES (1), (2);")
        self.inventory["expected_schema_sha256"] = self.schema_hash()
        self.save_inventory()
        self.assertEqual(self.manifest(self.collect())["status"], "FAILED")
        for version in (1, 2):
            self.inventory["secret_dependencies"].append(
                {"name": f"ROVE_OPEN_BANKING_VAULT_KEY_V{version}", "version": str(version), "custody_ref": "vault:fixture"})
        self.save_inventory()
        self.assertEqual(self.manifest(self.collect())["status"], "COMPLETE")

    def test_ledger_truncation_or_rewrite_fails_against_anchor(self):
        for raw in (b"", self.ledger_line(1)):
            with self.subTest(raw=raw):
                self.ledger.write_bytes(raw)
                manifest = self.manifest(self.collect())
                self.assertEqual(manifest["status"], "FAILED")

    def test_ledger_append_is_allowed_and_captured_after_artifact_copy(self):
        original = recovery.artifact_metadata
        def append(*args, **kwargs):
            artifacts = original(*args, **kwargs)
            with self.ledger.open("ab") as ledger:
                ledger.write(self.ledger_line(1))
            return artifacts
        with patch.object(recovery, "artifact_metadata", side_effect=append):
            result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["ledger"]["records"], 2)
        self.assertEqual((result / manifest["ledger"]["path"]).read_bytes(), self.ledger.read_bytes())
        self.assertTrue(manifest["ledger"]["replay_before_runtime_required"])

    def test_unattested_ledger_history_fails_before_writing_set(self):
        self.inventory["ledger"]["history_confirmed_complete"] = False
        self.save_inventory()
        with self.assertRaises(recovery.RecoveryError):
            self.collect()
        self.assertFalse(self.output.exists())

    def test_cutoff_does_not_claim_empty_ledger_is_historically_complete(self):
        self.ledger.write_bytes(b"")
        self.inventory["ledger"]["anchor"] = {"bytes": 0, "sha256": recovery.digest(b"")}
        self.inventory["ledger"] = self.cutoff_policy()
        self.save_inventory()
        result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["account_restore_coverage"]["mode"], "cutoff")
        self.assertEqual(manifest["ledger"]["completeness"], "FROM_VERIFIED_CUTOFF_ONLY")
        self.assertEqual(manifest["ledger"]["records"], 0)
        self.assertFalse(manifest["external_custody_verified"])
        recovery.check_account_restore_generation(policy_path=self.save_policy(self.inventory["ledger"]), set_path=result)

    def test_unverified_or_future_boundary_is_rejected(self):
        for field, value in (
            ("forward_coverage_confirmed", False), ("baseline_database_sha256", "invalid"),
            ("evidence_ref", "not-an-independent-reference"),
            ("cutoff_at_utc", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
            ("cutoff_at_utc", "2026-01-01T00:00:00"),
        ):
            with self.subTest(field=field):
                policy = self.cutoff_policy()
                policy["recovery_boundary"][field] = value
                with self.assertRaises(recovery.RecoveryError):
                    recovery.ledger_coverage(policy)

    def test_boundary_requires_all_fields_and_cannot_override_historical_claim(self):
        declared = self.cutoff_policy()
        del declared["recovery_boundary"]["baseline_database_sha256"]
        with self.assertRaises(recovery.RecoveryError):
            recovery.ledger_coverage(declared)
        declared = self.cutoff_policy()
        declared["history_confirmed_complete"] = True
        with self.assertRaisesRegex(recovery.RecoveryError, "ledger_history_not_attested"):
            recovery.ledger_coverage(declared)

    def test_snapshot_crossing_cutoff_is_blocked_even_if_completed_after_it(self):
        declared = self.cutoff_policy()
        coverage = recovery.ledger_coverage(declared)
        cutoff = datetime.fromisoformat(coverage["cutoff_at_utc"])
        manifest = {"account_restore_coverage": coverage,
                    "created_at_utc": (cutoff - timedelta(seconds=1)).isoformat(),
                    "snapshot_completed_at_utc": (cutoff + timedelta(seconds=1)).isoformat()}
        with self.assertRaisesRegex(recovery.RecoveryError, "generation_before_recovery_cutoff"):
            recovery.check_generation_boundary(manifest, coverage)

    def test_current_external_policy_overrides_old_set_historical_claim(self):
        result = self.collect()
        policy = self.save_policy(self.cutoff_policy())
        with self.assertRaisesRegex(recovery.RecoveryError, "generation_policy_mismatch"):
            recovery.check_account_restore_generation(policy_path=policy, set_path=result)

    def test_legacy_database_is_blocked_regardless_of_new_mtime(self):
        declared = self.cutoff_policy()
        declared["recovery_boundary"]["baseline_database_sha256"] = "0" * 64
        policy = self.save_policy(declared)
        os.utime(self.db, None)
        with self.assertRaisesRegex(recovery.RecoveryError, "legacy_generation_not_approved"):
            recovery.check_account_restore_generation(policy_path=policy, database_path=self.db)

    def test_exact_approved_bare_baseline_without_runtime_binding_is_unknown(self):
        policy = self.save_policy(self.cutoff_policy())
        before = self.db.read_bytes(), self.ledger.read_bytes()
        with self.assertRaisesRegex(recovery.RecoveryError, "runtime_revision_binding_missing"):
            recovery.check_account_restore_generation(policy_path=policy, database_path=self.db)
        self.assertEqual(before, (self.db.read_bytes(), self.ledger.read_bytes()))

    def test_latest_external_ledger_append_is_required_without_mutating_set(self):
        result = self.collect()
        before = {p: p.read_bytes() for p in result.rglob("*") if p.is_file()}
        policy = self.save_policy(self.inventory["ledger"])
        self.ledger.write_bytes(self.ledger.read_bytes() + self.ledger_line(1))
        recovery.check_account_restore_generation(policy_path=policy, set_path=result)
        self.assertEqual(before, {p: p.read_bytes() for p in result.rglob("*") if p.is_file()})
        self.ledger.write_bytes(b"")
        with self.assertRaises(recovery.RecoveryError):
            recovery.check_account_restore_generation(policy_path=policy, set_path=result)

    def test_gate_requires_latest_ledger_to_include_entire_captured_prefix(self):
        original = self.ledger.read_bytes()
        self.ledger.write_bytes(original + self.ledger_line(1))
        result = self.collect()
        self.ledger.write_bytes(original)
        with self.assertRaisesRegex(recovery.RecoveryError, "latest_ledger_history_changed"):
            recovery.check_account_restore_generation(policy_path=self.save_policy(self.inventory["ledger"]), set_path=result)

    def test_old_manifest_version_cannot_pass_generation_gate(self):
        result = self.collect()
        original = self.manifest(result)
        for version in (1, 3):
            with self.subTest(version=version):
                manifest = json.loads(json.dumps(original))
                manifest["manifest_version"] = version
                (result / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(recovery.RecoveryError, "set_not_complete"):
                    recovery.check_account_restore_generation(
                        policy_path=self.save_policy(self.inventory["ledger"]), set_path=result)

    def test_generation_gate_cli_requires_policy_and_marks_unknown_generation_unsafe(self):
        policy = self.save_policy(self.cutoff_policy())
        declared = json.loads(policy.read_text())
        declared["recovery_boundary"]["baseline_database_sha256"] = "0" * 64
        policy.write_text(json.dumps(declared))
        result = subprocess.run([sys.executable, "-B", str(Path(recovery.__file__)), "generation-gate",
                                 "--policy", str(policy), "--db", str(self.db)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ACCOUNT_RESTORE_GATE=UNSAFE_FOR_ACCOUNT_RESTORE", result.stdout)
        self.assertNotIn(self.secret, result.stdout + result.stderr)
        result = subprocess.run([sys.executable, "-B", str(Path(recovery.__file__)), "generation-gate",
                                 "--db", str(self.db)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_verify_cli_never_authorizes_account_restore(self):
        result = subprocess.run([sys.executable, "-B", str(Path(recovery.__file__)), "verify", str(self.collect())],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACCOUNT_RESTORE=NOT_AUTHORIZED", result.stdout)

    def test_zero_dropins_supported_without_inventing_a_required_file(self):
        self.inventory["runtime"]["config_files"] = [item for item in self.inventory["runtime"]["config_files"]
                                                     if item["role"] != "api_dropin"]
        self.effective_api["drop_in_paths"] = []
        self.save_inventory()
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertFalse(any(item["role"] == "api_dropin" for item in manifest["configuration"]))

    def test_all_four_dropins_have_sorted_path_hash_and_metadata_without_contents(self):
        self.inventory["runtime"]["config_files"] = [item for item in self.inventory["runtime"]["config_files"]
                                                     if item["role"] != "api_dropin"]
        paths = []
        for name in ("market-data.conf", "env.conf", "99-rove-wsgi.conf", "europe-market-data.conf"):
            path = self.root / name
            path.write_text("# " + name + "\nEnvironment=BREVO_API_KEY=" + self.secret + "\n")
            paths.append(str(path))
            self.inventory["runtime"]["config_files"].insert(0, {
                "role": "api_dropin", "path": str(path), "custody_ref": "offline:runtime-configuration",
                "external_content_revision": "fixture-dropin-v1"})
        self.effective_api["drop_in_paths"] = sorted(paths)
        self.save_inventory()
        result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        dropins = [item for item in manifest["configuration"] if item["role"] == "api_dropin"]
        self.assertEqual([item["configured_path"] for item in dropins], sorted(paths))
        for item in dropins:
            self.assertEqual(item["sha256"], recovery.digest(Path(item["source_path"]).read_bytes()))
            self.assertTrue({"source_mode", "source_uid", "source_gid", "bytes", "symlink_resolved"} <= set(item))
        for path in result.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret.encode(), path.read_bytes())

    def test_uninventoried_effective_dropin_is_not_silently_ignored(self):
        self.effective_api["drop_in_paths"].append(str(self.root / "unexpected.conf"))
        self.effective_api["drop_in_paths"].sort()
        self.assertEqual(self.manifest(self.collect())["error_code"], "effective_api_configuration_not_inventoried")

    def test_extra_declared_dropin_and_missing_required_dropin_fail_closed(self):
        self.effective_api["drop_in_paths"] = []
        self.assertEqual(self.manifest(self.collect())["error_code"], "effective_api_configuration_not_inventoried")
        self.effective_api["drop_in_paths"] = [str(self.root / "api_dropin.conf")]
        (self.root / "api_dropin.conf").unlink()
        with self.assertRaisesRegex(recovery.RecoveryError, "config_file_unavailable"):
            self.collect()

    def test_config_symlink_records_actual_nginx_target_without_copying_contents(self):
        path = self.root / "nginx_site.conf"
        actual = self.root / "nginx_site_actual.conf"
        path.rename(actual)
        path.symlink_to(actual)
        result = self.collect()
        manifest = self.manifest(result)
        self.assertEqual(manifest["status"], "COMPLETE")
        record = next(item for item in manifest["configuration"] if item["role"] == "nginx_site")
        self.assertEqual(record["configured_path"], str(path))
        self.assertEqual(record["source_path"], str(actual))
        self.assertTrue(record["symlink_resolved"])
        self.assertEqual(record["sha256"], recovery.digest(actual.read_bytes()))

    def test_config_retarget_during_capture_fails(self):
        path = self.root / "nginx_site.conf"
        actual = self.root / "nginx_site_actual.conf"
        path.rename(actual)
        path.symlink_to(actual)
        replacement = self.root / "nginx_other.conf"
        replacement.write_bytes(actual.read_bytes())
        original = recovery.artifact_metadata
        def retarget(*args, **kwargs):
            artifacts = original(*args, **kwargs)
            path.unlink()
            path.symlink_to(replacement)
            return artifacts
        with patch.object(recovery, "artifact_metadata", side_effect=retarget):
            self.assertEqual(self.manifest(self.collect())["error_code"], "runtime_changed_during_capture")

    def test_invalid_or_dangling_config_paths_fail_before_collection(self):
        for name in ("relative.conf", str(self.root / ".." / "outside.conf"), str(self.root / "missing.conf")):
            with self.subTest(path=name), self.assertRaises(recovery.RecoveryError):
                recovery.configuration_path(name)
        path = self.root / "dangling.conf"
        path.symlink_to(self.root / "missing.conf")
        with self.assertRaisesRegex(recovery.RecoveryError, "config_file_unavailable"):
            recovery.configuration_path(str(path))

    def test_new_effective_environment_file_requires_inventory(self):
        self.effective_api["environment_files"].append(str(self.root / "additional.env"))
        self.effective_api["environment_files"].sort()
        self.assertEqual(self.manifest(self.collect())["error_code"], "effective_environment_not_inventoried")

    def test_direct_unit_secret_requires_custody_metadata_but_never_copies_value(self):
        unit = self.root / "api_dropin.conf"
        unit.write_text('Environment = "ROVE_INTERNAL_PUSH_SECRET=' + self.secret + '"\n')
        self.assertEqual(self.manifest(self.collect())["error_code"], "secret_dependency_missing")
        self.inventory["secret_dependencies"].append({"name": "ROVE_INTERNAL_PUSH_SECRET", "version": "v1",
                                                       "custody_ref": "offline:push-key"})
        self.save_inventory()
        result = self.collect()
        self.assertEqual(self.manifest(result)["status"], "COMPLETE")
        for path in result.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret.encode(), path.read_bytes())

    def test_provider_iban_historical_key_versions_require_inventory(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("CREATE TABLE app_provider_accounts(iban_key_version INTEGER);"
                               "INSERT INTO app_provider_accounts VALUES (2), (3), (NULL);")
        self.inventory["expected_schema_sha256"] = self.schema_hash()
        self.save_inventory()
        self.assertEqual(self.manifest(self.collect())["error_code"], "secret_dependency_missing")
        for version in (2, 3):
            self.inventory["secret_dependencies"].append({
                "name": f"ROVE_OPEN_BANKING_VAULT_KEY_V{version}", "version": str(version), "custody_ref": "vault:versions"})
        self.save_inventory()
        self.assertEqual(self.manifest(self.collect())["status"], "COMPLETE")

    def test_raw_secret_values_cannot_replace_custody_references(self):
        self.inventory["secret_dependencies"][0]["custody_ref"] = self.secret
        self.save_inventory()
        with self.assertRaisesRegex(recovery.RecoveryError, "secret_inventory_invalid"):
            self.collect()

    def test_manifest_configuration_cannot_diverge_from_hashed_metadata(self):
        result = self.collect()
        manifest = self.manifest(result)
        manifest["configuration"][0]["sha256"] = "0" * 64
        (result / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(recovery.RecoveryError, "set_configuration_metadata_mismatch"):
            recovery.verify_recovery_set(result)

    def test_unknown_inventory_fields_cannot_smuggle_secret_contents(self):
        self.inventory["runtime"]["BREVO_API_KEY"] = self.secret
        self.save_inventory()
        with self.assertRaises(recovery.RecoveryError):
            self.collect()
        self.assertFalse(self.output.exists())

    def test_wrong_git_sha_fails(self):
        manifest = self.manifest(self.collect(expected_git_sha="0" * 40))
        self.assertEqual(manifest["status"], "FAILED")
        self.assertEqual(manifest["error_code"], "git_revision_mismatch")

    def test_dirty_repository_fails(self):
        (self.repo / "unrelated.py").write_text("unrelated")
        self.assertEqual(self.manifest(self.collect())["error_code"], "git_worktree_dirty")

    def test_expected_production_environment_is_the_only_untracked_exception(self):
        env = self.repo / ".rove-app-api.env"
        shutil.copyfile(self.env, env)
        self.inventory["runtime"]["environment_files"] = [{
            "path": str(env), "custody_ref": "vault:runtime-config", "external_content_revision": "fixture-env-v1"}]
        self.effective_api["environment_files"] = [str(env)]
        self.save_inventory()
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["software"]["allowed_untracked_environment_files"], [".rove-app-api.env"])
        self.assertFalse(manifest["software"]["worktree_clean"])

    def test_tool_must_match_committed_release_not_only_head(self):
        (self.repo / "rove_recovery_set.py").write_text("# other implementation\n")
        self.git("add", ".")
        self.git("commit", "-qm", "different collector")
        manifest = self.manifest(self.collect(expected_git_sha=self.git("rev-parse", "HEAD").strip()))
        self.assertEqual(manifest["error_code"], "tool_not_bound_to_git_revision")

    def test_detached_head_still_records_exact_release(self):
        self.git("checkout", "--detach", "-q", self.sha)
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertTrue(manifest["software"]["detached_head"])
        self.assertIsNone(manifest["software"]["branch"])

    def test_schema_mismatch_fails(self):
        self.inventory["expected_schema_sha256"] = "0" * 64
        self.save_inventory()
        self.assertEqual(self.manifest(self.collect())["error_code"], "schema_fingerprint_mismatch")

    def test_output_must_not_overlap_inputs_or_repository(self):
        for output in (self.repo / "sets", self.public / "sets", self.reports / "sets", self.root):
            with self.subTest(output=output), self.assertRaises(recovery.RecoveryError):
                self.collect(output=output)
        self.assertFalse((self.public / "sets").exists())

    def test_symlink_inputs_and_required_artifacts_fail_closed(self):
        source = self.add_public()
        moved = self.root / "elsewhere.html"
        source.rename(moved)
        source.symlink_to(moved)
        self.assertEqual(self.manifest(self.collect())["status"], "FAILED")
        source.unlink()
        moved.rename(source)
        moved_ledger = self.root / "actual-ledger.jsonl"
        self.ledger.rename(moved_ledger)
        self.ledger.symlink_to(moved_ledger)
        with self.assertRaises(recovery.RecoveryError):
            self.collect()

    def test_public_path_cannot_select_env_or_another_users_file(self):
        self.add_public()
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE report_links SET html_path=?", (str(self.env),))
            conn.commit()
        manifest = self.manifest(self.collect())
        self.assertEqual(manifest["error_code"], "report_path_outside_root")
        for file in self.output.rglob("*"):
            if file.is_file():
                self.assertNotIn(self.secret.encode(), file.read_bytes())

    def test_unknown_pdf_ownership_or_layout_fails_not_silently_omitted(self):
        pdf = self.reports / "rove_report_777_2026-09.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        self.assertEqual(self.manifest(self.collect())["error_code"], "pdf_owner_unknown")
        pdf.unlink()
        (self.reports / "unclassified.pdf").write_bytes(b"%PDF-1.4")
        self.assertEqual(self.manifest(self.collect())["error_code"], "report_layout_unknown")

    def test_permissions_are_private_without_changing_sources(self):
        original_mode = self.env.stat().st_mode & 0o777
        result = self.collect()
        self.assertEqual(result.stat().st_mode & 0o777, 0o700)
        for path in result.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertEqual(self.env.stat().st_mode & 0o777, original_mode)

    def test_verifier_rejects_tampering_extra_files_and_failed_sets(self):
        result = self.collect()
        ledger = result / "ledger/account_delete_tombstones.jsonl"
        ledger.write_bytes(ledger.read_bytes() + self.ledger_line(1))
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_recovery_set(result)
        result = self.collect()
        extra = result / "unexpected.env"
        extra.write_text("unexpected")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_recovery_set(result)
        self.ledger.unlink()
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify_recovery_set(self.collect())

    def test_verifier_never_opens_manifest_path_outside_set(self):
        result = self.collect()
        manifest = self.manifest(result)
        manifest["database"]["path"] = "../../synthetic.db"
        (result / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(recovery.RecoveryError, "manifest_path_invalid"):
            recovery.verify_recovery_set(result)

    def test_collect_never_imports_application_or_contacts_network(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(self.manifest(self.collect())["status"], "COMPLETE")
        result = subprocess.run([sys.executable, "-c",
            "import rove_recovery_set, sys; assert 'rove_app_api' not in sys.modules; "
            "assert 'report_engine' not in sys.modules; assert 'dotenv' not in sys.modules"],
            cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_inventory_keys_rejected(self):
        self.inventory_path.write_text('{"inventory_version": 1, "inventory_version": 2}')
        with self.assertRaises(recovery.RecoveryError):
            self.collect()

    def test_runtime_change_during_capture_fails(self):
        original = recovery.artifact_metadata
        def change(*args, **kwargs):
            artifacts = original(*args, **kwargs)
            self.env.write_text(self.env.read_text() + "# changed during capture\n")
            return artifacts
        with patch.object(recovery, "artifact_metadata", side_effect=change):
            self.assertEqual(self.manifest(self.collect())["error_code"], "runtime_changed_during_capture")

    def test_ledger_rewrite_during_capture_fails(self):
        original = recovery.artifact_metadata
        def change(*args, **kwargs):
            artifacts = original(*args, **kwargs)
            self.ledger.write_bytes(self.ledger_line(1))
            return artifacts
        with patch.object(recovery, "artifact_metadata", side_effect=change):
            self.assertEqual(self.manifest(self.collect())["error_code"], "ledger_history_changed")

    def test_empty_ledger_cannot_be_attested_as_historical_coverage(self):
        self.ledger.write_bytes(b"")
        self.inventory["ledger"]["anchor"] = {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
        self.save_inventory()
        with self.assertRaisesRegex(recovery.RecoveryError, "empty_ledger_history_not_proven"):
            self.collect()

    def test_sqlite_user_version_is_part_of_expected_schema(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA user_version=123")
        self.assertEqual(self.manifest(self.collect())["error_code"], "schema_fingerprint_mismatch")

    def test_fifo_cannot_hang_artifact_collection(self):
        path = self.reports / "rove_report_1_2026-09.pdf"
        os.mkfifo(path, 0o600)
        self.assertEqual(self.manifest(self.collect())["error_code"], "source_file_invalid")

    def test_unprivate_output_is_rejected_not_chmodded(self):
        self.output.mkdir(mode=0o755)
        self.output.chmod(0o755)
        with self.assertRaises(recovery.RecoveryError):
            self.collect()
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_verification_does_not_change_completed_set(self):
        result = self.collect()
        before = {path: path.read_bytes() for path in result.rglob("*") if path.is_file()}
        recovery.verify_recovery_set(result)
        self.assertEqual(before, {path: path.read_bytes() for path in result.rglob("*") if path.is_file()})

    def test_cli_failure_emits_only_safe_status_without_secret_values(self):
        self.inventory["runtime"]["unexpected_secret"] = self.secret
        self.save_inventory()
        args = [str(Path(recovery.__file__)), "create", "--db", str(self.db), "--ledger", str(self.ledger),
                "--inventory", str(self.inventory_path), "--repo", str(self.repo),
                "--expected-git-sha", self.sha, "--output-dir", str(self.output)]
        result = subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("RECOVERY_SET_STATUS=FAILED", result.stderr)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def replay_fixture(self):
        generation = self.collect()
        policy = self.save_policy(self.inventory["ledger"])
        target = self.root / "staged.db"
        shutil.copyfile(generation / "database/clarity.db", target)
        target.chmod(0o600)
        return generation, policy, target

    def replay_command(self, generation, policy, target, *extra):
        return ["reapply_account_delete_tombstones.py", "--db", str(target),
                "--policy", str(policy), "--recovery-set", str(generation), *extra]

    def assert_replay_blocked(self, arguments, target):
        before = target.read_bytes() if target.exists() else None
        imports, writes = [], []
        original_import = builtins.__import__
        original_connect = sqlite3.connect

        def guarded_import(name, *args, **kwargs):
            if name == "rove_app_api":
                imports.append(name)
            return original_import(name, *args, **kwargs)

        def guarded_connect(database, *args, **kwargs):
            if not kwargs.get("uri") or "mode=ro" not in str(database):
                writes.append(str(database))
            return original_connect(database, *args, **kwargs)

        output = io.StringIO()
        with patch.object(sys, "argv", arguments), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(output), patch("builtins.__import__", side_effect=guarded_import), \
                patch.object(sqlite3, "connect", side_effect=guarded_connect):
            self.assertEqual(reapply.main(), 2)
        self.assertEqual(imports, [])
        self.assertEqual(writes, [])
        self.assertEqual(before, target.read_bytes() if target.exists() else None)
        self.assertIn("ACCOUNT_RESTORE=BLOCKED", output.getvalue())
        self.assertNotIn(self.secret, output.getvalue())
        return output.getvalue()

    def test_canonical_replay_accepts_safe_generation_and_is_user_scoped(self):
        generation, policy, target = self.replay_fixture()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "VERIFIED")
        self.assertEqual(manifest["account_restore_coverage"]["mode"], "historical")
        self.assertEqual(manifest["account_restore_safety"]["status"], recovery.SAFE_FOR_ACCOUNT_RESTORE)
        source_before = (generation / "database/clarity.db").read_bytes()
        with patch.object(sys, "argv", self.replay_command(generation, policy, target)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reapply.main(), 0)
        with contextlib.closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute("SELECT user_id, cash FROM users").fetchall(), [(1, 125.5)])
        self.assertEqual((generation / "database/clarity.db").read_bytes(), source_before)

    def test_canonical_replay_blocks_explicit_unsafe_and_unknown_before_db_work(self):
        generation, policy, target = self.replay_fixture()
        manifest = self.manifest(generation)
        for status in (recovery.UNSAFE_FOR_ACCOUNT_RESTORE, recovery.UNKNOWN):
            with self.subTest(status=status):
                manifest["account_restore_safety"]["status"] = status
                (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
                self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_canonical_replay_has_no_missing_policy_or_legacy_cli_bypass(self):
        generation, policy, target = self.replay_fixture()
        self.assert_replay_blocked(["reapply_account_delete_tombstones.py", "--db", str(target),
                                   "--ledger", str(self.ledger)], target)
        output = self.assert_replay_blocked(self.replay_command(generation, self.root / "missing.json", target), target)
        self.assertIn("FileNotFoundError", output)

    def test_replay_rejects_missing_and_damaged_latest_ledger(self):
        generation, policy, target = self.replay_fixture()
        self.ledger.unlink()
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)
        self.ledger.write_bytes(b"damaged ledger\n")
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_replay_rejects_missing_and_inconsistent_manifest_anchor(self):
        generation, policy, target = self.replay_fixture()
        original = self.manifest(generation)
        for anchor in (None, {"bytes": 0, "sha256": recovery.digest(b"")},
                       {"bytes": self.ledger.stat().st_size, "sha256": "0" * 64}):
            with self.subTest(anchor=anchor):
                manifest = json.loads(json.dumps(original))
                if anchor is None:
                    del manifest["ledger"]["anchor"]
                else:
                    manifest["ledger"]["anchor"] = anchor
                (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
                self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_generation_safety_manifest_binds_id_times_anchor_git_schema_and_gate(self):
        generation, policy, target = self.replay_fixture()
        original = self.manifest(generation)
        safety = original["account_restore_safety"]
        self.assertEqual(safety["status"], recovery.SAFE_FOR_ACCOUNT_RESTORE)
        self.assertEqual(safety["ledger_anchor"], original["ledger"]["anchor"])
        self.assertEqual(safety["git_sha"], self.sha)
        self.assertEqual(safety["sqlite_user_version"], 0)
        for key in ("reason", "gate_version", "recovery_set_id", "created_at_utc", "snapshot_completed_at_utc",
                    "schema_sha256", "sqlite_user_version", "database_sha256", "git_sha", "manifest_version"):
            with self.subTest(key=key):
                manifest = json.loads(json.dumps(original))
                manifest["account_restore_safety"][key] = "tampered"
                (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
                self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_missing_safety_status_is_unknown_and_cannot_replay(self):
        generation, policy, target = self.replay_fixture()
        manifest = self.manifest(generation)
        del manifest["account_restore_safety"]
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        classification = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(classification["status"], recovery.UNKNOWN)
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_pre_cutoff_generation_is_unsafe_even_with_rebound_safety_metadata(self):
        generation, policy, target = self.replay_fixture()
        manifest = self.manifest(generation)
        declared = self.cutoff_policy()
        manifest["account_restore_coverage"] = recovery.ledger_coverage(declared)
        manifest["created_at_utc"] = (datetime.fromisoformat(declared["recovery_boundary"]["cutoff_at_utc"])
                                      - timedelta(seconds=1)).isoformat()
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        self.save_policy(declared)
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result["status"], recovery.UNSAFE_FOR_ACCOUNT_RESTORE)
        self.assertEqual(result["reason"], "generation_before_recovery_cutoff")
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_replay_target_must_match_approved_database_not_another_generation(self):
        generation, policy, target = self.replay_fixture()
        with contextlib.closing(sqlite3.connect(target)) as conn:
            conn.execute("UPDATE users SET cash=200 WHERE user_id=1")
            conn.commit()
        output = self.assert_replay_blocked(self.replay_command(generation, policy, target), target)
        self.assertIn("replay_generation_database_mismatch", output)

    def test_replay_cannot_mutate_generation_or_create_missing_target(self):
        generation, policy, target = self.replay_fixture()
        self.assert_replay_blocked(self.replay_command(generation, policy, generation / "database/clarity.db"),
                                   generation / "database/clarity.db")
        target.unlink()
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)

    def test_replay_rejects_active_production_database_before_import(self):
        generation, policy, target = self.replay_fixture()
        with patch.object(reapply, "__file__", str(self.root / "reapply_account_delete_tombstones.py")):
            active = self.root / "clarity.db"
            shutil.copyfile(target, active)
            active.chmod(0o600)
            output = self.assert_replay_blocked(self.replay_command(generation, policy, active), active)
        self.assertIn("replay_requires_separate_staging_database", output)

    def test_replay_rejects_sidecars_and_different_ledger(self):
        generation, policy, target = self.replay_fixture()
        sidecar = Path(str(target) + "-wal")
        sidecar.write_bytes(b"unapproved")
        self.assert_replay_blocked(self.replay_command(generation, policy, target), target)
        sidecar.unlink()
        self.assert_replay_blocked(self.replay_command(generation, policy, target, "--ledger",
                                                      str(self.root / "other.jsonl")), target)

    def test_readonly_replay_precheck_never_imports_app_or_mutates_inputs(self):
        generation, policy, target = self.replay_fixture()
        before = target.read_bytes(), self.ledger.read_bytes(), policy.read_bytes()
        output = io.StringIO()
        with patch.object(sys, "argv", self.replay_command(generation, policy, target, "--check-only")), \
                contextlib.redirect_stdout(output), patch.object(sqlite3, "connect", wraps=sqlite3.connect) as connect:
            self.assertEqual(reapply.main(), 0)
        self.assertTrue(all("mode=ro" in str(call.args[0]) for call in connect.call_args_list))
        self.assertEqual(before, (target.read_bytes(), self.ledger.read_bytes(), policy.read_bytes()))
        self.assertIn("REPLAY=NOT_RUN", output.getvalue())

    def test_current_ledger_deletions_not_only_captured_prefix_are_replayed(self):
        generation, policy, target = self.replay_fixture()
        self.ledger.write_bytes(self.ledger.read_bytes() + self.ledger_line(1))
        self.assertEqual(reapply.replay_approved_generation(database=target, policy=policy,
                                                           recovery_set=generation), 2)
        with contextlib.closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_ledger_change_during_replay_rolls_back_all_account_mutations(self):
        import rove_app_api as api
        generation, policy, target = self.replay_fixture()
        original_delete = api.delete_user_rows_for_tombstone

        def append_during_delete(conn, user_id):
            original_delete(conn, user_id)
            self.ledger.write_bytes(self.ledger.read_bytes() + self.ledger_line(1))

        with patch.object(api, "delete_user_rows_for_tombstone", side_effect=append_during_delete):
            with self.assertRaisesRegex(recovery.RecoveryError, "ledger_changed_during_replay"):
                reapply.replay_approved_generation(database=target, policy=policy, recovery_set=generation)
        with contextlib.closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_unapproved_baseline_cannot_replay_and_force_option_does_not_exist(self):
        generation, policy, target = self.replay_fixture()
        declared = self.cutoff_policy()
        declared["recovery_boundary"]["baseline_database_sha256"] = "0" * 64
        self.save_policy(declared)
        self.assert_replay_blocked(["reapply_account_delete_tombstones.py", "--db", str(target),
                                   "--policy", str(policy), "--baseline", str(self.db)], target)
        command = [sys.executable, "-B", str(Path(reapply.__file__)),
                   *self.replay_command(generation, policy, target, "--force")[1:]]
        before = target.read_bytes()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --force", result.stderr)
        self.assertEqual(target.read_bytes(), before)

    def test_whitespace_only_ledger_does_not_prove_historical_coverage(self):
        self.ledger.write_bytes(b"\n \n")
        declared = self.inventory["ledger"]
        declared["anchor"] = {"bytes": 3, "sha256": recovery.digest(self.ledger.read_bytes())}
        with self.assertRaisesRegex(recovery.RecoveryError, "empty_ledger_history_not_proven"):
            recovery.check_account_restore_generation(policy_path=self.save_policy(declared), database_path=self.db)

    def test_valid_but_different_manifest_anchor_cannot_override_independent_policy(self):
        self.ledger.write_bytes(self.ledger.read_bytes() + self.ledger_line(1))
        generation, policy, target = self.replay_fixture()
        manifest = self.manifest(generation)
        manifest["ledger"]["anchor"] = {"bytes": self.ledger.stat().st_size,
                                        "sha256": recovery.digest(self.ledger.read_bytes())}
        manifest["account_restore_safety"] = recovery.generation_manifest_safety(manifest)
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        recovery.verify_recovery_set(generation)
        output = self.assert_replay_blocked(self.replay_command(generation, policy, target), target)
        self.assertIn("generation_ledger_anchor_mismatch", output)

    def test_policy_inside_generation_cannot_authorize_itself(self):
        generation, policy, target = self.replay_fixture()
        self.assert_replay_blocked(self.replay_command(generation, generation / "manifest.json", target), target)

    def test_changed_policy_during_replay_rolls_back(self):
        import rove_app_api as api
        generation, policy, target = self.replay_fixture()
        original_delete = api.delete_user_rows_for_tombstone

        def change_policy(conn, user_id):
            original_delete(conn, user_id)
            policy.write_bytes(policy.read_bytes() + b"\n")

        with patch.object(api, "delete_user_rows_for_tombstone", side_effect=change_policy):
            with self.assertRaisesRegex(recovery.RecoveryError, "policy_changed_during_replay"):
                reapply.replay_approved_generation(database=target, policy=policy, recovery_set=generation)
        with contextlib.closing(sqlite3.connect(target)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_replay_staging_database_must_be_private(self):
        generation, policy, target = self.replay_fixture()
        target.chmod(0o644)
        output = self.assert_replay_blocked(self.replay_command(generation, policy, target), target)
        self.assertIn("replay_target_not_private", output)

    def test_prospective_naked_baseline_is_blocked_before_database_access(self):
        declared = self.prospective_policy()
        declared["recovery_boundary"]["baseline_database_sha256"] = recovery.digest(self.db.read_bytes())
        policy = self.save_policy(declared)
        with patch.object(recovery, "file_metadata", side_effect=AssertionError("no DB admission")), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("no SQLite access")):
            result = recovery.classify_account_restore_generation(policy_path=policy, database_path=self.db)
        self.assertEqual(result, {"status": recovery.UNSAFE_FOR_ACCOUNT_RESTORE,
                                  "reason": "prospective_complete_set_required"})

    def test_prospective_byte_identical_pre_t0_copy_remains_unsafe_despite_new_mtime(self):
        old = self.root / "before-t0.db"
        backup.create_verified_backup(self.db, old)
        old_completed = datetime.now(timezone.utc)
        declared = self.prospective_policy()
        cutoff = datetime.now(timezone.utc)
        declared["recovery_boundary"]["cutoff_at_utc"] = cutoff.isoformat()
        new = self.root / "after-t0.db"
        backup.create_verified_backup(self.db, new)
        self.assertLess(old_completed, cutoff)
        self.assertEqual(old.read_bytes(), new.read_bytes())
        declared["recovery_boundary"]["baseline_database_sha256"] = recovery.digest(new.read_bytes())
        policy = self.save_policy(declared)
        os.utime(old, None)
        for database in (old, new):
            with self.subTest(database=database.name):
                result = recovery.classify_account_restore_generation(policy_path=policy, database_path=database)
                self.assertEqual(result["status"], recovery.UNSAFE_FOR_ACCOUNT_RESTORE)

    def test_prospective_pre_and_equal_t0_full_sets_are_unsafe_even_with_trusted_receipt(self):
        generation, declared, policy = self.prospective_fixture()
        original = self.manifest(generation)
        cutoff = recovery.utc_timestamp(declared["recovery_boundary"]["cutoff_at_utc"])
        for started in (cutoff - timedelta(seconds=1), cutoff):
            with self.subTest(started=started):
                manifest = json.loads(json.dumps(original))
                manifest["created_at_utc"] = started.isoformat()
                manifest["account_restore_safety"]["created_at_utc"] = started.isoformat()
                raw = recovery.json_bytes(manifest)
                (generation / "manifest.json").write_bytes(raw)
                declared["generation_receipts"][generation.name] = recovery.digest(raw)
                self.save_policy(declared)
                result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
                self.assertEqual(result, {"status": recovery.UNSAFE_FOR_ACCOUNT_RESTORE,
                                          "reason": "generation_before_recovery_cutoff"})

    def test_prospective_complete_post_t0_set_with_independent_receipt_passes_readonly(self):
        generation, declared, policy = self.prospective_fixture()
        before = {p: p.read_bytes() for p in generation.rglob("*") if p.is_file()}
        policy_before, ledger_before, db_before = policy.read_bytes(), self.ledger.read_bytes(), self.db.read_bytes()
        manifest = recovery.verify_recovery_set(generation)
        self.assertEqual(manifest["ledger"]["anchor_id"], declared["recovery_boundary"]["ledger_anchor_id"])
        self.assertEqual(manifest["runtime_revision_binding"]["status"], "VERIFIED")
        self.assertEqual(manifest["account_restore_safety"]["gate_version"], recovery.PROSPECTIVE_GATE_VERSION)
        self.assertIsNone(manifest["account_restore_coverage"]["baseline_database_sha256"])
        with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as connect:
            result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation,
                                                                   admission_mode="prospective")
        self.assertEqual(result["status"], recovery.SAFE_FOR_ACCOUNT_RESTORE)
        self.assertTrue(all("mode=ro" in str(call.args[0]) for call in connect.call_args_list))
        self.assertEqual(before, {p: p.read_bytes() for p in generation.rglob("*") if p.is_file()})
        self.assertEqual((policy_before, ledger_before, db_before),
                         (policy.read_bytes(), self.ledger.read_bytes(), self.db.read_bytes()))

    def test_recovery_version_contract_is_consistent(self):
        self.assertEqual(recovery.INVENTORY_VERSION, 2)
        self.assertEqual(recovery.MANIFEST_VERSION, 4)
        self.assertEqual(recovery.PROSPECTIVE_GATE_VERSION, "3")
        self.assertEqual(recovery.GENERATION_GATE_VERSION, "1")
        self.assertEqual(recovery.TOOL_VERSION, "1.3")

    def test_prospective_old_full_set_is_unsafe_without_admission_receipt(self):
        generation, declared, policy = self.prospective_fixture()
        declared["recovery_boundary"]["cutoff_at_utc"] = datetime.now(timezone.utc).isoformat()
        declared.pop("generation_receipts")
        self.save_policy(declared)
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result, {"status": recovery.UNSAFE_FOR_ACCOUNT_RESTORE,
                                  "reason": "generation_before_recovery_cutoff"})

    def test_prospective_missing_or_invalid_t0_and_anchor_are_blocked(self):
        for field, value in (("cutoff_at_utc", None), ("cutoff_at_utc", "not-UTC"),
                             ("cutoff_at_utc", "2026-01-01T00:00:00"),
                             ("cutoff_at_utc", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
                             ("ledger_anchor_id", None), ("ledger_anchor_id", "invalid")):
            with self.subTest(field=field, value=value):
                declared = self.prospective_policy()
                if value is None:
                    del declared["recovery_boundary"][field]
                else:
                    declared["recovery_boundary"][field] = value
                result = recovery.classify_account_restore_generation(policy_path=self.save_policy(declared),
                                                                       database_path=self.db)
                self.assertEqual(result["status"], recovery.UNKNOWN)
        for anchor in (None, {"bytes": 0, "sha256": "invalid"}):
            declared = self.prospective_policy()
            if anchor is None:
                del declared["anchor"]
            else:
                declared["anchor"] = anchor
            self.assertEqual(recovery.classify_account_restore_generation(
                policy_path=self.save_policy(declared), database_path=self.db)["status"], recovery.UNKNOWN)

    def test_partial_prospective_policy_has_no_silent_legacy_fallback(self):
        for key in sorted(recovery.PROSPECTIVE_FIELDS):
            with self.subTest(key=key):
                declared = self.prospective_policy()
                declared["recovery_boundary"]["baseline_database_sha256"] = recovery.digest(self.db.read_bytes())
                del declared["recovery_boundary"][key]
                self.assertEqual(recovery.classify_account_restore_generation(
                    policy_path=self.save_policy(declared), database_path=self.db)["status"], recovery.UNKNOWN)
        declared = self.cutoff_policy()
        declared["generation_receipts"] = {}
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=self.save_policy(declared), database_path=self.db)["status"], recovery.UNKNOWN)

    def test_prospective_invalid_policy_and_gate_versions_are_blocked(self):
        for key, value in (("admission_mode", "legacy"), ("policy_version", True), ("policy_version", 2),
                           ("gate_version", recovery.GENERATION_GATE_VERSION), ("gate_version", "99")):
            with self.subTest(key=key, value=value):
                declared = self.prospective_policy()
                declared["recovery_boundary"][key] = value
                self.assertEqual(recovery.classify_account_restore_generation(
                    policy_path=self.save_policy(declared), database_path=self.db)["status"], recovery.UNKNOWN)

    def test_prospective_missing_receipt_or_receipt_for_other_generation_is_unknown(self):
        generation, declared, policy = self.prospective_fixture()
        for receipts in (None, {}, {"recovery_set_20260101T000000Z_" + "0" * 32: "1" * 64}):
            with self.subTest(receipts=receipts):
                if receipts is None:
                    declared.pop("generation_receipts", None)
                else:
                    declared["generation_receipts"] = receipts
                self.save_policy(declared)
                self.assertEqual(recovery.classify_account_restore_generation(
                    policy_path=policy, set_path=generation)["status"], recovery.UNKNOWN)

    def test_prospective_receipt_cannot_be_replaced_by_baseline_hash(self):
        generation, declared, policy = self.prospective_fixture()
        declared["generation_receipts"][generation.name] = recovery.digest((generation / "database/clarity.db").read_bytes())
        self.save_policy(declared)
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result["reason"], "generation_manifest_receipt_mismatch")
        self.assertEqual(result["status"], recovery.UNSAFE_FOR_ACCOUNT_RESTORE)

    def test_prospective_manipulated_timestamp_fails_even_with_rebound_self_safety(self):
        generation, _, policy = self.prospective_fixture()
        manifest = self.manifest(generation)
        manifest["created_at_utc"] = self.changed_created_at(manifest)
        manifest["account_restore_safety"] = recovery.generation_manifest_safety(manifest)
        (generation / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        recovery.verify_recovery_set(generation)
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result["reason"], "generation_manifest_receipt_mismatch")
        self.assertEqual(result["status"], recovery.UNSAFE_FOR_ACCOUNT_RESTORE)

    def test_prospective_timestamp_without_safety_binding_is_blocked(self):
        generation, declared, policy = self.prospective_fixture()
        manifest = self.manifest(generation)
        manifest["created_at_utc"] = self.changed_created_at(manifest)
        raw = recovery.json_bytes(manifest)
        (generation / "manifest.json").write_bytes(raw)
        declared["generation_receipts"][generation.name] = recovery.digest(raw)
        self.save_policy(declared)
        result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result["reason"], "generation_safety_binding_invalid")
        self.assertEqual(result["status"], recovery.UNKNOWN)

    def test_prospective_invalid_generation_id_and_id_time_mismatch_are_blocked(self):
        generation, declared, policy = self.prospective_fixture()
        original = self.manifest(generation)
        for identity in ("arbitrary", "recovery_set_20260101T000000Z_" + "0" * 32):
            with self.subTest(identity=identity):
                manifest = json.loads(json.dumps(original))
                manifest["recovery_set_id"] = identity
                with self.assertRaises(recovery.RecoveryError):
                    recovery.generation_manifest_safety(manifest)
        manifest = json.loads(json.dumps(original))
        manifest["created_at_utc"] = (recovery.utc_timestamp(manifest["created_at_utc"])
                                      - timedelta(seconds=1)).isoformat()
        raw = recovery.json_bytes(manifest)
        (generation / "manifest.json").write_bytes(raw)
        declared["generation_receipts"][generation.name] = recovery.digest(raw)
        self.save_policy(declared)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["reason"], "generation_time_identity_mismatch")

    def test_prospective_wrong_trusted_anchor_id_or_prefix_cannot_admit_set(self):
        generation, declared, policy = self.prospective_fixture()
        declared["recovery_boundary"]["ledger_anchor_id"] = "ledger_anchor_" + "2" * 32
        self.save_policy(declared)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["reason"], "generation_policy_mismatch")
        declared["recovery_boundary"]["ledger_anchor_id"] = "ledger_anchor_" + "1" * 32
        declared["anchor"]["sha256"] = "0" * 64
        self.save_policy(declared)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["reason"], "ledger_history_changed")

    def test_prospective_missing_manifest_anchor_id_is_blocked(self):
        generation, declared, policy = self.prospective_fixture()
        manifest = self.manifest(generation)
        del manifest["ledger"]["anchor_id"]
        raw = recovery.json_bytes(manifest)
        (generation / "manifest.json").write_bytes(raw)
        declared["generation_receipts"][generation.name] = recovery.digest(raw)
        self.save_policy(declared)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["reason"], "generation_ledger_anchor_mismatch")

    def test_prospective_git_and_schema_are_bound_to_independent_policy(self):
        generation, declared, policy = self.prospective_fixture()
        for field in ("expected_git_sha", "expected_schema_sha256"):
            with self.subTest(field=field):
                original = declared["recovery_boundary"][field]
                declared["recovery_boundary"][field] = "0" * len(original)
                self.save_policy(declared)
                self.assertEqual(recovery.classify_account_restore_generation(
                    policy_path=policy, set_path=generation)["reason"], "generation_policy_mismatch")
                declared["recovery_boundary"][field] = original

    def test_prospective_missing_required_file_and_incomplete_set_are_blocked(self):
        generation, declared, policy = self.prospective_fixture()
        runtime = generation / "runtime/metadata.json"
        original_runtime = runtime.read_bytes()
        runtime.unlink()
        self.assertNotEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["status"], recovery.SAFE_FOR_ACCOUNT_RESTORE)
        recovery.private_write(runtime, original_runtime)
        manifest = self.manifest(generation)
        manifest["status"] = "FAILED"
        raw = recovery.json_bytes(manifest)
        (generation / "manifest.json").write_bytes(raw)
        declared["generation_receipts"][generation.name] = recovery.digest(raw)
        self.save_policy(declared)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation)["reason"], "set_not_complete")

    def test_prospective_unknown_and_unsafe_are_blocked_even_with_matching_receipt(self):
        generation, declared, policy = self.prospective_fixture()
        original = self.manifest(generation)
        for status in (recovery.UNKNOWN, recovery.UNSAFE_FOR_ACCOUNT_RESTORE):
            with self.subTest(status=status):
                manifest = json.loads(json.dumps(original))
                manifest["account_restore_safety"]["status"] = status
                raw = recovery.json_bytes(manifest)
                (generation / "manifest.json").write_bytes(raw)
                declared["generation_receipts"][generation.name] = recovery.digest(raw)
                self.save_policy(declared)
                self.assertEqual(recovery.classify_account_restore_generation(
                    policy_path=policy, set_path=generation)["status"], status)

    def test_prospective_mode_cannot_be_downgraded_by_cli_expectation(self):
        generation, _, policy = self.prospective_fixture()
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, set_path=generation, admission_mode="legacy")["reason"],
            "generation_admission_mode_mismatch")
        legacy = self.cutoff_policy()
        legacy.pop("generation_receipts", None)
        policy = self.save_policy(legacy)
        self.assertEqual(recovery.classify_account_restore_generation(
            policy_path=policy, database_path=self.db, admission_mode="prospective")["reason"],
            "generation_admission_mode_mismatch")

    def test_prospective_canonical_replay_baseline_blocks_before_import_or_write(self):
        policy = self.save_policy(self.prospective_policy())
        target = self.root / "staged.db"
        shutil.copyfile(self.db, target)
        target.chmod(0o600)
        output = self.assert_replay_blocked(["reapply_account_delete_tombstones.py", "--db", str(target),
                                             "--policy", str(policy), "--baseline", str(self.db)], target)
        self.assertIn("prospective_complete_set_required", output)

    def test_prospective_canonical_replay_check_only_accepts_complete_set_without_mutation(self):
        generation, _, policy = self.prospective_fixture()
        target = self.root / "staged.db"
        shutil.copyfile(generation / "database/clarity.db", target)
        target.chmod(0o600)
        before = target.read_bytes(), self.ledger.read_bytes(), policy.read_bytes()
        output = io.StringIO()
        with patch.object(sys, "argv", self.replay_command(generation, policy, target, "--check-only")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(reapply.main(), 0)
        self.assertIn("REPLAY=NOT_RUN", output.getvalue())
        self.assertEqual(before, (target.read_bytes(), self.ledger.read_bytes(), policy.read_bytes()))

    def test_prospective_canonical_cli_accepts_only_bound_complete_sets(self):
        generation, declared, policy = self.prospective_fixture()
        command = [sys.executable, "-B", str(Path(recovery.__file__)), "generation-gate", "--policy", str(policy)]
        for arguments, code, status in ((["--set", str(generation)], 0, recovery.SAFE_FOR_ACCOUNT_RESTORE),
                                        (["--db", str(self.db)], 1, recovery.UNSAFE_FOR_ACCOUNT_RESTORE),
                                        (["--set", str(generation), "--admission-mode", "legacy"], 1,
                                         recovery.UNSAFE_FOR_ACCOUNT_RESTORE)):
            with self.subTest(arguments=arguments):
                result = subprocess.run([*command, *arguments], capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn("ACCOUNT_RESTORE_GATE=" + status, result.stdout)
                self.assertNotIn(self.secret, result.stdout + result.stderr)
        for field in ("cutoff_at_utc", "ledger_anchor_id"):
            value = declared["recovery_boundary"].pop(field)
            self.save_policy(declared)
            result = subprocess.run([*command, "--set", str(generation)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ACCOUNT_RESTORE_GATE=UNKNOWN", result.stdout)
            declared["recovery_boundary"][field] = value
        self.save_policy(declared)
        result = subprocess.run([*command, "--set", str(generation), "--force"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_prospective_cli_rejects_full_set_provenance_failures(self):
        generation, declared, policy = self.prospective_fixture()
        original = self.manifest(generation)
        command = [sys.executable, "-B", str(Path(recovery.__file__)), "generation-gate",
                   "--policy", str(policy), "--set", str(generation), "--admission-mode", "prospective"]
        cutoff = declared["recovery_boundary"]["cutoff_at_utc"]
        for category in ("pre-t0", "equal-t0", "unbound-time", "changed-time", "wrong-anchor", "unknown", "manual-safe"):
            with self.subTest(category=category):
                manifest = json.loads(json.dumps(original))
                if category in {"pre-t0", "equal-t0"}:
                    manifest["created_at_utc"] = (recovery.utc_timestamp(cutoff) - timedelta(
                        seconds=1 if category == "pre-t0" else 0)).isoformat()
                elif category in {"unbound-time", "changed-time"}:
                    manifest["created_at_utc"] = self.changed_created_at(manifest)
                    if category == "changed-time":
                        manifest["account_restore_safety"] = recovery.generation_manifest_safety(manifest)
                elif category == "wrong-anchor":
                    manifest["ledger"]["anchor_id"] = "ledger_anchor_" + "2" * 32
                elif category == "unknown":
                    manifest["account_restore_safety"]["status"] = recovery.UNKNOWN
                else:
                    manifest["account_restore_safety"] = {"status": recovery.SAFE_FOR_ACCOUNT_RESTORE}
                raw = recovery.json_bytes(manifest)
                (generation / "manifest.json").write_bytes(raw)
                # Even a receipt cannot authorize a structurally invalid set;
                # a self-rebound valid timestamp still cannot replace the receipt.
                declared["generation_receipts"][generation.name] = recovery.digest(
                    recovery.json_bytes(original) if category == "changed-time" else raw)
                self.save_policy(declared)
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("ACCOUNT_RESTORE_GATE=" + recovery.SAFE_FOR_ACCOUNT_RESTORE, result.stdout)
                self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_prospective_malformed_receipts_are_blocked_without_fallback(self):
        for receipts in ([], None, {"invalid-id": "1" * 64},
                         {"recovery_set_20260101T000000Z_" + "0" * 32: "invalid-hash"}):
            with self.subTest(receipts=receipts):
                declared = self.prospective_policy()
                declared["generation_receipts"] = receipts
                result = recovery.classify_account_restore_generation(
                    policy_path=self.save_policy(declared), database_path=self.db)
                self.assertEqual(result, {"status": recovery.UNKNOWN, "reason": "generation_receipts_invalid"})

    def test_prospective_manifest_change_during_verification_is_blocked(self):
        generation, _, policy = self.prospective_fixture()
        original_verify = recovery.verify_recovery_set

        def change_after_verify(path):
            manifest = original_verify(path)
            manifest["completed_at_utc"] = "tampered-after-verification"
            (path / "manifest.json").write_bytes(recovery.json_bytes(manifest))
            return manifest

        with patch.object(recovery, "verify_recovery_set", side_effect=change_after_verify):
            result = recovery.classify_account_restore_generation(policy_path=policy, set_path=generation)
        self.assertEqual(result, {"status": recovery.UNKNOWN, "reason": "generation_manifest_changed"})

    def test_prospective_collection_at_t0_never_marks_set_safe(self):
        declared = self.prospective_policy()
        self.inventory["ledger"] = declared
        self.save_inventory()
        with patch.object(recovery, "utc_now", return_value=declared["recovery_boundary"]["cutoff_at_utc"]):
            generation = self.collect()
        manifest = self.manifest(generation)
        self.assertEqual(manifest["status"], "FAILED")
        self.assertEqual(manifest["error_code"], "generation_before_recovery_cutoff")
        self.assertEqual(manifest["account_restore_safety"]["status"], recovery.UNSAFE_FOR_ACCOUNT_RESTORE)


class EffectiveSystemdInventoryTests(unittest.TestCase):
    def inventory(self, output):
        with patch.object(recovery.subprocess, "check_output", return_value=output.encode()) as command:
            result = recovery.systemd_api_configuration()
        arguments = command.call_args.args[0]
        self.assertNotIn("--property=Environment", arguments)
        self.assertNotIn("--property=ExecStart", arguments)
        return result

    def test_zero_one_and_multiple_dropin_paths_are_sorted(self):
        for dropins in ([], ["/etc/systemd/a.conf"], ["/etc/systemd/z.conf", "/etc/systemd/a.conf"]):
            with self.subTest(dropins=dropins):
                output = "LoadState=loaded\nFragmentPath=/etc/systemd/api.service\nDropInPaths=" + " ".join(dropins)
                result = self.inventory(output + "\nEnvironmentFiles=\n")
                self.assertEqual(result["drop_in_paths"], sorted(dropins))

    def test_environment_files_accept_repeated_and_combined_properties(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve()
            first, second = path / ".env", path / ".rove-app-api.env"
            first.write_text("SYNTHETIC_SECRET=not-displayed")
            second.write_text("SYNTHETIC_SECRET=not-displayed")
            values = [f"{first} (ignore_errors=no)", f"{second} (ignore_errors=yes)"]
            for tail in ("EnvironmentFiles=" + " ".join(values),
                         "\n".join("EnvironmentFiles=" + value for value in values)):
                result = self.inventory("LoadState=loaded\nFragmentPath=/etc/systemd/api.service\nDropInPaths=\n" + tail)
                self.assertEqual(result["environment_files"], sorted([str(first), str(second)]))
                self.assertNotIn("not-displayed", json.dumps(result))

    def test_missing_optional_environment_is_recorded_but_required_file_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp).resolve() / "missing.env"
            output = f"LoadState=loaded\nFragmentPath=/etc/systemd/api.service\nDropInPaths=\nEnvironmentFiles={missing}"
            result = self.inventory(output + " (ignore_errors=yes)")
            self.assertEqual(result["absent_optional_environment_files"], [str(missing)])
            with self.assertRaisesRegex(recovery.RecoveryError, "effective_environment_file_missing"):
                self.inventory(output + " (ignore_errors=no)")

    def test_unavailable_systemd_or_malformed_paths_fail_closed(self):
        with patch.object(recovery.subprocess, "check_output", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(recovery.RecoveryError, "effective_systemd_inventory_unavailable"):
                recovery.systemd_api_configuration()
        for output in ("LoadState=not-found\nFragmentPath=\nDropInPaths=",
                       "LoadState=loaded\nFragmentPath=/etc/systemd/api.service\nDropInPaths=relative.conf",
                       "LoadState=loaded\nFragmentPath=/etc/systemd/api.service\nDropInPaths=/etc/../other.conf",
                       "LoadState=loaded\nFragmentPath=/etc/systemd/api.service\nDropInPaths=/a.conf /a.conf"):
            with self.subTest(output=output), self.assertRaises(recovery.RecoveryError):
                self.inventory(output)


class ExistingBackupRegressionTests(unittest.TestCase):
    def test_integrity_check_results_are_enforced_without_leaking_details(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [("synthetic page corruption detail",)]
        with patch.object(backup.sqlite3, "connect", return_value=conn):
            with self.assertRaisesRegex(backup.BackupValidationError, "^integrity_check_failed$"):
                backup.verify_database(Path("/synthetic/immutable.db"))
        conn.execute.assert_called_once_with("PRAGMA integrity_check")
        conn.close.assert_called_once()

    def test_backup_success_then_rotation_and_readonly_source(self):
        with tempfile.TemporaryDirectory(prefix="rove-backup-test-") as temp:
            root = Path(temp).resolve()
            db = root / "source.db"
            destination = root / "automatic"
            destination.mkdir()
            with contextlib.closing(sqlite3.connect(db)) as conn:
                conn.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users VALUES (1);")
            original = db.read_bytes()
            old = destination / "clarity_auto_20000101_000000.db"
            old.write_bytes(b"old snapshot")
            os.utime(old, (0, 0))
            manual = destination / "manual.db"
            manual.write_bytes(b"manual snapshot")
            with patch.object(sys, "argv", ["backup_clarity_db.py", "--db", str(db), "--backup-dir", str(destination), "--keep-days", "30"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(backup.main(), 0)
            self.assertFalse(old.exists())
            self.assertTrue(manual.exists())
            snapshots = list(destination.glob("clarity_auto_*.db"))
            self.assertEqual(len(snapshots), 1)
            backup.verify_database(snapshots[0])
            self.assertEqual(snapshots[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(db.read_bytes(), original)

    def test_failed_backup_does_not_rotate_or_overwrite_existing_copy(self):
        with tempfile.TemporaryDirectory(prefix="rove-backup-test-") as temp:
            root = Path(temp).resolve()
            db = root / "source.db"
            with contextlib.closing(sqlite3.connect(db)) as conn:
                conn.executescript("CREATE TABLE parent(id PRIMARY KEY);"
                                   "CREATE TABLE child(id REFERENCES parent(id)); INSERT INTO child VALUES (123);")
            output = root / "new.db"
            with self.assertRaises(RuntimeError):
                backup.create_verified_backup(db, output)
            self.assertFalse(output.exists())
            output.write_bytes(b"do not overwrite")
            with self.assertRaises(FileExistsError):
                backup.create_verified_backup(db, output)
            self.assertEqual(output.read_bytes(), b"do not overwrite")

    def test_missing_database_never_creates_empty_source(self):
        with tempfile.TemporaryDirectory(prefix="rove-backup-test-") as temp:
            root = Path(temp).resolve()
            with self.assertRaises(FileNotFoundError):
                backup.create_verified_backup(root / "missing.db", root / "copy.db")
            self.assertFalse((root / "missing.db").exists())
            self.assertFalse((root / "copy.db").exists())

    def test_main_failure_never_rotates_existing_backups(self):
        with tempfile.TemporaryDirectory(prefix="rove-backup-test-") as temp:
            root = Path(temp).resolve()
            source = root / "invalid.db"
            with contextlib.closing(sqlite3.connect(source)) as conn:
                conn.executescript("CREATE TABLE parent(id PRIMARY KEY);"
                                   "CREATE TABLE child(id REFERENCES parent(id)); INSERT INTO child VALUES (123);")
            destination = root / "automatic"
            destination.mkdir()
            old = destination / "clarity_auto_20000101_000000.db"
            old.write_bytes(b"older recovery candidate")
            os.utime(old, (0, 0))
            with patch.object(sys, "argv", ["backup_clarity_db.py", "--db", str(source), "--backup-dir", str(destination)]):
                with patch.object(backup, "cleanup_old_backups") as rotation:
                    with self.assertRaises(backup.BackupValidationError):
                        backup.main()
                    rotation.assert_not_called()
            self.assertEqual(old.read_bytes(), b"older recovery candidate")
            self.assertEqual(list(destination.iterdir()), [old])


if __name__ == "__main__":
    unittest.main()
