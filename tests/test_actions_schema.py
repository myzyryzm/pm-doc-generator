"""Action-map schema validation, normalization, and JSON extraction."""

import copy
import unittest

from helpers import fixture_maps  # noqa: F401
from pmdoc.actions.map import validate_map_output
from pmdoc.actions.schema import (
    SCHEMA_EXAMPLE, normalize_action_map, schema_example_json, validate_action_map,
)
from pmdoc.claude.envelope import JsonBlockError, extract_json_block
from pmdoc.config import CANNOT_COMPLETE_SENTINEL


class ExtractJsonTest(unittest.TestCase):
    def test_fenced_with_prose(self):
        self.assertEqual(extract_json_block('Done.\n```json\n{"a": 1}\n```\nBye'), {"a": 1})

    def test_last_valid_block_wins(self):
        text = '```json\n{"a": 1}\n```\nfixed:\n```json\n{"a": 2}\n```'
        self.assertEqual(extract_json_block(text), {"a": 2})

    def test_bare_json_and_braces(self):
        self.assertEqual(extract_json_block('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json_block('here {"a": 1} ok'), {"a": 1})

    def test_no_json(self):
        with self.assertRaises(JsonBlockError):
            extract_json_block("no json here")
        with self.assertRaises(JsonBlockError):
            extract_json_block("```json\n{broken\n```")


class SchemaTest(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertTrue(validate_action_map(SCHEMA_EXAMPLE).ok)
        self.assertIn('"ui-action"', schema_example_json())

    def test_errors_are_repairable(self):
        bad = copy.deepcopy(SCHEMA_EXAMPLE)
        del bad["actions"][0]["entry"]
        bad["actions"][1]["route"] = {"method": "FETCH", "path": "/x"}
        bad["actions"][2]["trigger"] = {"type": "sometimes"}
        bad["actions"][0]["effects"] = [{"type": "magic"}]      # coerced later, not an error
        r = validate_action_map(bad)
        self.assertFalse(r.ok)
        self.assertTrue(r.repairable)
        self.assertEqual(len(r.errors), 3)

    def test_off_list_effect_types_are_coerced(self):
        obj = {"api_base": None, "actions": [
            {"id": "x", "kind": "process", "label": "x", "summary": "x", "area": "A",
             "entry": {"file": "a.py"}, "trigger": {"type": "startup"},
             "calls": [{"method": "get", "path": "user"}],
             "effects": [{"type": "http", "target": "API"}, {"type": "magic", "detail": "d"},
                         {"type": "DB-Write"}]}]}
        self.assertTrue(validate_action_map(obj).ok)
        m, _ = normalize_action_map(obj, "frontend")
        a = m["actions"][0]
        self.assertEqual([e["type"] for e in a["effects"]], ["http-call", "other", "db-write"])
        self.assertEqual(a["effects"][1]["detail"], "[magic] d")
        self.assertEqual(a["calls"][0]["method"], "GET")

    def test_empty_and_wrong_shape(self):
        self.assertFalse(validate_action_map({"actions": []}).ok)
        self.assertFalse(validate_action_map([]).ok)

    def test_normalize_prefixes_ids_dedupes_and_defaults(self):
        obj = {"api_base": None, "actions": [
            {"id": "Create Article", "kind": "api-route", "label": "x", "summary": "x", "area": "A",
             "entry": {"file": "a.py"}, "route": {"method": "post", "path": "/a"}},
            {"id": "create-article", "kind": "api-route", "label": "y", "summary": "y", "area": "A",
             "entry": {"file": "a.py"}, "route": {"method": "get", "path": "/a"}}]}
        m, warnings = normalize_action_map(obj, "backend")
        self.assertEqual([a["id"] for a in m["actions"]],
                         ["backend:api:create-article", "backend:api:create-article-2"])
        self.assertEqual(m["actions"][0]["route"]["method"], "POST")
        self.assertEqual(m["actions"][0]["aliases"], [])
        self.assertEqual(m["actions"][0]["auth"], "unknown")
        self.assertEqual(len(warnings), 1)

    def test_map_output_validation_order(self):
        refusal = validate_map_output(f"{CANNOT_COMPLETE_SENTINEL} repo is empty")
        self.assertFalse(refusal.ok)
        self.assertFalse(refusal.repairable)
        nojson = validate_map_output("I mapped everything, see above.")
        self.assertFalse(nojson.ok)
        self.assertTrue(nojson.repairable)
        good = validate_map_output("```json\n" + schema_example_json() + "\n```")
        self.assertTrue(good.ok)


if __name__ == "__main__":
    unittest.main()
