import os
import json
import shutil
import sqlite3
import subprocess
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch
import report_engine
from rove_score import calculate_score
from rove_expense_domain import classified_expenses

from rove_app_state import _monthly_budget_truth
from rove_feature_announcements import (
    ensure_feature_announcement_tables,
    get_feature_announcements_for_user,
    mark_feature_announcement,
)


FRONTEND_PATH = Path(os.environ.get(
    "ROVE_FRONTEND_PATH",
    Path(__file__).resolve().parent / "frontend" / "index.html",
))


class FinalFixServerTests(unittest.TestCase):
    def budget_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL,
                category TEXT, created_at TEXT
            );
            CREATE TABLE category_budgets (
                user_id INTEGER, category TEXT, monthly_limit REAL,
                source TEXT, active_month TEXT
            );
            CREATE TABLE app_cash_movements (id INTEGER PRIMARY KEY, user_id INTEGER, expense_id INTEGER, kind TEXT);
        """)
        return conn

    def test_budget_truth_separates_category_and_whole_month_remaining(self):
        month = date.today().strftime("%Y-%m")
        previous = "2000-01"
        with closing(self.budget_connection()) as conn:
            conn.executemany(
                "INSERT INTO category_budgets VALUES (1, ?, ?, 'manual', ?)",
                [("LEBENSMITTEL", 600, month), ("SHOPPING", 349, month)],
            )
            conn.executemany(
                "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, ?)",
                [
                    (1, 557, "LEBENSMITTEL", f"{month}-10 12:00:00"),
                    (1, 1007, "SONSTIGES", f"{month}-11 12:00:00"),
                    (1, 75, "ABOS", f"{month}-12 12:00:00"),
                    (1, 300, "ETF", f"{month}-13 12:00:00"),
                    (1, 200, "UMBUCHUNG", f"{month}-14 12:00:00"),
                    (1, 999, "SHOPPING", f"{previous}-10 12:00:00"),
                    (2, 900, "LEBENSMITTEL", f"{month}-10 12:00:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO app_cash_movements (user_id, expense_id, kind) VALUES (1, ?, ?)",
                [(3, "fixed"), (4, "investment"), (5, "transfer")],
            )
            truth = _monthly_budget_truth(
                conn, 1, income=4430, fixed_costs=2100, savings=1000
            )
            without_savings = _monthly_budget_truth(
                conn, 1, income=4430, fixed_costs=2100, savings=0
            )

        self.assertEqual(truth["category_limit_total"], 949)
        self.assertEqual(truth["category_spent"], 557)
        self.assertEqual(truth["category_remaining"], 392)
        self.assertEqual(truth["variable_expenses"], 1564)
        self.assertEqual(truth["financial_month_budget"], 1330)
        self.assertEqual(truth["free_month_remaining"], -234)
        self.assertEqual(
            without_savings["free_month_remaining"] - truth["free_month_remaining"],
            1000,
        )

    def test_budget_truth_exposes_pre_expense_budget_without_double_counting_savings(self):
        month = date.today().strftime("%Y-%m")
        with closing(self.budget_connection()) as conn:
            conn.execute(
                "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (1, 20, 'LEBENSMITTEL', ?)",
                (f"{month}-10 12:00:00",),
            )
            truth = _monthly_budget_truth(
                conn, 1, income=4430, fixed_costs=2105.32, savings=1000
            )

        self.assertEqual(truth["financial_month_budget"], 1324.68)
        self.assertEqual(truth["variable_expenses"], 20)
        self.assertEqual(truth["free_month_remaining"], 1304.68)

    def test_same_payment_classification_in_budget_score_and_report(self):
        month = date.today().strftime("%Y-%m")
        with closing(self.budget_connection()) as conn:
            conn.executescript("""
                ALTER TABLE expenses ADD COLUMN merchant TEXT DEFAULT 'Example';
                ALTER TABLE expenses ADD COLUMN description TEXT DEFAULT '';
                CREATE TABLE app_user_features(user_id INTEGER, feature_key TEXT, enabled INTEGER);
            """)
            user = dict(income=4430, fixed_costs=2105.32, etf_savings=300, cash_savings=700)
            def budget():
                return _monthly_budget_truth(conn, 1, income=4430, fixed_costs=2105.32, savings=1000)
            self.assertEqual(budget()["free_month_remaining"], 1324.68)
            conn.execute("INSERT INTO expenses (id,user_id,amount,category,created_at) VALUES (1,1,20,'ABOS',?)", (month+"-01",))
            # A different user's movement must not classify this user's payment.
            conn.execute("INSERT INTO app_cash_movements VALUES (1,2,1,'fixed')")
            for kind, expected in ((None, 20), ("fixed", 0), ("transfer", 0), ("savings", 0), ("payment", 20)):
                with self.subTest(kind=kind):
                    conn.execute("DELETE FROM app_cash_movements WHERE user_id=1")
                    if kind:
                        conn.execute("INSERT INTO app_cash_movements VALUES (2,1,1,?)", (kind,))
                    truth = budget()
                    self.assertEqual(truth["variable_expenses"], expected)
                    self.assertEqual(truth["free_month_remaining"], round(1324.68-expected,2))
                    with patch.object(report_engine, "get_db", return_value=conn):
                        total, _, _ = report_engine.get_expense_stats(1, month)
                    self.assertEqual(total, expected)
                    score = calculate_score(conn, 1, user)
                    expected_score = calculate_score(conn, 1, user, total_expenses=expected)
                    self.assertEqual(score, expected_score)
                    self.assertEqual(len(classified_expenses(conn,1,month)), 1)

    def test_running_month_budget_pace_calibration(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE expenses (
                id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL,
                category TEXT, created_at TEXT
            );
            CREATE TABLE app_user_features(
                user_id INTEGER, feature_key TEXT, enabled INTEGER
            );
        """)
        conn.execute(
            "INSERT INTO expenses VALUES (1, 1, 1, 'SONSTIGES', '2026-09-01 12:00:00')"
        )
        user = {
            "income": 1000, "other_income": 0, "fixed_costs": 0,
            "etf_savings": 0, "cash_savings": 0, "current_cash": 0,
            "onboarding_step": 10, "clarity_points": 0,
        }
        today = date(2026, 9, 11)
        expected_spend = 1000 * 11 / 30
        baseline_other_components = None
        for pace, points in (
            (0.90, 25), (1.10, 22), (1.20, 18), (1.40, 14),
            (1.58, 10), (1.90, 6),
        ):
            with self.subTest(pace=pace):
                total = expected_spend * pace
                score = calculate_score(
                    conn, 1, user, total_expenses=total,
                    report_month="2026-09", today=today,
                )
                self.assertEqual(score["budget"], points)
                self.assertGreater(score["spendable_budget"] - total, 0)
                other_components = (
                    score["savings"], score["consistency"], score["structure"]
                )
                if baseline_other_components is None:
                    baseline_other_components = other_components
                else:
                    self.assertEqual(other_components, baseline_other_components)

        overrun = calculate_score(
            conn, 1, user, total_expenses=1001,
            report_month="2026-09", today=today,
        )
        self.assertEqual(overrun["budget"], 0)

        live_user = {
            "income": 3500, "other_income": 930, "fixed_costs": 2587.62,
            "etf_savings": 300, "cash_savings": 700, "current_cash": 6100,
            "onboarding_step": 10, "clarity_points": 0,
        }
        live = calculate_score(
            conn, 1, live_user, total_expenses=489,
            report_month="2026-09", today=today,
        )
        self.assertAlmostEqual(live["spendable_budget"], 842.38, places=2)
        self.assertAlmostEqual(live["spendable_budget"] - 489.0, 353.38, places=2)
        self.assertAlmostEqual(live["budget"], 10, places=0)

    def test_opened_is_not_prominent_but_remains_in_archive(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript("""
                CREATE TABLE users (user_id INTEGER PRIMARY KEY);
                CREATE TABLE app_accounts (
                    id INTEGER PRIMARY KEY, user_id INTEGER,
                    verified_at TEXT, created_at TEXT
                );
                INSERT INTO users VALUES (1);
                INSERT INTO app_accounts (user_id, verified_at, created_at)
                VALUES (1, CURRENT_TIMESTAMP, datetime('now', '-1 day'));
            """)
            ensure_feature_announcement_tables(conn)
            conn.execute("""
                INSERT INTO app_feature_announcements
                    (feature_id, title, priority, published_at)
                VALUES ('opened_feature', 'Opened', 'major', CURRENT_TIMESTAMP),
                       ('seen_feature', 'Seen', 'major', CURRENT_TIMESTAMP)
            """)
            mark_feature_announcement(conn, 1, "opened_feature", "opened")
            mark_feature_announcement(conn, 1, "seen_feature", "seen")
            payload = get_feature_announcements_for_user(conn, 1)

        self.assertEqual(payload["prominent_count"], 1)
        self.assertEqual(payload["unseen_count"], 1)
        self.assertEqual(
            [item["feature_id"] for item in payload["eligible"]], ["seen_feature"]
        )
        self.assertEqual(
            {item["feature_id"] for item in payload["archive"]},
            {"opened_feature", "seen_feature"},
        )


class FinalFixFrontendTests(unittest.TestCase):
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

    def test_crypto_and_monthly_routes_have_safe_end_states(self):
        router = self.function_source("openFeatureDeepLink")
        monthly = self.function_source("renderMonthlyPlan")
        self.assertIn('else openAssetKind("crypto")', router)
        self.assertNotIn("if(index<0) return false", router)
        self.assertIn("Aktuell ist nichts fällig.", monthly)

    def test_consumption_uses_payment_classification_not_category_or_merchant(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        script = self.function_source("isConsumptionExpense") + self.function_source("catSpentFrom") + """
const groups=[{items:[
 {a:-20,cat:'Abos',n:'Netflix',classification:'consumption'},
 {a:-20,cat:'Abos',n:'Netflix',classification:'fixed_cost'},
 {a:-20,cat:'Abos',classification:'transfer'},
 {a:-20,cat:'Abos',classification:'savings'},
 {a:-20,cat:'Abos',transfer:true},
 {a:20,cat:'Abos',classification:'income'}
]}];
process.stdout.write(JSON.stringify([catSpentFrom('Abos',groups),
 isConsumptionExpense({a:-20,cat:'Abos',n:'Netflix'})]));
"""
        result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), [20, True])

    def test_crypto_route_selects_management_or_add_without_dead_end(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        router = self.function_source("openFeatureDeepLink")
        script = """
let calls=[];
let DATA={assets:[{name:"Krypto",assetKey:"asset:crypto"}]};
function openAssetDetail(index){calls.push(["manage",index]);}
function openAssetKind(kind){calls.push(["add",kind]);}
function openTalk(){} function openScore(){} function openReports(){}
function openSettings(){} function openActivity(){} function openAnalysis(){}
function openAnalysisMerchants(){} function openMonthlyPlan(){} function closeSheet(){}
function go(){}
""" + router + """
openFeatureDeepLink("asset-krypto");
DATA.assets=[];
openFeatureDeepLink("asset-krypto");
process.stdout.write(JSON.stringify(calls));
"""
        result = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        self.assertEqual(json.loads(result.stdout), [["manage", 0], ["add", "crypto"]])

    def test_monthly_route_renders_due_and_nothing_due_states(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        renderer = self.function_source("renderMonthlyPlan")
        script = """
const el={innerHTML:""};
const document={getElementById:()=>el};
const APP_MODE="bridge";
let DATA={monthlyPlan:{},monthlyCheckinActions:[],payday:{},sts:{sparratenParts:{}},etfPlan:{}};
function updateMonthlyPlanEntryPoint(){} function monthlyPlanStatus(){return "";}
function eur2(value){return String(value);} function etfPlanSummaryCard(){return "";}
""" + renderer + """
renderMonthlyPlan();
const empty=el.innerHTML.includes("Aktuell ist nichts fällig.");
DATA.monthlyCheckinActions=[{kind:"month_close",due:true,completed:false,title:"Monat abschließen",detail:"Fällig",month:"2026-07"}];
renderMonthlyPlan();
process.stdout.write(JSON.stringify({empty,due:el.innerHTML.includes("Monat abschließen"),wrong:el.innerHTML.includes("Aktuell ist nichts fällig.")}));
"""
        result = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        self.assertEqual(
            json.loads(result.stdout), {"empty": True, "due": True, "wrong": False}
        )

    def test_budget_and_coach_use_explicit_month_truth(self):
        free_budget = self.function_source("freeBudget")
        mentor = self.function_source("mentorLine")
        self.assertIn("free_month_remaining", free_budget)
        self.assertIn("budgetFrameStatus().left", mentor)
        self.assertIn("Deine Budgets haben noch", mentor)
        self.assertIn("Dein Monatsplan liegt aktuell", mentor)
        self.assertNotIn("über deinem verfügbaren Betrag", mentor)
        self.assertNotIn("über deinem Budget", mentor)
        self.assertEqual(
            self.frontend.count("DATA.sts.free_month_remaining=data.available;"), 3
        )

    def test_cashflow_budget_summary_uses_canonical_month_truth(self):
        summary_start = self.frontend.index("const canonicalBudget=")
        summary_end = self.frontend.index("box.innerHTML", summary_start)
        summary = self.frontend[summary_start:summary_end]
        self.assertIn("financial_month_budget", summary)
        self.assertIn("free_month_remaining", summary)
        self.assertIn("variableMonthExpenses", summary)
        self.assertIn("Budget um ${eur(Math.abs(remaining))} überschritten", summary)
        self.assertIn("Monatsplan nicht gedeckt", summary)
        self.assertNotIn("eur(totalLimit)", summary)
        self.assertNotIn("eur(budgetLeft)", summary)
        self.assertNotIn("homeBudgetStatus", self.frontend)
        self.assertNotIn("renderHomeBudgetStatus", self.frontend)

    def test_divergent_budget_truth_renders_without_contradiction(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed")
        mentor = self.function_source("mentorLine")
        script = """
const output={innerHTML:""};
const mentorCard={classList:{toggle(){},remove(){},add(){}},dataset:{}};
const document={getElementById:()=>output,querySelector:()=>mentorCard};
const APP_MODE="bridge";
const DATA={monthlyCheckinActions:[],featureAnnouncements:{},goals:[{}],assets:[{}],budgets:[{}],monthlyPlan:{incomeStatus:"confirmed"}};
function mentorDataReady(){return false;} function freeBudget(){return -234;}
function budgetFrameStatus(){return {left:392};} function eur(value){return `${value} €`;}
""" + mentor + """
mentorLine();
process.stdout.write(output.innerHTML);
"""
        result = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )
        self.assertIn("392 €", result.stdout)
        self.assertIn("234 €", result.stdout)
        self.assertIn("Deine Budgets haben noch", result.stdout)
        self.assertNotIn("über deinem Budget", result.stdout)

    def test_sprint_three_priority_and_state_isolation_stay_present(self):
        mentor = self.function_source("mentorLine")
        local_state = self.function_source("updateAnnouncementLocalState")
        self.assertLess(mentor.index("if(dueActions.length)"), mentor.index("coachAnnouncement"))
        self.assertIn("coach?.feature_id===featureId", local_state)
        self.assertIn("DATA.featureAnnouncements.coach=null", local_state)


if __name__ == "__main__":
    unittest.main()
