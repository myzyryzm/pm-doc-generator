"""Prompt → actions: prefilter ranking, the Claude path (stubbed), fallbacks, expansion."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import fixture_maps
from pmdoc.actions import resolve as R
from pmdoc.actions.link import build_links
from pmdoc.claude.session import ClaudeRun
from pmdoc.project import Project, Repo


def _project():
    return Project(name="mini", description="A blog.", repos=[
        Repo("backend", Path("."), "backend", "config"),
        Repo("frontend", Path("."), "frontend", "config")])


def _answer(ids, slug="add-post", clarification=None):
    return "```json\n" + json.dumps({"story_id_suggestion": slug, "matched_action_ids": ids,
                                     "repos_involved": [], "rationale": "because",
                                     "clarification_needed": clarification}) + "\n```"


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.maps = fixture_maps()
        self.links = build_links(self.maps, ["/api"])
        self.index = R.build_index(self.maps, self.links)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "resolve.log"

    def run_resolve(self, prompt="explain the add post feature", **kw):
        return R.resolve(_project(), self.maps, self.links, prompt, log_path=self.log,
                         model="haiku", **kw)

    def test_prefilter_ranks_publish_first(self):
        ranked = sorted(self.index, key=lambda e: -R.score("explain the add post feature", e))
        self.assertEqual(ranked[0]["id"], "frontend:ui:publish-article")

    def test_prefilter_keeps_chain_mates(self):
        kept = {e["id"] for e in R.prefilter("add post", self.index, limit=1)}
        self.assertEqual(kept, {"frontend:ui:publish-article", "backend:api:create-article"})

    def test_small_index_is_not_filtered(self):
        self.assertEqual(len(R.prefilter("anything", self.index)), len(self.index))

    def test_claude_path_expands_chain_and_orders_repos(self):
        with mock.patch("pmdoc.claude.session.invoke",
                        return_value=ClaudeRun(True, _answer(["frontend:ui:publish-article"]),
                                               session_id="s")) as inv:
            res = self.run_resolve()
        self.assertEqual(inv.call_args.kwargs["tools"], "")      # no tools for the resolver
        self.assertEqual(res.method, "claude")
        self.assertEqual(res.story_slug, "add-post")
        self.assertEqual(res.doc_id, "mini_add-post")
        self.assertEqual(res.action_ids, ["frontend:ui:publish-article", "backend:api:create-article"])
        self.assertEqual(res.repos_involved, ["backend", "frontend"])   # backend tier first
        self.assertEqual([a["id"] for a in res.entry_points["backend"]], ["backend:api:create-article"])

    def test_unknown_id_is_repaired_once(self):
        runs = [ClaudeRun(True, _answer(["frontend:ui:nope"]), session_id="s"),
                ClaudeRun(True, _answer(["frontend:ui:publish-article"]), session_id="s")]
        with mock.patch("pmdoc.claude.session.invoke", return_value=runs[0]), \
                mock.patch("pmdoc.claude.session.resume", return_value=runs[1]) as rs:
            res = self.run_resolve()
        self.assertIn("unknown action id", rs.call_args.args[1])
        self.assertEqual(res.matched_action_ids, ["frontend:ui:publish-article"])

    def test_falls_back_to_keywords(self):
        with mock.patch("pmdoc.claude.session.invoke", return_value=ClaudeRun(False, error="boom")):
            res = self.run_resolve()
        self.assertEqual(res.method, "prefilter")
        self.assertEqual(res.matched_action_ids, ["frontend:ui:publish-article"])

    def test_pinned_skips_claude(self):
        with mock.patch("pmdoc.claude.session.invoke") as inv:
            res = self.run_resolve(pinned=["backend:api:get-tags"], slug="tags")
        inv.assert_not_called()
        self.assertEqual(res.method, "pinned")
        self.assertEqual(res.repos_involved, ["backend"])
        with self.assertRaises(R.ResolveError):
            self.run_resolve(pinned=["backend:api:nope"])

    def test_slug_rules(self):
        with self.assertRaises(R.ResolveError):
            self.run_resolve(pinned=["backend:api:get-tags"], slug="Bad_Slug")
        self.assertEqual(R.clean_slug("Add_Post!!", "x"), "add-post")
        self.assertEqual(R.clean_slug(None, "How does favoriting an article work?"),
                         "favoriting-article")

    def test_clarification_surfaces(self):
        with mock.patch("pmdoc.claude.session.invoke",
                        return_value=ClaudeRun(True, _answer([], clarification="Which one?"),
                                               session_id="s")):
            res = self.run_resolve("explain the thing")
        self.assertEqual(res.clarification, "Which one?")
        self.assertEqual(res.action_ids, [])


if __name__ == "__main__":
    unittest.main()
