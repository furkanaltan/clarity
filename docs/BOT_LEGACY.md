# Rov.E Bot and Telegram Legacy Separation Audit

Stand: 31.08.2026. This is a read-only architecture inventory. It does not
authorize stopping `clarity-bot.service`, changing production, migrating data or
retiring a feature.

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
