from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

import rove_app_api as api
from migrate_etf_contribution_schema import run as run_schema_migration
from repair_etf_contribution_assignments import run as run_repair
from rove_app_state import _etf_positions
from rove_investment_contributions import holding_contribution_summary
from rove_market_data import apply_market_quote, ensure_market_tracking_schema
from test_financial_accounts_sprint2 import create_db


class EtfContributionAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_db(self.db_path)
        with closing(self.connect()) as conn:
            conn.execute(
                """CREATE TABLE portfolio_holdings (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       instrument_key TEXT NOT NULL,
                       instrument_label TEXT NOT NULL,
                       isin TEXT NOT NULL DEFAULT '',
                       price_symbol TEXT,
                       monthly_contribution REAL NOT NULL DEFAULT 0,
                       total_invested REAL,
                       start_price REAL,
                       last_price REAL,
                       last_checked_at DATETIME,
                       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                       updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                       UNIQUE(user_id, instrument_key)
                   )"""
            )
            ensure_market_tracking_schema(conn)
            api.ensure_app_etf_savings_plan_table(conn)
            api.ensure_app_etf_position_plans_table(conn)
            api.ensure_investment_contribution_schema(conn)
            conn.execute(
                """UPDATE users SET current_investments=10000, etf_savings=300
                     WHERE user_id=1"""
            )
            self.giro_id = int(conn.execute(
                """SELECT id FROM app_financial_accounts
                    WHERE user_id=1 AND legacy_key='giro'"""
            ).fetchone()[0])
            self._global_plan(conn, 300)
            conn.commit()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _global_plan(self, conn: sqlite3.Connection, amount: float) -> None:
        conn.execute("UPDATE users SET etf_savings=? WHERE user_id=1", (amount,))
        conn.execute(
            """INSERT OR REPLACE INTO app_etf_savings_plan
                   (user_id, execution_day, source_account, source_account_id,
                    mode, active, start_month)
               VALUES (1, 15, 'giro', ?, 'auto', 1, '2020-01')""",
            (self.giro_id,),
        )

    def _holding(
        self,
        conn: sqlite3.Connection,
        key: str,
        name: str,
        value: float,
        *,
        live: bool = True,
        quantity: float = 100,
    ) -> int:
        cursor = conn.execute(
            """INSERT INTO portfolio_holdings
                   (user_id, instrument_key, instrument_label, total_invested,
                    instrument_type, quantity, price_symbol, quote_currency,
                    market_value, valuation_enabled, last_price)
               VALUES (1, ?, ?, ?, 'etf', ?, ?, 'EUR', ?, ?, 100)""",
            (key, name, value, quantity if live else None, key.upper(), value if live else None,
             1 if live else 0),
        )
        return int(cursor.lastrowid)

    def _position_plan(
        self, conn: sqlite3.Connection, holding_id: int, amount: float
    ) -> None:
        conn.execute(
            """INSERT INTO app_etf_position_plans
                   (user_id, holding_id, monthly_amount, execution_day,
                    source_account, source_account_id, mode, active, start_month)
               VALUES (1, ?, ?, 15, 'giro', ?, 'auto', 1, '2020-01')""",
            (holding_id, amount, self.giro_id),
        )

    def test_live_plan_is_assigned_once_without_inventing_market_data(self):
        with closing(self.connect()) as conn:
            holding_id = self._holding(conn, "sp500", "S&P 500", 10000)
            self._position_plan(conn, holding_id, 300)
            before_cash = float(conn.execute(
                "SELECT current_cash FROM users WHERE user_id=1"
            ).fetchone()[0])
            before_net = before_cash + 10000
            quantity_before, market_before = conn.execute(
                "SELECT quantity, market_value FROM portfolio_holdings WHERE id=?",
                (holding_id,),
            ).fetchone()

            first = api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()
            second = api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()

            user = conn.execute(
                "SELECT current_cash,current_investments FROM users WHERE user_id=1"
            ).fetchone()
            holding = conn.execute(
                "SELECT quantity,market_value,total_invested FROM portfolio_holdings WHERE id=?",
                (holding_id,),
            ).fetchone()
            event = conn.execute(
                """SELECT holding_id,asset_name,event_type,amount FROM investment_events
                    WHERE source='app_etf_plan'"""
            ).fetchone()
            positions = _etf_positions(conn, 1)

            self.assertTrue(first["ok"])
            self.assertTrue(second["alreadyRecorded"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM investment_events WHERE source='app_etf_plan'"
            ).fetchone()[0], 1)
            self.assertAlmostEqual(float(user["current_cash"]), before_cash - 300, places=2)
            self.assertAlmostEqual(float(user["current_investments"]), 10300, places=2)
            self.assertAlmostEqual(float(user["current_cash"]) + float(user["current_investments"]), before_net, places=2)
            self.assertEqual(float(holding["quantity"]), float(quantity_before))
            self.assertEqual(float(holding["market_value"]), float(market_before))
            self.assertEqual(float(holding["total_invested"]), 10000)
            self.assertEqual(int(event["holding_id"]), holding_id)
            self.assertEqual(event["asset_name"], "S&P 500")
            self.assertEqual(event["event_type"], "recurring_plan_pending")
            self.assertEqual(positions[0]["pendingContribution"], 300)
            self.assertEqual(positions[0]["v"], 10300)
            self.assertEqual(round(10300 - sum(p["v"] for p in positions), 2), 0)
            financial_giro = conn.execute(
                "SELECT balance FROM app_financial_accounts WHERE id=?", (self.giro_id,)
            ).fetchone()[0]
            legacy_giro = conn.execute(
                "SELECT amount FROM app_account_balances WHERE user_id=1 AND account_key='giro'"
            ).fetchone()[0]
            self.assertEqual(float(financial_giro), 700)
            self.assertEqual(float(legacy_giro), 700)

    def test_one_plan_does_not_touch_the_other_etf(self):
        with closing(self.connect()) as conn:
            first_id = self._holding(conn, "first", "S&P 500", 10000)
            second_id = self._holding(conn, "second", "MSCI World", 8000, quantity=80)
            conn.execute("UPDATE users SET current_investments=18000 WHERE user_id=1")
            self._position_plan(conn, first_id, 300)
            conn.execute("UPDATE app_etf_savings_plan SET active=0 WHERE user_id=1")
            result = api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()
            first = holding_contribution_summary(conn, 1, first_id)
            second = holding_contribution_summary(conn, 1, second_id)
            second_holding = conn.execute(
                "SELECT quantity,market_value FROM portfolio_holdings WHERE id=?", (second_id,)
            ).fetchone()
            self.assertTrue(result["ok"])
            self.assertEqual(first["pending"], 300)
            self.assertEqual(second["pending"], 0)
            self.assertEqual(float(second_holding["quantity"]), 80)
            self.assertEqual(float(second_holding["market_value"]), 8000)

    def test_manual_holding_adds_to_existing_manual_value(self):
        with closing(self.connect()) as conn:
            holding_id = self._holding(conn, "manual", "Manual ETF", 1000, live=False)
            conn.execute("UPDATE users SET current_investments=1000 WHERE user_id=1")
            self._position_plan(conn, holding_id, 300)
            result = api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()
            holding = conn.execute(
                "SELECT total_invested,market_value,quantity FROM portfolio_holdings WHERE id=?",
                (holding_id,),
            ).fetchone()
            event = conn.execute(
                "SELECT event_type,holding_id FROM investment_events WHERE source='app_etf_plan'"
            ).fetchone()
            self.assertTrue(result["ok"])
            self.assertEqual(float(holding["total_invested"]), 1300)
            self.assertIsNone(holding["market_value"])
            self.assertIsNone(holding["quantity"])
            self.assertEqual(event["event_type"], "recurring_plan")
            self.assertEqual(int(event["holding_id"]), holding_id)

    def test_two_position_plans_are_booked_to_their_own_holdings(self):
        with closing(self.connect()) as conn:
            first_id = self._holding(conn, "first", "S&P 500", 10000)
            second_id = self._holding(conn, "second", "MSCI World", 8000, quantity=80)
            conn.execute("UPDATE users SET current_investments=18000 WHERE user_id=1")
            self._global_plan(conn, 500)
            self._position_plan(conn, first_id, 300)
            self._position_plan(conn, second_id, 200)
            result = api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()
            events = conn.execute(
                """SELECT holding_id,amount FROM investment_events
                    WHERE source='app_etf_plan' ORDER BY holding_id"""
            ).fetchall()
            self.assertEqual(result["amount"], 500)
            self.assertEqual([(int(r["holding_id"]), float(r["amount"])) for r in events], [
                (first_id, 300.0), (second_id, 200.0),
            ])
            self.assertEqual(holding_contribution_summary(conn, 1, first_id)["pending"], 300)
            self.assertEqual(holding_contribution_summary(conn, 1, second_id)["pending"], 200)

    def test_legacy_fallback_remains_globally_unassigned(self):
        with closing(self.connect()) as conn:
            self._holding(conn, "sp500", "S&P 500", 10000)
            result = api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()
            event = conn.execute(
                "SELECT holding_id,asset_name FROM investment_events WHERE source='app_etf_plan'"
            ).fetchone()
            positions = _etf_positions(conn, 1)
            assigned = sum(float(position["v"]) for position in positions)
            current = float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0])
            self.assertTrue(result["ok"])
            self.assertIsNone(event["holding_id"])
            self.assertEqual(event["asset_name"], "ETF-Sparplan")
            self.assertEqual(round(current - assigned, 2), 300)

    def test_market_refresh_keeps_history_and_quantity_update_absorbs_pending(self):
        with closing(self.connect()) as conn:
            holding_id = self._holding(conn, "sp500", "S&P 500", 10000)
            self._position_plan(conn, holding_id, 300)
            api.record_due_etf_plan(conn, 1, force=True)
            conn.commit()

            daily = apply_market_quote(conn, holding_id, {
                "symbol": "SP500", "resolved_symbol": "SP500.XETRA",
                "native_price": 101, "eur_price": 101, "provider": "leeway",
            })
            self.assertEqual(daily["delta"], 100)
            self.assertEqual(holding_contribution_summary(conn, 1, holding_id)["pending"], 300)
            self.assertEqual(holding_contribution_summary(conn, 1, holding_id)["contributed"], 300)

            conn.execute("UPDATE portfolio_holdings SET quantity=103 WHERE id=?", (holding_id,))
            conn.commit()
            updated = apply_market_quote(conn, holding_id, {
                "symbol": "SP500", "resolved_symbol": "SP500.XETRA",
                "native_price": 101, "eur_price": 101, "provider": "leeway",
            }, reconcile_pending=True)
            summary = holding_contribution_summary(conn, 1, holding_id)
            total = float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0])
            market_events = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction='out' THEN -amount ELSE amount END),0)
                     FROM investment_events WHERE event_type='market_valuation'"""
            ).fetchone()[0]
            self.assertEqual(updated["pending_absorbed"], 300)
            self.assertEqual(updated["delta"], 3)
            self.assertEqual(summary["pending"], 0)
            self.assertEqual(summary["contributed"], 300)
            self.assertEqual(total, 10403)
            self.assertEqual(float(market_events), 103)

    def test_repair_is_unique_idempotent_and_blocks_ambiguity(self):
        with closing(self.connect()) as conn:
            holding_id = self._holding(conn, "sp500", "S&P 500", 10000)
            self._position_plan(conn, holding_id, 300)
            conn.execute(
                """INSERT INTO investment_events
                       (user_id,amount,direction,asset_type,asset_name,event_type,source,created_at)
                   VALUES (1,300,'in','etf','ETF-Sparplan','recurring_plan','app_etf_plan',?)""",
                (f"{datetime.now():%Y-%m}-15 10:00:00",),
            )
            conn.commit()
        dry = run_repair(self.db_path, apply=False)
        self.assertEqual(dry["summary"]["ready"], 1)
        self.assertEqual(dry["summary"]["changed"], 0)
        first = run_repair(self.db_path, apply=True)
        second = run_repair(self.db_path, apply=True)
        self.assertEqual(first["summary"]["changed"], 1)
        self.assertEqual(second["summary"]["changed"], 0)
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT holding_id,event_type,asset_name FROM investment_events"
            ).fetchone()
            self.assertEqual(int(row["holding_id"]), holding_id)
            self.assertEqual(row["event_type"], "recurring_plan_pending")
            self.assertEqual(row["asset_name"], "S&P 500")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

        ambiguous_path = Path(self.temp.name) / "ambiguous.db"
        create_db(ambiguous_path)
        with closing(sqlite3.connect(ambiguous_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """CREATE TABLE portfolio_holdings (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
                       instrument_key TEXT NOT NULL,instrument_label TEXT NOT NULL,
                       isin TEXT NOT NULL DEFAULT '',total_invested REAL,
                       instrument_type TEXT NOT NULL DEFAULT 'etf',valuation_enabled INTEGER DEFAULT 1,
                       quantity REAL DEFAULT 1,price_symbol TEXT DEFAULT 'TEST',
                       UNIQUE(user_id,instrument_key))"""
            )
            api.ensure_app_etf_position_plans_table(conn)
            api.ensure_investment_contribution_schema(conn)
            for key in ("one", "two"):
                hid = conn.execute(
                    """INSERT INTO portfolio_holdings
                       (user_id,instrument_key,instrument_label,total_invested)
                       VALUES (1,?,?,1000)""", (key, key)
                ).lastrowid
                conn.execute(
                    """INSERT INTO app_etf_position_plans
                       (user_id,holding_id,monthly_amount,execution_day,source_account,mode,active,start_month)
                       VALUES (1,?,300,15,'giro','auto',1,'2020-01')""", (hid,)
                )
            conn.execute(
                """INSERT INTO investment_events
                   (user_id,amount,direction,asset_type,asset_name,event_type,source,created_at)
                   VALUES (1,300,'in','etf','ETF-Sparplan','recurring_plan','app_etf_plan',?)""",
                (f"{datetime.now():%Y-%m}-15 10:00:00",),
            )
            conn.commit()
        ambiguous = run_repair(ambiguous_path, apply=False)
        self.assertEqual(ambiguous["summary"]["blocked"], 1)
        self.assertEqual(ambiguous["candidates"][0]["reason"], "ambiguous_position_plans")

    def test_schema_migration_is_backup_first_and_idempotent(self):
        schema_path = Path(self.temp.name) / "schema.db"
        create_db(schema_path)
        dry = run_schema_migration(schema_path, apply=False)
        first = run_schema_migration(schema_path, apply=True)
        second = run_schema_migration(schema_path, apply=True)
        self.assertFalse(dry["holding_id_after"])
        self.assertTrue(first["changed"])
        self.assertTrue(Path(first["backup"]).is_file())
        self.assertFalse(second["changed"])
        self.assertEqual(second["backup"], "")
        self.assertEqual(second["integrity_check"], "ok")
        self.assertEqual(second["foreign_key_errors"], 0)


if __name__ == "__main__":
    unittest.main()
