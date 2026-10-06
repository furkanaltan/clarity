"""Legacy report collection uses disposable repositories and synthetic PDFs only."""

import gzip
import hashlib
import os
import sqlite3
import unittest
from contextlib import closing

import rove_recovery_set as recovery
import test_recovery_set as fixtures


class LegacyReportCollectorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecoverySetTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def report(self, name, *, archived=False, payload=b"%PDF-1.4\nSynthetic report\n"):
        root = self.fixture.reports / "archive" if archived else self.fixture.reports
        root.mkdir(exist_ok=True)
        path = root / name
        raw = gzip.compress(payload, mtime=0) if name.endswith(".gz") else payload
        path.write_bytes(raw)
        return path, raw

    def collect(self):
        result = self.fixture.collect()
        return result, self.fixture.manifest(result)

    def test_current_and_legacy_variants_preserve_bytes_hash_and_owner(self):
        expected = {}
        for prefix in ("rove", "clarity"):
            for archived in (False, True):
                for suffix in (".pdf", ".pdf.gz"):
                    name = f"{prefix}_report_1_2026-06{suffix}"
                    path, raw = self.report(name, archived=archived, payload=name.encode())
                    expected["artifacts/reports/" + path.relative_to(self.fixture.reports).as_posix()] = raw
        result, manifest = self.collect()
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(recovery.verify_recovery_set(result), manifest)
        self.assertEqual({item["path"] for item in manifest["artifacts"]}, set(expected))
        for item in manifest["artifacts"]:
            self.assertEqual(item["owner_user_id"], 1)
            self.assertEqual(item["category"], "retained_report_pdf")
            self.assertEqual(item["sha256"], hashlib.sha256(expected[item["path"]]).hexdigest())
            self.assertEqual((result / item["path"]).read_bytes(), expected[item["path"]])

    def test_production_filename_is_recognized_with_synthetic_owner_and_contents(self):
        with closing(sqlite3.connect(self.fixture.db)) as db:
            db.execute("INSERT INTO users VALUES (?, ?)", (653187414, 0))
            db.commit()
        name = "clarity_report_653187414_2026-06.pdf.gz"
        _, raw = self.report(name, archived=True)
        result, manifest = self.collect()
        self.assertEqual(manifest["status"], "COMPLETE")
        item, = manifest["artifacts"]
        self.assertEqual(item["owner_user_id"], 653187414)
        self.assertEqual(item["path"], "artifacts/reports/archive/" + name)
        self.assertEqual((result / item["path"]).read_bytes(), raw)
        self.assertEqual(recovery.verify_recovery_set(result), manifest)

    def test_invalid_legacy_user_ids_fail_closed(self):
        for owner in ("0", "-1", "+1", "01", "1.0", "1e0", "\u0661"):
            with self.subTest(owner=owner):
                path, _ = self.report(f"clarity_report_{owner}_2026-06.pdf.gz", archived=True)
                self.assertEqual(self.collect()[1]["error_code"], "report_layout_unknown")
                path.unlink()

    def test_invalid_legacy_months_and_names_fail_closed(self):
        names = [f"clarity_report_1_{month}.pdf.gz" for month in ("2026-00", "2026-13", "2026-6", "26-06")]
        names += ["clarity_report_1_2026-06.pdf.gz.bak", "clarity_report_1_2026-06.PDF",
                  "clarity_report_1_2026-06.pdf\n", "clarity_report_1_2026-06_extra.pdf"]
        for name in names:
            with self.subTest(name=name):
                path, _ = self.report(name, archived=True)
                self.assertEqual(self.collect()[1]["error_code"], "report_layout_unknown")
                path.unlink()

    def test_unrelated_clarity_file_is_not_skipped(self):
        self.report("clarity_notes.pdf")
        self.assertEqual(self.collect()[1]["error_code"], "report_layout_unknown")

    def test_legacy_unknown_owner_fails_closed(self):
        self.report("clarity_report_777_2026-06.pdf.gz", archived=True)
        self.assertEqual(self.collect()[1]["error_code"], "pdf_owner_unknown")

    def test_deleted_users_legacy_and_current_reports_are_filtered(self):
        for prefix in ("rove", "clarity"):
            self.report(f"{prefix}_report_99_2026-06.pdf.gz", archived=True)
        result, manifest = self.collect()
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(recovery.verify_recovery_set(result), manifest)

    def test_legacy_symlink_is_rejected(self):
        path, _ = self.report("clarity_report_1_2026-06.pdf.gz", archived=True)
        actual = self.fixture.root / "actual.gz"
        path.rename(actual)
        path.symlink_to(actual)
        self.assertEqual(self.collect()[1]["error_code"], "symlink_not_allowed")

    def test_legacy_directory_is_rejected(self):
        (self.fixture.reports / "clarity_report_1_2026-06.pdf").mkdir()
        _, manifest = self.collect()
        self.assertEqual(manifest["status"], "FAILED")
        self.assertEqual(manifest["error_code"], "IsADirectoryError")

    def test_legacy_hardlink_is_rejected(self):
        path, _ = self.report("clarity_report_1_2026-06.pdf")
        os.link(path, self.fixture.root / "linked.pdf")
        self.assertEqual(self.collect()[1]["error_code"], "source_file_invalid")

    def test_legacy_fifo_is_rejected_without_waiting(self):
        os.mkfifo(self.fixture.reports / "clarity_report_1_2026-06.pdf")
        self.assertEqual(self.collect()[1]["error_code"], "source_file_invalid")

    def test_archive_symlink_and_traversal_root_are_rejected(self):
        archive = self.fixture.reports / "archive"
        archive.symlink_to(self.fixture.root, target_is_directory=True)
        self.assertEqual(self.collect()[1]["error_code"], "symlink_not_allowed")
        archive.unlink()
        self.fixture.inventory["artifact_roots"]["reports"] = str(self.fixture.reports / ".." / "reports")
        self.fixture.save_inventory()
        with self.assertRaisesRegex(recovery.RecoveryError, "absolute_path_required"):
            self.fixture.collect()

    def test_current_legacy_collision_preserves_both_paths_deterministically(self):
        self.report("rove_report_1_2026-06.pdf.gz", archived=True, payload=b"current bytes")
        self.report("clarity_report_1_2026-06.pdf.gz", archived=True, payload=b"legacy bytes")
        first, manifest = self.collect()
        second, repeated = self.collect()
        self.assertEqual(manifest["status"], "COMPLETE")
        self.assertEqual(manifest["artifacts"], repeated["artifacts"])
        paths = [item["path"] for item in manifest["artifacts"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(set(paths)), 2)
        self.assertNotEqual(manifest["artifacts"][0]["sha256"], manifest["artifacts"][1]["sha256"])
        for path in paths:
            self.assertEqual((first / path).read_bytes(), (second / path).read_bytes())

    def test_legacy_manifest_hash_tampering_is_rejected(self):
        self.report("clarity_report_1_2026-06.pdf.gz", archived=True)
        result, manifest = self.collect()
        item, = manifest["artifacts"]
        (result / item["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(recovery.RecoveryError, "set_checksum_mismatch"):
            recovery.verify_recovery_set(result)

    def test_duplicate_legacy_manifest_record_is_rejected(self):
        self.report("clarity_report_1_2026-06.pdf.gz", archived=True)
        result, manifest = self.collect()
        manifest["artifacts"].append(dict(manifest["artifacts"][0]))
        (result / "manifest.json").write_bytes(recovery.json_bytes(manifest))
        with self.assertRaisesRegex(recovery.RecoveryError, "manifest_path_invalid"):
            recovery.verify_recovery_set(result)


if __name__ == "__main__":
    unittest.main()
