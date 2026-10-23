# Rov.E Bot and Telegram Legacy Separation Audit

Stand: 31.08.2026. This is a read-only architecture inventory. It does not
authorize stopping `clarity-bot.service`, changing production, migrating data or
retiring a feature.

The inventory through Wave 7 is the pre-cleanup baseline. Wave 8 records a
local-only retirement below; production still runs the pre-Wave-8 bot until a
separate deployment is explicitly approved.

## Audit evidence and limits

- Local source inspected: `bot.py`, `rove_app_api.py`, `rove_app_state.py`,
  `rove_score.py`, `rove_expense_domain.py`, `rove_report_worker.py`,
  `rove_monthly_reminders.py`, `rove_tracking_reminders.py` and deployment units.
- Production service metadata was read through `systemctl show` only.
- The read-only SSH account cannot read `/root/clarity/clarity.db` and returned no
  usable `clarity-bot` journal entries. User activity and recent bot writes are
  therefore `UNKNOWN`; zero visible events is not evidence of zero usage.
- No personal IDs, message contents, secrets or production DB values were read
  into this document.

## Entrypoint and runtime

| Item | Current implementation |
|---|---|
| Entrypoint | `bot.py` guarded by `if __name__ == "__main__"` |
| Telegram init | `telebot.TeleBot(TELEGRAM_TOKEN)`, menu setup, webhook deletion and long polling |
| Scheduler init | One in-process APScheduler `BackgroundScheduler` in Europe/Berlin |
| Background jobs | `send_evening_recaps` daily at 20:30; app reports use separate systemd workers |
| DB model | Repeated short SQLite connections, WAL and foreign keys enabled; many functions commit directly |
| External services | Telegram Bot API, OpenAI chat, Twelve Data quotes, report renderer and web push through report code |
| Environment | `TELEGRAM_TOKEN`, `OPENAI_API_KEY`, `CLARITY_DB_NAME`, `ADMIN_USER_IDS`/`ADMIN_USER_ID`, `CLARITY_USER_APPROVAL`, `CLARITY_BOT_LOCK_FILE`, `TWELVE_DATA_API_KEY`, report worker limits |
| Process lock | `fcntl` lock at `CLARITY_BOT_LOCK_FILE` prevents duplicate polling |

Production service facts:

- Unit: `/etc/systemd/system/clarity-bot.service`
- State: active/running and enabled
- ExecStart: `/usr/bin/python3 /root/clarity/bot.py`
- Working directory: `/root/clarity`
- Run user: systemd default, therefore root
- Environment file: optional `/root/clarity/.rove-leeway.env`; `bot.py` also loads
  `/root/clarity/.env` through `python-dotenv`
- Restart: always, after five seconds
- Systemd links: wanted by `multi-user.target`; no Rov.E unit requires or triggers
  the bot directly

## Telegram handler inventory

There are three registered TeleBot handlers: one callback catch-all, one command
handler with 33 command names, and one text catch-all.

Legend: R/W are DB reads and writes. App equivalent describes product coverage,
not exact behavioral identity.

| Trigger | Purpose | R | W | AI | Report | Admin | App equivalent |
|---|---|---:|---:|---:|---:|---:|---|
| `/start` | Telegram onboarding or current budget summary | YES | YES | NO | NO | NO | PARTIAL |
| `/help` | Telegram help copy | NO | NO | NO | NO | NO | PARTIAL |
| `/score` | Score, history and RP presentation | YES | YES | NO | NO | NO | YES for score, PARTIAL for RP |
| `/scoreinfo` | Score explanation | NO | NO | NO | NO | NO | YES |
| `/badges` | Earned Telegram badges | YES | NO | NO | NO | NO | PARTIAL |
| `/verfeinern` | Legacy profile refinement flow | YES | YES | NO | NO | NO | PARTIAL |
| `/portfolio` | Telegram portfolio setup and tracking | YES | YES | NO | NO | NO | YES, different implementation |
| `/undo` | Delete latest expense and reverse cash movement | YES | YES | NO | NO | NO | YES |
| `/editlast` | Change latest expense amount and cash movement | YES | YES | NO | NO | NO | PARTIAL |
| `/id` | Display Telegram actor/chat IDs | NO | NO | NO | NO | NO | NO |
| `/settings` | Restart legacy Telegram profile setup | YES | YES | NO | NO | NO | PARTIAL |
| `/goal` | Goal status and forecast | YES | NO | NO | NO | NO | YES |
| `/status` | Remaining monthly budget | YES | NO | NO | NO | NO | YES |
| `/stats` | Current expense categories | YES | NO | NO | NO | NO | YES |
| `/reset` | Start destructive Telegram reset confirmation | NO | memory only | NO | NO | NO | YES through authenticated account deletion |
| `/reset_confirm` | Delete legacy user-domain rows | YES | YES | NO | NO | NO | PARTIAL; semantics differ |
| `/investiert` | Confirm savings, mutate wealth, award RP | YES | YES | NO | NO | NO | YES through monthly check-in, not identical |
| `/testreport` | Generate and send an unrestricted test report | YES | possible report writes | NO | YES | YES | PARTIAL |
| `/admin` | List Telegram admin commands | NO | NO | NO | NO | YES | PARTIAL |
| `/pending` | List pending legacy access requests | YES | NO | NO | NO | YES | YES |
| `/approve` | Approve legacy access | YES | YES | NO | NO | YES | YES |
| `/revoke` | Revoke legacy access | YES | YES | NO | NO | YES | YES |
| `/adminusers` | Legacy user/activity overview | YES | NO | NO | NO | YES | YES/PARTIAL |
| `/health` | Bot, DB, backup and report summary | YES | NO | NO | NO | YES | YES/PARTIAL |
| `/reportjobs` | Report queue inspection | YES | NO | NO | YES | YES | YES/PARTIAL |
| `/backupnow` | Create a manual SQLite backup file | YES | backup file | NO | NO | YES | PARTIAL; timer replaces routine need |
| `/nudge_inactive` | Preview/send Telegram beta nudges | YES | NO | NO | NO | YES | NO |
| `/testrecap` | Send evening recap to the admin | YES | NO | NO | NO | YES | NO |
| `/ruhe` | Toggle Telegram recap preference in `user_badges` | YES | YES | NO | NO | NO | NO |
| `/announce_rename` | Preview/send bulk rename announcement | YES | NO | NO | NO | YES | NO |
| `/announce_app_migration` | Preview/send app migration announcement | YES | NO | NO | NO | YES | NO |
| `/appwechsel` | Alias for app migration announcement | YES | NO | NO | NO | YES | NO |
| `/app` | Show canonical app URL | NO | NO | NO | NO | NO | not applicable |
| callback `admin_approve:*` | Approve a requested Telegram user | YES | YES | NO | NO | YES | YES |
| callback `admin_revoke:*` | Revoke a requested Telegram user | YES | YES | NO | NO | YES | YES |
| callback `confirm_reset` | Delete legacy user-domain rows | YES | YES | NO | NO | NO | PARTIAL |
| callback `cancel_reset` | Cancel in-memory reset | NO | memory only | NO | NO | NO | YES |
| callback `start_refine` | Start profile refinement | YES | YES | NO | NO | NO | PARTIAL |
| callback `skip_refine` | Skip profile refinement | NO | NO | NO | NO | NO | PARTIAL |
| catch-all text | Approval, onboarding, profile/budget/investment edits, expense parsing, finance Q&A and AI fallback | YES | YES | YES | NO | NO | PARTIAL |

