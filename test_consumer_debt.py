import copy
import re
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from datetime import date

import rove_app_api as api
import rove_app_state as state
import report_engine
from report_story_v2 import build_report_story_v2
from rove_web_report_renderer import build_render_context, render_template
from rove_consumer_debt import (
    DEBT_TYPES, ensure_consumer_debt_schema, list_consumer_debts,
    total_consumer_debt, net_worth_total, save_consumer_debt, delete_consumer_debt,
)
from test_financial_accounts_sprint2 import create_db
from test_report_story_v2 import standard_payload
from report_html_renderer import _render_hell_pages

ROOT = Path(__file__).resolve().parent


class ConsumerDebtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        create_db(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("UPDATE app_financial_accounts SET balance=0 WHERE user_id=1")
        self.conn.execute("UPDATE app_financial_accounts SET balance=10000 WHERE user_id=1 AND legacy_key='giro'")
        self.conn.execute("UPDATE users SET current_cash=10000,current_investments=5000 WHERE user_id=1")
        self.conn.commit()

    def payload(self, amount=20000, **kwargs):
        return {"name": "Privatkredit", "debt_type": "personal_loan", "outstanding_balance": amount, **kwargs}

    def test_live_net_worth_once_and_budget_unchanged(self):
        before = state.build_live_app_data(self.conn, 1)
        save_consumer_debt(self.conn, 1, self.payload())
        after = state.build_live_app_data(self.conn, 1)
        self.assertEqual(before["netWorth"], 15000)
        self.assertEqual(after["netWorth"], -5000)
        self.assertEqual(after["consumerDebtTotal"], 20000)
        self.assertEqual(after["sts"], before["sts"])
        self.assertEqual(after["score"], before["score"])

    def test_net_worth_series_keeps_existing_history_without_consumer_debt(self):
        series, labels = state._net_worth_series(self.conn, 1, 15000)
        self.assertEqual(len(series["1W"]), 8)
        self.assertEqual(len(labels["1W"]), 8)

    def test_current_property_equity_does_not_drift_into_old_series_points(self):
        state.ensure_app_properties_table(self.conn)
        self.conn.execute(
            "INSERT INTO app_properties(user_id,market_value,remaining_debt) VALUES (1,180000,171000)"
        )
        series, _ = state._net_worth_series(self.conn, 1, 19000)
        self.assertEqual(series["1W"][:-1], [10.0] * 7)
        self.assertEqual(series["1W"][-1], 19.0)

    def test_property_coverage_timestamp_is_additive_and_stable(self):
        state.ensure_app_properties_table(self.conn)
        self.conn.execute(
            "INSERT INTO app_properties(user_id,market_value,remaining_debt) VALUES (1,180000,171000)"
        )
        state.ensure_app_properties_table(self.conn)
        first = state.get_app_property(self.conn, 1)["coverage_started_at"]
        self.assertTrue(first)
        self.conn.execute("UPDATE app_properties SET market_value=181000 WHERE user_id=1")
        second = state.get_app_property(self.conn, 1)["coverage_started_at"]
        self.assertEqual(first, second)

    def test_property_coverage_timestamp_is_exposed_in_live_state(self):
        state.ensure_app_properties_table(self.conn)
        self.conn.execute(
            "INSERT INTO app_properties(user_id,market_value,remaining_debt,coverage_started_at) "
            "VALUES (1,180000,171000,'2026-09-12 08:00:00')"
        )
        live = state.build_live_app_data(self.conn, 1)
        property_asset = next(asset for asset in live["assets"] if asset["name"] == "Immobilie")
        self.assertEqual(property_asset["real"]["coverageStartedAt"], "2026-09-12 08:00:00")

    def test_property_coverage_timestamp_is_user_scoped(self):
        state.ensure_app_properties_table(self.conn)
        self.conn.execute(
            "INSERT INTO app_properties(user_id,market_value,remaining_debt,coverage_started_at) "
            "VALUES (1,180000,171000,'2026-09-12 08:00:00')"
        )
        self.assertIsNotNone(state.get_app_property(self.conn, 1))
        self.assertIsNone(state.get_app_property(self.conn, 2))

    def test_monthly_snapshot_uses_calendar_month_end(self):
        self.assertEqual(state._report_month_end("2026-08"), date(2026, 8, 31))
        self.assertEqual(state._report_month_end("2026-02"), date(2026, 2, 28))
        self.assertEqual(state._report_month_end("2028-02"), date(2028, 2, 29))

    def test_debt_history_changes_only_from_effective_events(self):
        debt_id = save_consumer_debt(self.conn, 1, self.payload(20000))
        save_consumer_debt(self.conn, 1, self.payload(15000), debt_id)
        today = date.today()
        self.conn.execute(
            "UPDATE app_consumer_debt_events SET effective_at=? WHERE debt_id=? AND event_type='created'",
            ((today - state.timedelta(days=4)).isoformat() + " 12:00:00", debt_id),
        )
        self.conn.execute(
            "UPDATE app_consumer_debt_events SET effective_at=? WHERE debt_id=? AND event_type='updated'",
            ((today - state.timedelta(days=2)).isoformat() + " 12:00:00", debt_id),
        )
        series, _ = state._net_worth_series(self.conn, 1, 0)
        self.assertEqual(series["1W"], [15.0, 15.0, 15.0, -5.0, -5.0, 0.0, 0.0, 0.0])

    def test_legacy_debt_gets_created_at_baseline_without_current_backfill(self):
        ensure_consumer_debt_schema(self.conn)
        debt_id = self.conn.execute(
            """INSERT INTO app_consumer_debts
               (user_id,name,debt_type,outstanding_balance,active,created_at,updated_at)
               VALUES (1,'Alt','personal_loan',20000,1,datetime('now','-4 day'),datetime('now'))"""
        ).lastrowid
        ensure_consumer_debt_schema(self.conn)
        events = self.conn.execute(
            "SELECT event_type, outstanding_balance FROM app_consumer_debt_events WHERE debt_id=?",
            (debt_id,),
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in events], [("legacy_baseline", 20000.0)])

    def test_mortgage_and_legacy_total_are_not_consumer_debt(self):
        state.ensure_app_properties_table(self.conn)
        self.conn.execute("INSERT INTO app_properties(user_id,market_value,remaining_debt) VALUES (1,250000,280000)")
        self.conn.execute('UPDATE users SET fixed_costs_details=? WHERE user_id=1', ('{"kredite":{"restschuld":280000}}',))
        self.assertEqual(total_consumer_debt(self.conn, 1), 0)
        self.assertEqual(state.build_live_app_data(self.conn, 1)["netWorth"], -15000)

    def test_overdraft_is_cash_only(self):
        self.conn.execute("UPDATE app_financial_accounts SET balance=-2000 WHERE user_id=1 AND legacy_key='giro'")
        self.conn.execute("UPDATE users SET current_cash=-2000 WHERE user_id=1")
        self.assertEqual(state.build_live_app_data(self.conn, 1)["netWorth"], 3000)
        for kind in ("overdraft", "mortgage", "car_financing"):
            with self.assertRaises(ValueError):
                save_consumer_debt(self.conn, 1, self.payload(debt_type=kind))

    def test_all_types_sum_inactive_zero_and_user_scoping(self):
        for kind in DEBT_TYPES:
            save_consumer_debt(self.conn, 1, self.payload(10.01, debt_type=kind))
        save_consumer_debt(self.conn, 1, self.payload(0))
        save_consumer_debt(self.conn, 1, self.payload(999, active=False))
        foreign = save_consumer_debt(self.conn, 2, self.payload(4000))
        self.assertEqual(total_consumer_debt(self.conn, 1), 50.05)
        self.assertEqual(total_consumer_debt(self.conn, 2), 4000)
        with self.assertRaises(LookupError):
            save_consumer_debt(self.conn, 1, self.payload(1), foreign)
        with self.assertRaises(LookupError):
            delete_consumer_debt(self.conn, 1, foreign)
        self.assertEqual(total_consumer_debt(self.conn, 2), 4000)

    def test_create_is_user_scoped_idempotent_and_conflicts_on_payload_change(self):
        payload = {**self.payload(), "request_id": "debt-create-a"}
        first = save_consumer_debt(self.conn, 1, payload, request_id=payload["request_id"])
        replay = save_consumer_debt(self.conn, 1, payload, request_id=payload["request_id"])
        self.assertEqual(first, replay)
        self.assertEqual(total_consumer_debt(self.conn, 1), 20000)
        with self.assertRaises(ValueError):
            save_consumer_debt(self.conn, 1, {**payload, "outstanding_balance": 1}, request_id=payload["request_id"])
        other = save_consumer_debt(self.conn, 2, payload, request_id=payload["request_id"])
        self.assertNotEqual(first, other)

    def test_invalid_amounts_and_names(self):
        for amount in (-1, float("nan"), float("inf"), "abc", None, True, 10000001):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                save_consumer_debt(self.conn, 1, self.payload(amount))
        for name in ("", " " * 4, "x" * 121, None):
            with self.assertRaises(ValueError):
                save_consumer_debt(self.conn, 1, self.payload(name=name))

    def test_snapshot_freezes_debt_and_retry_does_not_overwrite(self):
        debt_id = save_consumer_debt(self.conn, 1, self.payload())
        first = state.capture_monthly_financial_snapshot(self.conn, 1, "2026-08", 500)
        self.assertEqual(first["total_consumer_debt"], 20000)
        self.assertEqual(first["net_worth"], -5000)
        save_consumer_debt(self.conn, 1, self.payload(100), debt_id)
        again = state.capture_monthly_financial_snapshot(self.conn, 1, "2026-08", 900)
        self.assertEqual(first, again)
        self.assertEqual(state.build_live_app_data(self.conn, 1)["netWorth"], 14900)

    def test_old_snapshot_addition_is_nullable_without_backfill(self):
        state.ensure_monthly_financial_snapshots_table(self.conn)
        self.conn.execute("ALTER TABLE monthly_financial_snapshots DROP COLUMN total_consumer_debt")
        self.conn.execute("INSERT INTO monthly_financial_snapshots(user_id,report_month,net_worth) VALUES (1,'2026-07',15000)")
        state.ensure_monthly_financial_snapshots_table(self.conn)
        state.ensure_monthly_financial_snapshots_table(self.conn)
        save_consumer_debt(self.conn, 1, self.payload())
        old = state.capture_monthly_financial_snapshot(self.conn, 1, "2026-07", 500)
        self.assertIsNone(old["total_consumer_debt"])
        self.assertEqual(old["net_worth"], 15000)
        wealth = report_engine._report_wealth_truth(
            {"current_investments":5000,"property_equity":0,"total_consumer_debt":old["total_consumer_debt"]},
            {"current_cash":10000}, {"holdings":[]})
        self.assertIsNone(wealth["total"])
        self.assertFalse(wealth["available"])

    def test_write_and_snapshot_rollback(self):
        ensure_consumer_debt_schema(self.conn)
        state.ensure_monthly_financial_snapshots_table(self.conn)
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        save_consumer_debt(self.conn, 1, self.payload())
        state.capture_monthly_financial_snapshot(self.conn, 1, "2026-08", 0)
        self.conn.rollback()
        self.assertEqual(total_consumer_debt(self.conn, 1), 0)
        self.assertIsNone(state.get_monthly_financial_snapshot(self.conn, 1, "2026-08"))

    def test_api_crud_and_no_cross_user_access(self):
        with patch.object(api, "DB_PATH", self.path), patch.object(api, "user_from_token", side_effect=lambda conn,token: {"one":1,"two":2}.get(token)):
            client = api.app.test_client()
            def call(method, path="/v1/consumer-debts", token="one", payload=None):
                return client.open(path, method=method, json=payload, headers={"Authorization":"Bearer "+token,"Origin":"https://getrove.de"})
            self.assertEqual(call("GET", token="bad").status_code, 401)
            response = call("POST", payload={**self.payload(), "request_id": "api-create-1"})
            self.assertEqual(response.status_code, 200, response.json)
            replay = call("POST", payload={**self.payload(), "request_id": "api-create-1"})
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json["debtId"], response.json["debtId"])
            conflict = call("POST", payload={**self.payload(1), "request_id": "api-create-1"})
            self.assertEqual(conflict.status_code, 409)
            path = "/v1/consumer-debts/" + str(response.json["debtId"])
            self.assertEqual(call("GET", token="two").json["consumerDebts"], [])
            self.assertEqual(call("PUT", path, "two", self.payload(1)).status_code, 404)
            self.assertEqual(call("DELETE", path, "two").status_code, 404)
            self.assertEqual(call("PUT", path, payload=self.payload(active=False)).json["consumerDebtTotal"], 0)
            self.assertEqual(call("PUT", path, payload=self.payload(500)).json["consumerDebtTotal"], 500)
            self.assertEqual(call("DELETE", path).json["consumerDebtTotal"], 0)
            self.assertEqual(call("POST", payload=[]).status_code, 400)

    def test_reports_show_signed_total_and_separate_debt(self):
        data = copy.deepcopy(standard_payload())
        data["profile"].update(current_investments=5000,cash_reserve=10000,property_equity=0,total_consumer_debt=20000,net_worth=-5000)
        data["report_truth"]["wealth"] = report_engine._report_wealth_truth(data["profile"], {"current_cash":10000}, {"holdings":[]})
        data["report_story_v2"] = build_report_story_v2(data)
        context = build_render_context(data)
        self.assertEqual(context["net_worth_amount"], "-5.000 €")
        self.assertEqual(context["consumer_debt_amount"], "20.000 €")
        self.assertEqual(sum(row["share"] for row in data["report_truth"]["wealth"]["allocation"]), 100)
        for name in ("rove_web_report.html",):
            html = render_template((ROOT / "report_templates" / name).read_text(), data)
            self.assertIn("Konsumschulden: 20.000 €", html)
            self.assertNotIn("NaN €", html)
        pdf_pages = _render_hell_pages(data)
        self.assertEqual(len(pdf_pages), 10)
        self.assertIn("Konsumschulden: 20.000 €", "".join(pdf_pages))

    def test_report_data_live_then_frozen_then_unknown_debt(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS monthly_snapshots (user_id INTEGER, month TEXT, net_worth REAL, clarity_score INTEGER, budget_ok INTEGER)")
        debt_id = save_consumer_debt(self.conn, 1, self.payload())
        state.capture_monthly_financial_snapshot(self.conn, 1, "2026-08", 0)
        self.conn.commit()
        with patch.object(report_engine, "DB_NAME", str(self.path)), patch("report_ai_text.generate_ai_narratives", return_value={}):
            live = report_engine.build_report_data(1, date.today().strftime("%Y-%m"))
            self.assertEqual(live["profile"]["net_worth"], -5000)
            self.assertEqual(live["profile"]["total_consumer_debt"], 20000)
            save_consumer_debt(self.conn, 1, self.payload(1), debt_id)
            self.conn.commit()
            frozen = report_engine.build_report_data(1, "2026-08")
            self.assertEqual(frozen["profile"]["net_worth"], -5000)
            self.assertEqual(frozen["profile"]["total_consumer_debt"], 20000)
            self.conn.execute("UPDATE monthly_financial_snapshots SET total_consumer_debt=NULL WHERE user_id=1")
            self.conn.commit()
            old = report_engine.build_report_data(1, "2026-08")
            self.assertIsNone(old["profile"]["net_worth"])
            self.assertIsNone(old["profile"]["total_consumer_debt"])

    def test_frontend_recalculation_deducts_once_and_profile_is_separate(self):
        html = (ROOT / "frontend/index.html").read_text()
        fn = html.split("function recalcNetWorth(){",1)[1].split("\n}",1)[0]
        script = "const assert=require('assert');let APP_MODE='bridge';const document={getElementById:()=>null};const DATA={assets:[{value:10000},{value:5000}],consumerDebtTotal:20000,netWorthAvailable:true};\nfunction recalcNetWorth(){"+fn+"\n}\n"
        script += "recalcNetWorth();assert.equal(DATA.netWorth,-5000);recalcNetWorth();assert.equal(DATA.netWorth,-5000);DATA.netWorthAvailable=false;recalcNetWorth();assert.equal(DATA.netWorth,null);APP_MODE='profile';recalcNetWorth();assert.equal(DATA.netWorth,15000);"
        subprocess.run(["node","-e",script],check=True,capture_output=True,text=True)
        self.assertIn('DATA.consumerDebtTotal = Number(b.consumerDebtTotal||0)', html)
        self.assertIn('escapeAccountHtml(row.name)', html)

    def test_german_debt_amount_parser(self):
        html = (ROOT / "frontend/index.html").read_text()
        fn = html.split("function parseGermanAmount(raw){", 1)[1].split("\n}", 1)[0]
        script = "const assert=require('assert');function parseGermanAmount(raw){"+fn+"\n}\n"
        script += "assert.equal(parseGermanAmount('20.000'),20000);assert.equal(parseGermanAmount('20.000,00'),20000);assert.equal(parseGermanAmount('20.000,50'),20000.5);assert.equal(parseGermanAmount('20000,00'),20000);assert(Number.isNaN(parseGermanAmount('20,00.50')));"
        subprocess.run(["node","-e",script],check=True,capture_output=True,text=True)

    def test_live_series_does_not_backfill_current_debt(self):
        save_consumer_debt(self.conn, 1, self.payload())
        live = state.build_live_app_data(self.conn, 1)
        self.assertEqual(len(live["series"]["1W"]), 8)
        self.assertEqual(live["series"]["1W"][:-1], [15.0] * 7)
        self.assertEqual(live["series"]["1W"][-1], -5.0)
        self.assertEqual(live["histDates"]["1W"][-1], "Heute")

    def test_historical_series_uses_frozen_monthly_snapshot(self):
        save_consumer_debt(self.conn, 1, self.payload())
        state.capture_monthly_financial_snapshot(self.conn, 1, "2026-08", 0)
        series = state.build_live_app_data(self.conn, 1)["series"]
        self.assertGreater(len(series["1J"]), 1)
        self.assertIn(-5.0, series["1J"])

    def test_javascript_syntax(self):
        html = (ROOT / "frontend/index.html").read_text()
        for attrs, source in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
            if "src=" not in attrs and source.strip():
                subprocess.run(["node","--check"],input=source,text=True,capture_output=True,check=True)
