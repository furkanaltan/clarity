"""Legacy crypto regression uses the unchanged synthetic GPG fixture, not real keys."""

import gzip
import io
import json
import os
import sqlite3
import tarfile
import unittest
from contextlib import closing

import rove_recovery_crypto as crypto
import rove_recovery_set as recovery
import test_recovery_crypto as fixtures


class LegacyReportCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RecoveryCryptoTests.setUpClass.__func__(cls)

    encrypt = fixtures.RecoveryCryptoTests.encrypt
    verify = fixtures.RecoveryCryptoTests.verify
    assert_rejected = fixtures.RecoveryCryptoTests.assert_rejected
    rewrite_meta = fixtures.RecoveryCryptoTests.rewrite_meta
    replace_archive = fixtures.RecoveryCryptoTests.replace_archive
    tree_bytes = staticmethod(fixtures.RecoveryCryptoTests.tree_bytes)

    def setUp(self):
        fixtures.RecoveryCryptoTests.setUp(self)
        archive = self.fixture.reports / "archive"
        archive.mkdir()
        self.name = "clarity_report_1_2026-06.pdf.gz"
        (archive / self.name).write_bytes(gzip.compress(b"%PDF-1.4\nSynthetic legacy\n", mtime=0))
        self.source = self.fixture.collect()
        self.relative = "artifacts/reports/archive/" + self.name
        self.legacy = self.source / self.relative
        self.assertEqual(self.fixture.manifest(self.source)["status"], "COMPLETE")

    def manifest(self):
        return json.loads((self.source / "manifest.json").read_bytes())

    def save_manifest(self, value):
        (self.source / "manifest.json").write_bytes(recovery.json_bytes(value))

    def test_shared_report_policy_has_identical_current_and_legacy_acceptance(self):
        names = [f"{prefix}_report_1_2026-06{suffix}"
                 for prefix in ("rove", "clarity") for suffix in (".pdf", ".pdf.gz")]
        names += ["clarity_report_0_2026-06.pdf", "clarity_report_01_2026-06.pdf",
                  "clarity_report_-1_2026-06.pdf", "clarity_report_1_2026-00.pdf",
                  "clarity_report_1_2026-13.pdf", "clarity_report_1_2026-6.pdf",
                  "clarity_other.pdf", "clarity_report_1_2026-06.pdf.gz.bak",
                  "clarity_report_1_2026-06.pdf\n", "CLARITY_report_1_2026-06.pdf"]
        for name in names:
            for parent in ("", "archive/"):
                with self.subTest(name=name, parent=parent):
                    self.assertEqual(bool(recovery.PDF.fullmatch(name)),
                                     bool(crypto.ARTIFACT.fullmatch("artifacts/reports/" + parent + name)))
        for path in ("/artifacts/reports/" + self.name,
                     "artifacts/reports/archive/../" + self.name,
                     "artifacts/reports/other/" + self.name,
                     "artifacts/reports/archive\\" + self.name):
            self.assertIsNone(crypto.ARTIFACT.fullmatch(path))

    def test_all_current_and_legacy_variants_round_trip_byte_exact(self):
        for prefix in ("rove", "clarity"):
            for parent in (self.fixture.reports, self.fixture.reports / "archive"):
                for suffix in (".pdf", ".pdf.gz"):
                    name = f"{prefix}_report_1_2026-06{suffix}"
                    raw = b"%PDF-1.4\nSynthetic: " + name.encode() + b"\n"
                    (parent / name).write_bytes(gzip.compress(raw, mtime=0) if suffix.endswith(".gz") else raw)
        self.source = self.fixture.collect()
        original = self.tree_bytes(self.source)
        self.encrypt()
        result = self.verify(reference_set=self.source)
        self.assertEqual(result["verification"], "PASS")
        self.assertEqual(result["account_restore"], "NOT_AUTHORIZED")
        self.assertEqual(self.tree_bytes(self.offline / self.source.name), original)
        self.assertEqual(self.tree_bytes(self.source), original)
        artifacts = self.manifest()["artifacts"]
        self.assertEqual(sum(item["category"] == "retained_report_pdf" for item in artifacts), 9)
        self.assertTrue(all(item["owner_user_id"] == 1 for item in artifacts))

    def test_production_filename_round_trip_uses_only_synthetic_data(self):
        with closing(sqlite3.connect(self.fixture.db)) as db:
            db.execute("INSERT INTO users VALUES (?, ?)", (653187414, 0))
            db.commit()
        name = "clarity_report_653187414_2026-06.pdf.gz"
        (self.fixture.reports / "archive" / name).write_bytes(gzip.compress(b"%PDF-1.4\nSynthetic\n", mtime=0))
        self.source = self.fixture.collect()
        self.encrypt()
        self.assertEqual(self.verify(reference_set=self.source)["verification"], "PASS")
        item, = [item for item in self.manifest()["artifacts"] if item["path"].endswith(name)]
        self.assertEqual(item["owner_user_id"], 653187414)

    def test_invalid_legacy_manifest_paths_fail_before_encryption(self):
        original = self.manifest()
        paths = ["artifacts/reports/archive/" + name for name in (
            "clarity_report_0_2026-06.pdf.gz", "clarity_report_01_2026-06.pdf.gz",
            "clarity_report_1_2026-13.pdf.gz", "clarity_report_1_2026-06.pdf.gz.bak", "clarity_notes.pdf")]
        paths += ["/" + self.relative, "artifacts/reports/archive/../" + self.name,
                  "artifacts/reports/archive\\" + self.name]
        for path in paths:
            with self.subTest(path=path):
                manifest = json.loads(json.dumps(original))
                item, = [item for item in manifest["artifacts"] if item["path"] == self.relative]
                item["path"] = path
                self.save_manifest(manifest)
                with self.assertRaisesRegex(crypto.CryptoError, "manifest_path_invalid"):
                    self.encrypt()
                self.assertFalse(self.output.exists())

    def test_legacy_symlink_is_rejected(self):
        actual = self.root / "actual.gz"
        self.legacy.rename(actual)
        self.legacy.symlink_to(actual)
        self.assert_rejected(self.encrypt)

    def test_legacy_directory_is_rejected(self):
        self.legacy.unlink()
        self.legacy.mkdir(mode=0o700)
        self.assert_rejected(self.encrypt)

    def test_legacy_hardlink_is_rejected(self):
        os.link(self.legacy, self.root / "linked.gz")
        self.assert_rejected(self.encrypt)

    def test_legacy_hash_tampering_is_rejected_before_encryption(self):
        raw = bytearray(self.legacy.read_bytes())
        raw[-1] ^= 1
        self.legacy.write_bytes(raw)
        with self.assertRaisesRegex(crypto.CryptoError, "source_checksum_mismatch"):
            self.encrypt()
        self.assertFalse(self.output.exists())

    def test_legacy_tampering_in_real_reencrypted_archive_is_rejected(self):
        self.encrypt()
        self.legacy.write_bytes(self.legacy.read_bytes() + b"tampered")
        self.replace_archive(lambda path: crypto.archive_set(self.source, path))
        with self.assertRaisesRegex(recovery.RecoveryError, "set_checksum_mismatch"):
            self.verify()
        self.assertFalse(self.offline.exists())

    def test_duplicate_legacy_manifest_path_is_rejected(self):
        manifest = self.manifest()
        item, = [item for item in manifest["artifacts"] if item["path"] == self.relative]
        manifest["artifacts"].append(dict(item))
        self.save_manifest(manifest)
        with self.assertRaisesRegex(crypto.CryptoError, "manifest_path_invalid"):
            self.encrypt()
        self.assertFalse(self.output.exists())

    def test_unknown_and_traversal_legacy_paths_in_real_ciphertext_are_rejected(self):
        self.encrypt()
        cases = [("artifacts/reports/archive/clarity_notes.pdf", "archive_file_not_allowed"),
                 ("artifacts/reports/archive/../" + self.name, "archive_path_invalid"),
                 ("artifacts/reports/archive/clarity_report_1_2026-13.pdf.gz", "archive_file_not_allowed")]
        for relative, error in cases:
            with self.subTest(relative=relative):
                def build(path):
                    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
                        item = tarfile.TarInfo(self.source.name + "/" + relative)
                        item.mode, item.size = 0o600, 4
                        archive.addfile(item, io.BytesIO(b"test"))
                self.replace_archive(build)
                with self.assertRaisesRegex(crypto.CryptoError, error):
                    self.verify()
                self.assertFalse(self.offline.exists())

    def test_duplicate_legacy_archive_member_in_real_ciphertext_is_rejected(self):
        self.encrypt()
        original = self.root / "original.tar.gz"
        crypto.archive_set(self.source, original)
        def build(path):
            with tarfile.open(original) as source, tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
                for item in source:
                    raw = source.extractfile(item).read() if item.isfile() else None
                    archive.addfile(item, io.BytesIO(raw) if raw is not None else None)
                    if item.name == self.source.name + "/" + self.relative:
                        archive.addfile(item, io.BytesIO(raw))
        self.replace_archive(build)
        with self.assertRaisesRegex(crypto.CryptoError, "archive_path_invalid"):
            self.verify()
        self.assertFalse(self.offline.exists())

    def test_legacy_deleted_owner_is_filtered_before_crypto_round_trip(self):
        for prefix in ("clarity", "rove"):
            (self.fixture.reports / "archive" / f"{prefix}_report_99_2026-06.pdf.gz").write_bytes(b"synthetic")
        self.source = self.fixture.collect()
        self.assertTrue(all(item["owner_user_id"] == 1 for item in self.manifest()["artifacts"]))
        self.encrypt()
        self.assertEqual(self.verify(reference_set=self.source)["verification"], "PASS")

    def test_legacy_set_does_not_bypass_unsafe_or_unknown_generation_gate(self):
        manifest = self.manifest()
        for status in (recovery.UNSAFE_FOR_ACCOUNT_RESTORE, recovery.UNKNOWN):
            with self.subTest(status=status):
                manifest["account_restore_safety"]["status"] = status
                self.save_manifest(manifest)
                with self.assertRaisesRegex(crypto.CryptoError, "generation_not_safe"):
                    self.encrypt()
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
