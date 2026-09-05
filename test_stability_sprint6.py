import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import get_app_contracts, normalize_legacy_contracts
from migrate_legacy_contracts import run as migration_run
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session


class StabilitySprint6ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "clarity.db"
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    fixed_costs REAL DEFAULT 0,
                    fixed_costs_details TEXT DEFAULT '{}'
                );
                CREATE TABLE app_state_links (
                    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', expires_at TEXT NOT NULL
                );
                CREATE TABLE user_access (user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
                """
            )
            details = {"abos": {"spotify": 50}, "wohnen": {"miete": 900}, "kredite": {"restschuld": 100000}}
            conn.execute("INSERT INTO users VALUES (1, 950, ?)", (json.dumps(details),))
            conn.execute("INSERT INTO users VALUES (2, 10, '{\"abos\": {\"spotify\": 10}}')")
            conn.execute("INSERT INTO app_state_links VALUES ('user-one', 1, 'active', '2099-01-01')")
            conn.execute("INSERT INTO app_state_links VALUES ('user-two', 2, 'active', '2099-01-01')")
            conn.execute("INSERT INTO user_access VALUES (1, 'approved')")
            conn.execute("INSERT INTO user_access VALUES (2, 'approved')")
            conn.commit()
        self.patchers = [
            patch.object(api, "DB_PATH", self.path),
            patch.object(api, "AUTH_SECRET", "legacy-contract-test-secret"),
            patch.object(api, "build_live_app_data", lambda *_args: {"sts": {"available": 0}, "vertraege": []}),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)
        ensure_unlocked_test_session(self.path, 1, "user-one-session")
        ensure_unlocked_test_session(self.path, 2, "user-two-session")

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def request(self, token, payload):
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, token, domain="localhost", path="/")
            return client.post("/v1/contracts", json=payload)

    def test_normalization_is_idempotent_and_keeps_fixed_cost_total(self):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            first = normalize_legacy_contracts(conn, 1)
            conn.commit()
            self.assertEqual(first["created"], 2)
            self.assertEqual(conn.execute("SELECT fixed_costs FROM users WHERE user_id=1").fetchone()[0], 950)
            self.assertEqual(len(get_app_contracts(conn, 1)), 2)
            self.assertEqual(json.loads(conn.execute("SELECT fixed_costs_details FROM users WHERE user_id=1").fetchone()[0])["kredite"]["restschuld"], 100000)
            conn.execute("BEGIN IMMEDIATE")
            second = normalize_legacy_contracts(conn, 1)
            conn.commit()
            self.assertEqual(second["created"], 0)
            self.assertEqual(len(get_app_contracts(conn, 1)), 2)

    def test_legacy_origin_contract_uses_the_normal_app_edit_and_delete_path(self):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            normalize_legacy_contracts(conn, 1)
            conn.commit()
            contract_id = next(item["id"] for item in get_app_contracts(conn, 1) if item["n"] == "Spotify")

        edited = self.request("user-one-session", {"action": "update", "contract_id": contract_id, "amount": 60})
        self.assertEqual(edited.status_code, 200, edited.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT fixed_costs FROM users WHERE user_id=1").fetchone()[0], 960)
            self.assertEqual(conn.execute("SELECT source FROM app_contracts WHERE contract_id=?", (contract_id,)).fetchone()[0], "telegram_legacy")

        forbidden = self.request("user-two-session", {"action": "delete", "contract_id": contract_id})
        self.assertEqual(forbidden.status_code, 404)
        deleted = self.request("user-one-session", {"action": "delete", "contract_id": contract_id})
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_contracts WHERE user_id=1 AND contract_id=?", (contract_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT fixed_costs FROM users WHERE user_id=1").fetchone()[0], 900)

    def test_exact_native_duplicate_is_not_copied_a_second_time(self):
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            api.ensure_app_contracts_table(conn)
            conn.execute(
                """INSERT INTO app_contracts (user_id, contract_id, detail_key, name, category, amount)
                   VALUES (1, 'native-spotify', 'app_native-spotify', 'Spotify', 'Abos', 50)"""
            )
            result = normalize_legacy_contracts(conn, 1)
            conn.commit()
            self.assertEqual(result["exact_duplicates"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_contracts WHERE user_id=1").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT fixed_costs FROM users WHERE user_id=1").fetchone()[0], 950)

    def test_legacy_section_totals_are_metadata_and_do_not_block_real_contracts(self):
        with closing(self.connect()) as conn:
            details = {
                "abos": {"gesamt": 500, "spotify": 50},
                "wohnen": {"hausgeld": 0, "miete": 900},
            }
            conn.execute(
                "UPDATE users SET fixed_costs_details=?, fixed_costs=? WHERE user_id=1",
                (json.dumps(details), 950),
            )
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            result = normalize_legacy_contracts(conn, 1)
            conn.commit()

            self.assertEqual(result["created"], 2)
            self.assertEqual(conn.execute("SELECT fixed_costs FROM users WHERE user_id=1").fetchone()[0], 950)
            names = {row["n"] for row in get_app_contracts(conn, 1)}
            self.assertEqual(names, {"Spotify", "Miete"})
            remaining = json.loads(conn.execute(
                "SELECT fixed_costs_details FROM users WHERE user_id=1"
            ).fetchone()[0])
            self.assertEqual(remaining["abos"]["gesamt"], 500)
            self.assertEqual(remaining["wohnen"]["hausgeld"], 0)

    def test_dry_run_reports_contract_and_fixed_cost_invariants(self):
        result = migration_run(self.path, apply=False)
        gate = result["gate"]
        self.assertEqual(gate["users_with_unexpected_fixed_cost_delta"], 0)
        self.assertTrue(gate["contract_count_preserved"])
        self.assertEqual(gate["expected_operational_legacy_contracts_remaining"], 0)
        self.assertEqual(gate["second_run_new_contracts"], 0)


if __name__ == "__main__":
    unittest.main()
