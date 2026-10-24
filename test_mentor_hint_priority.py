from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


class MentorHintPriorityTests(unittest.TestCase):
    def test_existing_actionable_signals_are_checked_before_generic_score(self):
        source = (Path(__file__).resolve().parent / "frontend/index.html").read_text()
        selection = source[source.index("let mentorSoftSelection=null;"):
                           source.index("function shouldWelcomeFirstBudget(){")]
        renderer = source[source.index("function mentorLine(){"):
                          source.index("let coachV4ClaimKey=")]
        script = """
const assert=require('assert');
const body={innerHTML:''}, card={dataset:{},classList:{remove(){},toggle(){}}};
const document={getElementById:()=>body,querySelector:()=>card}, APP_MODE='bridge';
const storage=new Map();
const localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,String(value))};
function mentorOwnerKey(){return 'bridge:synthetic';}
const score={id:'motivation',type:'motivation',priority:10,title:'Score fallback',
  message:'Synthetisch',deep_link:'score'};
const DATA={monthlyPlan:{incomeStatus:'confirmed'},goals:[{}],assets:[{}],budgets:[],
  score:{value:81,next_lever:'Mobilität',savings_confirmed:true,savings_ratio:.1,
    points:{value:25,to_next:10,next_rank:'Stufe 2'},tracking_days_90:12}};
let free=500, active=false, allowClaim=true, claims=0, incomeDue=false;
function setMentorCtaTarget(target){card.dataset.mentorTarget=target;}
function shouldWelcomeFirstBudget(){return false;}
function announcementEscape(v){return String(v);}
function mentorFactorLabel(v){return String(v);}
function mentorDataReady(){return false;}
function freeBudget(){return free;}
function catSpent(){return 126;}
function mentorBudgetAlert(){return {active};}
function budgetFrameStatus(){return {left:300};}
function eur(v){return String(v);} function eur2(v){return Number(v).toFixed(2).replace('.',',')+' €';}
function daysUntilMonthEnd(){return 10;}
function mentorIncomeReminderDue(){return incomeDue;}
function claimMentorV4Insight(){claims++;return allowClaim;}
function acknowledgeMentorV4AfterRender(){}
""" + selection + renderer + """
function render({alert=false,available=true,v4=false,candidate=score,balance=500,due=[],salaryDue=false,reset=false}={}){
  if(reset){storage.clear();mentorSoftSelection=null;}
  active=alert;allowClaim=available;free=balance;claims=0;
  incomeDue=salaryDue;
  DATA.budgets=alert?[{cat:'Mobilität',limit:100}]:[];
  DATA.monthlyCheckinActions=due;DATA.mentorCandidate=candidate;
  DATA.coachV4Insight=v4?{insight_id:'v4:test',title:'Belegtes V4',message:'Synthetisch'}:null;
  mentorLine();
  return {html:body.innerHTML,claims};
}
const results={
  activeCategory:render({alert:true}),
  activeCategoryWithV4:render({alert:true,v4:true}),
  monthly:render({due:[{due:true,completed:false,title:'Monatscheck',detail:'Prüfen'}]}),
  salary:render({salaryDue:true}),
  negativeBalance:render({balance:-20}),
  validV4:render({v4:true}),
  noSignals:render(),
  suppressedV4:render({v4:true,available:false}),
  concreteV3:render({candidate:{...score,id:'budget-overrun',type:'budget_overrun',
    priority:100,title:'Dringendes V3'},alert:true,v4:true}),
};
storage.clear();mentorSoftSelection=null;
const openingChoices=[];
for(let opening=0;opening<4;opening++){
  if(opening) mentorSoftSelection=null;
  openingChoices.push(render({reset:false}).html);
}
const sameSessionBefore=body.innerHTML;
mentorLine();
const sameSessionAfter=body.innerHTML;
results.openingChoices=openingChoices;
results.sameSessionStable=sameSessionBefore===sameSessionAfter;
DATA.score={value:null,next_lever:null,points:{value:0},tracking_days_90:0};
storage.clear();mentorSoftSelection=null;
const singleCandidateChoices=[];
for(let opening=0;opening<3;opening++){
  if(opening) mentorSoftSelection=null;
  singleCandidateChoices.push(render({reset:false}).html);
}
results.singleCandidateChoices=singleCandidateChoices;
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        for name in ("activeCategory", "activeCategoryWithV4"):
            with self.subTest(name=name):
                self.assertIn("Mobilität", cases[name]["html"])
                self.assertIn("Mobilität liegt 26,00 € über Budget.", cases[name]["html"])
                self.assertNotIn("Score fallback", cases[name]["html"])
                self.assertEqual(cases[name]["claims"], 0)
        self.assertIn("Monatscheck", cases["monthly"]["html"])
        self.assertNotIn("Score fallback", cases["monthly"]["html"])
        self.assertIn("Gehalt ist für diesen Monat noch nicht bestätigt", cases["salary"]["html"])
        self.assertNotIn("Score fallback", cases["salary"]["html"])
        self.assertIn("20", cases["negativeBalance"]["html"])
        self.assertNotIn("Score fallback", cases["negativeBalance"]["html"])
        self.assertIn("Belegtes V4", cases["validV4"]["html"])
        self.assertEqual(cases["validV4"]["claims"], 1)
        for name in ("noSignals", "suppressedV4"):
            self.assertIn("zur freien Verfügung", cases[name]["html"])
            self.assertNotIn("Score fallback", cases[name]["html"])
        self.assertIn("Dringendes V3", cases["concreteV3"]["html"])
        self.assertEqual(cases["concreteV3"]["claims"], 0)
        choices = cases["openingChoices"]
        self.assertEqual(len(set(choices)), 4)
        self.assertIn("zur freien Verfügung", choices[0])
        self.assertIn("Dein Rov.E Score", choices[1])
        self.assertIn("Rov.E Punkte", choices[2])
        self.assertIn("12 Tagen", choices[3])
        self.assertTrue(cases["sameSessionStable"])
        self.assertEqual(len(set(cases["singleCandidateChoices"])), 1)


if __name__ == "__main__":
    unittest.main()