The catch-all also handles pending edit and portfolio actions, free-form expense
deletion, deterministic merchant/category parsing, multi-expense input, investment
classification, profile corrections and AI-assisted expense extraction.

## Direct database write paths

The source contains 37 functions with direct SQL writes, including schema setup,
plus Telegram calls into the shared `create_expense_for_user` write service.
`SAFE TO RETIRE` is deliberately conservative.

| Function | Tables | Type | User scoped | API equivalent | Bot exclusive | Safe to retire now |
|---|---|---|---:|---|---:|---:|
| `init_db` | users, expenses, badges, snapshots, investments, reports, access, budgets, rules, holdings | CREATE/ALTER/index/bootstrap | NO | PARTIAL migrations | YES | NO |
| `save_user_category_rule` | `user_category_rules` | UPSERT | YES | NO | YES | NO |
| `find_user_category_rule` | `user_category_rules` | UPDATE usage counter | YES | NO | YES | NO |
| `update_latest_expense_for_rule` | `expenses` | UPDATE | YES | PARTIAL | NO | NO |
| `get_or_create_user` | `users` | INSERT | YES | YES | NO | NO |
| `reset_user_data` | ten legacy user tables | DELETE | YES | PARTIAL, different account-delete scope | NO | NO |
| `update_user_field` | `users` | UPDATE | YES | PARTIAL | NO | NO |
| `save_investment_event` | `investment_events` | INSERT | YES | YES/PARTIAL | NO | NO |
| `replace_onboarding_investment_start` | `investment_events` | DELETE/INSERT | YES | PARTIAL | YES | NO |
| `save_portfolio_snapshot` | `portfolio_snapshots` | INSERT | YES | YES/PARTIAL | NO | NO |
| `replace_onboarding_portfolio_snapshots` | `portfolio_snapshots` | DELETE/INSERT | YES | PARTIAL | YES | NO |
| `add_cp` | `users` | UPDATE | YES | PARTIAL | YES | NO |
| `maybe_delete_logged_expense` | `expenses` | DELETE | YES | YES | NO | NO |
| `ensure_access_record` | `user_access` | INSERT/UPDATE | YES | PARTIAL | NO | NO |
| `approve_user_access` | `user_access` | UPSERT | YES | YES | NO | NO |
| `revoke_user_access` | `user_access` | UPSERT | YES | YES | NO | NO |
| `remember_budget_marker` | `user_badges` | INSERT | YES | PARTIAL | YES | NO |
| `save_category_budget` | `category_budgets` | UPSERT | YES | YES | NO | NO |
| `delete_category_budgets` | `category_budgets` | DELETE | YES | YES | NO | NO |
| `remember_monthly_moment` | `user_badges` | INSERT | YES | NO | YES | NO |
| `record_score_history_if_needed` | `score_history` | INSERT | YES | PARTIAL | NO | NO |
| `award_badge` | `user_badges` | INSERT | YES | PARTIAL | YES | NO |
| `handle_month_transition` | `monthly_snapshots`, users and badges via helpers | UPSERT/UPDATE | YES | PARTIAL month-close replacement | NO | NO |
| `save_portfolio_holding` | `portfolio_holdings` | UPSERT | YES | YES | NO | NO |
| `save_portfolio_total_invested` | `portfolio_holdings` | UPDATE | YES | YES | NO | NO |
| `maybe_delete_portfolio_holding` | `portfolio_holdings` | DELETE | YES | YES | NO | NO |
| `update_expense_amount` | `expenses` | UPDATE | YES | PARTIAL | NO | NO |
| `_set_app_account_amount` | `app_account_balances`, `users` | UPSERT/UPDATE | YES | shared app domain exists | NO | NO |
| `reverse_app_paid_expense` | `app_cash_movements`, balances | DELETE/UPDATE | YES | YES | NO | NO |
| `sync_app_paid_expense_amount` | `app_cash_movements`, balances | UPDATE | YES | PARTIAL | NO | NO |
| `handle_commands` direct branches | `expenses`, `user_badges` | DELETE/INSERT | YES | YES/PARTIAL | NO | NO |
| `create_monthly_report_jobs` | `report_jobs` | INSERT | YES | YES, worker | NO | NO |
| `claim_due_report_jobs` | `report_jobs` | UPDATE | YES | YES, worker | NO | NO |
| `mark_report_job_sent` | `report_jobs` | UPDATE | YES | YES, worker | NO | NO |
| `mark_report_job_failed` | `report_jobs` | UPDATE | YES | YES, worker | NO | NO |
| `mark_report_job_skipped` | `report_jobs` | UPDATE | YES | YES, worker | NO | NO |
| `toggle_recap_muted` | `user_badges` | INSERT/DELETE | YES | NO | YES | NO |
| catch-all via `create_expense_for_user` | expenses, cash movements and balances | shared transactional INSERT/UPDATE | YES | YES, same service | NO | NO |

