from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


class FrontendLiquidityCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND.read_text(encoding="utf-8")

    def test_score_explanation_uses_monthly_fixed_costs(self):
        score = (ROOT / "rove_score.py").read_text(encoding="utf-8")
        bot = (ROOT / "bot.py").read_text(encoding="utf-8")
        state = (ROOT / "rove_app_state.py").read_text(encoding="utf-8")

        self.assertIn("Cash-Puffer im Verhältnis zu deinen monatlichen Fixkosten", self.frontend)
        self.assertIn("deine hinterlegten monatlichen Fixkosten abdeckt", bot)
        self.assertIn("hinterlegten monatlichen Fixkosten", score)
        self.assertIn("hinterlegten monatlichen Fixkosten", state)
        for source in (self.frontend, bot, score, state):
            self.assertNotIn("notwendigen Monatsausgaben", source)

    def test_emergency_fund_is_not_a_new_goal_preset_and_existing_goal_is_preserved(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required to exercise the goal-preset source")

        ng_presets = re.search(r"const NG_PRESETS\s*=\s*\[.*?\];", self.frontend, re.S).group()
        onboarding_goals = re.search(r"const OB_GOALS\s*=\s*\[.*?\];", self.frontend, re.S).group()
        goal_style = re.search(
            r"function goalStyle\(name\)\s*\{.*?\n\}", self.frontend, re.S
        ).group()
        existing_goal = {"t": "Notgroschen", "cur": 1250, "tar": 10000, "tint": "#35D07F"}
        script = "\n".join(
            [
                ng_presets,
                onboarding_goals,
                f"const DATA={{goals:[{json.dumps(existing_goal, ensure_ascii=False)}]}};",
                goal_style,
                "const before=JSON.stringify(DATA.goals);",
                "const existingStyle=goalStyle('Notgroschen');",
                "if(NG_PRESETS.some(([name])=>name==='Notgroschen')) throw Error('normal preset remains');",
                "if(OB_GOALS.some(({n})=>n==='Notgroschen')) throw Error('onboarding preset remains');",
                "if(existingStyle.icon!=='shield') throw Error('existing goal classification changed');",
                "if(JSON.stringify(DATA.goals)!==before) throw Error('saved goal was mutated');",
                "console.log('PASS');",
            ]
        )
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "PASS")


if __name__ == "__main__":
    unittest.main()
