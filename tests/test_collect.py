"""Export transforms (ported from the original collector) and the directory-level collect."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import GOOD_STORY
from pmdoc import collect as C
from pmdoc.collect import compact_whitespace, set_doc_id_heading, strip_open_questions
from pmdoc.project import Project


class TestSetDocIdHeading(unittest.TestCase):

    def test_rewrites_stale_h1_and_reports_the_old_one(self):
        text = "# blog_feed_global\n\n## Story\n\nbody\n"
        out, old = set_doc_id_heading(text, "blog_feed_personal")
        self.assertEqual(out.splitlines()[0], "# blog_feed_personal")
        self.assertEqual(old, "blog_feed_global")
        self.assertIn("## Story", out)

    def test_matching_h1_is_left_alone(self):
        text = "# blog_tags\n\nbody\n"
        out, old = set_doc_id_heading(text, "blog_tags")
        self.assertEqual(out, text)
        self.assertIsNone(old)

    def test_only_the_first_h1_is_rewritten(self):
        text = "# wrong_id\n\n# Appendix\n"
        out, _ = set_doc_id_heading(text, "right_id")
        self.assertEqual(out, "# right_id\n\n# Appendix\n")

    def test_h2_headings_are_not_mistaken_for_the_title(self):
        text = "## Story\n\nbody\n"
        out, old = set_doc_id_heading(text, "some_doc")
        self.assertEqual(out, "# some_doc\n\n## Story\n\nbody\n")
        self.assertIsNone(old)

    def test_h1_below_leading_content_is_still_found(self):
        text = "<!-- slug: delta/63.0/thing -->\n\n# wrong_id\n\nbody\n"
        out, old = set_doc_id_heading(text, "right_id")
        self.assertEqual(out, "<!-- slug: delta/63.0/thing -->\n\n# right_id\n\nbody\n")
        self.assertEqual(old, "wrong_id")

    def test_is_idempotent(self):
        once, _ = set_doc_id_heading("# wrong_id\n\nbody\n", "right_id")
        twice, old = set_doc_id_heading(once, "right_id")
        self.assertEqual(once, twice)
        self.assertIsNone(old)


class TestCompactWhitespace(unittest.TestCase):

    def test_padded_table_collapses_to_single_space_cells(self):
        text = ("| Field    | Where     | Notes            |\n"
                "| -------- | --------- | ---------------- |\n"
                "| `code`   | **Code**  | Prefixed for GST |\n")
        self.assertEqual(compact_whitespace(text),
                         "| Field | Where | Notes |\n"
                         "| --- | --- | --- |\n"
                         "| `code` | **Code** | Prefixed for GST |\n")

    def test_alignment_colons_survive_delimiter_compaction(self):
        text = "| a | b | c |\n| :------ | :----: | ------: |\n| 1 | 2 | 3 |\n"
        self.assertEqual(compact_whitespace(text).splitlines()[1], "| :--- | :---: | ---: |")

    def test_empty_cell_matches_the_untouched_compact_convention(self):
        # The story-writing skills emit `| |` for an empty cell; a padded file
        # must come back to exactly that so unformatted docs are a no-op.
        padded = "| a   | b   |     |\n| --- | --- | --- |\n|     | x   | y   |\n"
        compact = "| a | b | |\n| --- | --- | --- |\n| | x | y |\n"
        self.assertEqual(compact_whitespace(padded), compact)
        self.assertEqual(compact_whitespace(compact), compact)

    def test_indented_table_keeps_its_indent(self):
        text = "- item\n\n  | Column   | What   |\n  | -------- | ------ |\n  | **A**    | b      |\n"
        self.assertEqual(compact_whitespace(text),
                         "- item\n\n  | Column | What |\n  | --- | --- |\n  | **A** | b |\n")

    def test_escaped_pipe_inside_a_cell_is_not_a_separator(self):
        text = "| Name        | Shown as        |\n| ----------- | --------------- |\n| **Series**  | `Corolla \\| Corolla` |\n"
        out = compact_whitespace(text)
        self.assertEqual(out.splitlines()[2], "| **Series** | `Corolla \\| Corolla` |")

    def test_fenced_code_blocks_pass_through_untouched(self):
        fence = "```text\n| a    | b    |\n| ---- | ---- |\ntrailing   \n\n\n\nkeep\n```\n"
        text = "# t\n\n" + fence + "\n| a    | b    |\n"
        out = compact_whitespace(text)
        self.assertIn(fence, out)
        self.assertTrue(out.endswith("\n| a | b |\n"))

    def test_trailing_whitespace_blank_runs_and_final_newline(self):
        text = "# t   \n\n\n\n## Story\t\n\n  - nested item  \n\n\n\n"
        self.assertEqual(compact_whitespace(text), "# t\n\n## Story\n\n  - nested item\n")

    def test_non_breaking_spaces_become_plain_spaces(self):
        self.assertEqual(compact_whitespace("a\u00a0b\n"), "a b\n")

    def test_is_idempotent_and_a_noop_on_compact_text(self):
        compact = ("# doc_id\n\n## Story\n\nProse with  two inner spaces kept.\n\n"
                   "| a | b |\n| --- | --- |\n| 1 | |\n\n- list\n  - nested\n")
        self.assertEqual(compact_whitespace(compact), compact)
        padded = compact.replace("| a | b |", "| a     | b     |")
        once = compact_whitespace(padded)
        self.assertEqual(once, compact)
        self.assertEqual(compact_whitespace(once), once)


class TestStripOpenQuestions(unittest.TestCase):
    def test_section_removed_up_to_next_h2(self):
        text = "# x\n\n## Story\na\n\n## Open Questions / Uncertain Areas\n- q\n\n## Impacts\nb\n"
        self.assertEqual(strip_open_questions(text), "# x\n\n## Story\na\n\n## Impacts\nb\n")

    def test_no_section_is_noop(self):
        self.assertEqual(strip_open_questions("# x\n\n## Story\na\n"), "# x\n\n## Story\na\n")


class TestCollectDirectory(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.stories = self.root / "stories"
        self.base = self.root / "corpus"
        self.out = self.base / "mini"          # always a per-project subfolder
        for name, value in (("STORIES_DIR", self.stories), ("ROOT", self.root)):
            p = mock.patch.object(C, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.project = Project(name="mini", description="", repos=[])
        self.write("add-post", GOOD_STORY, manifest={"doc_id": "mini_add-post"})
        self.write("favorite", GOOD_STORY.replace("# mini_add-post", "# stale title"))
        self.write("broken", "## not a story\n")
        (self.stories / "mini" / "unfinished").mkdir(parents=True)

    def write(self, slug, text, manifest=None):
        d = self.stories / "mini" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "STORY.md").write_text(text)
        if manifest:
            (d / "manifest.json").write_text(json.dumps(manifest))

    def test_collects_valid_stories_with_transforms(self):
        self.assertEqual(C.collect(self.project, to=self.base), 0)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["mini_add-post.md", "mini_favorite.md"])
        fav = (self.out / "mini_favorite.md").read_text()
        self.assertTrue(fav.startswith("# mini_favorite\n"))       # H1 forced to the doc id
        self.assertNotIn("Open Questions", fav)
        self.assertIn("## Impacts", fav)
        self.assertIn("Open Questions", (self.stories / "mini/favorite/STORY.md").read_text())

    def test_single_story_and_clean(self):
        self.out.mkdir(parents=True)
        (self.out / "stale.md").write_text("old")
        (self.base / "other-project").mkdir()
        (self.base / "other-project" / "keep.md").write_text("keep")
        self.assertEqual(C.collect(self.project, to=self.base, story="add-post", clean=True), 0)
        self.assertEqual([p.name for p in self.out.iterdir()], ["mini_add-post.md"])
        self.assertTrue((self.base / "other-project" / "keep.md").exists())   # --clean is per project
        self.assertEqual(C.collect(self.project, to=self.base, story="nope"), 2)

    def test_default_keeps_other_files(self):
        self.out.mkdir(parents=True)
        (self.out / "other.md").write_text("keep")
        C.collect(self.project, to=self.base)
        self.assertTrue((self.out / "other.md").exists())


if __name__ == "__main__":
    unittest.main()
