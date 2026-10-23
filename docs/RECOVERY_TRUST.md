# External Recovery Trust Authorities

Scope: two external witnesses and minimal adapters, not an active backup pipeline.
The app, current control files, canonical generation gate, frozen baseline, T0,
history proof and pre-T0 restrictions are unchanged. No scheduler, uploader job,
reader keys, Production packages or Production runtime changes are introduced.

## Contracts

`rove_recovery_trust.py` contains both strict Lambda handlers and local adapters.
`deploy/recovery/trust_stack.py` renders their CREATE-only CloudFormation template.
Deployment parameters are non-secret, independently approved configuration, not
Production ENV contents. Keep actual parameter files and operational receipts
outside Git. The template has no default Production account, bucket or policy.

Pin the account, environment, approved canonical policy SHA-256, release SHA-256,
public fingerprint and both numeric published Lambda version ARNs. No alias or
`$LATEST` is accepted. The SDK resolves normal AWS endpoints, with no endpoint
override, a 3-second connect timeout and a 25-second read timeout. Invoke retries
are limited to three byte-identical requests on specified transient failures.
AccessDenied, FunctionError, malformed responses and business BLOCK never retry
into a fallback. HTTP 200 alone is not success. Unknown schemas/fields block.

### Manifest and Crypto Receipts

The Receipt Authority validates a small request against its published, immutable
configuration. It conditionally creates a policy snapshot and generation receipt
with `If-None-Match: *`, an explicit transport checksum and ExpectedBucketOwner.
It verifies the stored bytes, exact returned version and COMPLIANCE retention.
Identical retries return the original document, timestamp and version. Conflicting
bindings block; there is no overwrite, delete marker or alternate key fallback.

The manifest receipt binds generation, manifest hash, externally observed UTC time,
policy revision, receipt version, published function ARN, release and policy object
version. A separate crypto receipt preserves the exact `crypto.json` bytes and
their independently stored SHA-256, including ciphertext/archive hashes, size and
recipient fingerprints. It also pins the preceding manifest receipt object version.
Crypto metadata is not an ENV file and contains no secrets or customer contents.

The authority is a durable external witness of a submitted binding, not proof that
a compromised VPS originally produced truthful data. An AWS control-plane admin
remains a separate trust root. Runtime roles cannot administer the authorities.

### Existing Generation Gate

`RecoveryTrustClient.authorized_generation()` fully validates the local set and
the independently approved policy before invoking the authority. Only a strictly
validated authenticated response supplies a receipt to a private, ephemeral policy
copy used by the existing `check_account_restore_generation()` function. This copy
is not independently trusted on its own and is removed when the context ends.
Original policy, control files and baseline are never changed by the adapter.

Missing evidence remains blocked; UNSAFE, UNKNOWN, UNVERIFIED runtime revisions,
legacy manifests and pre-T0 generations cannot request their way into SAFE. The
manual canonical replay/restore path is not modified. Do not use ephemeral policy
copies as saved approvals or introduce a direct replay bypass.

### Metadata Verifier

The second Lambda has its own execution role. Bucket/account/prefix are fixed in
published configuration. It accepts one exact key derived from the generation and
a non-null VersionId. It calls only HeadObject and GetObjectRetention with that
VersionId and ExpectedBucketOwner. It checks size, both lock responses, COMPLIANCE,
unexpired retention and at least 30 days from LastModified. S3 represents
LastModified at whole-second precision; only a one-second rounding tolerance is
permitted when comparing the fractional retention timestamp.

**Metadata validation does not hash the S3 body.** The response explicitly says
`ciphertext_sha256_verified=false`; an ETag is not treated as SHA-256. The adapter
separately revalidates the locally hashed ciphertext and externally witnessed
crypto metadata. Existing byte-exact retrieval/restore proofs are not replaced.
The IAM GetObjectVersion permission needed for versioned HEAD technically also
allows reading encrypted object bytes. The handler never performs such a GET,
has no GPG private key and has no write/list/delete permissions.

### Future Pipeline Integration (Not Activated)

1. Collect and fully validate a new complete manifest-v4 recovery set.
2. Enter `authorized_generation(policy_path=..., set_path=...)`.
3. Obtain the external manifest receipt and pass the existing canonical gate.
4. Use the existing encryption wrapper; hash and locally validate its output.
5. Call `session.bind_crypto(envelope)` to preserve external sidecar custody.
6. Perform the existing conditional ciphertext upload and retain its exact VersionId.
7. Call `session.verify_uploaded(version_id)`; no latest fallback.
8. Record success only after the exact-version verifier returns a fully validated PASS.

The session clears success before each attempt and discards an old crypto approval
before rebinding. A failed dependency cannot leave the current attempt successful.
This module does not upload, create generations, restore or start application jobs.
There is no ready-made four-hour pipeline in this release.

## IAM and Storage

