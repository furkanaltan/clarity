"""Additive, provider-neutral Open-Banking persistence foundation.

No provider API client belongs here. Imported provider data remains isolated from
Rov.E's existing manual expense and financial-truth paths.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Mapping

from rove_provider_vault import (
    AAD_VERSION,
    decrypt_provider_secret,
    encrypt_provider_secret,
)


_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SECRET_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _provider(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not _PROVIDER_RE.fullmatch(normalized):
        raise ValueError("provider_invalid")
    return normalized


def _secret_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not _SECRET_TYPE_RE.fullmatch(normalized):
        raise ValueError("secret_type_invalid")
    return normalized


def _uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("provider_uuid_invalid") from exc


def _external_reference(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 512:
        raise ValueError(f"{field}_invalid")
    return normalized


def _connection_provider(
    conn: sqlite3.Connection, *, user_id: int, connection_id: str
) -> tuple[str, str]:
    normalized_connection_id = _uuid(connection_id)
    row = conn.execute(
        """SELECT provider FROM app_provider_connections
             WHERE user_id = ? AND id = ?""",
        (user_id, normalized_connection_id),
    ).fetchone()
    if not row:
        raise LookupError("provider_connection_not_found")
    return normalized_connection_id, str(row[0])


def ensure_provider_schema(conn: sqlite3.Connection) -> None:
    """Create Phase-1 tables only; no existing financial table is changed."""
    if not table_exists(conn, "users"):
        raise RuntimeError("provider_schema_requires_users")
    user_columns = conn.execute("PRAGMA table_info(users)").fetchall()
    user_id_column = next((column for column in user_columns if column[1] == "user_id"), None)
    if not user_id_column or int(user_id_column[5]) != 1:
        # SQLite accepts malformed foreign keys until their first validation.
        # Refuse a non-canonical parent table before Phase-1 DDL can be applied.
        raise RuntimeError("provider_schema_requires_users_user_id_primary_key")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_provider_connections (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            provider_connection_id TEXT,
            bank_id TEXT,
            bank_name TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'active', 'reauth_required', 'consent_expired',
                                 'disconnect_pending', 'disconnected', 'failed')),
            consent_expires_at TEXT,
            last_synced_at TEXT,
            last_error_code TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            disconnected_at TEXT,
            UNIQUE(user_id, id),
            UNIQUE(user_id, provider, provider_connection_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_provider_consents (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL,
            provider_consent_id TEXT,
            scope_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL CHECK(status IN ('pending', 'active', 'expired', 'revoked', 'failed')),
            granted_at TEXT,
            expires_at TEXT,
            revoked_at TEXT,
            provider_updated_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, connection_id, id),
            UNIQUE(connection_id, provider_consent_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id)
                REFERENCES app_provider_connections(user_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_provider_accounts (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL,
            provider_account_id TEXT NOT NULL,
            display_name TEXT,
            account_type TEXT,
            currency TEXT NOT NULL DEFAULT 'EUR',
            iban_last4 TEXT,
            iban_key_version INTEGER,
            iban_nonce BLOB,
            iban_ciphertext BLOB,
            iban_auth_tag BLOB,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, connection_id, id),
            UNIQUE(connection_id, provider_account_id),
            CHECK(iban_last4 IS NULL OR length(iban_last4) = 4),
            CHECK(
                (iban_key_version IS NULL AND iban_nonce IS NULL AND iban_ciphertext IS NULL AND iban_auth_tag IS NULL)
                OR
                (iban_key_version IS NOT NULL AND iban_nonce IS NOT NULL AND iban_ciphertext IS NOT NULL AND iban_auth_tag IS NOT NULL)
            ),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id)
                REFERENCES app_provider_connections(user_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_provider_secrets (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            secret_type TEXT NOT NULL,
            key_version INTEGER NOT NULL,
            aad_version INTEGER NOT NULL,
            nonce BLOB NOT NULL,
            ciphertext BLOB NOT NULL,
            auth_tag BLOB NOT NULL,
            expires_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            rotated_at TEXT,
            UNIQUE(connection_id, secret_type),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id)
                REFERENCES app_provider_connections(user_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_provider_auth_attempts (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL,
            state_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id)
                REFERENCES app_provider_connections(user_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_provider_sync_runs (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL,
            operation TEXT NOT NULL CHECK(operation IN ('initial', 'manual', 'background', 'disconnect', 'delete')),
            status TEXT NOT NULL CHECK(status IN ('started', 'succeeded', 'failed', 'skipped')),
            imported_accounts INTEGER NOT NULL DEFAULT 0,
            imported_transactions INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            provider_correlation_id TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id)
                REFERENCES app_provider_connections(user_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_bank_transactions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL,
            provider_account_row_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_transaction_id TEXT NOT NULL,
            transaction_state TEXT NOT NULL CHECK(transaction_state IN ('pending', 'booked')),
            booked_at TEXT,
            value_at TEXT,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'EUR',
            merchant_name TEXT,
            purpose TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, connection_id, id),
            UNIQUE(connection_id, provider_transaction_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id)
                REFERENCES app_provider_connections(user_id, id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, connection_id, provider_account_row_id)
                REFERENCES app_provider_accounts(user_id, connection_id, id) ON DELETE CASCADE
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_connections_user ON app_provider_connections(user_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_consents_connection ON app_provider_consents(connection_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_accounts_connection ON app_provider_accounts(connection_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_secrets_expiry ON app_provider_secrets(connection_id, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_auth_attempts_expiry ON app_provider_auth_attempts(expires_at, consumed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_sync_runs_connection ON app_provider_sync_runs(connection_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bank_transactions_account_booking ON app_bank_transactions(provider_account_row_id, booked_at)")


def create_provider_connection(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    provider: str,
    connection_id: str | None = None,
) -> str:
    """Create an internal UUID before any external provider interaction exists."""
    if not conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
        raise LookupError("user_not_found")
    internal_id = _uuid(connection_id) if connection_id else str(uuid.uuid4())
    conn.execute(
        """INSERT INTO app_provider_connections (id, user_id, provider)
           VALUES (?, ?, ?)""",
        (internal_id, user_id, _provider(provider)),
    )
    return internal_id


def create_provider_account(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    connection_id: str,
    provider_account_id: str,
    display_name: str = "",
    account_type: str = "",
    currency: str = "EUR",
) -> str:
    internal_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO app_provider_accounts
               (id, user_id, connection_id, provider_account_id, display_name, account_type, currency)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            internal_id,
            user_id,
            _uuid(connection_id),
            _external_reference(provider_account_id, "provider_account_id"),
            str(display_name).strip() or None,
            str(account_type).strip() or None,
            str(currency).strip().upper() or "EUR",
        ),
    )
    return internal_id


def set_provider_account_iban(
    conn: sqlite3.Connection,
    iban: str,
    *,
    user_id: int,
    connection_id: str,
    provider_account_row_id: str,
    env: Mapping[str, str] | None = None,
) -> None:
    """Persist a full IBAN only as a vault envelope plus its display-safe last four."""
    normalized_connection_id, provider = _connection_provider(
        conn, user_id=user_id, connection_id=connection_id
    )
    internal_account_id = _uuid(provider_account_row_id)
    normalized_iban = "".join(str(iban or "").upper().split())
    if not re.fullmatch(r"[A-Z0-9]{15,34}", normalized_iban):
        raise ValueError("iban_invalid")
    exists = conn.execute(
        """SELECT 1 FROM app_provider_accounts
             WHERE user_id = ? AND connection_id = ? AND id = ?""",
        (user_id, normalized_connection_id, internal_account_id),
    ).fetchone()
    if not exists:
        raise LookupError("provider_account_not_found")
    envelope = encrypt_provider_secret(
        normalized_iban,
        user_id=user_id,
        connection_id=normalized_connection_id,
        provider=provider,
        secret_type="iban",
        resource_id=internal_account_id,
        env=env,
    )
    conn.execute(
        """UPDATE app_provider_accounts
              SET iban_last4 = ?, iban_key_version = ?, iban_nonce = ?,
                  iban_ciphertext = ?, iban_auth_tag = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND connection_id = ? AND id = ?""",
        (
            normalized_iban[-4:], envelope.key_version, envelope.nonce,
            envelope.ciphertext, envelope.auth_tag, user_id,
            normalized_connection_id, internal_account_id,
        ),
    )


def load_provider_account_iban(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    connection_id: str,
    provider_account_row_id: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return an IBAN only to an explicitly authorized server-side caller."""
    normalized_connection_id, provider = _connection_provider(
        conn, user_id=user_id, connection_id=connection_id
    )
    internal_account_id = _uuid(provider_account_row_id)
    row = conn.execute(
        """SELECT iban_key_version, iban_nonce, iban_ciphertext, iban_auth_tag
             FROM app_provider_accounts
             WHERE user_id = ? AND connection_id = ? AND id = ?""",
        (user_id, normalized_connection_id, internal_account_id),
    ).fetchone()
    if not row or any(value is None for value in row):
        raise LookupError("provider_account_iban_not_found")
    return decrypt_provider_secret(
        key_version=int(row[0]), nonce=bytes(row[1]), ciphertext=bytes(row[2]),
        auth_tag=bytes(row[3]), user_id=user_id,
        connection_id=normalized_connection_id, provider=provider,
        secret_type="iban", resource_id=internal_account_id, env=env,
    ).decode("utf-8")


