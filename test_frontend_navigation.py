from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path


FRONTEND_PATH = Path(
    os.environ.get(
        "ROVE_FRONTEND_PATH",
        str(Path(__file__).resolve().parent / "frontend" / "index.html"),
    )
)


class FrontendNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")
        day_start = cls.frontend.index("function dayKey(d){")
        day_end = cls.frontend.index("function dLabel", day_start)
        chart_start = cls.frontend.index("function chartTodayPoints()")
        chart_end = cls.frontend.index("function straightPath", chart_start)
        cls.chart_adapter = cls.frontend[day_start:day_end] + cls.frontend[chart_start:chart_end]
        timestamp_start = cls.frontend.index("function chartPointTimestamp")
        activity_start = cls.frontend.index("function chartActivityEvents")
        activity_end = cls.frontend.index("function chartValueDomain", activity_start)
        cls.chart_adapter += cls.frontend[timestamp_start:activity_start]
        cls.chart_adapter += cls.frontend[activity_start:activity_end]
        restore_start = cls.frontend.index("function restoreBridgeLocal(")
        restore_end = cls.frontend.index("const PAIR_API_BASE_URL", restore_start)
        cls.chart_adapter += cls.frontend[restore_start:restore_end]

    def run_chart_adapter(self, script):
        node_script = f"""
const DATA = {{
  netWorth: 34000,
  series: {{"1W":[30,31,32],"1M":[10,11],"6M":[1,2],"1J":[3,4]}},
  histDates: {{"1W":["8. Sep","9. Sep","Heute"],"1M":["8. Aug","Heute"],"6M":["Apr. 2026","Heute"],"1J":["Sept. 2025","Heute"]}}
}};
const APP_MODE = "bridge";
function saveBridgeLocal() {{}}
const BRIDGE_BOT_ASSET_NAMES=new Set();
const document={{querySelector:()=>null}};
const CHART={{range:"1T"}};
function drawChart() {{}}
function updateChangeBadge() {{}}
const today = new Date();
const todayKey = today.getFullYear()+"-"+String(today.getMonth()+1).padStart(2,"0")+"-"+String(today.getDate()).padStart(2,"0");
const PROFILE_META = {{netHistorySchemaVersion:2,coverageBoundaries:{{}},netHistory:[
  {{d:todayKey,v:32000,t:today.getTime()+3600000,source:"system",event_id:null,version:2}},
  {{d:todayKey,v:31000,t:today.getTime()+7200000,source:"expense",event_id:"seed",version:2}}
]}};
{self.chart_adapter}
{script}
"""
        result = subprocess.run(
            ["node", "--input-type=commonjs"],
            input=node_script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_anonymous_value_changes_never_form_persistent_v(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory=[{d:todayKey,v:40138,t:1,source:"system",event_id:null,version:2}];
DATA.netWorth=35138; syncNetHistory();
DATA.netWorth=40138;
for(let i=0;i<5;i++) syncNetHistory();
console.log(JSON.stringify(PROFILE_META.netHistory.map(p=>p.v)));
""")
        self.assertEqual(result, [40138])

    def test_confirmed_event_identity_is_idempotent_and_delete_is_exact(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory=[{d:todayKey,v:40138,t:1,source:"system",event_id:null,version:2}];
DATA.netWorth=35138; syncNetHistory(); tagLatestNetHistoryPoint("expense","X");
for(let i=0;i<5;i++){syncNetHistory();tagLatestNetHistoryPoint("expense","X");}
const created=JSON.parse(JSON.stringify(PROFILE_META.netHistory));
DATA.netWorth=35121;tagLatestNetHistoryPoint("expense","Y");
reconcileDeletedExpenseHistory("X");reconcileDeletedExpenseHistory("X");
console.log(JSON.stringify({created,remaining:PROFILE_META.netHistory.map(p=>p.event_id)}));
""")
        self.assertEqual([p["v"] for p in result["created"]], [40138, 35138])
        self.assertEqual(result["remaining"], [None, "Y"])

    def test_create_delete_returns_day_delta_to_zero(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory=[{d:todayKey,v:40138,t:1,source:"system",event_id:null,version:2}];
DATA.netWorth=35138;tagLatestNetHistoryPoint("expense","X");
DATA.netWorth=40138;syncNetHistory();reconcileDeletedExpenseHistory("X");
console.log(JSON.stringify(chartDataForRange("1T").pts));
""")
        self.assertEqual(result, [40.138])

    def test_restore_filters_legacy_and_preserves_identified_events_idempotently(self):
        result = self.run_chart_adapter("""
const baseline={d:todayKey,v:40138,t:1,source:"system",event_id:null,version:2};
const event={d:todayKey,v:35138,t:2,source:"expense",event_id:"X",version:2};
const snapshot={userId:7,netHistorySchemaVersion:2,netHistory:[baseline,{d:todayKey,v:30000,t:3},event,event]};
restoreBridgeLocal(snapshot,7);syncNetHistory();
const first=JSON.stringify(PROFILE_META.netHistory);
const saved=JSON.parse(JSON.stringify({userId:7,netHistorySchemaVersion:2,netHistory:PROFILE_META.netHistory}));
PROFILE_META.netHistory=[];
restoreBridgeLocal(saved,7);syncNetHistory();restoreBridgeLocal(saved,7);syncNetHistory();
console.log(JSON.stringify({same:first===JSON.stringify(PROFILE_META.netHistory),values:PROFILE_META.netHistory.map(p=>p.v)}));
""")
        self.assertTrue(result["same"])
        self.assertEqual(result["values"], [40138, 35138])

    def test_range_sequence_does_not_change_provenance_history(self):
        result = self.run_chart_adapter("""
const before=JSON.stringify(PROFILE_META.netHistory),server=JSON.stringify(DATA.series);
for(const range of ["1T","1W","1M","6M","1J","1T"]) chartDataForRange(range);
console.log(JSON.stringify({history:before===JSON.stringify(PROFILE_META.netHistory),server:server===JSON.stringify(DATA.series)}));
""")
        self.assertEqual(result, {"history": True, "server": True})

    def test_chart_adapter_keeps_long_ranges_server_based_without_mutating_state(self):
        result = self.run_chart_adapter("""
const before = JSON.stringify(DATA);
const oneDay = chartDataForRange("1T");
const ranges = ["1W","1M","6M","1J"].map(range => [range, chartDataForRange(range)]);
console.log(JSON.stringify({oneDay,ranges,unchanged:before===JSON.stringify(DATA),lastIsCurrent:ranges.every(([,data])=>data.pts.at(-1)===34)}));
""")
        self.assertEqual(result["oneDay"]["pts"], [32, 31])
        self.assertEqual(len(result["oneDay"]["dates"]), 2)
        self.assertTrue(result["oneDay"]["intraday"])
        self.assertTrue(result["unchanged"])
        self.assertTrue(result["lastIsCurrent"])
        for _, data in result["ranges"]:
            self.assertEqual(data["pts"][-1], 34)
            self.assertEqual(data["dates"][-1], "Heute")
            self.assertEqual(len(data["pts"]), 3 if _ == "1W" else 2)

    def test_chart_adapter_preserves_cents(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory[0].v = 32000.49;
PROFILE_META.netHistory[1].v = 32000.51;
console.log(JSON.stringify(chartDataForRange("1T")));
""")
        self.assertEqual(result["pts"], [32.00049, 32.00051])
        self.assertAlmostEqual((result["pts"][1]-result["pts"][0])*1000, 0.02)

    def test_chart_adapter_keeps_seventeen_euro_day_delta_exact(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory = [
  {d:todayKey,v:35000,t:today.getTime()+3600000},
  {d:todayKey,v:34983,t:today.getTime()+7200000}
];
const data = chartDataForRange("1T");
console.log(JSON.stringify({data,delta:(data.pts.at(-1)-data.pts[0])*1000}));
        """)
        self.assertEqual(result["data"]["pts"], [35, 34.983])
        self.assertAlmostEqual(result["delta"], -17, places=9)

    def test_legacy_net_history_migrates_to_current_v2_baseline(self):
        result = self.run_chart_adapter("""
DATA.netWorth = 34000;
PROFILE_META.netHistorySchemaVersion = 1;
PROFILE_META.netHistory = [{d:todayKey,v:35000,t:1000}];
const data = chartDataForRange("1T");
console.log(JSON.stringify({data,history:PROFILE_META.netHistory}));
""")
        self.assertEqual(result["data"]["pts"], [34])
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["version"], 2)
        self.assertEqual(result["history"][0]["source"], "system")

    def test_deleted_expense_provenance_is_reconciled_without_removing_other_events(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory = [
  {d:todayKey,v:35000,t:1000,source:"system",event_id:null,version:2},
  {d:todayKey,v:34983,t:2000,source:"expense",event_id:"a",version:2},
  {d:todayKey,v:34800,t:3000,source:"expense",event_id:"b",version:2}
];
reconcileDeletedExpenseHistory("a");
console.log(JSON.stringify(PROFILE_META.netHistory));
""")
        self.assertEqual([point["event_id"] for point in result], [None, "b"])

    def test_chart_adapter_appends_only_one_today_without_local_history(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory = [];
for (const range of ["1W","1M","6M","1J"]) DATA.histDates[range][DATA.histDates[range].length-1] = "Gestern";
const before = JSON.stringify(DATA);
const ranges = ["1W","1M","6M","1J"].map(range => ({range, data:chartDataForRange(range)}));
console.log(JSON.stringify({ranges,unchanged:before===JSON.stringify(DATA)}));
""")
        self.assertTrue(result["unchanged"])
        for item in result["ranges"]:
            data = item["data"]
            self.assertEqual(data["pts"][-1], 34)
            self.assertEqual(data["dates"][-2:], ["Gestern", "Heute"])
            self.assertEqual(len(data["pts"]), 4 if item["range"] == "1W" else 3)

    def test_chart_adapter_keeps_single_point_neutral(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory = [{d:todayKey,v:34000,t:1000}];
console.log(JSON.stringify(chartDataForRange("1T")));
""")
        self.assertEqual(result["pts"], [34])
        self.assertEqual(result["dates"], [])
        self.assertFalse(result["intraday"])
        self.assertFalse(result["dayDeltaAvailable"])

    def test_one_day_activity_uses_canonical_events_and_preserves_order(self):
        result = self.run_chart_adapter("""
DATA.chartV2 = {version:2, ranges:{"1T":[
  {id:"day-start:today",at:"2026-09-13 08:00:00",value:40000,label:"08:00",scope:"day",source:"reconstructed"},
  {id:"expense:11",at:"2026-09-13 12:00:00",value:39983,label:"12:00",scope:"day",source:"reconstructed_event"},
  {id:"cash:12",at:"2026-09-13 13:00:00",value:40483,label:"13:00",scope:"day",source:"reconstructed_event"},
  {id:"expense:13",at:"2026-09-13 14:00:00",value:40473,label:"14:00",scope:"day",source:"reconstructed_event"}
]}};
DATA.tx = [{d:"Heute",items:[
  {sid:11,n:"Supermarkt",a:-17,desc:"Wocheneinkauf"},
  {csid:12,n:"Gehalt",a:500,desc:"Monatliches Einkommen"},
  {sid:13,n:"Tankstelle",a:-10}
]}];
PROFILE_META.netHistory = [{d:todayKey,v:1,t:1,source:"expense",event_id:"fake",version:2}];
console.log(JSON.stringify(chartActivityEvents().map(({id,name,amount,type,description})=>({id,name,amount,type,description}))));
""")
        self.assertEqual(
            result,
            [
                {"id": "expense:11", "name": "Supermarkt", "amount": -17, "type": "expense", "description": "Wocheneinkauf"},
                {"id": "cash:12", "name": "Gehalt", "amount": 500, "type": "income", "description": "Monatliches Einkommen"},
                {"id": "expense:13", "name": "Tankstelle", "amount": -10, "type": "expense", "description": ""},
            ],
        )

    def test_one_day_activity_legacy_fallback_sorts_real_timestamps(self):
        result = self.run_chart_adapter("""
PROFILE_META.netHistory = [
  {d:todayKey,v:40000,t:1000,source:"system",event_id:null,version:2},
  {d:todayKey,v:39990,t:9000,source:"expense",event_id:"2",version:2},
  {d:todayKey,v:39980,t:2000,source:"expense",event_id:"1",version:2}
];
DATA.tx = [{d:"Heute",items:[{id:1,n:"Frühstück",a:-20},{id:2,n:"Brot",a:-10}]}];
console.log(JSON.stringify(chartActivityEvents().map(({id,name})=>({id,name}))));
""")
        self.assertEqual(
            result,
            [{"id": "expense:1", "name": "Frühstück"}, {"id": "expense:2", "name": "Brot"}],
        )

    def test_mentor_priority_questions_bypass_legacy_local_score_answer(self):
        start = self.frontend.index("function isMentorPriorityQuestion(")
        end = self.frontend.index("// Dispatcher: Thema", start)
        helper = self.frontend[start:end]
        script = f"""
{helper}
const questions = [
  "Was ist aktuell mein größter finanzieller Schwachpunkt?",
  "Was ist mein wichtigster finanzieller Hebel?",
  "Was bremst meinen Score aktuell?",
  "Was soll ich als Nächstes verbessern?",
  "Woran soll ich zuerst arbeiten?",
  "Wie kann ich meine finanzielle Situation sinnvoll verbessern?"
];
console.log(JSON.stringify(questions.map(q => isMentorPriorityQuestion(q.toLowerCase()))));
"""
        result = subprocess.run(
            ["node", "--input-type=commonjs"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True] * 6)
        self.assertLess(
            self.frontend.index("if(isMentorPriorityQuestion(t)) return null;"),
            self.frontend.index("if(/score|controller|stratege|rang|einstufung|verfassung|punkte/.test(t)) return ans_score(t);")
        )

    def test_one_day_activity_bar_scale_keeps_small_values_visible(self):
        result = self.run_chart_adapter("""
console.log(JSON.stringify([
  chartActivityBarHeight(-10,100),
  chartActivityBarHeight(-100,100),
  chartActivityBarHeight(50,100)
]));
""")
        self.assertEqual(result, [14, 66, 33])

    def test_one_day_activity_width_keeps_many_events_scrollable(self):
        result = self.run_chart_adapter("""
console.log(JSON.stringify([chartActivityWidth(4), chartActivityWidth(10)]));
""")
        self.assertEqual(result, [360, 876])

    def test_one_day_activity_positions_are_left_aligned_and_evenly_spaced(self):
        result = self.run_chart_adapter("""
console.log(JSON.stringify([
  chartActivityX(2,0),
  chartActivityX(2,1),
  chartActivityX(4,0),
  chartActivityX(4,3)
]));
""")
        self.assertEqual(result, [36, 120, 36, 288])

    def test_one_day_renderer_is_activity_view_without_touching_long_ranges(self):
        self.assertIn('id="chartViewport"', self.frontend)
        self.assertIn('.chart-activity-bar-expense{fill:url(#chartActivityExpense)}', self.frontend)
        self.assertIn('filter:drop-shadow(0 2px 4px rgba(0,0,0,.12))', self.frontend)
        self.assertIn('function chartActivityWidth(count)', self.frontend)
        self.assertIn('function chartActivityX(count,index,width=360)', self.frontend)
        self.assertIn('#tab-home .chart-wrap.activity-view .ranges{margin-top:8px}', self.frontend)
        self.assertIn('wrap?.classList.add("activity-view")', self.frontend)
        self.assertIn('wrap?.classList.remove("activity-view")', self.frontend)
        self.assertIn("Noch keine Aktivität heute.", self.frontend)
        self.assertNotIn("Deine heutigen Buchungen erscheinen hier.", self.frontend)
        self.assertIn('class="chart-activity-empty-mascot"', self.frontend)
        self.assertIn('@keyframes chartActivityRoveHover', self.frontend)
        self.assertIn('@keyframes chartActivityRoveBlink', self.frontend)
        self.assertIn('M60 6 C 84 6 95 30 93 60', self.frontend)
        self.assertIn('x="${width/2-45}" y="78" width="90" height="98"', self.frontend)
        self.assertIn('y="194" text-anchor="middle">Noch keine Aktivität heute.', self.frontend)
        self.assertIn('baseline=160, barGap=4, barBottom=baseline-barGap, labelY=179, amountY=198', self.frontend)
        self.assertIn('chartActivityBarHeight(event.amount,maxAmount,108,18)', self.frontend)
        self.assertIn('Math.min(84,(width-leftInset*2)/(count-1))', self.frontend)
        self.assertIn('const centerX=chartActivityX(count,index,width), barWidth=Math.min(34, count>1?30:26);', self.frontend)
        self.assertIn('rx="5" ry="5"', self.frontend)
        self.assertIn(
            'if(range==="1T"){\n    drawDayActivityChart(chartActivityEvents());\n    return;\n  }',
            self.frontend,
        )
        self.assertIn('resetChartActivityView();\n  const rangeData=chartDataForRange(range)', self.frontend)
        self.assertIn('data-event-id="${escapeAccountHtml(event.id)}"', self.frontend)

    def test_property_coverage_break_neutralizes_long_range_delta(self):
        result = self.run_chart_adapter("""
DATA.netWorth = 40138.41;
DATA.assets = [{name:"Immobilie",value:9000,source:"app"}];
PROFILE_META.coverageBoundaries = {property:{d:todayKey,value:9000}};
const ranges = ["1W","1M","6M","1J"].map(range => chartDataForRange(range));
console.log(JSON.stringify(ranges));
""")
        for data in result:
            self.assertEqual(data["breakBefore"], len(data["pts"]) - 1)
            self.assertEqual(data["adjustment"], 9)

    def test_property_coverage_does_not_mutate_raw_server_points(self):
        result = self.run_chart_adapter("""
DATA.netWorth = 40138.41;
DATA.assets = [{name:"Immobilie",value:9000,source:"app"}];
PROFILE_META.coverageBoundaries = {property:{d:todayKey,value:9000}};
const beforeSeries = JSON.stringify(DATA.series);
const beforeDates = JSON.stringify(DATA.histDates);
const data = chartDataForRange("1W");
console.log(JSON.stringify({beforeSeries,beforeDates,afterSeries:JSON.stringify(DATA.series),afterDates:JSON.stringify(DATA.histDates),data}));
""")
        self.assertEqual(result["beforeSeries"], result["afterSeries"])
        self.assertEqual(result["beforeDates"], result["afterDates"])
        self.assertEqual(result["data"]["pts"][-1], 40.13841)

    def test_property_coverage_boundary_comes_from_server_metadata(self):
        result = self.run_chart_adapter("""
DATA.assets = [{name:"Immobilie",value:9000,source:"app",real:{coverageStartedAt:todayKey+" 08:00:00"}}];
PROFILE_META.coverageBoundaries = {property:{d:"2000-01-01",value:1}};
syncPropertyCoverageBoundaryFromServer();
console.log(JSON.stringify({boundary:PROFILE_META.coverageBoundaries.property,todayKey}));
""")
        self.assertEqual(result["boundary"], {"d": result["todayKey"], "value": 9000})

    def test_local_coverage_boundary_is_not_a_source_without_server_metadata(self):
        result = self.run_chart_adapter("""
DATA.assets = [{name:"Immobilie",value:9000,source:"app",real:{}}];
PROFILE_META.coverageBoundaries = {property:{d:todayKey,value:9000}};
syncPropertyCoverageBoundaryFromServer();
console.log(JSON.stringify(PROFILE_META.coverageBoundaries));
""")
        self.assertEqual(result, {})

    def test_delete_reconciliation_requests_immediate_chart_redraw(self):
        self.assertIn("function redrawAfterHistoryReconciliation()", self.frontend)
        self.assertIn(
            "reconcileDeletedExpenseHistory(sid);\n    redrawAfterHistoryReconciliation();",
            self.frontend,
        )

    def test_uses_one_marked_history_bridge(self):
        self.assertIn('const ROVE_NAV_STATE="roveNav"', self.frontend)
        self.assertIn("function rovePushState(sheet=null)", self.frontend)
        self.assertIn('window.addEventListener("popstate"', self.frontend)
        self.assertIn("history.replaceState({roveNav:true,tab:roveActiveTab(),sheet:null}", self.frontend)

    def test_tab_clicks_push_but_restoration_does_not(self):
        self.assertIn('go(t.dataset.tab,{history:true})', self.frontend)
        self.assertIn('go(state.tab,{fromHistory:true})', self.frontend)
        self.assertIn("if(options.history) rovePushState()", self.frontend)

    def test_sheet_open_and_close_share_existing_helpers(self):
        self.assertIn('rovePushState(id)', self.frontend)
        self.assertIn('const shouldRestore=!options.fromHistory', self.frontend)
        self.assertIn('if(shouldRestore) history.back()', self.frontend)
        self.assertIn('closeSheet({fromHistory:true})', self.frontend)
        self.assertIn("function roveRestoreSheet(sheetId)", self.frontend)
        self.assertIn("repsheet:()=>openReports()", self.frontend)

    def test_deep_link_cleanup_preserves_rove_state(self):
        self.assertIn('const state=roveIsState(history.state)?history.state:', self.frontend)
        self.assertIn('history.replaceState(state,"",url.pathname+url.search+url.hash)', self.frontend)

    def test_auth_and_swipe_paths_remain_present(self):
        self.assertIn('function showPinScreen(mode="locked")', self.frontend)
        self.assertIn("function roveAuthFlowActive()", self.frontend)
        self.assertIn("if(roveAuthFlowActive()) return", self.frontend)
        self.assertIn("function initSheetSwipeDismiss()", self.frontend)
        self.assertIn('if(sheet.classList.contains("on"))closeSheet()', self.frontend)

    def test_pin_unlock_reveals_after_single_live_state_render(self):
        resume = self.frontend.split("async function resumeAfterPin(){", 1)[1].split("async function submitPinSetup", 1)[0]
        self.assertIn("const ok=await loadBridgeState();", resume)
        self.assertIn('document.getElementById("app")?.removeAttribute("hidden");', resume)
        self.assertNotIn("await refreshAppDataFromServer();", resume)

    def test_initial_boot_does_not_refresh_after_bootstrap_render(self):
        boot = self.frontend.split("// Splash nach der Animation", 1)[1].split("// Autosave:", 1)[0]
        self.assertIn('bootstrapAuthenticatedApp().then(state => {', boot)
        self.assertIn('if(state==="ready") return true;', boot)
        self.assertNotIn('if(state==="ready") return refreshAppDataFromServer();', boot)

    def test_home_asset_add_field_has_local_bottom_spacing(self):
        self.assertIn(
            '<div class="card home-assets-card" style="padding:2px 16px 18px" id="assets"></div>',
            self.frontend,
        )
        self.assertIn('.account-add{width:100%;min-height:50px;', self.frontend)

    def test_budget_parent_card_does_not_jump_when_budget_row_is_pressed(self):
        self.assertIn('#tab-tx #budget-list>.card:active{transform:none;opacity:1}', self.frontend)
        self.assertIn('const row=e.target.closest("[data-bgt]");', self.frontend)
        self.assertIn('if(row) openBudgetSheet(+row.dataset.bgt);', self.frontend)

    def test_settings_logout_has_no_boolean_badge(self):
        self.assertIn('action("logout","Abmelden","Auf diesem Gerät sicher abmelden")', self.frontend)
        self.assertNotIn('action("logout","Abmelden","Auf diesem Gerät sicher abmelden",true)', self.frontend)

    def test_home_chart_uses_truthful_paths_and_visible_context_without_changing_data(self):
        self.assertIn('@keyframes chartLineDraw', self.frontend)
        self.assertIn('.chart .chart-line-main{stroke-dasharray:1;', self.frontend)
        self.assertIn('const glassLine=(d,animate)=>', self.frontend)
        self.assertIn('glassLine(fullLine,animate)', self.frontend)
        self.assertIn('function straightPath(xy)', self.frontend)
        self.assertIn('function monotonePath(xy)', self.frontend)
        self.assertIn('function chartPath(xy){ return xy.length<=3 ? straightPath(xy) : monotonePath(xy); }', self.frontend)
        self.assertIn('function oneDayPath(xy)', self.frontend)
        self.assertIn('function chartPathForRange(xy,range)', self.frontend)
        self.assertIn('function chartPointTimestamp(point,index)', self.frontend)
        self.assertIn('function chartXPositions(range,rangeData,n,W,padX)', self.frontend)
        self.assertIn('const xs=chartXPositions(range,rangeData,n,W,padX);', self.frontend)
        self.assertIn('const xs=CHART.xs||CHART.pts.map', self.frontend)
        self.assertNotIn('function catmullPath(', self.frontend)
        self.assertNotIn('if(win.length<2) win=[h[0], h[h.length-1]];', self.frontend)
        self.assertIn('id="chartContext"', self.frontend)
        self.assertNotIn('id="chartDelta" hidden', self.frontend)
        self.assertIn('data-r="1T">1T', self.frontend)
        self.assertIn('data-r="1J">1J', self.frontend)
        self.assertNotIn('data-r="Max"', self.frontend)
        self.assertIn('function chartDataForRange(range)', self.frontend)
        self.assertIn('const rangeData=chartDataForRange(range), pts=rangeData.pts;', self.frontend)
        self.assertIn('function chartTodayPoints()', self.frontend)
        self.assertIn('const todayPoints=chartTodayPoints();', self.frontend)
        self.assertNotIn('DATA.series["1T"]=intraday.map(point=>Math.round(Number(point.v))/1000);', self.frontend)
        self.assertIn('const stamp=Number(point.t);', self.frontend)
        self.assertIn('if(last && last.d===dk){', self.frontend)
        self.assertIn('if(Math.abs(Number(last.v)-value)>0.005) h.push({d:dk,v:value,t:now});', self.frontend)
        self.assertIn('source:"system",event_id:null,version:2', self.frontend)
        self.assertNotIn('const dayStart=source.length>1 ? Number(source[source.length-2]) : null;', self.frontend)
        self.assertIn('const neutralDay=range==="1T"&&!rangeData.intraday&&!rangeData.dayDeltaAvailable;', self.frontend)
        self.assertIn('const neutralLine=`M ${padX} ${last[1].toFixed(1)} L ${W-padX} ${last[1].toFixed(1)}`;', self.frontend)
        self.assertIn('} else if(range==="1T"&&!rangeData.intraday){', self.frontend)
        self.assertIn('function chartValueDomain(range, rangeData)', self.frontend)
        self.assertIn('const domains=CHART.valueDomains||(CHART.valueDomains={});', self.frontend)
        self.assertIn('if(previous&&previous.key===key)', self.frontend)
        self.assertIn('const domain=chartValueDomain(range,rangeData);', self.frontend)
        self.assertNotIn('chart-day-reference', self.frontend)
        self.assertIn('context.hidden=range==="1T"', self.frontend)
        self.assertIn('.chart-context[hidden]{display:none}', self.frontend)
        self.assertIn('CHART.dates=rangeData.dates||seriesDates(range);', self.frontend)
        self.assertIn('if(CHART.range==="1T"&&!CHART.dayHasIntraday) return;', self.frontend)
        self.assertIn('drawChart("1T")', self.frontend)
        self.assertIn('dEl.textContent = neutralDay || pts.length===1 ? "—"', self.frontend)
        self.assertIn('drawChart(CHART.range||"1W",null,false)', self.frontend)
        self.assertIn('e.sid=data.id;   // Server-ID merken, damit dieselbe Buchung ohne Reload löschbar ist\n    await refreshAppDataFromServer();', self.frontend)
        self.assertIn('stroke="#F5F7F8"', self.frontend)
        self.assertIn('stroke="#FFFFFF"', self.frontend)
        self.assertIn('id="chartScrubClipRect"', self.frontend)
        self.assertIn('id="chartScrubPath"', self.frontend)
        self.assertIn('clip-path="url(#chartScrubClip)"', self.frontend)
        self.assertNotIn('id="chartStroke"', self.frontend)

    def test_one_day_chart_reuses_user_scoped_local_history(self):
        self.assertIn('netHistory:Array.isArray(PROFILE_META.netHistory)?PROFILE_META.netHistory.slice(-760):[]', self.frontend)
        self.assertIn('netHistorySchemaVersion:PROFILE_META.netHistorySchemaVersion||2', self.frontend)
        self.assertIn('PROFILE_META.netHistory=validatedNetHistory(snapshot.netHistory)', self.frontend)
        self.assertIn('if(APP_MODE!=="profile" && APP_MODE!=="bridge") return;', self.frontend)
        self.assertIn('syncNetHistory();\n    rebuildSeriesFromHistory();\n    saveBridgeLocal();', self.frontend)
        self.assertIn('return h.filter(point=>point&&point.d===today&&Number.isFinite(Number(point.v)))', self.frontend)
        self.assertIn('return index===points.length-1 ? "Jetzt" : "Heute";', self.frontend)
        self.assertIn('Die lokale History wird ausschliesslich vom Chart-Adapter gelesen.', self.frontend)
        self.assertNotIn('DATA.histDates["1T"]=', self.frontend)
        self.assertNotIn('DATA.series[range]=win.map(s=>Math.round(s.v)/1000);', self.frontend)

    def test_expenses_use_existing_server_id_for_history_provenance(self):
        create_start = self.frontend.index("async function syncExpenseToServer(e, raw){")
        create_end = self.frontend.index("// Gegenstück zu syncExpenseToServer", create_start)
        create_source = self.frontend[create_start:create_end]
        delete_start = self.frontend.index("async function syncExpenseDeleteToServer(sid){")
        delete_end = self.frontend.index("async function syncExpenseCategoryToServer", delete_start)
        delete_source = self.frontend[delete_start:delete_end]
        self.assertIn("e.sid=data.id;", create_source)
        self.assertIn('tagLatestNetHistoryPoint("expense",e.sid);', create_source)
        self.assertIn("reconcileDeletedExpenseHistory(sid);", delete_source)

    def test_expense_delete_reconciles_full_state_for_single_cash(self):
        start = self.frontend.index("async function syncExpenseDeleteToServer(sid){")
        end = self.frontend.index("async function syncExpenseCategoryToServer", start)
        delete_source = self.frontend[start:end]
        self.assertIn("await refreshAppDataFromServer();", delete_source)
        self.assertNotIn("applyServerCashAccounts(data.accounts)", delete_source)

    def test_home_empty_state_keeps_chart_for_canonical_financial_data(self):
        self.assertIn('function homeHasFinancialData()', self.frontend)
        self.assertIn('if(Array.isArray(DATA.financialAccounts) && DATA.financialAccounts.length) return true;', self.frontend)
        self.assertIn('if(DATA.netWorthAvailable===true && Number.isFinite(netWorth) && netWorth!==0) return true;', self.frontend)
        self.assertIn('function applyHomeEmpty(){\n  const empty=!homeHasFinancialData();', self.frontend)
        self.assertNotIn('function applyHomeEmpty(){\n  const empty=!DATA.assets.length;', self.frontend)

    def test_bridge_start_renders_net_worth_after_ticker_unlock(self):
        self.assertIn('if(nw){\n      delete nw.dataset.lock;\n      recalcNetWorth();', self.frontend)

    def test_chart_value_domain_stays_stable_during_live_balance_updates(self):
        start = self.frontend.index("function chartValueDomain(")
        end = self.frontend.index("function drawChart(", start)
        function_source = self.frontend[start:end]
        script = f"""
const CHART={{}};
eval({json.dumps(function_source)});
function y(value, domain) {{
  return 126 - (value-domain.min)/(domain.max-domain.min)*108;
}}
for (const [range, initial, updated, intraday] of [
  ["1T", [31.336, 40], [31.336, 39.4], false],
  ["1W", [30.56, 30.627, 30.278, 30.988, 31.5, 31.342, 31.336, 40],
          [30.56, 30.627, 30.278, 30.988, 31.5, 31.342, 31.336, 39.4], true]
]) {{
  const first=chartValueDomain(range, {{pts:initial, dates:["Start","Heute"], intraday}});
  const before=y(initial.at(-1), first);
  const second=chartValueDomain(range, {{pts:updated, dates:["Start","Heute"], intraday}});
  const after=y(updated.at(-1), second);
  if (first.min!==second.min || first.max!==second.max || !(after>before+1)) process.exit(1);
}}
"""
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chart_scrubbing_coalesces_pointer_updates_per_frame(self):
        self.assertIn('let scrubbing=false, scrubRaf=0, pendingClientX=null;', self.frontend)
        self.assertIn('scrubRaf=requestAnimationFrame(()=>{', self.frontend)
        self.assertIn('if(scrubRaf) return;', self.frontend)
        self.assertIn('cancelAnimationFrame(scrubRaf)', self.frontend)
        self.assertIn('CHART.scrubClip.setAttribute("width"', self.frontend)
        self.assertIn('CHART.scrubPath.getPointAtLength', self.frontend)
        self.assertIn('chartEl.addEventListener("pointerdown",start)', self.frontend)
        self.assertIn('try{chartEl.setPointerCapture(e.pointerId);}catch(_){}', self.frontend)
        self.assertIn('const wrapRect=wrap.getBoundingClientRect();', self.frontend)
        self.assertIn('const px=(rect.left-wrapRect.left)+(point.x/W)*rect.width', self.frontend)
        self.assertIn('e.preventDefault();', self.frontend)
        self.assertNotIn('.net .val.scrubbing{color:var(--blue)}', self.frontend)

    def test_bank_screenshot_import_keeps_file_until_analysis_finishes(self):
        self.assertIn('apiFetch("/v1/import/screenshot"', self.frontend)
        self.assertIn('analyzeScreenshot(file).finally(()=>{ scanFile.value=""; });', self.frontend)
        self.assertIn('class="scan-launch" id="scanOpen"', self.frontend)
        self.assertIn('class="scan-launch" id="scanPick"', self.frontend)
        self.assertIn('screenshot_invalid_response:', self.frontend)
        self.assertIn('screenshot_no_transactions:', self.frontend)
        self.assertIn('data.analysisStatus==="no_transactions"', self.frontend)
        opener = self.frontend.split("function openScreenshotImport(){", 1)[1].split("function scanSetState", 1)[0]
        self.assertIn('closeAllSheetsSoft();', opener)
        self.assertIn('openOnly("importsheet");', opener)
        self.assertNotIn('closeSheet();', opener)

    def test_goal_detail_uses_shared_swipe_sheet_and_neutral_header(self):
        self.assertIn('class="sheet goal-detail-sheet" id="gsheet"', self.frontend)
        self.assertIn('"gsheet"', self.frontend.split('const SWIPE_DISMISS_SHEET_IDS=', 1)[1].split(']);', 1)[0])
        self.assertNotIn('id="gClose"', self.frontend)
        self.assertIn('#gsheet.goal-detail-sheet', self.frontend)

    def test_goal_and_screenshot_actions_use_scoped_neutral_styles(self):
        self.assertIn('#gsheet.goal-detail-sheet #gInfo', self.frontend)
        self.assertIn('#gsheet.goal-detail-sheet .send', self.frontend)
        self.assertIn('#gsheet.goal-detail-sheet #gDel', self.frontend)
        self.assertIn('#sheet #scanOpen,#importsheet #scanPick', self.frontend)

    def test_consumer_debt_screen_uses_current_finance_profile_ui(self):
        debt_screen = self.frontend.split("function renderConsumerDebts", 1)[1].split(
            "async function setDebtStatus", 1
        )[0]
        self.assertIn('class="debt-heading"', debt_screen)
        self.assertIn('class="debt-select"', debt_screen)
        self.assertIn("Eine Hypothek wird separat", debt_screen)
        self.assertNotIn("data-debt-back", debt_screen)
        self.assertIn('"setsheet"', self.frontend.split("const SWIPE_DISMISS_SHEET_IDS=", 1)[1].split("];", 1)[0])

    def test_onboarding_keeps_mortgage_rate_property_bound(self):
        assets = self.frontend.split("function obBuildAssets", 1)[1].split(
            "// Budget-Vorschlag", 1
        )[0]
        finish = self.frontend.split("async function finishOnboarding", 1)[1].split(
            "// Der eingegebene Kontostand", 1
        )[0]
        self.assertIn('data-win="immo_rate"', self.frontend)
        self.assertIn("Die Hypothekenrate wird hier separat gespeichert", self.frontend)
        self.assertIn('rate:w.immo_rate||0', assets)
        self.assertIn("property_monthly_rate:OB.wealth.immo_rate||0", finish)
        self.assertNotIn("rate:OB.contracts.kredit||0", finish)

    def test_property_edit_requires_explicit_generic_credit_link_choice(self):
        property_flow = self.frontend.split("function ambiguousPropertyCreditContracts", 1)[1].split(
            "function saveImmoSheet", 1
        )[0]
        save = self.frontend.split("async function saveImmoSheet", 1)[1].split(
            "const CONTRACT_CATS", 1
        )[0]
        self.assertIn("window.confirm", property_flow)
        self.assertIn("window.prompt", property_flow)
        self.assertIn("0: keiner, separat anlegen", property_flow)
        self.assertIn("link_existing_contract_id", save)
        self.assertIn("if(!propertyCredit.decided) return;", save)
        self.assertIn("!serverContractLegacyRef(item)", property_flow)
        self.assertIn('String(item.n||"").trim().toLowerCase()==="kredit"', property_flow)

    def test_score_explanation_localizes_transliterated_german_copy(self):
        score = self.frontend.split("function renderScore(){", 1)[1].split(
            "// Faktor antippen", 1
        )[0]
        self.assertIn("const scoreDisplayText=value=>", score)
        self.assertIn("scoreDisplayText(f.n)", score)
        self.assertIn("scoreDisplayText(f.why", score)
        self.assertIn("scoreDisplayText(f.lever", score)

    def test_tabbar_waits_for_stable_initial_measurement_before_reveal(self):
        self.assertIn('class="tabbar tabbar-layout-pending"', self.frontend)
        self.assertIn('.tabbar.tabbar-layout-pending{visibility:hidden!important}', self.frontend)
        self.assertIn('function queueInitialReveal(height)', self.frontend)
        self.assertIn('bar.classList.remove("tabbar-layout-pending")', self.frontend)
        self.assertIn('initialStableFrames>=2', self.frontend)

    def test_frontend_build_check_reloads_only_once_for_a_new_server_build(self):
        build = re.search(r'<meta name="rove-frontend-build" content="([^"]+)">', self.frontend)
        self.assertIsNotNone(build)
        self.assertRegex(build.group(1), r"^frontend-[0-9a-f]{7,40}$|^frontend-__GIT_COMMIT__$")
        self.assertIn('fetch(location.pathname, {cache:"no-store", credentials:"same-origin"})', self.frontend)
        self.assertIn('meta[name="rove-frontend-build"]', self.frontend)
        self.assertIn('if(serverBuild===CURRENT_BUILD)', self.frontend)
        self.assertIn('sessionStorage.removeItem(RELOAD_KEY)', self.frontend)
        self.assertIn('sessionStorage.getItem(RELOAD_KEY)', self.frontend)
        self.assertIn('sessionStorage.setItem(RELOAD_KEY,serverBuild)', self.frontend)
        self.assertIn('if(attempted===serverBuild || mittendrin())', self.frontend)
        self.assertIn('next.searchParams.set("rove_build",serverBuild)', self.frontend)

    def test_service_worker_still_has_no_fetch_cache_handler(self):
        service_worker = (FRONTEND_PATH.parent / "sw.js").read_text(encoding="utf-8")
        self.assertNotIn('addEventListener("fetch"', service_worker)
        self.assertIn("self.skipWaiting()", service_worker)
        self.assertIn("self.clients.claim()", service_worker)


if __name__ == "__main__":
    unittest.main()
