"""External trust regressions: synthetic sets and SDK fakes, never AWS or real keys."""

import base64
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import rove_recovery_trust as trust
import rove_recovery_set as recovery
import test_recovery_set as fixtures


class ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code, "Message": "synthetic-do-not-log"}}


def configuration(git_sha="a" * 40, ledger=None):
    now = datetime.now(timezone.utc)
    policy = dict(path="/synthetic/ledger.jsonl", anchor={"bytes": 0, "sha256": trust.sha(b"")},
                  audit_reference="offline:synthetic", history_confirmed_complete=False,
                  recovery_boundary=dict(admission_mode="prospective", cutoff_at_utc=(now - timedelta(days=2)).isoformat(),
                      evidence_ref="offline:synthetic", expected_git_sha=git_sha, expected_schema_sha256="b" * 64,
                      forward_coverage_confirmed=True, gate_version="3", ledger_anchor_id="ledger_anchor_" + "1" * 32,
                      policy_version=1), generation_receipts={})
    if ledger is not None:
        policy = copy.deepcopy(ledger)
        policy["generation_receipts"] = {}
    pins = trust.Pins("111111111111", "synthetic-test", trust.sha(trust.json_bytes(policy)),
                      "synthetic-receipts", "synthetic-recovery",
                      "arn:aws:lambda:eu-central-1:111111111111:function:synthetic-receipt:1",
                      "arn:aws:lambda:eu-central-1:111111111111:function:synthetic-verifier:2",
                      "d" * 64, "A" * 40)
    return pins, policy


class FakeS3:
    def __init__(self, pins):
        self.pins = pins
        self.objects = {}
        self.calls = []
        self.denied = False
        self.lock_mode = "COMPLIANCE"
        self.lock_days = 365
        self.head = {}
        self.retention = {}

    def put_object(self, **args):
        self.calls.append(("put", args))
        if self.denied:
            raise ClientError("AccessDenied")
        assert args["IfNoneMatch"] == "*" and args["ExpectedBucketOwner"] == self.pins.account_id
        assert args["ContentMD5"] == base64.b64encode(hashlib.md5(args["Body"], usedforsecurity=False).digest()).decode()
        assert args["Bucket"] == self.pins.receipt_bucket
        if args["Key"] in self.objects:
            raise ClientError("PreconditionFailed")
        version = "synthetic-version-" + str(len(self.objects) + 1)
        self.objects[args["Key"]] = (version, args["Body"], datetime.now(timezone.utc) + timedelta(days=self.lock_days))
        return {"VersionId": version}

    def get_object(self, **args):
        self.calls.append(("get", args))
        version, raw, _ = self.objects[args["Key"]]
        if "VersionId" in args and args["VersionId"] != version:
            raise ClientError("NoSuchVersion")
        return {"VersionId": version, "Body": io.BytesIO(raw)}

    def get_object_retention(self, **args):
        self.calls.append(("retention", args))
        if args["Bucket"] == self.pins.recovery_bucket:
            return {"Retention": copy.deepcopy(self.retention)}
        version, _, until = self.objects[args["Key"]]
        assert args["VersionId"] == version
        return {"Retention": {"Mode": self.lock_mode, "RetainUntilDate": until}}

    def head_object(self, **args):
        self.calls.append(("head", args))
        return copy.deepcopy(self.head)


class FakeLambda:
    def __init__(self, pins, policy, s3):
        self.pins, self.policy, self.s3 = pins, policy, s3
        self.meta = SimpleNamespace(endpoint_url="https://lambda.eu-central-1.amazonaws.com")
        self.calls = []
        self.mutate = lambda value: value
        self.errors = []
        self.function_error = False
        self.executed_version = None

    def invoke(self, **args):
        self.calls.append(args)
        if self.errors:
            raise self.errors.pop(0)
        request = trust.strict_json(args["Payload"])
        handler = trust.receipt_authority if args["FunctionName"] == self.pins.receipt_arn else trust.metadata_verifier
        try:
            value = handler(request, self.pins, self.policy, self.s3, datetime.now(timezone.utc))
        except trust.TrustError as exc:
            value = dict(response_version=1, status="BLOCK", code=str(exc))
        value = self.mutate(value)
        response = dict(StatusCode=200, ExecutedVersion=self.executed_version or args["FunctionName"].split(":")[-1],
                        Payload=io.BytesIO(trust.json_bytes(value)))
        if self.function_error:
            response["FunctionError"] = "Unhandled"
        return response


