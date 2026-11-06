"""The gate between an agent's final message and the corpus."""

import unittest

from helpers import GOOD_PRODUCT_BACKEND, GOOD_PRODUCT_FRONTEND, GOOD_STORY, good_technical
from pmdoc.config import CANNOT_COMPLETE_SENTINEL, TECHNICAL_SENTINEL
from pmdoc.validate import (
    chunk_budget_warnings, detect_refusal, strip_preamble, validate_product_doc,
    validate_raw_output, validate_story_doc, validate_technical_doc,
)

ADDENDUM = ("Both remaining investigations came back. They confirm the main trace.\n\n"
            "## Corrections to what I printed\n\n**1.** It lives in another repo.\n\n"
            "Everything else in both documents stands as printed.\n")


class PreambleTest(unittest.TestCase):
    def test_strips_short_lead_in_and_fences(self):
        self.assertTrue(strip_preamble("I now have a complete picture.\n\n" + GOOD_STORY)
                        .startswith("# mini_add-post"))
        self.assertTrue(strip_preamble("```markdown\n" + GOOD_STORY + "```").startswith("# mini"))

    def test_bounded_search(self):
        long = "\n".join(f"line {i}" for i in range(30)) + "\n# late heading\n"
        self.assertTrue(strip_preamble(long).startswith("line 0"))


class RefusalTest(unittest.TestCase):
    def test_sentinel_even_when_formatted(self):
        for text in (f"{CANNOT_COMPLETE_SENTINEL} no code", f"**{CANNOT_COMPLETE_SENTINEL}** no code",
                     f"`{CANNOT_COMPLETE_SENTINEL}` no code"):
            self.assertIsNotNone(detect_refusal(text))

    def test_prose_refusal_head_only(self):
        self.assertIsNotNone(detect_refusal("I'm unable to find this behavior in the repo."))
        late = GOOD_STORY + ("\nfiller " * 200) + "\nThe user is unable to edit it; i was unable to.\n"
        self.assertIsNone(detect_refusal(late))

    def test_harness_references(self):
        self.assertIsNotNone(detect_refusal(GOOD_STORY + "\nRun python cli.py collect.\n"))
        self.assertIsNone(detect_refusal(GOOD_STORY + "\nRun python cli.py collect.\n",
                                         check_harness_refs=False))
        self.assertIsNone(detect_refusal(GOOD_STORY + "\nStorybook lives in src/stories/.\n"))


class DocumentTest(unittest.TestCase):
    def test_good_documents_pass(self):
        self.assertTrue(validate_product_doc(GOOD_PRODUCT_BACKEND, kind="backend").ok)
        self.assertTrue(validate_product_doc(GOOD_PRODUCT_FRONTEND, kind="frontend").ok)
        self.assertTrue(validate_product_doc(GOOD_PRODUCT_BACKEND, kind="worker").ok)
        self.assertTrue(validate_technical_doc(good_technical("backend")).ok)
        self.assertTrue(validate_story_doc(GOOD_STORY).ok)

    def test_addendum_is_rejected_but_repairable(self):
        r = validate_product_doc(ADDENDUM, kind="backend")
        self.assertFalse(r.ok)
        self.assertTrue(r.repairable)

    def test_refusal_is_not_repairable(self):
        r = validate_story_doc(f"{CANNOT_COMPLETE_SENTINEL} write-ups are thin")
        self.assertFalse(r.ok)
        self.assertFalse(r.repairable)

    def test_reworded_headings_still_match(self):
        doc = GOOD_STORY.replace("## Rules & Behaviors", "## Rules and Behaviors") \
                        .replace("## Open Questions / Uncertain Areas", "## Open Questions")
        self.assertTrue(validate_story_doc(doc).ok)

    def test_missing_sentinel(self):
        self.assertFalse(validate_raw_output(GOOD_PRODUCT_BACKEND).ok)
        self.assertTrue(validate_raw_output(GOOD_PRODUCT_BACKEND + TECHNICAL_SENTINEL).ok)

    def test_h2_opener_rejected(self):
        self.assertFalse(validate_story_doc(GOOD_STORY.replace("# mini_add-post\n\n", "")).ok)


class ChunkBudgetTest(unittest.TestCase):
    def test_long_section_without_subsections_warns(self):
        doc = "# x\n\n## Requirements\n" + ("word " * 600) + "\n\n## Impacts\nshort\n"
        warnings = chunk_budget_warnings(doc)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Requirements", warnings[0])
        sub = doc.replace("## Requirements\n", "## Requirements\n### Editing\n")
        self.assertEqual(chunk_budget_warnings(sub), [])

    def test_open_questions_is_exempt(self):
        doc = "# x\n\n## Open Questions / Uncertain Areas\n" + ("word " * 600) + "\n"
        self.assertEqual(chunk_budget_warnings(doc), [])


if __name__ == "__main__":
    unittest.main()
