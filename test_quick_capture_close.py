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
        self.assertIn('id="quickClose"', self.frontend)
        self.assertIn('aria-label="Schnellerfassen schließen"', self.frontend)
        self.assertIn('document.getElementById("quickClose").addEventListener("click",closeSheet)', self.frontend)
        self.assertIn('sheetBg.addEventListener("click",closeSheet)', self.frontend)
        self.assertIn('e.key==="Escape" && sheet.classList.contains("on")', self.frontend)

    def test_close_target_is_mobile_sized(self):
        self.assertIn('#sheet .sheet-dismiss{top:8px;width:40px;height:40px}', self.frontend)


if __name__ == "__main__":
    unittest.main()