Indirect write orchestration also occurs in `apply_investment_change`,
`handle_daily_activity`, budget setup, onboarding, pending-action handling and
badge checks. These call the direct functions above and are not separate SQL
owners.

## Important database read domains

| Domain | Bot use | Canonical counterpart | Drift risk |
|---|---|---|---|
| Users and onboarding | identity, profile, steps, savings and activity | API account/profile/state | HIGH |
| Expenses and merchants | entry, edit, delete, category stats and AI context | shared expense domain plus API | MEDIUM |
| Category budgets and learned rules | setup, status and learned aliases | API budgets; no app rule manager | HIGH |
| Investments and portfolio | events, holdings, totals and snapshots | API/state portfolio and monthly plan | HIGH |
| Cash accounts and movements | reverse/sync Telegram expense edits | financial account modules/API | HIGH |
| Goals | goal amount, current amount and monthly rate | API/state goals | MEDIUM |
| Properties and contracts | net-worth and finance answers | API/state properties/contracts | MEDIUM |
| Score, RP and badges | score output, history, streaks and rewards | `rove_score` plus app state; RP/badges partial | HIGH |
| Monthly snapshots | legacy first-contact month transition | app month-close and report state | HIGH |
| Reports | queue, links, status and dispatch | report worker/enqueue/maintenance | MEDIUM |
| Access and app accounts | Telegram approval and migration targeting | admin API/cockpit | MEDIUM |

## Scheduler inventory

| Job | Schedule | Purpose | DB reads | DB writes | External effect | Systemd overlap | Replacement |
|---|---|---|---|---|---|---|---|
| `send_evening_recaps` | daily 20:30 Europe/Berlin | Telegram summary for users who tracked today | users, access, expenses, recap badge | none during send | Telegram messages | NO exact overlap; tracking reminder is a different product | PARTIAL, app push infrastructure exists but recap semantics do not |

Report queue functions remain in `bot.py`, but `setup_monthly_report_scheduler`
does not schedule them. Production app report enqueue, processing and maintenance
are owned by systemd timers and `rove_report_worker.py`.

## Duplicated domain logic

| Domain | Bot implementation | App/API implementation | Identical | Drift risk |
|---|---|---|---:|---|
| Expense creation | Telegram parser plus shared `rove_expense_domain` writer | API calls the same writer | NO at routing layer | MEDIUM |
| Expense edit/delete | bot SQL and cash-sync helpers | API expense endpoints | NO | HIGH |
| Profile/onboarding | state machine in `bot.py` | app authentication/profile state | NO | HIGH |
| Budget | bot formulas, markers and CRUD | API/state budget truth and CRUD | NO | HIGH |
| Portfolio/investments | bot events, snapshots and holdings | richer API/state crypto, ETF and holdings paths | NO | HIGH |
| Score | bot presentation/history/RP wrapper | shared `rove_score` and app state | PARTIAL | MEDIUM |
| Goals | bot forecast/read path | API/state goal CRUD and progress | NO | MEDIUM |
| Monthly close | first Telegram contact creates snapshot and rewards | explicit due-only app month close | NO | HIGH |
| Access administration | Telegram approvals and lists | admin API and app cockpit | PARTIAL | MEDIUM |
| Reports | dormant bot queue copy and Telegram delivery | systemd worker and app report archive | PARTIAL | HIGH |
| Notifications | Telegram recap/nudges/announcements | app push reminders and feature announcements | NO | HIGH |
| AI assistant | free-form bot OpenAI prompt and booking | app AI endpoint and deterministic routing | NO | HIGH |

## Exclusive bot functionality

