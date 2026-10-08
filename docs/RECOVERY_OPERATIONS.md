# Recovery Operations: Separate Immutable Release

Status: implementation candidate, NOT activated, NOT FROZEN. This document does
not replace the frozen recovery contract. A successful local/mock test is not
an AWS or operator-delivery attestation.

## Ownership and Isolation

`rove_recovery_operations.py` owns scheduling orchestration and status only.
Collector, SQLite backup, canonical generation gate, GPG crypto, and external
trust adapter are reused unchanged. The builder exports these exact committed
bytes; collector/gate/backup/crypto must match app commit
`4e6c29074acb7f08f7908ea34f2d405051b4925b`. The trust adapter was introduced
separately in `5a8cdc4b39110fc92f4fc1612d421e2f6b9df301`; its bytes must match
that commit AND the live-approved Lambda code hash. Both origins are recorded
separately; the app commit is never relabeled as containing the newer adapter.

Production `/root/clarity`, its HEAD, API venv, runtime/environment, all existing
timers, T0, inventory, policy, ledger anchor and original baseline remain
unchanged. No application/bot import, replay, restore, policy rewrite or IAM
operation is part of the job. Existing 03:20 DB backup remains independent.

Install from a task-only Git commit, after relevant and full-suite validation.
Export with `scripts/build_recovery_operations_release.py <exact-commit>
<new-external-directory>` from a clean canonical source. The export contains
only its explicit allowlist and `release.json`. Do not copy a working tree.
Record the commit and release-manifest SHA-256 independently before transfer.
No keys, credentials, configs or databases belong in the package or Git.

Operator installs code as root-owned directories 0555/files 0444 at
`/opt/rove-recovery/releases/<exact-commit>/`. The root-controlled `current`
pointer selects this release. The runtime verifies the full file inventory,
hashes, pinned commit, permissions and frozen-code compatibility before use.
The package is immutable by deployment convention, not resistant to a hostile
root administrator; the independent commit/hash receipt remains authoritative.

Use a dedicated Python 3.12 venv at `/opt/rove-recovery/venv`, populated solely
from this release's `requirements/recovery.txt`. Do not touch API packages.
Use existing `/usr/bin/gpg` >=2.4.4 and `gpgconf`. Only the approved PUBLIC key
is installed at `/etc/rove-recovery/recovery-public.asc` (root:root 0600).
Private key/passphrase are NEVER installed on the VPS.

## Operator Prerequisites: STOP Until Evidenced

Before installing/enabling units, obtain fresh read-only production evidence:
HEAD, expected three untracked ENV files only, API/Gunicorn health, bot inactive,
no restart loop, VKS mail OFF/live approval NO, current policy/inventory hashes,
frozen source/core hashes and latest ledger anchor. Verify the independent vault
revision still covers the actual four ENV and seven runtime config files.
Do not output their contents or secret values.

Read the deployed trust stack outputs/parameters in CloudShell Frankfurt.
Record actual `environment_id`, numeric published receipt/verifier ARNs, private
receipt bucket, approved public fingerprint, policy revision and code-release
SHA-256. Reuse the live-approved trust bindings exactly; no new versions,
aliases, policy changes or endpoint/signing overrides. The operations job uses
no reader key and makes no IAM calls. The existing uploader may invoke the
two approved numeric Lambda versions and conditional-PUT `recovery/*` only.

Existing names identified from source (not production-value verification):
Telegram `TELEGRAM_TOKEN`, operator allowlist `ADMIN_USER_IDS`/`ADMIN_USER_ID`;
Brevo `BREVO_API_KEY`, sender `ROVE_LOGIN_FROM_EMAIL`. HetrixTools' own Telegram
integration is not a reusable bot-token proof. An operator must verify that the
existing bot may message the selected allowlisted operator chat, and independently
identify the existing operator email. No customer recipient or business mail
preference is used. If either existing channel cannot be evidenced: STOP, NO-GO;
do not create a new secret/account or silently fall back to one channel.

Existing secret values are supplied privately by the operator as root:root 0600
files in `/etc/rove-recovery/credentials/` (directory 0700), with names:
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `TELEGRAM_TOKEN`, `BREVO_API_KEY`.
Use systemd `LoadCredential`; no ENV values, CLI values, interactive echoes or
credential files in a release. These are copies/references of existing approved
credentials, not new keys. Keep their custody in the existing independent vault.

