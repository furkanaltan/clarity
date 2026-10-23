from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


class MentorHintPriorityTests(unittest.TestCase):
    def test_existing_actionable_signals_are_checked_before_generic_score(self):
        source = (Path(__file__).resolve().parent / "frontend/index.html").read_text()
        renderer = source[source.index("function mentorLine(){"):
                          source.index("let coachV4ClaimKey=")]
        script = """
const assert=require('assert');
const body={innerHTML:''}, card={dataset:{},classList:{remove(){},toggle(){}}};
const document={getElementById:()=>body,querySelector:()=>card}, APP_MODE='bridge';
const score={id:'motivation',type:'motivation',priority:10,title:'Score fallback',
  message:'Synthetisch',deep_link:'score'};
const DATA={monthlyPlan:{incomeStatus:'confirmed'},goals:[{}],assets:[{}],budgets:[]};
let free=500, active=false, allowClaim=true, claims=0;
function setMentorCtaTarget(target){card.dataset.mentorTarget=target;}
function shouldWelcomeFirstBudget(){return false;}
function announcementEscape(v){return String(v);}
function mentorDataReady(){return false;}
function freeBudget(){return free;}
function catSpent(){return 126;}
function mentorBudgetAlert(){return {active};}
function budgetFrameStatus(){return {left:300};}
function eur(v){return String(v);} function eur2(v){return String(v);}
function daysUntilMonthEnd(){return 10;}
function mentorIncomeReminderDue(){return false;}
function claimMentorV4Insight(){claims++;return allowClaim;}
function acknowledgeMentorV4AfterRender(){}
""" + renderer + """
function render({alert=false,available=true,v4=false,candidate=score,balance=500,due=[]}={}){
  active=alert;allowClaim=available;free=balance;claims=0;
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
  negativeBalance:render({balance:-20}),
  validV4:render({v4:true}),
  noSignals:render(),
  suppressedV4:render({v4:true,available:false}),
  concreteV3:render({candidate:{...score,id:'budget-overrun',type:'budget_overrun',
    priority:100,title:'Dringendes V3'},alert:true,v4:true}),
};
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        for name in ("activeCategory", "activeCategoryWithV4"):
            with self.subTest(name=name):
                self.assertIn("Mobilität", cases[name]["html"])
                self.assertIn("26 über Plan", cases[name]["html"])
                self.assertNotIn("Score fallback", cases[name]["html"])
                self.assertEqual(cases[name]["claims"], 0)
        self.assertIn("Monatscheck", cases["monthly"]["html"])
        self.assertNotIn("Score fallback", cases["monthly"]["html"])
        self.assertIn("20", cases["negativeBalance"]["html"])
        self.assertNotIn("Score fallback", cases["negativeBalance"]["html"])
        self.assertIn("Belegtes V4", cases["validV4"]["html"])
        self.assertEqual(cases["validV4"]["claims"], 1)
        for name in ("noSignals", "suppressedV4"):
            self.assertIn("Score fallback", cases[name]["html"])
        self.assertIn("Dringendes V3", cases["concreteV3"]["html"])
        self.assertEqual(cases["concreteV3"]["claims"], 0)


if __name__ == "__main__":
    unittest.main()
