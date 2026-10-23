"""Synthetic contract tests; never open a workspace DB or use the network."""

from __future__ import annotations

import copy
import socket
import sqlite3
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import rove_app_api as api
import rove_app_state as state
import test_financial_accounts_sprint3 as onboarding_fixtures
import test_onboarding_draft as cookie_fixtures
from rove_financial_accounts import ensure_initial_financial_accounts


ORIGIN = "https://getrove.de"
COLLECTION = "/v1/valuables"
IMPORT = COLLECTION + "/import"
OWNER_HEADER = "X-Rove-User-ID"
REAL_USER_FROM_TOKEN = api.user_from_token
REAL_PIN_STATE = api.pin_state
REAL_BUILD_STATE = state.build_live_app_data
INSERT_FAILURE = "synthetic_second_valuable_insert_failure"


def position(number=1, name="Camera", value=125.25):
    return {"id": f"{number:032x}", "n": name, "v": value}


def invalid_positions():
    for value in (0, -1, 0.001, 1.001, 10_000_000.01, True, False, "12.50",
                  None, [], {}, float("nan"), float("inf"), -float("inf")):
        yield position(value=value)
    for name in ("", "   ", "x" * 81, None, True, 123, [], {},
                 "a\x00b", "a\nb", "a\rb", "a\tb", "a\x1fb", "a\x7fb"):
        yield position(name=name)
    for identifier in ("", "a" * 31, "a" * 33, "g" * 32, True, 1, None, []):
        yield {**position(), "id": identifier}
    for key in ("id", "n", "v"):
        yield {k: v for k, v in position().items() if k != key}
    yield {**position(), "user_id": 2}
    yield {**position(), "extra": "not allowed"}


class SyntheticCookieCase(unittest.TestCase):
    """Compose fixture helpers, without inheriting their existing test methods."""

    unlock_pin = True

    def setUp(self):
        self.fixtures = []
        real_connect = sqlite3.connect

        def synthetic_connect(database, *args, **kwargs):
            raw = str(database)
            target = Path(unquote(urlsplit(raw).path) if raw.startswith("file:") else raw).resolve()
            roots = [Path(f.temp.name).resolve() for f in self.fixtures if hasattr(f, "temp")]
            if not any(target.is_relative_to(root) for root in roots):
                raise AssertionError(f"Non-synthetic database access forbidden: {target}")
            return real_connect(database, *args, **kwargs)

        for guard in (
            patch.object(sqlite3, "connect", side_effect=synthetic_connect),
            patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
            patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")),
            patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Network forbidden")),
            patch.dict(api.app.config, {"TESTING": True}),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        self.cookies = self.use_fixture(cookie_fixtures.OnboardingDraftApiTests)
        self.db_path = self.cookies.db_path
        if self.unlock_pin:
            self.start_patch(patch.object(api, "pin_state", return_value="unlocked"))

    def use_fixture(self, fixture_type):
        fixture = fixture_type(methodName="runTest")
        self.fixtures.append(fixture)

        def cleanup():
            if hasattr(fixture, "patchers"):
                fixture.tearDown()
            elif hasattr(fixture, "temp"):
                fixture.temp.cleanup()
            fixture.doCleanups()

        self.addCleanup(cleanup)
        fixture.setUp()
        return fixture

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def request(self, method="GET", path=COLLECTION, payload=None, *, user=1, client=None,
                origin=ORIGIN, headers=None):
        client = client if client is not None else self.cookies.client(user)
        request_headers = {"Origin": origin}
        if user is not None:
            request_headers[OWNER_HEADER] = str(user)
        for key, value in (headers or {}).items():
            if value is None:
                request_headers.pop(key, None)
            else:
                request_headers[key] = value
        return client.open(path, method=method, json=payload,
                           headers=request_headers)

    def assert_ok(self, response):
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIs(response.get_json()["ok"], True)
        return response.get_json()

    def assert_list(self, response, expected, *, user=1):
        body = self.assert_ok(response)
        self.assertEqual(set(body), {"ok", "user_id", "valuables"})
        self.assertIs(type(body["user_id"]), int)
        self.assertEqual(body["user_id"], user)
        self.assertCountEqual(body["valuables"], expected)
        for item in body["valuables"]:
            self.assertEqual(set(item), {"id", "n", "v"})
            self.assertRegex(item["id"], r"^[0-9a-f]{32}$")
        return body["valuables"]

    def assert_account_changed(self, response):
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json(), {"ok": False, "error": "account_changed"})

    def create(self, item=None, *, user=1):
        item = position() if item is None else item
        body = self.assert_ok(self.request("POST", payload=item, user=user))
        self.assertEqual(body["user_id"], user)
        return item

    def import_rows(self, positions, *, user=1):
        return self.request("POST", IMPORT, {"confirmed": True, "positions": positions}, user=user)

    def seed_imports(self, count):
        rows = []
        for start in range(0, count, 50):
            stop = min(start + 50, count)
            batch = [{"n": f"Item {i}", "v": i + 1} for i in range(start, stop)]
            rows = self.assert_ok(self.import_rows(batch))["valuables"]
            self.assertEqual(len(rows), stop)
        return rows

    def snapshot(self, *, user=None, financial_only=False):
        """Ignore permitted auth activity and empty additive tables, not business rows."""
        result = {}
        with closing(self.connect()) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            for table in tables:
                if table in {"app_sessions", "app_session_pins"}:
                    continue
                if financial_only and "valuable" in table:
                    continue
                escaped = table.replace('"', '""')
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{escaped}")')}
                if user is not None and "user_id" not in columns:
                    continue
                query = f'SELECT * FROM "{escaped}"'
                rows = conn.execute(query + " WHERE user_id=?", (user,)).fetchall() if user is not None else conn.execute(query).fetchall()
                if rows:
                    result[table] = sorted((tuple(row) for row in rows), key=repr)
        return result

    def fail_second_insert(self):
        with closing(self.connect()) as conn:
            api.ensure_valuables_schema(conn)
            conn.execute(f"""CREATE TRIGGER synthetic_insert_failure BEFORE INSERT ON app_valuables
                WHEN (SELECT COUNT(*) FROM app_valuables WHERE user_id=NEW.user_id) >= 1
                BEGIN SELECT RAISE(ABORT, '{INSERT_FAILURE}'); END""")
            conn.commit()

    def assert_insert_failure(self, operation):
        with patch.object(api, "create_valuable", wraps=api.create_valuable) as inserts:
            try:
                response = operation()
            except sqlite3.DatabaseError as exc:
                self.assertIn(INSERT_FAILURE, str(exc))
            else:
                self.assertGreaterEqual(response.status_code, 400, response.get_json())
            self.assertEqual(inserts.call_count, 2, "Must reach the second real model insert")


