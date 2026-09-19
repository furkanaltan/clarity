from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import refresh_market_positions as worker
import rove_app_api as api
import rove_market_data as market
from test_crypto_v1 import create_crypto_db, quote


def add_holding(
    path: Path,
    *,
    holding_id: int,
    instrument_key: str,
    label: str,
    symbol: str,
    instrument_type: str,
    provider: str,
    provider_asset_id: str | None,
    value: float,
) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """INSERT INTO portfolio_holdings
               (id, user_id, instrument_key, instrument_label, isin, price_symbol,
                monthly_contribution, total_invested, start_price, last_price,
                market_value, market_data_provider, valuation_enabled, quantity,
                quote_currency, instrument_type, provider_asset_id)
               VALUES (?, 1, ?, ?, '', ?, 0, ?, ?, ?, ?, ?, 1, 1, 'EUR', ?, ?)""",
            (
                holding_id, instrument_key, label, symbol, value, value, value,
                value, provider, instrument_type, provider_asset_id,
            ),
        )
        conn.commit()


class MarketRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "clarity.db"
        create_crypto_db(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_crypto_refresh_updates_value_and_exposes_run_metrics(self):
        add_holding(
            self.path,
            holding_id=1,
            instrument_key="crypto:1",
            label="Bitcoin",
            symbol="BTC",
            instrument_type="crypto",
            provider="coinmarketcap",
            provider_asset_id="1",
            value=10,
        )
        with patch.object(market, "fetch_crypto_eur_quotes", return_value=quote("1", 20, "BTC")):
            result = market.refresh_all_market_positions(self.path)

        self.assertEqual(result["positions_considered"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["provider_failures"], {})
        self.assertTrue(result["started_at"])
        self.assertTrue(result["finished_at"])
        self.assertGreaterEqual(result["duration_seconds"], 0)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT market_value,last_price,market_value_updated_at,last_checked_at "
                    "FROM portfolio_holdings WHERE id=1"
                ).fetchone(),
                (20.0, 20.0, unittest.mock.ANY, unittest.mock.ANY),
            )

    def test_cmc_failure_keeps_old_crypto_value_and_does_not_block_stock(self):
        add_holding(
            self.path,
            holding_id=1,
            instrument_key="crypto:1",
            label="Bitcoin",
            symbol="BTC",
            instrument_type="crypto",
            provider="coinmarketcap",
            provider_asset_id="1",
            value=10,
        )
        add_holding(
            self.path,
            holding_id=2,
            instrument_key="stock:1",
            label="Test ETF",
            symbol="TEST",
            instrument_type="etf",
            provider="twelve_data",
            provider_asset_id=None,
            value=100,
        )
        stock_quote = {
            "symbol": "TEST",
            "resolved_symbol": "TEST",
            "currency": "EUR",
            "native_price": 110,
            "eur_price": 110,
            "provider": "twelve_data",
        }
        with patch.object(
            market,
            "fetch_crypto_eur_quotes",
            side_effect=RuntimeError("X-CMC_PRO_API_KEY=secret"),
        ), patch.object(market, "fetch_eur_quote", return_value=stock_quote):
            result = market.refresh_all_market_positions(self.path)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["provider_failures"], {"coinmarketcap": 1})
        self.assertNotIn("secret", json.dumps(result))
        with closing(sqlite3.connect(self.path)) as conn:
            rows = dict(conn.execute(
                "SELECT price_symbol,market_value FROM portfolio_holdings ORDER BY id"
            ).fetchall())
            self.assertEqual(rows, {"BTC": 10.0, "TEST": 110.0})

    def test_missing_quote_does_not_write_zero_or_mark_position_updated(self):
        add_holding(
            self.path,
            holding_id=1,
            instrument_key="crypto:1",
            label="Bitcoin",
            symbol="BTC",
            instrument_type="crypto",
            provider="coinmarketcap",
            provider_asset_id="1",
            value=10,
        )
        with patch.object(market, "fetch_crypto_eur_quotes", return_value={}):
            result = market.refresh_all_market_positions(self.path)

        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["provider_failures"], {"coinmarketcap": 1})
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT market_value,last_price,market_value_updated_at "
                    "FROM portfolio_holdings WHERE id=1"
                ).fetchone(),
                (10.0, 10.0, None),
            )


class MarketRefreshWiringTests(unittest.TestCase):
    def test_worker_entrypoint_calls_canonical_refresh_function(self):
        result = {
            "started_at": "2026-09-18T00:00:00+00:00",
            "finished_at": "2026-09-18T00:00:01+00:00",
            "duration_seconds": 1.0,
            "positions_considered": 2,
            "updated": 2,
            "failed": 0,
            "provider_failures": {},
            "failures": [],
            "total": 2,
        }
        with patch.object(worker, "refresh_all_market_positions", return_value=result) as refresh:
            with patch.object(sys, "argv", ["refresh_market_positions.py", "--db", "/tmp/test.db"]):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(worker.main(), 0)
        refresh.assert_called_once_with("/tmp/test.db")
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["positions_considered"], 2)
        self.assertNotIn("failures", payload)

    def test_canonical_systemd_wiring_prevents_orphaned_refresh_function(self):
        root = Path(__file__).resolve().parent
        wrapper = (root / "refresh_market_positions.py").read_text(encoding="utf-8")
        service = (root / "deploy/systemd/rove-market-refresh.service").read_text(encoding="utf-8")
        timer = (root / "deploy/systemd/rove-market-refresh.timer").read_text(encoding="utf-8")
        self.assertIn("refresh_all_market_positions", wrapper)
        self.assertIn("refresh_market_positions.py --db /root/clarity/clarity.db", service)
        self.assertIn("Unit=rove-market-refresh.service", timer)
        self.assertIn("OnCalendar=*-*-* 22:30:00 Europe/Berlin", timer)


class MarketHealthTests(unittest.TestCase):
    def test_health_exposes_provider_configuration_without_changing_legacy_flag(self):
        with patch.dict(
            os.environ,
            {
                "TWELVE_DATA_API_KEY": "stock-key",
                "COINMARKETCAP_API_KEY": "crypto-key",
                "LEEWAY_API_TOKEN": "europe-token",
            },
            clear=False,
        ):
            with api.app.test_request_context("/health"):
                payload = api.health().get_json()
        self.assertTrue(payload["marketDataConfigured"])
        self.assertTrue(payload["stockMarketDataConfigured"])
        self.assertTrue(payload["cryptoMarketDataConfigured"])
        self.assertTrue(payload["europeMarketDataConfigured"])


if __name__ == "__main__":
    unittest.main()
