"""Every prompt template renders completely; the real prompts render for the fixture maps."""

import unittest

from helpers import fixture_maps
from pmdoc import templates
from pmdoc.config import ALL_PRODUCT_DOCS_MARKER, DEFAULT_PROMPTS_DIR, PRIOR_CONTEXT_MARKER


class TemplateTest(unittest.TestCase):
    def test_every_template_renders_with_its_placeholders(self):
        paths = sorted(DEFAULT_PROMPTS_DIR.glob("*.md"))
        self.assertGreaterEqual(len(paths), 10)
        for path in paths:
            with self.subTest(template=path.name):
                text = path.read_text()
                out = templates.render_text(text, **{k: f"<{k}>" for k in templates.placeholders(text)})
                self.assertNotIn("{{", out)

    def test_project_override_then_builtin(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mini").mkdir()
            (Path(tmp) / "mini" / "resolve.md").write_text("custom {{PROJECT_NAME}}")
            with mock.patch("pmdoc.templates.prompts_dir", lambda p: Path(tmp) / p):
                self.assertEqual(templates.render("resolve", "mini", PROJECT_NAME="x"), "custom x")
                self.assertEqual(templates.template_path("synthesis", "mini"),
                                 DEFAULT_PROMPTS_DIR / "synthesis.md")
                self.assertEqual(templates.template_path("resolve", "other"),
                                 DEFAULT_PROMPTS_DIR / "resolve.md")
        with self.assertRaises(templates.TemplateError):
            templates.load("no_such_template", "mini")

    def test_missing_variable_raises(self):
        with self.assertRaises(templates.TemplateError):
            templates.render_text("hi {{NAME}}")

    def test_values_are_not_re_expanded(self):
        self.assertEqual(templates.render_text("{{A}}", A="{{B}}"), "{{B}}")

    def test_runtime_markers_survive(self):
        trace = templates.load("trace_repo")
        synth = templates.load("synthesis")
        self.assertIn(PRIOR_CONTEXT_MARKER, trace)
        self.assertIn(ALL_PRODUCT_DOCS_MARKER, synth)


class RealPromptsTest(unittest.TestCase):
    """The full prep path for a fixture resolution, without any model call."""

    def test_prep_story_renders_everything(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from pmdoc.actions.link import build_links
        from pmdoc.actions.resolve import expand, Resolution
        from pmdoc.project import Project, Repo
        from pmdoc.story import prep

        maps = fixture_maps()
        links = build_links(maps, ["/api"])
        kinds = {"backend": "backend", "frontend": "frontend"}
        ids, chains, repos, entry = expand(["frontend:ui:publish-article"], maps, links, kinds)
        res = Resolution("add-post", "mini_add-post", ["frontend:ui:publish-article"], ids,
                         chains, repos, entry, rationale="r")
        project = Project("mini", "A blog.", [Repo("backend", Path("/tmp"), "backend", "config"),
                                              Repo("frontend", Path("/tmp"), "frontend", "config")])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(prep, "story_dir", lambda p, s: Path(tmp) / "s"), \
                    mock.patch.object(prep, "story_logs_dir", lambda p, s: Path(tmp) / "l"):
                manifest = prep.prep_story(project, res, "explain the add post feature", maps, links)
                fe = (Path(tmp) / "l/prompts/frontend.md").read_text()
                be = (Path(tmp) / "l/prompts/backend.md").read_text()
                syn = (Path(tmp) / "l/prompts/_synthesis.md").read_text()
        self.assertEqual([r["name"] for r in manifest["repos"]], ["backend", "frontend"])
        self.assertIn("`src/pages/NewArticle.tsx:15 onSubmit`", fe)
        self.assertIn("served by **Create article** in `backend`", fe)
        self.assertIn("called by: **Publish Article** in `frontend`", be)
        self.assertIn("## What the User Sees & Does", fe)
        self.assertIn("## What Happens Behind the Scenes", be)
        self.assertIn("# mini_add-post — backend", be)
        for heading in ("## Story", "## Requirements", "## Rules & Behaviors", "## Dependencies",
                        "## Impacts", "## Open Questions / Uncertain Areas"):
            self.assertIn(heading, syn)
        for text in (fe, be, syn):
            self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main()
