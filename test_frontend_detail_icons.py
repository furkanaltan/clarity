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

    def test_contract_detail_reuses_neutral_icon_system(self):
        self.assertIn('class="logo rov-icon rov-icon--regular detail-contract-icon"', self.frontend)
        self.assertNotIn(
            'class="logo rov-icon rov-icon--regular" style="background:${v.tint||\'#8FA8BC\'}22;color:${v.tint||\'#8FA8BC\'}"',
            self.frontend,
        )

    def test_expense_detail_neutralizes_only_generic_icons(self):
        self.assertIn("function transactionDetailLogo(t)", self.frontend)
        self.assertIn('class="logo rov-icon rov-icon--regular detail-expense-icon"', self.frontend)
        self.assertIn("if(merchantDomain(t?.n)) return transactionLogo(t);", self.frontend)
        self.assertIn("const detailLogo=transactionDetailLogo(item);", self.frontend)

    def test_profile_menu_uses_neutral_icons(self):
        self.assertIn("profile-menu-icon", self.frontend)
        self.assertIn("#psheet .profile-menu-icon", self.frontend)
        self.assertNotIn('class="pic rov-icon rov-icon--regular ${m.tone === "white" ? "is-white" : ""}"', self.frontend)


if __name__ == "__main__":
    unittest.main()
