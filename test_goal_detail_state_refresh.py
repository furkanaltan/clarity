from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


class GoalDetailStateRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND.read_text(encoding="utf-8")

    def js_section(self, start: str, end: str) -> str:
        start_at = self.frontend.index(start)
        end_at = self.frontend.index(end, start_at)
        return self.frontend[start_at:end_at]

    def run_node(
        self, body: str, *, initial, server, deferred=False, api_ok=True,
        api_error="forced_failure", error_details=None,
    ):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the goal state regression tests")

        helpers = self.js_section(
            "function mergeBotGoals(serverGoals){", "\nfunction refreshAppDataFromServer()"
        )
        sync_goal = self.js_section(
            "async function syncGoal(action, payload={}){", "\nasync function saveNewGoal()"
        )
        allocation_mode = self.js_section(
            "function renderGoalAllocationMode(){", "\nfunction openGoalSheet("
        )
        assign_money = self.js_section(
            "async function assignMoney(){", '\ndocument.getElementById("gEdit").addEventListener'
        )
        delete_handler = self.js_section(
            'document.getElementById("gDel").addEventListener("click",e=>{',
            '\ndocument.getElementById("goals-all").addEventListener',
        )

        script = f"""
const assert=require("node:assert/strict");
const DATA={{goals:{json.dumps(initial)}}};
const SERVER_GOALS={json.dumps(server)};
const APP_MODE="bridge";
const API_OK={str(api_ok).lower()};
const API_ERROR={json.dumps(api_error)};
const API_ERROR_DETAILS={json.dumps(error_details or {})};
const DEFER_API={str(deferred).lower()};
let requestCount=0, resolveApi=null, closeCount=0, renderSnapshots=[], requestPayloads=[];
let gIdx=0, gMode="assign", goalWriteInFlight=false, goalDeleteInFlight=false;
const nodes=Object.create(null);
function el(id){{
  if(!nodes[id]) nodes[id]={{id,dataset:{{}},attributes:{{}},style:{{}},disabled:false,hidden:false,textContent:"",innerHTML:"",
    addEventListener(type,fn){{this.listeners=this.listeners||{{}};this.listeners[type]=fn;}},
    setAttribute(name,value){{this.attributes[name]=value;}},
    removeAttribute(name){{delete this.attributes[name];}},
    focus(){{}},select(){{}}}};
  return nodes[id];
}}
const document={{getElementById:el}};
const gIn=el("gIn"); gIn.value="50";
el("gHint").textContent="Wie viel möchtest du zuordnen?";
function apiReady(){{return true;}}
function makeResponse(){{const body=API_OK?{{ok:true,goals:SERVER_GOALS}}:{{ok:false,error:API_ERROR,...API_ERROR_DETAILS}};return {{ok:API_OK,status:API_OK?200:409,json:async()=>body}};}}
function apiFetch(path,options){{
  assert.equal(path,"/v1/goals"); requestCount++; requestPayloads.push(JSON.parse(options.body));
  if(DEFER_API) return new Promise(resolve=>{{resolveApi=()=>resolve(makeResponse());}});
  return Promise.resolve(makeResponse());
}}
function mergeBotGoals(serverGoals){{
  const current=Array.isArray(DATA.goals)?DATA.goals:[];
  const merged=[...(serverGoals||[])];
  current.forEach(goal=>{{
    const exists=merged.some(server=>String(server.t||"").toLowerCase()===String(goal.t||"").toLowerCase());
    if(!exists && goal.source!=="bot") merged.push(goal);
  }});
  return merged;
}}
function renderGoals(){{renderSnapshots.push(DATA.goals.map(goal=>({{id:goal.id,cur:goal.cur,tar:goal.tar}})));}}
function mentorLine(){{}}
function showToast(){{}}
function closeSheet(){{closeCount++;}}
function buzz(){{}}
function celebrateGoal(){{}}
function logActivity(){{}}
function persistAppState(){{}}
function eur(value){{return `${{value}} €`;}}
function pct(value,total){{return Math.round(value/total*100);}}
{helpers}
{sync_goal}
{allocation_mode}
{assign_money}
{delete_handler}
(async()=>{{
{body}
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
        result = subprocess.run(
            [node, "--input-type=commonjs"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(result.stdout.strip(), "PASS")

    def test_confirmed_delete_removes_goal_from_rendered_list_without_reload(self):
        goal = {"id": "goal-1", "t": "Dubai", "cur": 51, "tar": 4000, "source": "app"}
        primary = {"id": "primary", "t": "Reserve", "cur": 20, "tar": 100, "source": "bot"}
        self.run_node(
            """
