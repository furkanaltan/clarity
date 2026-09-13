from __future__ import annotations

import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path

from report_html_renderer import score_dimensions
from rove_score import (
    DEBT_STATUS_NONE,
    DEBT_STATUS_PRESENT,
    DEBT_STATUS_UNKNOWN,
    calculate_score,
    ensure_debt_status_column,
)
from rove_consumer_debt import delete_consumer_debt, save_consumer_debt


TODAY = date(2026, 9, 30)


def make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            income REAL DEFAULT 0,
            other_income REAL DEFAULT 0,
            fixed_costs REAL DEFAULT 0,
            etf_savings REAL DEFAULT 0,
            cash_savings REAL DEFAULT 0,
            current_cash REAL DEFAULT 0,
            current_investments REAL DEFAULT 0,
            onboarding_step INTEGER DEFAULT 10,
            clarity_points INTEGER DEFAULT 0,
            goal_description TEXT DEFAULT '',
            goal_amount REAL DEFAULT 0,
            debt_status TEXT DEFAULT 'unknown'
        );
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            category TEXT,
            created_at TEXT
        );
        CREATE TABLE app_user_features (
            user_id INTEGER,
            feature_key TEXT,
            enabled INTEGER
        );
        CREATE TABLE app_consumer_debts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            debt_type TEXT,
            outstanding_balance REAL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            request_id TEXT,
            request_fingerprint TEXT
        );
        CREATE TABLE app_properties (
            user_id INTEGER PRIMARY KEY,
            market_value REAL DEFAULT 0,
            remaining_debt REAL DEFAULT 0,
            monthly_rate REAL DEFAULT 0
        );
        CREATE TABLE app_contracts (
            user_id INTEGER,
            contract_id TEXT,
            detail_key TEXT,
            name TEXT,
            category TEXT,
            amount REAL,
            source TEXT,
            legacy_ref TEXT
        );
        INSERT INTO users (user_id) VALUES (1);
        """
    )
    return conn


def add_tracking_days(conn: sqlite3.Connection, days: int) -> None:
    for index in range(days):
        created = (TODAY - timedelta(days=index)).isoformat() + " 12:00:00"
        conn.execute(
            "INSERT INTO expenses (id, user_id, amount, category, created_at) VALUES (?, 1, 1, 'SONSTIGES', ?)",
            (index + 1, created),
        )


def user(**overrides):
    values = {
        "income": 3000,
        "other_income": 0,
        "fixed_costs": 1000,
        "etf_savings": 0,
        "cash_savings": 0,
        "current_cash": 0,
        "current_investments": 0,
        "onboarding_step": 10,
        "clarity_points": 0,
        "debt_status": DEBT_STATUS_UNKNOWN,
    }
    values.update(overrides)
    return values


def add_property_rate_contract(conn: sqlite3.Connection, amount: float = 900) -> None:
    conn.execute(
        """INSERT INTO app_contracts
           (user_id, contract_id, detail_key, name, category, amount, source, legacy_ref)
           VALUES (1, 'mortgage-rate', 'property_monthly_rate', 'Immobilienkredit',
                   'Kredite', ?, 'property', 'app_property:monthly_rate')""",
        (amount,),
    )


def score(conn: sqlite3.Connection, profile: dict, total_expenses: float = 0):
    return calculate_score(
        conn,
        1,
        profile,
        total_expenses=total_expenses,
        report_month="2026-09",
        today=TODAY,
    )


class ScoreV2Tests(unittest.TestCase):
    def test_score_has_five_weighted_factors_and_version(self):
        with make_connection() as conn:
            result = score(conn, user())

        self.assertEqual(result["score_version"], 2)
        self.assertEqual(
            [(factor["key"], factor["max"]) for factor in result["factors"]],
            [("budget", 20), ("savings", 20), ("liquidity", 20), ("debt", 30), ("tracking", 10)],
        )
        self.assertEqual(sum(factor["points"] for factor in result["factors"]), result["raw_score"])

    def test_active_bot_score_breakdown_uses_only_v2_labels(self):
        source = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
        score_command = source.split("elif cmd == '/score':", 1)[1].split("elif cmd == '/scoreinfo':", 1)[0]
        for label in (
            "Budget / Cashflow",
            "Sparrate",
            "Liquidität",
            "Schuldenstruktur",
            "Tracking / Datenqualität",
        ):
            self.assertIn(label, score_command)
        self.assertNotIn("/25", score_command)
        self.assertIn("Er besteht aus 5 Bereichen:", source)
        self.assertNotIn("Er besteht aus 4 Bereichen:", source)

    def test_report_v2_dimensions_do_not_use_legacy_four_factor_fallback(self):
        v2_parts = {
            "score_version": 2,
            "factors": [
                {"key": "budget", "n": "Budget / Cashflow", "points": 12, "max": 20},
                {"key": "savings", "n": "Savings Rate", "points": 10, "max": 20},
                {"key": "liquidity", "n": "Liquidity", "points": 8, "max": 20},
                {"key": "debt", "n": "Debt Structure", "points": 24, "max": 30},
                {"key": "tracking", "n": "Tracking / Data Quality", "points": 6, "max": 10},
            ],
        }
        dimensions = score_dimensions(v2_parts)
        self.assertEqual(
            [(item["key"], item["max"]) for item in dimensions],
            [("budget", 20), ("savings", 20), ("liquidity", 20), ("debt", 30), ("tracking", 10)],
        )
        self.assertEqual([item["value"] for item in dimensions], [12, 10, 8, 24, 6])

        legacy_shape = {
            "score_version": 2,
            "factors": [
                {"key": "budget", "n": "Budget Control", "points": 20, "max": 25},
                {"key": "savings", "n": "Savings Execution", "points": 20, "max": 25},
                {"key": "consistency", "n": "Tracking Consistency", "points": 20, "max": 25},
                {"key": "structure", "n": "Financial Structure", "points": 20, "max": 25},
            ]
        }
        fallback = score_dimensions(legacy_shape)
        self.assertEqual(len(fallback), 5)
        self.assertEqual([item["max"] for item in fallback], [20, 20, 20, 30, 10])
        self.assertTrue(all(item["value"] is None for item in fallback))

    def test_active_pdf_template_uses_five_v2_dimensions(self):
        template = (Path(__file__).resolve().parent / "report_templates" / "rove_pdf_report.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("{% for part in score_parts %}", template)
        score_block = template.split("{% for part in score_parts %}", 1)[1].split(
            "Rov.E Points gesamt", 1
        )[0]
        self.assertNotIn("/25", score_block)
        self.assertIn("{{ part.label }}", score_block)
        self.assertIn("{{ part.max }}", score_block)

    def test_debt_status_migration_defaults_existing_users_to_unknown(self):
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO users VALUES (1)")
            ensure_debt_status_column(conn)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            self.assertIn("debt_status", columns)
            self.assertEqual(conn.execute("SELECT debt_status FROM users WHERE user_id=1").fetchone()[0], DEBT_STATUS_UNKNOWN)

    def test_unknown_debt_is_neutral_not_full_or_zero(self):
        with make_connection() as conn:
            unknown = score(conn, user(debt_status=DEBT_STATUS_UNKNOWN))
            declared_none = score(conn, user(debt_status=DEBT_STATUS_NONE))

        self.assertGreater(unknown["debt"], 0)
        self.assertLess(unknown["debt"], 30)
        self.assertEqual(declared_none["debt"], 30)

    def test_consumer_debt_create_and_delete_reconcile_persisted_status(self):
        with make_connection() as conn:
            self.assertEqual(conn.execute("SELECT debt_status FROM users WHERE user_id=1").fetchone()[0], DEBT_STATUS_UNKNOWN)
            debt_id = save_consumer_debt(
                conn,
                1,
                {
                    "name": "Kredit",
                    "debt_type": "personal_loan",
                    "outstanding_balance": 15000,
                    "active": True,
                },
                request_id="score-v2-status",
            )
            self.assertEqual(conn.execute("SELECT debt_status FROM users WHERE user_id=1").fetchone()[0], DEBT_STATUS_PRESENT)
            delete_consumer_debt(conn, 1, debt_id)
            self.assertEqual(conn.execute("SELECT debt_status FROM users WHERE user_id=1").fetchone()[0], DEBT_STATUS_UNKNOWN)

    def test_present_debt_without_rate_is_partial_and_materially_worse(self):
        with make_connection() as conn:
            conn.execute(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (1, 1, 'personal_loan', 15000)"
            )
            result = score(conn, user(debt_status=DEBT_STATUS_PRESENT))

        self.assertEqual(result["debt_status"], DEBT_STATUS_PRESENT)
        self.assertEqual(result["debt_confidence"], "partial")
        self.assertLess(result["debt"], 30)
        self.assertGreater(result["debt_penalty"], 0)

    def test_missing_debt_type_has_no_hard_consumer_penalty(self):
        with make_connection() as conn:
            conn.execute(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (1, 1, NULL, 15000)"
            )
            result = score(conn, user(debt_status=DEBT_STATUS_PRESENT))

        self.assertEqual(result["debt_confidence"], "unknown")
        self.assertGreaterEqual(result["debt"], 15)

    def test_savings_is_monotone_and_confirmed_savings_gets_priority(self):
        with make_connection() as conn:
            values = [score(conn, user(etf_savings=300 * ratio), 0)["savings"] for ratio in (0, .5, 1, 1.5, 2, 2.5)]
            conn.execute("CREATE TABLE app_month_closures (user_id INTEGER, month_key TEXT, actual_savings REAL)")
            conn.execute("INSERT INTO app_month_closures VALUES (1, '2026-09', 300)")
            confirmed = score(conn, user(etf_savings=0), 0)

        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(confirmed["savings"], values[2])

    def test_liquidity_is_monotone_in_months_of_fixed_costs(self):
        with make_connection() as conn:
            values = [score(conn, user(current_cash=1000 * months))["liquidity"] for months in (0, .5, 1, 2, 3, 6)]

        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 20)

    def test_mortgage_is_separate_and_moderate(self):
        with make_connection() as conn:
            conn.execute("INSERT INTO app_properties (user_id, market_value, remaining_debt, monthly_rate) VALUES (1, 300000, 250000, 900)")
            result = score(conn, user(debt_status=DEBT_STATUS_NONE, current_cash=3000, etf_savings=450), 0)

        self.assertGreater(result["debt"], 15)
        self.assertLess(result["debt"], 30)
        self.assertEqual(result["consumer_debt_total"], 0)

    def test_mortgage_rate_in_fixed_costs_is_not_penalized_twice(self):
        with make_connection() as conn:
            conn.execute(
                "INSERT INTO app_properties (user_id, market_value, remaining_debt, monthly_rate) VALUES (1, 300000, 250000, 900)"
            )
            add_property_rate_contract(conn)
            included = score(
                conn,
                user(debt_status=DEBT_STATUS_NONE, current_cash=6000, fixed_costs=1900),
            )

        with make_connection() as conn:
            conn.execute(
                "INSERT INTO app_properties (user_id, market_value, remaining_debt, monthly_rate) VALUES (1, 300000, 250000, 900)"
            )
            not_included = score(
                conn,
                user(debt_status=DEBT_STATUS_NONE, current_cash=6000, fixed_costs=1000),
            )

        self.assertTrue(included["mortgage_rate_in_fixed_costs"])
        self.assertEqual(included["mortgage_confidence"], "structure_only_rate_in_fixed_costs")
        self.assertLess(included["mortgage_penalty"], 9.0)
        self.assertEqual(included["spendable_budget"], 1100.0)
        self.assertAlmostEqual(included["liquidity_months"], 6000 / 1900)
        self.assertEqual(not_included["mortgage_penalty"], 9.0)

    def test_consumer_debt_is_clearly_worse_than_mortgage_structure(self):
        with make_connection() as conn:
            conn.execute(
                "INSERT INTO app_properties (user_id, market_value, remaining_debt, monthly_rate) VALUES (1, 300000, 250000, 900)"
            )
            add_property_rate_contract(conn)
            mortgage_only = score(
                conn,
                user(debt_status=DEBT_STATUS_NONE, current_cash=6000, fixed_costs=1900),
            )
            conn.execute(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (1, 1, 'personal_loan', 15000)"
            )
            combined = score(
                conn,
                user(debt_status=DEBT_STATUS_PRESENT, current_cash=6000, fixed_costs=1900),
            )

        self.assertGreater(mortgage_only["debt"], combined["debt"])
        self.assertGreater(mortgage_only["debt"] - combined["debt"], 5)
        self.assertEqual(mortgage_only["mortgage_penalty"], combined["mortgage_penalty"])

    def test_multiple_consumer_debts_are_worse_than_one_same_balance(self):
        with make_connection() as conn:
            conn.executemany(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (?, 1, ?, ?)",
                [(1, "personal_loan", 7500), (2, "bnpl", 7500)],
            )
            multiple = score(conn, user(debt_status=DEBT_STATUS_PRESENT))
            conn.execute("DELETE FROM app_consumer_debts WHERE id=2")
            single = score(conn, user(debt_status=DEBT_STATUS_PRESENT))

        self.assertLess(multiple["debt"], single["debt"])

    def test_debt_state_and_totals_are_scoped_to_user(self):
        with make_connection() as conn:
            conn.execute("INSERT INTO users (user_id, debt_status) VALUES (2, 'present')")
            conn.execute(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (1, 2, 'personal_loan', 12000)"
            )
            first_user = score(conn, user(debt_status=DEBT_STATUS_UNKNOWN))
            second_user = calculate_score(
                conn,
                2,
                user(debt_status=DEBT_STATUS_PRESENT),
                total_expenses=0,
                report_month="2026-09",
                today=TODAY,
            )

        self.assertEqual(first_user["consumer_debt_total"], 0.0)
        self.assertEqual(first_user["debt_status_effective"], DEBT_STATUS_UNKNOWN)
        self.assertEqual(second_user["consumer_debt_total"], 12000.0)
        self.assertEqual(second_user["debt_status_effective"], DEBT_STATUS_PRESENT)

    def test_mortgage_and_consumer_debt_are_scored_separately(self):
        with make_connection() as conn:
            conn.execute(
                "INSERT INTO app_properties (user_id, market_value, remaining_debt, monthly_rate) VALUES (1, 300000, 250000, 900)"
            )
            conn.execute(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (1, 1, 'personal_loan', 5000)"
            )
            result = score(conn, user(income=3000, debt_status=DEBT_STATUS_PRESENT))

        self.assertEqual(result["consumer_debt_total"], 5000.0)
        self.assertGreater(result["mortgage_penalty"], 0)
        self.assertEqual(result["mortgage_confidence"], "rate_known")
        self.assertLess(result["debt"], 30)

    def test_personas_land_in_requested_ranges(self):
        personas = []
        with make_connection() as conn:
            add_tracking_days(conn, 15)
            personas.append(score(conn, user(
                etf_savings=750, current_cash=6000, debt_status=DEBT_STATUS_NONE,
                goal_description="Sicherheitsreserve", goal_amount=10000,
            )))
        with make_connection() as conn:
            add_tracking_days(conn, 15)
            personas.append(score(conn, user(
                etf_savings=300, current_cash=1500,
                goal_description="Urlaub", goal_amount=5000,
            ), 0))
        with make_connection() as conn:
            add_tracking_days(conn, 30)
            conn.execute("INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (1, 1, 'personal_loan', 15000)")
            personas.append(score(conn, user(
                etf_savings=60, debt_status=DEBT_STATUS_PRESENT,
                goal_description="Neues Auto", goal_amount=15000,
            ), 2200))
        with make_connection() as conn:
            add_tracking_days(conn, 30)
            conn.execute("INSERT INTO app_properties (user_id, market_value, remaining_debt, monthly_rate) VALUES (1, 300000, 250000, 900)")
            add_property_rate_contract(conn)
            personas.append(score(conn, user(
                income=3500, fixed_costs=1500, etf_savings=525, current_cash=3000,
                debt_status=DEBT_STATUS_NONE, goal_description="Eigenheim", goal_amount=250000,
            )))
        with make_connection() as conn:
            add_tracking_days(conn, 30)
            conn.executemany(
                "INSERT INTO app_consumer_debts (id, user_id, debt_type, outstanding_balance) VALUES (?, 1, ?, ?)",
                [(1, "credit_card", 10000), (2, "bnpl", 5000)],
            )
            personas.append(score(conn, user(etf_savings=60, current_cash=1600, current_investments=100000, debt_status=DEBT_STATUS_PRESENT), 2000))

        for result, expected in zip(personas, ((85, 95), (60, 75), (25, 45), (65, 80), (35, 55))):
            with self.subTest(score=result["total"], expected=expected):
                self.assertGreaterEqual(result["total"], expected[0])
                self.assertLessEqual(result["total"], expected[1])


if __name__ == "__main__":
    unittest.main()
