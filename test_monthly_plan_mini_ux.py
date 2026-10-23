import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


class MonthlyPlanMiniUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")

    def test_monthly_plan_uses_neutral_dark_material(self):
        self.assertIn(
            "#monthlyplansheet .vd-btn{margin:10px 0 1px!important;"
            "min-height:46px;border-color:rgba(205,220,228,.2)",
            self.source,
        )
        self.assertIn(
            "#monthlyplansheet .set-card:has([data-month-close]),"
            "#monthlyplansheet .set-card:has([data-etf-due])",
            self.source,
        )
        self.assertNotIn("rgba(68,153,195,.28),rgba(31,92,125,.14)", self.source)
        self.assertNotIn("rgba(82,167,207,.16),transparent 49%", self.source)

    def test_monthly_plan_has_light_theme_material(self):
        self.assertIn(
            ':root[data-theme="light"] #monthlyplansheet .vd-btn{'
            "color:#31424a;border-color:rgba(67,90,108,.16)",
            self.source,
        )
        self.assertIn(
            ':root[data-theme="light"] #monthlyplansheet .etf-action.primary{'
            "color:#31424a;border-color:rgba(67,90,108,.16)",
            self.source,
        )

    def test_etf_savings_button_remains_clickable(self):
        self.assertIn('<button class="etf-plan-link" ${configureAttr}>', self.source)
        self.assertIn('data-etf-plan-detail="configure"', self.source)
        self.assertIn('data-etf-plan="configure"', self.source)
        self.assertIn('data-etf-plan-detail', self.source)
        self.assertIn('data-etf-plan', self.source)
        self.assertIn('syncEtfPlanAction(action)', self.source)

    def test_monthly_plan_actions_and_logic_hooks_remain(self):
        self.assertIn("function renderMonthlyPlan()", self.source)
        self.assertIn('data-monthly-setting="payday"', self.source)
        self.assertIn('data-monthly-plan="${action}"', self.source)
        self.assertIn('data-month-close="${action.month}"', self.source)
        self.assertIn('data-etf-due="${action.holdingId||"legacy"}"', self.source)
        self.assertIn('document.getElementById("monthlyplanbody").addEventListener("click"', self.source)


if __name__ == "__main__":
    unittest.main()
