"""Pinned external recovery witnesses; no upload, restore, scheduler or app startup."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


VERSION = 1
LIMIT = 16384
HASH = re.compile(r"[0-9a-f]{64}\Z")
GENERATION = re.compile(r"recovery_set_([0-9]{8}T[0-9]{6}Z)_[0-9a-f]{32}\Z")
ARN = re.compile(r"arn:aws:lambda:(eu-central-1):([0-9]{12}):function:([A-Za-z0-9_-]{1,64}):([1-9][0-9]*)\Z")
BASE = {"request_version", "operation", "account_id", "environment_id", "policy_revision",
        "generation_id", "manifest_sha256"}
CRYPTO_EXTRA = {"metadata_sha256", "metadata_json", "manifest_receipt_version_id"}
CRYPTO_FIELDS = {"wrapper_version", "recovery_set_id", "git_sha", "manifest_sha256",
                 "archive_sha256", "ciphertext_sha256", "ciphertext_bytes", "encrypted_at_utc",
                 "public_key_fingerprint", "encryption_key_fingerprint"}
STORAGE_FIELDS = {"bucket", "key", "version_id", "sha256", "mode", "retain_until_utc"}
TRANSIENT_CODES = {"TooManyRequestsException", "ServiceException", "SlowDown",
                   "InternalError", "ServiceUnavailable", "ConditionalRequestConflict"}
TRANSIENT_TYPES = {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
                   "ConnectionClosedError"}


class TrustError(RuntimeError):
    """Fixed codes only, never SDK error messages or credential/request payloads."""


def require(condition, code):
    if not condition:
        raise TrustError(code)


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    require(isinstance(raw, (bytes, str)) and len(raw) <= LIMIT, "json_size_invalid")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    def reject_constant(value):
        raise TrustError("json_constant_invalid")
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise TrustError("json_invalid") from exc


def fields(value, expected):
    require(isinstance(value, dict) and set(value) == expected, "schema_invalid")
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        require(isinstance(result, datetime) and result.utcoffset() == timedelta(0), "timestamp_invalid")
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrustError("timestamp_invalid") from exc


def version_id(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024
            and value.lower() not in {"null", "latest"}
            and all(33 <= ord(char) <= 126 for char in value), "object_version_required")
    return value


def published_arn(value, account):
    match = ARN.fullmatch(value) if isinstance(value, str) else None
    require(match is not None and match[2] == account, "authority_identity_invalid")
    return match


def validate_policy(policy, revision):
    fields(policy, {"path", "anchor", "audit_reference", "history_confirmed_complete",
                    "recovery_boundary", "generation_receipts"})
    require(bool(HASH.fullmatch(revision)) and sha(json_bytes(policy)) == revision, "policy_revision_invalid")
    fields(policy["anchor"], {"bytes", "sha256"})
    require(type(policy["anchor"]["bytes"]) is int and policy["anchor"]["bytes"] >= 0
            and isinstance(policy["anchor"]["sha256"], str)
            and bool(HASH.fullmatch(policy["anchor"]["sha256"])), "policy_anchor_invalid")
    boundary = fields(policy["recovery_boundary"], {
        "admission_mode", "cutoff_at_utc", "evidence_ref", "expected_git_sha",
        "expected_schema_sha256", "forward_coverage_confirmed", "gate_version",
        "ledger_anchor_id", "policy_version"})
    require(policy["history_confirmed_complete"] is False and boundary["admission_mode"] == "prospective"
            and boundary["forward_coverage_confirmed"] is True and boundary["gate_version"] == "3"
            and type(boundary["policy_version"]) is int and boundary["policy_version"] == 1
            and bool(re.fullmatch(r"[0-9a-f]{40}", str(boundary["expected_git_sha"])))
            and bool(HASH.fullmatch(str(boundary["expected_schema_sha256"])))
            and bool(re.fullmatch(r"ledger_anchor_[0-9a-f]{32}", str(boundary["ledger_anchor_id"]))),
            "policy_contract_invalid")
    timestamp(boundary["cutoff_at_utc"])
    for name in ("audit_reference", "path"):
        require(isinstance(policy[name], str) and len(policy[name]) <= 256, "policy_contract_invalid")
    require(isinstance(policy["generation_receipts"], dict) and all(
        isinstance(key, str) and GENERATION.fullmatch(key) and isinstance(value, str) and HASH.fullmatch(value)
        for key, value in policy["generation_receipts"].items()), "policy_contract_invalid")
    return policy


@dataclass(frozen=True)
class Pins:
    account_id: str
    environment_id: str
    policy_revision: str
    receipt_bucket: str
    recovery_bucket: str
    receipt_arn: str
    verifier_arn: str
    release_sha256: str
    public_key_fingerprint: str
    retention_days: int = 30

    def __post_init__(self):
        require(bool(re.fullmatch(r"[0-9]{12}", self.account_id)), "account_invalid")
        require(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,47}", self.environment_id)), "environment_invalid")
        require(bool(HASH.fullmatch(self.policy_revision)) and bool(HASH.fullmatch(self.release_sha256)), "pin_invalid")
        require(bool(re.fullmatch(r"[0-9A-F]{40}", self.public_key_fingerprint)), "fingerprint_invalid")
        require(type(self.retention_days) is int and self.retention_days == 30, "retention_policy_invalid")
        for name in (self.receipt_bucket, self.recovery_bucket):
            require(isinstance(name, str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", name)), "bucket_invalid")
        require(self.receipt_bucket != self.recovery_bucket, "separate_receipt_bucket_required")
        published_arn(self.receipt_arn, self.account_id)
        published_arn(self.verifier_arn, self.account_id)
        require(self.receipt_arn != self.verifier_arn, "separate_authorities_required")


def binding(request, pins, policy, now):
    operation = request.get("operation") if isinstance(request, dict) else None
    expected = BASE | (CRYPTO_EXTRA if operation == "crypto" else set())
    fields(request, expected)
    require(type(request["request_version"]) is int and request["request_version"] == VERSION
            and operation in {"manifest", "crypto"}, "request_version_invalid")
    require(request["account_id"] == pins.account_id and request["environment_id"] == pins.environment_id,
            "account_environment_mismatch")
    require(request["policy_revision"] == pins.policy_revision, "policy_revision_mismatch")
    match = GENERATION.fullmatch(request["generation_id"]) if isinstance(request["generation_id"], str) else None
    require(match is not None and isinstance(request["manifest_sha256"], str)
            and bool(HASH.fullmatch(request["manifest_sha256"])), "generation_binding_invalid")
    try:
        created = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TrustError("generation_time_invalid") from exc
    require(timestamp(policy["recovery_boundary"]["cutoff_at_utc"]) < created <= now + timedelta(seconds=60),
            "generation_outside_policy_boundary")
    known = policy["generation_receipts"].get(request["generation_id"])
    require(known is None or known == request["manifest_sha256"], "approved_manifest_conflict")
    if operation == "crypto":
        version_id(request["manifest_receipt_version_id"])
        validate_crypto(request["metadata_json"], request["metadata_sha256"], request, pins, policy, now)
    return request


def validate_crypto(raw, digest, request, pins, policy, now):
    require(isinstance(raw, str) and isinstance(digest, str) and bool(HASH.fullmatch(digest))
            and sha(raw.encode()) == digest, "metadata_hash_mismatch")
    meta = fields(strict_json(raw), CRYPTO_FIELDS)
    require(meta["wrapper_version"] == "1.0" and meta["recovery_set_id"] == request["generation_id"]
            and meta["manifest_sha256"] == request["manifest_sha256"]
            and meta["git_sha"] == policy["recovery_boundary"]["expected_git_sha"], "metadata_binding_mismatch")
    for key in ("manifest_sha256", "ciphertext_sha256", "archive_sha256"):
        require(isinstance(meta[key], str) and bool(HASH.fullmatch(meta[key])), "metadata_invalid")
    require(meta["public_key_fingerprint"] == pins.public_key_fingerprint
            and bool(re.fullmatch(r"[0-9A-F]{40}", str(meta["encryption_key_fingerprint"])))
            and type(meta["ciphertext_bytes"]) is int and 0 < meta["ciphertext_bytes"] <= 1107296256,
            "metadata_invalid")
    require(timestamp(meta["encrypted_at_utc"]) <= now, "metadata_time_invalid")
    return meta


def aws_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")


def sdk_client(service):
    # No endpoint overrides, credential files, ambient custom endpoints or SDK retries.
    import boto3
    from botocore.config import Config
    config = Config(region_name="eu-central-1", connect_timeout=3, read_timeout=25,
                    retries={"total_max_attempts": 1}, ignore_configured_endpoint_urls=True)
    return boto3.client(service, config=config)


def settings(context):
    account = os.environ["TRUST_ACCOUNT"]
    arn = context.invoked_function_arn
    match = published_arn(arn, account)
    require(match[4] == context.function_version and match[3] == os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            "unpublished_or_wrong_function")
    # A function knows its own published ARN, never an invented peer version.
    pins = SimpleNamespace(account_id=account, environment_id=os.environ["TRUST_ENVIRONMENT"],
        policy_revision=os.environ["TRUST_POLICY_REVISION"], receipt_bucket=os.environ["TRUST_RECEIPT_BUCKET"],
        recovery_bucket=os.environ["TRUST_RECOVERY_BUCKET"], receipt_arn=arn, verifier_arn=arn,
        release_sha256=os.environ["TRUST_RELEASE_SHA256"],
        public_key_fingerprint=os.environ["TRUST_PUBLIC_FINGERPRINT"], retention_days=30)
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,47}", pins.environment_id)
            and HASH.fullmatch(pins.release_sha256)
            and re.fullmatch(r"[0-9A-F]{40}", pins.public_key_fingerprint)
            and pins.receipt_bucket != pins.recovery_bucket, "authority_configuration_invalid")
    for bucket in (pins.receipt_bucket, pins.recovery_bucket):
        require(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket), "authority_configuration_invalid")
    policy = validate_policy(strict_json(os.environ["TRUST_POLICY_JSON"]), pins.policy_revision)
    return pins, policy


def storage_proof(s3, pins, key, version, raw, now):
    version_id(version)
    locked = s3.get_object_retention(Bucket=pins.receipt_bucket, Key=key, VersionId=version,
                                     ExpectedBucketOwner=pins.account_id)["Retention"]
    until = timestamp(locked.get("RetainUntilDate"))
    require(locked.get("Mode") == "COMPLIANCE" and until >= now + timedelta(days=31),
            "receipt_lock_missing_or_short")
    return dict(bucket=pins.receipt_bucket, key=key, version_id=version, sha256=sha(raw),
                mode="COMPLIANCE", retain_until_utc=until.isoformat())


def read_stored(s3, pins, key, now, version=None):
    args = dict(Bucket=pins.receipt_bucket, Key=key, ExpectedBucketOwner=pins.account_id)
    if version is not None:
        args["VersionId"] = version_id(version)
    response = s3.get_object(**args)
    body = response["Body"]
    try:
        raw = body.read(LIMIT + 1)
    finally:
        body.close()
    require(not response.get("DeleteMarker") and len(raw) <= LIMIT, "stored_receipt_invalid")
    actual_version = version_id(response.get("VersionId"))
    require(version is None or version == actual_version, "receipt_version_mismatch")
    return strict_json(raw), storage_proof(s3, pins, key, actual_version, raw, now)


def create_stored(s3, pins, key, value, now, *, compare):
    raw = json_bytes(value)
    try:
        response = s3.put_object(Bucket=pins.receipt_bucket, Key=key, Body=raw,
                                 ContentType="application/json", IfNoneMatch="*",
                                 ContentMD5=base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode(),
                                 ExpectedBucketOwner=pins.account_id)
        version = version_id(response.get("VersionId"))
    except Exception as exc:
        if aws_code(exc) != "PreconditionFailed":
            raise
        version = None
    stored, proof = read_stored(s3, pins, key, now, version)
    require(compare(stored, value), "receipt_binding_conflict")
    return stored, proof


def receipt_authority(request, pins, policy, s3, now):
    binding(request, pins, policy, now)
    policy_key = f"policies/{pins.policy_revision}.json"
    _, policy_proof = create_stored(s3, pins, policy_key, policy, now, compare=lambda a, b: a == b)
    if request["operation"] == "crypto":
        manifest_key = f"receipts/{pins.environment_id}/{request['generation_id']}/manifest.json"
        existing, _ = read_stored(s3, pins, manifest_key, now, request["manifest_receipt_version_id"])
        manifest_request = {key: request[key] for key in BASE}
        manifest_request["operation"] = "manifest"
        require(existing["request"] == manifest_request and existing["authority_arn"] == pins.receipt_arn
                and existing["release_sha256"] == pins.release_sha256, "manifest_receipt_binding_mismatch")
    value = dict(receipt_version=VERSION, request=request, observed_at_utc=now.isoformat(),
                 authority_arn=pins.receipt_arn, release_sha256=pins.release_sha256,
                 policy_version_id=policy_proof["version_id"])
    key = f"receipts/{pins.environment_id}/{request['generation_id']}/{request['operation']}.json"
    def same_binding(stored, candidate):
        fields(stored, set(candidate))
        timestamp(stored["observed_at_utc"])
        return all(stored[field] == candidate[field] for field in candidate if field != "observed_at_utc")
    stored, proof = create_stored(s3, pins, key, value, now, compare=same_binding)
    return dict(response_version=VERSION, status="PASS", receipt=stored,
                storage=proof, policy_storage=policy_proof)


VERIFIER_REQUEST = BASE | {"object_key", "version_id", "size", "retention_days"}
VERIFIER_RESPONSE = {"response_version", "status", "account_id", "environment_id", "policy_revision",
                     "authority_arn", "release_sha256", "generation_id", "manifest_sha256", "bucket",
                     "object_key", "version_id", "size", "mode", "retain_until_utc", "last_modified_utc",
                     "observed_at_utc", "retention_days", "ciphertext_sha256_verified"}


def metadata_verifier(request, pins, policy, s3, now):
    fields(request, VERIFIER_REQUEST)
    common = {key: request[key] for key in BASE}
    require(common["operation"] == "metadata", "operation_invalid")
    common["operation"] = "manifest"
    binding(common, pins, policy, now)
    key = f"recovery/{request['generation_id']}.tar.gz.gpg"
    require(request["object_key"] == key, "object_key_mismatch")
    version = version_id(request["version_id"])
    require(type(request["size"]) is int and 0 < request["size"] <= 1107296256, "size_invalid")
    require(type(request["retention_days"]) is int and request["retention_days"] == pins.retention_days,
            "retention_policy_mismatch")
    args = dict(Bucket=pins.recovery_bucket, Key=key, VersionId=version, ExpectedBucketOwner=pins.account_id)
    head = s3.head_object(**args)
    require(head.get("VersionId") == version and not head.get("DeleteMarker"), "object_version_mismatch")
    require(type(head.get("ContentLength")) is int and head["ContentLength"] == request["size"], "size_mismatch")
    retention = s3.get_object_retention(**args)["Retention"]
    modified, until = timestamp(head.get("LastModified")), timestamp(retention.get("RetainUntilDate"))
    require(head.get("ObjectLockMode") == retention.get("Mode") == "COMPLIANCE", "object_lock_mismatch")
    require(timestamp(head.get("ObjectLockRetainUntilDate")) == until, "retention_response_mismatch")
    # S3 LastModified is whole-second, whereas retention preserves fractional seconds.
    require(modified <= now and until > now
            and until >= modified + timedelta(days=pins.retention_days) - timedelta(seconds=1),
            "retention_too_short_or_expired")
    return dict(response_version=VERSION, status="PASS", account_id=pins.account_id,
                environment_id=pins.environment_id, policy_revision=pins.policy_revision,
                authority_arn=pins.verifier_arn, release_sha256=pins.release_sha256,
                generation_id=request["generation_id"], manifest_sha256=request["manifest_sha256"],
                bucket=pins.recovery_bucket, object_key=key, version_id=version, size=request["size"],
                mode="COMPLIANCE", retain_until_utc=until.isoformat(), last_modified_utc=modified.isoformat(),
                observed_at_utc=now.isoformat(), retention_days=pins.retention_days,
                ciphertext_sha256_verified=False)


def dispatch(event, context, kind):
    try:
        require(len(json_bytes(event)) <= LIMIT, "request_too_large")
        require(os.environ["TRUST_KIND"] == kind, "handler_role_mismatch")
        pins, policy = settings(context)
        handler = receipt_authority if kind == "receipt" else metadata_verifier
        return handler(event, pins, policy, sdk_client("s3"), datetime.now(timezone.utc))
    except Exception as exc:
        return dict(response_version=VERSION, status="BLOCK",
                    code=str(exc) if isinstance(exc, TrustError) else "authority_dependency_failed")


def receipt_handler(event, context):
    return dispatch(event, context, "receipt")


def verifier_handler(event, context):
    return dispatch(event, context, "verifier")


class RecoveryTrustClient:
    """Only authenticated, version-qualified Invoke responses can provide evidence."""

    def __init__(self, pins: Pins, policy: dict, *, client=None):
        self.pins = pins
        self.policy = validate_policy(policy, pins.policy_revision)
        self.client = client if client is not None else sdk_client("lambda")
        endpoint = getattr(getattr(self.client, "meta", None), "endpoint_url", None)
        require(endpoint == "https://lambda.eu-central-1.amazonaws.com", "lambda_endpoint_invalid")

    def request(self, generation, manifest_hash, operation):
        return dict(request_version=VERSION, operation=operation, account_id=self.pins.account_id,
                    environment_id=self.pins.environment_id, policy_revision=self.pins.policy_revision,
                    generation_id=generation, manifest_sha256=manifest_hash)

    def invoke(self, arn, request):
        expected_version = published_arn(arn, self.pins.account_id)[4]
        payload = json_bytes(request)
        require(len(payload) <= LIMIT, "request_too_large")
        for attempt in range(3):
            try:
                response = self.client.invoke(FunctionName=arn, InvocationType="RequestResponse",
                                              LogType="None", Payload=payload)
                break
            except Exception as exc:
                if (attempt == 2 or (aws_code(exc) not in TRANSIENT_CODES
                                    and type(exc).__name__ not in TRANSIENT_TYPES)):
                    raise TrustError("lambda_invocation_failed") from None
                time.sleep(0.2 * (attempt + 1))
        require(isinstance(response, dict), "lambda_response_invalid")
        body = response.get("Payload")
        try:
            require(response.get("StatusCode") == 200 and "FunctionError" not in response
                    and response.get("ExecutedVersion") == expected_version, "lambda_execution_failed")
            require(body is not None and callable(getattr(body, "read", None)), "lambda_payload_missing")
            data = strict_json(body.read(LIMIT + 1))
        except TrustError:
            raise
        except Exception:
            raise TrustError("lambda_payload_invalid") from None
        finally:
            if body is not None:
                body.close()
        require(isinstance(data, dict) and type(data.get("response_version")) is int
                and data["response_version"] == VERSION and data.get("status") == "PASS", "authority_blocked")
        return data

    def validate_storage(self, proof, key, digest):
        fields(proof, STORAGE_FIELDS)
        require(proof["bucket"] == self.pins.receipt_bucket and proof["key"] == key
                and proof["sha256"] == digest and proof["mode"] == "COMPLIANCE", "receipt_storage_mismatch")
        version_id(proof["version_id"])
        require(timestamp(proof["retain_until_utc"]) >= datetime.now(timezone.utc) + timedelta(days=31),
                "receipt_lock_missing_or_short")

    def receipt(self, request):
        binding(request, self.pins, self.policy, datetime.now(timezone.utc))
        result = fields(self.invoke(self.pins.receipt_arn, request),
                        {"response_version", "status", "receipt", "storage", "policy_storage"})
        receipt = fields(result["receipt"], {"receipt_version", "request", "observed_at_utc", "authority_arn",
                                           "release_sha256", "policy_version_id"})
        binding(receipt["request"], self.pins, self.policy, datetime.now(timezone.utc))
        require(type(receipt["receipt_version"]) is int and receipt["receipt_version"] == VERSION
                and receipt["request"] == request and receipt["authority_arn"] == self.pins.receipt_arn
                and receipt["release_sha256"] == self.pins.release_sha256, "receipt_binding_mismatch")
        observed = timestamp(receipt["observed_at_utc"])
        require(timestamp(self.policy["recovery_boundary"]["cutoff_at_utc"]) < observed
                <= datetime.now(timezone.utc) + timedelta(seconds=60), "receipt_time_invalid")
        key = f"receipts/{self.pins.environment_id}/{request['generation_id']}/{request['operation']}.json"
        self.validate_storage(result["storage"], key, sha(json_bytes(receipt)))
        self.validate_storage(result["policy_storage"], f"policies/{self.pins.policy_revision}.json",
                              self.pins.policy_revision)
        require(receipt["policy_version_id"] == result["policy_storage"]["version_id"], "policy_version_mismatch")
        return result

    @contextmanager
    def authorized_generation(self, *, policy_path: Path, set_path: Path):
        import rove_recovery_set as recovery
        raw_policy, _ = recovery.stable_bytes(policy_path)
        require(sha(raw_policy) == self.pins.policy_revision
                and recovery.strict_json(raw_policy) == self.policy, "independent_policy_mismatch")
        raw_manifest, _ = recovery.stable_bytes(set_path / "manifest.json")
        manifest = recovery.verify_recovery_set(set_path)
        require(manifest["account_restore_safety"]["status"] == recovery.SAFE_FOR_ACCOUNT_RESTORE,
                "candidate_not_safe")
        recovery.check_generation_boundary(manifest, recovery.ledger_coverage(self.policy))
        request = self.request(manifest["recovery_set_id"], sha(raw_manifest), "manifest")
        receipt = self.receipt(request)
        derived = strict_json(json_bytes(self.policy))
        derived["generation_receipts"][request["generation_id"]] = request["manifest_sha256"]
        with tempfile.TemporaryDirectory(prefix="rove-external-approval-") as directory:
            policy_copy = Path(directory).resolve() / "policy.json"
            recovery.private_write(policy_copy, json_bytes(derived))
            approval = recovery.check_account_restore_generation(policy_path=policy_copy, set_path=set_path,
                                                                  admission_mode="prospective")
            require(recovery.stable_bytes(set_path / "manifest.json")[0] == raw_manifest
                    and recovery.stable_bytes(policy_path)[0] == raw_policy, "generation_changed_during_approval")
            session = TrustSession(self, set_path, policy_path, raw_policy, raw_manifest, approval, receipt)
            try:
                yield session
            finally:
                session.active = False


def cipher_metadata(path):
    """Stable, bounded streaming hash without buffering the ciphertext in memory."""
    import rove_recovery_crypto as crypto
    path = crypto.absolute(path)
    with crypto.directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "ciphertext_file_invalid")
            require(0 < before.st_size <= crypto.MAX_ARCHIVE, "ciphertext_size_invalid")
            digest, total = hashlib.sha256(), 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                require(total <= crypto.MAX_ARCHIVE, "ciphertext_size_invalid")
                digest.update(chunk)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            require(crypto.identity(before) == crypto.identity(os.fstat(source.fileno())) == crypto.identity(current)
                    and total == before.st_size, "ciphertext_changed")
            return {"sha256": digest.hexdigest(), "bytes": total}


class TrustSession:
    """Gate-bound adapter state, not a new backup/upload pipeline."""

    def __init__(self, client, source, policy, policy_raw, manifest_raw, approval, receipt):
        self.client, self.source, self.policy = client, source, policy
        self.policy_raw, self.manifest_raw = policy_raw, manifest_raw
        self.approval, self.manifest_receipt = approval, receipt
        self.active = True
        self.crypto_receipt = None
        self.offsite_verified = False

    def check_current(self):
        import rove_recovery_set as recovery
        require(self.active, "approval_context_closed")
        require(recovery.stable_bytes(self.source / "manifest.json")[0] == self.manifest_raw
                and recovery.stable_bytes(self.policy)[0] == self.policy_raw, "approved_inputs_changed")
        # Revalidate the full set, latest ledger and ephemeral receipt-bound policy.
        recovery.check_account_restore_generation(policy_path=self.approval.policy_path, set_path=self.source,
                                                   admission_mode="prospective")

    def bind_crypto(self, envelope: Path):
        import rove_recovery_crypto as crypto
        self.offsite_verified = False
        self.crypto_receipt = None
        self.check_current()
        request = dict(self.manifest_receipt["receipt"]["request"], operation="crypto",
                       manifest_receipt_version_id=self.manifest_receipt["storage"]["version_id"])
        raw, info = crypto.read_file(envelope / "crypto.json", limit=LIMIT)
        meta = crypto.validate_metadata(raw, info["sha256"], self.client.pins.public_key_fingerprint)
        request.update(metadata_json=raw.decode("utf-8"), metadata_sha256=info["sha256"])
        validate_crypto(request["metadata_json"], info["sha256"], request, self.client.pins,
                        self.client.policy, datetime.now(timezone.utc))
        cipher = envelope / (request["generation_id"] + ".tar.gz.gpg")
        cipher_info = cipher_metadata(cipher)
        require(cipher_info == {"sha256": meta["ciphertext_sha256"], "bytes": meta["ciphertext_bytes"]},
                "ciphertext_hash_mismatch")
        result = self.client.receipt(request)
        require(crypto.read_file(envelope / "crypto.json", limit=LIMIT)[0] == raw
                and cipher_metadata(cipher) == cipher_info, "envelope_changed")
        self.crypto_receipt, self.envelope = result, envelope
        return result

    def verify_uploaded(self, version):
        import rove_recovery_crypto as crypto
        self.offsite_verified = False
        self.check_current()
        require(self.crypto_receipt is not None, "external_crypto_receipt_required")
        bound = self.crypto_receipt["receipt"]["request"]
        raw, info = crypto.read_file(self.envelope / "crypto.json", limit=LIMIT)
        require(raw.decode() == bound["metadata_json"] and info["sha256"] == bound["metadata_sha256"],
                "bound_crypto_metadata_changed")
        meta = strict_json(raw)
        cipher = self.envelope / (bound["generation_id"] + ".tar.gz.gpg")
        require(cipher_metadata(cipher)
                == {"sha256": meta["ciphertext_sha256"], "bytes": meta["ciphertext_bytes"]}, "ciphertext_hash_mismatch")
        request = self.client.request(bound["generation_id"], bound["manifest_sha256"], "metadata")
        request.update(object_key=f"recovery/{bound['generation_id']}.tar.gz.gpg", version_id=version_id(version),
                       size=meta["ciphertext_bytes"], retention_days=self.client.pins.retention_days)
        result = fields(self.client.invoke(self.client.pins.verifier_arn, request), VERIFIER_RESPONSE)
        expected = {key: request[key] for key in ("account_id", "environment_id", "policy_revision", "generation_id",
                                                 "manifest_sha256", "object_key", "version_id", "size", "retention_days")}
        expected.update(authority_arn=self.client.pins.verifier_arn, release_sha256=self.client.pins.release_sha256,
                        bucket=self.client.pins.recovery_bucket, mode="COMPLIANCE", ciphertext_sha256_verified=False)
        require(all(type(result[key]) is type(value) and result[key] == value for key, value in expected.items()),
                "verifier_binding_mismatch")
        observed, modified, until = (timestamp(result[key]) for key in
                                     ("observed_at_utc", "last_modified_utc", "retain_until_utc"))
        now = datetime.now(timezone.utc)
        require(abs((now - observed).total_seconds()) <= 60 and modified <= observed and until > now
                and until >= modified + timedelta(days=self.client.pins.retention_days) - timedelta(seconds=1),
                "verifier_retention_or_freshness_invalid")
        self.check_current()
        require(crypto.read_file(self.envelope / "crypto.json", limit=LIMIT)[0] == raw, "bound_crypto_metadata_changed")
        require(cipher_metadata(cipher)
                == {"sha256": meta["ciphertext_sha256"], "bytes": meta["ciphertext_bytes"]}, "ciphertext_hash_mismatch")
        self.offsite_verified = True
        return result
