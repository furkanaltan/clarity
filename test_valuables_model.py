"""Isolated valuables-model tests; no app imports or production database access."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal, Inexact, Rounded, localcontext
from pathlib import Path

from rove_valuables import (
    claim_valuable_request,
    clean_valuable,
    clean_valuables,
    create_valuable,
    delete_valuable,
    ensure_valuables_schema,
    list_valuables,
    update_valuable,
    valuable_request_matches,
)


def valuable(number=1, name="Watch", value=123.45):
    return {"id": f"{number:032x}", "n": name, "v": value}


class ValuableValidationTests(unittest.TestCase):
    def test_clean_returns_exact_shape_float_and_does_not_mutate(self):
        item = valuable(name="  Cafe\u0301  ", value=12)
        original = dict(item)
        result = clean_valuable(item)
        self.assertEqual(result, valuable(name="Caf\u00e9", value=12.0))
        self.assertIs(type(result["v"]), float)
        self.assertEqual(item, original)
        self.assertIsNot(result, item)

    def test_item_requires_dictionary_and_exact_keys(self):
        for item in (None, [], (), "item", 1, True, {},
                     {"n": "Watch", "v": 1},
                     {"id": valuable()["id"], "n": "Watch"},
                     {**valuable(), "origin": "manual"},
                     {**valuable(), "extra": None},
                     {"id": valuable()["id"], "n": "Watch", "value": 1}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                clean_valuable(item)

    def test_id_is_stable_exact_lowercase_hex_not_generated_or_trimmed(self):
        position_id = "0123456789abcdef0123456789abcdef"
        self.assertEqual(clean_valuable({**valuable(), "id": position_id})["id"], position_id)
        for bad in (None, 1, True, b"a" * 32, "", "a" * 31, "a" * 33,
                    "A" * 32, "g" * 32, "-" * 32, " a" + "0" * 30,
                    "a" * 32 + "\n", "a" * 31 + "\x00", "\uff11" * 32):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clean_valuable({**valuable(), "id": bad})

    def test_name_nfc_unicode_and_trimmed_length_boundaries(self):
        cases = [
            (" x ", "x"),
            ("\u00a0Gr\u00fc\u00dfe \U0001f48d\u3000", "Gr\u00fc\u00dfe \U0001f48d"),
            ("e\u0301" * 80, "\u00e9" * 80),
            (" " + "x" * 80 + " ", "x" * 80),
            ("\u65e5\u672c\u8a9e", "\u65e5\u672c\u8a9e"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(clean_valuable(valuable(name=raw))["n"], expected)

    def test_name_rejects_wrong_types_empty_and_overlong(self):
        for bad in (None, 1, False, [], {}, b"Watch", "", "   ", "\u00a0", "x" * 81):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clean_valuable(valuable(name=bad))

    def test_name_rejects_controls_even_at_edges_and_unsafe_format_chars(self):
        for control in ("\x00", "\x01", "\x1f", "\x7f", "\x85", "\n", "\r", "\t",
                        "\u200b", "\u202e", "\ud800"):
            for raw in (control + "Watch", "Wa" + control + "tch", "Watch" + control):
                with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):
                    clean_valuable(valuable(name=raw))

    def test_numeric_valid_boundaries_are_not_rounded(self):
        for value in (0.01, 0.29, 1, 1.0, 1.23, 19.99, 9_999_999.99, 10_000_000):
            with self.subTest(value=value):
                result = clean_valuable(valuable(value=value))
                self.assertEqual(result["v"], float(value))
                self.assertIs(type(result["v"]), float)

    def test_numeric_rejects_types_nonfinite_bounds_and_extra_precision(self):
        for bad in (None, True, False, "1", "1.00", Decimal("1.00"), [], {},
                    complex(1, 0), float("nan"), float("inf"), float("-inf"),
                    0, -0.0, -1, 10_000_000.01, 10**100,
                    0.001, 0.009, 1.001, 1.005, 123.456, 0.1 + 0.2, 5e-324):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clean_valuable(valuable(value=bad))

    def test_numeric_precision_is_independent_of_global_decimal_context(self):
        with localcontext() as context:
            context.prec = 1
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            for value in (0.01, 0.29, 1.23, 9_999_999.99, 10_000_000):
                with self.subTest(value=value):
                    self.assertEqual(clean_valuable(valuable(value=value))["v"], float(value))
            for bad in (1.001, 1.005, 0.1 + 0.2):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    clean_valuable(valuable(value=bad))

    def test_batch_requires_list_not_other_iterables(self):
        for items in (None, {}, (), "", iter([valuable()]), {"item"}, 1, False):
            with self.subTest(items=items), self.assertRaises(ValueError):
                clean_valuables(items)

    def test_batch_allows_empty_and_fifty_items_without_mutating(self):
        self.assertEqual(clean_valuables([]), [])
        items = [valuable(i, " Watch ", 1) for i in range(50)]
        result = clean_valuables(items)
        self.assertEqual(len(result), 50)
        self.assertEqual([item["id"] for item in result], [item["id"] for item in items])
        self.assertTrue(all(item["n"] == "Watch" for item in result))
        self.assertTrue(all(item["n"] == " Watch " for item in items))
        self.assertIsNot(result, items)
        self.assertTrue(all(a is not b for a, b in zip(result, items)))

    def test_batch_rejects_fifty_one_invalid_items_and_all_duplicate_ids(self):
        batches = [
            [valuable(i) for i in range(51)],
            [valuable(), None],
            [valuable(), valuable()],
            [valuable(), valuable(name="Different", value=1)],
        ]
        for items in batches:
            with self.subTest(items=items), self.assertRaises(ValueError):
                clean_valuables(items)


class ValuableDatabaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rove-valuables-test-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "synthetic.sqlite"
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("""CREATE TABLE users (
            user_id INTEGER PRIMARY KEY, current_cash REAL, current_investments REAL,
            portfolio_balance REAL, fixed_costs_details TEXT
        )""")
        self.conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
            [(1, 1200.25, 3000.5, 4200.75, '{"valuable_legacy":123}'),
             (2, 900.0, 2000.0, 2900.0, '{}')],
        )
        for table in ("expenses", "investment_events", "monthly_reports", "app_cash_movements"):
            self.conn.execute(
                f"CREATE TABLE {table} (user_id INTEGER, amount REAL, payload TEXT)"
            )
            self.conn.execute(f"INSERT INTO {table} VALUES (1, 51.25, 'unchanged')")
        self.conn.commit()
        self.conn.execute("BEGIN")

    def prepare(self):
        ensure_valuables_schema(self.conn)

    def receipt_rows(self):
        return self.conn.execute(
            "SELECT user_id, request_id, payload_hash, created_at "
            "FROM app_valuable_requests ORDER BY user_id, request_id"
        ).fetchall()

    def seed_position(self, item, user_id=1, origin="legacy"):
        self.conn.execute(
            "INSERT INTO app_valuables(user_id,position_id,name,value_cents,origin) "
            "VALUES (?,?,?,?,?)",
            (user_id, item["id"], item["n"], int(Decimal(str(item["v"])) * 100), origin),
        )

    def seed_full_user(self):
        self.prepare()
        self.conn.executemany(
            "INSERT INTO app_valuables(user_id,position_id,name,value_cents,origin) "
            "VALUES (1,?,'Seed',100,'legacy')",
            [(f"{i:032x}",) for i in range(500)],
        )

    def snapshot_finances(self):
        return {
            table: self.conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("users", "expenses", "investment_events", "monthly_reports",
                          "app_cash_movements")
        }

    def test_schema_is_additive_idempotent_and_does_not_commit(self):
        before = self.snapshot_finances()
        tables_before = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.prepare()
        self.prepare()
        tables_after = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertEqual(tables_after - tables_before,
                         {"app_valuables", "app_valuable_requests"})
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(before, self.snapshot_finances())
        self.conn.rollback()
        self.assertEqual(list_valuables(self.conn, 1), [])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='app_valuable_requests'"
        ).fetchone())

    def test_schema_cent_storage_and_origin_constraints(self):
        self.prepare()
        for bad in (0, -1, 1_000_000_001, 1.5, "invalid", None):
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(
                    "INSERT INTO app_valuables VALUES (1,?,'Watch',?,'manual','x','x')",
                    (valuable()["id"], bad),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.seed_position(valuable(), origin="unknown")
        for i, value in enumerate((0.01, 0.29, 123.45, 10_000_000)):
            self.assertTrue(create_valuable(self.conn, 1, valuable(i, value=value)))
        rows = self.conn.execute(
            "SELECT value_cents,typeof(value_cents) FROM app_valuables ORDER BY position_id"
        ).fetchall()
        self.assertEqual(rows, [(1, "integer"), (29, "integer"), (12345, "integer"),
                                (1_000_000_000, "integer")])

    def test_primary_keys_are_composite_and_duplicate_rows_are_forbidden(self):
        self.prepare()
        self.seed_position(valuable(), user_id=1)
        self.seed_position(valuable(), user_id=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.seed_position(valuable(), user_id=1)
        self.assertTrue(claim_valuable_request(self.conn, 1, "same", {"a": 1}))
        self.assertTrue(claim_valuable_request(self.conn, 2, "same", {"a": 2}))

    def test_create_replay_canonical_normalization_and_receipt_only_hash(self):
        item = valuable(name=" Cafe\u0301 ", value=12)
        self.assertIs(create_valuable(self.conn, 1, item), True)
        receipts = self.receipt_rows()
        self.assertIs(create_valuable(self.conn, 1, valuable(name="Caf\u00e9", value=12.0)), False)
        self.assertEqual(receipts, self.receipt_rows())
        self.assertEqual(list_valuables(self.conn, 1), [clean_valuable(item)])
        canonical = json.dumps(clean_valuable(item), sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(receipts[0][0:3], (1, "create:" + item["id"], expected))
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(app_valuable_requests)")]
        self.assertEqual(columns, ["user_id", "request_id", "payload_hash", "created_at"])
        self.assertNotIn("Caf", str(receipts))

    def test_changed_create_payload_conflicts_without_overwrite(self):
        item = valuable()
        create_valuable(self.conn, 1, item)
        before = self.receipt_rows()
        for changed in (valuable(name="Other"), valuable(value=123.46)):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                ValueError, "^valuable_request_conflict$"
            ):
                create_valuable(self.conn, 1, changed)
        self.assertEqual(list_valuables(self.conn, 1), [item])
        self.assertEqual(self.receipt_rows(), before)

    def test_create_replay_after_update_never_restores_old_values_or_origin(self):
        item = valuable()
        create_valuable(self.conn, 1, item, origin="onboarding")
        changed = valuable(name="Updated", value=999.99)
        self.assertTrue(update_valuable(self.conn, 1, item["id"], changed))
        self.assertFalse(create_valuable(self.conn, 1, item, origin="manual"))
        self.assertEqual(list_valuables(self.conn, 1), [changed])
        with self.assertRaisesRegex(ValueError, "^valuable_request_conflict$"):
            create_valuable(self.conn, 1, changed)
        self.assertEqual(self.conn.execute("SELECT origin FROM app_valuables").fetchone()[0],
                         "onboarding")

    def test_deleted_create_never_resurrects_and_changed_replay_conflicts(self):
        item = valuable()
        create_valuable(self.conn, 1, item)
        receipts = self.receipt_rows()
        self.assertTrue(delete_valuable(self.conn, 1, item["id"]))
        self.assertFalse(create_valuable(self.conn, 1, item))
        with self.assertRaisesRegex(ValueError, "^valuable_request_conflict$"):
            create_valuable(self.conn, 1, valuable(value=100))
        self.assertEqual(list_valuables(self.conn, 1), [])
        self.assertEqual(self.receipt_rows(), receipts)

    def test_deleted_create_receipt_is_durable_across_new_connections(self):
        item = valuable()
        create_valuable(self.conn, 1, item)
        self.conn.commit()
        deleting = sqlite3.connect(self.path)
        try:
            deleting.execute("PRAGMA foreign_keys=ON")
            deleting.execute("BEGIN")
            self.assertTrue(delete_valuable(deleting, 1, item["id"]))
            deleting.commit()
        finally:
            deleting.close()
        replaying = sqlite3.connect(self.path)
        try:
            replaying.execute("PRAGMA foreign_keys=ON")
            replaying.execute("BEGIN")
            self.assertFalse(create_valuable(replaying, 1, item))
            replaying.commit()
        finally:
            replaying.close()
        self.assertEqual(list_valuables(self.conn, 1), [])
        self.assertEqual(len(self.receipt_rows()), 1)

    def test_existing_position_without_receipt_cannot_be_hijacked(self):
        self.prepare()
        original = valuable()
        self.seed_position(original)
        self.conn.commit()
        self.conn.execute("BEGIN")
        with self.assertRaisesRegex(ValueError, "^valuable_request_conflict$"):
            create_valuable(self.conn, 1, valuable(name="Hijacked"))
        self.conn.rollback()
        self.assertEqual(list_valuables(self.conn, 1), [original])
        self.assertEqual(self.receipt_rows(), [])

    def test_existing_identical_position_gets_receipt_without_overwrite(self):
        self.prepare()
        item = valuable()
        self.seed_position(item)
        before = self.conn.execute("SELECT * FROM app_valuables").fetchall()
        self.assertFalse(create_valuable(self.conn, 1, item))
        self.assertEqual(self.conn.execute("SELECT * FROM app_valuables").fetchall(), before)
        self.assertEqual(len(self.receipt_rows()), 1)
        delete_valuable(self.conn, 1, item["id"])
        self.assertFalse(create_valuable(self.conn, 1, item))
        self.assertEqual(list_valuables(self.conn, 1), [])

    def test_create_accepts_only_explicit_origins_and_validates_before_writes(self):
        for bad in (None, True, 1, "Manual", "manual ", "other", []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                create_valuable(self.conn, 1, valuable(), origin=bad)
        with self.assertRaises(ValueError):
            create_valuable(self.conn, 1, valuable(value=1.001))
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='app_valuables'"
        ).fetchone())
        for i, origin in enumerate(("manual", "legacy", "onboarding")):
            self.assertTrue(create_valuable(self.conn, 1, valuable(i), origin=origin))
        self.assertEqual(self.conn.execute(
            "SELECT origin FROM app_valuables ORDER BY position_id"
        ).fetchall(), [("manual",), ("legacy",), ("onboarding",)])

    def test_reads_are_scoped_and_stably_sorted_with_tuple_or_row_factory(self):
        for number in (3, 1, 2):
            create_valuable(self.conn, 1, valuable(number))
        create_valuable(self.conn, 2, valuable(1, "Other user", 2))
        expected = [valuable(i) for i in (1, 2, 3)]
        self.assertEqual(list_valuables(self.conn, 1), expected)
        self.conn.row_factory = sqlite3.Row
        self.assertEqual(list_valuables(self.conn, 1), expected)
        self.assertEqual(list_valuables(self.conn, 2), [valuable(1, "Other user", 2)])
        self.assertEqual(list_valuables(self.conn, 999), [])
        self.assertIs(self.conn.row_factory, sqlite3.Row)

    def test_update_is_owned_full_replace_preserving_identity_origin_and_receipts(self):
        item = valuable()
        create_valuable(self.conn, 1, item, origin="legacy")
        self.conn.execute(
            "UPDATE app_valuables SET created_at='2000-01-01',updated_at='2000-01-01'"
        )
        receipts = self.receipt_rows()
        changed = valuable(name=" New name ", value=0.01)
        self.assertIs(update_valuable(self.conn, 2, item["id"], changed), False)
        self.assertIs(update_valuable(self.conn, 1, valuable(2)["id"], valuable(2)), False)
        self.assertEqual(list_valuables(self.conn, 1), [item])
        self.assertIs(update_valuable(self.conn, 1, item["id"], changed), True)
        self.assertEqual(list_valuables(self.conn, 1), [clean_valuable(changed)])
        self.assertEqual(list_valuables(self.conn, 2), [])
        row = self.conn.execute(
            "SELECT position_id,origin,created_at,updated_at FROM app_valuables"
        ).fetchone()
        self.assertEqual(row[:3], (item["id"], "legacy", "2000-01-01"))
        self.assertNotEqual(row[3], "2000-01-01")
        self.assertEqual(self.receipt_rows(), receipts)

    def test_update_rejects_partial_extra_mismatched_id_and_invalid_value(self):
        item = valuable()
        create_valuable(self.conn, 1, item)
        for bad in ({"n": "Only name"}, {"v": 1}, {**item, "extra": True},
                    valuable(2), valuable(value=1.001), valuable(name="\nWatch")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                update_valuable(self.conn, 1, item["id"], bad)
        self.assertEqual(list_valuables(self.conn, 1), [item])

    def test_update_and_delete_reject_invalid_path_ids(self):
        for bad in (None, 1, True, "", "a" * 31, "A" * 32, "a" * 32 + "\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    update_valuable(self.conn, 1, bad, valuable())
                with self.assertRaises(ValueError):
                    delete_valuable(self.conn, 1, bad)

    def test_delete_is_owned_and_keeps_other_users_positions_and_receipts(self):
        item = valuable()
        create_valuable(self.conn, 1, item)
        self.assertIs(delete_valuable(self.conn, 2, item["id"]), False)
        create_valuable(self.conn, 2, valuable(name="Other"))
        receipts = self.receipt_rows()
        self.assertIs(delete_valuable(self.conn, 1, item["id"]), True)
        self.assertIs(delete_valuable(self.conn, 1, item["id"]), False)
        self.assertEqual(list_valuables(self.conn, 1), [])
        self.assertEqual(list_valuables(self.conn, 2), [valuable(name="Other")])
        self.assertEqual(self.receipt_rows(), receipts)

    def test_absent_update_and_delete_return_false(self):
        item = valuable()
        self.assertIs(update_valuable(self.conn, 1, item["id"], item), False)
        self.assertIs(delete_valuable(self.conn, 1, item["id"]), False)

    def test_claim_canonical_nested_json_sorted_and_only_hash_stored(self):
        first = {"z": [3, {"y": True, "x": None}], "a": "\u00e4 secret"}
        second = {"a": "\u00e4 secret", "z": [3, {"x": None, "y": True}]}
        self.assertIs(claim_valuable_request(self.conn, 1, "onboarding:v1", first), True)
        before = self.receipt_rows()
        self.assertIs(claim_valuable_request(self.conn, 1, "onboarding:v1", second), False)
        self.assertEqual(self.receipt_rows(), before)
        canonical = json.dumps(first, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(before[0][2], hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        self.assertNotIn("secret", str(before))
        with self.assertRaisesRegex(ValueError, "^valuable_request_conflict$"):
            claim_valuable_request(self.conn, 1, "onboarding:v1", {"a": "changed"})
        self.assertEqual(self.receipt_rows(), before)

    def test_request_keys_preserve_all_printable_ascii_and_arbitrary_prefixes(self):
        keys = ["create:" + valuable()["id"], "onboarding:batch/1", "manual|anything",
                " ", " padded ", "padded", "x" * 128,
                "".join(chr(i) for i in range(32, 127))]
        for key in keys:
            with self.subTest(key=key):
                self.assertTrue(claim_valuable_request(self.conn, 1, key, {"a": 1}))
                self.assertFalse(claim_valuable_request(self.conn, 1, key, {"a": 1}))
        self.assertEqual({row[1] for row in self.receipt_rows()}, set(keys))

    def test_request_keys_reject_types_nonascii_controls_and_length(self):
        for bad in (None, False, 1, [], b"request", "", "x" * 129, "\u00e4", "\x7f",
                    "a\nb", "a\tb", "a\x00b", "\u200b", "\ud800"):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                claim_valuable_request(self.conn, 1, bad, {})
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='app_valuable_requests'"
        ).fetchone())

    def test_claim_rejects_nonfinite_and_nonjson_payload_before_writes(self):
        for bad in (float("nan"), float("inf"), float("-inf"),
                    {"nested": [float("nan")]}, {"nested": {"v": float("inf")}},
                    {"unsupported": object()}, {"bytes": b"x"}, {"set": {1}},
                    {1: "integer", "two": "string"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                claim_valuable_request(self.conn, 1, "bad", bad)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='app_valuable_requests'"
        ).fetchone())

    def test_request_replay_is_scoped_by_user(self):
        self.assertTrue(claim_valuable_request(self.conn, 1, "batch", {"v": 1}))
        self.assertTrue(claim_valuable_request(self.conn, 2, "batch", {"v": 2}))
        self.assertFalse(claim_valuable_request(self.conn, 1, "batch", {"v": 1}))
        with self.assertRaisesRegex(ValueError, "^valuable_request_conflict$"):
            claim_valuable_request(self.conn, 2, "batch", {"v": 1})

    def test_request_matches_canonical_payload_is_scoped_and_never_claims(self):
        payload = {"z": [1, {"b": True, "a": "\u00e4"}], "a": None}
        reordered = {"a": None, "z": [1, {"a": "\u00e4", "b": True}]}
        claim_valuable_request(self.conn, 1, "onboarding:batch", payload)
        claim_valuable_request(self.conn, 2, "other", payload)
        before = self.receipt_rows()
        changes_before = self.conn.total_changes
        self.assertIs(valuable_request_matches(self.conn, 1, "onboarding:batch", reordered), True)
        self.assertIs(valuable_request_matches(self.conn, 1, "onboarding:batch", {}), False)
        self.assertIs(valuable_request_matches(self.conn, 2, "onboarding:batch", payload), False)
        self.assertIs(valuable_request_matches(self.conn, 1, "other", payload), False)
        self.assertIs(valuable_request_matches(self.conn, 1, "missing", payload), False)
        self.assertEqual(self.receipt_rows(), before)
        self.assertEqual(self.conn.total_changes, changes_before)
        self.assertTrue(claim_valuable_request(self.conn, 1, "missing", payload))

    def test_request_matches_uses_same_validation_as_claim_without_schema_writes(self):
        for request_id, payload in (("", {}), ("a\nb", {}), ("x" * 129, {}),
                                    ("valid", {"v": float("nan")}),
                                    ("valid", {"v": object()})):
            with self.subTest(request_id=request_id, payload=payload):
                with self.assertRaises(ValueError) as claim_error:
                    claim_valuable_request(self.conn, 1, request_id, payload)
                with self.assertRaises(ValueError) as match_error:
                    valuable_request_matches(self.conn, 1, request_id, payload)
                self.assertEqual(str(match_error.exception), str(claim_error.exception))
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='app_valuable_requests'"
        ).fetchone())

    def test_request_matches_on_readonly_database_with_or_without_receipt_table(self):
        self.conn.commit()
        payload = {"items": [valuable()]}
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.conn.execute("BEGIN")
                    claim_valuable_request(self.conn, 1, "onboarding:done", payload)
                    self.conn.commit()
                reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
                try:
                    reader.execute("PRAGMA query_only=ON")
                    reader.set_authorizer(
                        lambda action, *args: sqlite3.SQLITE_OK
                        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ)
                        else sqlite3.SQLITE_DENY
                    )
                    self.assertIs(valuable_request_matches(
                        reader, 1, "onboarding:done", payload
                    ), present)
                    self.assertIs(valuable_request_matches(
                        reader, 1, "onboarding:done", {"items": []}
                    ), False)
                    self.assertIs(valuable_request_matches(
                        reader, 2, "onboarding:done", payload
                    ), False)
                    self.assertIs(valuable_request_matches(reader, 1, "missing", payload), False)
                    self.assertFalse(reader.in_transaction)
                    self.assertEqual(reader.total_changes, 0)
                finally:
                    reader.close()
        self.assertEqual(len(self.receipt_rows()), 1)

    def test_request_matches_does_not_begin_transaction_or_create_missing_tables(self):
        self.conn.commit()
        self.assertIs(valuable_request_matches(self.conn, 1, "missing", {}), False)
        self.assertFalse(self.conn.in_transaction)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('app_valuables','app_valuable_requests')"
        ).fetchone())

    def test_missing_users_are_rejected_by_both_foreign_keys(self):
        self.prepare()
        with self.assertRaises(sqlite3.IntegrityError):
            self.seed_position(valuable(), user_id=999)
        with self.assertRaises(sqlite3.IntegrityError):
            claim_valuable_request(self.conn, 999, "missing", {})
        with self.assertRaises(sqlite3.IntegrityError):
            create_valuable(self.conn, 999, valuable())
        self.assertEqual(self.receipt_rows(), [])

    def test_deleting_user_cascades_positions_and_receipts_only_for_that_user(self):
        for user_id in (1, 2):
            create_valuable(self.conn, user_id, valuable())
            claim_valuable_request(self.conn, user_id, "batch", {"a": user_id})
        self.conn.execute("DELETE FROM users WHERE user_id=1")
        self.assertEqual(list_valuables(self.conn, 1), [])
        self.assertEqual(list_valuables(self.conn, 2), [valuable()])
        self.assertEqual([row[0] for row in self.receipt_rows()], [2, 2])
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_live_limit_only_applies_to_actual_new_insert_and_is_user_scoped(self):
        self.seed_full_user()
        self.conn.commit()
        self.conn.execute("BEGIN")
        self.assertFalse(create_valuable(self.conn, 1, valuable(0, "Seed", 1)))
        self.assertTrue(update_valuable(self.conn, 1, valuable(1)["id"], valuable(1)))
        self.assertTrue(create_valuable(self.conn, 2, valuable(500)))
        self.conn.commit()
        self.conn.execute("BEGIN")
        with self.assertRaisesRegex(ValueError, "^valuable_limit_reached$"):
            create_valuable(self.conn, 1, valuable(500))
        self.conn.rollback()
        self.assertEqual(len(list_valuables(self.conn, 1)), 500)
        self.assertEqual(len(list_valuables(self.conn, 2)), 1)
        self.conn.execute("BEGIN")
        self.assertTrue(delete_valuable(self.conn, 1, valuable(2)["id"]))
        self.assertTrue(create_valuable(self.conn, 1, valuable(500)))
        self.assertEqual(len(list_valuables(self.conn, 1)), 500)

    def test_replay_precedes_count_at_limit_even_after_position_deleted(self):
        self.seed_full_user()
        old = valuable(0, "Seed", 1)
        self.assertFalse(create_valuable(self.conn, 1, old))
        delete_valuable(self.conn, 1, old["id"])
        create_valuable(self.conn, 1, valuable(500))
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            self.assertFalse(create_valuable(self.conn, 1, old))
            with self.assertRaisesRegex(ValueError, "^valuable_request_conflict$"):
                create_valuable(self.conn, 1, valuable(0, "Changed", 1))
        finally:
            self.conn.set_trace_callback(None)
        self.assertFalse(any("COUNT(" in sql.upper() for sql in statements))
        self.assertFalse(any(sql.lstrip().upper().startswith("INSERT INTO APP_VALUABLES(")
                             for sql in statements))
        self.assertEqual(len(list_valuables(self.conn, 1)), 500)
        self.assertNotIn(old["id"], {item["id"] for item in list_valuables(self.conn, 1)})

    def test_no_helper_commits_and_other_connection_cannot_see_pending_writes(self):
        self.prepare()
        self.conn.commit()
        observer = sqlite3.connect(self.path)
        self.addCleanup(observer.close)
        self.conn.execute("BEGIN")
        create_valuable(self.conn, 1, valuable())
        claim_valuable_request(self.conn, 1, "batch", {"a": 1})
        update_valuable(self.conn, 1, valuable()["id"], valuable(name="Changed"))
        delete_valuable(self.conn, 1, valuable()["id"])
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(list_valuables(observer, 1), [])
        self.assertEqual(observer.execute("SELECT * FROM app_valuable_requests").fetchall(), [])
        self.conn.rollback()
        self.assertEqual(self.receipt_rows(), [])

    def test_failed_batch_rolls_back_claim_and_all_positions_then_can_retry(self):
        self.prepare()
        self.conn.commit()
        items = [valuable(1), valuable(2), valuable(3)]
        payload = {"items": items}
        self.conn.execute("BEGIN")
        try:
            claim_valuable_request(self.conn, 1, "onboarding:batch", payload)
            for item in items:
                create_valuable(self.conn, 1, item, origin="onboarding")
            create_valuable(self.conn, 1, valuable(2, "Conflicting", 1))
            self.fail("Expected a batch conflict")
        except ValueError as error:
            self.assertEqual(str(error), "valuable_request_conflict")
            self.conn.rollback()
        self.assertEqual(list_valuables(self.conn, 1), [])
        self.assertEqual(self.receipt_rows(), [])
        self.conn.execute("BEGIN")
        self.assertTrue(claim_valuable_request(self.conn, 1, "onboarding:batch", payload))
        for item in items:
            self.assertTrue(create_valuable(self.conn, 1, item, origin="onboarding"))
        self.conn.commit()
        self.assertEqual(list_valuables(self.conn, 1), items)
        self.assertEqual(len(self.receipt_rows()), 4)

    def test_rollback_restores_updates_deletes_and_keeps_original_receipts(self):
        create_valuable(self.conn, 1, valuable())
        create_valuable(self.conn, 1, valuable(2))
        before = self.receipt_rows()
        self.conn.commit()
        self.conn.execute("BEGIN")
        update_valuable(self.conn, 1, valuable()["id"], valuable(value=1))
        delete_valuable(self.conn, 1, valuable(2)["id"])
        claim_valuable_request(self.conn, 1, "temporary", {})
        self.conn.rollback()
        self.assertEqual(list_valuables(self.conn, 1), [valuable(), valuable(2)])
        self.assertEqual(self.receipt_rows(), before)

    def test_all_operations_leave_user_money_and_financial_history_untouched(self):
        before = self.snapshot_finances()
        create_valuable(self.conn, 1, valuable())
        create_valuable(self.conn, 2, valuable())
        claim_valuable_request(self.conn, 1, "batch", {"items": [valuable()]})
        update_valuable(self.conn, 1, valuable()["id"], valuable(value=999.99))
        list_valuables(self.conn, 1)
        delete_valuable(self.conn, 1, valuable()["id"])
        create_valuable(self.conn, 1, valuable())
        self.prepare()
        self.conn.commit()
        self.assertEqual(self.snapshot_finances(), before)

    def test_readonly_reads_with_and_without_schema_never_write_or_begin_transaction(self):
        self.conn.commit()
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.conn.execute("BEGIN")
                    create_valuable(self.conn, 1, valuable())
                    self.conn.commit()
                reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
                try:
                    reader.execute("PRAGMA query_only=ON")
                    reader.set_authorizer(
                        lambda action, *args: sqlite3.SQLITE_OK
                        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ)
                        else sqlite3.SQLITE_DENY
                    )
                    expected = [valuable()] if present else []
                    self.assertEqual(list_valuables(reader, 1), expected)
                    self.assertEqual(list_valuables(reader, 2), [])
                    self.assertFalse(reader.in_transaction)
                    self.assertEqual(reader.total_changes, 0)
                finally:
                    reader.close()

    def test_sqlite_lock_prevents_two_writers_from_exceeding_limit(self):
        self.seed_full_user()
        delete_valuable(self.conn, 1, valuable(499)["id"])
        self.conn.commit()
        contender = sqlite3.connect(self.path, timeout=0)
        self.addCleanup(contender.close)
        contender.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("BEGIN")
        create_valuable(self.conn, 1, valuable(500))
        contender.execute("BEGIN")
        with self.assertRaises(sqlite3.OperationalError) as error:
            create_valuable(contender, 1, valuable(501))
        self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
        contender.rollback()
        self.conn.commit()
        contender.execute("BEGIN")
        with self.assertRaisesRegex(ValueError, "^valuable_limit_reached$"):
            create_valuable(contender, 1, valuable(501))
        contender.rollback()
        self.assertEqual(len(list_valuables(self.conn, 1)), 500)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM app_valuable_requests WHERE request_id=?",
            ("create:" + valuable(501)["id"],),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