class ValuablesApiTests(SyntheticCookieCase):
    def test_get_without_valuables_schema_returns_empty_list(self):
        with closing(self.connect()) as conn:
            self.assertFalse(api.table_exists(conn, "app_valuables"))
        self.assert_list(self.request(), [])

    def test_crud_returns_persisted_list_across_separate_clients_with_same_cookie(self):
        device_a, device_b = self.cookies.client(1), self.cookies.client(1)
        first, second = position(), position(2, "Bicycle", 999.99)
        self.assert_list(self.request("POST", payload=first, client=device_a), [first])
        self.assert_list(self.request(client=device_b), [first])
        response = self.request("POST", payload=second, client=device_b)
        self.assert_list(response, [first, second])
        self.assertEqual(response.get_json(), self.request(client=device_a).get_json())
        changed = {**first, "n": "Camera revised", "v": 200.01}
        response = self.request("PATCH", COLLECTION + "/" + first["id"], changed, client=device_a)
        self.assert_list(response, [changed, second])
        self.assertEqual(response.get_json(), self.request(client=device_b).get_json())
        response = self.request("DELETE", COLLECTION + "/" + first["id"], client=device_b)
        self.assert_list(response, [second])
        self.assertEqual(response.get_json(), self.request(client=device_a).get_json())

    def test_foreign_ids_are_404_and_other_account_rows_are_never_returned_or_changed(self):
        own, foreign = self.create(), self.create(position(2, "Other owner", 9000), user=2)
        before = self.snapshot(user=2)
        for method in ("PATCH", "DELETE"):
            with self.subTest(method=method):
                response = self.request(method, COLLECTION + "/" + foreign["id"],
                                        {**foreign, "v": 1} if method == "PATCH" else None)
                self.assertEqual(response.status_code, 404, response.get_json())
        self.assert_list(self.request(), [own])
        self.assert_list(self.request(user=2), [foreign], user=2)
        self.assertEqual(self.snapshot(user=2), before)

    def test_missing_object_ids_are_404(self):
        for method in ("PATCH", "DELETE"):
            with self.subTest(method=method):
                response = self.request(method, COLLECTION + "/" + position()["id"],
                                        position() if method == "PATCH" else None)
                self.assertEqual(response.status_code, 404, response.get_json())

    def test_owned_delete_replay_is_200_but_foreign_receipt_is_not_acknowledged(self):
        original = self.create()
        path = COLLECTION + "/" + original["id"]
        first = self.request("DELETE", path)
        self.assert_list(first, [])
        before = self.snapshot()
        replay = self.request("DELETE", path, client=self.cookies.client(1))
        self.assert_list(replay, [])
        self.assertEqual(replay.get_json(), first.get_json())
        self.assertEqual(self.snapshot(), before)
        foreign = self.request("DELETE", path, user=2)
        self.assertEqual(foreign.status_code, 404, foreign.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(user=2), [], user=2)

    def test_patch_requires_body_id_matching_path(self):
        original = self.create()
        before = self.snapshot()
        response = self.request("PATCH", COLLECTION + "/" + original["id"], position(2))
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(), [original])

    def test_create_replay_is_idempotent_but_changed_same_id_conflicts(self):
        original = self.create()
        before = self.snapshot()
        self.assert_list(self.request("POST", payload=original), [original])
        self.assertEqual(self.snapshot(), before)
        for change in ({"n": "Changed"}, {"v": 2}):
            with self.subTest(change=change):
                response = self.request("POST", payload={**original, **change})
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.get_json()["error"], "valuable_request_conflict")
                self.assertEqual(self.snapshot(), before)

    def test_invalid_create_payloads_reject_without_any_business_mutation(self):
        self.create(position(99, "Preserved", 77))
        before = self.snapshot()
        for payload in [*invalid_positions(), None, [], "text", 42, True]:
            with self.subTest(payload=repr(payload)):
                response = self.request("POST", payload=payload)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(self.snapshot(), before)

    def test_invalid_patch_payloads_reject_without_any_business_mutation(self):
        original = self.create()
        before = self.snapshot()
        for payload in [*invalid_positions(), None, [], "text", True]:
            with self.subTest(payload=repr(payload)):
                response = self.request("PATCH", COLLECTION + "/" + original["id"], payload)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(self.snapshot(), before)

    def test_name_and_money_boundaries_are_accepted_without_rounding_or_coercion(self):
        expected = []
        for number, name, value in ((1, "X", 0.01), (2, "x" * 80, 10_000_000),
                                    (3, "<script>alert(1)</script>", 12.34)):
            expected.append(position(number, name, value))
            self.assert_list(self.request("POST", payload=expected[-1]), expected)

    def test_live_limit_is_500_and_delete_frees_capacity(self):
        rows = self.seed_imports(499)
        last = position(100, "Five hundredth", 1)
        self.assert_list(self.request("POST", payload=last), rows + [last])
        before = self.snapshot()
        response = self.request("POST", payload=position(101))
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request("PATCH", COLLECTION + "/" + last["id"], last), rows + [last])
        self.assert_ok(self.request("DELETE", COLLECTION + "/" + last["id"]))
        self.assert_list(self.request("POST", payload=position(101)), rows + [position(101)])

    def operations(self):
        return (("GET", COLLECTION, None), ("POST", COLLECTION, position(2)),
                ("PATCH", COLLECTION + "/" + position()["id"], position()),
                ("DELETE", COLLECTION + "/" + position()["id"], None),
                ("DELETE", COLLECTION, None),
                ("POST", IMPORT, {"confirmed": True, "positions": [{"n": "Imported", "v": 1}]}))

    def test_cross_tab_cookie_switch_rejects_every_write_with_stale_displayed_owner(self):
        self.create()
        self.create(position(name="Other owner", value=999), user=2)
        switched_client = self.cookies.client(2)
        before = self.snapshot()
        for method, path, payload in self.operations():
            if method == "GET":
                continue
            with self.subTest(method=method, path=path):
                response = self.request(method, path, payload, user=1, client=switched_client)
                self.assert_account_changed(response)
                self.assertEqual(self.snapshot(), before)
        self.assert_account_changed(self.request("POST", payload=position(3), user=2,
                                                headers={OWNER_HEADER: "1"}))
        self.assertEqual(self.snapshot(), before)

    def test_missing_owner_header_rejects_every_valuables_write(self):
        self.create()
        before = self.snapshot()
        for method, path, payload in self.operations():
            if method == "GET":
                continue
            with self.subTest(method=method, path=path):
                self.assert_account_changed(self.request(method, path, payload, headers={OWNER_HEADER: None}))
                self.assertEqual(self.snapshot(), before)

    def test_get_returns_real_cookie_owner_even_when_displayed_owner_is_stale_or_absent(self):
        self.create()
        other = self.create(position(name="Other owner", value=999), user=2)
        switched_client = self.cookies.client(2)
        self.assert_list(self.request(user=1, client=switched_client), [other], user=2)
        self.assert_list(self.request(user=2, headers={OWNER_HEADER: None}), [other], user=2)

    def test_owner_header_cors_preflight_is_allowed_only_on_new_owner_bound_paths(self):
        before = self.snapshot()
        paths = ((COLLECTION, "POST"), (COLLECTION, "DELETE"), (IMPORT, "POST"),
                 (COLLECTION + "/" + position()["id"], "PATCH"),
                 ("/v1/onboarding/draft", "POST"), ("/v1/onboarding", "POST"))
        for path, method in paths:
            with self.subTest(path=path, method=method):
                response = self.request("OPTIONS", path, user=None, headers={
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": OWNER_HEADER + ", Content-Type",
                })
                self.assertIn(response.status_code, (200, 204))
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
                self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")
                allowed_headers = {value.strip().lower() for value in response.headers["Access-Control-Allow-Headers"].split(",")}
                self.assertIn(OWNER_HEADER.lower(), allowed_headers)
                self.assertIn("content-type", allowed_headers)
                self.assertIn(method, {value.strip() for value in response.headers["Access-Control-Allow-Methods"].split(",")})
        for path in ("/v1/auth/request-code", "/v1/auth/verify-code", "/v1/auth/me"):
            with self.subTest(legacy_auth_path=path):
                response = self.request("OPTIONS", path, user=None)
                self.assertIn(response.status_code, (200, 204))
                self.assertEqual(response.headers["Access-Control-Allow-Headers"], "Content-Type, Authorization")
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_and_legacy_bearer_only_requests_are_401(self):
        self.create()
        before = self.snapshot()
        for method, path, payload in self.operations():
            with self.subTest(method=method, path=path):
                response = self.request(method, path, payload, user=None,
                                        headers={"Authorization": "Bearer pilot-token"})
                self.assertEqual(response.status_code, 401, response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_real_revoked_and_expired_cookie_sessions_are_401(self):
        for update in ("revoked_at=CURRENT_TIMESTAMP", "expires_at='2000-01-01 00:00:00'"):
            with self.subTest(update=update), closing(self.connect()) as conn:
                conn.execute("UPDATE app_sessions SET revoked_at=NULL,expires_at='2099-01-01 00:00:00'")
                conn.execute(f"UPDATE app_sessions SET {update} WHERE token_hash=?",
                             (api.keyed_hash(self.cookies.tokens[1]),))
                conn.commit()
                self.assertEqual(self.request().status_code, 401)
                self.assertEqual(self.request("POST", payload=position()).status_code, 401)

    def test_untrusted_origin_is_403_for_every_write(self):
        original = self.create()
        before = self.snapshot()
        for method, path, payload in self.operations():
            if method == "GET":
                continue
            with self.subTest(method=method, path=path):
                response = self.request(method, path, payload, origin="https://attacker.example")
                self.assertEqual(response.status_code, 403, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(), [original])

    def test_actual_locked_pin_blocks_all_routes_without_bypassing_the_guard(self):
        original = self.create()
        with closing(self.connect()) as conn:
            api.ensure_session_pin_table(conn)
            session_id = conn.execute("SELECT id FROM app_sessions WHERE token_hash=?",
                                      (api.keyed_hash(self.cookies.tokens[1]),)).fetchone()[0]
            conn.execute("INSERT INTO app_session_pins(session_id,pin_verifier) VALUES(?,'synthetic-verifier')",
                         (session_id,))
            conn.commit()
        before = self.snapshot()
        with patch.object(api, "pin_state", REAL_PIN_STATE):
            for method, path, payload in self.operations():
                with self.subTest(method=method, path=path):
                    response = self.request(method, path, payload)
                    self.assertEqual(response.status_code, 423, response.get_json())
                    self.assertEqual(response.get_json()["pin_status"], "locked")
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(), [original])

    def test_cookie_account_wins_over_foreign_bearer_and_client_user_id_is_rejected(self):
        own = self.create()
        foreign = self.create(position(2), user=2)
        self.assert_list(self.request(headers={"Authorization": "Bearer other-token"}), [own])
        before = self.snapshot()
        response = self.request("POST", payload={**position(3), "user_id": 2})
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(user=2), [foreign], user=2)


