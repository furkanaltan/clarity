import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


class FrontendAskRoveSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")

    def _function(self, name, next_marker):
        start = self.source.index(f"function {name}(")
        if self.source[max(0, start - 6):start] == "async ":
            start -= 6
        end = self.source.index(next_marker, start)
        return self.source[start:end]

    def run_node(self, source):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for this frontend regression test")
        result = subprocess.run([node, "-e", source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stored_goal_and_merchant_names_are_escaped_in_chat_answers(self):
        helpers = self._function("announcementEscape", "function chatText")
        helpers += self._function("chatText", "function mentorFactorLabel")
        goal = self._function("ans_goal", "function ans_budgets")
        largest = self._function("ans_largest", "function ans_topcat")
        script = f'''const assert=require("node:assert/strict");
{helpers}
function eur(value){{return `${{value}} EUR`;}}
function eur2(value){{return `${{value}} EUR`;}}
function pct(value,target){{return target>0?Math.round(value/target*100):0;}}
const attack='<img src=x onerror=alert(1)>';
const DATA={{}};
function trackedOuts(){{return [{{a:-25,n:attack,cat:"Shopping"}}];}}
{goal}
{largest}
const goalAnswer=ans_goal({{t:attack,cur:0,tar:100}});
const merchantAnswer=ans_largest();
for(const answer of [goalAnswer,merchantAnswer]){{
  assert.ok(answer.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!answer.includes('<img'));
}}
'''
        self.run_node(script)

    def test_question_about_credit_does_not_mutate_but_explicit_command_still_works(self):
        question_guard = self._function("isChatQuestionOrHypothesis", "function isPersonalTradeRecommendationQuestion")
        trade_guard = self._function("isPersonalTradeRecommendationQuestion", "function ans_available_cash")
        dispatcher = self._function("roveAnswer", "// Vorschlags-Chips zeigen die Bandbreite")
        income = self._function("ans_cash_income", "async function ans_set_investment")
        script = f'''const assert=require("node:assert/strict");
let APP_MODE="bridge", calls=[];
const extractAmt=()=>50, eur=value=>`${{value}} EUR`;
async function syncCashAccount(...args){{calls.push(args);return true;}}
{question_guard}
{trade_guard}
{income}
{dispatcher}
(async()=>{{
  const question="Habe ich eine Gutschrift von 50 € bekommen?";
  assert.match(await roveAnswer(question), /keine Gutschrift bestätigen/);
  assert.equal(await ans_cash_income(question), null);
  assert.equal(calls.length, 0);
  const answer=await ans_cash_income("Füge 50 Euro Gutschrift meinem Girokonto hinzu".toLowerCase());
  assert.match(answer, /hinzugefügt/);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], ["adjust", {{account:"giro",direction:"add",amount:50}}]);
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
'''
        self.run_node(script)

    def test_chat_markup_renderer_allows_only_safe_formatting(self):
        renderer = self._function("appendSafeChatMarkup", "function renderSafeChatMarkup")
        renderer += self._function("renderSafeChatMarkup", "function pushBub")
        push_start = self.source.index("function pushBub(")
        push_end = self.source.index("function rovReply", push_start)
        push = self.source[push_start:push_end]
        self.assertIn('["b","br","span"]', renderer)
        self.assertIn("document.createTextNode(node.outerHTML||node.textContent||\"\")", renderer)
        self.assertIn("renderSafeChatMarkup(d,content)", push)
        self.assertNotIn("d.innerHTML=content", push)

    def test_available_money_question_routes_to_canonical_cash_not_wallet(self):
        wallet = self._function("isExplicitWalletCashQuestion", "function isAvailableCashQuestion")
        available = self._function("isAvailableCashQuestion", "// Dispatcher: Thema")
        script = f'''const assert=require("node:assert/strict");
{wallet}
{available}
const text="wie viel geld habe ich verfügbar?";
assert.equal(isExplicitWalletCashQuestion(text),false);
assert.equal(isAvailableCashQuestion(text),true);
assert.equal(isExplicitWalletCashQuestion("wie viel bargeld habe ich?"),true);
'''
        self.run_node(script)

    def test_personal_buy_sell_questions_hit_the_deterministic_boundary(self):
        trade = self._function("isPersonalTradeRecommendationQuestion", "function ans_available_cash")
        script = f'''const assert=require("node:assert/strict");
{trade}
for(const question of ["Welche Aktie soll ich kaufen?","Soll ich XRP kaufen?","Verkauf ich NEAR?"])
  assert.equal(isPersonalTradeRecommendationQuestion(question),true,question);
assert.equal(isPersonalTradeRecommendationQuestion("Wie hat sich mein Portfolio entwickelt?"),false);
'''
        self.run_node(script)

    def test_evidence_bound_questions_bypass_local_fallbacks(self):
        dispatcher = self._function("roveAnswer", "// Vorschlags-Chips zeigen die Bandbreite")
        script = f'''const assert=require("node:assert/strict");
function isChatQuestionOrHypothesis(){{return true;}}
{dispatcher}
(async()=>{{
  const questions=[
    "Was hat mein Vermögen diesen Monat bewegt?",
    "Warum ist mein Score gesunken?",
    "Wie groß ist mein Puffer?",
    "Was war das Wichtigste in meinem letzten Report?",
    "Wie hoch sind meine Schulden?",
    "Wie viel habe ich diesen Monat für Shopping ausgegeben?",
    "Warum war Shopping so hoch?"
  ];
  for(const question of questions) assert.equal(await roveAnswer(question),null,question);
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
'''
        self.run_node(script)

    def test_frontend_consumption_filter_matches_classified_server_contract(self):
        consumption = self._function("isConsumptionExpense", "// Beobachtete Ausgaben")
        script = f'''const assert=require("node:assert/strict");
{consumption}
const items=[
  {{a:-40,classification:"consumption"}},
  {{a:-50,classification:"fixed_cost"}},
  {{a:-75,classification:"transfer"}},
  {{a:-20,classification:"refund"}},
  {{a:15,classification:"income"}}
];
assert.equal(items.filter(isConsumptionExpense).reduce((sum,item)=>sum+Math.abs(item.a),0),40);
'''
        self.run_node(script)

    def test_quick_capture_has_no_fixed_cost_classification_control(self):
        start = self.source.index('<div class="sheet" id="sheet">')
        end = self.source.index('<div class="sheet tall" id="importsheet">', start)
        quick_sheet = self.source[start:end]
        self.assertNotIn("quickFixedCost", self.source)
        self.assertNotIn("quick-fixed-cost", self.source)
        self.assertNotIn("Geplante Fixkostenabbuchung", quick_sheet)
        self.assertIn('id="quickForm"', quick_sheet)
        self.assertIn('id="quickChips"', quick_sheet)
        self.assertIn('id="scanOpen"', quick_sheet)
        self.assertIn('class="scan-fixed"', self.source)


if __name__ == "__main__":
    unittest.main()
