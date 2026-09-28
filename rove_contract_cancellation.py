"""User-owned cancellation workflow; financial contract values never change here."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timezone

from rove_dates import business_today


TRANSITIONS = {
    "DRAFT": {"REVIEW_REQUIRED", "CANCELLED"},
    "REVIEW_REQUIRED": {"USER_CONFIRMED", "CANCELLED"},
    "USER_CONFIRMED": {"READY_TO_SEND", "CANCELLED"},
    "READY_TO_SEND": {"SENDING", "REVIEW_REQUIRED", "CANCELLED"},
    "SENDING": {"SENT", "FAILED"},
    "SENT": {"DELIVERY_RECORDED", "PROVIDER_RESPONSE"},
    "DELIVERY_RECORDED": {"PROVIDER_RESPONSE"},
    "PROVIDER_RESPONSE": {"TERMINATION_CONFIRMED", "FAILED"},
    "FAILED": {"SENDING", "REVIEW_REQUIRED", "CANCELLED"},
    "TERMINATION_CONFIRMED": set(),
    "CANCELLED": set(),
}
REVIEW_FIELDS = frozenset({
    "sender_name", "sender_address", "recipient", "contract_reference",
    "timing_choice", "cancellation_target_date",
})


class CancellationError(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.status = status


def ensure_cancellation_schema(conn: sqlite3.Connection) -> None:
    from rove_app_state import ensure_app_contracts_table

    ensure_app_contracts_table(conn)
    old = conn.execute("SELECT sql FROM sqlite_master WHERE name='app_contract_cancellations'").fetchone()
    migrate = old is not None and "SENDING" not in old[0]
    conn.execute("SAVEPOINT cancellation_schema")
    try:
        if migrate:
            conn.execute("ALTER TABLE app_contract_cancellations RENAME TO app_contract_cancellations_v1")
            conn.execute("ALTER TABLE app_contract_cancellation_events RENAME TO app_contract_cancellation_events_v1")
            conn.execute("DROP INDEX idx_cancellation_active_contract")
            conn.execute("DROP INDEX idx_cancellation_events_owner")
        _create_schema(conn)
        if migrate:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(app_contract_cancellations_v1)")]
            selection = ["CASE status WHEN 'CONFIRMED' THEN 'USER_CONFIRMED' ELSE status END" if key == "status" else key for key in columns]
            conn.execute(f"INSERT INTO app_contract_cancellations ({','.join(columns)}) SELECT {','.join(selection)} FROM app_contract_cancellations_v1")
            conn.execute("""INSERT INTO app_contract_cancellation_events
                (id,user_id,case_id,event_type,from_status,to_status,revision,created_at,actor,source)
                SELECT id,user_id,case_id,event_type,
                  CASE from_status WHEN 'CONFIRMED' THEN 'USER_CONFIRMED' ELSE from_status END,
                  CASE to_status WHEN 'CONFIRMED' THEN 'USER_CONFIRMED' ELSE to_status END,revision,created_at,'legacy','v1_migration'
                FROM app_contract_cancellation_events_v1""")
            for row in conn.execute("SELECT * FROM app_contract_cancellations WHERE user_confirmed_at IS NOT NULL").fetchall():
                case = dict(row)
                conn.execute("""UPDATE app_contract_cancellations SET confirmed_notice_sha256=?,confirmed_payload_sha256=?
                    WHERE user_id=? AND id=?""", (_notice_hash(case), _payload_hash(case), case['user_id'], case['id']))
            conn.execute("DROP TABLE app_contract_cancellation_events_v1")
            conn.execute("DROP TABLE app_contract_cancellations_v1")
        conn.execute("RELEASE cancellation_schema")
    except Exception:
        conn.execute("ROLLBACK TO cancellation_schema")
        conn.execute("RELEASE cancellation_schema")
        raise


def _create_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellations (
        id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        contract_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
            ('DRAFT','REVIEW_REQUIRED','USER_CONFIRMED','READY_TO_SEND','SENDING','SENT',
             'DELIVERY_RECORDED','PROVIDER_RESPONSE','TERMINATION_CONFIRMED','FAILED','CANCELLED')),
        revision INTEGER NOT NULL DEFAULT 1,
        sender_name TEXT NOT NULL DEFAULT '',
        sender_address TEXT NOT NULL DEFAULT '',
        recipient TEXT NOT NULL DEFAULT '',
        contract_reference TEXT NOT NULL DEFAULT '',
        timing_choice TEXT CHECK(timing_choice IN ('next_possible','date')),
        cancellation_target_date TEXT,
        date_source TEXT CHECK(date_source = 'user_review'),
        notice_date TEXT NOT NULL,
        generated_notice_text TEXT NOT NULL DEFAULT '',
        reviewed_contract_sha256 TEXT,
        confirmed_notice_sha256 TEXT,
        confirmed_payload_sha256 TEXT,
        user_confirmed_at TEXT,
        ready_to_send_at TEXT,
        termination_confirmed_at TEXT,
        confirmed_end_date TEXT,
        confirmation_source TEXT,
        error_code TEXT,
        retry_allowed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, id),
        FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
        FOREIGN KEY(user_id, contract_id) REFERENCES app_contracts(user_id, contract_id)
            ON DELETE CASCADE,
        CHECK(status NOT IN ('USER_CONFIRMED','READY_TO_SEND','SENDING','SENT','DELIVERY_RECORDED',
            'PROVIDER_RESPONSE','TERMINATION_CONFIRMED') OR user_confirmed_at IS NOT NULL),
        CHECK(status <> 'READY_TO_SEND' OR ready_to_send_at IS NOT NULL)
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_cancellation_active_contract
        ON app_contract_cancellations(user_id, contract_id)
        WHERE status <> 'CANCELLED'""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        case_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN
            ('case_created','review_required','review_updated','review_invalidated','user_confirmed',
             'ready_to_send','send_requested','sending','sent','delivery_recorded','response_received',
             'termination_confirmed','failed','retry','cancelled')),
        from_status TEXT,
        to_status TEXT NOT NULL,
        revision INTEGER NOT NULL,
        actor TEXT NOT NULL DEFAULT 'user',
        source TEXT NOT NULL DEFAULT 'app',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id, case_id) REFERENCES app_contract_cancellations(user_id, id)
            ON DELETE CASCADE
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_cancellation_events_owner
        ON app_contract_cancellation_events(user_id, case_id, id)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellation_messages (
        id TEXT NOT NULL, user_id INTEGER NOT NULL, case_id TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('outbound','inbound')),
        channel TEXT NOT NULL DEFAULT 'email' CHECK(channel='email'),
        sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
        body_sha256 TEXT NOT NULL, revision INTEGER NOT NULL,
        body_text TEXT, provider_message_id TEXT, sent_at TEXT, received_at TEXT,
        delivered_at TEXT, transport_status TEXT NOT NULL,
        source TEXT NOT NULL, error_code TEXT, retry_allowed INTEGER NOT NULL DEFAULT 0,
        reply_to TEXT, response_fingerprint TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,id),
        FOREIGN KEY(user_id,case_id) REFERENCES app_contract_cancellations(user_id,id) ON DELETE CASCADE,
        CHECK(direction <> 'outbound' OR body_text IS NULL)
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_cancellation_one_dispatch
        ON app_contract_cancellation_messages(user_id,case_id)
        WHERE direction='outbound' AND transport_status IN ('sending','accepted','unknown','delivered')""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_cancellation_response_dedup
        ON app_contract_cancellation_messages(user_id,case_id,response_fingerprint)
        WHERE response_fingerprint IS NOT NULL""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_cancellation_messages_owner
        ON app_contract_cancellation_messages(user_id,case_id,created_at)""")


