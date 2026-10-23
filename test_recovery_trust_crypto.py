"""One real synthetic GPG encryption through the unchanged canonical gate adapters."""

import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rove_recovery_crypto as crypto
import rove_recovery_trust as trust
import test_recovery_trust as fixtures


class TrustCryptoIntegrationTests(unittest.TestCase):
    def test_real_encrypted_set_is_bound_before_exact_version_verification(self):
        fixture = fixtures.CanonicalGateAdapterTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gpg = shutil.which("gpg")
        self.assertIsNotNone(gpg, "GPG required, no silent skip")
        temporary = tempfile.TemporaryDirectory(prefix="rvt-", dir=str(Path("/tmp").resolve()))
        self.addCleanup(temporary.cleanup)
        socket_parent = Path(temporary.name)
        public = fixture.fixture.root / "synthetic-public.asc"
        with crypto.gpg_session(gpg) as session:
            session.run(["--pinentry-mode", "loopback", "--passphrase-fd", "0", "--quick-generate-key",
                         "Synthetic Trust <trust@example.invalid>", "rsa2048", "encr", "1d"],
                        secret=b"synthetic-only-not-a-production-secret\n", agent=True)
            listing = session.run(["--with-colons", "--list-keys"])
            fingerprint = next(row.split(b":")[9].decode() for row in listing.splitlines() if row.startswith(b"fpr:"))
            public.write_bytes(session.run(["--armor", "--export", fingerprint]))
            public.chmod(0o600)
        pins = replace(fixture.pins, public_key_fingerprint=fingerprint)
        s3 = fixtures.FakeS3(pins)
        client = trust.RecoveryTrustClient(pins, fixture.policy, client=fixtures.FakeLambda(pins, fixture.policy, s3))
        envelope = fixture.fixture.root / "real-encrypted"
        with client.authorized_generation(policy_path=fixture.policy_path, set_path=fixture.source) as approved:
            encrypted = crypto.encrypt_set(fixture.source, public, fingerprint, envelope,
                                           gpg=gpg, gpg_temp_parent=socket_parent)
            receipt = approved.bind_crypto(envelope)
            self.assertEqual(receipt["receipt"]["request"]["metadata_sha256"], encrypted["metadata_sha256"])
            self.assertFalse(approved.offsite_verified)
            modified = datetime.now(timezone.utc) - timedelta(seconds=1)
            s3.head = dict(VersionId="exact-version", ContentLength=encrypted["ciphertext_bytes"], LastModified=modified,
                          ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=modified + timedelta(days=30))
            s3.retention = dict(Mode="COMPLIANCE", RetainUntilDate=modified + timedelta(days=30))
            self.assertEqual(approved.verify_uploaded("exact-version")["status"], "PASS")
            self.assertTrue(approved.offsite_verified)
        self.assertFalse(approved.active)


if __name__ == "__main__":
    unittest.main()
