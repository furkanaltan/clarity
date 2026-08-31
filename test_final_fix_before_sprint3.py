import os
import json
import shutil
import sqlite3
import subprocess
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

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
        self.assertEqual(truth["free_month_remaining"], -234)
        self.assertEqual(
            without_savings["free_month_remaining"] - truth["free_month_remaining"],
            1000,
        )

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
