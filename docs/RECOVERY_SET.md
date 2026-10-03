# Recovery set - Block 2A

## Scope and status

This block implements local collection, read-only verification and a mandatory
generation gate in the existing tombstone replay entry point. The collector does
not install a service, change a timer or retention, upload, encrypt, create keys,
restore a database, start the application, or contact any mail/provider endpoint.
The replay CLI can scrub only a separately staged, explicitly eligible database;
all replay acceptance tests use disposable synthetic data. No real restore is run.
Production Runtime and Monitoring Block 1 are unchanged.

`COMPLETE` means the required local files and metadata passed collection and
verification. It is NOT proof of offsite storage, recoverable external keys,
historical ledger completeness, financial restore correctness, or a measured RTO.
Those distinctions are explicit in the manifest. A full recovery freeze still
requires independent storage and an isolated restore acceptance test.

## Verified source contracts and remaining operator evidence

- Production DB: `/root/clarity/clarity.db`, based on the supplied backup unit.
- The deletion ledger is resolved by `rove_account_delete_cleanup.tombstone_path`.
  Without `ROVE_ACCOUNT_DELETE_TOMBSTONES`, the normal `/root/clarity` installation
  uses `/root/account_delete_tombstones.jsonl`. Do not infer the effective path
  from this default: verify the running configuration before creating inventory.
- Ledger records are append-only JSONL: positive integer `user_id` and UTC
  `deleted_at`. `record_delete_tombstone` fsyncs intent before account deletion.
  The collector additionally rejects missing timestamps, malformed records and
  partial final records. It neither changes nor replays the source ledger.
- Active Web report references are `report_links.html_path`, with the canonical
  layout `<ROVE_REPORT_PUBLIC_DIR>/<token>/index.html`. The renderer publishes
  each opaque-token directory while holding its DB write transaction.
- PDFs use `<CLARITY_REPORTS_DIR>/rove_report_<user>_<YYYY-MM>.pdf`, including
  retained `archive/*.pdf.gz` copies. Actual roots must be operator-verified.
- Python/Gunicorn versions, system timezone, effective units/drop-ins, Nginx
  configuration, ENV paths, ledger anchor and secret custody locations require
  current read-only Production inventory. Code defaults are not that proof.
- There is no canonical global migration sequence/version. Record the exact
  schema fingerprint and Git SHA, not an invented migration version.

Production inventory/activation is NOT performed by Block 2A. The collector
accepts no implicit DB, ledger, artifact root, or output directory defaults.
No real Production set has been demonstrated by the synthetic test suite.

### Production compatibility evidence (2026-10-01)

The supplied operator output confirms release
`8dcfd1d25546f57b510058ef634ab781e7dc23c5`, API active, bot inactive, four
effective API drop-ins, and the enabled Nginx symlink resolving to
`/etc/nginx/sites-available/getrove.de`. The ledger exists but contains zero
bytes. Its modification time is NOT a deletion timestamp or proof of history.
At that inventory, independent key/config custody did not exist. Subsequently the
operator confirmed `Runtime-Secrets im Tresor erfasst und unabhängig geprüft.`
That is operator evidence of independent custody, not a collector test of secrets.
Production Runtime and Monitoring Block 1 were not changed for this correction.

The metadata inventory found 41 DB files, not 41 validated recovery generations.
No historical deletion completeness or safe baseline has been demonstrated for
any of them. Their current account-restore classification is
`UNSAFE FOR ACCOUNT-RESTORE`, including files with recent modification times.
Do not delete them, overwrite them, or declare them safe to make a gate pass.
No numerical Production cutoff date is assigned by this document.

The current active report has no demonstrated missing `support.js` dependency;
that is not one of this correction's three Production blockers.

## Recovery directory contract

```text
recovery_set_<UTC timestamp>_<random ID>/
  database/clarity.db
  ledger/account_delete_tombstones.jsonl
  artifacts/public_reports/<original token>/index.html
  artifacts/public_reports/support.js  # only when an active report references it
  artifacts/reports/rove_report_<user>_<month>.pdf
  artifacts/reports/archive/rove_report_<user>_<month>.pdf.gz
  runtime/metadata.json
  manifest.json
```

The output root must be an explicitly chosen private directory outside the Git
checkout and every source tree, e.g. `/root/rove-recovery-sets` after separate
operator approval. Directories are `0700`, files `0600`. Original permissions,
numeric UID/GID, source paths and checksums are metadata, not chmod/chown actions
against live files. Do not restore these private modes onto publicly served
report directories without the future reviewed restore procedure.

