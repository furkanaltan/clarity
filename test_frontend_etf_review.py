from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


class FrontendEtfReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")
        start = cls.source.index("function isEtfReviewQuestion")
        end = cls.source.index("function isAmbiguousInvestmentQuestion", start)
        cls.helpers = cls.source[start:end]

    def run_node(self, data, expression):
        script = (
            "const DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
            "function eur2(value) { return Number(value).toFixed(2) + \" €\"; }\n"
            "function announcementEscape(value) {\n"
            "  return String(value ?? \"\").replace(/[&<>\"']/g, char => ({\"&\":\"&amp;\",\"<\":\"&lt;\",\">\":\"&gt;\",\"\\\"\":\"&quot;\",\"'\":\"&#39;\"}[char]));\n"
            "}\n"
            + self.helpers
            + expression
        )
        result = subprocess.run(
            ["node", "--input-type=commonjs"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_chip_is_personal_review_and_advice_stays_outside(self):
        result = self.run_node({}, """
console.log(JSON.stringify({
  chip: isEtfReviewQuestion("ETF prüfen".toLowerCase()),
  advice: isEtfReviewQuestion("Welchen ETF soll ich kaufen?".toLowerCase())
}));
""")
        self.assertTrue(result["chip"])
        self.assertFalse(result["advice"])

    def test_summary_uses_only_etf_holdings_and_existing_facts(self):
        data = {
            "netWorthAvailable": True,
            "netWorth": 30000,
            "sts": {"etfSparrate": 50},
            "etfPlan": {"active": True, "amount": 0},
            "assets": [{
                "name": "ETF & Investments",
                "positions": [
                    {"n": "ETF A", "v": 6000, "assetType": "etf", "holding": True,
                     "contributions": 1200, "pendingContribution": 100,
                     "positionPlan": {"active": True, "amount": 200}},
                    {"n": "ETF B", "v": 4000, "assetType": "etf", "holding": True,
                     "contributions": 800, "pendingContribution": 0,
                     "positionPlan": {"active": True, "amount": 100}},
                    {"n": "Aktie X", "v": 20000, "assetType": "stock"},
                    {"n": "Restbetrag", "v": 5000, "unassigned": True}
                ]
            }]
        }
        result = self.run_node(data, "console.log(JSON.stringify(ans_etf_review()));")
        self.assertIn("ETF-Gesamtwert: <b>10000.00 €</b>", result)
        self.assertIn("Monatliche ETF-Sparrate: <b>300.00 €</b>", result)
        self.assertIn("ETF-Positionen: <b>2</b>", result)
        self.assertIn("Größte ETF-Position: <b>ETF A</b>", result)
        self.assertIn("Anteil am Gesamtvermögen: <b>33,33 %</b>", result)
        self.assertIn("Erfasste ETF-Beiträge: <b>2100.00 €</b>", result)
        self.assertNotIn("Aktie X", result)
        self.assertNotIn("Restbetrag", result)

    def test_empty_state_is_explicit(self):
        result = self.run_node({"assets": []}, "console.log(JSON.stringify(ans_etf_review()));")
        self.assertEqual(result, "Keine ETF-Positionen hinterlegt.")

    def test_summary_does_not_mutate_canonical_state(self):
        data = {"assets": [{"name": "ETF & Investments", "positions": [
            {"n": "ETF A", "v": 100, "assetType": "etf", "holding": True}
        ]}]}
        result = self.run_node(data, """
const before = JSON.stringify(DATA);
ans_etf_review();
console.log(JSON.stringify(before === JSON.stringify(DATA)));
""")
        self.assertTrue(result)

    def test_summary_escapes_untrusted_position_name(self):
        data = {"assets": [{"name": "ETF & Investments", "positions": [
            {"n": "<img src=x onerror=alert(1)>", "v": 100, "assetType": "etf", "holding": True}
        ]}]}
        result = self.run_node(data, "console.log(JSON.stringify(ans_etf_review()));")
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", result)
        self.assertNotIn("<img", result)

    def test_actual_dispatch_precedes_ai_fallback(self):
        route = "if(isEtfReviewQuestion(t)) return ans_etf_review();"
        self.assertIn('"ETF prüfen"', self.source)
        self.assertIn(route, self.source)
        self.assertLess(self.source.index(route), self.source.index("const ai=await askRoveAi(t)"))


if __name__ == "__main__":
    unittest.main()