const button=el("gDel"), handler=button.listeners.click;
handler({currentTarget:button});
assert.equal(button.dataset.arm,"1");
handler({currentTarget:button});
assert.equal(button.disabled,true);
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(DATA.goals.map(goal=>goal.id),["primary"]);
assert.deepEqual(renderSnapshots.at(-1).map(goal=>goal.id),["primary"]);
assert.equal(closeCount,1);
assert.equal(button.disabled,false);
console.log("PASS");
""",
            initial=[goal, primary],
            server=[primary],
        )

    def test_assignment_renders_server_progress_once_and_prevents_duplicate_write(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 50, "tar": 100, "source": "app"}
        updated = {"id": "goal-1", "t": "Dubai", "cur": 100, "tar": 100, "source": "app"}
        self.run_node(
            """
const operation=assignMoney();
assert.equal(el("gSend").disabled,true);
assert.equal(el("gHint").textContent,"Zuordnung wird gespeichert …");
assert.equal(DATA.goals[0].cur,50); // No optimistic balance before server confirmation.
await assignMoney();
assert.equal(requestCount,1);
assert.deepEqual(requestPayloads[0],{action:"assign",goal_id:"goal-1",amount:50});
resolveApi();
await operation;
assert.equal(DATA.goals[0].cur,100);
assert.equal(renderSnapshots.length,1);
assert.equal(renderSnapshots[0][0].cur,100);
assert.equal(el("gSend").disabled,false);
assert.equal(el("gSend").attributes["aria-busy"],undefined);
assert.equal(gIn.value,"");
assert.equal(closeCount,1);
console.log("PASS");
""",
            initial=[old],
            server=[updated],
            deferred=True,
        )

    def test_failed_assignment_restores_controls_without_changing_goal(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 50, "tar": 100, "source": "app"}
        self.run_node(
            """
await assignMoney();
assert.equal(DATA.goals[0].cur,50);
assert.equal(renderSnapshots.length,0);
assert.equal(el("gSend").disabled,false);
assert.equal(el("gSend").attributes["aria-busy"],undefined);
assert.equal(el("gHint").textContent,"Wie viel möchtest du zuordnen?");
assert.equal(closeCount,0);
console.log("PASS");
""",
            initial=[old],
            server=[old],
            api_ok=False,
        )

    def test_older_state_refresh_cannot_overwrite_confirmed_assignment(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 50, "tar": 100, "source": "app"}
        updated = {"id": "goal-1", "t": "Dubai", "cur": 75, "tar": 100, "source": "app"}
        self.run_node(
            """
const oldRefreshRevision=goalStateRevision;
await syncGoal("assign",{goal_id:"goal-1",amount:25});
assert.equal(DATA.goals[0].cur,75);
assert.equal(applyServerGoals([SERVER_OLD_GOAL],oldRefreshRevision),false);
assert.equal(DATA.goals[0].cur,75);
console.log("PASS");
""".replace("SERVER_OLD_GOAL", json.dumps(old)),
            initial=[old],
            server=[updated],
        )

    def test_unassign_sends_positive_amount_and_renders_confirmed_state_without_refresh(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 500, "tar": 1000, "source": "app"}
        updated = {"id": "goal-1", "t": "Dubai", "cur": 400, "tar": 1000, "source": "app"}
        self.run_node(
            """
gMode="unassign"; gIn.value="100";
await assignMoney();
assert.deepEqual(requestPayloads,[{action:"unassign",goal_id:"goal-1",amount:100}]);
assert.equal(DATA.goals[0].cur,400);
assert.equal(renderSnapshots.length,1);
assert.equal(renderSnapshots[0][0].cur,400);
assert.equal(requestCount,1);
assert.equal(closeCount,1);
assert.match(el("gHint").textContent,/Zuordnung wird reduziert/);
console.log("PASS");
""",
            initial=[old],
            server=[updated],
        )

    def test_unassign_busy_state_prevents_duplicate_request(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 500, "tar": 1000, "source": "app"}
        updated = {"id": "goal-1", "t": "Dubai", "cur": 450, "tar": 1000, "source": "app"}
        self.run_node(
            """
gMode="unassign"; gIn.value="50";
const operation=assignMoney();
assert.equal(el("gSend").disabled,true);
assert.equal(el("gModeRemove").disabled,true);
await assignMoney();
assert.equal(requestCount,1);
resolveApi();
await operation;
assert.deepEqual(requestPayloads,[{action:"unassign",goal_id:"goal-1",amount:50}]);
assert.equal(DATA.goals[0].cur,450);
assert.equal(el("gSend").disabled,false);
assert.equal(el("gModeRemove").disabled,false);
console.log("PASS");
""",
            initial=[old],
            server=[updated],
            deferred=True,
        )

    def test_unassign_is_front_validated_and_quick_amounts_stay_positive(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 75, "tar": 1000, "source": "app"}
        self.run_node(
            """
