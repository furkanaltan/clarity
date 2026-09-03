import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendAnalysisMerchantComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_current_calendar_month_hides_only_merchant_comparison(self):
        self.assertIn(
            "function analysisIsCurrentCalendarMonth(offset=analysisMonthOffset)",
            self.frontend,
        )
        merchants = re.search(
            r"function analysisMerchantsView\(merchants\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(merchants)
        body = merchants.group("body")
        self.assertIn("const isCurrentMonth=analysisIsCurrentCalendarMonth();", body)
        self.assertIn("const comparison=isCurrentMonth?null:analysisComparison", body)
        self.assertIn('${comparison?analysisChangeHtml(comparison):""}', body)

    def test_categories_keep_shared_comparison_formula_and_rendering(self):
        categories = re.search(
            r"function analysisCategoriesView\(categories,total\)\{(?P<body>.*?)\n\}",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(categories)
        body = categories.group("body")
        self.assertIn("const comparison=analysisComparison(category.amount", body)
        self.assertIn("${analysisChangeHtml(comparison)}", body)
        self.assertIn("const percent=Math.round(delta/previous*1000)/10;", self.frontend)

    def test_completed_month_merchant_comparison_keeps_negative_delta(self):
        self.assertIn(
            "const delta=Math.round((current-previous)*100)/100;",
            self.frontend,
        )
        self.assertIn(
            'const arrow=delta>0?"↑":"↓";',
            self.frontend,
        )
        self.assertIn(
            'return {kind:delta>0?"up":"down",text:',
            self.frontend,
        )


if __name__ == "__main__":
    unittest.main()
