from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
from test_auth_pin_sprint9_phase2 import ensure_unlocked_test_session
from test_financial_accounts_sprint2 import create_db


WRITE_SQL = re.compile(r"^\s*(UPDATE|INSERT|DELETE|REPLACE|CREATE|ALTER|DROP)\b", re.I)
WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
}


class SqliteReadLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        create_db(self.path)
        for patcher in (patch.object(api, "DB_PATH", self.path),
                        patch.object(api, "AUTH_SECRET", "read-lifecycle-test-secret"),
                        patch.object(api, "hydrate_crypto_logos")):
            patcher.start()
            self.addCleanup(patcher.stop)
        api.app.config.update(TESTING=True)
        with closing(self.connect()) as conn:
            conn.execute("ALTER TABLE users ADD COLUMN onboarding_step INTEGER DEFAULT 10")
            conn.execute("ALTER TABLE users ADD COLUMN goal_description TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN goal_amount REAL")
            conn.execute("""CREATE TABLE portfolio_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                instrument_key TEXT NOT NULL, instrument_label TEXT NOT NULL,
                isin TEXT DEFAULT '', price_symbol TEXT, monthly_contribution REAL DEFAULT 0,
                total_invested REAL, start_price REAL, last_price REAL, last_checked_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, instrument_key))""")
            conn.commit()
        ensure_unlocked_test_session(self.path, 1, "readonly-session")
        api.prepare_runtime_schema()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def snapshot(self):
        with closing(self.connect()) as conn:
            return "\n".join(sorted(conn.iterdump()))

    def request(self, route):
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, "readonly-session")
            return client.get(route)

    def seed_rich_state(self):
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        with closing(self.connect()) as conn:
            conn.execute("UPDATE users SET goal_description='Read lifecycle',goal_amount=3000 WHERE user_id=1")
            conn.execute("INSERT INTO expenses(user_id,amount,category,merchant,created_at) VALUES (1,5000,'Sonstiges','Synthetic',?)", (now,))
            conn.execute("INSERT INTO app_cash_movements(user_id,kind,amount,label,created_at) VALUES (1,'income',3000,'Gehalt',?)", (now,))
            conn.execute("INSERT INTO app_contracts(user_id,contract_id,detail_key,name,category,amount,cancelable,created_at,updated_at) VALUES (1,'read-test','read-test','[VKS TEST] Read','Abos',64,1,?,?)", (now, now))
            conn.execute("INSERT INTO app_goals(user_id,goal_id,name,target_amount,current_amount) VALUES (1,'secondary','Synthetic goal',1000,250)")
            conn.execute("INSERT INTO app_properties(user_id,market_value,remaining_debt,coverage_started_at,coverage_equity_at_start) VALUES (1,120000,80000,'2026-09-01 12:00:00',40000)")
            conn.execute("INSERT INTO portfolio_holdings(user_id,instrument_key,instrument_label,instrument_type,total_invested) VALUES (1,'test-etf','Synthetic ETF','etf',500)")
            conn.execute("CREATE TABLE report_jobs(user_id INTEGER, report_month TEXT, status TEXT, created_at TEXT, opened_at TEXT)")
            conn.execute("INSERT INTO report_jobs VALUES (1,?,'sent',?,NULL)", (now[:7], now))
            conn.commit()

    def assert_no_request_writes(self, route):
        self.seed_rich_state()
        before = self.snapshot()
        statements, denied, connections = [], [], []
        connect = sqlite3.connect

        def trace_connection(*args, **kwargs):
            conn = connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            connections.append(conn)

            def authorize(action, table, column, database, trigger):
                if action in WRITE_ACTIONS:
                    denied.append((action, table))
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            return conn

        with patch.object(sqlite3, "connect", side_effect=trace_connection):
            response = self.request(route)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertGreaterEqual(len(connections), 2)
        self.assertTrue(all(isinstance(conn, api.StateReadConnection) for conn in connections))
        self.assertTrue(any(sql.lstrip().upper().startswith("SELECT") for sql in statements))
        self.assertEqual(denied, [])
        self.assertEqual([sql for sql in statements if WRITE_SQL.match(sql)], [])
        self.assertEqual(self.snapshot(), before)

    def test_get_state_executes_no_sqlite_write_operations(self):
        self.assert_no_request_writes("/v1/state")

    def test_get_transactions_executes_no_sqlite_write_operations(self):
        self.assert_no_request_writes("/v1/transactions")

    def test_get_state_succeeds_while_another_connection_holds_write_lock(self):
        # Exercise both supported journal modes; WAL is not the lifecycle fix.
        for journal in ("DELETE", "WAL"):
            with self.subTest(journal=journal), closing(self.connect()) as writer:
                writer.execute(f"PRAGMA journal_mode={journal}")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE users SET current_cash=999 WHERE user_id=1")
                try:
                    response = self.request("/v1/state")
                    self.assertEqual(response.status_code, 200, response.get_json())
                    self.assertEqual(response.get_json()["sts"]["konto"], 1250)
                finally:
                    writer.rollback()

    def test_prepared_state_matches_previous_rollback_after_write_builder(self):
        self.seed_rich_state()
        before = self.snapshot()
        frozen = datetime.now()

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.astimezone(tz)

        with patch.object(state, "datetime", FixedDatetime):
            with closing(self.connect()) as conn:
                statements = []
                conn.set_trace_callback(statements.append)
                conn.execute("SAVEPOINT previous_state")
                previous = state.build_live_app_data(conn, 1, activate_due_savings=False)
                conn.execute("ROLLBACK TO previous_state")
                conn.execute("RELEASE previous_state")
            with api.db(read_only=True) as conn:
                conn.execute("SAVEPOINT prepared_state")
                current = state.build_live_app_data(conn, 1, activate_due_savings=False)
                conn.execute("RELEASE prepared_state")
        self.assertTrue(any(WRITE_SQL.match(sql) for sql in statements))
        self.assertEqual(current, previous)
        self.assertTrue(current["mentor_events"])
        self.assertEqual(self.snapshot(), before)

    def test_mentor_new_seen_and_resolved_semantics_match_without_writes(self):
        event = {"event_id": "coach:synthetic", "event_type": "budget_overrun",
                 "source_id": "monthly_budget", "period_key": "2026-09",
                 "occurred_at": "2026-09-30T12:00:00", "fingerprint": "100.00"}
        for seen, resolved, fingerprint in ((None, None, "100.00"),
                                            ("2026-09-01", None, "100.00"),
                                            ("2026-09-01", "2026-09-02", "100.00"),
                                            ("2026-09-01", None, "50.00")):
            with self.subTest(seen=seen, resolved=resolved, fingerprint=fingerprint):
                with closing(self.connect()) as conn:
                    conn.execute("DELETE FROM app_mentor_event_state")
                    conn.execute("INSERT INTO app_mentor_event_state(user_id,event_id,event_type,source_id,period_key,occurred_at,fingerprint,seen_at,resolved_at) VALUES (1,?,?,?,?,?,?,?,?)",
                                 (event["event_id"], event["event_type"], event["source_id"],
                                  event["period_key"], event["occurred_at"], fingerprint, seen, resolved))
                    conn.commit()
                    before = self.snapshot()
                    conn.execute("SAVEPOINT previous_mentor")
                    previous = state._observe_mentor_event(conn, 1, event, worsening=True)
                    conn.execute("ROLLBACK TO previous_mentor")
                    conn.execute("RELEASE previous_mentor")
                with api.db(read_only=True) as conn:
                    current = state._observe_mentor_event(conn, 1, event, worsening=True)
                    state.mark_mentor_event_seen(conn, 1, event["event_id"])
                    state._resolve_mentor_event(conn, 1, event["event_id"])
                self.assertEqual(current, previous)
                self.assertEqual(self.snapshot(), before)

    def test_sqlite_itself_rejects_accidental_request_write(self):
        before = self.snapshot()
        with api.db(read_only=True) as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE users SET current_cash=0 WHERE user_id=1")
        self.assertEqual(self.snapshot(), before)

    def test_missing_database_read_fails_without_creating_database(self):
        missing = self.path.parent / "missing.db"
        with patch.object(api, "DB_PATH", missing), self.assertRaises(sqlite3.OperationalError):
            with api.db(read_only=True):
                self.fail("Missing database was opened")
        self.assertFalse(missing.exists())

    def test_startup_is_idempotent_and_does_not_activate_due_finance(self):
        with closing(self.connect()) as conn:
            conn.execute("INSERT INTO app_scheduled_savings(user_id,effective_month,etf_savings,cash_savings) VALUES (1,'2020-01',300,150)")
            conn.commit()
        before = self.snapshot()
        api.prepare_runtime_schema()
        api.prepare_runtime_schema()
        self.assertEqual(self.snapshot(), before)

    def test_month_close_first_use_is_persisted_only_by_protected_refresh(self):
        self.assertEqual(self.request("/v1/state").status_code, 200)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_month_close_enrollment").fetchone()[0], 0)
        with api.app.test_client() as client:
            client.set_cookie(api.SESSION_COOKIE_NAME, "readonly-session")
            self.assertEqual(client.post("/v1/state", headers={"Origin": "https://foreign.invalid"}).status_code, 403)
            response = client.post("/v1/state", headers={"Origin": "https://getrove.de"})
            self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT starts_month FROM app_month_close_enrollment WHERE user_id=1").fetchone()[0], datetime.now().strftime("%Y-%m"))

    def test_neighbor_read_routes_do_not_call_state_compatibility_preparation(self):
        self.seed_rich_state()
        with patch.object(api, "build_live_app_data", side_effect=AssertionError("Unexpected state lifecycle")), \
                patch.object(state, "ensure_app_properties_table", side_effect=AssertionError("Unexpected property backfill")):
            for route, expected in (("/v1/contract-cancellations?contract_id=read-test", 200),
                                    ("/v1/push/preferences", 200),
                                    ("/v1/auth/me", 200),
                                    ("/v1/reports/2026-09/pdf", 404),
                                    ("/v1/public-reports/missing/", 410)):
                with self.subTest(route=route):
                    self.assertEqual(self.request(route).status_code, expected)

    def test_legacy_property_backfill_preserves_finance_and_never_rewrites_history(self):
        with closing(self.connect()) as conn:
            conn.execute("DROP TABLE app_properties")
            conn.execute("CREATE TABLE app_properties(user_id INTEGER PRIMARY KEY,market_value REAL,remaining_debt REAL,monthly_rate REAL,house_fee REAL,management_fee REAL)")
            conn.execute("INSERT INTO app_properties VALUES (1,250000.23,150000.11,500,20,10)")
            conn.execute("UPDATE users SET debt_status='invalid-legacy-value' WHERE user_id=1")
            before = tuple(conn.execute("SELECT current_cash,current_investments,income,fixed_costs,etf_savings,cash_savings FROM users WHERE user_id=1").fetchone())
            conn.commit()
        api.prepare_runtime_schema()
        with closing(self.connect()) as conn:
            row = dict(conn.execute("SELECT * FROM app_properties WHERE user_id=1").fetchone())
            self.assertEqual(row["coverage_equity_at_start"], 100000.12)
            self.assertIsNotNone(row["coverage_started_at"])
            self.assertEqual(conn.execute("SELECT debt_status FROM users WHERE user_id=1").fetchone()[0], "unknown")
            self.assertEqual(tuple(conn.execute("SELECT current_cash,current_investments,income,fixed_costs,etf_savings,cash_savings FROM users WHERE user_id=1").fetchone()), before)
            conn.execute("UPDATE app_properties SET market_value=300000,remaining_debt=100000 WHERE user_id=1")
            conn.commit()
        api.prepare_runtime_schema()
        with closing(self.connect()) as conn:
            current = dict(conn.execute("SELECT * FROM app_properties WHERE user_id=1").fetchone())
        self.assertEqual(current["coverage_started_at"], row["coverage_started_at"])
        self.assertEqual(current["coverage_equity_at_start"], row["coverage_equity_at_start"])
        response = self.request("/v1/state")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["netWorth"], 201750)

    def test_prepared_new_database_has_no_fabricated_property_or_mentor_records(self):
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_properties").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_mentor_event_state").fetchone()[0], 0)
        response = self.request("/v1/state")
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_properties").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_mentor_event_state").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