Create root-owned 0700 directories `/etc/rove-recovery`,
`/var/lib/rove-recovery/state`, `/var/lib/rove-recovery/work`; parent directories
must already exist safely. `/run/rove-recovery-gpg` is a short, 0700 systemd
RuntimeDirectory. Do not use a deep GPG path that exceeds Unix-socket limits.

Non-sensitive `/etc/rove-recovery/operations.json` must be root:root 0600.
Its strict schema (replace ALL placeholders with independently verified values):

```json
{
  "version": 1,
  "release_commit": "EXACT_NEW_OPERATIONS_COMMIT",
  "app_commit": "4e6c29074acb7f08f7908ea34f2d405051b4925b",
  "app_repo": "/root/clarity",
  "database": "/root/clarity/clarity.db",
  "ledger": "/root/account_delete_tombstones.jsonl",
  "inventory": "/root/rove-recovery-control/ACTUAL_FROZEN_INVENTORY.json",
  "inventory_sha256": "INDEPENDENT_FROZEN_INVENTORY_SHA256",
  "policy": "/root/rove-recovery-control/ACTUAL_FROZEN_POLICY_REVISION.json",
  "public_key": "/etc/rove-recovery/recovery-public.asc",
  "public_key_sha256": "APPROVED_PUBLIC_KEY_SHA256",
  "state_root": "/var/lib/rove-recovery/state",
  "work_root": "/var/lib/rove-recovery/work",
  "gpg_temp_parent": "/run/rove-recovery-gpg",
  "gpg": "/usr/bin/gpg",
  "pins": {
    "account_id": "295356239462",
    "environment_id": "ACTUAL_STACK_ENVIRONMENT",
    "policy_revision": "ad2a6783914b20b3f0d273c446e6d39e0d86a9385eb40b9112fb5d80c9c60316",
    "receipt_bucket": "ACTUAL_RECEIPT_BUCKET",
    "recovery_bucket": "rove-recovery-prod-2026-295356239462-eu-central-1-an",
    "receipt_arn": "ACTUAL_NUMERIC_PUBLISHED_RECEIPT_ARN",
    "verifier_arn": "ACTUAL_NUMERIC_PUBLISHED_VERIFIER_ARN",
    "release_sha256": "ACTUAL_APPROVED_TRUST_MODULE_SHA256",
    "public_key_fingerprint": "APPROVED_40_HEX_PUBLIC_FINGERPRINT",
    "retention_days": 30
  },
  "recipients": {
    "telegram_chat_id": "VERIFIED_OPERATOR_CHAT_ID",
    "email": "VERIFIED_EXISTING_OPERATOR_EMAIL",
    "from_email": "VERIFIED_EXISTING_BREVO_SENDER"
  }
}
```

Any config or release change invalidates the locally recorded operator delivery
confirmation. No fallback to ambient AWS credentials, alternative recipients,
latest object version or unpublished Lambda is permitted.

## Activation Sequence: Operator Only

1. Verify independent release receipt, package hashes, app isolation and all
   dependencies on VPS. Validate all four unit files with `systemd-analyze verify`
   before installation. Preflight fail means no activation.
2. Install ONLY the four new unit files from the committed immutable release;
   reload systemd. Do not start/enable either timer yet. No API/nginx/bot restart.
3. Invoke `test-alert --config /etc/rove-recovery/operations.json` through a
   temporary root systemd unit using the same `LoadCredential` and runtime
   isolation as the watcher. A returned Telegram/Brevo API acceptance is NOT
   proof of inbox delivery. Read the probe reference on BOTH actual channels.
4. Only after actual receipt of both messages, invoke
   `confirm-alert --config ... --both-received <that-probe-id>` in that context
   within one hour. Never manufacture the activation JSON or confirm a mock.
5. Start `rove-recovery-offsite.service` ONCE, with timers still disabled. It
   creates one regular new generation. Wait for complete success including two
   external receipts, canonical gate, GPG, checksum-confirmed conditional PUT
   and exact-version external verifier. No second test generation unless needed.