Each set is built in a unique private `.pending` directory. A successful set is
verified before publication. Failure produces a `.failed` directory/manifest
with a sanitized error code; partial private copies can remain there. Unsafe
paths or invalid inventory fail before creating a set. Neither partial nor
failed sets are authoritative, uploadable, or eligible to replace local backups.
This block adds no recovery-set rotation; failed/pending sets can consume disk
and must be covered by the separately approved activation/retention plan.

These are sensitive LOCAL plaintext files. Restrictive permissions are not
encryption. Do not serve this directory, commit it, or upload it unencrypted.

## Required inventory

Inventory is strict JSON. Unknown/duplicate keys are rejected; it must contain
only the following fields, with actual independently checked values:

| Field | Required content |
| --- | --- |
| `inventory_version` | Integer `1` |
| `database` | Absolute canonical DB path, equal to `--db` |
| `expected_schema_sha256` | `schema_metadata(conn)["sha256"]`, covering ordered schema objects and SQLite `user_version`; obtained read-only from the known compatible release |
| `ledger.path` | Effective absolute ledger path, equal to `--ledger` |
| `ledger.history_confirmed_complete` | Boolean `true` only after checking historical deletion coverage; otherwise `false` plus the verified boundary below |
| `ledger.audit_reference` | Non-secret `offline:`, `escrow:` or `vault:` audit reference |
| `ledger.anchor` | `{ "bytes": <verified prefix length>, "sha256": <prefix hash> }`; prefix ends at a complete record |
| `artifact_roots` | Absolute existing `public_reports` and `reports` directories |
| `runtime` | Whitelisted runtime properties below, never raw ENV values |
| `secret_dependencies` | List of `{ "name", "version", "custody_ref" }`, no secret values |

`runtime` contains exactly:

- `python_version`, `sqlite_version`, `gunicorn_version` (observed patch versions).
- `entrypoint`: `rove_app_wsgi:app`; `worker_class`: `gthread`; `workers`: `1`;
  `threads`: `4`; `bind`: `127.0.0.1:5057`. Different runtime inventory fails
  rather than silently describing an unreviewed runtime.
- `timezone`: effective system timezone used by naive Web report expiry strings.
- `environment_files`: absolute paths of ALL effective existing EnvironmentFiles.
- `config_files`: exactly one `{ "role", "path", "custody_ref" }` for each of
  `api_service`, `nginx_site`, `nginx_headers`, and zero or more `api_dropin`
  entries, one per effective file. Roles and paths cannot silently be omitted.
  Configuration symlinks alone are resolved strictly, preserving the configured
  path and actual target path, hash, bytes, UID/GID and mode in the manifest.
  DB, ledger, ENV and report symlinks remain forbidden. Invalid, missing,
  dangling or retargeted configuration fails closed.

The collector reads only `LoadState`, `FragmentPath`, `DropInPaths` and
`EnvironmentFiles` from `systemctl show rove-app-api.service`, never Environment
values or ExecStart arguments. The declared service/drop-in paths must exactly
match the effective paths; existing effective ENV paths must also match.
An unavailable/ambiguous unit or missing required EnvironmentFile blocks
collection. Absent optional ENV files are recorded explicitly. Drop-ins are
sorted deterministically by configured path within their role and rechecked
after capture. An added/uninventoried drop-in is an error, not an omission.

The four observed Production drop-ins to inventory are:

```text
/etc/systemd/system/rove-app-api.service.d/99-rove-wsgi.conf
/etc/systemd/system/rove-app-api.service.d/env.conf
/etc/systemd/system/rove-app-api.service.d/europe-market-data.conf
/etc/systemd/system/rove-app-api.service.d/market-data.conf
```

These paths are evidence from the operator output, not hardcoded collector
defaults. Recheck the effective list at real collection; additional paths must
be explicitly inventoried. Use the enabled Nginx path for provenance and retain
its resolved `sites-available` target identity. Nginx snippets need their own
inventory/custody entry; no raw unit, Nginx or ENV contents enter the set.

Configuration/ENV files are hashed and inventoried but their contents are NOT
copied. Version-controlled runtime templates plus expected settings are included
by reference to the Git SHA; exact effective external configuration must have
the declared independent custody reference. The collector records that custody
is NOT independently verified. A reference string is not a restore test.

