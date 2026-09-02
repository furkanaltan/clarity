import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendDetailIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_generic_detail_header_reuses_neutral_icon_system(self):
        self.assertIn('class="gicon rov-icon detail-header-icon"', self.frontend)
        self.assertNotIn(
            'class="gicon" style="background:${a.tint}22;color:${a.tint};width:44px;height:44px"',
            self.frontend,
        )

    def test_external_asset_logos_remain_separate(self):
        self.assertIn("cryptoLogo||`", self.frontend)
        self.assertIn("investmentPositionMark(p)", self.frontend)
        self.assertIn("function cryptoHeaderLogo", self.frontend)


if __name__ == "__main__":
    unittest.main()
