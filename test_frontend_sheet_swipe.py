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


class FrontendSheetSwipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")
        start = cls.frontend.index("const SWIPE_DISMISS_SHEET_IDS")
        end = cls.frontend.index("const SHEET_SWIPE_RUNTIME", start)
        cls.swipe_config = cls.frontend[start:end]

    def test_shared_helper_covers_detail_sheets(self):
        for sheet_id in ("sheet", "actsheet", "txsheet", "vdsheet", "dsheet", "repsheet", "scoresheet", "talksheet", "monthlyplansheet"):
            with self.subTest(sheet_id=sheet_id):
                self.assertIn(f'"{sheet_id}"', self.swipe_config)
        self.assertIn("function initSheetSwipeDismiss()", self.frontend)
        self.assertIn("initSheetSwipeDismiss();", self.frontend)

    def test_account_details_share_dsheet_and_monthly_plan_is_safe_to_dismiss(self):
        self.assertIn('document.getElementById("dsheet").classList.add("on")', self.frontend)
        self.assertIn('function openMonthlyPlan(){', self.frontend)
        self.assertIn('"monthlyplansheet"', self.swipe_config)

    def test_admin_edit_sheet_remains_blocked(self):
        self.assertIn('"adminsheet"', self.frontend[self.frontend.index("const SWIPE_BLOCKED_SHEET_IDS"):])

    def test_coach_uses_swipe_without_redundant_close_button(self):
        self.assertIn('id="talksheet"', self.frontend)
        self.assertNotIn('id="talkClose"', self.frontend)
        self.assertNotIn(".chat-close", self.frontend)

    def test_sensitive_and_edit_forms_remain_blocked(self):
        blocked = self.frontend[self.frontend.index("const SWIPE_BLOCKED_SHEET_IDS"):]
        for sheet_id in ("importsheet", "gsheet", "bsheet", "immosheet", "contractsheet", "deletesheet"):
            with self.subTest(sheet_id=sheet_id):
                self.assertIn(f'"{sheet_id}"', blocked)

    def test_swipe_reuses_existing_close_path(self):
        self.assertIn('if(sheet.classList.contains("on"))closeSheet()', self.frontend)
        self.assertIn("sheetHasScrolledContent(event.target,sheet)", self.frontend)
        self.assertIn("dy<=0||Math.abs(dx)>Math.abs(dy)", self.frontend)
        self.assertIn("current.distance>=current.height*.3", self.frontend)
        self.assertNotIn('sheet.classList.contains("tall")&&!fromGrab', self.frontend)

    def test_small_and_cancelled_gestures_settle_back(self):
        self.assertIn('sheet.style.transform="translate3d(0,0,0)"', self.frontend)
        self.assertIn('sheet.classList.add("swipe-settling")', self.frontend)
        self.assertIn('sheet.addEventListener("pointercancel",snapBack)', self.frontend)


if __name__ == "__main__":
    unittest.main()
