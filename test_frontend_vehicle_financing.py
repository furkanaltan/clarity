import re
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendVehicleFinancingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")

    def test_vehicle_financing_has_contracts_entry_and_state_hydration(self):
        self.assertIn('"Fahrzeugfinanzierung"', self.source)
        self.assertIn('data-vccat="${c}"', self.source)
        self.assertNotIn('id="addVehicleFinancingBtn"', self.source)
        self.assertIn('id="vehiclefinancingsheet"', self.source)
        self.assertIn('Array.isArray(data.vehicleFinancings)', self.source)
        self.assertIn('vehicleFinancingByContract', self.source)

    def test_financing_and_leasing_fields_are_distinct(self):
        self.assertIn('id="vfTypeChoice"', self.source)
        self.assertIn('data-vftype="financed"', self.source)
        self.assertIn('data-vftype="leasing"', self.source)
        self.assertIn('purchase_price:vehicleFinancingType==="financed"?purchase_price:null', self.source)
        self.assertIn('outstanding_balance:vehicleFinancingType==="financed"?outstanding_balance:null', self.source)

    def test_vehicle_edit_and_delete_use_vehicle_endpoint(self):
        self.assertRegex(self.source, r'/v1/vehicle-financings/\$\{vehicleFinancingEditId\}')
        self.assertRegex(self.source, r'/v1/vehicle-financings/\$\{Number\(vehicleFinancingByContract\(v\)\.id\)\}')
        self.assertIn('id="vdVehicleEdit"', self.source)

    def test_no_vehicle_data_is_routed_to_net_worth_or_debt(self):
        self.assertNotIn('DATA.netWorth += vehicle', self.source)
        self.assertNotIn('consumerDebtTotal += vehicle', self.source)

    def test_vehicle_amounts_are_normalized_and_paid_amount_is_derived_numerically(self):
        self.assertIn('function normalizeVehicleFinancing(row)', self.source)
        self.assertIn('function vehicleMoney(raw)', self.source)
        self.assertIn('paid_amount:purchase_price!=null&&outstanding_balance!=null', self.source)
        self.assertIn('Math.round((purchase_price-outstanding_balance)*100)/100', self.source)
        self.assertIn('data.vehicleFinancings.map(normalizeVehicleFinancing)', self.source)

    def test_vehicle_chip_opens_existing_create_flow(self):
        self.assertIn('if(b.dataset.vccat==="Fahrzeugfinanzierung")', self.source)
        self.assertIn('openVehicleFinancingSheet(); buzz(); return;', self.source)

    def test_leasing_toggle_stays_inside_open_sheet(self):
        self.assertIn('const vehicleFinancingSheet=document.getElementById("vehiclefinancingsheet")', self.source)
        self.assertIn('vehicleFinancingSheet.addEventListener("click",event=>', self.source)
        self.assertIn('const toggle=event.target.closest("[data-vftype]")', self.source)
        self.assertIn('event.preventDefault();', self.source)
        self.assertIn('event.stopPropagation();', self.source)
        self.assertIn('setVehicleFinancingType(toggle.dataset.vftype);', self.source)
        start = self.source.index('const vehicleFinancingSheet=')
        end = self.source.index('document.getElementById("vcCats")', start)
        handler = self.source[start:end]
        self.assertNotIn('querySelectorAll("#vehiclefinancingsheet [data-vftype]")', handler)
        self.assertNotIn('closeSheet();', handler)

    def test_leasing_toggle_uses_one_stable_delegated_handler(self):
        start = self.source.index('const vehicleFinancingSheet=')
        end = self.source.index('document.getElementById("vcCats")', start)
        handler = self.source[start:end]
        self.assertEqual(handler.count('vehicleFinancingSheet.addEventListener("click"'), 1)
        self.assertIn('setVehicleFinancingType(toggle.dataset.vftype);', handler)
        self.assertIn('return;', handler)

    def test_type_choice_is_create_only_while_detail_is_static(self):
        self.assertIn('typeChoice.hidden=vehicleFinancingEditId!==null;', self.source)
        self.assertIn('const detailSubtitle=vehicle', self.source)
        detail = self.source[self.source.index('function openContract('):self.source.index('document.getElementById("addContractBtn")')]
        self.assertNotIn('data-vftype=', detail)
        self.assertNotIn('<span class="k">Art</span>', detail)

    def test_vehicle_contract_row_shows_static_type_and_derived_annual_cost(self):
        self.assertIn('vehicle?.vehicle_name||v.n', self.source)
        self.assertIn('${eur2(v.a*12)} / Jahr', self.source)
        self.assertIn('${vehicle.remaining_months} Monate', self.source)
        self.assertIn('/ Monat</small>', self.source)

    def test_create_toggle_click_updates_real_field_visibility(self):
        render_start = self.source.index('function renderVehicleFinancingType(){')
        render_end = self.source.index('function openVehicleFinancingSheet', render_start)
        handler_start = self.source.index('const vehicleFinancingSheet=document.getElementById("vehiclefinancingsheet");')
        handler_end = self.source.index('document.getElementById("vcCats")', handler_start)
        runtime = self.source[render_start:render_end] + self.source[handler_start:handler_end]
        script = f'''const assert=require("assert");
let clickHandler=null, vehicleFinancingEditId=null, vehicleFinancingType="financed";
const buttons=["financed","leasing"].map(value=>({{dataset:{{vftype:value}},classList:{{on:false,toggle(name,on){{this[name]=on;}}}}}}));
const elements={{
  vfTypeChoice:{{hidden:false,querySelectorAll:()=>buttons}},
  vfPurchaseWrap:{{hidden:false}}, vfBalanceWrap:{{hidden:false}},
  vehiclefinancingsheet:{{addEventListener(type,handler){{if(type==="click")clickHandler=handler;}},contains:target=>buttons.includes(target)}}
}};
global.document={{getElementById:id=>elements[id]}};
function buzz(){{}}
{runtime}
const leasing=buttons[1];
let prevented=false, stopped=false;
clickHandler({{target:{{closest:selector=>selector==="[data-vftype]"?leasing:null}},preventDefault:()=>{{prevented=true;}},stopPropagation:()=>{{stopped=true;}}}});
assert.equal(vehicleFinancingType,"leasing");
assert.equal(elements.vfPurchaseWrap.hidden,true);
assert.equal(elements.vfBalanceWrap.hidden,true);
assert.equal(buttons[1].classList.on,true);
assert.equal(prevented,true); assert.equal(stopped,true);
setVehicleFinancingType("financed");
assert.equal(elements.vfPurchaseWrap.hidden,false);
assert.equal(elements.vfBalanceWrap.hidden,false);
assert.equal(buttons[0].classList.on,true);
vehicleFinancingEditId=7; renderVehicleFinancingType();
assert.equal(elements.vfTypeChoice.hidden,true);
'''
        subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)

    def test_vehicle_create_inputs_are_labeled_and_native(self):
        for field, label in (("vfName", "Fahrzeug"), ("vfPurchase", "Kaufpreis"),
                             ("vfBalance", "Finanzierung offen"), ("vfPayment", "Monatsrate"),
                             ("vfMonths", "Restlaufzeit"), ("vfEnd", "Vertragsende")):
            self.assertIn(f'for="{field}">{label}', self.source)
        self.assertIn('id="vfMonths" type="text" inputmode="numeric" pattern="[0-9]*"', self.source)
        self.assertIn('id="vfEnd" type="date"', self.source)
        self.assertIn('class="vehicle-term-alternative">oder optional</div>', self.source)
        self.assertNotIn('pin-pad-input" id="vf', self.source)


if __name__ == "__main__":
    unittest.main()
