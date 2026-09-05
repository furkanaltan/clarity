# Rov.E Environment Variables

This inventory describes every variable in `deploy/env.example`. `Required`
means required for the named runtime or feature. A blank safe default means the
feature remains disabled until production supplies a value.

Production secrets must stay in root-readable environment files outside Git.
`CHANGE_ME` is a template marker, never a deployable value.

| Name | Used by | Required | Secret | Safe default |
|---|---|---:|---:|---|
| `CLARITY_DB_NAME` | API, bot, workers and maintenance jobs | YES | NO | No production default; use an absolute DB path |
| `CLARITY_REPORTS_DIR` | Report generation and delivery | NO | NO | `/root/clarity/reports` |
| `CLARITY_REPORT_ARCHIVE_DAYS` | Report maintenance | NO | NO | `60` |
| `ROVE_APP_AUTH_SECRET` | API cookie/session signing | YES | YES | None |
| `ROVE_APP_ALLOWED_ORIGIN` | API CORS validation | YES | NO | `https://getrove.de` for the documented host |
| `ROVE_APP_ALLOWED_ORIGINS` | API CORS allowlist | YES | NO | Rov.E production origins from the template |
| `ROVE_APP_API_PORT` | API listener | NO | NO | `5057` |
| `ROVE_APP_AUTH_CODE_TTL_MINUTES` | Login code expiry | NO | NO | `10` |
| `ROVE_ACCOUNT_DELETE_CODE_TTL_MINUTES` | Account deletion verification | NO | NO | `10` |
| `ROVE_APP_AUTH_SESSION_TTL_DAYS` | Account session expiry | NO | NO | `180` |
| `ROVE_APP_SESSION_COOKIE` | API session cookie name | NO | NO | `rove_app_session` |
| `ROVE_APP_COOKIE_SECURE` | Secure-cookie enforcement | YES | NO | `1` |
| `ROVE_LOGIN_FROM_EMAIL` | Login and recovery mail | YES when email auth is enabled | NO | `info@getrove.de` for the documented host |
| `ROVE_LOGIN_FROM_NAME` | Login and recovery mail | NO | NO | `Rov.E` |
| `BREVO_API_KEY` | Login and recovery mail delivery | YES when email auth is enabled | YES | None |
| `ROVE_ADMIN_USER_IDS` | API administration allowlist | NO | NO | Empty list |
| `TELEGRAM_TOKEN` | Telegram bot | YES for bot service | YES | None |
| `ADMIN_USER_IDS` | Telegram administration allowlist | NO | NO | Empty list |
| `CLARITY_USER_APPROVAL` | Telegram user approval gate | NO | NO | `1` |
| `CLARITY_BOT_LOCK_FILE` | Telegram singleton lock | NO | NO | `clarity_bot.lock` |
| `OPENAI_API_KEY` | AI chat and screenshot extraction | YES when AI features are enabled | YES | None |
| `ROVE_AI_CHAT_MODEL` | AI chat | NO | NO | `gpt-4o-mini` |
| `ROVE_AI_CHAT_TIMEOUT_SECONDS` | AI chat request timeout | NO | NO | `12` |
| `ROVE_AI_CHAT_MAX_INPUT_CHARS` | AI chat input limit | NO | NO | `2000` |
| `ROVE_AI_CHAT_MAX_OUTPUT_CHARS` | AI chat output limit | NO | NO | `1200` |
| `ROVE_AI_CHAT_RATE_LIMIT` | AI chat rate limit | NO | NO | `20` |
| `ROVE_AI_REPORT_TEXT` | AI-generated report text switch | NO | NO | `1` |
| `ROVE_SCREENSHOT_MODEL` | Screenshot extraction | NO | NO | `gpt-4o-mini` |
| `ROVE_SCREENSHOT_DAILY_LIMIT` | Screenshot extraction quota | NO | NO | `10` |
| `ROVE_SCREENSHOT_MAX_BYTES` | Screenshot upload limit | NO | NO | `5242880` |
| `ROVE_SCREENSHOT_MAX_ROWS` | Screenshot import row limit | NO | NO | `20` |
| `COINMARKETCAP_API_KEY` | Crypto quotes and metadata | YES when crypto market data is enabled | YES | None |
| `TWELVE_DATA_API_KEY` | Equity market data | YES when Twelve Data is enabled | YES | None |
| `LEEWAY_API_TOKEN` | European market data | YES when Leeway is enabled | YES | None |
| `ROVE_VAPID_PUBLIC` | Browser push subscription | YES when web push is enabled | NO | None |
| `ROVE_VAPID_PRIVATE` | Web push signing | YES when web push is enabled | YES | None |
| `ROVE_VAPID_SUBJECT` | Web push contact identity | YES when web push is enabled | NO | `mailto:info@getrove.de` for the documented host |
| `ROVE_INTERNAL_PUSH_SECRET` | Internal push endpoint authentication | YES when push workers are enabled | YES | None |
| `ROVE_APP_INTERNAL_PUSH_URL` | Reminder-to-API push delivery | YES when push workers are enabled | NO | Local API URL from the template |
| `MIN_TRACKING_DAYS` | Tracking reminder eligibility | NO | NO | `14` |
| `REPORT_SEND_WINDOW_START_HOUR` | Report delivery window | NO | NO | `8` |
| `REPORT_SEND_WINDOW_END_HOUR` | Report delivery window | NO | NO | `14` |
| `REPORT_WORKER_BATCH_SIZE` | Report worker throughput | NO | NO | `1` |
| `REPORT_WORKER_INTERVAL_SECONDS` | Report worker pacing | NO | NO | `10` |
| `REPORT_MAX_ATTEMPTS` | Report retry policy | NO | NO | `3` |
| `REPORT_RETRY_DELAY_MINUTES` | Report retry policy | NO | NO | `15` |
| `REPORT_PROCESSING_TIMEOUT_MINUTES` | Stale report recovery | NO | NO | `45` |
| `REPORT_CREATION_MISFIRE_GRACE_SECONDS` | Report scheduler catch-up | NO | NO | `21600` |
| `ROVE_REPORT_PUBLIC_DIR` | Generated public report storage | YES for report links | NO | `/var/www/reports` |
| `ROVE_REPORT_PUBLIC_BASE_URL` | Public report URL generation | YES for report links | NO | `https://getrove.de/reports` |
| `ROVE_REPORT_LINK_TTL_DAYS` | Public report link expiry | NO | NO | `30` |
| `ROVE_WEB_TEMPLATE_PATH` | HTML report rendering | YES for web reports | NO | Canonical template path from the template |
| `ROVE_APP_STATE_PUBLIC_DIR` | Retired app-state compatibility | NO | NO | `/root/clarity/public/app-state` |
| `ROVE_APP_STATE_PUBLIC_BASE_URL` | Retired app-state compatibility | NO | NO | Empty |
| `ROVE_APP_STATE_LINK_TTL_DAYS` | Retired app-state link expiry | NO | NO | `30` |
| `ROVE_APP_API_BASE_URL` | Optional external API base override | NO | NO | Empty |

## Source files

- Template: `deploy/env.example`
- Runtime inventory and recovery model: `docs/DEPLOYMENT.md`
- Dependency model: `docs/DEPENDENCIES.md`

This document records configuration behavior only. It contains no production
values and does not replace secret storage or host-specific access controls.
