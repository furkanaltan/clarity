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

    def rendered_candidate(self, route="score", label="Score ansehen"):
        renderer = self.frontend[self.frontend.index("function setMentorCtaTarget("):
                                 self.frontend.index("let reportOpenInFlight=")]
        candidate = {"id": "motivation", "type": "motivation", "priority": 10,
                     "deep_link": route, "title": "Stabile Basis",
                     "message": "Dein Score liegt bei 81/100.", "action_label": label}
        setup = """
const APP_MODE='bridge', body={innerHTML:''};
const arrow={dataset:{mentorTarget:'coach'},setAttribute(name,value){this.dataset.mentorTarget=value;}};
const mentor={dataset:{},classList:{remove(){},toggle(){}},querySelector:()=>arrow};
const document={querySelector:()=>mentor,getElementById:()=>body};
const announcementEscape=value=>String(value);
""" + "\nconst DATA={mentorCandidate:" + json.dumps(candidate) + "};\n"
        result = subprocess.run(["node", "-e", setup + renderer + """
mentorLine();
process.stdout.write(JSON.stringify({html:body.innerHTML,arrowTarget:arrow.dataset.mentorTarget}));
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def click_action(self, *, action=None, arrow=False, target="score", route="score"):
        start = self.frontend.index('document.querySelector(".mentor").addEventListener("click",e=>{')
        handler = self.frontend[start:self.frontend.index("\n// Chart-Zustand", start)]
        routes = self.frontend[self.frontend.index("function openFeatureDeepLink("):
                               self.frontend.index("async function openFeatureAnnouncement(")]
        script = """
const calls=[], listeners=[];
const mentorCard={dataset:{mentorDeepLink:ROUTE},addEventListener:(_type,handler)=>listeners.push(handler)};
const DATA={mentorCandidate:{id:'motivation',type:'motivation'}};
const document={querySelector:()=>mentorCard};
function buzz(){}
function openTalk(){calls.push('talk');}
function openMentorCoach(){calls.push('coach');}
function openScore(){calls.push('score');}
function openReports(){calls.push('reports');}
function openMonthlyPlan(){calls.push('monthly');}
function openWelcomeBudget(){calls.push('budget-setup');}
function openSheet(){calls.push('quick-add');}
function openMentorCandidate(candidate,route){return openFeatureDeepLink(route);}
""".replace("ROUTE", json.dumps(route)) + routes + handler + """
mentorCard.addEventListener('click',()=>openMentorCoach());
let stopped=false;
const event={stopImmediatePropagation(){stopped=true;},target:{closest(selector){
  if(ARROW && selector==='[data-mentor-cta]')return {dataset:{mentorTarget:TARGET}};
  if(ACTION && selector==='[data-mentoract]')return {dataset:{mentoract:ACTION}};
  return null;
}}};
for(const listener of listeners){if(!stopped)listener(event);}
process.stdout.write(JSON.stringify(calls));
""".replace("ARROW", json.dumps(arrow)).replace("ACTION", json.dumps(action)).replace("TARGET", json.dumps(target))
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_score_action_is_its_own_button_and_opens_only_score(self):
        rendered = self.rendered_candidate()
        markup = ET.fromstring("<div>" + rendered["html"] + "</div>")
        button = markup.find(".//button[@data-mentoract='candidate']")
        self.assertIsNotNone(button)
        self.assertEqual("".join(button.itertext()), "Score ansehen")
        self.assertEqual(button.get("type"), "button")
        self.assertEqual(self.click_action(action=button.get("data-mentoract")), ["score"])

    def test_score_candidate_does_not_retarget_the_chat_arrow(self):
        self.assertEqual(self.rendered_candidate()["arrowTarget"], "coach")

    def test_arrow_always_opens_coach_once_despite_module_targets(self):
        for target, route in (("score", "score"), ("reports", "reports"),
                              ("budget-setup", ""), ("coach", "")):
            with self.subTest(target=target):
                self.assertEqual(self.click_action(arrow=True, target=target, route=route), ["coach"])

    def test_explicit_module_actions_keep_their_routes_without_opening_chat(self):
        for route, expected in (("score", "score"), ("reports", "reports"),
                                ("monthly-checkin", "monthly")):
            with self.subTest(route=route):
                self.assertEqual(self.click_action(action="candidate", route=route), [expected])
        self.assertEqual(self.click_action(action="budget-setup"), ["budget-setup"])
        self.assertEqual(self.click_action(action="add"), ["quick-add"])

    def test_background_and_unknown_action_route_remain_safe(self):
        self.assertEqual(self.click_action(), ["coach"])
        self.assertEqual(self.click_action(action="candidate", route="unknown-route"), [])

    def test_score_message_does_not_end_with_a_separator(self):
        markup = ET.fromstring("<div>" + self.rendered_candidate()["html"] + "</div>")
        self.assertNotIn("\u00b7", markup.find(".//div[@class='m-note']").text)

    def test_rovi_icon_is_an_explicit_keyboard_accessible_chat_button(self):
        markup = self.frontend[self.frontend.index('<div class="mentor">'):]
        tag = re.search(r'<button\b[^>]*class="face"[^>]*>', markup)
        self.assertIsNotNone(tag)
        self.assertIn('type="button"', tag.group())
        self.assertIn('data-mentor-cta', tag.group())
        self.assertIn('aria-label="Ask Rovi öffnen"', tag.group())

    def test_v4_arrow_opens_the_same_insight_context_as_the_card(self):
        start = self.frontend.index('document.querySelector(".mentor").addEventListener("click",e=>{')
        handler = self.frontend[start:self.frontend.index("\n});", start) + 4]
        helper = ""
        if "async function openMentorCoach(){" in self.frontend:
            helper_start = self.frontend.index("async function openMentorCoach(){")
            helper = self.frontend[helper_start:self.frontend.index("\n}", helper_start) + 2]
        script = f"""
const fingerprint="a".repeat(64);
const DATA={{coachV4Insight:{{insight_id:"v4:synthetic",evidence_fingerprint:fingerprint,title:"Ausgaben"}}}}, calls=[];
let listener;
const card={{dataset:{{coachV4InsightId:"v4:synthetic",coachV4InsightFingerprint:fingerprint}},
  addEventListener:(_name,fn)=>listener=fn}};
const document={{querySelector:()=>card}};
function openTalk(options){{calls.push(["talk",options?.insight?.insight_id||null]);}}
function acknowledgeMentorV4Insight(insight){{calls.push(["acknowledged",insight.insight_id]);return Promise.resolve(true);}}
function markMentorV4InsightOpened(insight){{calls.push(["opened",insight.insight_id,insight.evidence_fingerprint]);return Promise.resolve().then(()=>{{calls.push(["saved"]);return true;}});}}
function mentorLine(){{}}
function buzz(){{}}
{helper}
{handler}
(async()=>{{
  listener({{target:{{closest:()=>({{dataset:{{mentorTarget:"coach"}}}})}},stopImmediatePropagation(){{}}}});
  await new Promise(resolve=>setImmediate(resolve));
  process.stdout.write(JSON.stringify(calls));
}})();
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            ["acknowledged", "v4:synthetic"],
            ["opened", "v4:synthetic", "a" * 64], ["saved"], ["talk", "v4:synthetic"]
        ])

    def test_card_surface_opens_coach_and_preserves_v4_insight_context(self):
        start = self.frontend.index("// A due check-in owns the complete card;")
        end = self.frontend.index("\n\n// ===================== EMPTY STATES:", start)
        handler = self.frontend[start:end]
        helper_start = self.frontend.index("async function openMentorCoach(){")
        helper = self.frontend[helper_start:self.frontend.index("\n}", helper_start) + 2]
        insight = {"insight_id": "v4:category-trend", "evidence_fingerprint": "b" * 64, "title": "Ausgaben im Blick"}
        script = f"""
const DATA={{coachV4Insight:{json.dumps(insight)}}}, calls=[], listeners={{}};
const card={{dataset:{{coachV4InsightId:"v4:category-trend",coachV4InsightFingerprint:"{'b' * 64}"}},style:{{}},
  addEventListener:(name,fn)=>listeners[name]=fn}};
const document={{querySelector:selector=>selector==="#tab-home .mentor"?card:null}};
function markMentorV4InsightOpened(value){{calls.push(["opened",value.insight_id,value.evidence_fingerprint]);return Promise.resolve(true);}}
function acknowledgeMentorV4Insight(value){{calls.push(["acknowledged",value.insight_id]);return Promise.resolve(true);}}
function mentorLine(){{calls.push(["render"]);}}
function openTalk(options){{calls.push(["talk",options?.insight?.insight_id||null]);}}
{helper}
{handler}
(async()=>{{
  listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  card.dataset.coachV4InsightId="v4:another-insight";
  listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  process.stdout.write(JSON.stringify(calls));
}})();
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            ["acknowledged", "v4:category-trend"], ["opened", "v4:category-trend", "b" * 64], ["render"],
            ["talk", "v4:category-trend"], ["talk", None],
        ])

    def test_v4_deduplication_is_server_owned_not_daily_local_storage(self):
        self.assertIn('"/v1/coach/insights/claim"', self.frontend)
        self.assertIn('"/v1/coach/insights/acknowledged"', self.frontend)
        self.assertIn('"/v1/coach/insights/opened"', self.frontend)
        self.assertNotIn("roveCoachV4Seen", self.frontend)
        self.assertIn('data.status==="suppressed"||data.status==="invalid"', self.frontend)
        self.assertIn('coachV4ClaimStatus="failed";\n        scheduleCoachV4ClaimRetry(key,5000);', self.frontend)

    def test_display_acknowledgement_follows_two_render_frames_and_checks_visibility(self):
        helper_start = self.frontend.index("function acknowledgeMentorV4AfterRender(insight){")
        helper_end = self.frontend.index("\nasync function acknowledgeMentorV4Insight", helper_start)
        helper = self.frontend[helper_start:helper_end]
        self.assertIn("requestAnimationFrame(()=>requestAnimationFrame", helper)
        self.assertIn('document.visibilityState==="hidden"', helper)
        self.assertIn("coachV4InsightFingerprint!==insight.evidence_fingerprint", helper)
        self.assertIn('coachV4ClaimStatus!=="reserved"', helper)

    def test_client_acknowledges_only_after_the_mentor_dom_renders(self):
        helper_start = self.frontend.index('let coachV4ClaimKey=null, coachV4ClaimStatus="idle"')
        helper_end = self.frontend.index("\nasync function markMentorV4InsightOpened", helper_start)
        helper = self.frontend[helper_start:helper_end]
        insight = {
            "insight_id": "v4:rendered", "evidence_fingerprint": "c" * 64,
            "title": "Ausgaben im Blick", "insight_type": "category_trend",
        }
        script = f"""
