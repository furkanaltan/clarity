import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"
STATE_PATH = Path(__file__).resolve().parent / "rove_app_state.py"


class FrontendTransferVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")
        cls.state = STATE_PATH.read_text(encoding="utf-8")

    def test_internal_transfer_has_explicit_presentation_discriminator(self):
        self.assertIn("function isInternalTransfer(transaction){", self.frontend)
        self.assertIn(
            "transaction?.transfer===true && transaction?.csid!=null && transaction?.targetAccountId!=null",
            self.frontend,
        )

    def test_render_tx_filters_only_visible_copy(self):
        render = re.search(
            r"function renderTx\(\)\{(?P<body>.*?)\n\}\nrenderTx\(\);",
            self.frontend,
            re.DOTALL,
        )
        self.assertIsNotNone(render)
        body = render.group("body")
        self.assertIn("const visibleSrc = src.map", body)
        self.assertIn("filter(t=>!isInternalTransfer(t))", body)
        self.assertIn("const src = isCurrent ? DATA.tx", body)
        self.assertNotIn("DATA.tx=", body)

    def test_normal_expense_and_transfer_paths_remain_distinct(self):
        self.assertIn('"transfer": True', self.state)
        self.assertIn('"targetAccountId"', self.state)
        self.assertIn('"sid": r["id"]', self.state)

    def test_dynamic_transfer_has_stable_request_id_for_retries(self):
        self.assertIn("PENDING_TRANSFER_REQUEST", self.frontend)
        self.assertIn("request_id:transferId", self.frontend)
        self.assertIn("PENDING_TRANSFER_REQUEST=null", self.frontend)


if __name__ == "__main__":
    unittest.main()