| Function | Category | Current owner | Replacement | Status | Retirement blocker |
|---|---|---|---|---|---|
| Telegram long polling, menu and replies | CRITICAL while Telegram is supported | bot | none needed after channel retirement | KEEP | usage unknown |
| Telegram onboarding and refinement | LEGACY but data-writing | bot | app onboarding/profile | MIGRATE | semantic/data parity not proved |
| Free-form Telegram expense and investment entry | USEFUL | bot plus shared expense writer | app quick capture and portfolio UI | VERIFY | user activity unknown; investment semantics differ |
| Telegram-only report delivery | CRITICAL for non-app users | bot/report engine | verified app account and push/report archive | MIGRATE | Telegram-only users may exist |
| Evening recap and `/ruhe` | USEFUL | bot scheduler | no exact app replacement | REPLACE | product decision and preference migration |
| Access-request admin notifications | ADMIN | bot | app cockpit | VERIFY | notification workflow differs |
| Bulk beta nudge/rename/migration messages | ADMIN | bot | no direct app equivalent | RETIRE_LATER | current operational use unknown |
| `/testreport`, `/testrecap` | ADMIN | bot | worker tests/operational tooling | REPLACE | safe admin tooling needed |
| `/backupnow` | ADMIN | bot | daily backup timer | VERIFY | manual recovery procedure must be accepted |
| Telegram RP/streak/badge presentation | LEGACY/USEFUL | bot | partial app score UI | MIGRATE | behavior and history parity missing |

## Report dependency decision

`PARTIAL`:

- Verified app accounts are enqueued and processed without `bot.py` by the
  systemd report worker. Their PDF/archive access and push path are app-native.
- `report_engine.send_report_to_user(..., bot=None)` deliberately skips users
  without a verified app account.
- Telegram-only users still require a bot object for web-report link and PDF
  delivery. Therefore report generation can run without the bot, but complete
  delivery for every legacy user cannot yet be guaranteed.

## Admin dependency decision

`PARTIAL`:

- App cockpit/API covers overview, invitations, access approval/revocation,
  health, backup freshness and report status.
- Telegram-only bulk nudges/announcements, test recap/report and manual backup
  command UX have no exact cockpit replacement.
- Current use of those commands is `UNKNOWN` because production activity logs
  were unavailable to the read-only account.

## User activity evidence

| Evidence | Result |
|---|---|
| Last observed bot user activity | UNKNOWN |
| Last observed bot write | UNKNOWN |
| Active Telegram user count, recent | UNKNOWN |
| Confidence | LOW |

The journal query exposed no usable entries and the DB file was not readable.
This must not be interpreted as evidence that the bot is unused.

## Target architecture

```text
Telegram transport (temporary)
        |
        v
thin adapter: parse identity, call shared services, format response
        |
        +--> shared domain services / API modules
        +--> dedicated systemd workers and timers
        +--> canonical app authentication and admin model
```

The adapter must not own schemas, finance formulas, direct SQL, report queues,
score rules or scheduler policy. If Telegram is ultimately removed, the adapter
and its service can be archived only after migration and activity gates pass.

## Recommended migration order

1. Add privacy-safe activity telemetry or obtain an approved read-only usage
   extract; establish the last active Telegram users and admin command usage.
2. Identify every approved Telegram user without a verified app account and
   complete migration before changing report delivery.
3. Freeze new domain behavior in `bot.py`; route expense, investment, budget,
   profile and access writes through shared services with parity tests.
4. Remove the dormant report queue copy from the bot only after proving the
   worker covers all eligible users and delivery channels.
5. Decide whether evening recap is retained; if yes, implement app push and a
   canonical preference before removing `/ruhe`.
6. Replace or formally retire Telegram-only admin bulk messaging and test tools;
   rely on the backup timer only after documenting manual recovery.
7. Convert Telegram to a thin adapter or read-only transition mode and observe
   it for an agreed period without finance writes.
8. Disable the service only through a separate production gate with backup,
   rollback, report verification and explicit owner approval.
9. Archive bot code only after the disabled observation window is complete.

## Decommission decision

SAFE TO DECOMMISSION NOW: **NO**

Blockers:

1. Recent Telegram user and write activity is unknown.
2. Telegram-only users may still depend on bot-delivered reports.
3. Direct finance, profile, score, badge and admin writes remain in the bot.
4. Evening recap and several Telegram admin workflows lack exact replacements.
5. Duplicate business rules are not behaviorally identical and carry high drift
   risk.

SAFE TO START SEPARATION WORK: **YES**, provided each step remains tested,
reversible and independent from production shutdown.

## Wave 7 retirement classification

Product decision: Telegram reminders, daily Telegram nudges and the Telegram
evening recap are not part of the future Rov.E app. This decision permits a
separate local removal changeset later; it does not authorize a production
service or timer change.