const DATA={{coachV4Insight:{json.dumps(insight)}}}, calls=[], frames=[];
let rendered=false;
const mentor={{dataset:{{}},getBoundingClientRect:()=>({{width:320,height:80}})}};
const document={{visibilityState:"visible",querySelector:()=>mentor}};
const window={{crypto:null}};
function requestAnimationFrame(callback){{frames.push(callback);}}
function apiReady(){{return true;}}
function apiFetch(path,options){{
  const payload=JSON.parse(options.body);calls.push([path,payload.reservation_token,rendered]);
  if(path.endsWith("/claim")) return Promise.resolve({{ok:true,status:200,json:async()=>({{ok:true,status:"reserved"}})}});
  if(!rendered) throw new Error("ack before render");
  return Promise.resolve({{ok:true,status:200,json:async()=>({{ok:true,acknowledged:true}})}});
}}
function mentorLine(){{
  if(claimMentorV4Insight(DATA.coachV4Insight)){{
    mentor.dataset.coachV4InsightId=DATA.coachV4Insight.insight_id;
    mentor.dataset.coachV4InsightFingerprint=DATA.coachV4Insight.evidence_fingerprint;
    rendered=true;
    acknowledgeMentorV4AfterRender(DATA.coachV4Insight);
  }}
}}
{helper}
claimMentorV4Insight(DATA.coachV4Insight);
(async()=>{{
  await new Promise(resolve=>setImmediate(resolve));
  while(frames.length){{frames.shift()();await new Promise(resolve=>setImmediate(resolve));}}
  await new Promise(resolve=>setImmediate(resolve));
  process.stdout.write(JSON.stringify({{calls,status:coachV4ClaimStatus,rendered}}));
}})();
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([call[0] for call in payload["calls"]], [
            "/v1/coach/insights/claim", "/v1/coach/insights/acknowledged"
        ])
        self.assertFalse(payload["calls"][0][2])
        self.assertTrue(payload["calls"][1][2])
        self.assertEqual(payload["calls"][0][1], payload["calls"][1][1])
        self.assertEqual(payload["status"], "shown")

    def test_old_acknowledgement_cannot_replace_new_insight_inflight_request(self):
        helper_start = self.frontend.index('let coachV4ClaimKey=null, coachV4ClaimStatus="idle"')
        helper_end = self.frontend.index("\nasync function markMentorV4InsightOpened", helper_start)
        helper = self.frontend[helper_start:helper_end]
        script = f"""
const calls=[], pending={{}};
const insightA={{insight_id:"v4:old",evidence_fingerprint:"a".repeat(64)}};
const insightB={{insight_id:"v4:new",evidence_fingerprint:"b".repeat(64)}};
const DATA={{coachV4Insight:insightB}};
const window={{crypto:null}};
function apiReady(){{return true;}}
function apiFetch(_path,options){{
  const id=JSON.parse(options.body).insight_id;calls.push(id);
  return new Promise(resolve=>pending[id]=resolve);
}}
{helper}
(async()=>{{
  const oldKey=`${{insightA.insight_id}}:${{insightA.evidence_fingerprint}}`;
  coachV4ClaimKey=oldKey;coachV4ClaimStatus="reserved";coachV4ReservationToken="1".repeat(32);
  const oldAck=acknowledgeMentorV4Insight(insightA);
  coachV4ClaimKey=`${{insightB.insight_id}}:${{insightB.evidence_fingerprint}}`;
  coachV4ClaimStatus="reserved";coachV4ReservationToken="2".repeat(32);
  coachV4AckPromise=null;coachV4AckKey=null;
  const newAck=acknowledgeMentorV4Insight(insightB);
  const duplicateAck=acknowledgeMentorV4Insight(insightB);
  const duplicateCalls=calls.length;
  pending[insightA.insight_id]({{ok:true,status:200,json:async()=>({{ok:true,acknowledged:true}})}});
  await oldAck;
  const newRequestStillTracked=coachV4AckKey===`${{insightB.insight_id}}:${{insightB.evidence_fingerprint}}`;
  pending[insightB.insight_id]({{ok:true,status:200,json:async()=>({{ok:true,acknowledged:true}})}});
  await Promise.all([newAck,duplicateAck]);
  process.stdout.write(JSON.stringify({{calls,duplicateCalls,newRequestStillTracked,status:coachV4ClaimStatus}}));
}})();
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["calls"], ["v4:old", "v4:new"])
        self.assertEqual(payload["duplicateCalls"], 2)
        self.assertTrue(payload["newRequestStillTracked"])
        self.assertEqual(payload["status"], "shown")

    def test_real_mentor_render_orders_concrete_v3_v4_and_score_fallback(self):
        start = self.frontend.index("function mentorLine(){")
        mentor = self.frontend[start:self.frontend.index("\nlet coachV4ClaimKey", start)]
        script = f"""
