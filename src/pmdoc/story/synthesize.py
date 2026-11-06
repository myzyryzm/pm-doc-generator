"""
synthesize.py — weave the per-repo product docs into STORY.md, and assemble
STORY.technical.md beside it.

Synthesis is one session with NO tools: everything it needs (the per-repo
write-ups and the resolved chain) is inlined in its prompt. Its output must
pass the strictest validator before it is written, because STORY.md is the
file that reaches the corpus.

STORY.technical.md is assembled by Python, not by a model: the resolved chain
with file:line per action, then each repo's technical appendix with its
headings demoted one level. No extra cost, nothing lost in a "merge", and it is
never exported (collect only reads STORY.md).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pmdoc.actions.render import entry_ref, what
from pmdoc.claude.repair import build_doc_nudge, reject, run_with_validation
from pmdoc.claude.session import ClaudeRun
from pmdoc.config import (
    ALL_PRODUCT_DOCS_MARKER, DEFAULT_EFFORT, DEFAULT_MAX_TURNS, DEFAULT_MODEL,
    STORY_DOC_NAME, STORY_TECHNICAL_NAME, SYNTHESIS_LOG_NAME, story_dir, story_logs_dir,
)
from pmdoc.console import emit
from pmdoc.files import atomic_write_text
from pmdoc.story.run import abspath, product_doc_path, technical_doc_path
from pmdoc.validate import (
    chunk_budget_warnings, strip_preamble, validate_product_doc, validate_story_doc,
)

BOLD = "\033[1m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"
GRAY = "\033[90m"; RESET = "\033[0m"

SYNTHESIS_MAX_TURNS = 3   # no tools: one turn to write, slack for a stray retry


def synthesize(manifest: dict, *, model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAX_TURNS,
               effort: str | None = DEFAULT_EFFORT) -> bool:
    project, slug, doc_id = manifest["project"], manifest["story_slug"], manifest["doc_id"]
    s_dir, l_dir = story_dir(project, slug), story_logs_dir(project, slug)
    l_dir.mkdir(parents=True, exist_ok=True)
    repos = manifest["repos"]

    # Precondition: every repo contributed a document that actually is one. A
    # stale file from an earlier attempt can be on disk, so re-validate content,
    # not just existence — that check is free and the call after it is not.
    unusable = []
    for repo in repos:
        doc = product_doc_path(s_dir, repo["name"])
        if not doc.exists():
            unusable.append(f"{repo['name']}: no document on disk")
            continue
        result = validate_product_doc(doc.read_text(encoding="utf-8"), kind=repo["kind"])
        if not result.ok:
            unusable.append(f"{repo['name']}: {result.errors[0]}")
    if unusable:
        emit(f"  {RED}✗ refusing to synthesize — per-repo docs are not usable:{RESET}")
        for item in unusable:
            emit(f"  {GRAY}    - {item}{RESET}")
        emit(f"  {GRAY}  Re-run those repos first; a story woven from this would be "
             f"silently incomplete.{RESET}")
        return False

    prompt_path = abspath(manifest["synthesis_prompt"])
    if not prompt_path.exists():
        emit(f"  {RED}✗ no synthesis prompt at {prompt_path} — re-run prep-story{RESET}")
        return False
    blocks = [f"### Product write-up: `{r['name']}` ({r['kind']})\n\n"
              + product_doc_path(s_dir, r["name"]).read_text(encoding="utf-8") for r in repos]
    prompt_text = prompt_path.read_text(encoding="utf-8").replace(
        ALL_PRODUCT_DOCS_MARKER, "\n\n---\n\n".join(blocks))

    log_path = l_dir / SYNTHESIS_LOG_NAME
    emit(f"  {BOLD}Synthesizing {STORY_DOC_NAME}{RESET}")
    emit(f"      {GRAY}log:  tail -f {log_path}{RESET}")
    run, text, result = run_with_validation(
        prompt_text, cwd=s_dir, log_path=log_path, model=model,
        max_turns=SYNTHESIS_MAX_TURNS, tools="", allowed_tools=None, effort=effort,
        validate_fn=lambda raw: validate_story_doc(strip_preamble(raw)),
        nudge_fn=lambda errors, attempt: build_doc_nudge(errors, attempt, expect_sentinel=False,
                                                         h1_line=f"# {doc_id}"),
    )
    if text is None:
        emit(f"      {RED}✗ synthesis failed — see {log_path}{RESET}")
        reject(log_path, result, run, doc_path=s_dir / "STORY.INVALID.md")
        return False

    story = strip_preamble(text) + "\n"
    atomic_write_text(s_dir / STORY_DOC_NAME, story)
    cost = f", ${run.cost_usd:.2f}" if run.cost_usd is not None else ""
    emit(f"      {GREEN}✓ wrote {s_dir / STORY_DOC_NAME}{RESET} "
         f"{GRAY}({len(story.splitlines())} lines{cost}){RESET}")
    for w in chunk_budget_warnings(story):
        emit(f"      {YELLOW}⚠ {w}{RESET}")

    tech_docs = {r["name"]: technical_doc_path(s_dir, r["name"]).read_text(encoding="utf-8")
                 for r in repos if technical_doc_path(s_dir, r["name"]).exists()}
    atomic_write_text(s_dir / STORY_TECHNICAL_NAME, assemble_technical_appendix(manifest, tech_docs))
    emit(f"      {GRAY}  + {STORY_TECHNICAL_NAME}{RESET}")
    return True


_HEADING_RE = re.compile(r"^(#{1,5})(\s)", re.MULTILINE)
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


def demote(text: str, levels: int = 1) -> str:
    """Push every heading down `levels` (## → ###), skipping fenced code."""
    out, in_fence = [], False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            line = _HEADING_RE.sub(lambda m: m.group(1) + "#" * levels + m.group(2), line)
        out.append(line)
    return "\n".join(out)


def _drop_h1(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def assemble_technical_appendix(manifest: dict, tech_docs: dict[str, str]) -> str:
    doc_id = manifest["doc_id"]
    actions = manifest.get("actions", {})
    res = manifest.get("resolution", {})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = [f"# {doc_id} (Technical Appendix)", "",
           f"_Engineer companion to {STORY_DOC_NAME}. Never exported to the corpus. "
           f"Generated {stamp} from: \"{manifest.get('prompt', '')}\"._", "",
           "## Resolved action chain", ""]
    for c in res.get("chains", []):
        out.append(f"### {c['label']}")
        for aid in c["actions"]:
            a = actions.get(aid)
            if not a:
                out.append(f"- `{aid}`")
                continue
            repo = aid.split(":", 1)[0]
            out.append(f"- `{aid}` ({repo}, {a['kind']}) — {what(a)} — `{entry_ref(a, full=True)}`"
                       + (f" (declared `{a['declared_at']}`)" if a.get("declared_at") else ""))
        out.append("")
    if res.get("rationale"):
        out += [f"_Resolution ({res.get('method', '?')}): {res['rationale']}_", ""]
    for r in manifest["repos"]:
        out += [f"## {r['name']} ({r['kind']})", ""]
        body = tech_docs.get(r["name"])
        out += [demote(_drop_h1(body)) if body else "_No technical appendix on disk._", ""]
    return "\n".join(out).rstrip() + "\n"
