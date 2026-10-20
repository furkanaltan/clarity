import json
import re
import subprocess
import unittest
from pathlib import Path


SOURCE = Path(__file__).parent.joinpath("frontend", "index.html").read_text(encoding="utf-8")


def section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]


def function(name):
    return re.search(r"(?:async )?function " + name + r"\([^\n]*\)\{[\s\S]*?\n\}", SOURCE).group()


ICONS = section("function moduleIcon(name){", "// Empty State")
ONBOARDING = section("const OB = {", "async function persistServerOnboardingDraft(")
ONBOARDING += section("const OB_INCOME_LABELS =", "async function continueAfterServerOnboarding(")
LOANS = section("const LOAN_NAME_RE=", "function ans_debt(){")
MENTOR = section("function setMentorCtaTarget(", "let reportOpenInFlight=")

DOM = """
const assert=require('node:assert/strict');
const elements=new Map();
function element(){return {innerHTML:'',textContent:'',hidden:false,disabled:false,dataset:{},style:{},
  classList:{add(){},remove(){},toggle(){}},setAttribute(){},append(){}};}
const mentor=element(), arrow={setAttribute:(name,value)=>{arrow[name]=value;}};
mentor.querySelector=()=>arrow;
mentor.addEventListener=(_type,handler)=>{mentor.clickHandler=handler;};
const events=[];
const document={getElementById:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},
  querySelector:selector=>selector==='.mentor'?mentor:selector==='#txPills [data-f="budget"]'?{click:()=>events.push('budget-tab')}:element()};
const eur=value=>String(value)+' €', eur2=eur;
const setTimeout=()=>0;
const SERVER_ONBOARDING_REQUIRED=true, ONBOARDING_DRAFT_SAVE_ERROR='';
let APP_MODE='bridge', BRIDGE_LIVE_STATE_READY=true;
const PROFILE_META={onboarded:true};
const DATA={score:{platform_days:0},budgets:[],budgetHistory:{},txHistory:{},reports:[],goals:[],assets:[],tx:[],
  monthlyCheckinActions:[],mentorCandidate:{type:'liquidity',priority:90,deep_link:'score',title:'Dein Notgroschen ist noch knapp',message:'Synthetisch'}};
function mentorDataReady(){return BRIDGE_LIVE_STATE_READY;}
let free=1000;
function freeBudget(){return free;}
function trackedOuts(){return [];}
const announcementEscape=value=>String(value);
function closeSheetAndNavigate(tab){events.push(tab);}
function buzz(){}
"""


