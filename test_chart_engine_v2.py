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
        return node(self.engine + "\nconst input=" + json.dumps(live["chartV2"]) + """;
const before=JSON.stringify(input);
const output=Object.fromEntries(['1T','1W','1M','6M','1J'].map(r=>[r,buildRangeSeriesV2(input,r)]));
if(before!==JSON.stringify(input)) throw new Error('mutated canonical input');
console.log(JSON.stringify(output));
""")

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
            self.assertEqual(data['breaks'],[len(data['pts'])-1])
            self.assertEqual(data['rawPoints'][0]['value'],40138)
        with sqlite3.connect(self.path) as conn:
            conn.execute('DELETE FROM app_properties WHERE user_id=1')
        self.assertEqual(self.ranges(self.live()),self.ranges(before))

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
const data={v2:true,pts:[40.138,40.078]};
const a=chartValueDomain('1T',data),b=chartValueDomain('1T',data);
console.log(JSON.stringify({same:JSON.stringify(a)===JSON.stringify(b),fraction:.06/(a.max-a.min),a}));
""")
        self.assertTrue(result['same'])
        self.assertLess(result['fraction'],.04)
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