Secret dependencies must identify current signing/HMAC keys, transport keys,
webhook/push secrets, TLS/Cockpit recovery dependencies where applicable, and
every provider vault key version needed by retained ciphertext. Required ENV
credential names in ENV and direct unit `Environment=` assignments are checked
without retaining their values. Historical provider
key versions are also discovered from `app_provider_secrets.key_version` and
`app_provider_accounts.iban_key_version`; missing references fail collection.
For non-versioned ENV secrets, use an independently assigned custody revision,
not a digest of the secret itself. Custody references accept only opaque
`offline:`, `escrow:` or `vault:` identifiers, never embedded credentials/URLs.
Do not introduce historical plaintext ENV backups as a workaround.

No filesystem check can prove that an entire older deletion history was not
lost before the first anchor. The historical check/audit is an explicit operator
attestation. Retain its trusted anchor independently; do not refresh it from a
possibly truncated ledger just to make collection pass. Later captures verify
the anchored prefix is unchanged and capture appended complete records.

## Ledger evidence and safe recovery boundary

Read-only evidence must distinguish an actual account deletion from a file or
authentication cleanup. `account_delete_file_cleanup.created_at/completed_at`
are queue/file-operation times; rows can be queued by TTL/legacy cleanup, and
owner IDs need not remain present. They cannot establish complete account-delete
history. Account delete codes are removed with account data. Admin events can
be anonymized on deletion. A successful delete-route log may corroborate a time
but not reconstruct the entire deleted identity set. Absence of rows/logs is
not proof that no deletion happened. Do not derive tombstones from guesses.

The following operator-only read-only query returns counts/time bounds and key
version numbers, never IDs, emails, cleanup paths, secret values or raw log rows.
It does not import the app or run schema ensures. Results must be reviewed before
claiming that Production history is reconstructible:

```bash
cd /root/clarity
git rev-parse HEAD
stat -c 'LEDGER bytes=%s modified=%y' /root/account_delete_tombstones.jsonl
/root/rove-app-api-venv/bin/python -B - <<'PY'
import sqlite3
from contextlib import closing
with closing(sqlite3.connect("file:/root/clarity/clarity.db?mode=ro", uri=True)) as db:
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("account_delete_file_cleanup", "app_account_delete_codes", "app_admin_events"):
        if table not in tables:
            print(table + "=MISSING")
            continue
        where = " WHERE lower(action) LIKE '%delet%' AND success=1" if table == "app_admin_events" else ""
        count, earliest, latest = db.execute(
            'SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM "' + table + '"' + where).fetchone()
        print(f"{table}: rows={count} earliest={earliest} latest={latest} HISTORY_PROOF=NO")
    for table, column in (("app_provider_secrets", "key_version"), ("app_provider_accounts", "iban_key_version")):
        if table not in tables:
            print(table + "=MISSING")
            continue
        versions = [row[0] for row in db.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL ORDER BY "{column}"')]
        print(f"{table}: required_key_versions={versions}")
    db.rollback()
PY
```

Even a nonempty cleanup table or successful admin action count is corroboration,
not historical completeness. Existing Gunicorn access logs intentionally omit
request paths; generic `method=DELETE` entries cannot identify account deletion.
Older route-specific HTTP/Nginx logs, when retained, can corroborate deletion
times without printing request rows, but are not an identity-complete ledger.
Historical tombstones would need an independently validated source retaining
the actual deletion identities/times. No such source has been demonstrated yet.

If complete historic identity/time coverage cannot be recovered, establish a
prospective boundary from a separately approved, verified, sealed SQLite
snapshot of the authoritative current DB. Prove the ledger's effective path
and fail-closed/fsynced deletion path, rule out other unledgered account-delete
entry points, and independently record the accepted baseline hash and the
start of continuous forward coverage. Do not use a filename, file mtime,
empty ledger or a fresh code deployment as the proof. If these prerequisites
cannot be established, there is no safe cutoff and the gate stays closed.

The trusted ledger declaration then has `history_confirmed_complete: false`
and this additional object, with real independently verified values:

```json
{
  "recovery_boundary": {
    "cutoff_at_utc": "<verified-UTC-boundary>",
    "baseline_database_sha256": "<approved-sealed-baseline-SHA-256>",
    "evidence_ref": "offline:<independent-approval-reference>",
    "forward_coverage_confirmed": true
  }
}
```

