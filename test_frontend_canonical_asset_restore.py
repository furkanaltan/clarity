from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


def section(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    return source[start_at : source.index(end, start_at)]


class CanonicalAssetRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = FRONTEND.read_text(encoding="utf-8")
        cls.classification = section(
            cls.html, "const BRIDGE_BOT_ASSET_NAMES", "function readBridgeLocal()"
        )
        cls.bridge_storage = section(
            cls.html, "const BRIDGE_BOT_ASSET_NAMES", "const PAIR_API_BASE_URL"
        )
        cls.asset_merge = section(
            cls.html, "const ASSET_ORDER_KEY_BY_NAME=", "function applyAssetOrder()"
        )
        cls.net_worth = section(
            cls.html, "function setCanonicalNetWorth(value){", "// Girokonto/Tagesgeld/Bargeld direkt"
        )
        cls.summary = section(
            cls.html, "function renderHomeSachwerteSummary(){", "// Wer ETF/Krypto beim Onboarding"
        )
        cls.home_state = section(
            cls.html, "function homeHasFinancialData(){", "function applyHomeEmpty()"
        )

    def run_node(self, script: str) -> object:
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_restore_only_accepts_sachwerte_and_preserves_legacy_snapshot(self):
        script = f"""
const assert=require('node:assert/strict');
const BRIDGE_LOCAL_KEY='rove-bridge-local-v1';
let APP_MODE='bridge';
let DATA={{assets:[
  {{name:'Girokonto',value:10000,source:'bot'}},
  {{name:'ETF & Investments',value:8000,source:'app'}},
  {{name:'Immobilie',value:30000,source:'app'}}
],vertraege:[],goals:[],budgets:[]}};
let PROFILE_META={{netHistory:[],netHistorySchemaVersion:2,coverageBoundaries:{{}}}};
const original={{userId:7,assets:[
  {{name:'Sachwerte',value:2000,source:'app',manual:true,positions:[]}},
  {{name:'ETF Depot',value:500,source:'app'}},
  {{name:'Unbekannter Altwert',value:800,source:'app'}},
  {{name:'Girokonto',value:9000,source:'app'}},
  {{name:'Immobilie',value:12000,source:'app',real:{{eigenkapital:12000}}}}
],budgets:[],vertraege:[],goals:[],netHistory:[]}};
const store=new Map([[BRIDGE_LOCAL_KEY,JSON.stringify(original)]]);
const localStorage={{getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value)}};
function validatedNetHistory(value){{return Array.isArray(value)?value:[];}}
{self.bridge_storage}
BRIDGE_USER_ID=7;
const before=localStorage.getItem(BRIDGE_LOCAL_KEY);
restoreBridgeLocal(readBridgeLocal(),7);
const unchangedAfterRestore=before===localStorage.getItem(BRIDGE_LOCAL_KEY);
const active=DATA.assets.map(asset=>[asset.name,asset.value]).sort((a,b)=>a[0].localeCompare(b[0]));
saveBridgeLocal();
const persisted=JSON.parse(localStorage.getItem(BRIDGE_LOCAL_KEY));
console.log(JSON.stringify({{active,unchangedAfterRestore,persisted:persisted.assets.map(asset=>[asset.name,asset.value]).sort((a,b)=>a[0].localeCompare(b[0]))}}));
"""
        result = self.run_node(script)
        self.assertTrue(result["unchangedAfterRestore"])
        self.assertEqual(
            result["active"],
            [["ETF & Investments", 8000], ["Girokonto", 10000], ["Immobilie", 30000], ["Sachwerte", 2000]],
        )
        self.assertEqual(
            result["persisted"],
            [["ETF Depot", 500], ["Girokonto", 9000], ["Immobilie", 12000], ["Sachwerte", 2000], ["Unbekannter Altwert", 800]],
        )

    def test_server_assets_merge_with_only_explicit_local_sachwerte(self):
        script = f"""
const assert=require('node:assert/strict');
{self.classification}
{self.asset_merge}
const server=[
  {{name:'Girokonto',value:10000,source:'financial-account'}},
  {{name:'ETF & Investments',value:8000,source:'app'}},
  {{name:'Immobilie',value:30000,source:'app'}}
];
const current=[
  {{name:'Sachwerte',value:2000,source:'app',assetKey:'asset:valuables'}},
  {{name:'ETF Depot',value:500,source:'app'}},
  {{name:'Unbekannter Altwert',value:800,source:'app'}},
  {{name:'Girokonto',value:9000,source:'app'}}
];
const multi=mergeServerAssets(server,current,true).map(asset=>[asset.name,asset.value]);
const single=mergeServerAssets(server,current,false).map(asset=>[asset.name,asset.value]);
console.log(JSON.stringify({{multi,single}}));
"""
        result = self.run_node(script)
        expected = [["Girokonto", 10000], ["ETF & Investments", 8000], ["Immobilie", 30000], ["Sachwerte", 2000]]
        self.assertEqual(result["multi"], expected)
        self.assertEqual(result["single"], expected)

    def test_bridge_net_worth_uses_server_value_without_recounting_assets_or_debt(self):
        script = f"""
const assert=require('node:assert/strict');
let APP_MODE='bridge';
const display={{dataset:{{}},textContent:''}};
const document={{getElementById:()=>display}};
const DATA={{assets:[{{value:5000}},{{value:8000}},{{name:'Immobilie',value:14000}},{{name:'Sachwerte',value:2000}}],consumerDebtTotal:6600,netWorth:null,netWorthAvailable:false}};
function eur2(value){{return new Intl.NumberFormat('de-DE',{{minimumFractionDigits:2}}).format(value)+' €';}}
{self.net_worth}
setCanonicalNetWorth(20400);recalcNetWorth();
const bridge={{value:DATA.netWorth,text:display.textContent}};
setCanonicalNetWorth(null);recalcNetWorth();
const unavailable=DATA.netWorth;
APP_MODE='profile';recalcNetWorth();
console.log(JSON.stringify({{bridge,unavailable,profile:DATA.netWorth}}));
"""
        result = self.run_node(script)
        self.assertEqual(result["bridge"], {"value": 20400, "text": "20.400,00 €"})
        self.assertIsNone(result["unavailable"])
        self.assertEqual(result["profile"], 29000)

    def test_sachwerte_summary_is_separate_and_hidden_when_empty(self):
        script = f"""
const assert=require('node:assert/strict');
let APP_MODE='bridge';
let BRIDGE_LIVE_STATE_READY=true;
const line={{hidden:true,textContent:''}};
const document={{getElementById:id=>id==='homeSachwerteSummary'?line:null}};
const DATA={{assets:[
  {{name:'Sachwerte',value:2000,source:'app'}},
  {{name:'ETF Depot',value:500,source:'app'}},
  {{name:'Unbekannter Altwert',value:800,source:'app'}}
]}};
function classifyBridgeAsset(asset){{return asset?.name==='Sachwerte'?'local-valuables':'unknown';}}
function eur2(value){{return `${{Number(value).toLocaleString('de-DE')}} €`;}}
{self.summary}
renderHomeSachwerteSummary();
const visible={{hidden:line.hidden,text:line.textContent}};
DATA.assets[0].value=0;renderHomeSachwerteSummary();
const empty={{hidden:line.hidden,text:line.textContent}};
console.log(JSON.stringify({{visible,empty}}));
"""
        result = self.run_node(script)
        self.assertEqual(
            result["visible"],
            {"hidden": False, "text": "Sachwerte separat: 2.000 € · nicht im Vermögensverlauf"},
        )
        self.assertEqual(result["empty"], {"hidden": True, "text": ""})

    def test_local_sachwerte_alone_do_not_activate_the_canonical_home_chart(self):
        script = f"""
let APP_MODE='bridge';
let BRIDGE_LIVE_STATE_READY=true;
const DATA={{assets:[{{name:'Sachwerte',value:2000,source:'app'}}],financialAccounts:[],netWorth:0,netWorthAvailable:false,series:{{}}}};
function classifyBridgeAsset(asset){{return asset?.name==='Sachwerte'?'local-valuables':'server';}}
{self.home_state}
const localOnly=homeHasFinancialData();
DATA.assets.push({{name:'Girokonto',value:1,source:'financial-account'}});
const canonical=homeHasFinancialData();
console.log(JSON.stringify({{localOnly,canonical}}));
"""
        self.assertEqual(self.run_node(script), {"localOnly": False, "canonical": True})

    def test_server_net_worth_is_read_from_both_api_state_paths(self):
        self.assertIn("setCanonicalNetWorth(data.netWorth)", self.html)
        self.assertIn("setCanonicalNetWorth(b.netWorth)", self.html)
        self.assertIn("renderHomeSachwerteSummary();", section(self.html, "function renderAssets(){", "function missingAssetRow()"))

    def test_inline_javascript_syntax(self):
        for attrs, source in re.findall(r"<script([^>]*)>(.*?)</script>", self.html, re.S):
            if "src=" in attrs or not source.strip():
                continue
            result = subprocess.run(
                ["node", "--check"], input=source, text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_day_chart_adapter_remains_unchanged(self):
        baseline = subprocess.run(
            ["git", "show", "HEAD:frontend/index.html"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        marker = "function chartDataForRange(range){"
        self.assertEqual(
            section(self.html, marker, "function normalizeChartSeriesV2("),
            section(baseline, marker, "function normalizeChartSeriesV2("),
        )


if __name__ == "__main__":
    unittest.main()
