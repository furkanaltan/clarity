# VKS Mail Operations And Launch Gates

Verify the live commit and service configuration before every operation. This
document describes operator gates and does not itself prove that production or
Brevo is configured.

## Defaults And Isolation

- `ROVE_VKS_EMAIL_ENABLED` defaults to `0`: no dispatch.
- `ROVE_VKS_MAIL_MODE` defaults to `test`, not public live sending.
- Test sending additionally requires exact `ROVE_VKS_TEST_USER_ID`,
  `ROVE_VKS_TEST_CONTRACT_ID`, `ROVE_VKS_TEST_RECIPIENT`.
- The owned contract name must start with `[VKS TEST] ` and represent an internal
  test only. Recipient, user and contract must match the configured allowlist.
- Use an explicitly authorized internal test account, never a customer account or
  provider recipient. Create the dedicated test contract through existing contract
  management; no real financial/customer data. Do not modify production by script
  just to manufacture a test case.
- Public sending requires BOTH mode `live` AND `ROVE_VKS_LIVE_APPROVED=1`, in
  addition to the send flag and existing transport configuration. This approval
  flag is an operator decision, not automatic provider verification.
- Existing `BREVO_API_KEY`, `ROVE_LOGIN_FROM_EMAIL`, `ROVE_LOGIN_FROM_NAME` are
  reused. Never print credentials, commit ENV files or install production packages
  as part of this feature.

## Brevo Delivery Webhook

- The endpoint is `POST /webhooks/brevo/vks`. It is unavailable unless
  `ROVE_VKS_BREVO_WEBHOOK_TOKEN` is configured; the endpoint accepts only a
  constant-time-checked `Authorization: Bearer` token. Do not reuse the Brevo API
  key as this callback token.
- Configure the Brevo transactional webhook with Bearer authentication using the
  same independently generated token. Subscribe only to sent/request, delivered,
  deferred, soft bounce, hard bounce, blocked, invalid and error events; do not
  enable batching for this endpoint.
- Events map only through an exact stored Brevo message ID to one outbound attempt.
  Recipient mismatch, duplicate/ambiguous IDs or a mismatching `X-Mailin-custom`
  attempt marker do not mutate a case. Unmatched authenticated events are logged
  using a one-way identifier hash and acknowledged without case mutation.
- The event timestamp uses Brevo `ts_event`, then `ts`; it never substitutes
  webhook receipt time. Retries are idempotent. Delivered means only that Brevo
  reported delivery, never that the provider confirmed cancellation.
- Deferred/soft-bounce events do not resend. Hard bounce, blocked, invalid and
  error move eligible cases to manual review with retries disabled. No webhook
  event starts a send, retry, cancellation or termination confirmation.

## Required Before Any Real Dispatch

1. Operator confirms in Brevo that the configured sender/domain is verified and
   authorized. Review external provider/recipient retention, deletion limitations
   and abuse policy. Local account deletion cannot recall an in-flight email.
2. Check verified user Reply-To. Sender remains the registered platform identity;
   never impersonate the user's email as From.
3. Verify the DB backup, integrity/FKs, controlled V1/V2/V3 schema migration,
   authenticated PDF runtime and existing daily maintenance timer. No blind code
   rollback against newer states; restore code/DB consistently if necessary.
4. Confirm explicit approval for ONE internal E2E run, with the exact dedicated
   account, test contract and operator-controlled mailbox. No customer/provider
   destination is allowed in test mode. Set the mail configuration only through
   the existing operator-managed service ENV workflow.
5. In the authenticated app, review and explicitly confirm the exact test text,
   then authorize sending. Check the single stored provider receipt. Double-click
   or reopen must not send again.
6. Verify inbox From, Reply-To and text, then reply from the controlled mailbox.
   Check delivery through the authenticated provider events lookup; acceptance is
   not delivery. Manually document the actual received reply and explicitly
   confirm its result. No AI parsing and no simulated provider-authentication claim.
7. Check optional/no enddate, contract metadata only, private PDF and account
   isolation. Reminder timing is tested locally with fixtures, not by backdating
   production records or sending extra messages.
8. Return the send flag to OFF after the approved internal run. Record the result
   and approve public activation separately; passing local tests never enables it.

## Failure And Abuse Controls

Atomic case/message locks are committed before network I/O. Attempts are limited
to 3 per case and 10 per user per rolling 24 hours. No automatic retries. A known
rejection allows only explicit, audited retry. Unknown timeout/5xx/409/receipt
failure or process crash stays locked: never reset the case blindly. Support must
reconcile provider receipt/correlation before any separately approved recovery.

Responses are untrusted, user-documented text (20 per case, 6,000 chars each).
Only explicit result confirmation can update contract cancellation metadata.
No automatic financial mutation or contract deletion occurs.

## Reminder And Case File Lifecycle

Daily maintenance marks accepted, unanswered cases due after 14 days and for
manual review after 28 days. These are product intervals, not legal deadlines.
Updates are bounded to 100 per run; due information is also visible when an owned
case is opened before maintenance. Monitor the aggregate `vks_reminders_updated`
count and backlog before scaling activation. No reminder mail/push spam exists.

Follow-up text is only prepared, not sent by Rov.E. If the user sends it through
their own mailbox, Rov.E does not invent a transport receipt or mark it sent.
Automatic follow-up dispatch is intentionally outside this version.

Private JSON/PDF includes the original notice, confirmation, attempts,
acceptance/delivery evidence, documented replies, prepared follow-ups, enddate
and timeline. PDF uses existing reportlab/fonts and produces no public or
server-persisted artifact. Browser/user downloads are user-owned copies, not
server files that local account deletion can recall. Follow-ups/messages remain
in the owned contract/account lifecycle and are removed by existing deletion and
restore-tombstone cleanup.

## Final Gate Matrix

Local gates: core, state machine, dispatch locking, proof separation, manual
response, confirmation, reminders, failure states, private PDF, privacy/user
isolation, contract metadata and UI/copy must PASS.

Operational gates: sender/domain, real Reply-To, real internal E2E, external mail
retention and production maintenance/migration/runtime must be evidenced by the
operator before public activation. Until then: public sending NO-GO; no claim of
an operationally FROZEN/launch-ready mail service.
