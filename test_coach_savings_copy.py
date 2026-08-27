from __future__ import annotations

import os
import unittest
from pathlib import Path


FRONTEND_PATH = Path(
    os.environ.get(
        "ROVE_FRONTEND_PATH",
        str(Path(__file__).resolve().parent.parent / "rove-app" / "index.html"),
    )
)


class CoachSavingsCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_high_planned_savings_is_not_a_repeating_coach_lever(self):
        self.assertIn('const savingsAwaitingClose = score.next_lever==="Sparrate"', self.frontend)
        self.assertIn('!score.savings_confirmed && Number(score.savings_ratio||0)>=0.20', self.frontend)
        self.assertIn('score.next_lever && !savingsAwaitingClose', self.frontend)

    def test_low_or_confirmed_savings_stays_actionable(self):
        self.assertIn('Number(score.savings_ratio||0)>=0.20', self.frontend)
        self.assertNotIn('score.next_lever!=="Sparrate"', self.frontend)


if __name__ == "__main__":
    unittest.main()
