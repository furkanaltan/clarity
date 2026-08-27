from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
from rove_app_state import build_live_app_data
from rove_financial_accounts import (
    FEATURE_MULTI_CASH_ACCOUNTS_V1,
    get_legacy_financial_account,
    set_feature_enabled,
)
from test_financial_accounts_sprint2 import create_db


class Sprint3FinancialAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_db(self.db_path)
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "user_from_token", lambda _conn, token: {"pilot-token": 1, "other-token": 2}.get(token)),
            patch.object(api, "build_live_app_data", lambda _c, _u: {"sts": {"available": 0}}),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(self, method: str, path: str, *, token="pilot-token", json=None):
        with api.app.test_client() as client:
            return client.open(path, method=method, json=json, headers={
                "Authorization": f"Bearer {token}", "Origin": "https://getrove.de",
            })

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create(self, account_type="checking", name="Testkonto", balance=0):
        response = self.request("POST", "/v1/financial-accounts", json={
            "type": account_type, "name": name, "balance": balance,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return int(response.get_json()["accountId"])

    def assert_invariants(self):
        with closing(self.connect()) as conn:
            current = float(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0])
            total = float(conn.execute("SELECT SUM(balance) FROM app_financial_accounts WHERE user_id=1").fetchone()[0])
            self.assertAlmostEqual(current, total, places=2)
            for account_type, legacy_key in (("checking", "giro"), ("savings", "tagesgeld"), ("wallet", "bargeld")):
                typed = float(conn.execute(
                    "SELECT COALESCE(SUM(balance),0) FROM app_financial_accounts WHERE user_id=1 AND account_type=?",
                    (account_type,),
                ).fetchone()[0])
                legacy = float(conn.execute(
                    "SELECT amount FROM app_account_balances WHERE user_id=1 AND account_key=?",
                    (legacy_key,),
                ).fetchone()[0])
                self.assertAlmostEqual(typed, legacy, places=2)

    def test_create_all_types_duplicate_names_and_balances(self):
        self.create("checking", "C24", -50)
        self.create("checking", "C24", 100)
        self.create("savings", "Reserve", 200)
        self.create("wallet", "Reisekasse", 30)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_financial_accounts WHERE user_id=1 AND name='C24'"
            ).fetchone()[0], 2)
        self.assert_invariants()

    def test_negative_savings_and_wallet_are_rejected(self):
        for account_type in ("savings", "wallet"):
            response = self.request("POST", "/v1/financial-accounts", json={
                "type": account_type, "name": "Nicht erlaubt", "balance": -1,
            })
            self.assertEqual(response.status_code, 400)

    def test_rename_and_set_balance_are_user_bound(self):
        account_id = self.create(balance=10)
        renamed = self.request("PATCH", f"/v1/financial-accounts/{account_id}", json={
            "action": "rename", "name": "  C24 Hauptkonto  ",
        })
        self.assertEqual(renamed.status_code, 200)
        changed = self.request("PATCH", f"/v1/financial-accounts/{account_id}", json={
            "action": "set_balance", "balance": -75,
        })
        self.assertEqual(changed.status_code, 200)
        foreign = self.request("PATCH", f"/v1/financial-accounts/{account_id}", token="other-token", json={
            "action": "rename", "name": "Fremd",
        })
        self.assertIn(foreign.status_code, (404,))
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT name,balance FROM app_financial_accounts WHERE id=?", (account_id,)).fetchone()
            self.assertEqual(row["name"], "C24 Hauptkonto")
            self.assertEqual(float(row["balance"]), -75)
        self.assert_invariants()

    def test_transfer_writes_concrete_movement_and_preserves_total(self):
        source = self.create("checking", "Quelle", 300)
        target = self.create("savings", "Ziel", 20)
        with closing(self.connect()) as conn:
            before = float(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0])
        response = self.request("POST", "/v1/financial-accounts/transfer", json={
            "sourceAccountId": source, "targetAccountId": target, "amount": 125,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            after = float(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0])
            movement = conn.execute(
                "SELECT kind,source_account_id,target_account_id FROM app_cash_movements ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual((movement["kind"], movement["source_account_id"], movement["target_account_id"]),
                             ("transfer", source, target))
        self.assertEqual(before, after)
        self.assert_invariants()

    def test_parallel_transfers_do_not_lose_updates(self):
        source = self.create("checking", "Quelle", 300)
        target = self.create("savings", "Ziel", 0)
        statuses: list[int] = []

        def transfer() -> None:
            response = self.request("POST", "/v1/financial-accounts/transfer", json={
                "sourceAccountId": source, "targetAccountId": target, "amount": 75,
            })
            statuses.append(response.status_code)

        threads = [threading.Thread(target=transfer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(statuses), [200, 200])
        with closing(self.connect()) as conn:
            balances = {
                int(row["id"]): float(row["balance"])
                for row in conn.execute(
                    "SELECT id,balance FROM app_financial_accounts WHERE id IN (?,?)",
                    (source, target),
                )
            }
            self.assertEqual(balances[source], 150)
            self.assertEqual(balances[target], 150)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_cash_movements WHERE kind='transfer'"
            ).fetchone()[0], 2)
        self.assert_invariants()

    def test_transfer_rejects_insufficient_and_foreign_accounts(self):
        source = self.create("savings", "Quelle", 10)
        target = self.create("checking", "Ziel", 0)
        denied = self.request("POST", "/v1/financial-accounts/transfer", json={
            "sourceAccountId": source, "targetAccountId": target, "amount": 11,
        })
        self.assertEqual(denied.status_code, 400)
        foreign = self.request("POST", "/v1/financial-accounts/transfer", token="other-token", json={
            "sourceAccountId": source, "targetAccountId": target, "amount": 1,
        })
        self.assertEqual(foreign.status_code, 404)

    def test_checking_transfer_may_use_overdraft(self):
        source = self.create("checking", "Giro", 10)
        target = self.create("savings", "Reserve", 0)
        response = self.request("POST", "/v1/financial-accounts/transfer", json={
            "sourceAccountId": source, "targetAccountId": target, "amount": 25,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            rows = {
                int(row["id"]): float(row["balance"])
                for row in conn.execute(
                    "SELECT id,balance FROM app_financial_accounts WHERE id IN (?,?)",
                    (source, target),
                )
            }
        self.assertEqual(rows[source], -15)
        self.assertEqual(rows[target], 25)
        self.assert_invariants()

    def test_legacy_withdrawal_path_keeps_checking_overdraft_for_pilot(self):
        response = self.request("POST", "/v1/accounts", json={
            "action": "transfer", "from": "giro", "to": "bargeld",
            "amount": 1100, "log": "withdrawal",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            giro = get_legacy_financial_account(conn, 1, "giro")
            wallet = get_legacy_financial_account(conn, 1, "bargeld")
            self.assertEqual(float(giro["balance"]), -100)
            self.assertEqual(float(wallet["balance"]), 1150)
            movement = conn.execute(
                "SELECT source_account_id,target_account_id FROM app_cash_movements "
                "WHERE kind='withdrawal' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual((movement[0], movement[1]), (giro["id"], wallet["id"]))
        self.assert_invariants()

    def test_roles_accept_checking_only_and_move_default(self):
        checking = self.create("checking", "Standard", 0)
        savings = self.create("savings", "Reserve", 0)
        for role in ("expense", "income", "fixed_cost", "screenshot"):
            response = self.request("POST", "/v1/financial-account-roles", json={
                "role": role, "accountId": checking,
            })
            self.assertEqual(response.status_code, 200, response.get_json())
        denied = self.request("POST", "/v1/financial-account-roles", json={
            "role": "expense", "accountId": savings,
        })
        self.assertEqual(denied.status_code, 400)

    def test_archive_requires_zero_and_no_roles(self):
        account_id = self.create("checking", "Alt", 5)
        blocked = self.request("PATCH", f"/v1/financial-accounts/{account_id}", json={"action": "archive"})
        self.assertEqual(blocked.status_code, 400)
        self.request("PATCH", f"/v1/financial-accounts/{account_id}", json={"action": "set_balance", "balance": 0})
        archived = self.request("PATCH", f"/v1/financial-accounts/{account_id}", json={"action": "archive"})
        self.assertEqual(archived.status_code, 200, archived.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT status FROM app_financial_accounts WHERE id=?", (account_id,)).fetchone()[0], "archived")

    def test_dynamic_asset_order_accepts_ids_and_rejects_foreign_id(self):
        account_id = self.create("checking", "Sortiert", 0)
        response = self.request("POST", "/v1/asset-order", json={
            "order": [f"cash-account:{account_id}", "asset:investments"],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        denied = self.request("POST", "/v1/asset-order", json={
            "order": ["cash-account:999999"],
        })
        self.assertEqual(denied.status_code, 400)

    def test_etf_plan_accepts_concrete_checking_or_savings_not_wallet(self):
        checking = self.create("checking", "ETF Quelle", 100)
        wallet = self.create("wallet", "Bar", 100)
        ok = self.request("POST", "/v1/profile", json={"etf_plan": {
            "execution_day": 31, "source_account": "giro", "source_account_id": checking,
            "mode": "confirm", "active": True,
        }})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        denied = self.request("POST", "/v1/profile", json={"etf_plan": {
            "execution_day": 31, "source_account": "giro", "source_account_id": wallet,
            "mode": "confirm", "active": True,
        }})
        self.assertEqual(denied.status_code, 400)

    def test_live_state_replaces_static_cash_and_preserves_duplicate_ids(self):
        first = self.create("checking", "Girokonto", 10)
        second = self.create("checking", "Girokonto", 20)
        with closing(self.connect()) as conn:
            state = build_live_app_data(conn, 1)
            ids = [account["id"] for account in state["financialAccounts"]]
            cash_assets = [asset for asset in state["assets"] if asset.get("financialAccountId")]
            self.assertIn(first, ids)
            self.assertIn(second, ids)
            self.assertEqual(len(cash_assets), len(state["financialAccounts"]))
            self.assertTrue(all(asset["assetKey"] == f"cash-account:{asset['financialAccountId']}" for asset in cash_assets))
            self.assertAlmostEqual(
                sum(float(asset["value"]) for asset in cash_assets),
                float(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0]),
                places=2,
            )
            self.assertTrue(state["features"][FEATURE_MULTI_CASH_ACCOUNTS_V1])

            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.commit()
            legacy = build_live_app_data(conn, 1)
            self.assertFalse(legacy["features"][FEATURE_MULTI_CASH_ACCOUNTS_V1])
            self.assertEqual(legacy["financialAccounts"], [])
            self.assertTrue(any(asset["assetKey"] == "cash:giro" for asset in legacy["assets"]))

    def test_live_holding_can_be_removed_without_erasing_market_history(self):
        with closing(self.connect()) as conn:
            conn.execute(
                """CREATE TABLE portfolio_holdings (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       instrument_key TEXT NOT NULL,
                       instrument_label TEXT NOT NULL,
                       isin TEXT NOT NULL,
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
            api.ensure_market_tracking_schema(conn)
            cursor = conn.execute(
                """INSERT INTO portfolio_holdings
                       (user_id, instrument_key, instrument_label, isin, total_invested,
                        market_value, instrument_type, quantity, price_symbol,
                        quote_currency, valuation_enabled)
                   VALUES (1, 'live_test', 'Test ETF', '', 900, 1250, 'etf', 10,
                           'TEST', 'EUR', 1)"""
            )
            holding_id = int(cursor.lastrowid)
            conn.execute("UPDATE users SET current_investments=1500 WHERE user_id=1")
            conn.execute(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, source)
                   VALUES (1, 900, 'in', 'etf', 'Test ETF', 'app'),
                          (1, 350, 'in', 'market', 'Test ETF', 'leeway')"""
            )
            conn.commit()

        response = self.request("DELETE", "/v1/investments", json={
            "asset_type": "etf", "asset_name": "Test ETF", "holding_id": holding_id,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM portfolio_holdings WHERE id=?", (holding_id,)
            ).fetchone()[0], 0)
            self.assertAlmostEqual(float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0]), 250, places=2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM investment_events WHERE source='app' AND asset_name='Test ETF'"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM investment_events WHERE source='leeway' AND asset_name='Test ETF'"
            ).fetchone()[0], 1)

    def test_new_stocks_do_not_consume_live_etf_market_or_pending_value(self):
        with closing(self.connect()) as conn:
            conn.execute(
                """CREATE TABLE portfolio_holdings (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       instrument_key TEXT NOT NULL,
                       instrument_label TEXT NOT NULL,
                       isin TEXT NOT NULL,
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
            api.ensure_market_tracking_schema(conn)
            api.ensure_investment_contribution_schema(conn)
            cursor = conn.execute(
                """INSERT INTO portfolio_holdings
                       (user_id, instrument_key, instrument_label, isin, total_invested,
                        market_value, instrument_type, quantity, price_symbol,
                        quote_currency, valuation_enabled)
                   VALUES (1, 'sp500', 'S&P 500', '', 8349.32, 8671.32, 'etf',
                           66.135209, 'AUM5', 'EUR', 1)"""
            )
            holding_id = int(cursor.lastrowid)
            conn.execute(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, event_type,
                        source, holding_id)
                   VALUES (1, 300, 'in', 'etf', 'S&P 500',
                           'recurring_plan_pending', 'app_etf_plan', ?)""",
                (holding_id,),
            )
            conn.execute("UPDATE users SET current_investments=8971.32 WHERE user_id=1")
            conn.commit()

        under_armour = self.request("POST", "/v1/investments", json={
            "asset_type": "stock", "asset_name": "Under Armour", "value": 1500,
        })
        xpeng = self.request("POST", "/v1/investments", json={
            "asset_type": "stock", "asset_name": "X-Peng", "value": 1000,
        })
        self.assertEqual(under_armour.status_code, 200, under_armour.get_json())
        self.assertEqual(xpeng.status_code, 200, xpeng.get_json())

        with closing(self.connect()) as conn:
            current = float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0])
            stocks = float(conn.execute(
                """SELECT SUM(CASE WHEN direction='out' THEN -amount ELSE amount END)
                     FROM investment_events WHERE user_id=1 AND asset_type='stock'"""
            ).fetchone()[0])
        self.assertAlmostEqual(current, 11471.32, places=2)
        self.assertAlmostEqual(stocks, 2500, places=2)

    def test_legacy_crypto_can_be_removed_with_a_compensating_event(self):
        with closing(self.connect()) as conn:
            conn.execute(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, source)
                   VALUES (1, 120, 'in', 'crypto', 'Bitcoin', 'telegram')"""
            )
            conn.commit()

        response = self.request("DELETE", "/v1/investments", json={
            "asset_type": "crypto", "asset_name": "Bitcoin",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with closing(self.connect()) as conn:
            self.assertAlmostEqual(float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0]), 380, places=2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM investment_events WHERE source='telegram' AND asset_name='Bitcoin'"
            ).fetchone()[0], 1)
            net = float(conn.execute(
                """SELECT SUM(CASE WHEN direction='out' THEN -amount ELSE amount END)
                     FROM investment_events
                    WHERE user_id=1 AND asset_type='crypto' AND asset_name='Bitcoin'"""
            ).fetchone()[0])
            self.assertAlmostEqual(net, 0, places=2)

    def test_complete_crypto_tile_removal_reduces_investment_total_atomically(self):
        with closing(self.connect()) as conn:
            conn.execute(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, source)
                   VALUES (1, 120, 'in', 'crypto', 'Bitcoin', 'telegram'),
                          (1, 80, 'in', 'crypto', 'Ethereum', 'app'),
                          (1, 20, 'out', 'crypto', 'Bitcoin', 'app'),
                          (1, 50, 'in', 'stock', 'Apple', 'app')"""
            )
            conn.commit()

        response = self.request("DELETE", "/v1/investments", json={
            "asset_type": "crypto", "delete_all": True,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["removed"]["positions"], 2)
        self.assertAlmostEqual(response.get_json()["removed"]["value"], 180, places=2)
        with closing(self.connect()) as conn:
            self.assertAlmostEqual(float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0]), 320, places=2)
            crypto_rows = conn.execute(
                """SELECT asset_name,
                          SUM(CASE WHEN direction='out' THEN -amount ELSE amount END) AS net
                     FROM investment_events
                    WHERE user_id=1 AND asset_type='crypto'
                    GROUP BY asset_name"""
            ).fetchall()
            self.assertTrue(crypto_rows)
            self.assertTrue(all(abs(float(row["net"] or 0)) < 0.001 for row in crypto_rows))
            self.assertAlmostEqual(float(conn.execute(
                """SELECT SUM(CASE WHEN direction='out' THEN -amount ELSE amount END)
                     FROM investment_events
                    WHERE user_id=1 AND asset_type='stock'"""
            ).fetchone()[0]), 50, places=2)

    def test_property_delete_is_user_bound(self):
        with closing(self.connect()) as conn:
            api.ensure_app_properties_table(conn)
            conn.execute(
                """INSERT INTO app_properties
                       (user_id, market_value, remaining_debt, monthly_rate, house_fee)
                   VALUES (1, 250000, 175000, 900, 250)"""
            )
            conn.commit()

        foreign = self.request("DELETE", "/v1/property", token="other-token")
        self.assertEqual(foreign.status_code, 200, foreign.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_properties WHERE user_id=1"
            ).fetchone()[0], 1)

        deleted = self.request("DELETE", "/v1/property")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM app_properties WHERE user_id=1"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