def store_provider_secret(
    conn: sqlite3.Connection,
    plaintext: str | bytes,
    *,
    user_id: int,
    connection_id: str,
    provider: str,
    secret_type: str,
    expires_at: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Encrypt and persist a provider secret without exposing its cleartext."""
    normalized_connection_id, connection_provider = _connection_provider(
        conn, user_id=user_id, connection_id=connection_id
    )
    normalized_provider = _provider(provider)
    normalized_secret_type = _secret_type(secret_type)
    if connection_provider != normalized_provider:
        raise ValueError("provider_connection_mismatch")
    envelope = encrypt_provider_secret(
        plaintext,
        user_id=user_id,
        connection_id=normalized_connection_id,
        provider=normalized_provider,
        secret_type=normalized_secret_type,
        env=env,
    )
    secret_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO app_provider_secrets
               (id, user_id, connection_id, provider, secret_type, key_version, aad_version,
                nonce, ciphertext, auth_tag, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(connection_id, secret_type) DO UPDATE SET
               provider = excluded.provider,
               key_version = excluded.key_version,
               aad_version = excluded.aad_version,
               nonce = excluded.nonce,
               ciphertext = excluded.ciphertext,
               auth_tag = excluded.auth_tag,
               expires_at = excluded.expires_at,
               updated_at = CURRENT_TIMESTAMP,
               rotated_at = CURRENT_TIMESTAMP""",
        (
            secret_id,
            user_id,
            normalized_connection_id,
            normalized_provider,
            normalized_secret_type,
            envelope.key_version,
            AAD_VERSION,
            envelope.nonce,
            envelope.ciphertext,
            envelope.auth_tag,
            expires_at,
        ),
    )
    return secret_id