class ValuablesImportTests(SyntheticCookieCase):
    def test_import_normalizes_and_uses_stable_ids_per_value_and_occurrence(self):
        first = [{"n": " Camera ", "v": 100}, {"n": "Camera", "v": 100.0},
                 {"n": "Camera", "v": 200}]
        rows = self.assert_ok(self.import_rows(first))["valuables"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["id"] for r in rows}), 3)
        self.assertCountEqual([(r["n"], r["v"]) for r in rows], [("Camera", 100), ("Camera", 100), ("Camera", 200)])
        normalized = [{"n": "Camera", "v": 200.0}, {"n": "Camera", "v": 100.0}, {"n": "Camera", "v": 100}]
        before = self.snapshot()
        self.assert_list(self.import_rows(normalized), rows)
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(client=self.cookies.client(1)), rows)
        self.assert_list(self.request(user=2), [], user=2)

    def test_deleted_import_occurrence_does_not_resurrect_on_another_device(self):
        batch = [{"n": "Ring", "v": 250}, {"n": "Ring", "v": 250}]
        rows = self.assert_ok(self.import_rows(batch))["valuables"]
        removed = rows[0]
        remaining = [r for r in rows if r["id"] != removed["id"]]
        self.assert_list(self.request("DELETE", COLLECTION + "/" + removed["id"]), remaining)
        before = self.snapshot()
        self.assert_list(self.import_rows(batch), remaining)
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(client=self.cookies.client(1)), remaining)

    def test_edited_import_is_not_overwritten_by_legacy_replay(self):
        batch = [{"n": "Camera", "v": 100}]
        row = self.assert_ok(self.import_rows(batch))["valuables"][0]
        changed = {**row, "n": "Updated Camera", "v": 50}
        self.assert_list(self.request("PATCH", COLLECTION + "/" + row["id"], changed), [changed])
        self.assert_list(self.import_rows(batch), [changed])

    def test_import_rejects_wrong_shape_confirmation_supplied_ids_and_invalid_rows(self):
        valid = {"n": "Preserved", "v": 2}
        self.assert_ok(self.import_rows([valid]))
        before = self.snapshot()
        bad = [None, [], {}, {"positions": [valid]}, {"confirmed": True},
               {"confirmed": True, "positions": []},
               {"confirmed": True, "positions": {}},
               {"confirmed": True, "positions": [valid], "user_id": 2}]
        bad += [{"confirmed": confirmation, "positions": [valid]} for confirmation in (False, 1, "true", None)]
        bad += [{"confirmed": True, "positions": [valid, item]} for item in
                (position(), {**valid, "user_id": 2}, {"n": "Invalid", "v": True},
                 {"n": "Invalid", "v": "1"}, {"n": "Invalid", "v": 1.001},
                 {"n": "Bad\nname", "v": 1}, {"n": "Invalid", "v": float("inf")},
                 {"n": "Invalid", "v": 0}, {"n": "Invalid"}, None)]
        for payload in bad:
            with self.subTest(payload=repr(payload)):
                response = self.request("POST", IMPORT, payload)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(self.snapshot(), before)

    def test_late_invalid_row_rolls_back_earlier_valid_row_and_receipt(self):
        before = self.snapshot()
        response = self.import_rows([{"n": "Must roll back", "v": 5}, {"n": "Invalid", "v": -1}])
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(), [])
        rows = self.assert_ok(self.import_rows([{"n": "Must roll back", "v": 5}]))["valuables"]
        self.assertEqual(len(rows), 1)

    def test_legacy_import_accepts_51_and_500_positions_in_one_request(self):
        for user, count in ((1, 51), (2, 500)):
            with self.subTest(user=user, count=count):
                batch = [{"n": f"Legacy {i:03d} ".ljust(80, "x"), "v": i + 1} for i in range(count)]
                if count == 500:
                    self.assertGreater(sum(len(item["n"]) for item in batch), 32 * 1024)
                other_before = self.snapshot(user=3 - user)
                response = self.import_rows(batch, user=user)
                rows = self.assert_ok(response)["valuables"]
                self.assertEqual(len(rows), count)
                self.assertEqual(len({item["id"] for item in rows}), count)
                self.assertCountEqual([{"n": item["n"], "v": item["v"]} for item in rows], batch)
                self.assert_list(response, rows, user=user)
                self.assert_list(self.request(user=user), rows, user=user)
                before_replay = self.snapshot()
                self.assert_list(self.import_rows(batch, user=user), rows, user=user)
                self.assertEqual(self.snapshot(), before_replay)
                self.assertEqual(self.snapshot(user=3 - user), other_before)

    def test_import_payload_over_128_kib_is_rejected_without_mutation(self):
        before = self.snapshot()
        payload = '{"confirmed":true,"positions":[{"n":"Camera","v":1}]}' + " " * (128 * 1024)
        response = self.cookies.client(1).post(
            IMPORT, data=payload, content_type="application/json", headers={"Origin": ORIGIN, OWNER_HEADER: "1"},
        )
        self.assertEqual(response.status_code, 413, response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_import_batch_limit_500_and_live_capacity_overflow_are_atomic(self):
        before = self.snapshot()
        too_many = [{"n": f"Item {i}", "v": 1} for i in range(501)]
        self.assertEqual(self.import_rows(too_many).status_code, 400)
        self.assertEqual(self.snapshot(), before)
        rows = self.seed_imports(499)
        before = self.snapshot()
        self.assertEqual(self.import_rows([{"n": "New 500", "v": 1}, {"n": "New 501", "v": 1}]).status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(), rows)
        rows = self.assert_ok(self.import_rows([{"n": "New 500", "v": 1}]))["valuables"]
        self.assertEqual(len(rows), 500)
        before = self.snapshot()
        replay = [{"n": f"Item {i}", "v": i + 1} for i in range(50)]
        self.assert_list(self.import_rows(replay), rows)
        self.assertEqual(self.snapshot(), before)

    def test_real_second_insert_failure_rolls_back_import_and_can_retry(self):
        batch = [{"n": "First", "v": 1}, {"n": "Second", "v": 2}]
        self.fail_second_insert()
        before = self.snapshot()
        self.assert_insert_failure(lambda: self.import_rows(batch))
        self.assertEqual(self.snapshot(), before)
        self.assert_list(self.request(), [])
        with closing(self.connect()) as conn:
            conn.execute("DROP TRIGGER synthetic_insert_failure")
            conn.commit()
        self.assertEqual(len(self.assert_ok(self.import_rows(batch))["valuables"]), 2)

    def test_bulk_delete_is_owner_scoped_and_keeps_import_replay_receipts(self):
        batch = [{"n": "Ring", "v": 500}, {"n": "Painting", "v": 100}]
        self.assert_ok(self.import_rows(batch))
        self.create(position(33, "Manual", 9))
        other_rows = self.assert_ok(self.import_rows(batch, user=2))["valuables"]
        other_before = self.snapshot(user=2)
        with closing(self.connect()) as conn:
            receipt_tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%valuable%' AND name != 'app_valuables'"
            )]
            self.assertTrue(receipt_tables, "Import replay protection must be persisted")
            receipts_before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}"')] for t in receipt_tables}
        self.assert_list(self.request("DELETE"), [])
        self.assert_list(self.request(client=self.cookies.client(1)), [])
        self.assertEqual(self.snapshot(user=2), other_before)
        self.assert_list(self.request(user=2), other_rows, user=2)
        with closing(self.connect()) as conn:
            for table, expected in receipts_before.items():
                self.assertEqual([tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')], expected)
        self.assert_list(self.import_rows(batch), [])
        self.assert_list(self.request("DELETE"), [])


class ValuablesDraftTests(SyntheticCookieCase):
    unlock_pin = False

    def save(self, draft, *, headers=None):
        return self.request("POST", "/v1/onboarding/draft", {"step": 5, "draft": draft}, headers=headers)

    def test_old_exact_draft_round_trips_unchanged(self):
        draft = self.cookies.draft()
        saved = self.assert_ok(self.save(draft, headers={OWNER_HEADER: None}))
        self.assertEqual(saved["user_id"], 1)
        resumed = self.assert_ok(self.request(path="/v1/onboarding/draft"))
        self.assertEqual(resumed["user_id"], 1)
        self.assertEqual(resumed["draft"], draft)
        self.assertNotIn("valuables", resumed["draft"])
        self.assertNotIn("completionId", resumed["draft"])

    def test_optional_valuables_and_completion_id_round_trip_independently_and_together(self):
        for extra in ({"valuables": []}, {"valuables": [position()]},
                      {"completionId": "a" * 32}, {"valuables": [position()], "completionId": "b" * 32}):
            with self.subTest(extra=extra):
                draft = self.cookies.draft(**extra)
                self.assert_ok(self.save(draft))
                resumed = self.assert_ok(self.request(path="/v1/onboarding/draft", client=self.cookies.client(1)))
                self.assertEqual(resumed["user_id"], 1)
                self.assertEqual(resumed["draft"], draft)
                self.assertEqual(resumed["step"], 5)
                self.assertIsNone(self.assert_ok(self.request(path="/v1/onboarding/draft", user=2))["draft"])
        with closing(self.connect()) as conn:
            self.assertEqual(api.list_valuables(conn, 1), [])

    def test_empty_draft_get_bootstraps_real_owner_without_header(self):
        for user in (1, 2):
            with self.subTest(user=user):
                response = self.request(path="/v1/onboarding/draft", user=user, headers={OWNER_HEADER: None})
                self.assertEqual(self.assert_ok(response), {"ok": True, "user_id": user, "step": 0, "draft": None})

    def test_extended_drafts_reject_missing_header_and_cross_tab_cookie_switch(self):
        self.assert_ok(self.save(self.cookies.draft(valuables=[position()], completionId="a" * 32)))
        self.assert_ok(self.request("POST", "/v1/onboarding/draft",
                                    {"step": 3, "draft": self.cookies.draft(name="Other owner")}, user=2))
        before = self.snapshot()
        switched_client = self.cookies.client(2)
        for extra in ({"valuables": []}, {"valuables": [position()]},
                      {"completionId": "a" * 32}, {"valuables": [position()], "completionId": "a" * 32}):
            for missing in (False, True):
                with self.subTest(extra=extra, missing_header=missing):
                    response = self.request("POST", "/v1/onboarding/draft",
                                            {"step": 5, "draft": self.cookies.draft(**extra)},
                                            user=1, client=switched_client,
                                            headers={OWNER_HEADER: None} if missing else None)
                    self.assert_account_changed(response)
                    self.assertEqual(self.snapshot(), before)
        resumed = self.assert_ok(self.request(path="/v1/onboarding/draft", user=1, client=switched_client))
        self.assertEqual(resumed["user_id"], 2)
        self.assertEqual(resumed["draft"], self.cookies.draft(name="Other owner"))

    def test_invalid_extensions_leave_previous_draft_unchanged(self):
        original = self.cookies.draft(valuables=[position()], completionId="a" * 32)
        self.assert_ok(self.save(original))
        before = self.snapshot()
        bad = [{"valuables": [item]} for item in invalid_positions()]
        bad += [{"valuables": value} for value in (None, {}, "invalid", True, [position(i) for i in range(51)])]
        bad += [{"completionId": value} for value in (None, True, [], "", "a" * 31, "g" * 32)]
        bad.append({"user_id": 2})
        for extra in bad:
            with self.subTest(extra=repr(extra)):
                response = self.save(self.cookies.draft(**extra))
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.assert_ok(self.request(path="/v1/onboarding/draft"))["draft"], original)


class MoneyCookieCase(SyntheticCookieCase):
    def setUp(self):
        super().setUp()
        # The atomicity fixture uses sprint2.create_db's complete money schema.
        # Transfer the real cookie fixture's auth rows, not its minimal users table.
        money = self.use_fixture(onboarding_fixtures.OnboardingAtomicityTests)
        with closing(sqlite3.connect(self.cookies.db_path)) as source, closing(money.connect()) as target:
            target.execute("DELETE FROM app_accounts")
            for table in ("app_accounts", "app_sessions"):
                cursor = source.execute(f'SELECT * FROM "{table}"')
                columns = ",".join(f'"{d[0]}"' for d in cursor.description)
                placeholders = ",".join("?" for _ in cursor.description)
                target.executemany(f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', cursor.fetchall())
            target.commit()
        self.db_path = money.db_path
        self.money = money
        self.start_patch(patch.object(api, "user_from_token", REAL_USER_FROM_TOKEN))
        self.start_patch(patch.object(api, "build_live_app_data", REAL_BUILD_STATE))


class ValuablesOnboardingTests(MoneyCookieCase):
    unlock_pin = False

    def payload(self):
        payload = self.money.payload()
        payload.update(valuables=[position(), position(2, "Painting", 2500)], request_id="a" * 32)
        payload["wealth"]["etf"] = 2500
        payload["goals"] = [{"name": "Holiday", "target": 2000, "current": 100}]
        return payload

    def submit(self, payload, *, user=1, client=None, headers=None):
        response = self.request("POST", "/v1/onboarding", payload, user=user, client=client, headers=headers)
        if response.status_code == 200 and payload.get("request_id"):
            self.assertEqual(response.get_json()["user_id"], user)
        return response

    def rows(self, user=1):
        with closing(self.connect()) as conn:
            return api.list_valuables(conn, user)

    def save_draft(self):
        draft = self.cookies.draft(valuables=self.payload()["valuables"], completionId="a" * 32)
        self.assert_ok(self.request("POST", "/v1/onboarding/draft", {"step": 5, "draft": draft}))
        return draft

    def unlock_actual_pin(self):
        with closing(self.connect()) as conn:
            session_id = conn.execute("SELECT id FROM app_sessions WHERE token_hash=?",
                                      (api.keyed_hash(self.cookies.tokens[1]),)).fetchone()[0]
            conn.execute("""INSERT INTO app_session_pins(session_id,pin_verifier,unlocked_at,last_activity_at)
                VALUES(?,'synthetic-verifier',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""", (session_id,))
            conn.commit()

    def test_real_cookie_completion_retry_before_pin_setup_uses_committed_receipt(self):
        self.save_draft()
        payload = self.payload()
        first = self.assert_ok(self.submit(payload, client=self.cookies.client(1)))
        self.assertCountEqual(self.rows(), payload["valuables"])
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT onboarding_step FROM users WHERE user_id=1").fetchone()[0], 10)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_onboarding_drafts WHERE user_id=1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_session_pins").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT SUM(balance) FROM app_financial_accounts WHERE user_id=1").fetchone()[0], 7000)
            self.assertEqual(conn.execute("SELECT current_investments FROM users WHERE user_id=1").fetchone()[0], 2500)
        before = self.snapshot()
        retry = self.assert_ok(self.submit(copy.deepcopy(payload), client=self.cookies.client(1)))
        self.assertEqual(retry, first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.request().status_code, 423, "Receipt replay must not unlock financial endpoints")

    def test_empty_valuables_with_stable_request_id_also_replays_before_pin_setup(self):
        payload = {**self.money.payload(), "valuables": [], "request_id": "b" * 32}
        first = self.assert_ok(self.submit(payload))
        before = self.snapshot()
        self.assertEqual(self.assert_ok(self.submit(payload, client=self.cookies.client(1))), first)
        self.assertEqual(self.snapshot(), before)

    def test_committed_receipt_cannot_fast_ack_before_pin_without_matching_owner_header(self):
        payload = self.payload()
        self.assert_ok(self.submit(payload))
        before = self.snapshot()
        with patch.object(api, "claim_valuable_request", wraps=api.claim_valuable_request) as claims:
            for owner in (None, "2"):
                with self.subTest(owner_header=owner):
                    response = self.submit(payload, client=self.cookies.client(1), headers={OWNER_HEADER: owner})
                    self.assertEqual(response.status_code, 423, response.get_json())
                    self.assertEqual(response.get_json()["pin_status"], "setup_required")
                    self.assertEqual(self.snapshot(), before)
            claims.assert_not_called()
        self.assert_ok(self.submit(payload, client=self.cookies.client(1)))
        self.assertEqual(self.snapshot(), before)

    def test_changed_whole_payload_same_id_conflicts_after_pin_is_unlocked(self):
        payload = self.payload()
        self.assert_ok(self.submit(payload))
        self.unlock_actual_pin()
        changes = {"name": "Other", "income": 9999, "wealth": {**payload["wealth"], "giro": 1},
                   "contracts": [], "goals": [], "etf_plan": {**payload["etf_plan"], "execution_day": 15},
                   "valuables": [{**position(), "v": 1}]}
        before = self.snapshot()
        for key, value in changes.items():
            with self.subTest(changed=key):
                response = self.submit({**payload, key: value})
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.get_json()["error"], "onboarding_request_conflict")
                self.assertEqual(self.snapshot(), before)

    def test_completed_legacy_without_request_id_still_conflicts_on_second_call(self):
        payload = self.money.payload()
        first = self.assert_ok(self.submit(payload, headers={OWNER_HEADER: None}))
        self.assertEqual(first, {"ok": True, "onboardingRequired": False, "pinSetupRequired": True})
        self.unlock_actual_pin()
        before = self.snapshot()
        response = self.submit(payload, headers={OWNER_HEADER: None})
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.rows(), [])

    def test_legacy_empty_valuables_without_request_id_is_accepted_once(self):
        payload = {**self.money.payload(), "valuables": []}
        self.assert_ok(self.submit(payload, headers={OWNER_HEADER: None}))
        self.unlock_actual_pin()
        before = self.snapshot()
        response = self.submit(payload, headers={OWNER_HEADER: None})
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.rows(), [])

    def test_nonempty_valuables_require_request_id_without_partial_completion(self):
        payload = self.payload()
        payload.pop("request_id")
        before = self.snapshot()
        response = self.submit(payload)
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(response.get_json()["error"], "onboarding_request_id_required")
        self.assertEqual(self.snapshot(), before)
        self.money.assert_not_onboarded()

    def test_new_completion_rejects_missing_header_and_cross_tab_cookie_switch_before_claim(self):
        self.save_draft()
        self.assert_ok(self.request("POST", "/v1/onboarding/draft",
                                    {"step": 3, "draft": self.cookies.draft(name="Other owner")}, user=2))
        before = self.snapshot()
        with patch.object(api, "claim_valuable_request", wraps=api.claim_valuable_request) as claims:
            for items in (self.payload()["valuables"], []):
                for missing in (False, True):
                    with self.subTest(valuables=items, missing_header=missing):
                        response = self.submit({**self.payload(), "valuables": items},
                                               user=1, client=self.cookies.client(2),
                                               headers={OWNER_HEADER: None} if missing else None)
                        self.assert_account_changed(response)
                        self.assertEqual(self.snapshot(), before)
            claims.assert_not_called()
        self.money.assert_not_onboarded()

    def test_invalid_valuables_and_request_ids_leave_entire_completion_unchanged(self):
        self.save_draft()
        before = self.snapshot()
        bad = [{"valuables": [item]} for item in invalid_positions()]
        bad += [{"valuables": value} for value in (None, {}, True, "bad", [position(i) for i in range(51)])]
        bad += [{"request_id": value} for value in (None, True, [], {}, "", "a" * 31, "g" * 32)]
        for extra in bad:
            with self.subTest(extra=repr(extra)):
                response = self.submit({**self.payload(), **extra})
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(self.snapshot(), before)
        self.money.assert_not_onboarded()

    def test_model_insert_failure_rolls_back_step_draft_financial_rows_and_receipt_then_retries(self):
        draft = self.save_draft()
        payload = self.payload()
        self.fail_second_insert()
        before = self.snapshot()
        self.assert_insert_failure(lambda: self.submit(payload))
        self.assertEqual(self.snapshot(), before)
        self.money.assert_not_onboarded()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.assert_ok(self.request(path="/v1/onboarding/draft"))["draft"], draft)
        with closing(self.connect()) as conn:
            conn.execute("DROP TRIGGER synthetic_insert_failure")
            conn.commit()
        self.assert_ok(self.submit(payload))
        self.assertCountEqual(self.rows(), payload["valuables"])
        self.assert_ok(self.submit(payload, client=self.cookies.client(1)))

    def test_matching_receipt_does_not_bypass_an_actual_locked_pin(self):
        payload = self.payload()
        self.assert_ok(self.submit(payload))
        with closing(self.connect()) as conn:
            session_id = conn.execute("SELECT id FROM app_sessions WHERE token_hash=?",
                                      (api.keyed_hash(self.cookies.tokens[1]),)).fetchone()[0]
            conn.execute("INSERT INTO app_session_pins(session_id,pin_verifier) VALUES(?,'synthetic-verifier')", (session_id,))
            conn.commit()
        before = self.snapshot()
        response = self.submit(payload, client=self.cookies.client(1))
        self.assertEqual(response.status_code, 423, response.get_json())
        self.assertEqual(response.get_json()["pin_status"], "locked")
        self.assertEqual(self.snapshot(), before)

    def test_new_request_id_cannot_complete_an_already_completed_account(self):
        self.assert_ok(self.submit(self.payload()))
        self.unlock_actual_pin()
        before = self.snapshot()
        response = self.submit({**self.payload(), "request_id": "b" * 32})
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_no_pin_replay_allowance_rejects_changed_missing_and_foreign_receipts(self):
        payload = self.payload()
        self.assert_ok(self.submit(payload))
        foreign_payload = {**payload, "request_id": "f" * 32}
        with closing(self.connect()) as conn:
            api.claim_valuable_request(conn, 2, "onboarding:" + "f" * 32, foreign_payload)
            conn.execute("UPDATE users SET onboarding_step=10 WHERE user_id=2")
            conn.commit()
        before = self.snapshot()
        missing_id = {k: v for k, v in payload.items() if k != "request_id"}
        rejected = [(1, {**payload, "income": 9999}), (1, missing_id),
                    (1, {**payload, "request_id": "b" * 32}), (1, foreign_payload),
                    (2, payload)]
        with patch.object(api, "claim_valuable_request", wraps=api.claim_valuable_request) as claims:
            for user, changed in rejected:
                with self.subTest(user=user, payload=changed):
                    response = self.submit(changed, user=user, client=self.cookies.client(user))
                    self.assertEqual(response.status_code, 423, response.get_json())
                    self.assertEqual(response.get_json()["pin_status"], "setup_required")
                    self.assertEqual(self.snapshot(), before)
            claims.assert_not_called()

    def test_receipt_without_completed_user_cannot_acknowledge_or_complete_onboarding(self):
        payload = self.payload()
        with closing(self.connect()) as conn:
            api.claim_valuable_request(conn, 1, "onboarding:" + payload["request_id"], payload)
            conn.commit()
        before = self.snapshot()
        response = self.submit(payload)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["error"], "onboarding_request_conflict")
        self.assertEqual(self.snapshot(), before)
        self.money.assert_not_onboarded()

    def test_fresh_registration_verification_and_completion_use_real_issued_cookie(self):
        email = "fresh-valuables@example.test"
        with closing(self.connect()) as conn:
            # create_app_only_user needs these production profile columns, absent
            # from the older money fixture. Extend only the synthetic schema.
            conn.execute("ALTER TABLE user_access ADD COLUMN approved_at TEXT")
            conn.execute("ALTER TABLE user_access ADD COLUMN username TEXT")
            api.ensure_payday_column(conn)
            conn.execute("INSERT INTO app_invitations(email,expires_at) VALUES(?,datetime('now','localtime','+1 day'))", (email,))
            conn.commit()
        client = api.app.test_client()
        self.assertEqual(self.request(client=client).status_code, 401)
        with patch.object(api, "AUTH_BUCKETS", {}), patch.object(api, "send_login_email") as mailer:
            requested = self.request("POST", "/v1/auth/request-code",
                                     {"email": email, "registration": True}, client=client, headers={OWNER_HEADER: None})
            self.assert_ok(requested)
            mailer.assert_called_once()
            self.assertEqual(mailer.call_args.args[0], email)
            code = mailer.call_args.args[1]
            verified = self.request("POST", "/v1/auth/verify-code", {"email": email, "code": code},
                                    client=client, headers={OWNER_HEADER: None})
        verified_body = self.assert_ok(verified)
        self.assertIs(verified_body["new_account"], True)
        self.assertIs(verified_body["onboarding_required"], True)
        self.assertNotIn("state_url", verified_body)
        issued_cookie = client.get_cookie(api.SESSION_COOKIE_NAME, path="/app-api/")
        self.assertIsNotNone(issued_cookie)
        # Flask calls /v1 directly; nginx normally strips the /app-api prefix.
        client.set_cookie(api.SESSION_COOKIE_NAME, issued_cookie.value, domain="localhost", path="/")
        with closing(self.connect()) as conn:
            new_user = conn.execute("SELECT user_id FROM app_accounts WHERE email=?", (email,)).fetchone()[0]
            self.assertNotIn(new_user, (1, 2))
            self.assertEqual(conn.execute("SELECT onboarding_step FROM users WHERE user_id=?", (new_user,)).fetchone()[0], 0)
            self.assertEqual(api.list_valuables(conn, new_user), [])
            self.assertIsNotNone(conn.execute("SELECT consumed_at FROM app_invitations WHERE email=?", (email,)).fetchone()[0])
        other_before = self.snapshot(user=1)
        payload = self.payload()
        draft = self.cookies.draft(valuables=payload["valuables"], completionId=payload["request_id"])
        self.assert_ok(self.request("POST", "/v1/onboarding/draft", {"step": 5, "draft": draft}, user=new_user, client=client))
        first = self.assert_ok(self.submit(payload, user=new_user, client=client))
        retry_device = api.app.test_client()
        retry_device.set_cookie(api.SESSION_COOKIE_NAME, issued_cookie.value, domain="localhost", path="/")
        before_retry = self.snapshot()
        self.assertEqual(self.assert_ok(self.submit(payload, user=new_user, client=retry_device)), first)
        self.assertEqual(self.snapshot(), before_retry)
        self.assertEqual(self.snapshot(user=1), other_before)
        with closing(self.connect()) as conn:
            self.assertEqual(conn.execute("SELECT onboarding_step FROM users WHERE user_id=?", (new_user,)).fetchone()[0], 10)
            self.assertCountEqual(api.list_valuables(conn, new_user), payload["valuables"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_onboarding_drafts WHERE user_id=?", (new_user,)).fetchone()[0], 0)
        self.assertEqual(self.request(user=new_user, client=retry_device).status_code, 423)


class ValuablesFinancialTruthTests(MoneyCookieCase):
    def live(self, user=1):
        with closing(self.connect()) as conn:
            result = state.build_live_app_data(conn, user, activate_due_savings=False)
            conn.commit()
            return result

    def test_live_state_tracks_server_valuables_without_changing_money_or_history(self):
        with closing(self.connect()) as conn:
            api.begin_write(conn)
            # The onboarding fixture adds an empty two-column placeholder, not
            # a production holdings schema. Use sprint2's no-holdings baseline.
            conn.execute("DROP TABLE portfolio_holdings")
            ensure_initial_financial_accounts(conn, 1, {"giro": 1000, "tagesgeld": 200, "bargeld": 50})
            conn.execute("UPDATE users SET current_cash=1250,current_investments=500,onboarding_step=10 WHERE user_id=1")
            conn.execute("""INSERT INTO investment_events(user_id,amount,direction,asset_type,event_type,source,created_at)
                VALUES(1,100,'in','etf','market_valuation','synthetic',datetime('now','-2 day'))""")
            conn.commit()
        before = self.live()
        self.assertEqual(before["netWorth"], 1750)
        self.assertEqual(before["valuables"], [])
        self.assertGreater(len(set(before["series"]["1W"])), 1, "History must not be an empty/constant comparison")
        self.assertNotIn("server-valuables", [a.get("source") for a in before["assets"]])
        other_before = self.live(2)
        financial_before = self.snapshot(financial_only=True)

        def assert_truth(expected):
            live = self.live()
            self.assertCountEqual(live["valuables"], expected)
            for key in ("netWorth", "series", "histDates", "chartV2", "financialAccounts", "sts", "budgets"):
                self.assertEqual(live[key], before[key], key)
            assets = [a for a in live["assets"] if a.get("source") == "server-valuables"]
            self.assertEqual(len(assets), int(bool(expected)))
            if expected:
                self.assertEqual(assets[0]["assetKey"], "asset:valuables")
                self.assertEqual(assets[0]["name"], "Sachwerte")
                self.assertCountEqual(assets[0]["positions"], expected)
                self.assertEqual(assets[0]["value"], round(sum(r["v"] for r in expected), 2))
            self.assertEqual([a for a in live["assets"] if a.get("source") != "server-valuables"], before["assets"])
            self.assertEqual(self.snapshot(financial_only=True), financial_before)
            self.assertEqual(self.live(2)["valuables"], other_before["valuables"])
            self.assertEqual(self.live(2)["netWorth"], other_before["netWorth"])

        own = self.create()
        assert_truth([own])
        changed = {**own, "v": 9_999_999.99}
        self.assert_ok(self.request("PATCH", COLLECTION + "/" + own["id"], changed))
        assert_truth([changed])
        rows = self.assert_ok(self.import_rows([{"n": "Ring", "v": 777.77}]))["valuables"]
        assert_truth(rows)
        self.assert_ok(self.request("DELETE", COLLECTION + "/" + own["id"]))
        assert_truth([r for r in rows if r["id"] != own["id"]])
        self.assert_ok(self.request("DELETE"))
        assert_truth([])


class ValuablesAccountDeletionTests(MoneyCookieCase):
    def test_account_delete_api_passes_real_ledger_gate_and_removes_only_owned_rows_and_receipts(self):
        self.start_patch(patch.object(api, "pin_state", REAL_PIN_STATE))
        with closing(self.connect()) as conn:
            conn.execute("""INSERT INTO app_session_pins
                (session_id,pin_verifier,unlocked_at,last_activity_at)
                SELECT id,'synthetic-verifier',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM app_sessions""")
            conn.execute("""INSERT INTO app_account_delete_codes(user_id,code_hash,expires_at)
                VALUES(1,?,'2099-01-01 00:00:00')""", (api.keyed_hash("delete:1:123456"),))
            conn.commit()
        expected_other = []
        for user in (1, 2):
            rows = self.assert_ok(self.import_rows([{"n": "Ring", "v": 500}], user=user))["valuables"]
            manual = self.create(position(10 + user), user=user)
            if user == 2:
                expected_other = rows + [manual]
        with closing(self.connect()) as conn:
            for user in (1, 2):
                api.claim_valuable_request(conn, user, "onboarding:" + str(user) * 32, {"synthetic": user})
            conn.execute("CREATE TABLE synthetic_delete_audit(user_id INTEGER NOT NULL, detail TEXT)")
            conn.execute("INSERT INTO synthetic_delete_audit VALUES(1,'deleted'),(2,'kept')")
            conn.commit()
            for user in (1, 2):
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_valuables WHERE user_id=?", (user,)).fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_valuable_requests WHERE user_id=?", (user,)).fetchone()[0], 3)
            other_auth_query = """SELECT s.*,p.* FROM app_sessions s
                JOIN app_accounts a ON a.id=s.account_id
                JOIN app_session_pins p ON p.session_id=s.id WHERE a.user_id=2"""
            other_auth = [tuple(row) for row in conn.execute(other_auth_query)]
            self.assertTrue(other_auth)
        other_before = self.snapshot(user=2)
        cleanup = api.account_delete_cleanup
        ledger = Path(self.money.temp.name) / "ledger.jsonl"
        ledger.touch(mode=0o600)
        original_db = api.db
        attempted_deletes = []
        real_fsync = cleanup.os.fsync
        durable = False

        def fsync_ledger(fd):
            nonlocal durable
            real_fsync(fd)
            self.assertEqual(cleanup.read_delete_tombstones(ledger), {1})
            durable = True

        def guard_delete(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_DELETE:
                attempted_deletes.append(table)
                if not durable or cleanup.read_delete_tombstones(ledger) != {1}:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        @contextmanager
        def guarded_db(**kwargs):
            with original_db(**kwargs) as conn:
                conn.set_authorizer(guard_delete)
                yield conn

        payload = {"confirmation": "LOESCHEN", "code": "123456"}
        with patch.dict("os.environ", {"ROVE_ACCOUNT_DELETE_TOMBSTONES": str(ledger)}), \
             patch.object(api, "account_delete_cleanup_roots", return_value=(ledger.parent,)), \
             patch.object(api, "account_delete_cleanup_paths", return_value=[]), \
             patch.object(cleanup, "generated_report_paths", return_value=([], {})), \
             patch.object(api, "remove_deleted_account_files", return_value=[]), \
             patch.object(api, "retry_account_delete_file_cleanup"), \
             patch.object(cleanup, "record_delete_tombstone", wraps=cleanup.record_delete_tombstone) as record, \
             patch.object(cleanup.os, "fsync", side_effect=fsync_ledger) as fsync, \
             patch.object(api, "db", guarded_db):
            response = self.request("DELETE", "/v1/account", payload)
            self.assertEqual(self.assert_ok(response), {"ok": True, "deleted": True, "cleanupPending": False})
            record.assert_called_once_with(1)
            fsync.assert_called_once()
            self.assertEqual(cleanup.read_delete_tombstones(ledger), {1})
            for table in ("app_valuables", "app_valuable_requests", "synthetic_delete_audit", "app_accounts", "users"):
                self.assertIn(table, attempted_deletes)
            self.assertEqual(self.snapshot(user=1), {})
            self.assertEqual(self.snapshot(user=2), other_before)
            with closing(self.connect()) as conn:
                self.assertEqual([tuple(row) for row in conn.execute(other_auth_query)], other_auth)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(self.request().status_code, 401)
            self.assert_list(self.request(user=2), expected_other, user=2)
            committed_ledger = ledger.read_bytes()
            self.assertEqual(self.request("DELETE", "/v1/account", payload).status_code, 401)
            record.assert_called_once_with(1)
            self.assertEqual(ledger.read_bytes(), committed_ledger)

    def test_tombstone_removes_all_owned_model_tables_but_preserves_other_account(self):
        for user in (1, 2):
            self.assert_ok(self.import_rows([{"n": "Ring", "v": 500}], user=user))
            self.create(position(10 + user), user=user)
        with closing(self.connect()) as conn:
            api.claim_valuable_request(conn, 1, "onboarding:" + "a" * 32, {"synthetic": 1})
            api.claim_valuable_request(conn, 2, "onboarding:" + "b" * 32, {"synthetic": 2})
            # Dynamic deletion must cover future user_id tables without an allowlist.
            conn.execute("CREATE TABLE synthetic_agent_audit(user_id INTEGER NOT NULL, detail TEXT)")
            conn.execute("INSERT INTO synthetic_agent_audit VALUES(1,'deleted'),(2,'kept')")
            conn.commit()
        other_before = self.snapshot(user=2)
        owned_before = self.snapshot(user=1)
        self.assertIn("app_valuables", owned_before)
        self.assertTrue(any("valuable" in name and name != "app_valuables" for name in owned_before))
        with closing(self.connect()) as conn:
            api.begin_write(conn)
            api.delete_user_rows_for_tombstone(conn, 1)
            conn.commit()
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_sessions WHERE account_id NOT IN (SELECT id FROM app_accounts)").fetchone()[0], 0)
        self.assertEqual(self.snapshot(user=1), {})
        self.assertEqual(self.snapshot(user=2), other_before)
        self.assertEqual(self.request().status_code, 401)
        self.assertEqual(len(self.assert_ok(self.request(user=2))["valuables"]), 2)
        before_repeat = self.snapshot()
        with closing(self.connect()) as conn:
            api.begin_write(conn)
            api.delete_user_rows_for_tombstone(conn, 1)
            conn.commit()
        self.assertEqual(self.snapshot(), before_repeat)


if __name__ == "__main__":
    unittest.main()
