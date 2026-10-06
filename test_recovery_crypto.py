"""Real GPG round trips use disposable keys, repositories and synthetic sets only."""

from __future__ import annotations

import builtins
import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import rove_recovery_crypto as crypto
import rove_recovery_set as recovery
import test_recovery_set as fixtures


class RecoveryCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gpg = shutil.which("gpg")
        if not cls.gpg:
            raise RuntimeError("GPG >= 2.4.4 required; crypto gate must not silently skip")
        cls.keys_temp = tempfile.TemporaryDirectory(prefix="rvc-keys-", dir=str(Path("/tmp").resolve()))
        cls.addClassCleanup(cls.keys_temp.cleanup)
        cls.keys = Path(cls.keys_temp.name)
        cls.passphrase = b"synthetic-test-only-offline-passphrase"
        cls.keypairs = []
        for number in range(2):
            with crypto.gpg_session(cls.gpg) as client:
                client.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0",
                            "--quick-generate-key", f"Synthetic Recovery {number} <fixture{number}@example.invalid>",
                            "rsa2048", "encr", "1d"], secret=cls.passphrase + b"\n", agent=True)
                listing = client.run(["--with-colons", "--list-keys"])
                fpr = next(line.split(b":")[9].decode() for line in listing.splitlines() if line.startswith(b"fpr:"))
                public, private = cls.keys / f"public-{number}.asc", cls.keys / f"private-{number}.asc"
                public.write_bytes(client.run(["--armor", "--export", fpr]))
                private.write_bytes(client.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0",
                                               "--armor", "--export-secret-keys", fpr],
                                              secret=cls.passphrase + b"\n", agent=True))
                public.chmod(0o600)
                private.chmod(0o600)
                cls.keypairs.append((fpr, public, private))

    def setUp(self):
        self.fixture = fixtures.RecoverySetTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.add_public()
        (self.fixture.reports / "rove_report_1_2026-09.pdf").write_bytes(b"%PDF-1.4\nSynthetic only\n")
        self.source = self.fixture.collect()
        self.assertEqual(self.fixture.manifest(self.source)["status"], "COMPLETE")
        self.fpr, self.public, self.private = self.keypairs[0]
        self.output = self.root / "encrypted"
        self.offline = self.root / "verified"

    def encrypt(self, **kwargs):
        arguments = dict(source=self.source, public_key=self.public, expected_fingerprint=self.fpr,
                         output=self.output, gpg=self.gpg, gpg_temp_parent=self.keys)
        arguments.update(kwargs)
        self.metadata = crypto.encrypt_set(**arguments)
        self.cipher = self.output / (self.source.name + ".tar.gz.gpg")
        self.meta = self.output / "crypto.json"
        return self.metadata

    def verify(self, **kwargs):
        arguments = dict(ciphertext=self.cipher, metadata_path=self.meta,
                         metadata_sha256=self.metadata["metadata_sha256"], private_key=self.private,
                         expected_fingerprint=self.fpr, output=self.offline,
                         passphrase=self.passphrase, gpg=self.gpg, gpg_temp_parent=self.keys)
        arguments.update(kwargs)
        return crypto.verify_offline(**arguments)

    def rewrite_meta(self, **changes):
        data = json.loads(self.meta.read_bytes())
        data.update(changes)
        raw = recovery.json_bytes(data)
        self.meta.write_bytes(raw)
        self.metadata["metadata_sha256"] = hashlib.sha256(raw).hexdigest()

    def rewrite_manifest(self, update):
        data = self.fixture.manifest(self.source)
        update(data)
        (self.source / "manifest.json").write_bytes(recovery.json_bytes(data))

    def assert_rejected(self, action, *, output=None):
        with self.assertRaises((crypto.CryptoError, recovery.RecoveryError, OSError, ValueError,
                                KeyError, tarfile.TarError, EOFError, sqlite3.DatabaseError,
                                recovery.BackupValidationError)):
            action()
        self.assertFalse((output or self.output).exists())
        self.assertFalse(list(self.root.glob(".recovery-crypto-*")))

    def replace_archive(self, build):
        archive = self.root / "replacement.tar.gz"
        build(archive)
        self.cipher.unlink()
        with crypto.gpg_session(self.gpg) as client:
            recipient = client.import_key(self.public, self.fpr)
            client.encrypt(archive, self.cipher, recipient)
        self.rewrite_meta(archive_sha256=crypto.file_hash(archive),
                          ciphertext_sha256=crypto.file_hash(self.cipher), ciphertext_bytes=self.cipher.stat().st_size)

    @staticmethod
    def tree_bytes(root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def test_valid_round_trip_byte_exact_and_private(self):
        original = self.tree_bytes(self.source)
        result = self.encrypt()
        verified = self.verify(reference_set=self.source)
        self.assertEqual(verified["verification"], "PASS")
        self.assertEqual(verified["account_restore"], "NOT_AUTHORIZED")
        self.assertTrue(verified["reference_compared"])
        self.assertEqual(self.tree_bytes(self.source), original)
        self.assertEqual(self.tree_bytes(self.offline / self.source.name), original)
        self.assertEqual(set(result) - {"metadata_sha256"}, crypto.META_FIELDS)
        self.assertEqual(set(p.name for p in self.output.iterdir()), {self.cipher.name, "crypto.json"})
        self.assertEqual(set(p.name for p in self.offline.iterdir()), {self.source.name})
        for root in (self.output, self.offline):
            for path in [root, *root.rglob("*")]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)

    def test_gpg_session_uses_explicit_private_workspace(self):
        private_workspace = self.keys
        observed = []

        class StubGPG:
            def __init__(self, executable, home):
                observed.append(Path(home))

            def close(self):
                pass

        with patch.object(crypto, "GPG", StubGPG):
            with crypto.gpg_session(self.gpg, temp_parent=private_workspace):
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0].parent, private_workspace)
                self.assertEqual(observed[0].stat().st_mode & 0o777, 0o700)
        self.assertFalse(observed[0].exists())

    def test_gpg_session_rejects_long_socket_paths(self):
        long_parent = self.root / ("p" * 60) / ("q" * 60)
        long_parent.mkdir(parents=True, mode=0o700)
        with self.assertRaisesRegex(crypto.CryptoError, "gpg_workspace_path_too_long"):
            with crypto.gpg_session(self.gpg, temp_parent=long_parent):
                self.fail("long socket path must fail before starting GPG")

    def test_deterministic_archive_ignores_filesystem_times(self):
        first, second = self.root / "one.tar.gz", self.root / "two.tar.gz"
        crypto.archive_set(self.source, first)
        for path in self.source.rglob("*"):
            os.utime(path, (100000, 100000))
        crypto.archive_set(self.source, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(archive.getnames(), sorted(archive.getnames()))
            self.assertTrue(all(item.mtime == 0 and item.uid == 0 for item in archive))

    def test_gzip_variation_does_not_break_offline_verification(self):
        self.encrypt()
        def build(path):
            tar_path = self.root / "uncompressed.tar"
            crypto.archive_set(self.source, tar_path, compressed=False)
            path.write_bytes(gzip.compress(tar_path.read_bytes(), compresslevel=1, mtime=123))
        self.replace_archive(build)
        self.assertEqual(self.verify()["verification"], "PASS")

    def test_pinned_encryption_subkey_round_trip(self):
        with crypto.gpg_session(self.gpg) as client:
            client.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0", "--quick-generate-key",
                        "Synthetic Subkey <subkey@example.invalid>", "rsa2048", "cert", "0"],
                       secret=self.passphrase + b"\n", agent=True)
            listing = client.run(["--with-colons", "--list-keys"])
            fpr = next(line.split(b":")[9].decode() for line in listing.splitlines() if line.startswith(b"fpr:"))
            client.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0", "--quick-add-key",
                        fpr, "cv25519", "encr", "0"], secret=self.passphrase + b"\n", agent=True)
            public, private = self.root / "subkey-public.asc", self.root / "subkey-private.asc"
            public.write_bytes(client.run(["--armor", "--export", fpr]))
            private.write_bytes(client.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0",
                                            "--armor", "--export-secret-keys", fpr],
                                           secret=self.passphrase + b"\n", agent=True))
            private.chmod(0o600)
        self.encrypt(public_key=public, expected_fingerprint=fpr)
        self.assertNotEqual(self.metadata["encryption_key_fingerprint"], fpr)
        result = self.verify(private_key=private, expected_fingerprint=fpr)
        self.assertEqual(result["verification"], "PASS")

    def test_expired_key_blocks_new_encryption_but_allows_historical_decryption(self):
        self.encrypt()
        original = crypto.GPG.run
        future = str(int(time.time()) + 2 * 86400)
        def in_future(client, args, **kwargs):
            return original(client, ["--faked-system-time", future, *args], **kwargs)
        with patch.object(crypto.GPG, "run", new=in_future):
            self.assert_rejected(lambda: self.encrypt(output=self.root / "expired"), output=self.root / "expired")
            self.assertEqual(self.verify()["verification"], "PASS")

    def test_ciphertext_randomized_archive_hash_stable(self):
        first = self.encrypt()
        second = crypto.encrypt_set(self.source, self.public, self.fpr, self.root / "encrypted2",
                                     gpg=self.gpg, gpg_temp_parent=self.keys)
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        self.assertNotEqual(first["ciphertext_sha256"], second["ciphertext_sha256"])

    def test_failed_set_rejected(self):
        self.rewrite_manifest(lambda m: m.update(status="FAILED"))
        self.assert_rejected(self.encrypt)

    def test_unsafe_and_unknown_generations_rejected(self):
        for status in (recovery.UNSAFE_FOR_ACCOUNT_RESTORE, recovery.UNKNOWN, "SAFE", None):
            with self.subTest(status=status):
                self.rewrite_manifest(lambda m: m["account_restore_safety"].update(status=status))
                self.assert_rejected(self.encrypt)

    def test_wrong_public_fingerprint(self):
        self.assert_rejected(lambda: self.encrypt(expected_fingerprint=self.keypairs[1][0]))

    def test_wrong_recipient_public_key(self):
        self.assert_rejected(lambda: self.encrypt(public_key=self.keypairs[1][1]))

    def test_multiple_recipients_and_private_material_rejected(self):
        combined = self.root / "combined.asc"
        combined.write_bytes(self.public.read_bytes() + self.keypairs[1][1].read_bytes())
        self.assert_rejected(lambda: self.encrypt(public_key=combined))
        self.assert_rejected(lambda: self.encrypt(public_key=self.private))

    def test_short_fingerprint_rejected(self):
        self.assert_rejected(lambda: self.encrypt(expected_fingerprint=self.fpr[-16:]))

    def test_wrong_private_key(self):
        self.encrypt()
        self.assert_rejected(lambda: self.verify(private_key=self.keypairs[1][2]), output=self.offline)

    def test_public_key_cannot_decrypt(self):
        self.encrypt()
        self.assert_rejected(lambda: self.verify(private_key=self.public), output=self.offline)

    def test_wrong_passphrase_and_empty_agent_cache(self):
        self.encrypt()
        self.verify()
        self.assert_rejected(lambda: self.verify(passphrase=b"wrong", output=self.root / "wrong-phrase"),
                             output=self.root / "wrong-phrase")

    def test_damaged_ciphertext(self):
        self.encrypt()
        raw = bytearray(self.cipher.read_bytes())
        raw[len(raw) // 2] ^= 1
        self.cipher.write_bytes(raw)
        self.assert_rejected(self.verify, output=self.offline)

    def test_damaged_ciphertext_with_rebound_hash_fails_gpg_integrity(self):
        self.encrypt()
        raw = bytearray(self.cipher.read_bytes())
        raw[-16] ^= 1
        self.cipher.write_bytes(raw)
        self.rewrite_meta(ciphertext_sha256=crypto.file_hash(self.cipher))
        self.assert_rejected(self.verify, output=self.offline)

    def test_altered_ciphertext_hash(self):
        self.encrypt()
        self.rewrite_meta(ciphertext_sha256="0" * 64)
        self.assert_rejected(self.verify, output=self.offline)

    def test_metadata_needs_independent_trusted_receipt(self):
        self.encrypt()
        self.meta.write_bytes(self.meta.read_bytes() + b"\n")
        self.assert_rejected(self.verify, output=self.offline)

    def test_metadata_fingerprint_cannot_select_other_key(self):
        self.encrypt()
        self.rewrite_meta(public_key_fingerprint=self.keypairs[1][0])
        self.assert_rejected(self.verify, output=self.offline)

    def test_wrong_archive_hash(self):
        self.encrypt()
        self.rewrite_meta(archive_sha256="0" * 64)
        self.assert_rejected(self.verify, output=self.offline)

    def test_altered_manifest_git_and_encryption_fingerprint_bindings(self):
        self.encrypt()
        original = json.loads(self.meta.read_bytes())
        for key in ("manifest_sha256", "git_sha", "encryption_key_fingerprint"):
            with self.subTest(binding=key):
                self.rewrite_meta(**original)
                self.rewrite_meta(**{key: "0" * len(original[key])})
                self.assert_rejected(self.verify, output=self.offline)

    def test_changed_file_after_decryption(self):
        self.encrypt()
        unpack = crypto.unpack
        def tamper(*args):
            result = unpack(*args)
            (result / "ledger/account_delete_tombstones.jsonl").write_bytes(b"{}\n")
            return result
        with patch.object(crypto, "unpack", side_effect=tamper):
            self.assert_rejected(self.verify, output=self.offline)

    def test_invalid_manifest(self):
        (self.source / "manifest.json").write_bytes(b'{"status":"COMPLETE","status":"FAILED"}')
        self.assert_rejected(self.encrypt)

    def test_manifest_external_database_path_never_opened(self):
        self.rewrite_manifest(lambda m: m["database"].update(path=str(self.fixture.db)))
        with patch.object(sqlite3, "connect", side_effect=AssertionError("SQL not allowed")):
            self.assert_rejected(self.encrypt)

    def test_missing_required_file(self):
        (self.source / "ledger/account_delete_tombstones.jsonl").unlink()
        self.assert_rejected(self.encrypt)

    def test_unexpected_file_and_empty_directory(self):
        extra = self.source / "unexpected"
        extra.write_text("synthetic only")
        extra.chmod(0o600)
        self.assert_rejected(self.encrypt)
        extra.unlink()
        extra.mkdir(mode=0o700)
        self.assert_rejected(self.encrypt)

    def test_symlink_file_and_directory_and_source_root(self):
        original = self.source / "database/clarity.db"
        raw = original.read_bytes()
        original.unlink()
        original.symlink_to(self.fixture.db)
        self.assert_rejected(self.encrypt)
        original.unlink()
        original.write_bytes(raw)
        original.chmod(0o600)
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        self.assert_rejected(lambda: self.encrypt(source=alias))
        shutil.rmtree(self.source / "database")
        (self.source / "database").symlink_to(self.root, target_is_directory=True)
        self.assert_rejected(self.encrypt)

    def test_hardlink_input_rejected(self):
        os.link(self.source / "database/clarity.db", self.root / "hardlink.db")
        self.assert_rejected(self.encrypt)

    def test_nonregular_fifo_input_rejected_without_blocking(self):
        ledger = self.source / "ledger/account_delete_tombstones.jsonl"
        ledger.unlink()
        os.mkfifo(ledger, 0o600)
        self.assert_rejected(self.encrypt)

    def test_changed_source_after_archive_is_not_published(self):
        archive = crypto.archive_set
        def tamper(*args):
            archive(*args)
            path = self.source / "late-file"
            path.write_bytes(b"unexpected")
            path.chmod(0o600)
        with patch.object(crypto, "archive_set", side_effect=tamper):
            self.assert_rejected(self.encrypt)

    def test_changed_source_file_after_archive_is_not_published(self):
        archive = crypto.archive_set
        def tamper(*args):
            archive(*args)
            (self.source / "ledger/account_delete_tombstones.jsonl").write_bytes(b"changed")
        with patch.object(crypto, "archive_set", side_effect=tamper):
            self.assert_rejected(self.encrypt)

    def test_malicious_archive_paths_and_links(self):
        self.encrypt()
        cases = [("../escaped", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                 (self.source.name + "/../escaped", tarfile.REGTYPE),
                 (self.source.name + "/link", tarfile.SYMTYPE),
                 (self.source.name + "/hard", tarfile.LNKTYPE)]
        for name, kind in cases:
            with self.subTest(kind=kind, name=name):
                def build(path):
                    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
                        item = tarfile.TarInfo(name)
                        item.type, item.mode = kind, 0o600
                        if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                            item.linkname = "/outside"
                        archive.addfile(item, io.BytesIO())
                self.replace_archive(build)
                self.assert_rejected(self.verify, output=self.offline)
        self.assertFalse((self.root / "escaped").exists())

    def test_duplicate_archive_member(self):
        self.encrypt()
        def build(path):
            with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
                item = tarfile.TarInfo(self.source.name)
                item.type, item.mode = tarfile.DIRTYPE, 0o700
                archive.addfile(item)
                archive.addfile(item)
        self.replace_archive(build)
        self.assert_rejected(self.verify, output=self.offline)

    def test_noncanonical_archive_trailing_content_rejected(self):
        self.encrypt()
        def build(path):
            crypto.archive_set(self.source, path)
            with path.open("ab") as handle:
                handle.write(gzip.compress(b"unexpected trailing archive bytes"))
        self.replace_archive(build)
        self.assert_rejected(self.verify, output=self.offline)

    def test_decompression_limit(self):
        path = self.root / "large.tar.gz"
        path.write_bytes(gzip.compress(b"x" * 2048))
        isolated = self.root / "extract"
        isolated.mkdir(mode=0o700)
        with patch.object(crypto, "MAX_ARCHIVE", 1024), self.assertRaises(crypto.CryptoError):
            crypto.unpack(path, isolated, self.source.name)

    def test_no_private_key_needed_on_encrypting_side(self):
        imported = []
        original = crypto.GPG.run
        def run(client, args, **kwargs):
            self.assertFalse(kwargs.get("agent", False))
            if "--import" in args:
                imported.append(Path(args[-1]).read_bytes())
            return original(client, args, **kwargs)
        with patch.object(crypto.GPG, "run", new=run):
            self.encrypt()
        self.assertTrue(imported)
        self.assertTrue(all(b"PRIVATE KEY" not in value for value in imported))

    def test_offline_has_no_source_db_app_provider_or_network_dependency(self):
        self.encrypt()
        shutil.rmtree(self.source)
        self.fixture.db.unlink()
        self.fixture.env.unlink()
        self.fixture.inventory_path.unlink()
        connect, original_import = sqlite3.connect, builtins.__import__
        opened = []
        def guarded_connect(database, *args, **kwargs):
            value = str(database)
            self.assertTrue(value.startswith((self.root / ".recovery-crypto-").as_uri()))
            self.assertIn("mode=ro&immutable=1", value)
            opened.append(value)
            return connect(database, *args, **kwargs)
        def guarded_import(name, *args, **kwargs):
            self.assertNotIn(name, {"rove_app_api", "bot", "dotenv", "rove_report_worker"})
            return original_import(name, *args, **kwargs)
        with patch.object(sqlite3, "connect", side_effect=guarded_connect), \
             patch.object(builtins, "__import__", side_effect=guarded_import), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
            result = self.verify()
        self.assertEqual(result["verification"], "PASS")
        self.assertTrue(opened)

    def test_sqlite_integrity_and_foreign_keys_rerun_offline(self):
        self.encrypt()
        verifier = recovery.verify_database
        calls = []
        def track(*args, **kwargs):
            calls.append((args, kwargs))
            return verifier(*args, **kwargs)
        with patch.object(recovery, "verify_database", side_effect=track):
            self.verify()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {"immutable": True})

    def test_ledger_anchor_tamper_rejected(self):
        self.rewrite_manifest(lambda m: m["ledger"]["anchor"].update(sha256="0" * 64))
        self.assert_rejected(self.encrypt)

    def test_corrupt_sqlite_with_rebound_file_hash_is_rejected(self):
        path = self.source / "database/clarity.db"
        path.write_bytes(b"not a SQLite database")
        self.rewrite_manifest(lambda m: m["database"].update(bytes=path.stat().st_size, sha256=crypto.file_hash(path)))
        self.assert_rejected(self.encrypt)

    def test_foreign_key_violation_with_rebound_file_hash_is_rejected(self):
        path = self.source / "database/clarity.db"
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE report_links SET user_id=777")
            conn.commit()
        self.rewrite_manifest(lambda m: m["database"].update(bytes=path.stat().st_size, sha256=crypto.file_hash(path)))
        self.assert_rejected(self.encrypt)

    def test_private_key_permissions_must_be_private(self):
        self.encrypt()
        private = self.root / "unsafe-private.asc"
        private.write_bytes(self.private.read_bytes())
        private.chmod(0o644)
        self.assert_rejected(lambda: self.verify(private_key=private), output=self.offline)

    def test_decryption_cannot_start_agent_without_cleanup_tool(self):
        self.encrypt()
        with patch.object(crypto.os, "access", return_value=False):
            self.assert_rejected(self.verify, output=self.offline)

    def test_cli_rejects_echoing_passphrase_fallback(self):
        import getpass
        import warnings
        args = ["crypto", "verify-offline", "--ciphertext", "/synthetic/cipher", "--metadata", "/synthetic/meta",
                "--private-key", "/synthetic/private", "--output", "/synthetic/out",
                "--metadata-sha256", "0" * 64, "--fingerprint", self.fpr]
        def fallback(*args):
            warnings.warn("synthetic insecure fallback", getpass.GetPassWarning)
            raise AssertionError("must reject before echoing")
        with patch.object(sys, "argv", args), patch.object(getpass, "getpass", side_effect=fallback), \
             patch.object(crypto, "verify_offline") as verify, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(crypto.main(), 1)
            verify.assert_not_called()

    def test_generation_binding_tamper_rejected(self):
        self.rewrite_manifest(lambda m: m["account_restore_safety"].update(git_sha="0" * 40))
        self.assert_rejected(self.encrypt)

    def test_reference_set_mismatch(self):
        self.encrypt()
        (self.source / "ledger/account_delete_tombstones.jsonl").write_bytes(b"changed")
        self.assert_rejected(lambda: self.verify(reference_set=self.source), output=self.offline)

    def test_existing_output_never_overwritten(self):
        self.output.mkdir(mode=0o700)
        marker = self.output / "keep"
        marker.write_bytes(b"original")
        with self.assertRaises(crypto.CryptoError):
            self.encrypt()
        self.assertEqual(marker.read_bytes(), b"original")

    def test_output_parent_must_be_private_and_not_symlink(self):
        parent = self.root / "public-output"
        parent.mkdir(mode=0o755)
        self.assert_rejected(lambda: self.encrypt(output=parent / "cipher"), output=parent / "cipher")
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_rejected(lambda: self.encrypt(output=alias / "cipher"), output=alias / "cipher")

    def test_output_cannot_be_inside_source(self):
        target = self.source / "encrypted"
        self.assert_rejected(lambda: self.encrypt(output=target), output=target)

    def test_cli_diagnostics_do_not_leak_input_or_gpg_errors(self):
        sentinel = "SYNTHETIC-PRIVATE-KEY-OR-PASSPHRASE-MUST-NOT-LEAK"
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["crypto", "encrypt", "--set", str(self.source), "--public-key", str(self.public),
                "--fingerprint", self.fpr, "--output", str(self.output),
                "--gpg-temp-parent", str(self.keys)]
        with patch.object(sys, "argv", args), patch.object(crypto, "encrypt_set", side_effect=RuntimeError(sentinel)), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(crypto.main(), 1)
        self.assertNotIn(sentinel, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("PASS", stdout.getvalue() + stderr.getvalue())
        with crypto.gpg_session(self.gpg) as client:
            response = subprocess.CompletedProcess([], 1, b"", sentinel.encode())
            with patch.object(subprocess, "run", return_value=response):
                with self.assertRaises(crypto.CryptoError) as caught:
                    client.run(["--import", "/synthetic"])
        self.assertNotIn(sentinel, str(caught.exception))

    def test_success_output_and_recovered_set_do_not_contain_key_material(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.encrypt()
            self.verify()
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        contents = b"".join(self.tree_bytes(self.offline).values()) + self.meta.read_bytes()
        self.assertNotIn(self.fixture.secret.encode(), contents)
        self.assertNotIn(self.passphrase, contents)
        self.assertNotIn(b"BEGIN PGP PRIVATE KEY", contents)
        self.assertNotIn(b"BEGIN PGP PUBLIC KEY", contents)


if __name__ == "__main__":
    unittest.main()