def load_provider_secret(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    connection_id: str,
    provider: str,
    secret_type: str,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Load a secret only for its exact owner, connection and provider context."""
    normalized_connection_id, connection_provider = _connection_provider(
        conn, user_id=user_id, connection_id=connection_id
    )
    normalized_provider = _provider(provider)
    normalized_secret_type = _secret_type(secret_type)
    if connection_provider != normalized_provider:
        raise ValueError("provider_connection_mismatch")
    row = conn.execute(
        """SELECT key_version, aad_version, nonce, ciphertext, auth_tag
             FROM app_provider_secrets
             WHERE user_id = ? AND connection_id = ? AND provider = ? AND secret_type = ?""",
        (user_id, normalized_connection_id, normalized_provider, normalized_secret_type),
    ).fetchone()
    if not row:
        raise LookupError("provider_secret_not_found")
    return decrypt_provider_secret(
        key_version=int(row[0]),
        aad_version=int(row[1]),
        nonce=bytes(row[2]),
        ciphertext=bytes(row[3]),
        auth_tag=bytes(row[4]),
        user_id=user_id,
        connection_id=normalized_connection_id,
        provider=normalized_provider,
        secret_type=normalized_secret_type,
        env=env,
    )


def delete_provider_data_for_user(conn: sqlite3.Connection, user_id: int) -> None:
    """Explicit, FK-safe provider cleanup used by account deletion and restore."""
    for table in (
        "app_bank_transactions",
        "app_provider_sync_runs",
        "app_provider_auth_attempts",
        "app_provider_secrets",
        "app_provider_accounts",
        "app_provider_consents",
        "app_provider_connections",
    ):
        if table_exists(conn, table):
            conn.execute(f'DELETE FROM "{table}" WHERE user_id = ?', (user_id,))


def record_bank_transaction(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    connection_id: str,
    provider_account_row_id: str,
    provider: str,
    provider_transaction_id: str,
    transaction_state: str,
    amount_minor: int,
    currency: str = "EUR",
) -> str:
    """Persist provider truth only; this never writes Rov.E expenses or balances."""
    if transaction_state not in ("pending", "booked"):
        raise ValueError("transaction_state_invalid")
    normalized_connection_id, connection_provider = _connection_provider(
        conn, user_id=user_id, connection_id=connection_id
    )
    normalized_provider = _provider(provider)
    if connection_provider != normalized_provider:
        raise ValueError("provider_connection_mismatch")
    internal_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO app_bank_transactions
               (id, user_id, connection_id, provider_account_row_id, provider, provider_transaction_id,
                transaction_state, amount_minor, currency)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            internal_id,
            user_id,
            normalized_connection_id,
            _uuid(provider_account_row_id),
            normalized_provider,
            _external_reference(provider_transaction_id, "provider_transaction_id"),
            transaction_state,
            int(amount_minor),
            str(currency).strip().upper() or "EUR",
        ),
    )
    return internal_id