def _require_schema(conn):
    if conn.execute("""SELECT COUNT(*) FROM sqlite_master WHERE type='table'
        AND name IN ('app_contract_cancellations','app_contract_cancellation_events','app_contract_cancellation_messages')""").fetchone()[0] != 3:
        raise CancellationError("cancellation_schema_unavailable", 503)


def _contract(conn, user_id, contract_id):
    row = conn.execute("SELECT * FROM app_contracts WHERE user_id=? AND contract_id=?",
                       (user_id, contract_id)).fetchone()
    if row is None:
        raise CancellationError("contract_not_found", 404)
    return dict(row)


def _eligible(contract):
    # The existing model represents current contracts; only its explicit flag is authority.
    if not contract["cancelable"] or not str(contract["name"]).strip():
        raise CancellationError("contract_not_cancelable", 409)
    if contract["source"] not in {"app", "telegram_legacy"} or (
        contract["source"] == "telegram_legacy" and not str(contract["legacy_ref"] or "").startswith("telegram_legacy:")
    ):
        raise CancellationError("contract_ownership_unverified", 409)
    _text(contract["name"], "provider_name", 240)


def _contract_hash(contract):
    facts = {key: contract[key] for key in ("contract_id", "name", "category", "cancelable", "source", "legacy_ref")}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _notice_hash(case):
    return hashlib.sha256(case['generated_notice_text'].encode()).hexdigest()


