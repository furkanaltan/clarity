"""Authenticated encryption for reversible Open-Banking provider secrets.

The module deliberately has no provider or HTTP dependency. It only accepts a
server-side keyring and binds every ciphertext to its immutable Rov.E context.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


VAULT_KEY_PREFIX = "ROVE_OPEN_BANKING_VAULT_KEY_V"
ACTIVE_KEY_VERSION_ENV = "ROVE_OPEN_BANKING_VAULT_ACTIVE_KEY_VERSION"
AAD_VERSION = 1
NONCE_BYTES = 12
AUTH_TAG_BYTES = 16
_SAFE_COMPONENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ProviderVaultError(RuntimeError):
    """The vault cannot safely encrypt or decrypt the requested secret."""


@dataclass(frozen=True)
class EncryptedProviderSecret:
    key_version: int
    nonce: bytes
    ciphertext: bytes
    auth_tag: bytes


def active_key_version(env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get(ACTIVE_KEY_VERSION_ENV, "1")).strip()
    if not raw.isdigit() or int(raw) < 1:
        raise ProviderVaultError("vault_key_version_invalid")
    return int(raw)


def _key_for_version(key_version: int, env: Mapping[str, str] | None) -> bytes:
    if not isinstance(key_version, int) or key_version < 1:
        raise ProviderVaultError("vault_key_version_invalid")
    source = os.environ if env is None else env
    raw = str(source.get(f"{VAULT_KEY_PREFIX}{key_version}", "")).strip()
    if not raw:
        raise ProviderVaultError("vault_key_unavailable")
    try:
        key = base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ProviderVaultError("vault_key_invalid") from exc
    if len(key) != 32:
        raise ProviderVaultError("vault_key_invalid")
    return key


def _component(value: object, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_COMPONENT.fullmatch(normalized):
        raise ProviderVaultError(f"vault_{field}_invalid")
    return normalized


def _connection_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderVaultError("vault_connection_id_invalid") from exc


def _aad(
    *,
    user_id: int,
    connection_id: str,
    provider: str,
    secret_type: str,
    aad_version: int,
    resource_id: str | None,
) -> bytes:
    if not isinstance(user_id, int) or user_id <= 0:
        raise ProviderVaultError("vault_user_id_invalid")
    if aad_version != AAD_VERSION:
        raise ProviderVaultError("vault_aad_version_unsupported")
    payload = {
        "aad_version": aad_version,
        "connection_id": _connection_uuid(connection_id),
        "provider": _component(provider, "provider"),
        "resource_id": str(resource_id or ""),
        "secret_type": _component(secret_type, "secret_type"),
        "user_id": user_id,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encrypt_provider_secret(
    plaintext: str | bytes,
    *,
    user_id: int,
    connection_id: str,
    provider: str,
    secret_type: str,
    key_version: int | None = None,
    aad_version: int = AAD_VERSION,
    resource_id: str | None = None,
    env: Mapping[str, str] | None = None,
) -> EncryptedProviderSecret:
    """Encrypt one provider secret with a fresh AES-GCM nonce."""
    if isinstance(plaintext, str):
        raw_plaintext = plaintext.encode("utf-8")
    elif isinstance(plaintext, bytes):
        raw_plaintext = plaintext
    else:
        raise ProviderVaultError("vault_plaintext_invalid")
    if not raw_plaintext:
        raise ProviderVaultError("vault_plaintext_invalid")
    version = active_key_version(env) if key_version is None else key_version
    key = _key_for_version(version, env)
    aad = _aad(
        user_id=user_id,
        connection_id=connection_id,
        provider=provider,
        secret_type=secret_type,
        aad_version=aad_version,
        resource_id=resource_id,
    )
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, raw_plaintext, aad)
    return EncryptedProviderSecret(
        key_version=version,
        nonce=nonce,
        ciphertext=sealed[:-AUTH_TAG_BYTES],
        auth_tag=sealed[-AUTH_TAG_BYTES:],
    )


def decrypt_provider_secret(
    *,
    key_version: int,
    nonce: bytes,
    ciphertext: bytes,
    auth_tag: bytes,
    user_id: int,
    connection_id: str,
    provider: str,
    secret_type: str,
    aad_version: int = AAD_VERSION,
    resource_id: str | None = None,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Decrypt only when the exact key and authenticated context match."""
    if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
        raise ProviderVaultError("vault_envelope_invalid")
    if not isinstance(ciphertext, bytes) or not isinstance(auth_tag, bytes) or len(auth_tag) != AUTH_TAG_BYTES:
        raise ProviderVaultError("vault_envelope_invalid")
    key = _key_for_version(key_version, env)
    aad = _aad(
        user_id=user_id,
        connection_id=connection_id,
        provider=provider,
        secret_type=secret_type,
        aad_version=aad_version,
        resource_id=resource_id,
    )
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + auth_tag, aad)
    except InvalidTag as exc:
        raise ProviderVaultError("vault_decryption_failed") from exc


def delete_provider_secret(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    connection_id: str,
    secret_type: str,
) -> bool:
    """Permanently remove one vaulted secret without ever loading its value."""
    normalized_connection_id = _connection_uuid(connection_id)
    normalized_secret_type = _component(secret_type, "secret_type")
    result = conn.execute(
        """DELETE FROM app_provider_secrets
             WHERE user_id = ? AND connection_id = ? AND secret_type = ?""",
        (user_id, normalized_connection_id, normalized_secret_type),
    )
    return int(result.rowcount or 0) > 0