gIdx=0;
setGoalAllocationMode("unassign");
assert.equal(gMode,"unassign");
assert.equal(el("gModeRemove").attributes["aria-pressed"],"true");
assert.match(el("gChips").innerHTML,/−100 €|−50 €/);
handleGoalQuickAmount({target:{closest:()=>({dataset:{amount:"100"}})}});
assert.equal(gIn.value,"100");
await assignMoney();
assert.equal(requestCount,0);
assert.equal(el("gHint").textContent,"Du kannst höchstens 75 € aus diesem Ziel lösen.");
assert.equal(DATA.goals[0].cur,75);
console.log("PASS");
""",
            initial=[old],
            server=[old],
        )

    def test_server_unassign_limit_is_shown_without_local_mutation(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 75, "tar": 1000, "source": "app"}
        self.run_node(
            """
gMode="unassign"; gIn.value="75";
await assignMoney();
assert.equal(requestPayloads[0].action,"unassign");
assert.equal(DATA.goals[0].cur,75);
assert.equal(renderSnapshots.length,0);
assert.equal(el("gHint").textContent,"Du kannst höchstens 50 € aus diesem Ziel lösen.");
assert.equal(el("gSend").disabled,false);
console.log("PASS");
""",
            initial=[old],
            server=[old],
            api_ok=False,
            api_error="goal_unassign_exceeds_current",
            error_details={"current_amount": 50},
        )

    def test_allocation_modes_keep_target_edit_presets_neutral(self):
        goal = {"id": "goal-1", "t": "Dubai", "cur": 75, "tar": 1000, "source": "app"}
        self.run_node(
            """
gIdx=0;
renderGoalAllocationMode();
assert.ok(el("gChips").innerHTML.includes("+50 €"));
assert.ok(el("gChips").innerHTML.includes("+500 €"));
handleGoalModeClick({target:{closest:()=>({dataset:{goalMode:"unassign"}})}});
assert.equal(el("gModeSwitch").hidden,false);
assert.equal(el("gModeRemove").attributes["aria-pressed"],"true");
assert.match(el("gChips").innerHTML,/−250 €/);
gMode="target"; renderGoalAllocationMode();
assert.equal(el("gModeSwitch").hidden,true);
assert.match(el("gChips").innerHTML,/>250 €/);
assert.doesNotMatch(el("gChips").innerHTML,/[+−] 250/);
console.log("PASS");
""",
            initial=[goal],
            server=[goal],
        )

    def test_unassign_revision_protects_new_server_state_from_stale_refresh(self):
        old = {"id": "goal-1", "t": "Dubai", "cur": 500, "tar": 1000, "source": "app"}
        updated = {"id": "goal-1", "t": "Dubai", "cur": 400, "tar": 1000, "source": "app"}
        self.run_node(
            """
const oldRefreshRevision=goalStateRevision;
await syncGoal("unassign",{goal_id:"goal-1",amount:100});
assert.equal(DATA.goals[0].cur,400);
assert.equal(applyServerGoals([SERVER_OLD_GOAL],oldRefreshRevision),false);
assert.equal(DATA.goals[0].cur,400);
console.log("PASS");
""".replace("SERVER_OLD_GOAL", json.dumps(old)),
            initial=[old],
            server=[updated],
        )

    def test_existing_goal_controls_remain_bound(self):
        self.assertIn('document.getElementById("gSend").addEventListener("click",assignMoney);', self.frontend)
        self.assertIn('if(e.key==="Enter") assignMoney();', self.frontend)
        self.assertIn('document.getElementById("gModeSwitch").addEventListener("click",handleGoalModeClick);', self.frontend)
        self.assertIn('document.getElementById("gDel").addEventListener("click",e=>{', self.frontend)
        self.assertIn('document.getElementById("gChips").addEventListener("click",handleGoalQuickAmount);', self.frontend)
        self.assertIn('document.getElementById("gRateRemove").addEventListener("click",async()=>{', self.frontend)
        self.assertIn('syncGoal("set_rate",{goal_id:g.id,goal_monthly_rate:null})', self.frontend)
        self.assertIn('<div class="goal-plan-row"><span>Rechnerischer Forecast</span>', self.frontend)
        self.assertIn('document.getElementById("gEdit").addEventListener("click",()=>{', self.frontend)
        self.assertIn('document.getElementById("goalManage").open=false;', self.frontend)
        self.assertIn("const goalRevisionAtFetch=goalStateRevision;", self.frontend)
        self.assertIn("applyServerGoals(data.goals,goalRevisionAtFetch);", self.frontend)


if __name__ == "__main__":
    unittest.main()
