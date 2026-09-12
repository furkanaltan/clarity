import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import rove_app_api as api
from rove_feature_announcements import (
    claim_coach_announcement,
    ensure_feature_announcement_tables,
    get_feature_announcements_for_user,
    mark_feature_announcement,
)


FRONTEND_PATH = Path(os.environ.get(
    "ROVE_FRONTEND_PATH",
    Path(__file__).resolve().parent / "frontend" / "index.html",
))


class FeatureAnnouncementSprintThreeServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE app_accounts (
                id INTEGER PRIMARY KEY, user_id INTEGER, verified_at TEXT, created_at TEXT
            );
            CREATE TABLE portfolio_holdings (
                id INTEGER PRIMARY KEY, user_id INTEGER, instrument_type TEXT
            );
            CREATE TABLE report_jobs (
                id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT
            );
            INSERT INTO users VALUES (1);
            INSERT INTO app_accounts (user_id, verified_at, created_at)
            VALUES (1, CURRENT_TIMESTAMP, datetime('now', '-30 days'));
        """)
        ensure_feature_announcement_tables(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def insert(self, feature_id, *, priority="major", published_at="2026-08-27 12:00:00"):
        self.conn.execute(
            """INSERT INTO app_feature_announcements
                   (feature_id, title, short_message, priority, deep_link, published_at)
               VALUES (?, ?, ?, ?, 'talk', ?)""",
            (feature_id, feature_id, f"{feature_id} ist verfügbar.", priority, published_at),
        )
        self.conn.commit()

    def claim(self, *, finance_due=False):
        item = claim_coach_announcement(
            self.conn, 1, finance_action_due=finance_due
        )
        self.conn.commit()
        return item

    def test_schema_migrates_existing_state_table_rerunnably(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("""
                CREATE TABLE users (user_id INTEGER PRIMARY KEY);
                CREATE TABLE app_feature_announcements (feature_id TEXT PRIMARY KEY);
                CREATE TABLE app_feature_announcement_state (
                    user_id INTEGER NOT NULL, feature_id TEXT NOT NULL,
                    seen_at TEXT, opened_at TEXT, dismissed_at TEXT, completed_at TEXT,
                    created_at TEXT, updated_at TEXT,
                    PRIMARY KEY(user_id, feature_id)
                );
            """)
            ensure_feature_announcement_tables(conn)
            ensure_feature_announcement_tables(conn)
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(app_feature_announcement_state)"
            )}
        self.assertIn("coach_shown_at", columns)

    def test_major_is_claimed_once_and_persists_across_reload_and_devices(self):
        self.insert("major_once")
        first = self.claim()
        self.assertEqual(first["feature_id"], "major_once")
        self.assertTrue(first["state"]["coach_shown"])
        self.assertIsNone(self.claim())
        row = self.conn.execute(
            "SELECT coach_shown_at FROM app_feature_announcement_state"
        ).fetchone()
        self.assertTrue(row["coach_shown_at"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_feature_announcement_state"
        ).fetchone()[0], 1)

    def test_finance_due_blocks_claim_and_feature_can_appear_after_completion(self):
        self.insert("major_after_income")
        self.assertIsNone(self.claim(finance_due=True))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_feature_announcement_state"
        ).fetchone()[0], 0)
        self.assertEqual(self.claim()["feature_id"], "major_after_income")

    def test_minor_never_enters_coach(self):
        self.insert("minor_only", priority="minor")
        self.assertIsNone(self.claim())

    def test_security_precedes_newer_major_and_only_one_is_claimed(self):
        self.insert("major_new", priority="major", published_at="2026-08-27 14:00:00")
        self.insert("security_old", priority="security", published_at="2026-08-27 13:00:00")
        self.insert("major_other", priority="major", published_at="2026-08-27 15:00:00")
        self.assertEqual(self.claim()["feature_id"], "security_old")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_feature_announcement_state"
        ).fetchone()[0], 1)

    def test_seen_opened_dismissed_completed_and_coach_shown_are_ineligible(self):
        for action in ("seen", "opened", "dismissed", "completed", "coach_shown"):
            feature_id = f"blocked_{action}"
            self.insert(feature_id)
            self.assertTrue(mark_feature_announcement(self.conn, 1, feature_id, action))
            self.conn.commit()
        self.assertIsNone(self.claim())

    def test_smart_dismiss_isolated_for_ai_crypto_and_report(self):
        self.conn.executescript("""
            CREATE TABLE app_ai_conversation_messages (
                id INTEGER PRIMARY KEY, user_id INTEGER
            );
            INSERT INTO app_ai_conversation_messages (user_id) VALUES (1);
            INSERT INTO portfolio_holdings (user_id, instrument_type) VALUES (1, 'crypto');
            INSERT INTO report_jobs (user_id, status) VALUES (1, 'sent');
        """)
        for feature_id in ("rove_ai_v2", "crypto_v2", "report_v2"):
            self.insert(feature_id)
        self.assertIsNone(self.claim())
        archive = {
            item["feature_id"]: item
            for item in get_feature_announcements_for_user(self.conn, 1)["archive"]
        }
        self.assertTrue(all(item["state"]["completed"] for item in archive.values()))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM app_feature_announcement_state"
        ).fetchone()[0], 0)

    def test_state_endpoint_blocks_claim_when_monthly_action_is_due(self):
        source = inspect.getsource(api.current_app_state)
        self.assertIn("claim_coach_announcement", source)
        self.assertIn('finance_action_due=bool(state.get("monthlyCheckinDueCount", 0))', source)
        self.assertLess(source.index("build_live_app_data"), source.index("claim_coach_announcement"))


class FeatureAnnouncementSprintThreeFrontendTests(unittest.TestCase):
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

    def test_finance_action_precedes_server_selected_feature(self):
        mentor = self.function_source("mentorLine")
        self.assertLess(mentor.index("if(dueActions.length)"), mentor.index("coachAnnouncement"))
        self.assertIn('DATA.featureAnnouncements?.coach', mentor)

    def test_coach_copy_is_compact_and_uses_server_payload(self):
        mentor = self.function_source("mentorLine")
        self.assertIn('"Wichtige Information":"Neu in Rov.E"', mentor)
        self.assertIn("coachAnnouncement.short_message", mentor)
        self.assertIn("Ausprobieren", mentor)
        self.assertNotIn("priority===\"major\"", mentor)

    def test_coach_click_reuses_sprint_two_router(self):
        self.assertIn('openFeatureAnnouncement(r.dataset.featureId)', self.frontend)
        self.assertIn('openFeatureAnnouncement(mentorCard.dataset.featureId)', self.frontend)
        self.assertEqual(self.frontend.count("function openFeatureDeepLink("), 1)

    def test_local_open_clears_only_targeted_coach_item(self):
        local_state = self.function_source("updateAnnouncementLocalState")
        self.assertIn("coach?.feature_id===featureId", local_state)
        self.assertIn('DATA.featureAnnouncements.coach=null', local_state)
        self.assertNotIn("clearAnnouncements", local_state)

    def test_feature_pulse_is_once_and_respects_reduced_motion(self):
        self.assertIn(".mentor.feature-announcement", self.frontend)
        self.assertIn("animation:mentorFeatureIn 1.6s ease-out 1", self.frontend)
        self.assertIn(".mentor.monthly-due,.mentor.feature-announcement{animation:none}", self.frontend)

    def test_normal_mentor_candidates_remain_present(self):
        mentor = self.function_source("mentorLine")
        for marker in ("freeBudget()", "mentorBudgetAlert", "score.next_lever", "tracking_days_90"):
            with self.subTest(marker=marker):
                self.assertIn(marker, mentor)


if __name__ == "__main__":
    unittest.main()
