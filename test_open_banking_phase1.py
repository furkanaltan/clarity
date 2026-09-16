"""Regression coverage for the isolated Open-Banking Phase-1 foundation."""

from __future__ import annotations

import base64
import ast
import inspect
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rove_provider_data import (
    create_provider_account,
    create_provider_connection,
    ensure_provider_schema,
    load_provider_account_iban,
    load_provider_secret,
    record_bank_transaction,
    set_provider_account_iban,
    store_provider_secret,
)
from rove_provider_vault import (
    ProviderVaultError,
    decrypt_provider_secret,
    delete_provider_secret,
    encrypt_provider_secret,
)
from rove_app_api import delete_user_rows_for_tombstone


USER_ID = 42
PROVIDER = "finapi"
TOKEN = "provider-token-must-never-be-logged"
KEY_V1 = base64.b64encode(b"A" * 32).decode("ascii")


class OpenBankingPhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "rove.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO users (user_id) VALUES (?)", (USER_ID,))
        ensure_provider_schema(self.conn)
        self.connection_id = create_provider_connection(
            self.conn, user_id=USER_ID, provider=PROVIDER
        )
        self.env = {"ROVE_OPEN_BANKING_VAULT_KEY_V1": KEY_V1}

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_encrypt_decrypt_round_trip(self) -> None:
        envelope = encrypt_provider_secret(
            TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        actual = decrypt_provider_secret(
            key_version=envelope.key_version, nonce=envelope.nonce, ciphertext=envelope.ciphertext,
            auth_tag=envelope.auth_tag, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        self.assertEqual(actual.decode("utf-8"), TOKEN)

    def test_same_plaintext_has_unique_nonce_and_ciphertext(self) -> None:
        first = encrypt_provider_secret(
            TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        second = encrypt_provider_secret(
            TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        self.assertNotEqual(first.nonce, second.nonce)
        self.assertNotEqual(first.ciphertext, second.ciphertext)

    def test_schema_rejects_noncanonical_users_table_before_ddl(self) -> None:
        malformed = sqlite3.connect(":memory:")
        self.addCleanup(malformed.close)
        malformed.execute("PRAGMA foreign_keys = ON")
        malformed.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")

        with self.assertRaisesRegex(
            RuntimeError, "provider_schema_requires_users_user_id_primary_key"
        ):
            ensure_provider_schema(malformed)

        provider_tables = malformed.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'app_provider_%'"
        ).fetchall()
        self.assertEqual(provider_tables, [])

    def test_wrong_authenticated_context_fails_closed(self) -> None:
        envelope = encrypt_provider_secret(
            TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        with self.assertRaisesRegex(ProviderVaultError, "vault_decryption_failed"):
            decrypt_provider_secret(
                key_version=1, nonce=envelope.nonce, ciphertext=envelope.ciphertext,
                auth_tag=envelope.auth_tag, user_id=USER_ID + 1, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="access_token", env=self.env,
            )

    def test_tampered_ciphertext_fails_closed(self) -> None:
        envelope = encrypt_provider_secret(
            TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        tampered = bytes([envelope.ciphertext[0] ^ 1]) + envelope.ciphertext[1:]
        with self.assertRaisesRegex(ProviderVaultError, "vault_decryption_failed"):
            decrypt_provider_secret(
                key_version=1, nonce=envelope.nonce, ciphertext=tampered,
                auth_tag=envelope.auth_tag, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="access_token", env=self.env,
            )

    def test_unknown_key_version_and_missing_key_fail_closed(self) -> None:
        envelope = encrypt_provider_secret(
            TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        with self.assertRaisesRegex(ProviderVaultError, "vault_key_unavailable"):
            decrypt_provider_secret(
                key_version=2, nonce=envelope.nonce, ciphertext=envelope.ciphertext,
                auth_tag=envelope.auth_tag, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="access_token", env=self.env,
            )
        with self.assertRaisesRegex(ProviderVaultError, "vault_key_unavailable"):
            encrypt_provider_secret(
                TOKEN, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="access_token", env={},
            )

    def test_vault_row_and_sqlite_backup_never_contain_plaintext(self) -> None:
        store_provider_secret(
            self.conn, TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        self.conn.commit()
        self.assertNotIn(TOKEN, "\n".join(self.conn.iterdump()))
        self.assertNotIn(TOKEN.encode("utf-8"), self.db_path.read_bytes())
        self.assertEqual(
            load_provider_secret(
                self.conn, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="access_token", env=self.env,
            ).decode("utf-8"),
            TOKEN,
        )
        with self.assertRaisesRegex(ProviderVaultError, "vault_key_unavailable"):
            load_provider_secret(
                self.conn, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="access_token", env={},
            )

    def test_secret_delete_never_decrypts_and_removes_the_row(self) -> None:
        store_provider_secret(
            self.conn, TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="refresh_token", env=self.env,
        )
        self.assertTrue(
            delete_provider_secret(
                self.conn, user_id=USER_ID, connection_id=self.connection_id,
                secret_type="refresh_token",
            )
        )
        with self.assertRaisesRegex(LookupError, "provider_secret_not_found"):
            load_provider_secret(
                self.conn, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="refresh_token", env=self.env,
            )

    def test_full_iban_is_vaulted_and_only_last_four_are_plaintext(self) -> None:
        account_id = create_provider_account(
            self.conn, user_id=USER_ID, connection_id=self.connection_id,
            provider_account_id="provider-account-iban",
        )
        iban = "DE89370400440532013000"
        set_provider_account_iban(
            self.conn, iban, user_id=USER_ID, connection_id=self.connection_id,
            provider_account_row_id=account_id, env=self.env,
        )
        self.conn.commit()
        self.assertNotIn(iban, "\n".join(self.conn.iterdump()))
        self.assertEqual(
            self.conn.execute(
                "SELECT iban_last4 FROM app_provider_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0],
            "3000",
        )
        self.assertEqual(
            load_provider_account_iban(
                self.conn, user_id=USER_ID, connection_id=self.connection_id,
                provider_account_row_id=account_id, env=self.env,
            ),
            iban,
        )

    def test_vault_does_not_log_secret_values(self) -> None:
        with self.assertNoLogs(logging.getLogger(), level="INFO"):
            store_provider_secret(
                self.conn, TOKEN, user_id=USER_ID, connection_id=self.connection_id,
                provider=PROVIDER, secret_type="refresh_token", env=self.env,
            )

    def test_account_delete_cascade_removes_provider_data_and_secrets(self) -> None:
        account_id = create_provider_account(
            self.conn, user_id=USER_ID, connection_id=self.connection_id,
            provider_account_id="provider-account-1",
        )
        store_provider_secret(
            self.conn, TOKEN, user_id=USER_ID, connection_id=self.connection_id,
            provider=PROVIDER, secret_type="access_token", env=self.env,
        )
        record_bank_transaction(
            self.conn, user_id=USER_ID, connection_id=self.connection_id,
            provider_account_row_id=account_id, provider=PROVIDER,
            provider_transaction_id="provider-transaction-1", transaction_state="booked", amount_minor=-1234,
        )
        delete_user_rows_for_tombstone(self.conn, USER_ID)
        for table in (
            "app_provider_connections", "app_provider_accounts", "app_provider_secrets", "app_bank_transactions",
        ):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_provider_transaction_is_idempotent_per_connection(self) -> None:
        account_id = create_provider_account(
            self.conn, user_id=USER_ID, connection_id=self.connection_id,
            provider_account_id="provider-account-1",
        )
        record_bank_transaction(
            self.conn, user_id=USER_ID, connection_id=self.connection_id,
            provider_account_row_id=account_id, provider=PROVIDER,
            provider_transaction_id="provider-transaction-1", transaction_state="pending", amount_minor=-1234,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            record_bank_transaction(
                self.conn, user_id=USER_ID, connection_id=self.connection_id,
                provider_account_row_id=account_id, provider=PROVIDER,
                provider_transaction_id="provider-transaction-1", transaction_state="booked", amount_minor=-1234,
            )

    def test_provider_connection_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_connection_mismatch"):
            store_provider_secret(
                self.conn, TOKEN, user_id=USER_ID, connection_id=self.connection_id,
                provider="other_provider", secret_type="access_token", env=self.env,
            )

    def test_phase_one_never_imports_existing_financial_truth_modules(self) -> None:
        tree = ast.parse(inspect.getsource(__import__("rove_provider_data")))
        imported_modules = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue(
            imported_modules.isdisjoint(
                {"rove_score", "report_engine", "rove_app_state", "rove_expense_domain"}
            )
        )


if __name__ == "__main__":
    unittest.main()
