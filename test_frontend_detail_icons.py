import json
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
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

    def test_contract_views_escape_names_and_restrict_tints(self):
        self.assertIn("function safeContractTint(value)", self.frontend)
        self.assertIn("const contractName=escapeAccountHtml(v.n), date=escapeAccountHtml(v.date);", self.frontend)
        self.assertIn("const name=escapeAccountHtml(vehicle?.vehicle_name||v.n);", self.frontend)
        self.assertIn('data-vn="${contractName}"', self.frontend)
        self.assertIn("const contractName=escapeAccountHtml(v.n), category=escapeAccountHtml(group.cat);", self.frontend)
        self.assertIn('data-vn="${contractName}"', self.frontend)
        self.assertIn("const tint = safeContractTint(g.items[0].tint);", self.frontend)

    def test_server_contract_delete_preserves_the_canonical_id(self):
        self.assertIn("function serverContractId(item)", self.frontend)
        self.assertIn('item.source!=="bot"&&!serverContractId(item)', self.frontend)
        self.assertIn('(group.items||[]).filter(item=>!serverContractId(item)).forEach', self.frontend)
        self.assertIn('data-contract-id="${escapeAccountHtml(id)}"', self.frontend)
        self.assertIn('if(!await syncContract("delete",{contract_id:serverContractId(v)})) return;', self.frontend)
        self.assertIn('action==="delete" ? "Vertrag konnte nicht gelöscht werden."', self.frontend)
        self.assertIn("Vertrag konnte nicht eindeutig zugeordnet werden.", self.frontend)

    def test_property_contracts_use_property_persistence(self):
        self.assertIn('"monthly_rate","house_fee","management_fee"', self.frontend)
        self.assertIn("async function syncPropertyContract(field,value)", self.frontend)
        self.assertIn("const propertyField=propertyContractField(v);", self.frontend)
        self.assertIn("syncPropertyContract(propertyField,amt)", self.frontend)
        self.assertIn("syncPropertyContract(propertyField,0)", self.frontend)
        self.assertNotIn('syncContract("update",{contract_id:v.id,amount:amt})', self.frontend)

    def test_bridge_does_not_restore_stale_server_property_from_local_storage(self):
        self.assertIn('asset.name==="Immobilie"', self.frontend)
        self.assertIn('asset.real', self.frontend)

    def test_expense_detail_neutralizes_only_generic_icons(self):
        self.assertIn("function transactionDetailLogo(t)", self.frontend)
        self.assertIn('class="logo rov-icon rov-icon--regular detail-expense-icon"', self.frontend)
        self.assertIn("if(merchantDomain(t?.n)) return transactionLogo(t);", self.frontend)
        self.assertIn("const detailLogo=transactionDetailLogo(item);", self.frontend)

    def test_profile_menu_uses_neutral_icons(self):
        self.assertIn("profile-menu-icon", self.frontend)
        self.assertIn("#psheet .profile-menu-icon", self.frontend)
        self.assertNotIn('class="pic rov-icon rov-icon--regular ${m.tone === "white" ? "is-white" : ""}"', self.frontend)

    def test_account_order_uses_local_neutral_icon_language(self):
        self.assertIn('class="gicon rov-icon rov-icon--regular asset-order-icon"', self.frontend)
        self.assertIn("#assetordersheet .asset-order-icon", self.frontend)
        self.assertNotIn('class="gicon asset-icon asset-order-icon" style="background:${asset.tint}22;color:${asset.tint}"', self.frontend)

    def run_icon_js(self, expression):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the frontend icon helper tests")
        helper = self.frontend.split("function moduleIcon(name){", 1)[1].split("// Empty State", 1)[0]
        assets = self.frontend.split("function assetIcon(asset){", 1)[1].split("// Wer ETF/Krypto", 1)[0]
        profile = self.frontend.split("const PICONS = {", 1)[1].split("function renderProfileMenu", 1)[0]
        script = "function moduleIcon(name){" + helper + "function assetIcon(asset){" + assets
        script += "const PICONS = {" + profile + f"\nconsole.log(JSON.stringify({expression}));"
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_module_references_resolve_to_passive_local_symbols(self):
        sprite = re.search(r'<svg class="rove-icon-library".*?</svg>', self.frontend, re.S).group()
        tree = ET.fromstring(sprite)
        ns = "{http://www.w3.org/2000/svg}"
        symbols = tree.findall(f".//{ns}symbol")
        ids = [symbol.attrib["id"] for symbol in symbols]
        self.assertEqual(len(ids), len(set(ids)))
        allowed_tags = {"svg", "defs", "symbol", "g", "path", "rect", "circle"}
        for element in tree.iter():
            self.assertIn(element.tag.removeprefix(ns), allowed_tags)
        rendered = self.run_icon_js("[...Object.values(ICONS), ...Object.values(PICONS)].join('')")
        for reference in re.findall(r'<use href="([^"]+)"', rendered + self.frontend):
            if "${" in reference:
                continue
            self.assertTrue(reference.startswith("#rove-module-"))
            self.assertIn(reference[1:], ids)
        for symbol in symbols:
            self.assertEqual(symbol.attrib["viewBox"], "0 0 28 28")

    def test_renamed_accounts_keep_type_icons_on_home_and_detail(self):
        result = self.run_icon_js("""['checking','savings','wallet'].map(accountType=>{
          const account={name:'Mein Konto',accountType,icon:'bank'};
          return {home:homeAssetIcon(account),detail:assetIcon(account)};
        })""")
        for pair, symbol in zip(result, ["checking", "savings", "cash"]):
            self.assertEqual(pair["home"], pair["detail"])
            self.assertIn(f'href="#rove-module-{symbol}"', pair["home"])

    def test_legacy_assets_use_the_same_icons_as_named_accounts(self):
        result = self.run_icon_js("""['Girokonto','Tagesgeld','Bargeld','ETF & Investments',
          'ETF Depot','Krypto','Immobilie','Sachwerte'].map(name=>{
            const asset={name};return [homeAssetIcon(asset),assetIcon(asset)];
          })""")
        for home, detail in result:
            self.assertEqual(home, detail)
            self.assertIn('class="rove-module-icon"', home)
        self.assertEqual(result[3], result[4])

    def test_score_menu_keeps_route_and_uses_compass_not_support_star(self):
        result = self.run_icon_js("({route:PMENU.find(m=>m.k==='score').k,icon:PICONS[PMENU.find(m=>m.k==='score').ic],star:PICONS.star})")
        self.assertEqual(result["route"], "score")
        self.assertIn('href="#rove-module-score"', result["icon"])
        self.assertNotEqual(result["icon"], result["star"])

    def test_module_icons_are_decorative_and_reject_non_module_keys(self):
        result = self.run_icon_js("({icon:moduleIcon('savings'),unknown:moduleIcon('https://example.invalid/icon'),prototype:moduleIcon('__proto__'),fallback:assetIcon({name:'Custom',icon:'plane'}),motif:ic('plane')})")
        self.assertIn('aria-hidden="true"', result["icon"])
        self.assertIn('focusable="false"', result["icon"])
        self.assertEqual(result["unknown"], "")
        self.assertEqual(result["prototype"], "")
        self.assertEqual(result["fallback"], result["motif"])


if __name__ == "__main__":
    unittest.main()
