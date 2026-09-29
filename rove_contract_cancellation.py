"""User-owned cancellation workflow; financial contract values never change here."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from rove_dates import business_today


TRANSITIONS = {
    "DRAFT": {"REVIEW_REQUIRED", "CANCELLED"},
    "REVIEW_REQUIRED": {"USER_CONFIRMED", "CANCELLED"},
    "USER_CONFIRMED": {"READY_TO_SEND", "CANCELLED"},
    "READY_TO_SEND": {"SENDING", "REVIEW_REQUIRED", "CANCELLED"},
    "SENDING": {"SENT", "FAILED"},
    "SENT": {"DELIVERY_RECORDED", "PROVIDER_RESPONSE", "FOLLOW_UP_DUE", "MANUAL_REVIEW_REQUIRED"},
    "DELIVERY_RECORDED": {"PROVIDER_RESPONSE", "FOLLOW_UP_DUE", "MANUAL_REVIEW_REQUIRED"},
    "PROVIDER_RESPONSE": {"TERMINATION_CONFIRMED", "FAILED", "MANUAL_REVIEW_REQUIRED"},
    "FOLLOW_UP_DUE": {"FOLLOW_UP_PREPARED", "PROVIDER_RESPONSE", "MANUAL_REVIEW_REQUIRED"},
    "FOLLOW_UP_PREPARED": {"PROVIDER_RESPONSE", "MANUAL_REVIEW_REQUIRED"},
    "MANUAL_REVIEW_REQUIRED": {"PROVIDER_RESPONSE", "TERMINATION_CONFIRMED", "FAILED"},
    "FAILED": {"SENDING", "REVIEW_REQUIRED", "CANCELLED", "MANUAL_REVIEW_REQUIRED"},
    "TERMINATION_CONFIRMED": set(),
    "CANCELLED": set(),
}
FOLLOW_UP_DAYS = 14
MANUAL_REVIEW_DAYS = 28
STATUS_LABELS = {
    "DRAFT": "Entwurf", "REVIEW_REQUIRED": "Angaben prüfen", "USER_CONFIRMED": "Text bestätigt",
    "READY_TO_SEND": "Bereit zum Versand", "SENDING": "Versand in Bearbeitung", "SENT": "Gesendet",
    "DELIVERY_RECORDED": "Zugestellt", "PROVIDER_RESPONSE": "Antwort dokumentiert",
    "FOLLOW_UP_DUE": "Nachfassen nötig", "FOLLOW_UP_PREPARED": "Nachfrage vorbereitet",
    "MANUAL_REVIEW_REQUIRED": "Manuelle Prüfung nötig", "TERMINATION_CONFIRMED": "Kündigung bestätigt",
    "FAILED": "Vorgang fehlgeschlagen", "CANCELLED": "Vorbereitung abgebrochen",
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
    migrate = old is not None and "FOLLOW_UP_DUE" not in old[0]
    v1 = old is not None and "SENDING" not in old[0]
    tables = ['app_contract_cancellations', 'app_contract_cancellation_events']
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellation_messages'").fetchone():
        tables.append('app_contract_cancellation_messages')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellation_delivery_events'").fetchone():
        tables.append('app_contract_cancellation_delivery_events')
    conn.execute("SAVEPOINT cancellation_schema")
    try:
        if migrate:
            for table in tables:
                conn.execute(f"ALTER TABLE {table} RENAME TO {table}_previous")
            for index in ('idx_cancellation_active_contract', 'idx_cancellation_events_owner',
                          'idx_cancellation_one_dispatch', 'idx_cancellation_response_dedup', 'idx_cancellation_messages_owner',
                          'idx_cancellation_provider_message_id', 'idx_cancellation_delivery_events_owner'):
                conn.execute(f"DROP INDEX IF EXISTS {index}")
        _create_schema(conn)
        if migrate:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(app_contract_cancellations_previous)")]
            selection = ["CASE status WHEN 'CONFIRMED' THEN 'USER_CONFIRMED' ELSE status END" if key == "status" else key for key in columns]
            conn.execute(f"INSERT INTO app_contract_cancellations ({','.join(columns)}) SELECT {','.join(selection)} FROM app_contract_cancellations_previous ORDER BY rowid")
            if v1:
                conn.execute("""INSERT INTO app_contract_cancellation_events
                (id,user_id,case_id,event_type,from_status,to_status,revision,created_at,actor,source)
                SELECT id,user_id,case_id,event_type,
                  CASE from_status WHEN 'CONFIRMED' THEN 'USER_CONFIRMED' ELSE from_status END,
                  CASE to_status WHEN 'CONFIRMED' THEN 'USER_CONFIRMED' ELSE to_status END,revision,created_at,'legacy','v1_migration'
                FROM app_contract_cancellation_events_previous""")
            else:
                for table in tables[1:]:
                    conn.execute(f"INSERT INTO {table} SELECT * FROM {table}_previous ORDER BY rowid")
            for row in conn.execute("SELECT * FROM app_contract_cancellations WHERE user_confirmed_at IS NOT NULL").fetchall():
                case = dict(row)
                if v1:
                    conn.execute("""UPDATE app_contract_cancellations SET confirmed_notice_sha256=?,confirmed_payload_sha256=?
                        WHERE user_id=? AND id=?""", (_notice_hash(case), _payload_hash(case), case['user_id'], case['id']))
                match = re.search(r'^Kündigung: (.+)$', case['generated_notice_text'], re.MULTILINE)
                if match:
                    conn.execute("UPDATE app_contract_cancellations SET confirmed_provider_name=? WHERE user_id=? AND id=?",
                                 (match[1], case['user_id'], case['id']))
            for table in reversed(tables):
                conn.execute(f"DROP TABLE {table}_previous")
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
             'DELIVERY_RECORDED','PROVIDER_RESPONSE','TERMINATION_CONFIRMED','FAILED','CANCELLED',
             'FOLLOW_UP_DUE','FOLLOW_UP_PREPARED','MANUAL_REVIEW_REQUIRED')),
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
        confirmed_provider_name TEXT,
        error_code TEXT,
        retry_allowed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, id),
        FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
        FOREIGN KEY(user_id, contract_id) REFERENCES app_contracts(user_id, contract_id)
            ON DELETE CASCADE,
        CHECK(status NOT IN ('USER_CONFIRMED','READY_TO_SEND','SENDING','SENT','DELIVERY_RECORDED',
            'PROVIDER_RESPONSE','TERMINATION_CONFIRMED','FOLLOW_UP_DUE','FOLLOW_UP_PREPARED') OR user_confirmed_at IS NOT NULL),
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
             'termination_confirmed','failed','retry','cancelled','follow_up_due','follow_up_prepared','manual_review_required')),
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
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_cancellation_response_dedup
        ON app_contract_cancellation_messages(user_id,case_id,response_fingerprint)
        WHERE response_fingerprint IS NOT NULL""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_cancellation_messages_owner
        ON app_contract_cancellation_messages(user_id,case_id,created_at)""")
    conn.execute("DROP INDEX IF EXISTS idx_cancellation_one_dispatch")
    conn.execute("""CREATE UNIQUE INDEX idx_cancellation_one_dispatch
        ON app_contract_cancellation_messages(user_id,case_id)
        WHERE direction='outbound' AND transport_status IN
            ('sending','accepted','unknown','delivered','deferred','soft_bounce',
             'hard_bounce','blocked','invalid','error')""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_cancellation_provider_message_id
        ON app_contract_cancellation_messages(provider_message_id)
        WHERE direction='outbound' AND provider_message_id IS NOT NULL""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellation_delivery_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_fingerprint TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        case_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        provider_message_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN
            ('sent','delivered','deferred','soft_bounce','hard_bounce','blocked','invalid','error')),
        occurred_at TEXT NOT NULL,
        received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id,case_id) REFERENCES app_contract_cancellations(user_id,id) ON DELETE CASCADE,
        FOREIGN KEY(user_id,message_id) REFERENCES app_contract_cancellation_messages(user_id,id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_cancellation_delivery_events_owner
        ON app_contract_cancellation_delivery_events(user_id,case_id,occurred_at,id)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellation_followups (
        id TEXT NOT NULL, user_id INTEGER NOT NULL, case_id TEXT NOT NULL, original_message_id TEXT NOT NULL,
        body_text TEXT NOT NULL, body_sha256 TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'user_prepared',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,id), UNIQUE(user_id,case_id),
        FOREIGN KEY(user_id,case_id) REFERENCES app_contract_cancellations(user_id,id) ON DELETE CASCADE,
        FOREIGN KEY(user_id,original_message_id) REFERENCES app_contract_cancellation_messages(user_id,id) ON DELETE CASCADE
    )""")


