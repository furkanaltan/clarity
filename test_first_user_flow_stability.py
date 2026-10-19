from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


def section(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    return source[start_at : source.index(end, start_at)]


class FirstUserFlowStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = FRONTEND.read_text(encoding="utf-8")
        cls.finish_onboarding = section(
            source, "async function continueAfterServerOnboarding()", "// liest getippte Werte des aktuellen Screens"
        )
        cls.onboarding_advance = section(
            source, "async function obAdvance(", 'document.getElementById("onboard").addEventListener("click"'
        )
        cls.onboarding_draft_state = section(
            source, "function resetOnboardingDraft(){", "// Übliche weitere Einkommensquellen"
        )
        cls.onboarding_draft_request = section(
            source, "async function onboardingDraftRequest(", "function serializeOnboardingDraft()"
        )
        cls.load_onboarding_draft = section(
            source, "async function showServerOnboarding(){", 'window.addEventListener("rove:api-locked"'
        )
        cls.expense_sync = section(
            source, "async function syncExpenseToServer(e, raw){", "// Gegenstück zu syncExpenseToServer"
        )
        cls.add_entry = section(
            source, "async function addEntry(){", "// Neues Abo erkannt"
        )
        cls.close_and_navigate = section(
            source, "function closeSheetAndNavigate(tab){", 'document.querySelectorAll(".tab")'
        )

    def run_node(self, script: str):
        result = subprocess.run(
            ["node", "--input-type=commonjs"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_pin_is_checked_after_onboarding_without_reentering_setup(self):
        script = """
const assert=require('node:assert/strict');
const calls=[];
const finishButton={disabled:false,textContent:'Zur App'};
const document={querySelector:selector=>selector==='[data-ob="finish"]'?finishButton:null};
const sessionStorage={setItem:()=>{}};
let APP_MODE='bridge';
let SERVER_ONBOARDING_REQUIRED=true;
let SERVER_ONBOARDING_AWAITING_PIN=false;
let pinSetupScreens=0;
let pinChecks=0;
let resumes=0;
let OB={name:'Synthetic',income:2500,otherIncome:0,payday:15,etfRate:0,cashRate:0,wealth:{}};
function obBuildAssets(){return []}
function obBuildGoals(){return []}
function obBuildVertraege(){return []}
function obIncomeTotal(){return 2500}
function obSparSum(){return 0}
function obFixSum(){return 0}
function showPinScreen(mode){if(mode==='setup')pinSetupScreens++;}
function showAuthRecovery(){}
function showToast(){}
async function apiFetch(path,options){calls.push({path,options});return {ok:true,json:async()=>({ok:true})};}
async function fetchPinStatus(){pinChecks++;return {state:'unlocked'};}
async function resumeAfterPin(){resumes++;return true;}
""" + self.finish_onboarding + """
(async()=>{
  await finishOnboarding();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/v1/onboarding');
  assert.equal(pinChecks,1);
  assert.equal(resumes,1);
  assert.equal(pinSetupScreens,0);
  assert.equal(SERVER_ONBOARDING_AWAITING_PIN,false);
})();
"""
        self.run_node(script)

    def test_onboarding_save_failure_does_not_advance_and_can_be_retried(self):
        script = """
const assert=require('node:assert/strict');
const OB={step:2};
const OB_STEPS=['welcome','name','path','income','fix','wealth','sparrate','etfplan','goal','done'];
let APP_MODE='bridge',SERVER_ONBOARDING_REQUIRED=true,PIN_SCREEN_ACTIVE=false;
let ONBOARDING_DRAFT_SAVE_IN_FLIGHT=false,ONBOARDING_DRAFT_SAVE_ERROR='';
let rejectSave=true,renders=0;
const savedSteps=[];
const document={querySelectorAll:()=>[]};
function obCapture(){}
function obRender(){renders++;}
async function persistServerOnboardingDraft(step){savedSteps.push(step);if(rejectSave)throw new Error('synthetic offline');}
async function finishOnboarding(){}
""" + self.onboarding_advance + """
(async()=>{
  const first=await obAdvance();
  assert.equal(first,false);
  assert.equal(OB.step,2);
  assert.match(ONBOARDING_DRAFT_SAVE_ERROR,/noch nicht gespeichert/);
  rejectSave=false;
  const retry=await obAdvance();
  assert.equal(retry,true);
  assert.equal(OB.step,3);
  assert.deepEqual(savedSteps,[3,3]);
  assert.equal(ONBOARDING_DRAFT_SAVE_ERROR,'');
})();
"""
        self.run_node(script)

    def test_saved_onboarding_values_and_step_are_restored_on_reopen(self):
        script = """
const assert=require('node:assert/strict');
const ONBOARDING_DRAFT_TIMEOUT_MS=10000;
const OB={step:0,name:'',wantsApi:false,income:null,otherIncome:null,otherIncomeLabel:'',payday:null,contracts:{},customFix:[],wealth:{},etfRate:null,cashRate:null,etfExecutionDay:1,etfSource:'giro',etfMode:'auto',goalName:'',goalTar:null,goalCur:null,autoFinType:null,fixExpanded:false};
const OB_STEPS=['welcome','name','path','income','fix','wealth','sparrate','etfplan','goal','done'];
let ONBOARDING_DRAFT_LOAD_IN_FLIGHT=null,ONBOARDING_DRAFT_SAVE_ERROR='',rendered=false,authSurface=false;
const body={classList:{remove(){}},innerHTML:''},foot={innerHTML:''},onboard={classList:{remove(){}},hidden:true};
const document={getElementById:id=>({obBody:body,obFoot:foot,onboard}[id]||null)};
function setAuthSurface(value){authSurface=value}
function obRender(){rendered=true}
async function apiFetch(path,options){assert.equal(path,'/v1/onboarding/draft');assert.equal(options.method,'GET');return {ok:true,json:async()=>({ok:true,step:5,draft:{name:'Synthetic',wantsApi:false,income:4200,otherIncome:null,otherIncomeLabel:'',payday:15,contracts:{miete:1100},customFix:[],wealth:{giro:5000},etfRate:300,cashRate:100,etfExecutionDay:1,etfSource:'giro',etfMode:'auto',goalName:'Reise',goalTar:2500,goalCur:500,autoFinType:null,fixExpanded:false}})}}
""" + self.onboarding_draft_state + self.onboarding_draft_request + self.load_onboarding_draft + """
(async()=>{
  const ok=await showServerOnboarding();
  assert.equal(ok,true);
  assert.equal(OB.step,5);
  assert.equal(OB.income,4200);
  assert.deepEqual(OB.contracts,{miete:1100});
  assert.deepEqual(OB.wealth,{giro:5000});
  assert.equal(authSurface,true);
  assert.equal(rendered,true);
  assert.equal(onboard.hidden,false);
})();
"""
        self.run_node(script)

    def test_ambiguous_expense_result_stays_unconfirmed_and_retry_reuses_request_id(self):
        script = """
const assert=require('node:assert/strict');
let DATA={tx:[],sts:{}};
let PENDING_BOOKING_RETRY=null,BOOKING_STATE_UNCERTAIN=false;
let apiCalls=0,renderCalls=0,errorState=null;
const payloads=[];
function apiReady(){return true}
async function apiFetch(_path,options){
  apiCalls++; payloads.push(JSON.parse(options.body));
  if(apiCalls===1)throw new TypeError('synthetic network reset');
  return {ok:true,status:200,json:async()=>({ok:true,id:73})};
}
async function refreshAppDataFromServer(){return false}
function isDefiniteBookingRejection(error){return [400,409,422].includes(Number(error?.httpStatus))}
function rollbackFailedBooking(){}
function setQuickError(message,options={}){errorState={message,options}}
function showToast(){}
function renderTx(){renderCalls++}
function tagLatestNetHistoryPoint(){}
function mentorLine(){}
function renderBudgets(){}
console.warn=()=>{};
""" + self.expense_sync + """
(async()=>{
  const expense={id:1,n:'Synthetischer Einkauf',a:-18.5,cat:'Shopping',requestId:'synthetic-expense-once',syncStatus:'pending',optimisticBalanceApplied:true};
  DATA.tx=[{d:'Heute',items:[expense]}];
  const first=await syncExpenseToServer(expense,'Synthetischer Einkauf 18,50');
  assert.equal(first,false);
  assert.equal(expense.syncStatus,'uncertain');
  assert.equal(PENDING_BOOKING_RETRY.e,expense);
  assert.equal(errorState.options.retry,true);
  assert.equal(renderCalls,1);
  const second=await syncExpenseToServer(expense,'Synthetischer Einkauf 18,50');
  assert.equal(second,true);
  assert.deepEqual(payloads.map(item=>item.request_id),['synthetic-expense-once','synthetic-expense-once']);
  assert.equal(PENDING_BOOKING_RETRY,null);
  assert.equal(expense.sid,73);
  assert.equal(expense.syncStatus,undefined);
})();
"""
        self.run_node(script)

    def test_sheet_close_cannot_restore_old_tab_after_quick_add_navigation(self):
        script = """
const assert=require('node:assert/strict');
const events=[];
const ROVE_NAV_RESTORING=false,ROVE_NAV_SUPPRESSED=false;
const DEMO_HASHES=new Set();
const location={hash:'',href:'http://localhost/app'};
const history={replaceState:(state)=>events.push(['replace',state.tab,state.sheet])};
function closeSheet(options){events.push(['close',options.skipHistory]);}
function go(tab){events.push(['go',tab]);}
""" + self.close_and_navigate + """
closeSheetAndNavigate('tx');
assert.deepEqual(events,[['close',true],['go','tx'],['replace','tx',null]]);
"""
        self.run_node(script)

    def test_quick_add_shows_no_success_or_navigation_before_server_acceptance(self):
        script = """
const assert=require('node:assert/strict');
let APP_MODE='bridge',BOOKING_SYNC_BUSY=false,BOOKING_STATE_UNCERTAIN=false,PENDING_BOOKING_RETRY=null;
const quickIn={value:'Synthetischer Einkauf 18,50',readOnly:false,placeholder:'',focus(){}};
const quickForm={querySelector:()=>({disabled:false})};
const submitError={text:'',retry:false};
const events=[];
let serverResults=[false,true];
const data={identity:{},tx:[],sts:{}};
function apiReady(){return true}
function setQuickError(message){submitError.text=message}
function parseEntry(){return {id:1,n:'Einkauf',a:-18.5,cat:'Shopping',bar:false,transfer:false}}
async function commitEntry(){events.push('save');return serverResults.shift()}
function updateQuickCat(){}
function closeSheetAndNavigate(tab){events.push('navigate:'+tab)}
function buzz(){}
function showToast(message){events.push('toast:'+message)}
function eur(value){return String(value)}
function eur2(value){return String(value)}
""" + self.add_entry + """
(async()=>{
  await addEntry();
  assert.deepEqual(events,['save']);
  assert.equal(quickIn.value,'Synthetischer Einkauf 18,50');
  assert.match(submitError.text,/Nicht gespeichert/);
  await addEntry();
  assert.equal(quickIn.value,'');
  assert.deepEqual(events.slice(-3),['save','navigate:tx','toast:✓ Einkauf · −18.5']);
})();
"""
        self.run_node(script)


if __name__ == "__main__":
    unittest.main()
