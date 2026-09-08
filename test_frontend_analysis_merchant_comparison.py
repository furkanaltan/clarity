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

    def test_analysis_detail_uses_swipe_dismiss_without_back_navigation(self):
        self.assertNotIn('id="categoryDeepBack"', self.frontend)
        self.assertNotIn('getElementById("categoryDeepBack")', self.frontend)
        self.assertIn('"categorysheet"', re.search(
            r"const SWIPE_DISMISS_SHEET_IDS=Object\.freeze\(\[(?P<body>.*?)\]\);",
            self.frontend,
            re.DOTALL,
        ).group("body"))
        blocked = re.search(
            r"const SWIPE_BLOCKED_SHEET_IDS=Object\.freeze\(\[(?P<body>.*?)\]\);",
            self.frontend,
            re.DOTALL,
        ).group("body")
        self.assertNotIn('"categorysheet"', blocked)
        self.assertIn('class="grab category-deep-grab"', self.frontend)

    def test_analysis_merchant_rows_open_the_existing_detail_sheet(self):
        self.assertIn('class="analysis-preview-row analysis-merchant-row"', self.frontend)
        self.assertIn('class="analysis-detail-row analysis-merchant-row"', self.frontend)
        self.assertIn('class="category-deep-merchant" type="button"', self.frontend)
        self.assertIn("function openMerchantDeepDive(key)", self.frontend)
        self.assertIn(
            'analysisFilteredOutflows().filter(item=>analysisMerchantIdentity(item).key===key)',
            self.frontend,
        )
        self.assertIn(
            'openMerchantDeepDive(decodeURIComponent(merchant.dataset.analysisMerchantKey))',
            self.frontend,
        )

    def test_analysis_detail_has_one_primary_title(self):
        self.assertIn('id="categoryDeepTitle"', self.frontend)
        self.assertNotIn('<h2 id="categoryDeepTitle">', self.frontend)
        self.assertNotIn('class="category-deep-heading"', self.frontend)

    def test_open_analysis_creates_analysis_history_state(self):
        self.assertIn('go("analysis",{history:true});', self.frontend)


if __name__ == "__main__":
    unittest.main()