This fragment is a contract, NOT a runnable Production policy or an assertion
that approval already exists. Keep the ledger prefix anchor and approval
outside the VPS under independent custody; never reset the anchor to hide
truncation. A zero-length prefix attests only the start of prospective coverage,
not any earlier deletion. Continued operator assurance that no unlogged deletes
or ledger loss occurred is still necessary; file hashes cannot prove that alone.

Tool 1.2 / manifest 3 stores `account_restore_coverage`, including the cutoff,
approved baseline hash and evidence reference. Cutoff sets are labeled
`FROM_VERIFIED_CUTOFF_ONLY`, never historically complete. Set creation that
started before the boundary is rejected even if the snapshot completed later.
An empty ledger can never authorize historical coverage, even if the declaration
sets `history_confirmed_complete: true`. An empty future-only ledger is acceptable
only with the explicitly verified prospective boundary and baseline conditions.

`account_restore_safety` binds the generation ID, capture start/completion UTC,
ledger prefix anchor, coverage declaration, database SHA-256, Git SHA, schema
fingerprint, explicit SQLite `user_version`, manifest version and generation-gate
version `1`. Its status is
`SAFE_FOR_ACCOUNT_RESTORE` only for a successfully verified candidate under that
coverage, with reason `historical_coverage_attested` or
`after_verified_recovery_cutoff`. Failed sets record `UNSAFE_FOR_ACCOUNT_RESTORE`
and their safe error code; incomplete/unclassified candidates are `UNKNOWN`.
Verification rejects missing or inconsistent bindings. Eligibility still requires
the separate current trusted policy and latest ledger; a self-declared manifest
label and `COMPLETE`/integrity alone never authorize replay. There is no invented
global DB migration version: `schema_sha256` includes SQLite `user_version`.

The new `generation-gate` is read-only and requires a separate trusted policy
and the latest external ledger, not merely a policy inside the candidate set:

```text
python rove_recovery_set.py generation-gate --policy <trusted-ledger-policy.json> --set <candidate-set>
python rove_recovery_set.py generation-gate --policy <trusted-ledger-policy.json> --db <candidate-sealed-legacy.db>
```

An old manifest, missing proof or unavailable evidence classifies as `UNKNOWN`;
pre-cutoff, explicitly unsafe, mismatched or unapproved bare generations classify
as `UNSAFE_FOR_ACCOUNT_RESTORE`. BOTH statuses return a nonzero exit and block.
All bare legacy DB copies remain blocked irrespective of mtime, except the
exact independently approved baseline hash; even that exception requires a
sealed, integrity/FK-valid DB and ledger replay before any account use.
Append-only newer ledger records are accepted and MUST also be replayed.
Passing the generation gate does not copy/restore a DB, scrub files, validate
key possession, or authorize publication. Source/set bytes are unchanged.

### Canonical replay integration

`RUNBOOK.md` and `docs/DEPLOYMENT.md` identify the existing manual restore path;
`reapply_account_delete_tombstones.py` is its canonical executable replay step.
There is no separate restore-copy CLI. Both procedures require the read-only gate
BEFORE selecting/copying a generation into staging and BEFORE publication.
The existing replay entry point now enforces the same gate internally, before
importing application code or opening any writable SQLite connection:

```text
python reapply_account_delete_tombstones.py --db <separate-staging.db> --policy <trusted-ledger-policy.json> --recovery-set <approved-set>
python reapply_account_delete_tombstones.py --db <separate-staging.db> --policy <trusted-ledger-policy.json> --baseline <exact-approved-baseline.db>
```

These are documentation contracts, NOT commands to run on Production now.
`--check-only` executes only eligibility and staged-file checks, without importing
the app or replaying. Missing policy/source, unsafe/unknown status, a policy inside
the set, differing ledger or differing staged DB hash abort. Existing generations,
the source baseline, active configured DBs, sidecars and missing targets cannot be
mutated or silently created by replay. There is no `--force` or env bypass.
Only validated latest-ledger IDs are passed to the existing user-scoped deletion
function. Ledger changes during replay roll back the entire staging transaction.
The old bare `--db --ledger` invocation is blocked. Core account-delete/runtime
logic is unchanged; no service startup or schema migration is added.

Local tests enforce the executable replay entry point. Root can always bypass
application tooling by manually copying files or running arbitrary SQL; that is
not a supported recovery path. Mandatory gate deployment and operator acceptance
are still outstanding. The currently deployed old replay CLI has NOT been changed
by local implementation. Do not claim Production enforcement or a freeze yet.