class ReceiptTrustTests(unittest.TestCase):
    def setUp(self):
        self.pins, self.policy = configuration()
        self.s3 = FakeS3(self.pins)
        self.sdk = FakeLambda(self.pins, self.policy, self.s3)
        self.client = trust.RecoveryTrustClient(self.pins, self.policy, client=self.sdk)
        self.gen = "recovery_set_" + (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ") + "_" + "2" * 32
        self.request = self.client.request(self.gen, "e" * 64, "manifest")

    def test_first_receipt_is_locked_version_bound_and_authenticated(self):
        result = self.client.receipt(self.request)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["receipt"]["request"], self.request)
        self.assertEqual(result["storage"]["sha256"], trust.sha(trust.json_bytes(result["receipt"])))
        self.assertEqual(self.sdk.calls[0]["FunctionName"], self.pins.receipt_arn)
        self.assertEqual(self.sdk.calls[0]["InvocationType"], "RequestResponse")
        self.assertEqual(self.sdk.calls[0]["LogType"], "None")
        self.assertTrue(all(args["IfNoneMatch"] == "*" for method, args in self.s3.calls if method == "put"))
        self.assertTrue(all("VersionId" in args for method, args in self.s3.calls if method == "retention"))

    def test_identical_repeat_returns_exact_saved_document_and_version(self):
        first = self.client.receipt(self.request)
        second = self.client.receipt(self.request)
        self.assertEqual(first, second)
        self.assertEqual(len(self.s3.objects), 2)

    def test_conflicting_hash_never_creates_a_second_receipt(self):
        self.client.receipt(self.request)
        before = copy.deepcopy(self.s3.objects)
        with self.assertRaisesRegex(trust.TrustError, "authority_blocked"):
            self.client.receipt(dict(self.request, manifest_sha256="f" * 64))
        self.assertEqual(self.s3.objects, before)

    def test_receipt_storage_denied_is_not_success(self):
        self.s3.denied = True
        with self.assertRaises(ClientError):
            trust.receipt_authority(self.request, self.pins, self.policy, self.s3, datetime.now(timezone.utc))
        self.assertFalse(self.s3.objects)

    def test_missing_and_short_receipt_lock_are_blocked(self):
        for mode, days in ((None, 365), ("GOVERNANCE", 365), ("COMPLIANCE", 30)):
            with self.subTest(mode=mode, days=days):
                self.s3 = FakeS3(self.pins)
                self.s3.lock_mode, self.s3.lock_days = mode, days
                with self.assertRaisesRegex(trust.TrustError, "receipt_lock_missing_or_short"):
                    trust.receipt_authority(self.request, self.pins, self.policy, self.s3, datetime.now(timezone.utc))

    def test_request_wrong_account_environment_policy_generation_and_hash_block(self):
        for field, value in (("account_id", "222222222222"), ("environment_id", "other"),
                             ("policy_revision", "0" * 64), ("generation_id", "../other"),
                             ("manifest_sha256", "bad"), ("request_version", True), ("operation", "other")):
            with self.subTest(field=field), self.assertRaises(trust.TrustError):
                trust.receipt_authority(dict(self.request, **{field: value}), self.pins, self.policy,
                                        self.s3, datetime.now(timezone.utc))
        self.assertFalse(self.s3.calls)

    def test_pre_t0_generation_is_never_accepted(self):
        old = "recovery_set_20000101T000000Z_" + "2" * 32
        with self.assertRaisesRegex(trust.TrustError, "generation_outside_policy_boundary"):
            self.client.receipt(dict(self.request, generation_id=old))

    def test_unknown_request_fields_and_arbitrary_data_are_rejected(self):
        for field in ("bucket", "policy", "history_confirmed_complete", "secret", "receipt"):
            with self.subTest(field=field), self.assertRaises(trust.TrustError):
                self.client.receipt(dict(self.request, **{field: "synthetic"}))

    def test_approved_existing_manifest_cannot_be_overridden(self):
        policy = copy.deepcopy(self.policy)
        policy["generation_receipts"][self.gen] = "f" * 64
        pins = trust.Pins(**dict(vars(self.pins), policy_revision=trust.sha(trust.json_bytes(policy))))
        with self.assertRaisesRegex(trust.TrustError, "approved_manifest_conflict"):
            trust.binding(dict(self.request, policy_revision=pins.policy_revision), pins, policy, datetime.now(timezone.utc))

    def test_response_wrong_account_function_release_policy_generation_hash_block(self):
        changes = [("authority_arn", self.pins.verifier_arn), ("release_sha256", "0" * 64), ("receipt_version", 2)]
        for field, value in changes:
            def mutate(result):
                result["receipt"][field] = value
                return result
            self.sdk.mutate = mutate
            with self.subTest(field=field), self.assertRaises(trust.TrustError):
                self.client.receipt(self.request)
        for field, value in (("account_id", "222222222222"), ("policy_revision", "0" * 64),
                             ("generation_id", self.gen + "x"), ("manifest_sha256", "0" * 64),
                             ("request_version", True)):
            def mutate(result):
                result["receipt"]["request"][field] = value
                return result
            self.sdk.mutate = mutate
            with self.subTest(field=field), self.assertRaises(trust.TrustError):
                self.client.receipt(self.request)

    def test_missing_receipt_and_unknown_fields_do_not_fall_back(self):
        for place in ("top", "receipt", "storage", "policy_storage"):
            def mutate(result):
                target = result if place == "top" else result[place]
                target["unknown"] = "synthetic"
                return result
            self.sdk.mutate = mutate
            with self.subTest(place=place), self.assertRaises(trust.TrustError):
                self.client.receipt(self.request)
        self.sdk.mutate = lambda result: {key: value for key, value in result.items() if key != "receipt"}
        with self.assertRaises(trust.TrustError):
            self.client.receipt(self.request)

    def test_lambda_200_function_error_or_wrong_executed_version_is_failure(self):
        self.sdk.function_error = True
        with self.assertRaisesRegex(trust.TrustError, "lambda_execution_failed"):
            self.client.receipt(self.request)
        self.sdk.function_error = False
        self.sdk.executed_version = "99"
        with self.assertRaisesRegex(trust.TrustError, "lambda_execution_failed"):
            self.client.receipt(self.request)

    def test_transient_invoke_retry_is_bounded_and_byte_identical(self):
        self.sdk.errors = [ClientError("TooManyRequestsException"), ClientError("ServiceException")]
        with patch.object(trust.time, "sleep"):
            self.client.receipt(self.request)
        self.assertEqual(len(self.sdk.calls), 3)
        self.assertEqual(len({args["Payload"] for args in self.sdk.calls}), 1)
        self.sdk.calls.clear()
        self.sdk.errors = [ClientError("ServiceException")] * 3
        with patch.object(trust.time, "sleep"), self.assertRaisesRegex(trust.TrustError, "lambda_invocation_failed"):
            self.client.receipt(self.request)
        self.assertEqual(len(self.sdk.calls), 3)

    def test_access_denied_never_retries_or_logs_sdk_error_message(self):
        self.sdk.errors = [ClientError("AccessDeniedException")]
        with self.assertRaisesRegex(trust.TrustError, "^lambda_invocation_failed$"):
            self.client.receipt(self.request)
        self.assertEqual(len(self.sdk.calls), 1)

    def test_unreachable_and_timeout_never_supply_a_receipt(self):
        for name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"):
            error = type(name, (Exception,), {})
            self.sdk.calls.clear()
            self.sdk.errors = [error("synthetic-private-message")] * 3
            with self.subTest(name=name), patch.object(trust.time, "sleep"), self.assertRaises(trust.TrustError):
                self.client.receipt(self.request)
            self.assertEqual(len(self.sdk.calls), 3)
        self.assertFalse(self.s3.objects)

    def test_malformed_truncated_and_missing_lambda_payloads_are_blocked(self):
        for body in (None, io.BytesIO(b'{"status":'), io.BytesIO(b'x' * (trust.LIMIT + 1))):
            response = dict(StatusCode=200, ExecutedVersion="1", Payload=body)
            with self.subTest(body=body), patch.object(self.sdk, "invoke", return_value=response), self.assertRaises(trust.TrustError):
                self.client.receipt(self.request)
            if body is not None:
                self.assertTrue(body.closed)

    def test_storage_and_policy_version_mismatch_are_blocked(self):
        for place, field, value in (("storage", "bucket", "other-bucket"), ("storage", "version_id", "null"),
                                    ("storage", "sha256", "0" * 64), ("storage", "mode", "GOVERNANCE"),
                                    ("policy_storage", "version_id", "other-version")):
            def mutate(result):
                result[place][field] = value
                return result
            self.sdk.mutate = mutate
            with self.subTest(place=place, field=field), self.assertRaises(trust.TrustError):
                self.client.receipt(self.request)

    def test_custom_lambda_endpoint_is_rejected(self):
        for endpoint in ("https://example.invalid", None, "http://lambda.eu-central-1.amazonaws.com"):
            self.sdk.meta.endpoint_url = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(trust.TrustError, "lambda_endpoint_invalid"):
                trust.RecoveryTrustClient(self.pins, self.policy, client=self.sdk)

    def test_latest_alias_foreign_account_or_region_pins_are_rejected(self):
        for arn in (self.pins.receipt_arn.rsplit(":", 1)[0] + ":$LATEST",
                    self.pins.receipt_arn.rsplit(":", 1)[0] + ":prod",
                    self.pins.receipt_arn.replace("111111111111", "222222222222"),
                    self.pins.receipt_arn.replace("eu-central-1", "us-east-1")):
            with self.subTest(arn=arn), self.assertRaises(trust.TrustError):
                trust.Pins(**dict(vars(self.pins), receipt_arn=arn))

    def test_crypto_metadata_requires_the_exact_prior_manifest_receipt(self):
        manifest = self.client.receipt(self.request)
        meta = dict(wrapper_version="1.0", recovery_set_id=self.gen, git_sha="a" * 40,
                    manifest_sha256="e" * 64, archive_sha256="f" * 64, ciphertext_sha256="c" * 64,
                    ciphertext_bytes=32, encrypted_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    public_key_fingerprint="A" * 40, encryption_key_fingerprint="B" * 40)
        raw = trust.json_bytes(meta).decode()
        request = dict(self.request, operation="crypto", metadata_json=raw, metadata_sha256=trust.sha(raw.encode()),
                       manifest_receipt_version_id=manifest["storage"]["version_id"])
        self.assertEqual(self.client.receipt(request)["receipt"]["request"]["metadata_json"], raw)
        with self.assertRaises(trust.TrustError):
            self.client.receipt(dict(request, metadata_sha256="0" * 64))
        with self.assertRaises(trust.TrustError):
            self.client.receipt(dict(request, manifest_receipt_version_id="wrong-version"))
        with self.assertRaises(trust.TrustError):
            self.client.receipt(dict(request, metadata_json=raw + " "))

    def test_duplicate_json_constants_and_oversize_are_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'x' * (trust.LIMIT + 1)):
            with self.subTest(raw=raw[:32]), self.assertRaises(trust.TrustError):
                trust.strict_json(raw)

    def test_crypto_unknown_fields_wrong_fingerprint_git_and_version_block(self):
        first = self.client.receipt(self.request)
        meta = dict(wrapper_version="1.0", recovery_set_id=self.gen, git_sha="a" * 40,
                    manifest_sha256="e" * 64, archive_sha256="f" * 64, ciphertext_sha256="c" * 64,
                    ciphertext_bytes=32, encrypted_at_utc=datetime.now(timezone.utc).isoformat(),
                    public_key_fingerprint="A" * 40, encryption_key_fingerprint="B" * 40)
        for changes in ({"secret": "synthetic"}, {"public_key_fingerprint": "C" * 40},
                        {"git_sha": "0" * 40}, {"wrapper_version": "2"}, {"ciphertext_bytes": True}):
            raw = trust.json_bytes(dict(meta, **changes)).decode()
            request = dict(self.request, operation="crypto", metadata_json=raw,
                           metadata_sha256=trust.sha(raw.encode()),
                           manifest_receipt_version_id=first["storage"]["version_id"])
            with self.subTest(changes=changes), self.assertRaises(trust.TrustError):
                self.client.receipt(request)
        self.assertEqual(len(self.s3.objects), 2)

    def test_dispatch_never_returns_dependency_messages_or_stack_traces(self):
        with patch.dict(trust.os.environ, {"TRUST_KIND": "receipt"}), \
             patch.object(trust, "settings", side_effect=ClientError("AccessDenied")):
            result = trust.receipt_handler({}, SimpleNamespace())
        self.assertEqual(result, {"response_version": 1, "status": "BLOCK", "code": "authority_dependency_failed"})


