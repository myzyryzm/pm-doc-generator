"""
The validate-and-repair loop, with `claude` stubbed out. These pin the two
behaviours that decide whether bad output can be written:

  * a formatting slip is repaired by resuming the session, and the repaired
    document — not the original — is what gets written;
  * a refusal is never repaired.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import (
    DOC_ID, GOOD_PRODUCT_BACKEND, GOOD_PRODUCT_FRONTEND, GOOD_STORY, trace_output,
)
from pmdoc.actions.schema import schema_example_json
from pmdoc.actions.map import validate_map_output
from pmdoc.claude import repair
from pmdoc.claude.session import ClaudeRun
from pmdoc.config import CANNOT_COMPLETE_SENTINEL, MAX_REPAIR_ATTEMPTS
from pmdoc.story import run as story_run
from pmdoc.story import synthesize as synth

REPO = {"name": "backend", "kind": "backend", "path": ".", "tier": 0}
GOOD = trace_output(GOOD_PRODUCT_BACKEND, "backend")
ADDENDUM = ("Both remaining investigations came back.\n\n## Corrections to what I printed\n\n"
            "Everything else in both documents stands as printed.\n")
REFUSAL = f"{CANNOT_COMPLETE_SENTINEL} the handler is not in this repository."


def _run(text, session_id="sess-1"):
    return ClaudeRun(True, text=text, session_id=session_id, num_turns=7, cost_usd=0.1)


class TraceRepairTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.log = self.dir / "backend.log"

    def trace(self):
        return story_run.trace_repo(REPO, "prompt", cwd=self.dir, s_dir=self.dir, log_path=self.log,
                                    doc_id=DOC_ID, model="haiku", max_turns=5, effort=None)

    def patch(self, invoke, resume=None):
        p1 = mock.patch("pmdoc.claude.session.invoke", return_value=invoke)
        p2 = mock.patch("pmdoc.claude.session.resume",
                        side_effect=resume if isinstance(resume, list) else None,
                        return_value=None if isinstance(resume, list) else resume)
        inv, res = p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return inv, res

    def test_good_output_written_without_repair(self):
        _, res = self.patch(_run(GOOD))
        ok, _ = self.trace()
        self.assertTrue(ok)
        res.assert_not_called()
        self.assertTrue((self.dir / "backend.md").read_text().startswith(f"# {DOC_ID} — backend"))
        self.assertIn("Technical Appendix", (self.dir / "backend.technical.md").read_text())

    def test_addendum_is_repaired_and_repaired_text_written(self):
        repaired = GOOD.replace("web address built from its title", "REPAIRED address")
        _, res = self.patch(_run(ADDENDUM), _run(repaired))
        ok, _ = self.trace()
        self.assertTrue(ok)
        nudge = res.call_args.args[1]
        for needle in ("Do NOT investigate further", f"# {DOC_ID} — backend",
                       "===TECHNICAL_APPENDIX===", "addendum"):
            self.assertIn(needle, nudge)
        self.assertEqual(res.call_args.args[0], "sess-1")
        self.assertIn("REPAIRED address", (self.dir / "backend.md").read_text())

    def test_refusal_is_never_repaired(self):
        _, res = self.patch(_run(REFUSAL))
        ok, _ = self.trace()
        self.assertFalse(ok)
        res.assert_not_called()
        self.assertFalse((self.dir / "backend.md").exists())
        self.assertTrue((self.dir / "backend.INVALID.md").exists())
        self.assertFalse(json.loads((self.dir / "backend.INVALID.json").read_text())["repairable"])

    def test_gives_up_after_max_attempts(self):
        _, res = self.patch(_run(ADDENDUM), [_run(ADDENDUM)] * MAX_REPAIR_ATTEMPTS)
        ok, _ = self.trace()
        self.assertFalse(ok)
        self.assertEqual(res.call_count, MAX_REPAIR_ATTEMPTS)
        self.assertIn("final attempt", res.call_args.args[1])
        self.assertTrue((self.dir / "backend.INVALID.md").exists())

    def test_no_session_id_means_no_repair(self):
        _, res = self.patch(_run(ADDENDUM, session_id=None))
        ok, _ = self.trace()
        self.assertFalse(ok)
        res.assert_not_called()

    def test_session_failure_is_not_retried(self):
        _, res = self.patch(ClaudeRun(False, error="claude exited 1"))
        ok, _ = self.trace()
        self.assertFalse(ok)
        res.assert_not_called()


class JsonRepairTest(unittest.TestCase):
    def test_bad_map_json_is_repaired(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        good = "```json\n" + schema_example_json() + "\n```"
        with mock.patch("pmdoc.claude.session.invoke", return_value=_run("I listed them above.")), \
                mock.patch("pmdoc.claude.session.resume", return_value=_run(good)) as res:
            run, text, result = repair.run_with_validation(
                "p", cwd=".", log_path=Path(tmp.name) / "m.log", model="haiku", max_turns=3,
                validate_fn=validate_map_output, nudge_fn=repair.build_json_nudge)
        self.assertTrue(result.ok)
        self.assertEqual(text, good)
        self.assertIn("single ```json fenced block", res.call_args.args[1])


class SynthesisTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name in ("story_dir", "story_logs_dir"):
            p = mock.patch.object(synth, name, lambda project, slug: self.dir)
            p.start()
            self.addCleanup(p.stop)
        (self.dir / "_synthesis.md").write_text("Synthesize.\n<!-- ALL_PRODUCT_DOCS -->\n")
        self.manifest = {
            "project": "mini", "story_slug": "add-post", "doc_id": DOC_ID, "prompt": "add post",
            "synthesis_prompt": str(self.dir / "_synthesis.md"),
            "repos": [{"name": "backend", "kind": "backend", "path": ".", "tier": 0},
                      {"name": "frontend", "kind": "frontend", "path": ".", "tier": 3}],
            "resolution": {"chains": []}, "actions": {}}

    def test_refuses_when_a_repo_doc_is_missing(self):
        (self.dir / "backend.md").write_text(GOOD_PRODUCT_BACKEND)
        with mock.patch("pmdoc.claude.session.invoke") as inv:
            self.assertFalse(synth.synthesize(self.manifest, model="haiku"))
        inv.assert_not_called()
        self.assertFalse((self.dir / "STORY.md").exists())

    def test_writes_story_and_technical_appendix(self):
        (self.dir / "backend.md").write_text(GOOD_PRODUCT_BACKEND)
        (self.dir / "frontend.md").write_text(GOOD_PRODUCT_FRONTEND)
        (self.dir / "backend.technical.md").write_text("# t\n\n## Entry Points / Code Path\nx.py:1\n")
        with mock.patch("pmdoc.claude.session.invoke",
                        return_value=_run("Here it is:\n\n" + GOOD_STORY)) as inv:
            self.assertTrue(synth.synthesize(self.manifest, model="haiku"))
        prompt = inv.call_args.args[0]
        self.assertIn("### Product write-up: `frontend` (frontend)", prompt)
        self.assertEqual(inv.call_args.kwargs["tools"], "")
        self.assertTrue((self.dir / "STORY.md").read_text().startswith(f"# {DOC_ID}"))
        tech = (self.dir / "STORY.technical.md").read_text()
        self.assertIn("### Entry Points / Code Path", tech)
        self.assertIn("_No technical appendix on disk._", tech)   # frontend had none


if __name__ == "__main__":
    unittest.main()
