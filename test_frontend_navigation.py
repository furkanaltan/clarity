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


if __name__ == "__main__":
    unittest.main()
