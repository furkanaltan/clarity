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


class QuickCaptureCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_quick_capture_has_explicit_close_paths(self):
        self.assertIn('sheetBg.addEventListener("click",closeSheet)', self.frontend)
        self.assertIn('e.key==="Escape" && sheet.classList.contains("on")', self.frontend)
        self.assertIn('"sheet"', self.frontend[self.frontend.index("const SWIPE_DISMISS_SHEET_IDS"):])
        self.assertNotIn('id="quickClose"', self.frontend)
        self.assertNotIn('getElementById("quickClose")', self.frontend)

    def test_quick_capture_has_swipe_and_overlay_dismissal(self):
        self.assertIn('function closeSheet(', self.frontend)
        self.assertIn('if(sheet.classList.contains("on"))closeSheet()', self.frontend)

    def test_mobile_input_sheet_owns_keyboard_viewport_without_hiding_native_ui(self):
        self.assertIn('MOBILE_INPUT_REGRESSION_GUARD', self.frontend)
        self.assertIn('#sheet.keyboard-open{bottom:var(--keyboard-cover,0px)', self.frontend)
        self.assertIn('document.body.classList.toggle("quick-input-keyboard-open",!!keyboardOpen)', self.frontend)
        self.assertIn('body.quick-input-keyboard-open .screen{visibility:hidden}', self.frontend)
        self.assertNotIn('keyboard-accessory', self.frontend.lower())

    def test_closed_sheets_are_not_focusable_and_quick_form_has_one_input(self):
        self.assertIn('el.toggleAttribute("inert",!active)', self.frontend)
        self.assertIn('el.setAttribute("aria-hidden",active?"false":"true")', self.frontend)
        self.assertIn('new MutationObserver(syncSheetInteractivity)', self.frontend)
        start = self.frontend.index('<div class="sheet" id="sheet">')
        end = self.frontend.index('<div class="sheet tall" id="importsheet">', start)
        quick_sheet = self.frontend[start:end]
        self.assertEqual(quick_sheet.count('<input '), 1)
        self.assertIn('id="quickIn"', quick_sheet)
        self.assertIn('id="quickSend" type="submit"', quick_sheet)
        self.assertIn('enterkeyhint="done"', quick_sheet)

    def test_keyboard_state_is_cleared_on_close(self):
        self.assertIn('document.body.classList.remove("quick-input-keyboard-open")', self.frontend)
        self.assertIn('sheet.classList.remove("keyboard-open")', self.frontend)
        self.assertIn('sheet.style.removeProperty("--sheet-viewport-height")', self.frontend)


if __name__ == "__main__":
    unittest.main()