| Function area | Current purpose | Category | App/API equivalent | DB writes | Dependencies and side effects | Safe to remove now | Reason |
|---|---|---|---|---|---|---:|---|
| Telegram polling, menu and reply adapter | Telegram transport | KEEP_TEMPORARILY | NO, transport-specific | indirect | token, TeleBot, live Telegram users | NO | channel activity remains UNKNOWN |
| Telegram onboarding/refinement | create and edit legacy profile | MIGRATE | PARTIAL | users, investments, snapshots | Telegram state machine and shared DB | NO | behavior parity and user migration are unproved |
| Free-form expense entry | parse and book Telegram expenses | KEEP_TEMPORARILY | YES/PARTIAL | shared expense writer | Telegram messages, category parser, AI fallback | NO | activity is unknown; writer is shared but routing is not |
| Free-form investment/portfolio entry | mutate wealth and holdings | MIGRATE | YES/PARTIAL | investments, snapshots, holdings | score, badges, quote provider | NO | semantics differ from app portfolio flows |
| Score/RP/badges/streak | reward and present progress | MIGRATE | PARTIAL | users, badges, score history | expense activity and legacy RP rules | NO | app score exists, RP/badge parity does not |
| Telegram-only report delivery | send links and PDFs to non-app users | KEEP_TEMPORARILY | PARTIAL | report artifacts/status through engine | report engine, Telegram, legacy users | NO | Telegram-only recipients may still exist |
| Bot-local report queue copy | enqueue/claim/update report jobs | RETIRE | YES | report jobs | no active bot scheduler call; systemd worker owns runtime | NO | remove only in a tested local changeset with worker parity proof |
| Evening recap scheduler and `/ruhe` | daily Telegram recap and preference | RETIRE | NO exact equivalent and none required by product decision | recap preference only | APScheduler, expenses, Telegram send | NO | technically separable, but code and runtime removal need separate gates |
| Access approval/request transport | legacy Telegram access workflow | MIGRATE | YES/PARTIAL | user access | admin API/cockpit and Telegram notifications | NO | notification and identity behavior still differ |
| Bulk nudge/rename/migration commands | manual Telegram broadcasts | RETIRE | NO | none | admin command and Telegram recipients | NO | operational use remains UNKNOWN |
| Admin overview/health/report jobs | Telegram operations UI | MIGRATE | YES/PARTIAL | none | admin API/cockpit and report worker | NO | confirm cockpit coverage and operator acceptance first |
| `/backupnow` | manual DB backup from Telegram | VERIFY | PARTIAL | backup file, not DB rows | filesystem, SQLite backup API | UNKNOWN | automated timer exists; manual usage is unknown |
| `/testreport` and `/testrecap` | Telegram admin diagnostics | VERIFY | PARTIAL/NO | possible report artifacts | report engine, Telegram | UNKNOWN | replace with safe operational tests before retirement |
| `init_db` schema bootstrap in bot | create/alter shared schema on bot start | MIGRATE | PARTIAL | schema | bot startup and shared production DB | NO | high-risk implicit migration owner |

Summary:

- RETIRE: evening recap/reminder path, dormant bot report queue copy, historical
  bulk Telegram messaging after usage verification.
- MIGRATE: onboarding, investment/portfolio, RP/badges, access administration,
  admin diagnostics and schema ownership.
- KEEP_TEMPORARILY: Telegram transport, free-form expense adapter and legacy
  report delivery until activity and account migration evidence exists.
- VERIFY/UNKNOWN: manual backup/test commands, recent Telegram activity, recent
  bot writes and Telegram-only report recipients.

## Telegram reminder retirement assessment

| Reminder path | Scheduler owner | Calls | DB writes | Report dependency | Admin dependency | App user dependency | Telegram-only | Technically safe to retire |
|---|---|---|---|---:|---:|---:|---:|---|
| Evening recap 20:30 | bot APScheduler | candidate query, recap builder, `bot.send_message` | NO during send | NO | NO | NO | YES | YES, in a separate code changeset |
| `/ruhe` recap preference | command, no scheduler | `toggle_recap_muted` | YES, `user_badges` | NO | NO | NO | YES | YES together with recap path |
| `/testrecap` | manual admin command | recap builder and Telegram send | NO | NO | YES | NO | YES | YES together with recap path |
| `/nudge_inactive` | manual admin command | candidate query and Telegram sends | NO | NO | YES | NO | YES | PARTIAL; usage must be verified |
| Rename/app-migration broadcasts | manual admin commands | recipient queries and Telegram sends | NO | NO | YES | NO | YES | PARTIAL; usage/migration state unknown |
| App tracking reminder | systemd timer | `rove_tracking_reminders.py`, web push | delivery-log writes | NO | NO | YES | NO | NO; outside Telegram retirement |
| App monthly reminder | systemd timer | `rove_monthly_reminders.py`, web push | delivery-log writes | NO | NO | YES | NO | NO; outside Telegram retirement |

No additional automatic daily Telegram tracking reminder was found in
`bot.py`. The only active in-process scheduled Telegram notification is the
20:30 evening recap.

## Prioritization of the 37 direct write functions

Categories:

- A: replacement already exists.
- B: suitable for extraction into an existing/shared service.
- C: bot-exclusive and still required while Telegram remains supported.
- D: likely obsolete because an independent owner exists or the product retired
  the behavior.
- E: high-risk or unclear and requires parity/evidence first.

Counts: **A=10, B=7, C=7, D=6, E=7**.

| Category | Functions |
|---|---|
| A (10) | `get_or_create_user`, `maybe_delete_logged_expense`, `approve_user_access`, `revoke_user_access`, `save_category_budget`, `delete_category_budgets`, `save_portfolio_holding`, `save_portfolio_total_invested`, `maybe_delete_portfolio_holding`, `reverse_app_paid_expense` |
| B (7) | `update_latest_expense_for_rule`, `update_user_field`, `save_investment_event`, `save_portfolio_snapshot`, `update_expense_amount`, `_set_app_account_amount`, `sync_app_paid_expense_amount` |
| C (7) | `save_user_category_rule`, `find_user_category_rule`, `add_cp`, `ensure_access_record`, `remember_budget_marker`, `remember_monthly_moment`, `award_badge` |
| D (6) | `create_monthly_report_jobs`, `claim_due_report_jobs`, `mark_report_job_sent`, `mark_report_job_failed`, `mark_report_job_skipped`, `toggle_recap_muted` |
| E (7) | `init_db`, `reset_user_data`, `replace_onboarding_investment_start`, `replace_onboarding_portfolio_snapshots`, `record_score_history_if_needed`, `handle_month_transition`, direct SQL branches in `handle_commands` |