class OnboardingUxPolishTests(unittest.TestCase):
    def run_js(self, body, source=""):
        result = subprocess.run(["node", "--input-type=commonjs"],
                                input=DOM + source + "\n" + body, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_new_vehicle_costs_are_not_loans_and_never_create_an_asset(self):
        self.run_js("""
OB.contracts={vehicle_costs:150,kredit:300};
OB.customFix=[{n:'Leasingrate',a:200}];
const rows=obBuildVertraege().flatMap(group=>group.items);
assert.equal(rows.find(row=>row.n==='Laufende Fahrzeugkosten').a,150);
assert.equal(isLoan(rows.find(row=>row.n==='Laufende Fahrzeugkosten')),false);
assert.equal(isLoan(rows.find(row=>row.n==='Kredit')),true);
assert.equal(isLoan(rows.find(row=>row.n==='Leasingrate')),true);
assert.equal(obFixSum(),650);
assert.deepEqual(obBuildAssets(),[]);
assert.equal(OB.autoFinType,null);
""", ICONS + ONBOARDING + LOANS)

    def test_legacy_drafts_keep_contract_names_amounts_and_financing(self):
        self.run_js("""
for(const financing of [null,'kredit','leasing','gekauft']){
  applyServerOnboardingDraft({contracts:{auto:200,vehicle_costs:80},customFix:[],wealth:{},autoFinType:financing});
  const before=JSON.stringify(serializeOnboardingDraft());
  const auto=obBuildVertraege().flatMap(group=>group.items).find(row=>row.n==='Auto');
  assert.equal(auto.a,200);
  assert.equal(auto.finType||null,financing);
  assert.equal(isLoan(auto),financing!=='gekauft');
  assert.equal(obFixSum(),280);
  assert.equal(JSON.stringify(serializeOnboardingDraft()),before);
}
""", ICONS + ONBOARDING + LOANS)

    def test_fix_step_has_no_financing_picker_and_keeps_legacy_values_visible(self):
        self.run_js("""
OB.step=4;OB.fixExpanded=true;obRender();
let html=document.getElementById('obBody').innerHTML;
assert.ok(html.includes('data-fix="vehicle_costs"'));
assert.ok(!html.includes('data-fix="auto"'));
assert.ok(!html.includes('data-autotype='));
assert.ok(html.includes('Kredit- und Leasingraten'));
OB.contracts.auto=250;OB.autoFinType='leasing';obRender();
html=document.getElementById('obBody').innerHTML;
assert.ok(html.includes('data-fix="auto"'));
assert.ok(html.includes('Gespeicherte Angabe: Leasing'));
assert.ok(!html.includes('data-autotype='));
assert.equal(OB.autoFinType,'leasing');
""", ICONS + ONBOARDING)

    def test_onboarding_uses_existing_asset_symbols_and_exact_mortgage_copy(self):
        self.run_js("""
OB.step=5;obRender();
const html=document.getElementById('obBody').innerHTML;
for(const symbol of ['building-bank','pig-money','cash','trending-up','circle-letter-b','building']){
  assert.ok(html.includes('href="#rove-module-asset-'+symbol+'"'));
}
assert.ok(html.includes('Deine Immobilienrate erfasst du separat. Hier nur weitere Kredite eintragen.'));
OB.step=6;obRender();
assert.ok(document.getElementById('obBody').innerHTML.includes('rove-module-asset-trending-up'));
assert.ok(document.getElementById('obBody').innerHTML.includes('rove-module-asset-pig-money'));
""", ICONS + ONBOARDING)

    def test_new_user_gets_welcome_instead_of_weak_liquidity(self):
        self.run_js("""
mentorLine();
const html=document.getElementById('mentorSay').innerHTML;
assert.ok(html.includes('Willkommen bei Rov.E!'));
assert.ok(html.includes('Dein Finanzüberblick beginnt hier. Richte jetzt dein erstes Budget ein und behalte deine Ausgaben im Blick.'));
assert.ok(html.includes('Budget einrichten'));
assert.ok(!html.includes('Notgroschen'));
assert.equal(arrow['data-mentor-target'],'budget-setup');
""", MENTOR)

    def test_budget_setup_restores_normal_coach_and_stays_normal_after_deletion(self):
        self.run_js("""
for(const auto of [false,true]){
  DATA.budgets=[{cat:'Lebensmittel',limit:250,auto}];mentorLine();
  assert.ok(document.getElementById('mentorSay').innerHTML.includes('Notgroschen'));
}
DATA.budgets=[];DATA.budgetHistory={'2026-09':[{cat:'Shopping',limit:100}]};mentorLine();
assert.ok(!document.getElementById('mentorSay').innerHTML.includes('Willkommen'));
""", MENTOR)

    def test_existing_and_unknown_age_accounts_are_not_guessed_to_be_new(self):
        self.run_js("""
for(const days of [2,90,undefined,null,'0',NaN]){
  DATA.score.platform_days=days;mentorLine();
  assert.ok(!document.getElementById('mentorSay').innerHTML.includes('Willkommen'));
}
DATA.score.platform_days=0;BRIDGE_LIVE_STATE_READY=false;mentorLine();
assert.ok(!document.getElementById('mentorSay').innerHTML.includes('Willkommen'));
""", MENTOR)

    def test_real_warnings_and_previous_usage_are_not_hidden(self):
        self.run_js("""
for(const type of ['budget_overrun','consumer_debt','consumer_debt_new','security']){
  DATA.mentorCandidate={type,priority:100,title:'Dringender Hinweis',message:'Synthetisch',deep_link:'score'};
  mentorLine();assert.ok(document.getElementById('mentorSay').innerHTML.includes('Dringender Hinweis'));
}
DATA.mentorCandidate={type:'liquidity',priority:90,title:'Normaler Hinweis',message:'Synthetisch'};
DATA.featureAnnouncements={coach:{priority:'security'}};assert.equal(shouldWelcomeFirstBudget(),false);
DATA.featureAnnouncements={};free=-50;assert.equal(shouldWelcomeFirstBudget(),false);
free=1000;DATA.txHistory={'2026-09':[{items:[{a:-10}]}]};assert.equal(shouldWelcomeFirstBudget(),false);
DATA.txHistory={};DATA.reports=[{status:'ready'}];assert.equal(shouldWelcomeFirstBudget(),false);
""", MENTOR)

    def test_welcome_text_and_arrow_use_existing_budget_navigation(self):
        handler = section('document.querySelector(".mentor").addEventListener("click",e=>{', '// Chart-Zustand')
        self.run_js("""
mentorLine();
const event=target=>({target,stopImmediatePropagation(){}});
mentor.clickHandler(event({closest:selector=>selector==='[data-mentoract]'?{dataset:{mentoract:'budget-setup'}}:null}));
assert.deepEqual(events,['tx','budget-tab']);
events.length=0;
mentor.clickHandler(event({closest:selector=>selector==='[data-mentor-cta]'?{dataset:{mentorTarget:'budget-setup'}}:null}));
assert.deepEqual(events,['tx','budget-tab']);
""", MENTOR + handler)

    def budget_script(self, budgets, body, offset=0):
        setup = """
let monthOffset=OFFSET;
DATA.budgets=BUDGETS;
DATA.budgetHistory={'2026-09':DATA.budgets};DATA.tx=[];
const monthKey=()=> '2026-09', monthLabel=()=> 'September';
function syncBudgets(){}
const catSpent=()=>0,catSpentFrom=()=>0,threeMonthAvg=()=>null;
const budgetFrameStatus=()=>({total:500,spent:0,left:500});
const before=JSON.stringify(DATA.budgets);
""".replace("OFFSET", str(offset)).replace("BUDGETS", json.dumps(budgets))
        return self.run_js(body, setup + function("renderBudgets"))

    def test_suggested_budget_label_is_once_and_does_not_label_manual_rows(self):
        self.budget_script([
            {"cat": "Shopping", "limit": 100, "auto": True},
            {"cat": "Lebensmittel", "limit": 300, "auto": False},
            {"cat": "Restaurant", "limit": 100, "auto": True},
        ], """
renderBudgets();
const html=document.getElementById('budget-list').innerHTML;
assert.equal((html.match(/Budgetvorschläge von Rov.E/g)||[]).length,1);
assert.ok(!html.includes('Vorschlag von Rov.E'));
assert.ok(html.indexOf('data-bgt="1"')<html.indexOf('Budgetvorschläge von Rov.E'));
for(const index of [0,1,2])assert.ok(html.includes('data-bgt="'+index+'"'));
assert.equal(JSON.stringify(DATA.budgets),before);
""")

    def test_manual_budgets_and_archived_months_have_no_suggestion_heading(self):
        for offset in (0, -1):
            self.budget_script([{"cat": "Lebensmittel", "limit": 300}], """
renderBudgets();
assert.ok(!document.getElementById('budget-list').innerHTML.includes('Budgetvorschläge'));
assert.equal(JSON.stringify(DATA.budgets),before);
""", offset)


if __name__ == "__main__":
    unittest.main()
