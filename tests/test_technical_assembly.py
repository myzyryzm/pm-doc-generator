"""STORY.technical.md is assembled deterministically from the per-repo appendices."""

import unittest

from helpers import fixture_maps
from pmdoc.story.synthesize import assemble_technical_appendix, demote


class AssemblyTest(unittest.TestCase):
    def test_demote_skips_code_fences(self):
        text = "## A\n```\n## not a heading\n```\n### B"
        self.assertEqual(demote(text), "### A\n```\n## not a heading\n```\n#### B")

    def test_layout(self):
        by_id = {a["id"]: a for m in fixture_maps().values() for a in m["actions"]}
        ids = ["frontend:ui:publish-article", "backend:api:create-article"]
        manifest = {
            "doc_id": "mini_add-post", "prompt": "explain the add post feature",
            "repos": [{"name": "backend", "kind": "backend"}, {"name": "frontend", "kind": "frontend"}],
            "resolution": {"method": "claude", "rationale": "add post = publish",
                           "chains": [{"label": "Publish Article", "actions": ids}]},
            "actions": {i: by_id[i] for i in ids},
        }
        out = assemble_technical_appendix(manifest, {
            "backend": "# mini_add-post — backend (Technical Appendix)\n\n## Data Flow\nput_item\n",
            "frontend": "# h\n\n## Entry Points / Code Path\nonSubmit\n"})
        self.assertTrue(out.startswith("# mini_add-post (Technical Appendix)"))
        self.assertIn("`src/pages/NewArticle.tsx:15 onSubmit`", out)
        self.assertIn("(declared `serverless.yml:90`)", out)
        self.assertLess(out.index("## backend (backend)"), out.index("## frontend (frontend)"))
        self.assertIn("### Data Flow", out)
        self.assertNotIn("# mini_add-post — backend", out)        # per-repo H1 dropped
        self.assertEqual(out.count("\n# "), 0)                    # exactly one H1


if __name__ == "__main__":
    unittest.main()