No historical cutoff can be derived from the supplied Production facts. The 41
inventoried historical bare DB files remain `UNSAFE FOR ACCOUNT-RESTORE`. New SAFE
generations require a separately attested, sealed baseline and continuous durable
ledger coverage from a recorded prospective UTC boundary, an independently held
policy/anchor, then a manifest-bound snapshot started at or after that boundary.
The cutoff is never backdated from file times, DB integrity or empty-ledger metadata.

## Recovery key/config inventory and independent custody

This is a metadata inventory, not a list of values. Category records must point
to encrypted independently held revisions. Git templates and reference names
alone do not demonstrate possession or recoverability.

| Category | Recovery needed / loss | Rotation | Historical versions | Independent custody |
| --- | --- | --- | --- | --- |
| Auth/HMAC (`ROVE_APP_AUTH_SECRET` and applicable signing keys) | Required for compatible signatures/verifiers; cannot regenerate the same key | Possible with explicitly planned session/code invalidation | Retain versions needed by eligible generations, or document deliberate auth reset | Required |
| Provider vault (`ROVE_OPEN_BANKING_VAULT_KEY_V<N>`, active selector) | Required; lost key means ciphertext cannot be decrypted | New encryption key can be introduced, but cannot replace keys for old ciphertext | Every version used by eligible DB/IBAN ciphertext, not only the active key | Required |
| Mail/integration credentials (Brevo, market, AI, provider access) | Required where the restored product uses them; reissue depends on independent provider access | Usually reissuable through the provider | Revision/provenance required; retired operational tokens need not be kept indefinitely | Required for active credentials and provider-admin recovery |
| Push (`ROVE_VAPID_PRIVATE`, public companion, internal push secret) | Existing subscriptions/signing may depend on the original key | Rotation can require coordinated subscription renewal | Retain needed keypair/revisions or explicitly accept re-registration | Required |
| VKS webhook token / internal service secrets | Required for matching integration authentication | Coordinate both endpoints; do not silently rotate during recovery | Active revision plus dependency mapping | Required |
| Runtime/ENV/systemd/Nginx | Actual effective files and non-secret settings required; Git does not recreate arbitrary live overrides | Reviewed configuration revision, not credential rotation | Exact effective revision for each eligible release, including all four drop-ins and resolved Nginx target | Required; encrypted attachments may contain secrets |
| TLS private key/cert chain and Cockpit access | TLS may be reissued if DNS/ACME access survives; Cockpit password can be reset via an approved procedure | Possible, but requires independent control/access | Applicable revision; no indiscriminate historical private-key retention | Required for retained material or a proven reissue/reset route |
| Hosting/DNS/provider-admin/MFA recovery access | Needed to regain control when the VPS is unavailable | Operator-controlled | Current access/recovery instructions; never depend solely on the lost VPS | Separate from runtime credentials and vault-unlock recovery |

Do not store an auth key's value digest as its public revision. Assign opaque
version IDs. Never remove a provider key while any retained eligible ciphertext
needs it. When a transport key is revoked, document replacement access; when a
vault encryption key is lost, quarantine affected generations rather than
pretend a new key can decrypt them. Keep key versions aligned with the 30-day
eligible-backup policy and explicit recovery milestones, not arbitrary plaintext
ENV history. No key/config value is copied into this local recovery set.

### Minimal operator setup and reported completion