A does not mean immediate deletion: Telegram call sites still need a shared
service boundary. D is the first retirement pool, but every removal remains a
separate tested change. The delegated `create_expense_for_user` path is not part
of the 37 because its SQL owner is already the shared expense module.

## Future owner for duplicated domains

| Domain | Bot owner | App/API owner | Preferred future owner | Behavioral difference | Drift risk | Extraction risk |
|---|---|---|---|---|---|---|
| Expense creation | Telegram parser and orchestration | shared expense domain and API | `rove_expense_domain` plus transport adapters | parsing/reward flow differs | MEDIUM | LOW |
| Expense edit/delete | bot SQL/cash helpers | API expense endpoints | shared expense domain | amount-edit and reversal semantics differ | HIGH | MEDIUM |
| Profile/onboarding | bot state machine | app API/state | app API/domain service | steps, identity and reset differ | HIGH | HIGH |
| Budget | bot formulas/CRUD | API/state budget truth | app budget domain/API | formulas and markers differ | HIGH | MEDIUM |
| Portfolio/investments | bot events/snapshots/holdings | API/state portfolio | portfolio domain modules/API | asset identity and month semantics differ | HIGH | HIGH |
| Score/RP | bot wrapper and writes | `rove_score` and app state | `rove_score` plus one history service | RP/badge side effects differ | MEDIUM | MEDIUM |
| Goals | bot read/forecast | API/state goal CRUD | app goal domain/API | primary-goal fallback differs | MEDIUM | LOW |
| Monthly close | first Telegram contact | due-only app month close | app monthly-plan domain | timing, confirmation and rewards differ | HIGH | HIGH |
| Access administration | Telegram access functions | admin API/cockpit | shared access service called by API/adapters | status and notification behavior differ | MEDIUM | MEDIUM |
| Reports | bot queue copy/delivery | report worker and app archive | `rove_report_worker` plus delivery adapters | legacy Telegram delivery differs | HIGH | MEDIUM |
| Notifications | recap/nudges/broadcasts | app web-push/announcements | app workers; no Telegram reminder owner | channel and preference model differ | HIGH | LOW for recap retirement, MEDIUM otherwise |
| AI assistant | bot prompt/booking | app AI endpoint/router | shared intent/domain service or app API | prompts, routing and booking differ | HIGH | HIGH |

## First separation candidates

### 1. Telegram evening recap retirement

- Why first: explicit product retirement decision; one isolated scheduler job;
  no report, auth or app-user dependency.
- Files affected later: `bot.py` and focused bot tests only.
- Tests: prove scheduler no longer registers recap, `/ruhe` and `/testrecap`
  behavior is intentionally removed, report worker remains unaffected, full suite.
- Rollback: restore the single local commit and restart only during a separately
  approved production gate.
- Risk: LOW locally, runtime change still requires its own gate.

### 2. Remove unreachable bot-local report maintenance helpers

- Candidate: `cleanup_expired_web_reports` and `archive_old_pdf_reports`, which
  have definitions but no bot call sites.
- Why first: runtime ownership already sits in `rove_report_worker.py`; removing
  unreachable helpers does not change the scheduler.
- Files affected later: `bot.py`, bot/report separation tests.
- Tests: static no-call assertion, report worker tests and full suite.
- Rollback: revert one local commit.
- Risk: LOW, subject to a final call-graph check.

### 3. Extract access approve/revoke writes

- Why first: app API and Telegram currently implement the same access domain and
  the cockpit already exposes both operations.
- Files affected later: a shared access service, `bot.py`, `rove_app_api.py` and
  focused auth/admin tests.
- Tests: approve/revoke parity, session revocation, Telegram callback isolation,
  admin authorization and full suite.
- Rollback: keep existing wrappers and revert the extraction commit.
- Risk: MEDIUM because auth/session side effects must remain identical.

No product code change is proposed inside Wave 7. Candidate 1 should be the
first separately approved implementation; candidates 2 and 3 must not be
bundled with it.

## Reminder retirement plan

1. Phase 1 - confirm dependencies: retain the call-graph evidence above and
   verify no hidden production override registers another bot scheduler job.
2. Phase 2 - isolate locally: remove only recap registration, recap functions,
   `/ruhe` and `/testrecap`; do not touch app web-push timers.
3. Phase 3 - add tests: scheduler registration, command behavior, report-worker
   independence and full regression suite.
4. Phase 4 - runtime later: deploy through a dedicated gate and restart the bot
   only after explicit approval; do not disable `clarity-bot.service` yet.
5. Phase 5 - archive after observation: confirm no reminder errors or required
   Telegram behavior before removing residual preference data/code.

## Remaining admin and report blockers

Admin blockers:

1. Current use of `/nudge_inactive`, rename/migration broadcasts,
   `/testreport`, `/testrecap` and `/backupnow` is UNKNOWN.
2. Access-request Telegram notifications are not identical to cockpit behavior.
3. A safe non-Telegram replacement for test-report diagnostics is not formally
   documented.
4. Operator acceptance of timer-only backup operations is unverified.

Report blockers:

1. Telegram-only users may still need link/PDF delivery through a bot object.
2. The number and last delivery date of Telegram-only report recipients is
   UNKNOWN.
3. Bot-local queue code is duplicated even though it is not scheduled.
4. A production gate must prove all eligible users have verified app accounts or
   an explicitly retained delivery adapter.

## Evidence required for the final decommission gate

Only aggregate, privacy-safe read-only evidence is required:

