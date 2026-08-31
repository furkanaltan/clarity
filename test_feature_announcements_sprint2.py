import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND_PATH = Path(os.environ.get(
    "ROVE_FRONTEND_PATH",
    Path(__file__).resolve().parent / "frontend" / "index.html",
))


class FeatureAnnouncementSprint2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND_PATH.read_text(encoding="utf-8")

    def function_source(self, name):
        marker = f"function {name}("
        start = self.frontend.find(marker)
        self.assertGreaterEqual(start, 0, name)
        brace = self.frontend.find("{", start)
        depth = 0
        quote = None
        escaped = False
        for index in range(brace, len(self.frontend)):
            char = self.frontend[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in {'"', "'", "`"}:
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return self.frontend[start:index + 1]
        self.fail(f"Unclosed function {name}")

    def test_fixture_counts_choose_individual_or_bundle(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        functions = "\n".join(self.function_source(name) for name in (
            "normalizeFeatureAnnouncements",
            "announcementRelevantItems",
            "announcementUnseenItems",
            "announcementFeedModel",
        ))
        script = f"""
const DATA={{featureAnnouncements:{{eligible:[],archive:[]}}}};
{functions}
const output=[];
for(const count of [0,1,2,3,4]){{
  DATA.featureAnnouncements=normalizeFeatureAnnouncements({{
    eligible:Array.from({{length:count}},(_,index)=>({{
      feature_id:`feature_${{index}}`,title:`Feature ${{index}}`,state:{{}}
    }}))
  }});
  const model=announcementFeedModel();
  output.push([count,model.kind,model.items.length]);
}}
process.stdout.write(JSON.stringify(output));
"""
        result = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        self.assertEqual(
            json.loads(result.stdout),
            [[0, "individual", 0], [1, "individual", 1], [2, "individual", 2],
             [3, "bundle", 3], [4, "bundle", 4]],
        )

    def test_seen_items_remain_prominent_but_opened_item_is_removed(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        functions = "\n".join(self.function_source(name) for name in (
            "normalizeFeatureAnnouncements",
            "announcementRelevantItems",
            "announcementUnseenItems",
            "announcementUnresolvedItems",
            "announcementFeedModel",
            "updateAnnouncementLocalState",
        ))
        script = f"""
const DATA={{featureAnnouncements:normalizeFeatureAnnouncements({{
  eligible:[
    {{feature_id:"crypto_tracking_v1",title:"Neu: Crypto Tracking",deep_link:"asset-krypto",state:{{}}}},
    {{feature_id:"monthly_checkin_v1",title:"Neu: Monatscheck",deep_link:"monthly-checkin",state:{{}}}}
  ], archive:[]
}})}};
{functions}
for(const item of DATA.featureAnnouncements.eligible) item.state.seen=true;
updateAnnouncementLocalState("crypto_tracking_v1","opened");
process.stdout.write(JSON.stringify({{
  feed:announcementFeedModel().items.map(item=>[item.feature_id,item.title,item.deep_link]),
  unresolved:announcementUnresolvedItems().map(item=>item.feature_id),
  crypto:DATA.featureAnnouncements.eligible[0].state,
  monthly:DATA.featureAnnouncements.eligible[1].state
}}));
"""
        result = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["feed"], [
            ["monthly_checkin_v1", "Neu: Monatscheck", "monthly-checkin"],
        ])
        self.assertEqual(payload["unresolved"], ["monthly_checkin_v1"])
        self.assertTrue(payload["crypto"]["opened"])
        self.assertFalse(payload["monthly"]["opened"])
        self.assertFalse(payload["monthly"]["completed"])

    def test_bell_dot_combines_activity_and_server_announcements(self):
        body = self.function_source("updateBellDot")
        self.assertIn("latestActivityTs() > readActivitySeenTs()", body)
        self.assertIn("announcementUnresolvedItems().length>0", body)
        self.assertIn('classList.toggle("has-unread", hasUnread)', body)

    def test_seen_items_stay_visible_and_opened_dismissed_or_completed_are_removed(self):
        body = self.function_source("announcementUnseenItems")
        self.assertIn("!item.state?.seen", body)
        relevant = self.function_source("announcementRelevantItems")
        self.assertNotIn("!item.state?.seen", relevant)
        self.assertIn("!item.state?.opened", relevant)
        self.assertIn("!item.state?.dismissed", relevant)
        self.assertIn("!item.state?.completed", relevant)
        local_state = self.function_source("updateAnnouncementLocalState")
        self.assertIn('action==="opened"?["seen","opened"]', local_state)
        self.assertIn('action==="completed"?["seen","opened","completed"]', local_state)

    def test_seen_is_sent_only_after_bell_content_is_rendered(self):
        open_activity = self.function_source("openActivity")
        self.assertLess(open_activity.index("renderActivity()"), open_activity.index("markRenderedAnnouncementsSeen"))
        render_activity = self.function_source("renderActivity")
        self.assertNotIn("markRenderedAnnouncementsSeen", render_activity)
        load_state = self.function_source("loadBridgeState")
        self.assertIn("setFeatureAnnouncements(b.feature_announcements)", load_state)
        self.assertNotIn('sendAnnouncementAction(', load_state)

    def test_existing_activity_and_upcoming_feed_are_retained(self):
        body = self.function_source("renderActivity")
        self.assertIn("announcements.html + upcomingHtml + groupsHtml", body)
        self.assertIn("upcomingGroups()", body)
        self.assertIn("DATA.activity", body)

    def test_archive_is_latest_first_limited_and_ninety_days(self):
        body = self.function_source("announcementArchiveItems")
        self.assertIn("90*24*60*60*1000", body)
        self.assertIn(".sort((a,b)=>", body)
        self.assertIn(".slice(0,10)", body)

    def test_settings_and_bell_bundle_open_whats_new(self):
        self.assertIn('action("whats-new","Was ist neu?"', self.frontend)
        self.assertIn('else if(k==="whats-new"){ openWhatsNew(); }', self.frontend)
        self.assertIn("data-announcement-bundle", self.frontend)
        self.assertIn('openOnly("whatsnewsheet")', self.frontend)

    def test_static_deep_link_allowlist_is_complete(self):
        body = self.function_source("openFeatureDeepLink")
        for route in (
            "talk", "score", "reports", "settings", "asset-krypto", "bell",
            "analysis", "analysis-merchants", "monthly-checkin", "goals", "contracts",
        ):
            with self.subTest(route=route):
                self.assertIn(route, body)
        self.assertIn("if(!action) return false", body)

    def test_crypto_and_monthly_tutorials_are_bound_to_their_feature_ids(self):
        tutorial = self.function_source("announcementTutorialSteps")
        self.assertIn('feature==="crypto_tracking_v1"', tutorial)
        self.assertIn('feature==="monthly_checkin_v1"', tutorial)
        self.assertNotIn('item?.tutorial_type==="steps"||feature.includes("crypto")', tutorial)

    def test_analysis_merchants_uses_existing_analysis_view(self):
        body = self.function_source("openAnalysisMerchants")
        self.assertIn("openAnalysis()", body)
        self.assertIn('analysisView="merchants"', body)
        self.assertIn("renderAnalysis()", body)

    def test_crypto_deep_link_opens_management_or_add_flow(self):
        body = self.function_source("openFeatureDeepLink")
        self.assertIn('if(index>=0) openAssetDetail(index)', body)
        self.assertIn('else openAssetKind("crypto")', body)
        self.assertNotIn("if(index<0) return false", body)

    def test_monthly_plan_has_a_clear_nothing_due_state(self):
        body = self.function_source("renderMonthlyPlan")
        self.assertIn("Aktuell ist nichts fällig.", body)
        self.assertIn("dueActions.length", body)

    def test_opened_seen_and_dismissed_use_existing_server_endpoint(self):
        sender = self.function_source("sendAnnouncementAction")
        self.assertIn("/v1/feature-announcements/", sender)
        self.assertIn('method:"POST"', sender)
        opener = self.function_source("openFeatureAnnouncement")
        self.assertIn('sendAnnouncementAction(featureId,"opened")', opener)
        self.assertIn('sendAnnouncementAction(body.dataset.featureId,"dismissed")', self.frontend)

    def test_announcement_navigation_closes_only_announcement_overlays_first(self):
        closer = self.function_source("closeAnnouncementOverlays")
        for sheet_id in ("actsheet", "whatsnewsheet", "announcementdetailsheet"):
            self.assertIn(sheet_id, closer)
        self.assertIn('sheetBg.classList.remove("on")', closer)
        opener = self.function_source("openFeatureAnnouncement")
        self.assertIn('openAnnouncementTarget(item.deep_link)', opener)
        self.assertNotIn("hasTutorial", opener)
        self.assertIn('openAnnouncementTarget(body.dataset.deepLink)', self.frontend)

    def test_targeted_open_precedes_overlay_close_without_cross_feature_state(self):
        opener = self.function_source("openFeatureAnnouncement")
        self.assertLess(
            opener.index('sendAnnouncementAction(featureId,"opened")'),
            opener.index("openAnnouncementTarget(item.deep_link)"),
        )
        self.assertNotIn("markRenderedAnnouncementsSeen", opener)
        self.assertNotIn("dismissed", opener)
        self.assertNotIn("completed", opener)

    def test_tutorials_are_small_and_no_overlay_tour_is_added(self):
        tutorial = self.function_source("announcementTutorialSteps")
        self.assertIn("quick_examples", tutorial)
        self.assertIn('feature==="crypto_tracking_v1"', tutorial)
        self.assertIn('feature==="monthly_checkin_v1"', tutorial)
        self.assertIn("Was ist ein ETF?", tutorial)
        self.assertIn("Hinterlege Menge und Einstandswert", tutorial)
        self.assertNotIn("joyride", self.frontend.lower())
        self.assertNotIn("spotlight-tour", self.frontend.lower())

    def test_new_sheets_have_visible_close_controls_and_escape(self):
        self.assertIn('data-close-whats-new aria-label="Was ist neu schließen"', self.frontend)
        self.assertIn('data-close-announcement-detail aria-label="Neuigkeit schließen"', self.frontend)
        self.assertIn('document.querySelector("[data-close-whats-new]").addEventListener("click",closeSheet)', self.frontend)
        self.assertIn('document.getElementById("whatsnewsheet")?.classList.contains("on")', self.frontend)

    def test_state_hydrates_on_start_and_refresh_without_financial_writes(self):
        refresh = self.function_source("refreshAppDataFromServer")
        load = self.function_source("loadBridgeState")
        self.assertIn("setFeatureAnnouncements(data.feature_announcements)", refresh)
        self.assertIn("setFeatureAnnouncements(b.feature_announcements)", load)
        feature_block = self.frontend[
            self.frontend.index("function announcementEscape"):
            self.frontend.index("// ===================== ROV.E SPRICHT")
        ]
        for forbidden in ("/v1/investments", "/v1/expenses", "/v1/contracts", "/v1/goals"):
            self.assertNotIn(forbidden, feature_block)


if __name__ == "__main__":
    unittest.main()