6. Only after success and an unchanged production postcheck, enable/start BOTH
   new timers. Verify active states and next recovery calendar elapse. Run the
   watcher and confirm HEALTHY. Inspect journal and durable success receipt.
7. Check public/local health, frozen control/baseline/source hashes, API PID and
   service/env/config hashes against preflight. Confirm HetrixTools remains green.
   Confirm no IAM/key/bucket changes. Then return evidence for formal acceptance.

Until these actual operator steps pass:
`NO-GO - AUTOMATED OFFSITE OPERATIONS NOT VERIFIED`.

## Runtime Semantics

Six scheduled UTC runs: 00:20, 04:20, 08:20, 12:20, 16:20, 20:20.
`Persistent=true` coalesces missed runs into one catch-up after restart; it does
not generate one backup for every missed slot. No timezone/DST drift, no jitter.
45-minute runtime bound is well below the four-hour interval. A persistent,
0600 flock prevents overlap; it is never removed while a job may hold it.

The source manifest keeps APP commit provenance. Operations commit provenance
is separate in `release.json` and the success receipt. Original policy remains
hash-identical; canonical trust adapter uses a short-lived derived policy only
after authenticating the independently stored manifest receipt. No local
self-attestation or historical-coverage upgrade is introduced.

`success.json` is atomically replaced/fsynced ONLY after external verifier PASS.
It preserves manifest/crypto authority responses, exact S3 version/size/hash,
policy/code/config bindings and verification time. Missing, changed-config or
future success is CRITICAL. Thresholds: <=6h HEALTHY, >6h WARNING, >10h CRITICAL.
The five-minute independent watcher detects a stopped recovery timer, failed
attempt or stale running job and retries undelivered alerts. Repeats are limited
to once per four hours per unchanged state only when BOTH providers accepted.

No remote-body retrieval occurs during the automated job: the frozen metadata
verifier explicitly proves version/size/retention, not remote SHA-256. PUT sends
an explicit SHA-256 checksum and requires the server's matching confirmation.
The earlier independent retrieval/restore remains separate evidence, not a
claim that each scheduled generation was decrypted/restored.

Only the job's isolated `job-<random-id>` workspace is removed after each run;
SIGKILL leftovers are reclaimed under the lock on next run. The old baseline
and independent control files are never swept. Failed/ambiguous upload attempts
are not retried on an alternate key; job ID/generation/version (when known)
remain in `attempt.json`. Uploaded objects are not deleted or unlocked. New
generations use their normal independent receipts, not the baseline's receipt.

## Failures, Rollback and Residual Risks

Transport messages contain fixed stage/reference only, no SQL, provider error
text, URL tokens, ENV values, credentials or customer data. A stage failure
alerts both channels and cannot advance last success. WARNING/CRITICAL and a
stopped main timer are detected by the separate watcher.

The watcher shares the VPS. If both timers/the host/network fail, it cannot
deliver its own alert. Existing external HetrixTools uptime monitors cover
public service outages, but do not independently prove recovery freshness when
the app remains online. This is an explicit common-mode residual risk, NOT an
external dead-man guarantee. Do not mark a missing independent recovery monitor
as tested. No new monitoring platform is introduced here.

The frozen authority requires at least 31 days remaining on a policy receipt.
An initially 365-day policy snapshot therefore cannot authorize new uploads
indefinitely. Before that threshold, separately approved policy/authority
maintenance is required; this job must fail closed and alert, not refresh or
overwrite the snapshot itself. Existing credentials must likewise be maintained
through the approved operator/vault process, not rotated by this job.

Operator rollback disables/stops ONLY the two new timers and their job/watch
services, then selects a previously verified operations release if available.
Do not roll back `/root/clarity`, mutate frozen controls, delete the baseline or
alter S3 retention. An ambiguous PUT needs an approved read-only exact-object
investigation, not a repeated upload or latest-version fallback.

A synthetic or first successful run proves mechanics, not sustained reliability.
Final acceptance requires the first actual run, both received alarm messages,
active next scheduled run, fresh success and unchanged production evidence.
Only then: `GO - AUTOMATED OFFSITE RECOVERY OPERATIONS VERIFIED`.