- timestamp of the last Telegram update/command;
- timestamp of the last DB write attributable to a `telegram:*` request or bot
  source;
- count of recently active Telegram users, without IDs;
- count and latest delivery date of reports sent to Telegram-only users;
- count of approved Telegram users without a verified app account;
- usage counts for the remaining admin-only Telegram commands.

Until that evidence and the migrations above are complete:

- SAFE TO DECOMMISSION BOT NOW: **NO**
- SAFE TO RETIRE TELEGRAM REMINDER PATHS LATER: **YES**, through the phased plan
- SAFE TO START SEPARATION: **YES**

## Wave 8 local-only retirement

Status: **RETIRED IN LOCAL CODE - NOT YET DEPLOYED**.

The following isolated Telegram reminder paths were removed from local
`bot.py` after confirming that they had no API, worker, systemd, timer or test
callers:

- the 20:30 `send_evening_recaps` APScheduler registration and its scheduler;
- `send_evening_recaps`, `build_evening_recap` and
  `get_evening_recap_candidates`;
- the `/ruhe` preference command, `toggle_recap_muted`, `is_recap_muted` and the
  now-unused recap preference constant;
- the admin-only `/testrecap` command.

The removed `/ruhe` path previously wrote only the Telegram-specific recap
preference to `user_badges`. That write disappeared together with the retired
Telegram reminder; no general badge function, badge award or badge history was
removed.

The following dormant bot-local report queue and maintenance helpers were also
removed after proving that their call graph was closed inside `bot.py` and had
no live entrypoint:

- `previous_month_key`, `get_active_user_ids`, `report_now` and
  `random_report_time_for_today`;
- `create_monthly_report_jobs`, `has_report_jobs_for_month`,
  `ensure_monthly_report_jobs` and `claim_due_report_jobs`;
- `mark_report_job_sent`, `mark_report_job_failed`,
  `mark_report_job_skipped`, `process_report_job` and
  `process_due_report_jobs`;
- `cleanup_expired_web_reports` and `archive_old_pdf_reports`.

Report production remains owned by `rove_report_worker.py`, its systemd units
and the existing report engine. Wave 8 does not change those paths, any app
web-push reminder, database schema or production runtime. The historical
37-write-path classification above remains the Wave-7 audit baseline; six
category-D write paths are now absent from local `bot.py` but remain present in
production until a later approved deploy.

Wave 8 does not make the Telegram bot safe to decommission. Long polling,
Telegram expense/onboarding flows, access administration and legacy report
delivery remain active in local code and production.

## Telegram decommission gate (Wave 12)

This is a read-only readiness assessment. It does not stop, disable, restart
or modify `clarity-bot.service`, and it does not authorize deletion of
Telegram data. Telegram-only behavior is not migrated merely to preserve the
retiring channel.

### Non-Telegram dependency result

No non-Telegram production runtime service was found to import `bot.py`, start
it as part of App operation, or require `clarity-bot.service` in systemd
ordering. `dashboard.py` can start the bot as an optional operator convenience;
that does not make it an App runtime dependency. The following runtimes are
separate from the bot process:

| Component | Depends on bot process | What stops working if bot stops | Blocker |
|---|---:|---|---:|
| `rove_app_api.py` | NO | No App API endpoint identified | NO |
| `rove_app_state.py` and domain modules | NO | App state and domain reads/writes continue; historical bot-sourced rows remain readable | NO |
| `rove_report_worker.py` and report engine | NO for App reports | App report enqueue, processing, storage and App delivery continue; Telegram-only delivery does not | YES for legacy recipients |
| `rove_monthly_reminders.py` | NO | App monthly push reminders continue | NO |
| `rove_tracking_reminders.py` | NO | App tracking reminders continue | NO |
| `refresh_market_positions.py` | NO | Scheduled market refresh continues | NO |
| `backup_clarity_db.py` | NO | Automated DB backups continue | NO |
| frontend and App authentication | NO | No App runtime dependency identified | NO |
| `dashboard.py` | NO | Only optional operator convenience for starting `bot.py` disappears | NO for App runtime |

The App and bot share SQLite and historical data contracts. This is a data
compatibility concern, not a requirement for the bot process to continuously
run. Category-budget bot writes, Telegram onboarding, portfolio commands and
Telegram undo do not need migration solely to preserve Telegram functionality
after the channel is retired.

### `clarity-bot.service` responsibility

The inspected unit defines:

| Field | Value |
|---|---|
| `ExecStart` | `/usr/bin/python3 /root/clarity/bot.py` |
| `WorkingDirectory` | `/root/clarity` |
| Environment | optional `/root/clarity/.rove-leeway.env`; `bot.py` also loads `.env` |
| Restart | `always`, `RestartSec=5` |
| Dependencies | `After=network.target`; no `Requires`/`Wants` from App units |
| Wanted by | `multi-user.target` |
| Shared files | source tree and environment files; no App import dependency |
| Shared DB | `/root/clarity/clarity.db`; historical rows remain valid if the process stops |

Its exclusive responsibility is Telegram transport, handlers and remaining
Telegram-specific administration/legacy flows. No other inspected service
requires it or orders itself after/before it.

### Remaining bot-only responsibilities

