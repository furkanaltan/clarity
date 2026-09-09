from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import rove_app_api as api
import rove_app_state as state
from rove_vehicle_financing import (
    VehicleFinancingConflictError,
    create_vehicle_financing,
    delete_vehicle_financing,
    ensure_vehicle_financing_schema,
    list_vehicle_financings,
    update_vehicle_financing,
)
from rove_vehicle_financing import _money
from test_financial_accounts_sprint2 import create_db


class VehicleFinancingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "clarity.db"
        create_db(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_vehicle_financing_schema(self.conn)
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.temp.cleanup)

    def payload(self, **overrides):
        value = {
            "vehicle_name": "Mercedes CLA",
            "financing_type": "financed",
            "purchase_price": 60000,
            "outstanding_balance": 20000,
            "monthly_payment": 600,
            "remaining_months": 20,
            "contract_end_date": "2028-05-01",
        }
        value.update(overrides)
        return value

    def test_create_links_one_contract_and_rate_has_one_source(self):
        financing_id = create_vehicle_financing(self.conn, 1, self.payload(), "req-1")
        rows = list_vehicle_financings(self.conn, 1)
        self.assertEqual([row["id"] for row in rows], [financing_id])
        self.assertEqual(rows[0]["paid_amount"], 40000.0)
        contract = self.conn.execute(
            "SELECT amount, category, name FROM app_contracts WHERE user_id=1 AND contract_id=?",
            (rows[0]["contract_id"],),
        ).fetchone()
        self.assertEqual(dict(contract), {"amount": 600.0, "category": "Mobilität", "name": "Mercedes CLA · Finanzierung"})

    def test_german_purchase_price_inputs_are_normalized_without_scaling(self):
        self.assertEqual(_money("60000"), _money("60.000"))
        self.assertEqual(_money("60000"), _money("60.000,00"))
        self.assertEqual(float(_money("60000")), 60000.0)
        financing_id = create_vehicle_financing(
            self.conn,
            1,
            self.payload(purchase_price="60.000", outstanding_balance="26.000"),
            "german-amounts",
        )
        row = next(item for item in list_vehicle_financings(self.conn, 1) if item["id"] == financing_id)
        self.assertEqual(row["purchase_price"], 60000.0)
        self.assertEqual(row["outstanding_balance"], 26000.0)
        self.assertEqual(row["paid_amount"], 34000.0)

    def test_retry_is_idempotent_and_changed_payload_conflicts(self):
        first = create_vehicle_financing(self.conn, 1, self.payload(), "req-1")
        self.assertEqual(create_vehicle_financing(self.conn, 1, self.payload(), "req-1"), first)
        with self.assertRaises(VehicleFinancingConflictError):
            create_vehicle_financing(self.conn, 1, self.payload(monthly_payment=650), "req-1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_vehicle_financings").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM app_contracts").fetchone()[0], 1)

    def test_update_is_atomic_and_keeps_rate_in_contract(self):
        financing_id = create_vehicle_financing(self.conn, 1, self.payload(), "req-1")
        update_vehicle_financing(self.conn, 1, financing_id, self.payload(outstanding_balance=18000, monthly_payment=620))
        row = list_vehicle_financings(self.conn, 1)[0]
        self.assertEqual(row["outstanding_balance"], 18000.0)
        self.assertEqual(row["monthly_payment"], 620.0)
        self.assertEqual(self.conn.execute("SELECT amount FROM app_contracts WHERE contract_id=?", (row["contract_id"],)).fetchone()[0], 620.0)

    def test_leasing_has_no_purchase_or_outstanding_balance(self):
        financing_id = create_vehicle_financing(self.conn, 1, self.payload(
            vehicle_name="Mercedes CLA Leasing", financing_type="leasing",
            purchase_price=None, outstanding_balance=None, monthly_payment=499,
        ), "lease-1")
        row = list_vehicle_financings(self.conn, 1)[0]
        self.assertEqual(row["id"], financing_id)
        self.assertIsNone(row["purchase_price"])
        self.assertIsNone(row["outstanding_balance"])
        self.assertIsNone(row["paid_amount"])

    def test_delete_removes_vehicle_and_linked_contract(self):
        financing_id = create_vehicle_financing(self.conn, 1, self.payload(), "req-1")
        contract_id = list_vehicle_financings(self.conn, 1)[0]["contract_id"]
        delete_vehicle_financing(self.conn, 1, financing_id)
        self.assertEqual(list_vehicle_financings(self.conn, 1), [])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM app_contracts WHERE contract_id=?", (contract_id,)).fetchone())

    def test_foreign_key_cascade_removes_vehicle_when_contract_is_deleted_directly(self):
        financing_id = create_vehicle_financing(self.conn, 1, self.payload(), "req-1")
        contract_id = list_vehicle_financings(self.conn, 1)[0]["contract_id"]
        self.conn.execute("DELETE FROM app_contracts WHERE user_id=? AND contract_id=?", (1, contract_id))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM app_vehicle_financings WHERE id=?", (financing_id,)).fetchone())

    def test_vehicle_financing_does_not_change_net_worth_or_consumer_debt(self):
        before = state.build_live_app_data(self.conn, 1)
        create_vehicle_financing(self.conn, 1, self.payload(), "req-1")
        after = state.build_live_app_data(self.conn, 1)
        self.assertEqual(after["netWorth"], before["netWorth"])
        self.assertEqual(after["consumerDebtTotal"], before["consumerDebtTotal"])


class VehicleFinancingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "clarity.db"
        create_db(self.path)
        self.patchers = [
            patch.object(api, "DB_PATH", self.path),
            patch.object(api, "user_from_token", lambda _conn, token: {"pilot-token": 1, "other-token": 2}.get(token)),
            patch.object(api, "build_live_app_data", lambda _conn, _user_id: {"vertraege": [], "vehicleFinancings": []}),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(self, method, path, payload, token="pilot-token"):
        with api.app.test_client() as client:
            return client.open(path, method=method, json=payload,
                               headers={"Authorization": f"Bearer {token}", "Origin": "https://getrove.de"})

    def test_api_request_id_retry_and_conflict(self):
        payload = {
            "request_id": "api-1", "vehicle_name": "Mercedes CLA", "financing_type": "financed",
            "purchase_price": 60000, "outstanding_balance": 20000, "monthly_payment": 600,
        }
        first = self.request("POST", "/v1/vehicle-financings", payload)
        retry = self.request("POST", "/v1/vehicle-financings", payload)
        conflict = self.request("POST", "/v1/vehicle-financings", {**payload, "monthly_payment": 601})
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(retry.status_code, 200, retry.get_json())
        self.assertEqual(first.get_json()["vehicleFinancingId"], retry.get_json()["vehicleFinancingId"])
        self.assertEqual(conflict.status_code, 409, conflict.get_json())

    def test_generic_contract_delete_is_blocked_for_vehicle_contract(self):
        payload = {
            "request_id": "api-vehicle-delete", "vehicle_name": "Mercedes CLA", "financing_type": "financed",
            "purchase_price": 60000, "outstanding_balance": 20000, "monthly_payment": 600,
        }
        created = self.request("POST", "/v1/vehicle-financings", payload)
        self.assertEqual(created.status_code, 200, created.get_json())
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            contract_id = conn.execute("SELECT contract_id FROM app_vehicle_financings WHERE user_id=1").fetchone()[0]
        blocked = self.request("POST", "/v1/contracts", {"action": "delete", "contract_id": contract_id})
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_contracts WHERE contract_id=?", (contract_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_vehicle_financings WHERE user_id=1").fetchone()[0], 1)

    def test_generic_contract_delete_still_works_for_normal_contract(self):
        created = self.request("POST", "/v1/contracts", {"action": "create", "name": "Normaler Vertrag", "category": "Abos", "amount": 12})
        self.assertEqual(created.status_code, 200, created.get_json())
        with closing(sqlite3.connect(self.path)) as conn:
            contract_id = conn.execute("SELECT contract_id FROM app_contracts WHERE name='Normaler Vertrag'").fetchone()[0]
        deleted = self.request("POST", "/v1/contracts", {"action": "delete", "contract_id": contract_id})
        self.assertEqual(deleted.status_code, 200, deleted.get_json())

    def test_foreign_vehicle_and_contract_operations_are_rejected(self):
        payload = {
            "request_id": "api-owned", "vehicle_name": "Mercedes CLA", "financing_type": "financed",
            "purchase_price": 60000, "outstanding_balance": 20000, "monthly_payment": 600,
        }
        created = self.request("POST", "/v1/vehicle-financings", payload)
        self.assertEqual(created.status_code, 200, created.get_json())
        financing_id = created.get_json()["vehicleFinancingId"]
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            contract_id = conn.execute("SELECT contract_id FROM app_vehicle_financings WHERE user_id=1").fetchone()[0]
        foreign_patch = self.request("PATCH", f"/v1/vehicle-financings/{financing_id}", {**payload, "vehicle_name": "Fremd"}, token="other-token")
        foreign_delete = self.request("DELETE", f"/v1/vehicle-financings/{financing_id}", {}, token="other-token")
        foreign_contract_delete = self.request("POST", "/v1/contracts", {"action": "delete", "contract_id": contract_id}, token="other-token")
        self.assertEqual(foreign_patch.status_code, 404)
        self.assertEqual(foreign_delete.status_code, 404)
        self.assertEqual(foreign_contract_delete.status_code, 404)

    def test_api_connections_enable_foreign_keys(self):
        with api.db() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
