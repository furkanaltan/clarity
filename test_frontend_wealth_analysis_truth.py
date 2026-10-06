from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


def section(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    return source[start_at : source.index(end, start_at)]


class WealthAnalysisFinancialTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = FRONTEND.read_text(encoding="utf-8")
        cls.class_meta = section(source, "const WEALTH_CLASS_META={", "const analysisEsc=")
        cls.net_worth = section(
            source,
            "function setCanonicalNetWorth(value){",
            "// Girokonto/Tagesgeld/Bargeld direkt",
        )
        cls.analysis_functions = section(
            source, "function wealthAnalysisData(", "function renderAnalysis()"
        )

    def run_node(self, assertions: str) -> None:
        script = f"""
const assert=require('node:assert/strict');
const ICONS={{wallet:'wallet',coins:'coins',chart:'chart',bitcoin:'bitcoin',house:'house',gem:'gem'}};
const analysisEsc=value=>String(value??'').replace(/[&<>\"']/g,char=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}})[char]);
const eur=value=>Number(value).toLocaleString('de-DE')+' €';
const eur2=value=>Number(value).toLocaleString('de-DE',{{minimumFractionDigits:2,maximumFractionDigits:2}})+' €';
const analysisArcPath=()=>'';
const emptyCard=()=>'';
let wealthSelectedClass='';
let APP_MODE='bridge';
const homeValue={{dataset:{{}},textContent:''}};
const document={{getElementById:id=>id==='networth'?homeValue:null}};
let DATA={{
  netWorth:20400,
  netWorthAvailable:true,
  consumerDebtTotal:6600,
  assets:[
    {{name:'Girokonto',value:10000}},
    {{name:'ETF & Investments',value:8000,positions:[{{n:'ETF',v:8000}}]}},
    {{name:'Immobilie',value:9000,real:{{eigenkapital:9000,marketValue:180000,remainingDebt:171000}}}},
    {{name:'Sachwerte',value:2000}}
  ]
}};
{self.class_meta}
{self.net_worth}
{self.analysis_functions}
{assertions}
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_analysis_uses_canonical_net_worth_and_matches_home(self) -> None:
        self.run_node("""
const data=wealthAnalysisData();
recalcNetWorth();
const view=wealthAnalysisView();
assert.equal(data.positiveTotal,29000);
assert.equal(data.canonicalNetWorth,20400);
assert.equal(homeValue.textContent,'20.400,00 €');
assert.ok(view.includes('20.400 €'));
assert.ok(view.includes('Gesamtvermögen'));
""")

    def test_local_sachwerte_do_not_change_analysis_net_worth(self) -> None:
        self.run_node("""
const before=wealthAnalysisData();
DATA.assets.find(asset=>asset.name==='Sachwerte').value=10000;
const after=wealthAnalysisData();
assert.equal(before.canonicalNetWorth,20400);
assert.equal(after.canonicalNetWorth,20400);
assert.equal(after.positiveTotal,37000);
""")

    def test_consumer_debt_is_not_subtracted_twice(self) -> None:
        self.run_node("""
const data=wealthAnalysisData();
assert.equal(DATA.consumerDebtTotal,6600);
assert.equal(data.canonicalNetWorth,20400);
assert.notEqual(data.canonicalNetWorth,20400-DATA.consumerDebtTotal);
""")

    def test_missing_canonical_value_does_not_fall_back_to_asset_sum(self) -> None:
        self.run_node("""
DATA.netWorth=null;
DATA.netWorthAvailable=false;
const view=wealthAnalysisView();
assert.ok(view.includes('>—</text>'));
assert.ok(view.includes('>Nicht verfügbar</text>'));
assert.ok(!view.includes('29.000 €'));
""")

    def test_property_equity_is_represented_once_in_distribution(self) -> None:
        self.run_node("""
const data=wealthAnalysisData();
const propertyClass=data.classes.find(item=>item.name==='Immobilien');
const propertyPositions=data.positions.filter(item=>item.className==='Immobilien');
assert.equal(propertyClass.amount,9000);
assert.equal(propertyPositions.length,1);
assert.equal(propertyPositions[0].value,9000);
""")

    def test_donut_explains_positive_segments_and_selected_class(self) -> None:
        self.run_node("""
let view=wealthAnalysisView();
assert.ok(view.includes('Vermögensverteilung'));
assert.ok(view.includes('Die Segmente zeigen positive Vermögenswerte.'));
assert.ok(view.includes('Nettovermögen oder der Wert der ausgewählten Klasse'));
assert.ok(view.includes('>Immobilien</span>'));
wealthSelectedClass='Immobilien';
view=wealthAnalysisView();
assert.ok(view.includes('>9.000 €</text>'));
assert.ok(view.includes('>Immobilien</text>'));
""")


if __name__ == "__main__":
    unittest.main()
