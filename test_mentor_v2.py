from __future__ import annotations

import unittest
from pathlib import Path

from rove_app_state import build_mentor_candidate


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


class MentorV2Tests(unittest.TestCase):
    @staticmethod
    def score(**overrides):
        value = {
            "value": 80,
            "budget": 18,
            "savings": 17,
            "liquidity": 17,
            "debt": 30,
            "tracking": 8,
            "savings_ratio": 0.20,
            "liquidity_months": 3,
            "consumer_debt_total": 0,
            "tracking_days_90": 30,
        }
        value.update(overrides)
        return value

    @staticmethod
    def candidate(**overrides):
        value = {
            "score": MentorV2Tests.score(),
            "budget_truth": {
                "free_month_remaining": 500,
                "category_remaining": 300,
            },
            "monthly_actions": [],
            "goals": [{"t": "Notgroschen"}],
            "contracts": [],
            "reports": [],
            "income": 3000,
            "fixed_costs": 900,
            "debt_status": "none",
        }
        value.update(overrides)
        return build_mentor_candidate(**value)

    def test_negative_budget_beats_lower_priority_signals(self):
        result = self.candidate(
            budget_truth={"free_month_remaining": -120, "category_remaining": 50},
            monthly_actions=[{"kind": "month_close", "due": True, "completed": False}],
            score=self.score(tracking_days_90=0),
        )
        self.assertEqual(result["type"], "budget_overrun")
        self.assertEqual(result["priority"], 100)
        self.assertEqual(result["deep_link"], "analysis")

    def test_consumer_debt_beats_tracking(self):
        result = self.candidate(
            score=self.score(
                consumer_debt_total=15000,
                consumer_debt_count=2,
                debt=18,
                tracking_days_90=0,
            ),
        )
        self.assertEqual(result["type"], "consumer_debt")
        self.assertIn("Konsumschulden", result["message"])
        self.assertIn("Hypothek", result["message"])

    def test_weak_liquidity_is_prioritized(self):
        result = self.candidate(
            score=self.score(liquidity=4, liquidity_months=0.5, tracking_days_90=30),
        )
        self.assertEqual(result["type"], "liquidity")
        self.assertEqual(result["deep_link"], "score")

    def test_unknown_debt_is_honest_and_not_treated_as_none(self):
        result = self.candidate(debt_status="unknown")
        self.assertEqual(result["type"], "debt_unknown")
        self.assertIn("noch nicht", result["message"])
        self.assertNotEqual(result["type"], "consumer_debt")

    def test_mortgage_does_not_create_consumer_debt_warning(self):
        result = self.candidate(
            score=self.score(debt=27, mortgage_penalty=3),
            debt_status="none",
        )
        self.assertNotEqual(result["type"], "consumer_debt")

    def test_due_monthly_action_is_used_without_higher_blocker(self):
        result = self.candidate(
            monthly_actions=[{
                "kind": "income",
                "due": True,
                "completed": False,
                "title": "Gehalt bestätigen",
                "detail": "Prüfe deinen Monatsplan.",
            }],
        )
        self.assertEqual(result["type"], "monthly_action")
        self.assertEqual(result["deep_link"], "monthly-checkin")
        self.assertEqual(result["title"], "Gehalt bestätigen")

    def test_due_monthly_action_beats_only_a_tracking_nudge(self):
        result = self.candidate(
            score=self.score(tracking_days_90=0),
            monthly_actions=[{"due": True, "completed": False, "kind": "month_close"}],
        )
        self.assertEqual(result["type"], "monthly_action")

    def test_lower_priority_sources_are_considered_deterministically(self):
        report = self.candidate(
            reports=[{"month": "2026-08", "status": "ready"}],
        )
        self.assertEqual(report["type"], "report")
        contracts = self.candidate(
            contracts=[{"cat": "Verträge", "items": [{"n": "Miete"}]}],
        )
        self.assertEqual(contracts["type"], "contracts")

    def test_all_candidate_links_are_existing_safe_routes(self):
        allowed = {"analysis", "score", "settings", "monthly-checkin", "reports", "contracts", "goals"}
        cases = [
            self.candidate(budget_truth={"free_month_remaining": -1, "category_remaining": 0}),
            self.candidate(score=self.score(consumer_debt_total=1, debt=10)),
            self.candidate(score=self.score(liquidity=4, liquidity_months=0.5)),
            self.candidate(score=self.score(savings=5, savings_ratio=0.05)),
            self.candidate(debt_status="unknown"),
            self.candidate(monthly_actions=[{"due": True, "completed": False}]),
            self.candidate(score=self.score(tracking_days_90=0)),
            self.candidate(reports=[{"status": "ready"}]),
            self.candidate(contracts=[{"items": []}]),
            self.candidate(goals=[]),
            self.candidate(score=self.score(value=80)),
        ]
        self.assertTrue(all(item["deep_link"] in allowed for item in cases))

    def test_candidate_has_the_complete_server_contract(self):
        result = self.candidate()
        self.assertEqual(
            set(result),
            {"id", "priority", "type", "title", "message", "action_label", "deep_link", "reason"},
        )

    def test_frontend_prefers_server_candidate_only_in_bridge(self):
        frontend = FRONTEND.read_text(encoding="utf-8")
        self.assertIn("DATA.mentorCandidate=data.mentor_candidate||null", frontend)
        candidate_branch = frontend.index('const serverCandidate=APP_MODE==="bridge" && DATA.mentorCandidate;')
        due_branch = frontend.index("if(dueActions.length)", candidate_branch)
        self.assertLess(candidate_branch, due_branch)
        self.assertIn('else if(a==="candidate") openFeatureDeepLink', frontend)
        self.assertIn("mentorFactorLabel", frontend)

    def test_visible_mentor_text_uses_german_umlauts(self):
        frontend = FRONTEND.read_text(encoding="utf-8")
        self.assertIn("Liquidität", frontend)
        self.assertIn("Datenqualität", frontend)
        self.assertIn("Konsumschulden", frontend)


if __name__ == "__main__":
    unittest.main()
