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

    def test_contract_views_escape_names_and_restrict_tints(self):
        self.assertIn("function safeContractTint(value)", self.frontend)
        self.assertIn("const name=escapeAccountHtml(v.n), date=escapeAccountHtml(v.date);", self.frontend)
        self.assertIn('data-vn="${name}"', self.frontend)
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


if __name__ == "__main__":
    unittest.main()
