from __future__ import annotations

import os
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

    def test_home_asset_add_field_has_local_bottom_spacing(self):
        self.assertIn(
            '<div class="card home-assets-card" style="padding:2px 16px 18px" id="assets"></div>',
            self.frontend,
        )
        self.assertIn('.account-add{width:100%;min-height:50px;', self.frontend)

    def test_home_chart_uses_white_drawn_line_without_changing_data(self):
        self.assertIn('@keyframes chartLineDraw', self.frontend)
        self.assertIn('.chart .chart-line-main{stroke-dasharray:1;', self.frontend)
        self.assertIn('const glassLine=(d,animate)=>', self.frontend)
        self.assertIn('glassLine(fullLine,true)', self.frontend)
        self.assertIn('stroke="#F5F7F8"', self.frontend)
        self.assertIn('stroke="#FFFFFF"', self.frontend)
        self.assertIn('id="chartScrubClipRect"', self.frontend)
        self.assertIn('id="chartScrubPath"', self.frontend)
        self.assertIn('clip-path="url(#chartScrubClip)"', self.frontend)
        self.assertNotIn('id="chartStroke"', self.frontend)

    def test_chart_scrubbing_coalesces_pointer_updates_per_frame(self):
        self.assertIn('let scrubbing=false, scrubRaf=0, pendingClientX=null;', self.frontend)
        self.assertIn('scrubRaf=requestAnimationFrame(()=>{', self.frontend)
        self.assertIn('if(scrubRaf) return;', self.frontend)
        self.assertIn('cancelAnimationFrame(scrubRaf)', self.frontend)
        self.assertIn('CHART.scrubClip.setAttribute("width"', self.frontend)
        self.assertIn('CHART.scrubPath.getPointAtLength', self.frontend)
        self.assertNotIn('.net .val.scrubbing{color:var(--blue)}', self.frontend)

    def test_frontend_build_check_reloads_only_once_for_a_new_server_build(self):
        self.assertIn('<meta name="rove-frontend-build" content="2026-09-04-frontend-1">', self.frontend)
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