- Receipt bucket: private, Block Public Access, versioned, SSE-S3, BucketOwnerEnforced.
- Receipt bucket: COMPLIANCE default retention **365 days**, no lifecycle rule.
- Both bucket and published versions use CloudFormation Retain on deletion/replacement.
- Only the authority role can PutObject into its receipt/policy prefixes.
- Bucket policy requires conditional writes and denies all runtime deletion and
  retention/legal-hold overrides. Uploader direct writes are explicitly denied.
- Authority role: PutObject, GetObject, GetObjectVersion, GetObjectRetention only
  on its receipt/policy prefixes; GetObject resolves the saved receipt after 412.
- Verifier role: GetObjectVersion and GetObjectRetention only on the configured
  encrypted recovery object prefix, plus its own bounded log stream permissions.
- Uploader: one additional inline policy granting InvokeFunction only on the two
  published numeric version ARNs. Existing write-only S3 policy is untouched.
- No IAM user, access key, AssumeRole right for uploader, function URL or schedule.
- Lambda obtains AWS-managed temporary execution-role credentials.

365-day COMPLIANCE retention is irreversible for written receipts during their
retention period, even for account root. This is distinct from the existing
30-day ciphertext bucket. Review this operational commitment before creating
the new stack. It is not a temporary disposable test bucket.

A receipt/policy must still have at least 31 days of immutable retention remaining.
Renew the independently approved policy and published release before this window
closes (at most 334 days after the first policy snapshot). Otherwise the authority
blocks. Existing archived bindings do not silently gain fresh timestamps/versions.
No renewal automation is introduced. Changes to code, policy, recipient or bucket
require a separately reviewed published release and updated independent pins.

## Operator Release Gate

No agent executes AWS or Production operations. The human uses CloudShell Frankfurt
with their existing temporary admin session; no permanent admin/reader keys are
created. Local tests must pass before any AWS resource creation. First run the
read-only infrastructure plan and review its template/parameter/policy hashes.
`deploy/recovery/trust_plan.py` accepts only independently pinned template and
parameter file hashes. It uses read-only STS/IAM/S3/CloudFormation APIs, verifies
account, absent new stack/bucket, existing Production configuration, write-only
uploader scope, zero active reader keys and the human admin credential model.
The output strips request-specific SDK metadata so configuration hashes are stable.
It never creates a change set, key, receipt or object. Unexpected drift blocks.
Then create a CloudFormation CREATE change set and inspect its exact resource list
before executing. Never apply an update to an existing stack in this workflow.

After creation, verify deployed code/release, published versions, role policies,
bucket policy, versioning, Object Lock and absence of unexpected rights. Only then
use the existing uploader credentials through a hidden in-memory CloudShell input.
Do not write credential files or print SDK exceptions/request bodies.

Live tests required before GO or commit:

- Synthetic non-customer manifest receipt: first creation, exact repeat, conflict BLOCK.
- Exact stored receipt/policy versions and active COMPLIANCE lock.
- Uploader direct receipt create/overwrite/delete denied. Never mutate frozen data.
- Verifier read-only against the already approved frozen S3 object VersionId, size,
  COMPLIANCE and exact retention; no upload, new version or download.
- Uploader still has no recovery read, role-assumption, IAM or function-update rights.
- Execution roles have no policy modification rights; verifier has no writes.
- Reader user still has zero active keys; human admin has no permanent access keys.
- Existing recovery bucket policies/config hashes unchanged; uploader's original
  policies unchanged except the reviewed, separate two-version invoke policy.
- Production/control/baseline unchanged; no timers, ENV changes, restart or restore.

On failure, block use of the new trust paths. Do not change permissions ad hoc or
retry a conflict using a new key. A rollback can remove the new uploader invoke
policy and disable Lambda invocation; immutable evidence is retained. Do not
shorten retention or delete the frozen ciphertext. No Production rollback is
needed because Production runtime and existing manual recovery path are untouched.

## Local Validation

Use the existing Python 3.12 project environment. No requirements change is needed
for pure contract tests. Boto3 is imported lazily and is provided by CloudShell and
the Lambda runtime for AWS operations. SDK support is a live precondition; missing
support blocks, not a reason to install packages on Production.

`test_recovery_trust.py` covers schemas, receipts, gate integration and fail-closed
adapter state using synthetic fixtures. `test_recovery_trust_infrastructure.py`
also executes the exact rendered inline Lambda handlers with synthetic SDKs and
checks least privilege and template limits. `test_recovery_trust_crypto.py` adds
one real synthetic GPG encryption across the gate/crypto/verifier adapter contract.
The existing 54 crypto and 14 legacy-crypto tests remain unmodified and mandatory.

Local PASS is not live verification. Until both external paths are deployed and
the controlled live/security checks pass, the result is:

`NO-GO - EXTERNAL RECOVERY TRUST PATHS NOT VERIFIED`
