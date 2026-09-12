"""Canonical chart input, pure ranges, and mutation/refresh regressions."""
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
from rove_financial_accounts import set_feature_enabled, FEATURE_MULTI_CASH_ACCOUNTS_V1
from test_financial_accounts_sprint2 import create_db

ROOT = Path(__file__).resolve().parent


def node(script):
    result = subprocess.run(["node"], input=script, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ChartEngineV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.db"
        create_db(self.path)
        with sqlite3.connect(self.path) as conn:
            set_feature_enabled(conn, 1, FEATURE_MULTI_CASH_ACCOUNTS_V1, False)
            conn.execute("UPDATE app_account_balances SET amount=CASE WHEN account_key='giro' THEN 40138 ELSE 0 END WHERE user_id=1")
            conn.execute("UPDATE users SET current_cash=40138,current_investments=0 WHERE user_id=1")
        self.addCleanup(patch.stopall)
        patch.object(api, "DB_PATH", self.path).start()
        patch.object(api, "user_from_token", return_value=1).start()
        self.client = api.app.test_client()
        self.html = (ROOT / "frontend/index.html").read_text()
        self.engine = self.html.split("function normalizeChartSeriesV2", 1)[1].split("function validatedNetHistory", 1)[0]
        self.engine = "function normalizeChartSeriesV2" + self.engine

    def live(self, uid=1):
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            return state.build_live_app_data(conn, uid)

    def ranges(self, live):
        coverage_equity = live.get("chartV2", {}).get("coverageEquityAtStart") or 0
        return node(self.engine + "\nconst input=" + json.dumps(live["chartV2"]) + """;
const before=JSON.stringify(input);
const output=Object.fromEntries(['1T','1W','1M','6M','1J'].map(r=>[r,buildRangeSeriesV2(input,r,PROPERTY_EQUITY)]));
if(before!==JSON.stringify(input)) throw new Error('mutated canonical input');
console.log(JSON.stringify(output));
""".replace('PROPERTY_EQUITY', json.dumps(coverage_equity)))

    def test_expense_create_delete_all_ranges_restore_without_restart(self):
        for amount in (60, 5000, 17):
            before = self.ranges(self.live())
            response = self.client.post('/v1/expenses', json={"amount": amount, "request_id": f"case-{amount}"})
            self.assertEqual(response.status_code, 200)
            created = self.live()
            self.assertEqual(created['netWorth'], 40138-amount)
            ranges = self.ranges(created)
            self.assertEqual(ranges['1T']['deltaEuro'], -amount)
            for points in ranges.values():
                self.assertAlmostEqual(points['pts'][-1]*1000, 40138-amount)
            deleted = self.client.delete('/v1/expenses/' + str(response.json['id']))
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(self.ranges(self.live()), before)

    def test_retry_is_one_event_and_card_mirror_is_not_double_counted(self):
        payload={"amount":60,"request_id":"one-event"}
        for _ in range(2):
            self.assertEqual(self.client.post('/v1/expenses',json=payload).status_code,200)
        points=self.live()['chartV2']['ranges']['1T']
        self.assertEqual(len(points),2)
        self.assertEqual([p['value'] for p in points],[40138,40078])
        self.assertTrue(points[-1]['id'].startswith('expense:'))

    def test_clean_browser_local_v_and_range_order_have_no_effect(self):
        live=self.live()
        adapter="function chartDataForRange"+self.html.split('function chartDataForRange',1)[1].split('function normalizeChartSeriesV2',1)[0]
        script=self.engine+adapter+"\nconst APP_MODE='bridge'; const DATA="+json.dumps(live)+""";
let PROFILE_META={netHistory:[{v:40138},{v:35138},{v:40138}]};
const before=JSON.stringify(DATA.chartV2);
const a=['1T','1W','1M','6M','1J','1T'].map(r=>chartDataForRange(r));
PROFILE_META={};DATA.netWorth=123;
const b=['1T','1W','1M','6M','1J','1T'].map(r=>chartDataForRange(r));
console.log(JSON.stringify({same:JSON.stringify(a)===JSON.stringify(b),pure:before===JSON.stringify(DATA.chartV2)}));
"""
        self.assertEqual(node(script),{'same':True,'pure':True})

    def test_property_coverage_breaks_without_faking_performance(self):
        before=self.live()
        with sqlite3.connect(self.path) as conn:
            state.ensure_app_properties_table(conn)
            conn.execute("INSERT INTO app_properties(user_id,market_value,remaining_debt,coverage_started_at) VALUES(1,11000,0,'2026-01-01 00:00:00')")
        after=self.live()
        self.assertEqual(after['netWorth'],before['netWorth']+11000)
        for name, data in self.ranges(after).items():
            if name=='1T': continue
            self.assertEqual(data['deltaEuro'],0)
            if name=='1J':
                self.assertEqual(data['breaks'],[4])
                self.assertEqual(data['rawPoints'][0]['value'],before['netWorth'])
                self.assertEqual(data['rawPoints'][4]['value'],before['netWorth']+11000)
            else:
                self.assertEqual(data['breaks'],[])
                self.assertEqual(data['rawPoints'][0]['value'],before['netWorth']+11000)
        with sqlite3.connect(self.path) as conn:
            conn.execute('DELETE FROM app_properties WHERE user_id=1')
        self.assertEqual(self.ranges(self.live()),self.ranges(before))

    def test_coverage_snapshot_is_migrated_and_immutable_when_equity_changes(self):
        with sqlite3.connect(self.path) as conn:
            state.ensure_app_properties_table(conn)
            conn.execute(
                """INSERT INTO app_properties
                   (user_id, market_value, remaining_debt, coverage_started_at)
                   VALUES (1, 9000, 0, '2026-01-01 00:00:00')"""
            )
        first = self.live()
        self.assertEqual(first["chartV2"]["coverageEquityAtStart"], 9000)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE app_properties SET market_value=12000 WHERE user_id=1"
            )
        second = self.live()
        self.assertEqual(second["chartV2"]["coverageEquityAtStart"], 9000)
        self.assertEqual(second["assets"][-1]["real"]["eigenkapital"], 12000)

        first_points = self.ranges(first)["1J"]["rawPoints"]
        second_points = self.ranges(second)["1J"]["rawPoints"]
        self.assertEqual(
            [point["value"] for point in first_points[:-1]],
            [point["value"] for point in second_points[:-1]],
        )
        self.assertEqual(second_points[-1]["value"] - first_points[-1]["value"], 3000)

    def test_property_api_preserves_snapshot_across_update_delete_and_readd(self):
        create = self.client.post(
            "/v1/property",
            json={"market_value": 9000, "remaining_debt": 0},
        )
        self.assertEqual(create.status_code, 200, create.get_json())
        self.assertEqual(create.get_json()["chartV2"]["coverageEquityAtStart"], 9000)

        update = self.client.post(
            "/v1/property",
            json={"market_value": 12000, "remaining_debt": 0},
        )
        self.assertEqual(update.status_code, 200, update.get_json())
        self.assertEqual(update.get_json()["chartV2"]["coverageEquityAtStart"], 9000)
        with sqlite3.connect(self.path) as conn:
            snapshot = conn.execute(
                "SELECT coverage_equity_at_start FROM app_properties WHERE user_id=1"
            ).fetchone()[0]
        self.assertEqual(snapshot, 9000)

        deleted = self.client.delete("/v1/property")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self.assertIsNone(deleted.get_json()["chartV2"]["coverageEquityAtStart"])

        readd = self.client.post(
            "/v1/property",
            json={"market_value": 12000, "remaining_debt": 0},
        )
        self.assertEqual(readd.status_code, 200, readd.get_json())
        self.assertEqual(readd.get_json()["chartV2"]["coverageEquityAtStart"], 12000)

    def test_coverage_snapshot_is_user_scoped(self):
        with sqlite3.connect(self.path) as conn:
            state.ensure_app_properties_table(conn)
            conn.execute(
                """INSERT INTO app_properties
                   (user_id, market_value, remaining_debt, coverage_started_at,
                    coverage_equity_at_start)
                   VALUES (1, 9000, 0, '2026-01-01 00:00:00', 9000)"""
            )
            conn.execute(
                """INSERT INTO app_properties
                   (user_id, market_value, remaining_debt, coverage_started_at,
                    coverage_equity_at_start)
                   VALUES (2, 12000, 0, '2026-02-01 00:00:00', 12000)"""
            )
        self.assertEqual(
            self.live(1)["chartV2"]["coverageEquityAtStart"], 9000
        )
        self.assertEqual(
            self.live(2)["chartV2"]["coverageEquityAtStart"], 12000
        )

    def test_v2_uses_persisted_snapshot_not_current_property_equity(self):
        adapter = "function chartDataForRange" + self.html.split(
            "function chartDataForRange", 1
        )[1].split("function normalizeChartSeriesV2", 1)[0]
        result = node(self.engine + adapter + """
const APP_MODE='bridge';
const DATA={
  netWorth:41000,
  chartV2:{
    version:2,
    coverageStartedAt:'2026-09-01 00:00:00',
    coverageEquityAtStart:9000,
    ranges:{'1M':[
      {id:'old',at:'2026-08-31',value:31000,label:'31. Aug',scope:'base'},
      {id:'covered',at:'2026-09-01',value:31000,label:'1. Sep',scope:'base'},
      {id:'today',at:'2026-09-12',value:41000,label:'Heute',scope:'full'}
    ]}
  },
  assets:[{name:'Immobilie',value:12000,real:{eigenkapital:12000}}]
};
const first=chartDataForRange('1M');
DATA.assets[0].value=9000;
DATA.assets[0].real.eigenkapital=9000;
const second=chartDataForRange('1M');
console.log(JSON.stringify({
  first:first.rawPoints.map(point=>point.value),
  second:second.rawPoints.map(point=>point.value),
  unchanged:JSON.stringify(first)===JSON.stringify(second)
}));
""")
        self.assertEqual(result["first"], [31000, 40000, 41000])
        self.assertEqual(result["second"], result["first"])
        self.assertTrue(result["unchanged"])

    def test_coverage_normalizes_base_points_into_continuous_full_line(self):
        input_data = {
            "version": 2,
            "coverageStartedAt": "2026-09-08 12:00:00",
            "ranges": {"1W": [
                {"id": "d1", "at": "2026-09-06", "value": 29000, "label": "So", "scope": "base"},
                {"id": "d2", "at": "2026-09-07", "value": 29100, "label": "Mo", "scope": "base"},
                {"id": "d3", "at": "2026-09-08", "value": 31100, "label": "Di", "scope": "base"},
                {"id": "d4", "at": "2026-09-09", "value": 31100, "label": "Mi", "scope": "base"},
                {"id": "d5", "at": "2026-09-10", "value": 31100, "label": "Do", "scope": "base"},
                {"id": "d6", "at": "2026-09-12", "value": 40127, "label": "Heute", "scope": "full"},
            ]},
        }
        result = node(self.engine + "\nconst input=" + json.dumps(input_data) + """;
const output=buildRangeSeriesV2(input,'1W',9000);
console.log(JSON.stringify(output));
""")
        self.assertEqual(result['breaks'], [2])
        self.assertEqual(result['segments'], [[0, 1], [2, 3, 4, 5]])
        self.assertEqual([p['value'] for p in result['rawPoints']], [29000, 29100, 40100, 40100, 40100, 40127])
        self.assertEqual(result['deltaEuro'], 127)

    def test_raw_series_preserves_cents_and_user_isolation(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE app_account_balances SET amount=40138.41 WHERE user_id=1 AND account_key='giro'")
            conn.execute("UPDATE users SET current_cash=40138.41 WHERE user_id=1")
        live=self.live()
        self.assertEqual(live['chartV2']['ranges']['1W'][-1]['value'],40138.41)
        self.assertNotEqual(self.live(2)['chartV2'],live['chartV2'])

    def test_small_movement_domain_is_pure_and_not_a_crash(self):
        domain='function chartValueDomain'+self.html.split('function chartValueDomain',1)[1].split('function drawChart',1)[0]
        result=node(domain+"""
const data={v2:true,netWorth:40078,pts:[40.138,40.078]};
const a=chartValueDomain('1T',data),b=chartValueDomain('1T',data);
console.log(JSON.stringify({same:JSON.stringify(a)===JSON.stringify(b),span:a.max-a.min,pixels:.06/(a.max-a.min)*108,a}));
""")
        self.assertTrue(result['same'])
        self.assertAlmostEqual(result['span'],40.078*.015,places=6)
        self.assertGreater(result['pixels'],10)
        self.assertLess(result['a']['min'],40.078)
        self.assertGreater(result['a']['max'],40.138)

    def test_range_domain_keeps_real_points_away_from_plot_edges(self):
        domain='function chartValueDomain'+self.html.split('function chartValueDomain',1)[1].split('function drawChart',1)[0]
        result=node(domain+"""
const data={v2:true,pts:[31,31.1,31.05,31,35.138]};
const d=chartValueDomain('1W',data);
const y=value=>126-(value-d.min)/(d.max-d.min)*108;
console.log(JSON.stringify({low:y(Math.min(...data.pts)),high:y(Math.max(...data.pts)),d}));
""")
        self.assertGreater(result['low'], 15)
        self.assertLess(result['high'], 111)

    def test_end_marker_uses_the_rendered_last_point(self):
        self.assertIn('const fullLine = n>1 ? linePoints.map(chartPath).join(" ") : "", last = xy[n-1];', self.html)
        self.assertIn('cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}"', self.html)
        self.assertIn('const domain=chartValueDomain(range,rangeData);', self.html)

    def test_singleton_current_v2_segment_keeps_endpoint_without_cross_scope_line(self):
        start = self.html.index('function drawChart(range, scrubIdx, animate=true){')
        end = self.html.index('\ndrawChart("1T");', start)
        renderer = self.html[start:end]
        self.assertIn('currentIsV2Singleton=rangeData.v2===true', renderer)
        self.assertIn('chart-endcap-v2-current', renderer)
        self.assertIn('+ (fullLine ? glassLine(fullLine,animate) : "")', renderer)
        self.assertIn('.filter(segment=>segment.length>1)', renderer)

    def test_bridge_start_never_falls_back_to_v1_before_hydration(self):
        adapter = "function chartDataForRange" + self.html.split("function chartDataForRange", 1)[1].split("function normalizeChartSeriesV2", 1)[0]
        result = node(adapter + """
const APP_MODE='bridge';
const DATA={chartV2:null,netWorth:42850,series:{'1W':[1,2]},histDates:{'1W':['Start','Heute']}};
function buildRangeSeriesV2(input,range){return {v2:true,pts:input? [99]:[]};}
const before=chartDataForRange('1W');
DATA.chartV2={version:2,ranges:{'1W':[]}};
const after=chartDataForRange('1W');
console.log(JSON.stringify({before,after}));
""")
        self.assertEqual(result['before'], {'v2': True, 'pts': []})
        self.assertEqual(result['after'], {'v2': True, 'pts': [99]})

    def test_bridge_hydration_assigns_v2_before_the_single_normal_render(self):
        start = self.html.index('async function loadBridgeState(){')
        end = self.html.index('\n// Web-Login', start)
        source = self.html[start:end]
        self.assertLess(source.index('DATA.chartV2=b.chartV2||null;'), source.index('drawChart(CHART.range||"1W",null,false);'))
        self.assertEqual(source.count('drawChart(CHART.range||"1W",null,false);'), 1)
        self.assertIn('if(b.series) DATA.series = b.series;', source)

    def test_refresh_during_read_queues_and_awaits_fresh_read(self):
        queue='let appDataRefreshInFlight'+self.html.split('let appDataRefreshInFlight',1)[1].split('async function fetchCanonicalAppState',1)[0]
        result=node(queue+"""
const pending=[];let reads=0;
function apiReady(){return true;}
function fetchCanonicalAppState(){reads++;return new Promise(resolve=>pending.push(resolve));}
(async()=>{
const first=refreshAppDataFromServer(),second=refreshAppDataFromServer();
pending.shift()(true);await Promise.resolve();await Promise.resolve();
if(reads!==2)throw new Error('post-mutation read skipped');
pending.shift()(true);await Promise.all([first,second]);
console.log(JSON.stringify({reads,settled:appDataRefreshInFlight===null}));
})();
""")
        self.assertEqual(result,{'reads':2,'settled':True})