def _payload_hash(case):
    facts = {key: case[key] for key in sorted(REVIEW_FIELDS) + ['contract_id', 'generated_notice_text', 'reviewed_contract_sha256', 'notice_date']}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _case_row(conn, user_id, case_id):
    _require_schema(conn)
    row = conn.execute("SELECT * FROM app_contract_cancellations WHERE user_id=? AND id=?",
                       (user_id, case_id)).fetchone()
    if row is None:
        raise CancellationError("cancellation_not_found", 404)
    return dict(row)


def _text(value, field, limit, *, multiline=False):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CancellationError("invalid_cancellation_review")
    value = value.strip()
    if len(value) > limit or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise CancellationError("invalid_cancellation_review")
    if not multiline and ("\n" in value or "\r" in value):
        raise CancellationError("invalid_cancellation_review")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _review_values(payload, existing):
    if not isinstance(payload, dict) or set(payload) - REVIEW_FIELDS:
        raise CancellationError("invalid_cancellation_review")
    values = dict(existing)
    for field, limit in (("sender_name", 160), ("sender_address", 600), ("recipient", 600), ("contract_reference", 160)):
        if field in payload:
            values[field] = _text(payload[field], field, limit, multiline=field in {"sender_address", "recipient"})
    if "timing_choice" in payload:
        values["timing_choice"] = payload["timing_choice"]
    if values["timing_choice"] not in (None, "next_possible", "date"):
        raise CancellationError("invalid_cancellation_timing")
    if "cancellation_target_date" in payload:
        values["cancellation_target_date"] = payload["cancellation_target_date"] or None
    if values["timing_choice"] == "date":
        raw = values["cancellation_target_date"]
        try:
            parsed = date.fromisoformat(raw) if isinstance(raw, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else None
        except ValueError:
            parsed = None
        if parsed is None or parsed < business_today():
            raise CancellationError("invalid_cancellation_target_date")
        values["cancellation_target_date"] = parsed.isoformat()
        values["date_source"] = "user_review"
    else:
        values["cancellation_target_date"] = values["date_source"] = None
    return values


def generate_notice(case, contract):
    """Use only supplied facts; a debit day is never a cancellation deadline."""
    lines = [value for value in (case["sender_name"], case["sender_address"], case["recipient"]) if value]
    lines.extend([date.fromisoformat(case["notice_date"]).strftime("%d.%m.%Y"),
                  f"Kündigung: {contract['name']}"])
    if case["contract_reference"]:
        lines.append(f"Vertrags-/Kundennummer: {case['contract_reference']}")
    target = (f"zum {date.fromisoformat(case['cancellation_target_date']).strftime('%d.%m.%Y')}"
              if case["timing_choice"] == "date" and case["cancellation_target_date"]
              else "zum nächstmöglichen Zeitpunkt")
    lines.extend(["Sehr geehrte Damen und Herren,",
                  f"hiermit kündige ich meinen Vertrag bei {contract['name']} {target}.",
                  "Bitte bestätigen Sie mir die Kündigung und den Beendigungszeitpunkt.",
                  "Mit freundlichen Grüßen"])
    if case["sender_name"]:
        lines.append(case["sender_name"])
    return "\n\n".join(lines)


def _missing(case):
    missing = [field for field in ("sender_name", "recipient", "timing_choice") if not case[field]]
    if case["timing_choice"] == "date" and not case["cancellation_target_date"]:
        missing.append("cancellation_target_date")
    return missing


def _event(conn, case, event_type, previous, *, actor='user', source='app'):
    conn.execute("""INSERT INTO app_contract_cancellation_events
        (user_id,case_id,event_type,from_status,to_status,revision,actor,source) VALUES (?,?,?,?,?,?,?,?)""",
        (case["user_id"], case["id"], event_type, previous, case["status"], case["revision"], actor, source))


def _transition(conn, case, target, event_type, **evidence):
    previous = case["status"]
    if target not in TRANSITIONS[previous]:
        raise CancellationError("invalid_cancellation_transition", 409)
    case["status"] = target
    conn.execute("UPDATE app_contract_cancellations SET status=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?",
                 (target, case["user_id"], case["id"]))
    _event(conn, case, event_type, previous, **evidence)


def get_cancellation_case(conn, user_id, case_id):
    case = _case_row(conn, user_id, case_id)
    contract = _contract(conn, user_id, case["contract_id"])
    case["provider_name"] = case["contract_name"] = contract["name"]
    case["contract_source"] = contract["source"]
    case["missing_fields"] = _missing(case)
    case["notice_sha256"] = _notice_hash(case)
    case["contract_changed"] = case["reviewed_contract_sha256"] != _contract_hash(contract)
    case["events"] = [dict(row) for row in conn.execute("""SELECT event_type,from_status,to_status,revision,created_at,actor,source
        FROM app_contract_cancellation_events WHERE user_id=? AND case_id=? ORDER BY id""", (user_id, case_id))]
    case.pop("reviewed_contract_sha256")
    case.pop("confirmed_payload_sha256")
    case['messages'] = [dict(row) for row in conn.execute("SELECT * FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? ORDER BY rowid", (user_id,case_id))]
    return case


def list_cancellation_cases(conn, user_id, contract_id):
    _require_schema(conn)
    _contract(conn, user_id, contract_id)
    return [get_cancellation_case(conn, user_id, row[0]) for row in conn.execute("""SELECT id
        FROM app_contract_cancellations WHERE user_id=? AND contract_id=? ORDER BY rowid DESC""", (user_id, contract_id))]


def start_cancellation_case(conn, user_id, contract_id):
    _require_schema(conn)
    contract = _contract(conn, user_id, contract_id)
    _eligible(contract)
    existing = conn.execute("""SELECT id FROM app_contract_cancellations WHERE user_id=? AND contract_id=?
        AND status <> 'CANCELLED'""", (user_id, contract_id)).fetchone()
    if existing:
        return get_cancellation_case(conn, user_id, existing[0])
    case_id = secrets.token_urlsafe(18)
    conn.execute("""INSERT INTO app_contract_cancellations
        (id,user_id,contract_id,status,notice_date) VALUES (?,?,?,'DRAFT',?)""",
        (case_id, user_id, contract_id, business_today().isoformat()))
    case = _case_row(conn, user_id, case_id)
    _event(conn, case, "case_created", None)
    conn.execute("""UPDATE app_contract_cancellations SET generated_notice_text=?,reviewed_contract_sha256=?
        WHERE user_id=? AND id=?""", (generate_notice(case, contract), _contract_hash(contract), user_id, case_id))
    _transition(conn, case, "REVIEW_REQUIRED", "review_required")
    return get_cancellation_case(conn, user_id, case_id)


def _revision(case, expected):
    if type(expected) is not int or expected != case["revision"]:
        raise CancellationError("cancellation_review_changed", 409)


def update_cancellation_review(conn, user_id, case_id, payload, expected_revision):
    case = _case_row(conn, user_id, case_id)
    _revision(case, expected_revision)
    if case["status"] != "REVIEW_REQUIRED":
        raise CancellationError("invalid_cancellation_transition", 409)
    contract = _contract(conn, user_id, case["contract_id"])
    _eligible(contract)
    values = _review_values(payload, case)
    changed_fields = sorted(REVIEW_FIELDS) + ["date_source"]
    if all(values[field] == case[field] for field in changed_fields) and case["reviewed_contract_sha256"] == _contract_hash(contract):
        return get_cancellation_case(conn, user_id, case_id)
    case.update(values)
    case["revision"] += 1
    conn.execute(f"""UPDATE app_contract_cancellations SET
        {','.join(field + '=?' for field in changed_fields)},revision=?,generated_notice_text=?,
        reviewed_contract_sha256=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?""",
        ([case[field] for field in changed_fields] + [case["revision"], generate_notice(case, contract),
          _contract_hash(contract), user_id, case_id]))
    _event(conn, case, "review_updated", "REVIEW_REQUIRED")
    return get_cancellation_case(conn, user_id, case_id)


def confirm_cancellation_case(conn, user_id, case_id, *, confirmed, expected_revision, notice_sha256):
    case = _case_row(conn, user_id, case_id)
    _revision(case, expected_revision)
    if confirmed is not True:
        raise CancellationError("cancellation_confirmation_required")
    if notice_sha256 != hashlib.sha256(case["generated_notice_text"].encode()).hexdigest():
        raise CancellationError("cancellation_review_changed", 409)
    contract = _contract(conn, user_id, case["contract_id"])
    _eligible(contract)
    if case["reviewed_contract_sha256"] != _contract_hash(contract):
        raise CancellationError("cancellation_contract_changed", 409)
    if case["status"] == "READY_TO_SEND":
        return get_cancellation_case(conn, user_id, case_id)
    if case["status"] != "REVIEW_REQUIRED":
        raise CancellationError("invalid_cancellation_transition", 409)
    if _missing(case):
        raise CancellationError("cancellation_review_incomplete", 409)
    _review_values({}, case)
    conn.execute("""UPDATE app_contract_cancellations SET user_confirmed_at=CURRENT_TIMESTAMP,
        confirmed_notice_sha256=?,confirmed_payload_sha256=? WHERE user_id=? AND id=?""",
        (_notice_hash(case), _payload_hash(case), user_id, case_id))
    _transition(conn, case, "USER_CONFIRMED", "user_confirmed")
    conn.execute("""UPDATE app_contract_cancellations SET ready_to_send_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND id=?""", (user_id, case_id))
    _transition(conn, case, "READY_TO_SEND", "ready_to_send")
    return get_cancellation_case(conn, user_id, case_id)


def cancel_cancellation_case(conn, user_id, case_id, expected_revision):
    case = _case_row(conn, user_id, case_id)
    _revision(case, expected_revision)
    if case["status"] == "CANCELLED":
        return get_cancellation_case(conn, user_id, case_id)
    if case['status'] == 'FAILED' and not case['retry_allowed']:
        raise CancellationError('cancellation_send_outcome_unknown', 409)
    _transition(conn, case, "CANCELLED", "cancelled")
    return get_cancellation_case(conn, user_id, case_id)


def email_address(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise CancellationError('cancellation_email_recipient_required')
    local, domain = value.rsplit('@',1)
    if len(local)>64 or local.startswith('.') or local.endswith('.') or '..' in local or any(
        not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in domain.split('.')
    ):
        raise CancellationError('cancellation_email_recipient_required')
    return value


def _timestamp(value, *, minimum=None):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        parsed = parsed.astimezone(timezone.utc)
        if parsed > datetime.now(timezone.utc) or (minimum and parsed < datetime.fromisoformat(minimum).replace(tzinfo=timezone.utc)):
            raise ValueError()
        return parsed.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, AttributeError):
        raise CancellationError('invalid_cancellation_evidence_date')


def verified_reply_address(conn, user_id):
    row = conn.execute("SELECT email FROM app_accounts WHERE user_id=? AND verified_at IS NOT NULL ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    if not row:
        raise CancellationError('cancellation_verified_reply_required', 409)
    return email_address(row[0])


def _invalidate_review(conn, case, contract, reason):
    case['revision'] += 1
    previous = case['status']
    if 'REVIEW_REQUIRED' not in TRANSITIONS[previous]:
        raise CancellationError('invalid_cancellation_transition',409)
    conn.execute("""UPDATE app_contract_cancellations SET status='REVIEW_REQUIRED',revision=?,user_confirmed_at=NULL,
        ready_to_send_at=NULL,confirmed_notice_sha256=NULL,confirmed_payload_sha256=NULL,
        generated_notice_text=?,reviewed_contract_sha256=?,error_code=?,retry_allowed=0,updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND id=?""", (case['revision'],generate_notice(case,contract),_contract_hash(contract),reason,case['user_id'],case['id']))
    case['status'] = 'REVIEW_REQUIRED'
    _event(conn,case,'review_invalidated',previous,actor='system',source='send_guard')
    return {'dispatch': False, 'error': reason, 'case':get_cancellation_case(conn,case['user_id'],case['id'])}


def prepare_cancellation_send(conn, user_id, case_id, *, expected_revision, notice_sha256, confirmed, sender, retry=False):
    """Caller holds BEGIN IMMEDIATE and must commit the attempt BEFORE network I/O."""
    case = _case_row(conn,user_id,case_id)
    _revision(case,expected_revision)
    if confirmed is not True:
        raise CancellationError('cancellation_send_confirmation_required')
    if case['status'] in {'SENDING','SENT','DELIVERY_RECORDED','PROVIDER_RESPONSE','TERMINATION_CONFIRMED'}:
        return {'dispatch':False,'case':get_cancellation_case(conn,user_id,case_id)}
    if retry:
        if case['status'] != 'FAILED' or not case['retry_allowed']:
            raise CancellationError('cancellation_retry_unsafe',409)
    elif case['status'] != 'READY_TO_SEND':
        raise CancellationError('invalid_cancellation_transition',409)
    contract = _contract(conn,user_id,case['contract_id'])
    if not case['user_confirmed_at'] or case['confirmed_notice_sha256'] != _notice_hash(case) or case['confirmed_payload_sha256'] != _payload_hash(case) or case['reviewed_contract_sha256'] != _contract_hash(contract) or generate_notice(case,contract) != case['generated_notice_text']:
        return _invalidate_review(conn,case,contract,'cancellation_confirmation_invalidated')
    _eligible(contract)
    if notice_sha256 != _notice_hash(case):
        raise CancellationError('cancellation_review_changed',409)
    try:
        email_address(case['recipient'])
        _review_values({},case)
    except CancellationError as exc:
        return _invalidate_review(conn,case,contract,str(exc))
    sender = email_address(sender)
    reply_to = verified_reply_address(conn,user_id)
    if conn.execute("SELECT COUNT(*) FROM app_contract_cancellation_messages WHERE user_id=? AND direction='outbound' AND created_at>=datetime('now','-1 day')",(user_id,)).fetchone()[0] >= 10:
        raise CancellationError('cancellation_send_limit',429)
    if conn.execute("SELECT COUNT(*) FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? AND direction='outbound'",(user_id,case_id)).fetchone()[0] >= 3:
        raise CancellationError('cancellation_retry_limit',429)
    message_id = secrets.token_urlsafe(18)
    subject = f"Kündigung: {contract['name']} [Rov.E {case_id}]"
    conn.execute("""INSERT INTO app_contract_cancellation_messages
        (id,user_id,case_id,direction,sender,recipient,subject,body_sha256,revision,transport_status,source,reply_to)
        VALUES (?,?,?,'outbound',?,?,?,?,?,'sending','brevo',?)""",
        (message_id,user_id,case_id,sender,case['recipient'],subject,_notice_hash(case),case['revision'],reply_to))
    _event(conn,case,'retry' if retry else 'send_requested',case['status'])
    conn.execute("UPDATE app_contract_cancellations SET error_code=NULL,retry_allowed=0 WHERE user_id=? AND id=?",(user_id,case_id))
    _transition(conn,case,'SENDING','sending',actor='system',source='send_lock')
    return {'dispatch':True,'message_id':message_id,'case_id':case_id,'user_id':user_id,'sender':sender,
            'recipient':case['recipient'],'reply_to':reply_to,'subject':subject,'body':case['generated_notice_text'],
            'body_sha256':_notice_hash(case),'case':get_cancellation_case(conn,user_id,case_id)}


def finish_cancellation_send(conn, user_id, case_id, message_id, *, provider_message_id=None, error_code=None, retry_allowed=False):
    case = _case_row(conn,user_id,case_id)
    message = conn.execute("SELECT * FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? AND id=? AND direction='outbound'",(user_id,case_id,message_id)).fetchone()
    if not message or message['transport_status'] != 'sending' or case['status'] != 'SENDING':
        raise CancellationError('cancellation_attempt_changed',409)
    if provider_message_id is not None:
        provider_message_id = _text(provider_message_id,'provider_message_id',300)
        if not provider_message_id:
            raise CancellationError('cancellation_transport_result_invalid')
        conn.execute("""UPDATE app_contract_cancellation_messages SET provider_message_id=?,sent_at=CURRENT_TIMESTAMP,
            transport_status='accepted' WHERE user_id=? AND id=?""",(provider_message_id,user_id,message_id))
        _transition(conn,case,'SENT','sent',actor='transport',source='brevo')
    else:
        conn.execute("UPDATE app_contract_cancellation_messages SET transport_status=?,error_code=?,retry_allowed=? WHERE user_id=? AND id=?",
                     ('rejected' if retry_allowed else 'unknown',error_code,bool(retry_allowed),user_id,message_id))
        conn.execute("UPDATE app_contract_cancellations SET error_code=?,retry_allowed=? WHERE user_id=? AND id=?",(error_code,bool(retry_allowed),user_id,case_id))
        _transition(conn,case,'FAILED','failed',actor='transport',source='brevo')
    return get_cancellation_case(conn,user_id,case_id)


def outbound_message(conn, user_id, case_id):
    case = _case_row(conn,user_id,case_id)
    row = conn.execute("""SELECT * FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=?
        AND direction='outbound' AND transport_status IN ('accepted','delivered') ORDER BY rowid DESC LIMIT 1""",(user_id,case_id)).fetchone()
    if row is None:
        raise CancellationError('cancellation_not_sent',409)
    return case,dict(row)


def record_cancellation_delivery(conn, user_id, case_id, evidence):
    case,message = outbound_message(conn,user_id,case_id)
    if evidence.get('messageId') != message['provider_message_id'] or evidence.get('email') != message['recipient'] or evidence.get('event') != 'delivered':
        raise CancellationError('cancellation_delivery_mismatch',409)
    # Delivery may precede local persistence of the provider's acceptance response.
    delivered_at = _timestamp(evidence.get('date'),minimum=message['created_at'])
    if message['delivered_at']:
        return get_cancellation_case(conn,user_id,case_id)
    conn.execute("UPDATE app_contract_cancellation_messages SET delivered_at=?,transport_status='delivered' WHERE user_id=? AND id=?",(delivered_at,user_id,message['id']))
    if case['status'] == 'SENT':
        _transition(conn,case,'DELIVERY_RECORDED','delivery_recorded',actor='transport',source='brevo_events_api')
    else:
        _event(conn,case,'delivery_recorded',case['status'],actor='transport',source='brevo_events_api')
    return get_cancellation_case(conn,user_id,case_id)


def document_cancellation_response(conn, user_id, case_id, payload, expected_revision):
    case,outbound = outbound_message(conn,user_id,case_id)
    _revision(case,expected_revision)
    if case['status'] not in {'SENT','DELIVERY_RECORDED','PROVIDER_RESPONSE'}:
        raise CancellationError('invalid_cancellation_transition',409)
    if not isinstance(payload,dict) or set(payload)-{'sender','subject','body','received_at','in_reply_to'}:
        raise CancellationError('invalid_cancellation_response')
    sender = email_address(payload.get('sender'))
    subject = _text(payload.get('subject'),'subject',300)
    body = _text(payload.get('body'),'body',6000,multiline=True)
    if not subject or not body:
        raise CancellationError('invalid_cancellation_response')
    if payload.get('in_reply_to') and payload['in_reply_to'] != outbound['provider_message_id']:
        raise CancellationError('cancellation_response_mismatch',409)
    received_at = _timestamp(payload.get('received_at'),minimum=outbound['created_at'])
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([sender,subject,body,received_at]).encode()).hexdigest()
    if conn.execute("SELECT 1 FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? AND response_fingerprint=?",(user_id,case_id,fingerprint)).fetchone():
        return get_cancellation_case(conn,user_id,case_id)
    if conn.execute("SELECT COUNT(*) FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? AND direction='inbound'",(user_id,case_id)).fetchone()[0] >= 20:
        raise CancellationError('cancellation_response_limit',429)
    case['revision'] += 1
    conn.execute("UPDATE app_contract_cancellations SET revision=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?",(case['revision'],user_id,case_id))
    conn.execute("""INSERT INTO app_contract_cancellation_messages
        (id,user_id,case_id,direction,sender,recipient,subject,body_sha256,revision,body_text,received_at,
         transport_status,source,response_fingerprint) VALUES (?,?,?,'inbound',?,?,?,?,?,?,?,'user_documented','user_documented',?)""",
        (secrets.token_urlsafe(18),user_id,case_id,sender,outbound['reply_to'],subject,body_hash,case['revision'],body,received_at,fingerprint))
    if case['status'] != 'PROVIDER_RESPONSE':
        _transition(conn,case,'PROVIDER_RESPONSE','response_received',source='user_documented')
    else:
        _event(conn,case,'response_received',case['status'],source='user_documented')
    return get_cancellation_case(conn,user_id,case_id)


def confirm_cancellation_response(conn, user_id, case_id, payload, expected_revision):
    case = _case_row(conn,user_id,case_id)
    _revision(case,expected_revision)
    if not isinstance(payload,dict) or set(payload)-{'message_id','body_sha256','confirmed','outcome','end_date'} or payload.get('confirmed') is not True:
        raise CancellationError('cancellation_provider_confirmation_required')
    message_id = _text(payload.get('message_id'),'message_id',300)
    message = conn.execute("SELECT * FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? AND id=? AND direction='inbound'",(user_id,case_id,message_id)).fetchone()
    if not message or message['body_sha256'] != payload.get('body_sha256'):
        raise CancellationError('cancellation_response_mismatch',409)
    latest = conn.execute("SELECT id FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? AND direction='inbound' ORDER BY rowid DESC LIMIT 1",(user_id,case_id)).fetchone()
    if not latest or latest[0] != message['id']:
        raise CancellationError('cancellation_response_mismatch',409)
    if case['status'] != 'PROVIDER_RESPONSE':
        raise CancellationError('invalid_cancellation_transition',409)
    outcome = payload.get('outcome')
    if not isinstance(outcome,str) or outcome not in {'termination_confirmed','rejected'}:
        raise CancellationError('invalid_cancellation_response')
    end = payload.get('end_date')
    if end == '':
        end = None
    if end is not None:
        try:
            if outcome != 'termination_confirmed' or not isinstance(end,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',end):
                raise ValueError()
            end = date.fromisoformat(end).isoformat()
        except ValueError:
            raise CancellationError('invalid_cancellation_target_date')
    if outcome == 'rejected':
        conn.execute("UPDATE app_contract_cancellations SET error_code='provider_rejected',retry_allowed=0 WHERE user_id=? AND id=?",(user_id,case_id))
        _transition(conn,case,'FAILED','failed',source='user_confirmed_provider_message')
    else:
        conn.execute("""UPDATE app_contract_cancellations SET termination_confirmed_at=CURRENT_TIMESTAMP,
            confirmed_end_date=?,confirmation_source='user_confirmed_provider_message' WHERE user_id=? AND id=?""",(end,user_id,case_id))
        _transition(conn,case,'TERMINATION_CONFIRMED','termination_confirmed',source='user_confirmed_provider_message')
        conn.execute("""UPDATE app_contracts SET cancellation_status='termination_confirmed',effective_end_date=?,
            cancellation_confirmed_at=CURRENT_TIMESTAMP WHERE user_id=? AND contract_id=?""",(end,user_id,case['contract_id']))
    return get_cancellation_case(conn,user_id,case_id)
