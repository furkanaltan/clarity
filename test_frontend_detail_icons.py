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

    def test_asset_detail_header_uses_only_asset_glass(self):
        self.assertIn('<div class="asset-glass-slot" aria-hidden="true">${assetIcon(a)}</div>', self.frontend)
        self.assertNotIn(
            'class="gicon" style="background:${a.tint}22;color:${a.tint};width:44px;height:44px"',
            self.frontend,
        )

    def test_external_asset_logos_remain_separate(self):
        self.assertIn("cryptoPositionMark(p)", self.frontend)
        self.assertIn("investmentPositionMark(p)", self.frontend)
        detail = self.frontend.split('const detailTitle=', 1)[1].split('const cashMeta=', 1)[0]
        self.assertNotIn("cryptoHeaderLogo(", detail)

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

    def test_account_order_uses_asset_glass_without_list_ridges(self):
        self.assertIn('class="asset-glass-slot asset-order-glass">${assetIcon(asset)}', self.frontend)
        self.assertIn(".asset-order-glass .rv-glass::before{display:none}", self.frontend)
        self.assertNotIn('class="gicon asset-icon asset-order-icon" style="background:${asset.tint}22;color:${asset.tint}"', self.frontend)

    def run_icon_js(self, expression, extra=""):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the frontend icon helper tests")
        helper = self.frontend.split("function moduleIcon(name){", 1)[1].split("// Empty State", 1)[0]
        assets = self.frontend.split("function assetIcon(asset){", 1)[1].split("// Wer ETF/Krypto", 1)[0]
        profile = self.frontend.split("const PICONS = {", 1)[1].split("function renderProfileMenu", 1)[0]
        script = "function moduleIcon(name){" + helper + "function assetIcon(asset){" + assets
        script += "const PICONS = {" + profile + f"\nconsole.log(JSON.stringify({expression}));"
        if extra:
            script = script.replace("console.log(JSON.stringify(", extra + "\nconsole.log(JSON.stringify(", 1)
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
            expected_viewbox = "0 0 24 24" if symbol.attrib["id"].startswith("rove-module-asset-") else "0 0 28 28"
            self.assertEqual(symbol.attrib["viewBox"], expected_viewbox)

    def test_navigation_uses_glass_badges_without_changing_quick_entry(self):
        nav = ET.fromstring(re.search(r'<nav class="tabbar.*?</nav>', self.frontend, re.S).group())
        buttons = nav.findall("button")
        self.assertEqual(len(buttons), 5)
        self.assertEqual([button.get("data-tab") for button in buttons], ["home", "ziele", None, "tx", "vertraege"])
        self.assertEqual(buttons[2].get("id"), "addBtn")
        self.assertEqual(buttons[2].get("aria-label"), "Schnell erfassen")
        self.assertEqual(buttons[2].find('div').get('class'), "plus")
        self.assertEqual(buttons[2].find('div/svg/path').get('d'), "M12 5v14M5 12h14")
        for button, glyph in zip([buttons[0], buttons[1], buttons[3], buttons[4]],
                                 ["home", "goals", "cashflow", "contracts"]):
            badge = button.find('span[@class="rv-glass rv-glass--44"]')
            self.assertIsNotNone(badge)
            icon = badge.find("svg")
            self.assertIn("rv-glass-symbol", icon.get("class"))
            self.assertEqual(icon.get("viewBox"), "0 0 28 28")
            self.assertEqual(icon.find("use").get("href"), f"#rove-module-{glyph}")
        self.assertIn("#app .tab.on>.rv-glass", self.frontend)
        self.assertIn("#app .tab.add .plus{width:42px;height:42px;border-radius:50%", self.frontend)
        self.assertNotIn('.tabbar > .tab::before{content:none}', self.frontend)
        self.assertNotIn('.tabbar > .tab:active:not(.add){transform:none', self.frontend)
        self.assertIn('.tab:active:not(.add){transform:scale(.94);opacity:.8}', self.frontend)

    def test_seven_asset_symbols_have_the_requested_tabler_outline_style(self):
        sprite = ET.fromstring(re.search(r'<svg class="rove-icon-library".*?</svg>', self.frontend, re.S).group())
        ns = "{http://www.w3.org/2000/svg}"
        symbols = [item for item in sprite.findall(f".//{ns}symbol") if item.get("id").startswith("rove-module-asset-")]
        self.assertEqual({item.get("id").removeprefix("rove-module-asset-") for item in symbols},
                         {"building-bank", "pig-money", "cash", "trending-up", "circle-letter-b", "building", "diamond"})
        for symbol in symbols:
            for key, value in {"viewBox": "0 0 24 24", "fill": "none", "stroke": "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round"}.items():
                self.assertEqual(symbol.get(key), value)
            self.assertGreater(len(symbol.findall(f"{ns}path")), 0)

    def test_renamed_accounts_keep_type_icons_on_home_and_detail(self):
        result = self.run_icon_js("""['checking','savings','wallet'].map(accountType=>{
          const account={name:'Mein Konto',accountType,icon:'bank'};
          return {home:homeAssetIcon(account),detail:assetIcon(account)};
        })""")
        for pair, symbol in zip(result, ["building-bank", "pig-money", "cash"]):
            self.assertEqual(pair["home"], pair["detail"])
            self.assertIn(f'href="#rove-module-asset-{symbol}"', pair["home"])

    def test_legacy_assets_use_the_same_icons_as_named_accounts(self):
        result = self.run_icon_js("""['Girokonto','Tagesgeld','Bargeld','ETF & Investments',
          'ETF Depot','Krypto','Immobilie','Sachwerte'].map(name=>{
            const asset={name};return [homeAssetIcon(asset),assetIcon(asset)];
          })""")
        for (home, detail), symbol in zip(result, ["building-bank", "pig-money", "cash", "trending-up", "trending-up", "circle-letter-b", "building", "diamond"]):
            self.assertEqual(home, detail)
            self.assertIn('class="rv-glass"', home)
            self.assertIn(f'href="#rove-module-asset-{symbol}"', home)
        self.assertEqual(result[3], result[4])

    def test_feature_families_share_glass_without_changing_icon_helpers(self):
        result = self.run_icon_js("""({keys:Object.keys(ICONS).filter(key=>ICONS[key].includes('rv-glass')),
          profile:Object.values(PICONS).join(''),unknown:assetGlassIcon('__proto__'),
          unsafe:assetGlassIcon('<img src=x>'),old:moduleIcon('checking')})""")
        self.assertEqual(set(result["keys"]), {"assetBank", "assetSavings", "assetWallet", "assetChart", "assetBitcoin", "assetProperty", "assetGem"})
        self.assertNotIn('rv-glass', result["profile"])
        self.assertNotIn('rv-glass', result["old"])
        self.assertEqual(result["unknown"], "")
        self.assertEqual(result["unsafe"], "")
        for selector in (
            "#app .gicon", "#app .prow .pic", "#app .support-icon", "#app .analysis-category-icon",
            "#app .wealth-class-icon", "#app .logo[style*=\"color:\"]", "#app .ob-item .iic",
            "#app .act .ai", "#app .announce-icon", "#app .detail-setting-icon",
            "#app .vks-status-mark", "#app .tx .logo:not(.merchant)",
        ):
            self.assertIn(selector, self.frontend)
        self.assertIn("#app .act .ai.is-warning", self.frontend)
        self.assertIn("#app .vks-status--failure .vks-status-mark", self.frontend)
        self.assertIn("#app .tab.on>.rv-glass", self.frontend)
        self.assertIn("#app .rv-glass .rove-module-icon", self.frontend)
        self.assertIn("--rove-icon-ink:#fff!important;--rove-icon-accent:#fff!important", self.frontend)
        self.assertIn('data-tab="vertraege"', self.frontend)

    def test_all_wealth_class_icons_use_the_shared_asset_glyphs(self):
        wealth_meta = "const WEALTH_CLASS_META={" + self.frontend.split(
            "const WEALTH_CLASS_META={", 1
        )[1].split("\n};", 1)[0] + "\n};"
        result = self.run_icon_js(
            "Object.entries(WEALTH_CLASS_META).map(([name,value])=>[name,value.icon])",
            wealth_meta,
        )
        expected = {
            "Cash": "building-bank", "Tagesgeld": "pig-money", "ETFs": "trending-up",
            "Aktien": "trending-up", "Krypto": "circle-letter-b", "Immobilien": "building",
            "Sonstige": "diamond",
        }
        self.assertEqual({name: re.search(r'href="#rove-module-asset-([^\"]+)', icon).group(1)
                          for name, icon in result}, expected)

    def test_feature_icon_glass_preserves_the_animated_rovi_face(self):
        self.assertNotIn("#app .mentor .face", self.frontend)
        self.assertNotIn("#app .thead .face", self.frontend)
        self.assertIn(".mentor .face .eye{transform-origin:center;animation:blink 4.6s infinite}", self.frontend)
        self.assertIn(".thead .face .eye{transform-origin:center;animation:blink 4.6s infinite}", self.frontend)
        self.assertIn("#app .prow .pic", self.frontend)
        self.assertIn("#app .support-icon", self.frontend)

    def test_ask_rovi_is_only_a_display_label(self):
        result = self.run_icon_js("({item:PMENU.find(item=>item.k==='talk'),icon:PICONS.chat})")
        self.assertEqual(result["item"]["l"], "Ask Rovi")
        self.assertEqual(result["item"]["ic"], "chat")
        self.assertIn('href="#rove-module-talk"', result["icon"])
        sheet = self.frontend.split('id="talksheet"', 1)[1].split('id="chat"', 1)[0]
        self.assertIn('<div class="tt">Ask Rovi</div>', sheet)

    def test_account_order_renderer_uses_seven_asset_badges_not_action_badges(self):
        render = 'function renderAssetOrderSheet(){' + self.frontend.split(
            'function renderAssetOrderSheet(){', 1)[1].split('function openAssetOrder(){', 1)[0]
        result = self.run_icon_js("""(()=>{
          const body={}; const document={getElementById:()=>body};
          const currentAssetOrderItems=()=>['Girokonto','Tagesgeld','Bargeld','ETF Depot',
            'Krypto','Immobilie','Sachwerte'].map(name=>({name}));
          const assetOrderKey=asset=>asset.name;
        """ + render + "renderAssetOrderSheet();return body.innerHTML;})()")
        self.assertEqual(result.count('class="rv-glass"'), 7)
        self.assertEqual(result.count('class="asset-glass-slot asset-order-glass"'), 7)
        self.assertEqual(result.count('class="asset-order-grip"'), 7)
        self.assertEqual(result.count('class="asset-order-move"'), 14)
        self.assertEqual(len(set(re.findall(r'href="#rove-module-asset-([^"]+)"', result))), 7)

    def test_asset_type_picker_keeps_seven_types_and_original_actions(self):
        section = self.frontend.split('const ASSET_ADD_OPTIONS=', 1)[1].split('function openFinancialAccountAdd(){', 1)[0]
        result = self.run_icon_js("""(()=>{
          const body={}; const document={getElementById:()=>body};
          const ACCOUNT_TYPE_META={checking:{label:'Girokonto',icon:'assetBank'},
            savings:{label:'Tagesgeld',icon:'assetSavings'},wallet:{label:'Bargeld',icon:'assetWallet'}};
          const ASSET_ADD_OPTIONS=""" + section + """
          renderFinancialAccountAdd(); const types=body.innerHTML;
          NEW_FINANCIAL_ACCOUNT_TYPE='checking';renderFinancialAccountAdd();
          return {types,accounts:body.innerHTML};
        })()""")
        self.assertEqual(result['types'].count('class="rv-glass"'), 7)
        self.assertEqual(result['accounts'].count('class="rv-glass"'), 3)
        self.assertEqual(len(set(re.findall(r'data-asset-kind="([^"]+)"', result['types']))), 7)
        self.assertIn('id="newAccountSave"', result['accounts'])

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
