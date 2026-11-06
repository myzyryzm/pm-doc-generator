# Story Synthesis: {{DOC_ID}}

You are writing the definitive **product story** for this functionality, for a product manager / QA who has never seen the code. Below are per-repo product write-ups (one per codebase). Weave them into ONE coherent, exhaustive story — the kind of thorough ticket someone would hand to QA to write test cases from.

Rules:

- **Product language only.** No file names, paths, component/function/field/class names, API paths, table names, CSS classes, or env-var names. If a source write-up leaked any of those, restate it in user-facing terms.
{{STYLE_RULES}}
- **Weave, don't concatenate.** Combine the behind-the-scenes rules (from backend write-ups) with the on-screen behavior (from frontend write-ups) into a single narrative. The reader should never be able to tell it came from multiple repos.
- **Be exhaustive.** Enumerate every user-triggered scenario, field, message, visual state, button, and enabled/disabled condition. Do not summarize away detail.
- Merge overlapping or duplicated points; reconcile and combine the Open Questions. Where two write-ups disagree, say what the product actually does if one of them proves it, otherwise record the disagreement under Open Questions.
- **Write for retrieval.** This document is split into chunks at each `## ` heading, and long sections are split again at `### ` headings. Give `## Requirements` `### ` subsections (by screen, step, or scenario group) once it has more than about eight bullets, keeping each under ~1,800 characters. Keep other `## ` sections under ~2,000 characters, or give them `### ` subsections too. Never park real content under `## Open Questions / Uncertain Areas` — that section is removed before indexing.

## The Product

`{{PROJECT_NAME}}` — {{PROJECT_DESCRIPTION}}

## What This Story Is About

{{USER_PROMPT}}

{{NOTES_SECTION}}

## How the Pieces Connect

The request was resolved to the flow below. Each step was investigated in the repo named beside it; the write-ups that follow are those investigations.

{{CHAIN_SUMMARY}}

## Source Material (per-repo product write-ups)

<!-- ALL_PRODUCT_DOCS -->

## How Your Output Is Captured

This session is headless and **only your FINAL message is captured** — anything printed before it is discarded. Print the story once, when you are finished, and begin it with `# {{DOC_ID}}` with no preamble and no closing commentary. Never end your turn with a correction note, an addendum, or a question; that is detected as a failed run.

If the source write-ups genuinely do not support a story, make your final message a single line beginning `{{CANNOT_COMPLETE_SENTINEL}}` followed by the reason, and name which repos are missing or thin. Do not fill gaps by inference — this document feeds a search corpus, where an invented rule is worse than a missing one.

## Output

Print ONLY the final markdown document — no preamble, no commentary. Use exactly these `## ` sections, in this order:

```markdown
{{STORY_TEMPLATE}}
```
