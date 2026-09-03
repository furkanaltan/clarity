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
        self.assertIn('function closeSheet()', self.frontend)
        self.assertIn('if(sheet.classList.contains("on"))closeSheet()', self.frontend)


if __name__ == "__main__":
    unittest.main()
