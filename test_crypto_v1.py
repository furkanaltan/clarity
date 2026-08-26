from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import io
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_market_data as market
from rove_app_state import _crypto_holdings_value, _crypto_positions, _etf_positions


def create_crypto_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, current_cash REAL DEFAULT 0,
                current_investments REAL DEFAULT 0
            );
            CREATE TABLE portfolio_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                instrument_key TEXT NOT NULL, instrument_label TEXT NOT NULL,
                isin TEXT NOT NULL DEFAULT '', price_symbol TEXT,
                monthly_contribution REAL NOT NULL DEFAULT 0,
                total_invested REAL, start_price REAL, last_price REAL,
                last_checked_at TEXT, started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, instrument_key)
            );
            CREATE TABLE investment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                amount REAL NOT NULL, direction TEXT NOT NULL, asset_type TEXT,
                asset_name TEXT, event_type TEXT, source TEXT, note TEXT, holding_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO users VALUES (1, 5000, 1000);
            INSERT INTO users VALUES (2, 9000, 0);
            INSERT INTO investment_events
                (user_id, amount, direction, asset_type, asset_name, event_type, source)
            VALUES (1, 1000, 'in', 'crypto', 'Krypto', 'initial_balance', 'app_onboarding');
        """)
        market.ensure_market_tracking_schema(conn)
        conn.commit()


def quote(asset_id: str, price: float, symbol: str = "BTC") -> dict:
    return {
        asset_id: {
            "name": {"BTC": "Bitcoin", "ETH": "Ethereum"}.get(symbol, symbol),
            "symbol": symbol, "resolved_symbol": symbol, "currency": "EUR",
            "native_price": price, "eur_price": price,
            "provider": "coinmarketcap", "provider_asset_id": asset_id,
        }
    }


class CryptoV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        create_crypto_db(self.db_path)
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "user_from_token", lambda _conn, token: {"one": 1, "two": 2}.get(token)),
            patch.object(api, "build_live_app_data", lambda _conn, _uid: {"assets": []}),
            patch.object(api, "fetch_crypto_eur_quotes", lambda ids: quote(
                str(ids[0]), 50000, "ETH" if str(ids[0]) == "1027" else "BTC"
            )),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(self, method: str, path: str, token: str = "one", json=None):
        with api.app.test_client() as client:
            return client.open(path, method=method, json=json, headers={
                "Authorization": f"Bearer {token}", "Origin": "https://getrove.de",
            })

    @staticmethod
    def payload(**extra):
        return {
            "providerAssetId": "1", "name": "Bitcoin", "symbol": "BTC",
            "quantity": 0.1, "costBasis": 4000, **extra,
        }

    def values(self, user_id=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(
                "SELECT current_cash,current_investments FROM users WHERE user_id=?", (user_id,)
            ).fetchone()

    def test_create_optional_cost_basis_and_duplicate_coin_preserve_cash(self):
        before = self.values()
        response = self.request("POST", "/v1/crypto/positions", json=self.payload())
        self.assertEqual(response.status_code, 200, response.get_json())
        after = self.values()
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], 6000)
        duplicate = self.request("POST", "/v1/crypto/positions", json=self.payload(quantity=0.2))
        self.assertEqual(duplicate.status_code, 409)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT quantity,total_invested,market_value,provider_asset_id FROM portfolio_holdings"
            ).fetchone()
            self.assertEqual(row, (0.1, 4000, 5000, "1"))

    def test_preview_calculates_market_value_without_write(self):
        before = self.values()
        response = self.request("POST", "/v1/crypto/preview", json=self.payload())
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["marketValue"], 5000)
        self.assertEqual(response.get_json()["profitLoss"], 1000)
        self.assertEqual(self.values(), before)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM portfolio_holdings").fetchone()[0], 0)

    def test_edit_delete_and_user_isolation(self):
        created = self.request("POST", "/v1/crypto/positions", json=self.payload()).get_json()
        holding_id = created["holdingId"]
        foreign = self.request("PATCH", f"/v1/crypto/positions/{holding_id}", token="two", json={
            "quantity": 0.2, "costBasis": None,
        })
        self.assertEqual(foreign.status_code, 404)
        edited = self.request("PATCH", f"/v1/crypto/positions/{holding_id}", json={
            "quantity": 0.2, "costBasis": None,
        })
        self.assertEqual(edited.status_code, 200, edited.get_json())
        self.assertEqual(self.values(), (5000, 11000))
        deleted = self.request("DELETE", f"/v1/crypto/positions/{holding_id}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertEqual(self.values(), (5000, 1000))

    def test_legacy_crypto_can_be_corrected_and_removed_without_touching_cash(self):
        corrected = self.request("POST", "/v1/investments", json={
            "asset_type": "crypto", "asset_name": "Krypto", "value": 750,
        })
        self.assertEqual(corrected.status_code, 200, corrected.get_json())
        self.assertEqual(self.values(), (5000, 750))
        foreign = self.request("DELETE", "/v1/investments", token="two", json={
            "asset_type": "crypto", "asset_name": "Krypto",
        })
        self.assertEqual(foreign.status_code, 404)
        removed = self.request("DELETE", "/v1/investments", json={
            "asset_type": "crypto", "asset_name": "Krypto",
        })
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(self.values(), (5000, 0))

    def test_one_of_five_legacy_crypto_rows_can_be_removed_without_touching_the_others(self):
        legacy_rows = (
            ("Krypto", 1000),
            ("Sonic", 200),
            ("Alchemy Pay", 300),
            ("Kaspa", 400),
            ("Chainlink", 500),
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM investment_events WHERE user_id=1")
            conn.executemany(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, event_type, source)
                   VALUES (1, ?, 'in', 'crypto', ?, 'initial_balance', 'app_onboarding')""",
                [(amount, name) for name, amount in legacy_rows],
            )
            conn.execute(
                "UPDATE users SET current_investments=? WHERE user_id=1",
                (sum(amount for _name, amount in legacy_rows),),
            )
            conn.commit()

        before_cash = self.values()[0]
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            positions = _crypto_positions(conn, 1)
        sonic = next(
            position for position in positions
            if position.get("legacyLabel") == "Sonic"
        )
        self.assertIsInstance(sonic["legacyRef"], int)

        removed = self.request(
            "DELETE", f"/v1/crypto/legacy/{sonic['legacyRef']}"
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(self.values(), (before_cash, 2200))

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            positions = _crypto_positions(conn, 1)
        self.assertEqual(
            {position["legacyLabel"] for position in positions if position.get("legacy")},
            {"Krypto", "Alchemy Pay", "Kaspa", "Chainlink"},
        )

    def test_legacy_crypto_with_blank_name_can_be_removed(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM investment_events WHERE user_id=1")
            conn.execute(
                """INSERT INTO investment_events
                       (user_id, amount, direction, asset_type, asset_name, event_type, source)
                   VALUES (1, 125, 'in', 'crypto', '', 'initial_balance', 'telegram_legacy')"""
            )
            conn.execute("UPDATE users SET current_investments=125 WHERE user_id=1")
            conn.commit()

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            position = next(p for p in _crypto_positions(conn, 1) if p.get("legacy"))

        removed = self.request("DELETE", f"/v1/crypto/legacy/{position['legacyRef']}")
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(self.values(), (5000, 0))

    def test_legacy_delete_commits_cookie_session_touch_before_write_lock(self):
        def cookie_authenticated_user(conn, _token):
            # Mirrors the production session's last_seen_at update, which opens a
            # deferred SQLite transaction before the finance mutation starts.
            conn.execute("UPDATE users SET current_cash=current_cash WHERE user_id=1")
            return 1

        with patch.object(api, "user_from_token", cookie_authenticated_user):
            removed = self.request("DELETE", "/v1/crypto/legacy/1")

        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual(self.values(), (5000, 0))

    def test_legacy_and_tracked_crypto_are_separate_without_etf_double_counting(self):
        self.request("POST", "/v1/crypto/positions", json=self.payload())
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertEqual(_crypto_holdings_value(conn, 1), 6000)
            positions = _crypto_positions(conn, 1)
            self.assertEqual(len(positions), 2)
            self.assertTrue(any(row.get("legacy") for row in positions))
            self.assertTrue(any(row.get("providerAssetId") == "1" for row in positions))
            self.assertEqual(_etf_positions(conn, 1), [])

    def test_profit_loss_and_unknown_cost_basis_are_safe(self):
        self.request("POST", "/v1/crypto/positions", json=self.payload())
        self.request("POST", "/v1/crypto/positions", json={
            "providerAssetId": "1027", "name": "Ethereum", "symbol": "ETH",
            "quantity": 0.1,
        })
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            positions = {row["symbol"]: row for row in _crypto_positions(conn, 1) if row.get("symbol")}
            self.assertEqual(positions["BTC"]["profitLoss"], 1000)
            self.assertIsNone(positions["ETH"]["costBasis"])
            self.assertIsNone(positions["ETH"]["profitLoss"])

    def test_screenshot_commit_is_explicit_idempotent_and_requires_quantity(self):
        bad = self.request("POST", "/v1/crypto/import/screenshot/commit", json={"positions": [{
            **self.payload(), "quantity": None, "importKey": "a" * 32,
        }]})
        self.assertEqual(bad.status_code, 400)
        payload = {"positions": [{**self.payload(), "importKey": "b" * 32}]}
        first = self.request("POST", "/v1/crypto/import/screenshot/commit", json=payload)
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self.request("POST", "/v1/crypto/import/screenshot/commit", json=payload)
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(len(second.get_json()["skipped"]), 1)
        self.assertEqual(self.values(), (5000, 6000))

    def test_screenshot_analysis_has_editable_preview_and_never_auto_commits(self):
        extracted = {"positions": [
            {"name": "Bitcoin", "symbol": "BTC", "quantity": 0.1, "cost_basis": 4000, "confidence": 0.99},
            {"name": "Ethereum", "symbol": "ETH", "quantity": None, "confidence": 0.8},
        ]}
        assets = [{"providerAssetId": "1", "name": "Bitcoin", "symbol": "BTC", "provider": "coinmarketcap"}]
        with patch.object(api, "request_crypto_screenshot_analysis", return_value=extracted), \
             patch.object(api, "screenshot_attempt_allowed", return_value=True), \
             patch.object(api, "search_crypto_assets", return_value=assets):
            with api.app.test_client() as client:
                response = client.post(
                    "/v1/crypto/import/screenshot",
                    data={"image": (io.BytesIO(b"\x89PNG\r\n\x1a\npreview"), "portfolio.png")},
                    content_type="multipart/form-data",
                    headers={"Authorization": "Bearer one", "Origin": "https://getrove.de"},
                )
        self.assertEqual(response.status_code, 200, response.get_json())
        rows = response.get_json()["positions"]
        self.assertTrue(rows[0]["selected"])
        self.assertFalse(rows[1]["selected"])
        self.assertTrue(rows[1]["missingQuantity"])
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM portfolio_holdings").fetchone()[0], 0)

    def test_screenshot_sonic_resolution_uses_name_before_ambiguous_s_symbol(self):
        extracted = {"positions": [{
            "name": "Sonic", "symbol": "S", "quantity": 100, "confidence": 0.99,
        }]}
        assets = [{
            "providerAssetId": "32684", "name": "Sonic", "symbol": "S",
            "provider": "coinmarketcap",
        }]
        with patch.object(api, "request_crypto_screenshot_analysis", return_value=extracted), \
             patch.object(api, "screenshot_attempt_allowed", return_value=True), \
             patch.object(api, "search_crypto_assets", return_value=assets) as search:
            with api.app.test_client() as client:
                response = client.post(
                    "/v1/crypto/import/screenshot",
                    data={"image": (io.BytesIO(b"\x89PNG\r\n\x1a\nsonic"), "portfolio.png")},
                    content_type="multipart/form-data",
                    headers={"Authorization": "Bearer one", "Origin": "https://getrove.de"},
                )
        self.assertEqual(response.status_code, 200, response.get_json())
        search.assert_called_once_with("Sonic", limit=6)
        self.assertEqual(response.get_json()["positions"][0]["providerAssetId"], "32684")


class CryptoProviderTests(unittest.TestCase):
    def test_coin_search_uses_stable_provider_id_and_is_case_insensitive(self):
        response = {"data": [{"id": 1, "name": "Bitcoin", "symbol": "BTC"}]}
        with patch.object(market, "_cmc_request_json", return_value=response):
            upper = market.search_crypto_assets("BTC")
            lower = market.search_crypto_assets("bitcoin")
        self.assertEqual(upper[0]["providerAssetId"], "1")
        self.assertEqual(lower[0]["symbol"], "BTC")

    def test_short_symbol_and_map_fallback_find_active_cmc_assets(self):
        empty = {"data": []}
        active_map = {"data": [
            {"id": 32684, "name": "Sonic", "symbol": "S", "slug": "sonic"},
            {"id": 6958, "name": "Alchemy Pay", "symbol": "ACH", "slug": "alchemy-pay"},
        ]}
        with patch.object(market, "_cmc_request_json", side_effect=[empty, empty, active_map]) as provider:
            sonic = market.search_crypto_assets("S")
        self.assertEqual(provider.call_args_list[-1].args[1]["limit"], 5000)
        with patch.object(market, "_cmc_request_json", side_effect=[empty, empty, active_map]):
            alchemy = market.search_crypto_assets("ACH")
        self.assertEqual(sonic[0]["symbol"], "S")
        self.assertEqual(alchemy[0]["symbol"], "ACH")

    def test_search_ignores_provider_rows_that_do_not_match_the_query(self):
        ignored_rows = {"data": [
            {"id": 999, "name": "Some Token", "symbol": "S", "slug": "some-token"},
        ]}
        active_map = {"data": [
            {"id": 32684, "name": "Sonic", "symbol": "S", "slug": "sonic"},
        ]}
        with patch.object(
            market, "_cmc_request_json", side_effect=[ignored_rows, ignored_rows, active_map]
        ):
            results = market.search_crypto_assets("Sonic")
        self.assertEqual(results, [{
            "providerAssetId": "32684", "name": "Sonic", "symbol": "S",
            "provider": "coinmarketcap",
        }])

    def test_screenshot_resolution_prefers_coin_name_over_ambiguous_symbol(self):
        rows = api.normalize_crypto_screenshot_rows(
            [{"name": "Sonic", "symbol": "S", "quantity": 100, "confidence": 0.99}],
            1,
            "image",
        )
        self.assertEqual(rows[0]["name"], "Sonic")
        self.assertEqual(rows[0]["symbol"], "S")

    def test_batch_quotes_and_missing_coin(self):
        response = {
            "data": {
                "1": {"symbol": "BTC", "quote": {"EUR": {"price": 50000}}},
                "1027": {"symbol": "ETH", "quote": {"EUR": {"price": 2500}}},
            }
        }
        with patch.object(market, "_cmc_request_json", return_value=response) as provider:
            quotes = market.fetch_crypto_eur_quotes([1, 1027, 999])
        self.assertEqual(set(quotes), {"1", "1027"})
        self.assertEqual(provider.call_count, 1)

    def test_refresh_failure_preserves_old_value_and_other_coin_continues(self):
        temp = tempfile.TemporaryDirectory()
        try:
            path = Path(temp.name) / "clarity.db"
            create_crypto_db(path)
            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                for asset_id, symbol in (("1", "BTC"), ("1027", "ETH")):
                    position = api._clean_crypto_position_payload({
                        "providerAssetId": asset_id, "name": symbol, "symbol": symbol,
                        "quantity": 1, "costBasis": 10,
                    })
                    api._insert_crypto_holding(conn, 1, position, quote(asset_id, 10, symbol)[asset_id])
                conn.commit()
            with patch.object(market, "fetch_crypto_eur_quotes", return_value=quote("1", 20, "BTC")):
                result = market.refresh_all_market_positions(path)
            self.assertEqual(result["updated"], 1, result)
            self.assertEqual(result["failed"], 1)
            with closing(sqlite3.connect(path)) as conn:
                rows = dict(conn.execute("SELECT price_symbol,market_value FROM portfolio_holdings"))
                self.assertEqual(rows, {"BTC": 20, "ETH": 10})
                self.assertEqual(conn.execute("SELECT current_cash FROM users WHERE user_id=1").fetchone()[0], 5000)
        finally:
            temp.cleanup()


class CryptoFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frontend_path = Path(os.environ.get(
            "ROVE_FRONTEND_PATH",
            Path(__file__).resolve().parent.parent / "rove-app" / "index.html",
        ))
        cls.html = frontend_path.read_text(encoding="utf-8")

    def test_mobile_flow_has_search_quantity_preview_edit_delete_and_screenshot(self):
        for marker in (
            "/v1/crypto/search", "cryptoQuantity", "cryptoCostBasis", "/v1/crypto/preview",
            "/v1/crypto/positions/", "cryptoScanFile", "/v1/crypto/import/screenshot/commit",
        ):
            self.assertIn(marker, self.html)

    def test_coinmarketcap_key_is_never_present_in_browser(self):
        self.assertNotIn("COINMARKETCAP_API_KEY", self.html)
        self.assertNotIn("X-CMC_PRO_API_KEY", self.html)

    def test_screenshot_import_commit_stays_in_sticky_safe_area(self):
        self.assertIn('class="scan-actions crypto-import-actions"', self.html)
        self.assertIn('class="scan-confirm" id="cryptoImportCommit"', self.html)
        self.assertIn("selectedRows.some(row=>row.needsCoinSelection)", self.html)

    def test_crypto_positions_and_legacy_value_have_visible_management_actions(self):
        self.assertIn('id="cryptoLegacySave"', self.html)
        self.assertIn('id="cryptoLegacyValue"', self.html)
        self.assertIn("position.legacyRef||null", self.html)
        self.assertIn("deleteLegacyCryptoPosition(position.legacyRef)", self.html)
        self.assertIn('class="asset-position-edit">Bearbeiten', self.html)

    def test_crypto_delete_keeps_the_open_sheet_scroll_position(self):
        self.assertIn("function refreshOpenAssetDetail(i, preserveScroll=false)", self.html)
        self.assertIn("refreshOpenAssetDetail(idx,true)", self.html)


if __name__ == "__main__":
    unittest.main()
