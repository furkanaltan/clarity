from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as app_state


def create_regression_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                onboarding_step INTEGER NOT NULL DEFAULT 10,
                current_investments REAL NOT NULL DEFAULT 0,
                etf_savings REAL NOT NULL DEFAULT 0,
                cash_savings REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE user_access (
                user_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );

            CREATE TABLE portfolio_holdings (
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
                last_checked_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                instrument_type TEXT NOT NULL DEFAULT 'etf',
                quantity REAL,
                quote_currency TEXT,
                market_value REAL,
                market_value_updated_at TEXT,
                market_data_provider TEXT,
                valuation_enabled INTEGER NOT NULL DEFAULT 0,
                provider_asset_id TEXT,
                position_source TEXT,
                import_key TEXT,
                UNIQUE(user_id, instrument_key),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE investment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                direction TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                event_type TEXT,
                source TEXT,
                note TEXT,
                holding_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY(holding_id) REFERENCES portfolio_holdings(id) ON DELETE SET NULL
            );

            INSERT INTO users (user_id, current_investments) VALUES (1, 500);
            INSERT INTO user_access (user_id, status) VALUES (1, 'approved');
            """
        )
        api.ensure_auth_tables(conn)
        api.ensure_session_pin_table(conn)
        conn.commit()


class InvestmentPositionDeleteRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_regression_db(self.db_path)
        self.raw_token = "investment-delete-regression-session"
        self.original_app_config = dict(api.app.config)

        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "AUTH_SECRET", "investment-delete-regression-secret"),
            patch.object(api, "build_live_app_data", return_value={"assets": []}),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
        self._create_unlocked_cookie_session()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        api.app.config.clear()
        api.app.config.update(self.original_app_config)
        self.temp.cleanup()

    def _create_unlocked_cookie_session(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            account = conn.execute(
                """INSERT INTO app_accounts
                       (email, user_id, verified_at, source)
                       VALUES (?, 1, CURRENT_TIMESTAMP, 'app')""",
                ("investment-delete@example.test",),
            )
            account_id = int(account.lastrowid)
            session = conn.execute(
                """INSERT INTO app_sessions (token_hash, account_id, expires_at)
                   VALUES (?, ?, ?)""",
                (
                    api.keyed_hash(self.raw_token),
                    account_id,
                    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            self.session_id = int(session.lastrowid)
            conn.execute(
                """INSERT INTO app_session_pins
                       (session_id, pin_verifier, unlocked_at, last_activity_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    self.session_id,
                    api.PASSWORD_HASHER.hash(
                        api.pin_secret_value(self.session_id, "1234")
                    ),
                ),
            )
            conn.commit()

    def request(self, method: str, path: str, *, json=None):
        with api.app.test_client() as client:
            client.set_cookie(
                api.SESSION_COOKIE_NAME,
                self.raw_token,
                domain="localhost",
                path="/",
            )
            return client.open(path, method=method, json=json)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def seed_tracked_crypto(self, holdings=(1000.0,), *, legacy=0.0) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                "UPDATE users SET current_investments=? WHERE user_id=1",
                (sum(holdings) + legacy,),
            )
            for index, value in enumerate(holdings):
                conn.execute(
                    """INSERT INTO portfolio_holdings
                           (user_id, instrument_key, instrument_label, total_invested,
                            instrument_type, quantity, quote_currency, market_value,
                            market_data_provider, valuation_enabled, provider_asset_id)
                       VALUES (1, ?, ?, ?, 'crypto', 1, 'EUR', ?, 'coinmarketcap', 1, ?)""",
                    (f"crypto_{index}", f"Coin {index}", value, value, str(index + 1)),
                )
            if legacy:
                conn.execute(
                    """INSERT INTO investment_events
                           (user_id, amount, direction, asset_type, asset_name, event_type, source)
                       VALUES (1, ?, 'in', 'crypto', 'Legacy coin', 'initial_balance', 'app_onboarding')""",
                    (legacy,),
                )
            conn.commit()

    def current_investments(self) -> float:
        with closing(self.connect()) as conn:
            return float(conn.execute(
                "SELECT current_investments FROM users WHERE user_id=1"
            ).fetchone()[0])

    def assert_crypto_basis(self, expected: float) -> None:
        with closing(self.connect()) as conn:
            self.assertAlmostEqual(api._crypto_holdings_value(conn, 1), expected, places=2)

    def non_crypto_detail_total(self) -> float:
        with closing(self.connect()) as conn:
            return sum(
                float(position.get("v") or 0)
                for position in app_state._etf_positions(conn, 1)
            )

    def test_new_stock_position_can_be_deleted_through_real_cookie_auth(self):
        created = self.request(
            "POST",
            "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie", "value": 200},
        )
        self.assertEqual(created.status_code, 200, created.get_json())

        with closing(self.connect()) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM investment_events WHERE user_id = 1"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM portfolio_holdings WHERE user_id = 1"
                ).fetchone()[0],
                0,
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT last_seen_at FROM app_sessions WHERE id = ?",
                    (self.session_id,),
                ).fetchone()[0]
            )

        removed = self.request(
            "DELETE",
            "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie"},
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())

        with closing(self.connect()) as conn:
            net = conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction = 'out' THEN -amount ELSE amount END), 0)
                     FROM investment_events
                    WHERE user_id = 1 AND asset_type = 'stock'
                      AND asset_name = 'Regression Aktie'"""
            ).fetchone()[0]
            self.assertAlmostEqual(float(net), 0.0, places=2)
            self.assertAlmostEqual(
                float(
                    conn.execute(
                        "SELECT current_investments FROM users WHERE user_id = 1"
                    ).fetchone()[0]
                ),
                300.0,
                places=2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM portfolio_holdings WHERE user_id = 1"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    """SELECT COUNT(*)
                         FROM investment_events e
                         LEFT JOIN portfolio_holdings h ON h.id = e.holding_id
                        WHERE e.holding_id IS NOT NULL AND h.id IS NULL"""
                ).fetchone()[0],
                0,
            )
            self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])

        repeated = self.request(
            "DELETE",
            "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie"},
        )
        self.assertEqual(repeated.status_code, 404, repeated.get_json())
        self.assertEqual(repeated.get_json().get("error"), "manual_investment_not_found")

    def test_tracked_crypto_is_preserved_when_stock_is_added_updated_and_deleted(self):
        self.seed_tracked_crypto()
        self.assert_crypto_basis(1000)

        created = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie", "value": 500},
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.assertEqual(self.current_investments(), 1500)
        self.assertEqual(self.non_crypto_detail_total(), 500)

        updated = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie", "value": 800},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(self.current_investments(), 1800)
        self.assertEqual(self.non_crypto_detail_total(), 800)

        reduced = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie", "value": 300},
        )
        self.assertEqual(reduced.status_code, 200, reduced.get_json())
        self.assertEqual(self.current_investments(), 1300)
        self.assertEqual(self.non_crypto_detail_total(), 300)

        removed = self.request(
            "DELETE", "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie"},
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(self.current_investments(), 1000)
        self.assertEqual(self.non_crypto_detail_total(), 0)
        self.assert_crypto_basis(1000)

    def test_tracked_crypto_is_preserved_when_etf_is_added_updated_and_deleted(self):
        self.seed_tracked_crypto()

        created = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "etf", "asset_name": "Regression ETF", "value": 500},
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.assertEqual(self.current_investments(), 1500)
        self.assertEqual(self.non_crypto_detail_total(), 500)

        updated = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "etf", "asset_name": "Regression ETF", "value": 700},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(self.current_investments(), 1700)
        self.assertEqual(self.non_crypto_detail_total(), 700)

        removed = self.request(
            "DELETE", "/v1/investments",
            json={"asset_type": "etf", "asset_name": "Regression ETF"},
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(self.current_investments(), 1000)
        self.assertEqual(self.non_crypto_detail_total(), 0)
        self.assert_crypto_basis(1000)

    def test_multiple_tracked_and_legacy_crypto_values_form_one_read_write_basis(self):
        self.seed_tracked_crypto((600.0, 400.0), legacy=200.0)
        self.assert_crypto_basis(1200)

        created = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Regression Aktie", "value": 500},
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.assertEqual(self.current_investments(), 1700)
        self.assert_crypto_basis(1200)
        self.assertEqual(self.non_crypto_detail_total(), 500)

        with closing(self.connect()) as conn:
            stock_value = float(conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN direction='out' THEN -amount ELSE amount END), 0)
                     FROM investment_events WHERE user_id=1 AND asset_type='stock'"""
            ).fetchone()[0])
        self.assertAlmostEqual(stock_value, 500, places=2)

    def test_new_stock_consumes_only_the_existing_non_crypto_remainder(self):
        self.seed_tracked_crypto()
        with closing(self.connect()) as conn:
            conn.execute("UPDATE users SET current_investments=1500 WHERE user_id=1")
            conn.commit()

        created = self.request(
            "POST", "/v1/investments",
            json={"asset_type": "stock", "asset_name": "Restbestand Aktie", "value": 200},
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.assertEqual(self.current_investments(), 1500)
        self.assertEqual(self.non_crypto_detail_total(), 200)
        self.assert_crypto_basis(1000)
        remaining_unassigned = self.current_investments() - 1000 - self.non_crypto_detail_total()
        self.assertEqual(remaining_unassigned, 300)


if __name__ == "__main__":
    unittest.main()
