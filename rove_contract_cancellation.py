"""User-owned cancellation preparation. V1 never sends or changes a contract."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import date

from rove_dates import business_today


TRANSITIONS = {
    "DRAFT": {"REVIEW_REQUIRED", "CANCELLED"},
    "REVIEW_REQUIRED": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"READY_TO_SEND", "CANCELLED"},
    "READY_TO_SEND": {"CANCELLED"},
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
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellations (
        id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        contract_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
            ('DRAFT','REVIEW_REQUIRED','CONFIRMED','READY_TO_SEND','CANCELLED')),
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
        user_confirmed_at TEXT,
        ready_to_send_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, id),
        FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
        FOREIGN KEY(user_id, contract_id) REFERENCES app_contracts(user_id, contract_id)
            ON DELETE CASCADE,
        CHECK(status NOT IN ('CONFIRMED','READY_TO_SEND') OR user_confirmed_at IS NOT NULL),
        CHECK(status <> 'READY_TO_SEND' OR ready_to_send_at IS NOT NULL)
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_cancellation_active_contract
        ON app_contract_cancellations(user_id, contract_id)
        WHERE status IN ('DRAFT','REVIEW_REQUIRED','CONFIRMED','READY_TO_SEND')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_contract_cancellation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        case_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN
            ('case_created','review_required','review_updated','user_confirmed','ready_to_send','cancelled')),
        from_status TEXT,
        to_status TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id, case_id) REFERENCES app_contract_cancellations(user_id, id)
            ON DELETE CASCADE
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_cancellation_events_owner
        ON app_contract_cancellation_events(user_id, case_id, id)""")


def _require_schema(conn):
    if conn.execute("""SELECT COUNT(*) FROM sqlite_master WHERE type='table'
        AND name IN ('app_contract_cancellations','app_contract_cancellation_events')""").fetchone()[0] != 2:
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


def _event(conn, case, event_type, previous):
    conn.execute("""INSERT INTO app_contract_cancellation_events
        (user_id,case_id,event_type,from_status,to_status,revision) VALUES (?,?,?,?,?,?)""",
        (case["user_id"], case["id"], event_type, previous, case["status"], case["revision"]))


def _transition(conn, case, target, event_type):
    previous = case["status"]
    if target not in TRANSITIONS[previous]:
        raise CancellationError("invalid_cancellation_transition", 409)
    case["status"] = target
    conn.execute("UPDATE app_contract_cancellations SET status=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?",
                 (target, case["user_id"], case["id"]))
    _event(conn, case, event_type, previous)


def get_cancellation_case(conn, user_id, case_id):
    case = _case_row(conn, user_id, case_id)
    contract = _contract(conn, user_id, case["contract_id"])
    case["provider_name"] = case["contract_name"] = contract["name"]
    case["contract_source"] = contract["source"]
    case["missing_fields"] = _missing(case)
    case["notice_sha256"] = hashlib.sha256(case["generated_notice_text"].encode()).hexdigest()
    case["contract_changed"] = case["reviewed_contract_sha256"] != _contract_hash(contract)
    case["events"] = [dict(row) for row in conn.execute("""SELECT event_type,from_status,to_status,revision,created_at
        FROM app_contract_cancellation_events WHERE user_id=? AND case_id=? ORDER BY id""", (user_id, case_id))]
    case.pop("reviewed_contract_sha256")
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
        AND status IN ('DRAFT','REVIEW_REQUIRED','CONFIRMED','READY_TO_SEND')""", (user_id, contract_id)).fetchone()
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
    conn.execute("""UPDATE app_contract_cancellations SET user_confirmed_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND id=?""", (user_id, case_id))
    _transition(conn, case, "CONFIRMED", "user_confirmed")
    conn.execute("""UPDATE app_contract_cancellations SET ready_to_send_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND id=?""", (user_id, case_id))
    _transition(conn, case, "READY_TO_SEND", "ready_to_send")
    return get_cancellation_case(conn, user_id, case_id)


def cancel_cancellation_case(conn, user_id, case_id, expected_revision):
    case = _case_row(conn, user_id, case_id)
    _revision(case, expected_revision)
    if case["status"] == "CANCELLED":
        return get_cancellation_case(conn, user_id, case_id)
    _transition(conn, case, "CANCELLED", "cancelled")
    return get_cancellation_case(conn, user_id, case_id)
