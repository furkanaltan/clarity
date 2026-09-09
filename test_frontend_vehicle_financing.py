import re
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


if __name__ == "__main__":
    unittest.main()
