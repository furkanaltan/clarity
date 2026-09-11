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

    def run_chart_adapter(self, script):
        node_script = f"""
const DATA = {{
  netWorth: 34000,
  series: {{"1W":[30,31,32],"1M":[10,11],"6M":[1,2],"1J":[3,4]}},
  histDates: {{"1W":["8. Sep","9. Sep","Heute"],"1M":["8. Aug","Heute"],"6M":["Apr. 2026","Heute"],"1J":["Sept. 2025","Heute"]}}
}};
const today = new Date();
const todayKey = today.getFullYear()+"-"+String(today.getMonth()+1).padStart(2,"0")+"-"+String(today.getDate()).padStart(2,"0");
const PROFILE_META = {{netHistory:[
  {{d:todayKey,v:32000,t:today.getTime()+3600000}},
  {{d:todayKey,v:31000,t:today.getTime()+7200000}}
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

    def test_chart_adapter_overlays_local_today_without_mutating_server_state(self):
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
        self.assertNotIn('function catmullPath(', self.frontend)
        self.assertNotIn('if(win.length<2) win=[h[0], h[h.length-1]];', self.frontend)
        self.assertIn('id="chartContext"', self.frontend)
        self.assertNotIn('id="chartDelta" hidden', self.frontend)
        self.assertIn('data-r="1T">1T', self.frontend)
        self.assertIn('data-r="1J">1J', self.frontend)
        self.assertNotIn('data-r="Max"', self.frontend)
        self.assertIn('function chartDataForRange(range)', self.frontend)
        self.assertIn('const pts=chartDataForRange(range).pts;', self.frontend)
        self.assertIn('function chartTodayPoints()', self.frontend)
        self.assertIn('const todayPoints=chartTodayPoints();', self.frontend)
        self.assertNotIn('DATA.series["1T"]=intraday.map(point=>Math.round(Number(point.v))/1000);', self.frontend)
        self.assertIn('const stamp=Number(point.t);', self.frontend)
        self.assertIn('if(last && last.d===dk){', self.frontend)
        self.assertIn('if(Math.abs(Number(last.v)-value)>0.005) h.push({d:dk,v:value,t:now});', self.frontend)
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
        self.assertIn('PROFILE_META.netHistory=snapshot.netHistory.filter', self.frontend)
        self.assertIn('if(APP_MODE!=="profile" && APP_MODE!=="bridge") return;', self.frontend)
        self.assertIn('syncNetHistory();\n    rebuildSeriesFromHistory();\n    saveBridgeLocal();', self.frontend)
        self.assertIn('return h.filter(point=>point&&point.d===today&&Number.isFinite(Number(point.v)))', self.frontend)
        self.assertIn('return index===points.length-1 ? "Jetzt" : "Heute";', self.frontend)
        self.assertIn('Die lokale History wird ausschliesslich vom Chart-Adapter gelesen.', self.frontend)
        self.assertNotIn('DATA.histDates["1T"]=', self.frontend)
        self.assertNotIn('DATA.series[range]=win.map(s=>Math.round(s.v)/1000);', self.frontend)

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

    def test_tabbar_waits_for_stable_initial_measurement_before_reveal(self):
        self.assertIn('class="tabbar tabbar-layout-pending"', self.frontend)
        self.assertIn('.tabbar.tabbar-layout-pending{visibility:hidden!important}', self.frontend)
        self.assertIn('function queueInitialReveal(height)', self.frontend)
        self.assertIn('bar.classList.remove("tabbar-layout-pending")', self.frontend)
        self.assertIn('initialStableFrames>=2', self.frontend)

    def test_frontend_build_check_reloads_only_once_for_a_new_server_build(self):
        build = re.search(r'<meta name="rove-frontend-build" content="([^"]+)">', self.frontend)
        self.assertIsNotNone(build)
        self.assertRegex(build.group(1), r"^\d{8}-[0-9a-f]{7,40}$")
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