const calls=[], card={{dataset:{{}},classList:{{remove(){{}},toggle(){{}}}},querySelector:()=>null}};
const output={{innerHTML:""}};
const document={{getElementById:()=>output,querySelector:()=>card}};
const APP_MODE="bridge";
const DATA={{monthlyCheckinActions:[],goals:[{{t:"Reserve"}}],assets:[{{value:1}}],budgets:[{{cat:"Essen",limit:100}}],monthlyPlan:{{incomeStatus:"confirmed"}}}};
function setMentorCtaTarget(value){{card.dataset.mentorTarget=value;}}
function shouldWelcomeFirstBudget(){{return false;}}
function announcementEscape(value){{return value;}}
function mentorDataReady(){{return true;}}
function trackedOuts(){{return [{{}}];}}
function freeBudget(){{return 100;}}
function catSpent(){{return 10;}}
function mentorBudgetAlert(){{return {{active:false}};}}
function daysUntilMonthEnd(){{return 10;}}
function mentorIncomeReminderDue(){{return false;}}
let available=true;
function claimMentorV4Insight(value){{calls.push(["claim",value.insight_id]);return available;}}
function acknowledgeMentorV4AfterRender(value){{calls.push(["ack",value.insight_id]);}}
{mentor}
const score={{id:"motivation",type:"motivation",priority:10,title:"Score fallback",message:"Stable",deep_link:"score"}};
const v4={{insight_id:"v4:synthetic",evidence_fingerprint:"a".repeat(64),title:"Evidence insight",message:"Validated"}};
function render(candidate,insight,canClaim=true){{
  DATA.mentorCandidate=candidate;DATA.coachV4Insight=insight;available=canClaim;calls.length=0;
  mentorLine();
  return {{html:output.innerHTML,calls:[...calls],id:card.dataset.coachV4InsightId||null,target:card.dataset.mentorTarget}};
}}
const results={{
  scoreWithV4:render(score,v4),
  concreteWithV4:render({{id:"budget-overrun",type:"budget_overrun",priority:100,title:"Urgent V3",message:"Act",deep_link:"analysis"}},v4),
  missingV4:render(score,null),
  suppressedV4:render(score,v4,false),
  changedMotivation:render({{...score,priority:50,title:"Concrete motivation"}},v4),
}};
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        self.assertIn("Evidence insight", cases["scoreWithV4"]["html"])
        self.assertNotIn("Score fallback", cases["scoreWithV4"]["html"])
        self.assertEqual(cases["scoreWithV4"]["calls"], [["claim", "v4:synthetic"], ["ack", "v4:synthetic"]])
        self.assertEqual(cases["scoreWithV4"]["id"], "v4:synthetic")
        for key, title, target in (("concreteWithV4", "Urgent V3", "analysis"),
                                   ("changedMotivation", "Concrete motivation", "score")):
            self.assertIn(title, cases[key]["html"])
            self.assertEqual(cases[key]["calls"], [])
            self.assertIsNone(cases[key]["id"])
            self.assertEqual(cases[key]["target"], target)
        for key in ("missingV4", "suppressedV4"):
            self.assertIn("Score fallback", cases[key]["html"])
            self.assertIsNone(cases[key]["id"])
            self.assertEqual(cases[key]["target"], "score")
            self.assertFalse(any(call[0] == "ack" for call in cases[key]["calls"]))

    def test_income_reminder_keeps_v3_priority_before_server_claim(self):
        mentor_start = self.frontend.index("function mentorLine(){")
        mentor_end = self.frontend.index("\nlet coachV4ClaimKey", mentor_start)
        mentor = self.frontend[mentor_start:mentor_end]
        reminder = mentor.index('if(APP_MODE==="bridge" && coachV4Insight?.insight_id && mentorIncomeReminderDue())')
        claim = mentor.index("claimMentorV4Insight(coachV4Insight)")
        self.assertLess(reminder, claim)

    def test_arrow_is_explicit_coach_cta_and_text_actions_keep_module_routes(self):
        self.assertIn(
            'data-mentor-cta data-mentor-target="coach"',
            self.frontend,
        )
        self.assertEqual(self.click_action(arrow=True, target="score"), ["coach"])
        self.assertEqual(self.click_action(action="score"), ["score"])
        self.assertEqual(self.click_action(action="report"), ["reports"])
        self.assertIn('else openTalk();', self.frontend)

    def test_score_and_report_candidates_define_targets(self):
        self.assertRegex(
            self.frontend,
            re.compile(r"Dein Rov\.E Score:.*?target:\"score\"", re.S),
        )
        self.assertIn('setMentorCtaTarget("report");', self.frontend)

    def test_server_candidate_deep_link_is_not_card_navigation(self):
        self.assertIn('setMentorCtaTarget(deepLink);', self.frontend)
        self.assertEqual(self.click_action(arrow=True, target="reports", route="reports"), ["coach"])
        self.assertEqual(self.click_action(action="candidate", route="reports"), ["reports"])
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
        self.assertEqual(result["calls"], [
            ["archive"], ["sheet", "repsheet"],
            ["message", "Dein Monatsreport ist noch nicht verfügbar."],
        ])

    def test_running_report_opens_matching_preview_without_marking_it_opened(self):
        for candidate in (
            {"type": "report", "id": "report:2026-09"},
            {"type": "report_ready", "id": "ready-september"},
        ):
            with self.subTest(candidate=candidate):
                result = self.run_report_navigation(
                    candidate,
                    [
                        {"month": "2026-08", "status": "ready", "pdfAvailable": True},
                        {"month": "2026-09", "status": "running"},
                    ],
                    events=[{"event_id": "ready-september", "event_type": "report_ready", "source_id": "2026-09"}],
                )
                self.assertTrue(result["opened"])
                self.assertEqual(result["calls"], [
                    ["archive"], ["sheet", "repsheet"], ["detail", "2026-09"],
                    ["sheet", "repdetailsheet"],
                ])

    def test_ready_web_report_opens_matching_month_without_pdf(self):
        result = self.run_report_navigation(
            {"type": "report", "id": "report:2026-09"},
            [{"month": "2026-09", "status": "ready", "pdfAvailable": False, "webUrl": "/synthetic-web-report"}],
        )
        self.assertTrue(result["opened"])
        self.assertEqual(result["calls"], [
            ["archive"], ["sheet", "repsheet"], ["detail", "2026-09"],
            ["sheet", "repdetailsheet"], ["opened", "2026-09"],
        ])

    def test_existing_running_preview_displays_current_values_without_file_actions(self):
        start = self.frontend.index("function renderReportDetail(")
        renderer = self.frontend[start:self.frontend.index("function formatReportExpiry(", start)]
        script = """
const DATA={reports:[{month:'2026-09',m:'September 2026',status:'running'}]};
const body={innerHTML:''};
const document={getElementById:()=>body};
function reportStats(){return {end:12345,expenses:67,spar:25};}
function topCategory(){return null;}
function eur(value){return `${value} EUR`;}
function moduleIcon(){return '';}
""" + renderer + "\nrenderReportDetail(0);process.stdout.write(body.innerHTML);"
        result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
        self.assertIn("Zwischenstand", result.stdout)
        self.assertIn("Vermögen aktuell", result.stdout)
        self.assertIn("12345 EUR", result.stdout)
        self.assertIn("Ausgaben bisher", result.stdout)
        self.assertIn("67 EUR", result.stdout)
        self.assertIn("Sparquote", result.stdout)
        self.assertIn("25 %", result.stdout)
        self.assertNotIn("data-report-web", result.stdout)
        self.assertNotIn("data-report-pdf", result.stdout)

    def test_unavailable_status_or_missing_artifacts_explain_unavailability(self):
        for report in (
            {"month": "2026-09", "status": "failed"},
            {"month": "2026-09", "status": "ready", "pdfAvailable": False, "webUrl": ""},
        ):
            with self.subTest(report=report):
                result = self.run_report_navigation({"type": "report", "id": "report:2026-09"}, [report])
                self.assertFalse(result["opened"])
                self.assertEqual(result["calls"], [
                    ["archive"], ["sheet", "repsheet"],
                    ["message", "Dein Monatsreport ist noch nicht verfügbar."],
                ])

    def test_unknown_report_month_is_not_guessed_from_available_reports(self):
        for candidate in (
            {"type": "report", "id": "report:2026-13"},
            {"type": "report", "id": "report:latest"},
            {"type": "report_ready", "id": "missing-event"},
        ):
            with self.subTest(candidate=candidate):
                result = self.run_report_navigation(candidate, [{"month": "2026-09", "status": "ready", "pdfAvailable": True}])
                self.assertFalse(result["opened"])
                self.assertEqual(result["calls"], [
                    ["archive"], ["sheet", "repsheet"],
                    ["message", "Dein Monatsreport ist noch nicht verfügbar."],
                ])

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
        action = markup.find(".//button[@data-mentoract='candidate']")
        self.assertIsNotNone(action)
        self.assertEqual(action.get("type"), "button")
        self.assertEqual("".join(action.itertext()), "Report öffnen")
        self.assertEqual(action.get("data-mentoract"), "candidate")


if __name__ == "__main__":
    unittest.main()
