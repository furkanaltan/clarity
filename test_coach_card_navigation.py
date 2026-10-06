from __future__ import annotations

import re
import unittest
from pathlib import Path


FRONTEND_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"


class CoachCardNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def test_card_surface_always_opens_coach(self):
        self.assertIn(
            'mentorCard.addEventListener("click",()=>openTalk())',
            self.frontend,
        )
        self.assertNotIn(
            'if(mentorCard.dataset.mentorDeepLink) openMentorCandidate(DATA.mentorCandidate,mentorCard.dataset.mentorDeepLink)',
            self.frontend,
        )
        self.assertNotIn(
            'else if((DATA.monthlyCheckinActions||[]).some(action=>action?.due&&!action?.completed)) openMonthlyPlan()',
            self.frontend,
        )

    def test_arrow_is_explicit_cta_with_coach_fallback(self):
        self.assertIn(
            'data-mentor-cta data-mentor-target="coach"',
            self.frontend,
        )
        self.assertIn('if(target==="score") openScore();', self.frontend)
        self.assertIn('else if(target==="report"||target==="reports") openReports();', self.frontend)
        self.assertIn('else openTalk();', self.frontend)

    def test_score_and_report_candidates_define_targets(self):
        self.assertRegex(
            self.frontend,
            re.compile(r"Dein Rov\.E Score:.*?target:\"score\"", re.S),
        )
        self.assertIn('setMentorCtaTarget("report");', self.frontend)

    def test_server_candidate_deep_link_is_not_card_navigation(self):
        self.assertIn('setMentorCtaTarget(deepLink);', self.frontend)
        self.assertIn(
            'if(deepLink && target!=="coach") openMentorCandidate(DATA.mentorCandidate,deepLink);',
            self.frontend,
        )
        self.assertIn(
            'if(route) openMentorCandidate(DATA.mentorCandidate,route);\n    else openTalk();',
            self.frontend,
        )

    def test_budget_content_never_deep_links_to_budget(self):
        self.assertIn('else if(a==="budget") openTalk();', self.frontend)
        self.assertNotIn(
            'else if(a==="budget"){ go("tx"); document.querySelector',
            self.frontend,
        )


if __name__ == "__main__":
    unittest.main()