The original operator inventory reported no suitable custody. The subsequently
created vault and independent Runtime-Secrets check are operator-confirmed. The
model and requirements below remain the custody contract; secret values were not
received or checked by the collector. A minimal independent model is
an offline KeePassXC encrypted `Rove-Recovery.kdbx` on the Mac, with a second
encrypted copy on a removable medium stored at another physical location.
This is key/config custody only, NOT recovery-set offsite replication.
Official instructions: [Getting Started](https://keepassxc.org/docs/KeePassXC_GettingStarted).
KeePassXC supports encrypted entries and attachments. Do not use plaintext
CSV/XML/HTML exports, terminal output, chat, Git or ordinary recovery sets for
secret transfer. Opening attachments externally may extract plaintext temporary
files; avoid that workflow for secret-bearing attachments.

1. Operator installs KeePassXC on the Mac from its official download, checking
   the signed release. No software is installed on the VPS for this step.
2. Create `Rove-Recovery.kdbx` with a new long, unique passphrase and the current
   recommended default encryption. Do not reuse the VPS login password. Do not
   add a keyfile unless a separately held keyfile backup is explicitly planned.
3. Create groups `Auth`, `Provider Vault`, `Mail-Push-Integrations`, `Runtime`,
   and a distinct `Admin Access`. Keep vault-unlock/recovery instructions in a
   separate sealed record accessible without either the VPS or that same vault.
   Provider MFA recovery must not exist only inside the vault it unlocks.
4. STOP and confirm only that the encrypted vault exists. No key values, screen
   captures, exports or access codes are to be sent. A reviewed secret-safe
   import/attachment workflow must be prepared for the installed version and
   actual operator access before transferring Production material.
5. After that separately approved import, save exact effective ENV/config
   revisions and required key versions, source path/hash metadata and boundary
   approval. Copy ONLY the encrypted closed `.kdbx` to the independent medium.
6. With no VPS connection, unlock that second copy and privately check entry
   completeness, attachment hashes and necessary key versions. Report only
   category/version coverage, access success and verification time. Never print
   values. Empty vault creation alone is NOT recovery proof.

Loss scenario: recovery must work with the Mac/VPS unavailable using the second
encrypted copy plus separately held unlock/admin recovery instructions. Rotation
scenario: update encrypted entries and config revision first, verify the
independent copy, then retire only material no eligible generation depends on.
The collector deliberately leaves `external_custody_verified: false`; the
operator must supply actual independent-access evidence for the Production gate.

## Mandatory contents, selection and exclusions

| Content | Why / regeneration / sensitivity |
| --- | --- |
| Entire SQLite snapshot | Canonical server-persisted financial/auth/VKS data, finalized report snapshots, queues and maintenance state; sensitive and mandatory |
| Complete current ledger | Required to suppress deleted accounts from historical DB copies; personal deletion identifiers; mandatory |
| Active, unexpired referenced Web HTML | Preserve the existing URL/token/expiry and original document; personal report content; mandatory when referenced |
| Referenced shared `support.js` | The current Web template references this local public script; exact existing bytes required when referenced; no generated replacement |
| Retained recognized PDFs/gzip PDFs | Preserve existing document bytes rather than generate a different historical report; sensitive; mandatory if present and owned |
| Runtime/schema metadata | Exact code SHA, schema DDL/fingerprint, expected runtime, file permissions, config hashes/custody references; mandatory |
| Secret dependency inventory | Identifies needed keys and expected independent custody, but includes no keys; mandatory |

Report owners must exist in the DB snapshot. Ledger-deleted owners' artifacts
are excluded when known during initial selection. Unclassified report files,
unknown owners, missing required files, symlinks or changing files fail closed.
Only the two explicit report roots and direct `archive` child are inspected;
there is no server-wide file search or arbitrary extra-file copy option.
Local HTML/CSS resource references are checked. The existing `/reports/support.js`
dependency is collected once when referenced; missing or unclassified local
resources fail closed rather than producing an incomplete `COMPLETE` set.
External resources such as Google Fonts are not fetched or frozen; their
availability is an external presentation dependency, not canonical financial data.

Expired/superseded/orphan Web reports, local generated report HTML, the current
`latest_preview.html`, temporary renders, legacy public app-state JSON, live
WAL/SHM, manual backup trees, logs and virtualenvs are not copied. Finalized
report snapshots remain in SQLite. VKS case PDFs are regenerable from their DB
records. Browser-only Sachwerte/local drafts are NOT covered by a server set;
no new browser synchronization/export feature is introduced here.

## SQLite and cross-file consistency

The shared `create_verified_backup` uses SQLite's Backup API with a read-only
source connection and a new exclusive destination. It never overwrites an old
backup or creates a missing source. The destination alone is sealed into DELETE
journal mode so it is a standalone snapshot, including committed WAL data.
Both `integrity_check` and `foreign_key_check` must pass; connections are closed.
Never append live Production sidecars or use `cp` of the live DB as the snapshot.

The observed schema must match the pre-verified expected fingerprint. The exact
running collector/helper bytes must match the clean committed Git revision.
Detached Production HEAD is recorded honestly. Only the three known untracked
active `.rove-*.env` files are tolerated, and only when explicitly inventoried;
all other untracked, staged or tracked changes fail.

Referenced Web files are selected from the sealed DB, not from a new live query.
Missing/changing files fail rather than weakening the manifest. PDF bytes are
preserved as stable retained documents, not declared to be regenerated output
from a particular snapshot. This is not a distributed atomic snapshot: a file
removed by concurrent cleanup can make collection fail. It does not lock live
writers across a whole filesystem copy or mutate Financial Truth.

The ledger is captured AFTER the DB snapshot, checked against its trusted prefix,
then captured again after artifacts. Append-only growth is allowed; rewrites,
truncation and partial records fail. A later deletion can therefore be present
in the final ledger even when its artifact was already copied. Restore MUST
apply that ledger to the private DB and scrub corresponding files before any
publication/runtime. A later restore also requires any newer complete ledger;
a sealed set cannot know deletions that occurred after its capture cutoff.
The existing tombstone replay CLI mutates its target DB only and does NOT scrub
restored report files. This block does not run it or claim that gap is solved.

## Manifest and verification

`manifest.json` records UTC creation/snapshot/ledger/completion times; set ID;
tool/manifest version; Git SHA/branch/detached state and tool checksums; DB name,
size, hash and both SQLite results; ledger path/status/hash/anchor/attestation;
artifact paths/hashes/reasons/ownership/original expiry; expected runtime/schema
metadata hash; required secret identities/revisions/custody references; and
`COMPLETE` or `FAILED` status. No user rows or credential values are printed.

`verify` is read-only: checks exact file inventory, private permissions, hashes,
SQLite integrity/FKs, schema fingerprint and the anchored ledger. It imports
neither the API, renderer nor dotenv; starts no jobs and performs no network
calls. It also crosschecks the manifest's configuration records with the hashed
runtime metadata. It explicitly reports `ACCOUNT_RESTORE=NOT_AUTHORIZED`;
generation eligibility requires the separate trusted-policy gate above.
SHA-256 detects corruption but is NOT a signature against an attacker
who can replace the whole manifest. Authenticated encryption and protected
independent versions belong to the next block, not this implementation.

## Separate activation gate (NOT RUN)

### Local generation-gate acceptance

Python 3.12.14: 132/132 relevant tests passed, comprising 99 recovery/backup/gate
tests and 33 existing privacy/logging/SQLite-read-lifecycle tests. Syntax and
whitespace checks passed. Fixtures exercise safe synthetic replay, hard stops
for unsafe/unknown/missing proofs, empty or corrupt ledgers, missing/mismatched
anchors, pre-cutoff timestamps, manifest binding, source/target isolation,
no force option, latest-ledger replay and rollback on policy/ledger changes.
No real account restore, Production change, commit, push or deploy was performed.
Production read-only acceptance of the installed gate is pending operator output
and the separately approved release; local success is not a Production freeze.

Before any Production use, explicitly approve and verify the inventory, ledger
history/anchor, source file permissions, external custody, roots, exact release
SHA/schema compatibility, available disk space and interference with retention.
Check current foreign keys read-only: adding the FK gate can expose existing
violations and intentionally stop creation; do not auto-repair Production data.

After this code is separately reviewed/committed/deployed and the inventory is
verified, the collector interface is:

```bash
python rove_recovery_set.py create \
  --db /absolute/verified/source.db \
  --ledger /absolute/verified/account_delete_tombstones.jsonl \
  --inventory /absolute/verified/recovery-inventory.json \
  --repo /absolute/clean/committed/repository \
  --expected-git-sha <full-verified-release-sha> \
  --output-dir /absolute/private/recovery-output
python rove_recovery_set.py verify /absolute/private/recovery-output/recovery_set_<id>
```

These are interfaces, not a Production activation instruction. No systemd/timer
change is included. Keep the existing automatic backup process and all old
paths/copies. Do not replace it, change its rotation, or grant uploader access
before a separately approved successful real-set verification. Rollback of
future collection means disable only the new collector, retain the existing
backup job and keep all prior backups/sets.

Production compatibility is still NO-GO until a historically proven ledger or
approved enforced cutoff, complete effective config inventory, and independent
key/config possession are all demonstrated. Local tests are not Production
acceptance. Do not mark Block 2A frozen while any of these is missing.

Only AFTER that GO, next single block:
OFFSITE REPLICATION OF THE COMPLETE RECOVERY SET.
Do not claim Recovery FROZEN until independent retrieval, current ledger/key
coverage, isolated financial checks and measured RPO/RTO are demonstrated.
