from __future__ import annotations

import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


class AccountDetailUxCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_account_action_subtitles_are_removed_without_removing_actions(self):
        self.assertNotIn("Geld zwischen deinen Konten verschieben.", self.frontend)
        self.assertNotIn(
            "Nur verwenden, wenn sich der Stand außerhalb von Rov.E geändert hat.",
            self.frontend,
        )
        self.assertIn('id="cashMoveSend"', self.frontend)
        self.assertIn('id="cashSetSend"', self.frontend)
        self.assertIn('id="cashRenameSend"', self.frontend)

    def test_transfer_note_and_account_role_assignment_remain_available(self):
        self.assertIn("Dein Gesamtvermögen bleibt dabei gleich.", self.frontend)
        self.assertIn('class="account-standard-usage"', self.frontend)
        self.assertIn("Standard-Verwendung", self.frontend)
        self.assertIn(
            "Lege fest, welches Konto Rov.E standardmäßig verwendet.",
            self.frontend,
        )
        self.assertNotIn(
            '<details class="account-standard-usage" open',
            self.frontend,
        )
        self.assertIn('data-account-role="${role}"', self.frontend)
        self.assertIn(
            'financialAccountRequest("/v1/financial-account-roles","POST"',
            self.frontend,
        )

    def test_account_detail_actions_use_neutral_material_styles(self):
        self.assertIn(
            ".detail-action-card.primary{border-color:var(--line2)",
            self.frontend,
        )
        self.assertIn(
            ".detail-action-card .vd-btn{margin-top:10px",
            self.frontend,
        )
        self.assertIn(
            '#dbody .detail-setting-icon{color:var(--muted)',
            self.frontend,
        )


if __name__ == "__main__":
    unittest.main()