def _require_schema(conn):
    if conn.execute("""SELECT COUNT(*) FROM sqlite_master WHERE type='table'
        AND name IN ('app_contract_cancellations','app_contract_cancellation_events',
                     'app_contract_cancellation_messages','app_contract_cancellation_delivery_events')""").fetchone()[0] != 4:
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
    sender_lines = [value for value in (case["sender_name"], case["sender_address"]) if value]
    paragraphs = []
    if sender_lines:
        paragraphs.append("\n".join(sender_lines))
    paragraphs.extend([date.fromisoformat(case["notice_date"]).strftime("%d.%m.%Y"),
                       "Sehr geehrte Damen und Herren,"])
    if case["contract_reference"]:
        reference = f"Vertrags-/Kundennummer: {case['contract_reference']}"
    else:
        reference = None
    target = (f"zum {date.fromisoformat(case['cancellation_target_date']).strftime('%d.%m.%Y')}"
              if case["timing_choice"] == "date" and case["cancellation_target_date"]
              else "zum nächstmöglichen Zeitpunkt")
    paragraphs.append(f"hiermit kündige ich meinen Vertrag bei {contract['name']} {target}.")
    if reference:
        paragraphs.append(reference)
    paragraphs.extend(["Bitte bestätigen Sie mir die Kündigung sowie den Beendigungszeitpunkt schriftlich.",
                       "Mit freundlichen Grüßen"])
    if case["sender_name"]:
        paragraphs[-1] += f"\n{case['sender_name']}"
    return "\n\n".join(paragraphs)