| Responsibility | Classification | App runtime required | Shutdown blocker |
|---|---|---:|---:|
| Telegram polling, menu and replies | A: Telegram-only / obsolete product channel | NO | YES until retirement gate is approved |
| Telegram onboarding and refinement | A: Telegram-only / obsolete | NO | YES for unmigrated users |
| Free-form Telegram expense and investment entry | A: Telegram-only / obsolete | NO | YES while active users depend on it |
| Telegram-only report delivery | A: Telegram-only / obsolete | NO | YES while recipients remain |
| Access approval/revocation notifications in Telegram | D: admin/operations dependency | NO | CONDITIONAL; cockpit coverage and acceptance required |
| `/nudge_inactive`, rename/migration broadcasts | D: admin/operations convenience | NO | NO, current use is UNKNOWN |
| `/testreport` and diagnostic commands | D: admin/operations convenience | NO | CONDITIONAL until replacement is accepted |
| `/backupnow` | D: admin/operations convenience | NO; timer exists | NO for routine runtime, manual usage UNKNOWN |

The local Wave-8 source contains no active scheduler registration; its runtime
entrypoint is Telegram long polling. Production still runs the previously
deployed bot version and must be checked separately before any runtime action.

### Report gate

- App report generation: **YES, independent of `bot.py`**.
- App report storage/archive: **YES, independent of `bot.py`**.
- App report delivery for verified App accounts: **YES, independent of `bot.py`**.
- Delivery to Telegram-only recipients: **NO, it still requires Telegram transport**.
- Normal App reports after Telegram retirement: **YES**, once user-account and
  pending-delivery checks pass.

### Scheduler gate

No remaining scheduler/background job was found inside local Wave-8 `bot.py`.
App background work is owned by separate systemd services and timers for
reports, monthly reminders, tracking reminders, market refresh and DB backup.
The old production bot may still contain pre-Wave-8 scheduler behavior until
a separate production comparison proves otherwise.

### User and data gate

The following production evidence remains **UNKNOWN** and must be collected as
aggregate, privacy-safe counts or timestamps before shutdown:

- recent active Telegram users;
- approved users without a verified App account;
- last Telegram command/write activity;
- pending Telegram report deliveries and Telegram-only recipients;
- recent use of admin convenience commands.

Historical Telegram rows do not by themselves block shutdown. Active runtime
dependency, pending delivery and account migration do.

### Database gate

- Bot shutdown requires DB migration: **NO**.
- Bot shutdown requires data deletion: **NO**.
- Bot shutdown can leave legacy data in place: **YES**.
- No App component was found to require continuous bot maintenance of a DB
  table. Shared historical rows must remain readable and must not be deleted as
  part of the runtime stop.

### Separate shutdown phases

1. **Phase A - stop/disable runtime later:** only after all gate conditions are
   proven; do not combine this with code removal.
2. **Phase B - observation:** monitor App health, reports, reminders, backups,
   auth and Telegram-dependent errors for at least seven days, including one
   report and monthly-reminder cycle where applicable.
3. **Phase C - remove Telegram-only code:** only after observation and user
   migration evidence pass.
4. **Phase D - legacy data/schema review:** separate decision; retain historical
   rows unless a later approved retention decision says otherwise.

### Rollback plan

Re-enable and start `clarity-bot.service` from the known production release,
verify the process lock and Telegram polling, and confirm that only one bot
instance is active. No DB restoration is expected because shutdown does not
delete or migrate data. A DB restore is only for a separately documented data
incident.

### Decommission checklist

- [ ] no non-Telegram runtime dependency
- [ ] App reports independent
- [ ] critical App operations independent
- [ ] scheduler responsibilities replaced or obsolete
- [ ] relevant users confirmed on App
- [ ] no required pending Telegram delivery
- [x] rollback documented
- [ ] production backup/current state known for shutdown window
- [ ] shutdown smoke tests defined and executed

### Wave 12 decision

**SAFE TO STOP BOT RUNTIME LATER: CONDITIONAL.** The App runtime is
independent, but active Telegram users, Telegram-only report recipients and
admin/operations usage are not verified. **SAFE TO DELETE BOT CODE NOW: NO.**
**USER ACTIVITY VERIFICATION STILL NEEDED: YES.**

The Wave-11 category-budget migration remains intentionally stopped. Because
Telegram is being retired as a product channel, those bot writes must not be
migrated unless the App or another non-Telegram runtime is shown to need them.

## Wave 13 production readiness evidence

Read-only check performed on 01.09.2026. No service, file, database, queue or
Telegram state was modified.

### Verified production facts

- `clarity-bot.service`: **ACTIVE / RUNNING**.
- `ExecStart`: `/usr/bin/python3 /root/clarity/bot.py`.
- Working directory: `/root/clarity`.
- Restart policy: `always`, five-second delay.
- Systemd target: `multi-user.target`.
- App API, report, reminder, market-refresh and DB-backup units are separate
  from the bot unit; their timers do not require `clarity-bot.service`.
- Local Wave-8 `bot.py` has no active internal scheduler registration.

### Production values not verifiable with current read-only account

The SSH account could not read `/root/clarity/bot.py` or the production DB,
including through non-interactive privilege escalation. Therefore these values
remain **UNKNOWN** rather than inferred:

- production Git branch, commit, bot hash and line count;
- whether the deployed production bot still contains the pre-Wave-8 evening
  recap or removed dead helpers;
- recent Telegram updates, commands, callbacks, outputs and last activity;
- `/testreport`, `/backupnow` and broadcast usage;
- Telegram report deliveries and active Telegram-only recipients;
- recent bot-specific DB write activity and affected domains;
- verified App-account coverage for relevant Telegram users.

The journal query returned no usable privacy-safe activity event. This is not
evidence of inactivity. The production shutdown gates therefore remain
conditional and no shutdown observation phase has started.