class MetadataVerifierTests(unittest.TestCase):
    def setUp(self):
        self.pins, self.policy = configuration()
        self.s3 = FakeS3(self.pins)
        now = datetime.now(timezone.utc)
        modified = now - timedelta(hours=1)
        self.s3.head = dict(VersionId="exact-version", ContentLength=793008, LastModified=modified,
                           ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=modified + timedelta(days=30))
        self.s3.retention = dict(Mode="COMPLIANCE", RetainUntilDate=modified + timedelta(days=30))
        generation = "recovery_set_" + modified.strftime("%Y%m%dT%H%M%SZ") + "_" + "2" * 32
        client = trust.RecoveryTrustClient(self.pins, self.policy, client=FakeLambda(self.pins, self.policy, self.s3))
        self.request = client.request(generation, "e" * 64, "metadata")
        self.request.update(object_key=f"recovery/{generation}.tar.gz.gpg", version_id="exact-version", size=793008,
                            retention_days=30)

    def verify(self):
        return trust.metadata_verifier(self.request, self.pins, self.policy, self.s3, datetime.now(timezone.utc))

    def test_exact_version_size_lock_and_retention_pass_without_body_get(self):
        result = self.verify()
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["ciphertext_sha256_verified"])
        self.assertEqual([method for method, _ in self.s3.calls], ["head", "retention"])
        for _, args in self.s3.calls:
            self.assertEqual(args, dict(Bucket=self.pins.recovery_bucket, Key=self.request["object_key"],
                                       VersionId="exact-version", ExpectedBucketOwner=self.pins.account_id))

    def test_wrong_version_and_delete_marker_block_without_latest_fallback(self):
        for changes in ({"VersionId": "other"}, {"DeleteMarker": True}):
            with self.subTest(changes=changes), patch.dict(self.s3.head, changes), self.assertRaises(trust.TrustError):
                self.verify()
        for invalid in (None, "", "null", "latest"):
            self.request["version_id"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(trust.TrustError):
                self.verify()

    def test_arbitrary_key_bucket_and_reduced_retention_are_rejected(self):
        original = dict(self.request)
        for changes in ({"object_key": "recovery/other.gpg"}, {"bucket": "other"}, {"retention_days": 1}):
            self.request = dict(original, **changes)
            with self.subTest(changes=changes), self.assertRaises(trust.TrustError):
                self.verify()
        self.assertFalse(self.s3.calls)

    def test_wrong_size_and_boolean_size_are_rejected(self):
        for size in (1, True, "793008"):
            with patch.dict(self.s3.head, ContentLength=size), self.assertRaises(trust.TrustError):
                self.verify()

    def test_missing_lock_or_governance_is_rejected(self):
        for mode in (None, "GOVERNANCE"):
            with patch.dict(self.s3.retention, Mode=mode), self.assertRaises(trust.TrustError):
                self.verify()

    def test_short_expired_and_inconsistent_retention_are_rejected(self):
        modified = self.s3.head["LastModified"]
        for until in (modified + timedelta(days=29), modified + timedelta(days=30, seconds=-2),
                      datetime.now(timezone.utc) - timedelta(seconds=1)):
            with patch.dict(self.s3.head, ObjectLockRetainUntilDate=until), \
                 patch.dict(self.s3.retention, RetainUntilDate=until), self.assertRaises(trust.TrustError):
                self.verify()
        with patch.dict(self.s3.retention, RetainUntilDate=modified + timedelta(days=31)), self.assertRaises(trust.TrustError):
            self.verify()

    def test_s3_subsecond_rounding_has_only_one_second_tolerance(self):
        until = self.s3.head["LastModified"] + timedelta(days=30, milliseconds=-925)
        with patch.dict(self.s3.head, ObjectLockRetainUntilDate=until), patch.dict(self.s3.retention, RetainUntilDate=until):
            self.assertEqual(self.verify()["status"], "PASS")


class CanonicalGateAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecoverySetTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.inventory["ledger"] = self.fixture.prospective_policy()
        self.fixture.save_inventory()
        self.source = self.fixture.collect()
        policy = copy.deepcopy(self.fixture.inventory["ledger"])
        policy["generation_receipts"] = {}
        self.pins, self.policy = configuration(ledger=policy)
        self.policy_path = self.fixture.root / "trusted-policy.json"
        self.policy_path.write_bytes(trust.json_bytes(self.policy))
        self.policy_path.chmod(0o600)
        self.s3 = FakeS3(self.pins)
        self.sdk = FakeLambda(self.pins, self.policy, self.s3)
        self.client = trust.RecoveryTrustClient(self.pins, self.policy, client=self.sdk)

    def test_without_independent_receipt_canonical_gate_is_unknown(self):
        result = recovery.classify_account_restore_generation(policy_path=self.policy_path, set_path=self.source)
        self.assertEqual(result, {"status": recovery.UNKNOWN, "reason": "generation_receipt_missing"})

    def test_authenticated_receipt_feeds_existing_gate_without_mutating_originals(self):
        original_policy = self.policy_path.read_bytes()
        original_manifest = (self.source / "manifest.json").read_bytes()
        with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source) as session:
            self.assertEqual(session.approval.database_path, self.source / "database/clarity.db")
            self.assertTrue(session.approval.policy_path.is_file())
            session.check_current()
            temporary = session.approval.policy_path
        self.assertFalse(temporary.exists())
        self.assertFalse(session.active)
        self.assertEqual(self.policy_path.read_bytes(), original_policy)
        self.assertEqual((self.source / "manifest.json").read_bytes(), original_manifest)
        with self.assertRaisesRegex(trust.TrustError, "approval_context_closed"):
            session.check_current()

    def test_wrong_independent_policy_blocks_before_aws_invoke(self):
        self.policy_path.write_bytes(self.policy_path.read_bytes() + b" ")
        with self.assertRaisesRegex(trust.TrustError, "independent_policy_mismatch"):
            with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source):
                self.fail("must not enter")
        self.assertFalse(self.sdk.calls)

    def test_receipt_wrong_manifest_or_version_never_admits_the_gate(self):
        self.sdk.executed_version = "99"
        with self.assertRaises(trust.TrustError):
            with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source):
                self.fail("must not enter")

    def test_unsafe_unknown_and_unverified_candidates_never_request_receipts(self):
        path = self.source / "manifest.json"
        original = path.read_bytes()
        for status in (recovery.UNSAFE_FOR_ACCOUNT_RESTORE, recovery.UNKNOWN):
            manifest = recovery.strict_json(original)
            manifest["account_restore_safety"]["status"] = status
            path.write_bytes(recovery.json_bytes(manifest))
            with self.subTest(status=status), self.assertRaises((trust.TrustError, recovery.RecoveryError)):
                with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source):
                    self.fail("must not enter")
        self.assertFalse(self.sdk.calls)
        path.write_bytes(original)
        manifest = recovery.strict_json(original)
        manifest["runtime_revision_binding"]["status"] = "UNVERIFIED"
        path.write_bytes(recovery.json_bytes(manifest))
        with self.assertRaises(recovery.RecoveryError):
            with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source):
                self.fail("must not enter")
        self.assertFalse(self.sdk.calls)
        path.write_bytes(original)

    def test_failed_crypto_rebinding_invalidates_previous_receipt(self):
        envelope = self.envelope()
        with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source) as session:
            session.bind_crypto(envelope)
            self.assertIsNotNone(session.crypto_receipt)
            self.sdk.errors = [ClientError("AccessDeniedException")]
            with self.assertRaises(trust.TrustError):
                session.bind_crypto(envelope)
            self.assertIsNone(session.crypto_receipt)
            with self.assertRaisesRegex(trust.TrustError, "external_crypto_receipt_required"):
                session.verify_uploaded("exact-version")
            self.assertFalse(session.offsite_verified)

    def envelope(self):
        manifest = self.fixture.manifest(self.source)
        cipher = b"synthetic-encrypted-placeholder-not-customer-data"
        meta = dict(wrapper_version="1.0", recovery_set_id=self.source.name, git_sha=self.fixture.sha,
                    manifest_sha256=trust.sha((self.source / "manifest.json").read_bytes()), archive_sha256="f" * 64,
                    ciphertext_sha256=trust.sha(cipher), ciphertext_bytes=len(cipher),
                    encrypted_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    public_key_fingerprint="A" * 40, encryption_key_fingerprint="B" * 40)
        envelope = self.fixture.root / "synthetic-envelope"
        envelope.mkdir(mode=0o700)
        (envelope / "crypto.json").write_bytes(trust.json_bytes(meta))
        (envelope / (self.source.name + ".tar.gz.gpg")).write_bytes(cipher)
        for path in envelope.iterdir():
            path.chmod(0o600)
        modified = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.s3.head = dict(VersionId="exact-version", ContentLength=len(cipher), LastModified=modified,
                           ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=modified + timedelta(days=30))
        self.s3.retention = dict(Mode="COMPLIANCE", RetainUntilDate=modified + timedelta(days=30))
        return envelope

    def test_offsite_success_requires_gate_crypto_receipt_and_external_verifier(self):
        with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source) as session:
            with self.assertRaisesRegex(trust.TrustError, "external_crypto_receipt_required"):
                session.verify_uploaded("exact-version")
            self.assertFalse(session.offsite_verified)
            session.bind_crypto(self.envelope())
            self.assertFalse(session.offsite_verified)
            self.assertEqual(session.verify_uploaded("exact-version")["status"], "PASS")
            self.assertTrue(session.offsite_verified)
            with self.assertRaises(trust.TrustError):
                session.verify_uploaded("wrong-version")
            self.assertFalse(session.offsite_verified)

    def test_altered_crypto_metadata_or_ciphertext_never_passes(self):
        envelope = self.envelope()
        with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source) as session:
            session.bind_crypto(envelope)
            meta = envelope / "crypto.json"
            original = meta.read_bytes()
            meta.write_bytes(original + b" ")
            with self.assertRaisesRegex(trust.TrustError, "bound_crypto_metadata_changed"):
                session.verify_uploaded("exact-version")
            meta.write_bytes(original)
            (envelope / (self.source.name + ".tar.gz.gpg")).write_bytes(b"tampered")
            with self.assertRaisesRegex(trust.TrustError, "ciphertext_hash_mismatch"):
                session.verify_uploaded("exact-version")
            self.assertFalse(session.offsite_verified)

    def test_ciphertext_streaming_hash_rejects_symlink_hardlink_and_path_change(self):
        envelope = self.envelope()
        cipher = envelope / (self.source.name + ".tar.gz.gpg")
        original = trust.cipher_metadata(cipher)
        self.assertEqual(original["sha256"], trust.sha(cipher.read_bytes()))
        alias = envelope / "alias"
        alias.symlink_to(cipher)
        with self.assertRaises(OSError):
            trust.cipher_metadata(alias)
        alias.unlink()
        os.link(cipher, alias)
        with self.assertRaisesRegex(trust.TrustError, "ciphertext_file_invalid"):
            trust.cipher_metadata(cipher)
        alias.unlink()
        replacement = envelope / "replacement"
        replacement.write_bytes(b"synthetic")
        with patch.object(trust.os, "stat", return_value=replacement.stat()), self.assertRaisesRegex(trust.TrustError, "ciphertext_changed"):
            trust.cipher_metadata(cipher)

    def test_verifier_malformed_wrong_authority_and_http_200_block_cannot_be_success(self):
        envelope = self.envelope()
        with self.client.authorized_generation(policy_path=self.policy_path, set_path=self.source) as session:
            session.bind_crypto(envelope)
            for field, value in (("authority_arn", self.pins.receipt_arn), ("status", "BLOCK"),
                                 ("version_id", "other"), ("unknown", True), ("response_version", 99),
                                 ("mode", "GOVERNANCE"), ("size", 1)):
                def mutate(result):
                    result[field] = value
                    return result
                self.sdk.mutate = mutate
                with self.subTest(field=field), self.assertRaises(trust.TrustError):
                    session.verify_uploaded("exact-version")
                self.assertFalse(session.offsite_verified)


if __name__ == "__main__":
    unittest.main()
