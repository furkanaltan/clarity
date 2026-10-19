from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
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

    def run_report_navigation(self, candidate, reports, *, events=(), route="reports"):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for navigation interaction tests")
        source = self.frontend
        handler = source[source.index("async function openMentorCandidate("):source.index("\nmentorLine();", source.index("async function openMentorCandidate("))]
        routes = source[source.index("function openFeatureDeepLink("):source.index("async function openFeatureAnnouncement(")]
        detail = source[source.index("function openReportDetail("):source.index("// Sheet-Wechsel:")]
        archive = re.search(r"function openReports\(\)\{[^\n]+", source).group()
        setup = {"reports": reports, "mentorEvents": list(events)}
        script = f"""
const DATA={json.dumps(setup)}, calls=[];
function openOnly(id,render){{if(render)render();calls.push(['sheet',id]);}}
function renderReports(){{calls.push(['archive']);}}
function renderReportDetail(index){{calls.push(['detail',DATA.reports[index].month]);}}
async function markReportOpened(month){{calls.push(['opened',month]);return true;}}
function showToast(message){{calls.push(['message',message]);}}
function openMonthlyPlan(){{calls.push(['monthly-plan']);}}
{archive}
{detail}
{routes}
{handler}
(async()=>{{
  const opened=await openMentorCandidate({json.dumps(candidate)},{json.dumps(route)});
  process.stdout.write(JSON.stringify({{opened,calls}}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_report_cta_opens_its_exact_month_and_marks_it_only_once(self):
        result = self.run_report_navigation(
            {"type": "report", "id": "report:2026-09"},
            [
                {"month": "2026-08", "status": "ready", "pdfAvailable": True},
                {"month": "2026-09", "status": "ready", "pdfAvailable": True},
            ],
        )
        self.assertTrue(result["opened"])
        self.assertEqual(result["calls"], [
            ["archive"], ["sheet", "repsheet"], ["detail", "2026-09"],
            ["sheet", "repdetailsheet"], ["opened", "2026-09"],
        ])

    def test_report_event_cta_uses_matching_event_month(self):
        result = self.run_report_navigation(
            {"type": "report_ready", "id": "coach:report_ready:event-september"},
            [
                {"month": "2026-08", "status": "ready", "pdfAvailable": True},
                {"month": "2026-09", "status": "ready", "pdfAvailable": True},
            ],
            events=[
                {"event_id": "other", "event_type": "report_ready", "source_id": "2026-08"},
                {"event_id": "coach:report_ready:event-september", "event_type": "report_ready", "source_id": "2026-09"},
            ],
        )
        self.assertTrue(result["opened"])
        self.assertIn(["detail", "2026-09"], result["calls"])
        self.assertEqual([call for call in result["calls"] if call[0] == "opened"], [["opened", "2026-09"]])

    def test_missing_report_never_opens_another_month_or_marks_it_opened(self):
        result = self.run_report_navigation(
            {"type": "report", "id": "report:2026-09"},
            [{"month": "2026-08", "status": "ready", "pdfAvailable": True}],
        )
        self.assertFalse(result["opened"])
        self.assertEqual(result["calls"], [["message", "Dein Monatsreport ist noch nicht verfügbar."]])

    def test_unfinished_or_missing_report_artifacts_explain_unavailability(self):
        for report in (
            {"month": "2026-09", "status": "running"},
            {"month": "2026-09", "status": "ready", "pdfAvailable": False, "webUrl": ""},
        ):
            with self.subTest(report=report):
                result = self.run_report_navigation({"type": "report", "id": "report:2026-09"}, [report])
                self.assertFalse(result["opened"])
                self.assertEqual(result["calls"], [["message", "Dein Monatsreport ist noch nicht verfügbar."]])

    def test_unknown_report_month_is_not_guessed_from_available_reports(self):
        for candidate in (
            {"type": "report", "id": "report:2026-13"},
            {"type": "report", "id": "report:latest"},
            {"type": "report_ready", "id": "missing-event"},
        ):
            with self.subTest(candidate=candidate):
                result = self.run_report_navigation(candidate, [{"month": "2026-09", "status": "ready", "pdfAvailable": True}])
                self.assertFalse(result["opened"])
                self.assertEqual(result["calls"], [["message", "Dein Monatsreport ist noch nicht verfügbar."]])

    def test_monthly_check_keeps_its_existing_confirmation_flow(self):
        result = self.run_report_navigation(
            {"type": "monthly_action", "id": "monthly-action:income"}, [], route="monthly-checkin",
        )
        self.assertTrue(result["opened"])
        self.assertEqual(result["calls"], [["monthly-plan"]])

    def test_generic_reports_route_keeps_the_archive(self):
        result = self.run_report_navigation(None, [])
        self.assertTrue(result["opened"])
        self.assertEqual(result["calls"], [["archive"], ["sheet", "repsheet"]])

    def test_rendered_report_action_text_uses_existing_candidate_click_target(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for navigation interaction tests")
        renderer = self.frontend[
            self.frontend.index("function setMentorCtaTarget("):
            self.frontend.index("let reportOpenInFlight=")
        ]
        script = """
const APP_MODE='bridge';
const DATA={mentorCandidate:{type:'report',id:'report:2026-09',deep_link:'reports',
  title:'Dein Monatsreport ist bereit',message:'September 2026',action_label:'Report öffnen'}};
const body={innerHTML:''};
const mentor={dataset:{},classList:{remove(){},toggle(){}},querySelector:()=>({setAttribute(){}})};
const document={querySelector:()=>mentor,getElementById:()=>body};
const announcementEscape=value=>String(value);
""" + renderer + "\nmentorLine();process.stdout.write(body.innerHTML);"
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        markup = ET.fromstring("<div>" + result.stdout + "</div>")
        action = markup.find(".//span[@class='m-nudge']")
        self.assertIsNotNone(action)
        self.assertEqual("".join(action.itertext()), "Report öffnen")
        self.assertEqual(action.get("data-mentoract"), "candidate")


if __name__ == "__main__":
    unittest.main()