def _missing(case):
    missing = [field for field in ("sender_name", "recipient", "timing_choice") if not case[field]]
    if case["timing_choice"] == "date" and not case["cancellation_target_date"]:
        missing.append("cancellation_target_date")
    return missing


def _event(conn, case, event_type, previous, *, actor='user', source='app', occurred_at=None):
    if occurred_at:
        conn.execute("""INSERT INTO app_contract_cancellation_events
            (user_id,case_id,event_type,from_status,to_status,revision,actor,source,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (case["user_id"], case["id"], event_type, previous, case["status"], case["revision"],
             actor, source, occurred_at))
    else:
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
    record_event = evidence.pop('record_event', True)
    if record_event:
        _event(conn, case, event_type, previous, **evidence)


def get_cancellation_case(conn, user_id, case_id):
    case = _case_row(conn, user_id, case_id)
    contract = _contract(conn, user_id, case["contract_id"])
    case["provider_name"] = case["contract_name"] = contract["name"]
    if case['confirmed_provider_name']:
        case['provider_name'] = case['confirmed_provider_name']
    case["contract_source"] = contract["source"]
    case["missing_fields"] = _missing(case)
    case["notice_sha256"] = _notice_hash(case)
    case["contract_changed"] = case["reviewed_contract_sha256"] != _contract_hash(contract)
    case["events"] = [dict(row) for row in conn.execute("""SELECT event_type,from_status,to_status,revision,created_at,actor,source
        FROM app_contract_cancellation_events WHERE user_id=? AND case_id=? ORDER BY id""", (user_id, case_id))]
    case.pop("reviewed_contract_sha256")
    case.pop("confirmed_payload_sha256")
    case['messages'] = [dict(row) for row in conn.execute("SELECT * FROM app_contract_cancellation_messages WHERE user_id=? AND case_id=? ORDER BY rowid", (user_id,case_id))]
    for row in conn.execute("""SELECT event_type,occurred_at,message_id FROM app_contract_cancellation_delivery_events
        WHERE user_id=? AND case_id=? ORDER BY occurred_at,id""", (user_id, case_id)):
        case['events'].append({
            'event_type': 'brevo_delivery_' + row['event_type'],
            'from_status': None,
            'to_status': case['status'],
            'revision': next((message['revision'] for message in case['messages'] if message['id'] == row['message_id']), case['revision']),
            'created_at': row['occurred_at'],
            'actor': 'transport',
            'source': 'brevo_webhook',
        })
    case['events'].sort(key=lambda event: (event['created_at'] or '', event['revision']))
    case['followups'] = [dict(row) for row in conn.execute("SELECT * FROM app_contract_cancellation_followups WHERE user_id=? AND case_id=? ORDER BY rowid", (user_id,case_id))]
    case['reminder'] = cancellation_reminder(case)
    case['status_label'] = STATUS_LABELS[case['status']]
    if case['status'] == 'DELIVERY_RECORDED':
        case['status_label'] = 'Zugestellt'
    elif case['status'] == 'SENT' and case['messages']:
        latest_outbound = next((message for message in reversed(case['messages']) if message['direction'] == 'outbound'), None)
        if latest_outbound and latest_outbound['transport_status'] in {'deferred', 'soft_bounce'}:
            case['status_label'] = 'Zustellung verzögert'
    case['contract_ended'] = bool(case['status'] == 'TERMINATION_CONFIRMED' and case['confirmed_end_date']
                                  and case['confirmed_end_date'] < business_today().isoformat())
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
        confirmed_notice_sha256=?,confirmed_payload_sha256=?,confirmed_provider_name=? WHERE user_id=? AND id=?""",
        (_notice_hash(case), _payload_hash(case), contract['name'], user_id, case_id))
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
        ready_to_send_at=NULL,confirmed_notice_sha256=NULL,confirmed_payload_sha256=NULL,confirmed_provider_name=NULL,
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
    if case['status'] in {'SENDING','SENT','DELIVERY_RECORDED','PROVIDER_RESPONSE','TERMINATION_CONFIRMED',
                          'FOLLOW_UP_DUE','FOLLOW_UP_PREPARED','MANUAL_REVIEW_REQUIRED'}:
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
    subject = (f"Kündigung meines Vertrags – {case['contract_reference']}"
               if case['contract_reference'] else f"Kündigung meines Vertrags bei {contract['name']}")
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
        AND direction='outbound' AND transport_status IN
            ('accepted','delivered','deferred','soft_bounce','hard_bounce','blocked','invalid','error')
        ORDER BY rowid DESC LIMIT 1""",(user_id,case_id)).fetchone()
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
        _transition(conn,case,'DELIVERY_RECORDED','delivery_recorded',actor='transport',source='brevo_events_api',
                    occurred_at=delivered_at)
    else:
        _event(conn,case,'delivery_recorded',case['status'],actor='transport',source='brevo_events_api',
               occurred_at=delivered_at)
    return get_cancellation_case(conn,user_id,case_id)


BREVO_DELIVERY_EVENT_TYPES = frozenset({
    'sent', 'delivered', 'deferred', 'soft_bounce', 'hard_bounce', 'blocked', 'invalid', 'error',
})
BREVO_TERMINAL_DELIVERY_FAILURES = frozenset({'hard_bounce', 'blocked', 'invalid', 'error'})


def record_brevo_delivery_event(conn, *, provider_message_id, recipient, event_type,
                                occurred_at, attempt_marker=None):
    """Record one authenticated, exactly-correlated Brevo event without resending."""
    provider_message_id = _text(provider_message_id, 'provider_message_id', 300)
    recipient = email_address(recipient)
    if event_type not in BREVO_DELIVERY_EVENT_TYPES:
        raise CancellationError('invalid_brevo_delivery_event')
    rows = conn.execute("""SELECT m.*,c.status AS case_status FROM app_contract_cancellation_messages m
        JOIN app_contract_cancellations c ON c.user_id=m.user_id AND c.id=m.case_id
        WHERE m.direction='outbound' AND m.provider_message_id=?""", (provider_message_id,)).fetchall()
    if len(rows) != 1:
        return {'result': 'unmatched'}
    message = dict(rows[0])
    if message['recipient'].casefold() != recipient.casefold():
        return {'result': 'unmatched'}
    if attempt_marker is not None and attempt_marker != 'vks-attempt=' + message['id']:
        return {'result': 'unmatched'}
    occurred_at = _timestamp(occurred_at, minimum=message['created_at'])
    fingerprint = hashlib.sha256(json.dumps(
        [provider_message_id, recipient.casefold(), event_type, occurred_at],
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    inserted = conn.execute("""INSERT OR IGNORE INTO app_contract_cancellation_delivery_events
        (event_fingerprint,user_id,case_id,message_id,provider_message_id,event_type,occurred_at)
        VALUES (?,?,?,?,?,?,?)""", (fingerprint, message['user_id'], message['case_id'], message['id'],
                                      provider_message_id, event_type, occurred_at)).rowcount
    if not inserted:
        return {'result': 'duplicate', 'case': get_cancellation_case(conn, message['user_id'], message['case_id'])}

    event_rows = conn.execute("""SELECT event_type,occurred_at FROM app_contract_cancellation_delivery_events
        WHERE user_id=? AND message_id=? ORDER BY occurred_at,id""", (message['user_id'], message['id'])).fetchall()
    terminal = [row for row in event_rows if row['event_type'] in BREVO_TERMINAL_DELIVERY_FAILURES]
    delivered = [row for row in event_rows if row['event_type'] == 'delivered']
    if message['delivered_at'] and not delivered:
        delivered_at = message['delivered_at']
    else:
        delivered_at = delivered[-1]['occurred_at'] if delivered else None
    latest_terminal = terminal[-1] if terminal else None
    latest_delivered = delivered[-1] if delivered else None
    effective_delivery_time = latest_delivered['occurred_at'] if latest_delivered else delivered_at
    if latest_terminal and (not effective_delivery_time or latest_terminal['occurred_at'] >= effective_delivery_time):
        transport_status = latest_terminal['event_type']
    elif effective_delivery_time:
        transport_status = 'delivered'
    else:
        latest = event_rows[-1]
        transport_status = {'sent': 'accepted'}.get(latest['event_type'], latest['event_type'])
    conn.execute("""UPDATE app_contract_cancellation_messages SET transport_status=?,
        delivered_at=COALESCE(delivered_at,?),retry_allowed=0 WHERE user_id=? AND id=?""",
        (transport_status, delivered_at, message['user_id'], message['id']))

    case = _case_row(conn, message['user_id'], message['case_id'])
    if transport_status in BREVO_TERMINAL_DELIVERY_FAILURES:
        if 'MANUAL_REVIEW_REQUIRED' in TRANSITIONS.get(case['status'], set()):
            _transition(conn, case, 'MANUAL_REVIEW_REQUIRED', 'manual_review_required',
                        actor='transport', source='brevo_webhook', record_event=False)
        conn.execute("UPDATE app_contract_cancellations SET retry_allowed=0 WHERE user_id=? AND id=?",
                     (message['user_id'], message['case_id']))
    elif transport_status == 'delivered' and case['status'] == 'SENT':
        _transition(conn, case, 'DELIVERY_RECORDED', 'delivery_recorded', actor='transport',
                    source='brevo_webhook', record_event=False)
    return {'result': 'recorded', 'case': get_cancellation_case(conn, message['user_id'], message['case_id'])}


def document_cancellation_response(conn, user_id, case_id, payload, expected_revision):
    case,outbound = outbound_message(conn,user_id,case_id)
    _revision(case,expected_revision)
    if case['status'] not in {'SENT','DELIVERY_RECORDED','PROVIDER_RESPONSE','FOLLOW_UP_DUE',
                              'FOLLOW_UP_PREPARED','MANUAL_REVIEW_REQUIRED'}:
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
    if case['status'] not in {'PROVIDER_RESPONSE', 'MANUAL_REVIEW_REQUIRED'}:
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


def cancellation_reminder(case, *, now=None):
    """Product reminders, not statutory deadlines. A documented response stops them."""
    result = {'due_at': None, 'manual_review_at': None, 'due': False, 'manual_review_due': False}
    messages = case.get('messages', [])
    sent = next((message for message in messages if message['direction'] == 'outbound'
                 and message['transport_status'] in {'accepted', 'delivered'} and message['sent_at']), None)
    if not sent:
        return result
    sent_at = datetime.fromisoformat(sent['sent_at']).replace(tzinfo=timezone.utc)
    due_at = sent_at + timedelta(days=FOLLOW_UP_DAYS)
    review_at = sent_at + timedelta(days=MANUAL_REVIEW_DAYS)
    result.update(due_at=due_at.strftime('%Y-%m-%d %H:%M:%S'),
                  manual_review_at=review_at.strftime('%Y-%m-%d %H:%M:%S'))
    open_case = case['status'] in {'SENT', 'DELIVERY_RECORDED', 'FOLLOW_UP_DUE', 'FOLLOW_UP_PREPARED'}
    if open_case and not any(message['direction'] == 'inbound' for message in messages):
        current = now or datetime.now(timezone.utc)
        result.update(due=current >= due_at, manual_review_due=current >= review_at)
    return result


def refresh_cancellation_reminders(conn, *, now=None, limit=100):
    """Bounded existing-maintenance hook. Never sends mail or creates another cancellation."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_contract_cancellation_followups'").fetchone():
        return 0  # Schema is owned by controlled API startup, not by maintenance.
    changed = 0
    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(days=FOLLOW_UP_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    rows = conn.execute("""SELECT c.user_id,c.id FROM app_contract_cancellations c
        WHERE c.status IN ('SENT','DELIVERY_RECORDED','FOLLOW_UP_DUE','FOLLOW_UP_PREPARED')
        AND EXISTS(SELECT 1 FROM app_contract_cancellation_messages m WHERE m.user_id=c.user_id AND m.case_id=c.id
                   AND m.direction='outbound' AND m.transport_status IN ('accepted','delivered') AND m.sent_at<=?)
        AND NOT EXISTS(SELECT 1 FROM app_contract_cancellation_messages r WHERE r.user_id=c.user_id
                       AND r.case_id=c.id AND r.direction='inbound')
        AND (c.status IN ('SENT','DELIVERY_RECORDED') OR EXISTS(
             SELECT 1 FROM app_contract_cancellation_messages m WHERE m.user_id=c.user_id AND m.case_id=c.id
             AND m.sent_at<=? AND m.transport_status IN ('accepted','delivered')))
        ORDER BY c.updated_at,c.rowid LIMIT ?""",
        (cutoff, (current - timedelta(days=MANUAL_REVIEW_DAYS)).strftime('%Y-%m-%d %H:%M:%S'), limit)).fetchall()
    for row in rows:
        case = get_cancellation_case(conn, row['user_id'], row['id'])
        reminder = cancellation_reminder(case, now=current)
        target = 'MANUAL_REVIEW_REQUIRED' if reminder['manual_review_due'] else 'FOLLOW_UP_DUE'
        if target == case['status'] or case['status'] == 'FOLLOW_UP_PREPARED' and target == 'FOLLOW_UP_DUE':
            continue
        _advance_case_revision(conn, case)
        _transition(conn, case, target, target.lower(), actor='system', source='internal_reminder')
        changed += 1
    return changed


def _advance_case_revision(conn, case):
    case['revision'] += 1
    conn.execute("UPDATE app_contract_cancellations SET revision=? WHERE user_id=? AND id=?",
                 (case['revision'], case['user_id'], case['id']))


def prepare_cancellation_followup(conn, user_id, case_id, expected_revision):
    case, message = outbound_message(conn, user_id, case_id)
    _revision(case, expected_revision)
    existing = conn.execute("SELECT 1 FROM app_contract_cancellation_followups WHERE user_id=? AND case_id=?",
                            (user_id, case_id)).fetchone()
    if existing and case['status'] == 'FOLLOW_UP_PREPARED':
        return get_cancellation_case(conn, user_id, case_id)
    full = get_cancellation_case(conn, user_id, case_id)
    reminder = cancellation_reminder(full)
    if not reminder['due'] or reminder['manual_review_due'] or case['status'] not in {'SENT', 'DELIVERY_RECORDED', 'FOLLOW_UP_DUE'}:
        raise CancellationError('cancellation_followup_not_due', 409)
    if not case['confirmed_provider_name'] or case['confirmed_notice_sha256'] != _notice_hash(case):
        raise CancellationError('cancellation_manual_review_required', 409)
    sent_date = datetime.fromisoformat(message['sent_at']).strftime('%d.%m.%Y')
    target = (f"zum {date.fromisoformat(case['cancellation_target_date']).strftime('%d.%m.%Y')}"
              if case['timing_choice'] == 'date' and case['cancellation_target_date'] else 'zum nächstmöglichen Zeitpunkt')
    lines = [case['sender_name'], message['recipient'], f"Nachfrage zu meiner Kündigung: {case['confirmed_provider_name']}",
             f"Versand der ursprünglichen Kündigung: {sent_date}"]
    if case['contract_reference']:
        lines.append(f"Vertrags-/Kundennummer: {case['contract_reference']}")
    lines.extend(['Sehr geehrte Damen und Herren,',
                  f"am {sent_date} habe ich Ihnen meine Kündigung {target} übermittelt.",
                  'Bitte teilen Sie mir den Bearbeitungsstand mit und bestätigen Sie den Beendigungszeitpunkt.',
                  'Diese Nachfrage ändert weder die ursprüngliche Kündigung noch den darin genannten Termin.',
                  'Mit freundlichen Grüßen', case['sender_name']])
    body = '\n\n'.join(lines)
    if case['status'] != 'FOLLOW_UP_DUE':
        _transition(conn, case, 'FOLLOW_UP_DUE', 'follow_up_due', actor='system', source='internal_reminder')
    _advance_case_revision(conn, case)
    conn.execute("""INSERT INTO app_contract_cancellation_followups
        (id,user_id,case_id,original_message_id,body_text,body_sha256) VALUES (?,?,?,?,?,?)""",
        (secrets.token_urlsafe(18), user_id, case_id, message['id'], body, hashlib.sha256(body.encode()).hexdigest()))
    _transition(conn, case, 'FOLLOW_UP_PREPARED', 'follow_up_prepared')
    return get_cancellation_case(conn, user_id, case_id)


def request_cancellation_manual_review(conn, user_id, case_id, expected_revision):
    case = _case_row(conn, user_id, case_id)
    _revision(case, expected_revision)
    if case['status'] == 'MANUAL_REVIEW_REQUIRED':
        return get_cancellation_case(conn, user_id, case_id)
    _advance_case_revision(conn, case)
    _transition(conn, case, 'MANUAL_REVIEW_REQUIRED', 'manual_review_required')
    conn.execute("UPDATE app_contract_cancellations SET retry_allowed=0 WHERE user_id=? AND id=?", (user_id, case_id))
    return get_cancellation_case(conn, user_id, case_id)


def build_cancellation_file(conn, user_id, case_id):
    """Private evidence projection, not a new snapshot or a claim of legal validity."""
    case = get_cancellation_case(conn, user_id, case_id)
    messages = case['messages']

    def message_for(event, direction):
        candidates = [message for message in messages
                      if message['direction'] == direction and message['revision'] == event['revision']]
        return candidates[-1] if candidates else None

    timeline = []
    for event in case['events']:
        event = dict(event)
        occurred_at = event['created_at']
        if event['event_type'] == 'user_confirmed':
            occurred_at = case['user_confirmed_at'] or occurred_at
        elif event['event_type'] == 'ready_to_send':
            occurred_at = case['ready_to_send_at'] or occurred_at
        elif event['event_type'] in {'send_requested', 'sending', 'retry'}:
            message = message_for(event, 'outbound')
            occurred_at = (message or {}).get('created_at') or occurred_at
        elif event['event_type'] == 'sent':
            message = message_for(event, 'outbound')
            occurred_at = ((message or {}).get('sent_at') or (message or {}).get('created_at')
                           or occurred_at)
        elif event['event_type'] == 'delivery_recorded':
            message = message_for(event, 'outbound')
            occurred_at = ((message or {}).get('delivered_at') or (message or {}).get('created_at')
                           or occurred_at)
        elif event['event_type'] == 'response_received':
            message = message_for(event, 'inbound')
            occurred_at = ((message or {}).get('received_at') or (message or {}).get('created_at')
                           or occurred_at)
        elif event['event_type'] == 'follow_up_prepared' and case['followups']:
            occurred_at = case['followups'][-1]['created_at'] or occurred_at
        elif event['event_type'] == 'termination_confirmed':
            occurred_at = case['termination_confirmed_at'] or occurred_at
        event['occurred_at'] = occurred_at
        timeline.append(event)
    def timeline_order(event):
        try:
            value = datetime.fromisoformat(event['occurred_at'].replace('Z', '+00:00'))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc), event['revision']
        except (AttributeError, TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc), event['revision']

    timeline.sort(key=timeline_order)

    message_fields = ('direction', 'sender', 'recipient', 'subject', 'body_text', 'body_sha256',
                      'provider_message_id', 'sent_at', 'received_at', 'delivered_at',
                      'transport_status', 'source', 'error_code', 'reply_to', 'created_at')
    return {
        'title': 'Kündigungsakte', 'case_reference': case['id'], 'revision': case['revision'],
        'generated_on': datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Berlin')).date().isoformat(),
        'provider': case['confirmed_provider_name'] or (case['provider_name'] if not case['messages'] else None),
        'contract': case['contract_name'], 'contract_reference': case['contract_reference'] or None,
        'status': case['status'], 'status_label': case['status_label'],
        'notice_text': case['generated_notice_text'], 'user_confirmed_at': case['user_confirmed_at'],
        'notice_integrity': bool(case['confirmed_notice_sha256'] and case['confirmed_notice_sha256'] == case['notice_sha256']),
        'confirmed_end_date': case['confirmed_end_date'], 'confirmed_at': case['termination_confirmed_at'],
        'confirmation_source': case['confirmation_source'],
        'messages': [{field: message[field] for field in message_fields} for message in case['messages']],
        'followups': [{key: followup[key] for key in ('body_text', 'body_sha256', 'source', 'created_at')}
                      for followup in case['followups']],
        'timeline': timeline,
    }
