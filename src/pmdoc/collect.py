"""
collect.py — export a project's stories as a corpus a RAG chunker can ingest.

    python cli.py collect shop-app --to corpus/

Each stories/<project>/<slug>/STORY.md that validates becomes
<dir>/<project>/<doc_id>.md,
with three transforms on the way:

  * whitespace compaction — editor-padded tables are pure token cost, and a
    chunker that splits on character count slices padded tables mid-row;
  * the "Open Questions / Uncertain Areas" section is stripped — it is the
    honest home for everything unproven, which is exactly what must not be
    retrieved as fact;
  * the H1 is forced to the doc id — the chunker uses it as every chunk's
    title and breadcrumb.

STORY.technical.md and the per-repo docs are never exported. Sources are never
modified; only the exported copy is transformed.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from pmdoc.config import MANIFEST_NAME, ROOT, STORIES_DIR, STORY_DOC_NAME, corpus_dir, doc_id_for
from pmdoc.console import emit
from pmdoc.files import atomic_write_text
from pmdoc.project import Project
from pmdoc.validate import validate_story_doc

GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; GRAY = "\033[90m"; RESET = "\033[0m"

# Level-2 heading whose text starts with "Open Questions" (e.g. the
# "## Open Questions / Uncertain Areas" section). Dropped from collected output.
OPEN_QUESTIONS_HEADING = re.compile(r"^##\s+open questions\b", re.IGNORECASE)
# A level-1 or level-2 heading marks where the dropped section ends.
SECTION_BOUNDARY = re.compile(r"^#{1,2}\s")
# The doc's H1 title line (`# some_doc_id`).
H1_HEADING = re.compile(r"^#\s+(.*)$")
# Opening/closing line of a fenced code block (``` or ~~~, optionally indented).
CODE_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
# A GFM table cell separator: a pipe that isn't backslash-escaped.
TABLE_PIPE = re.compile(r"(?<!\\)\|")
# A cell of a table delimiter row: dashes with optional alignment colons.
DELIMITER_CELL = re.compile(r"^(:?)-+(:?)$")


def _compact_table_row(line: str) -> str:
    """Collapse the column-alignment padding an editor added to one table row.

    `| a    | bb   |` -> `| a | bb |`, and a delimiter row's dash runs shrink to
    three (`| ---- |` -> `| --- |`, alignment colons kept). Leading indent is
    preserved so tables nested under list items stay nested. Empty cells render
    as a single space (`| a | | b |`), so an already-compact file passes through
    byte-identical.
    """
    indent_len = len(line) - len(line.lstrip())
    indent, body = line[:indent_len], line.strip()
    inner = body[1:-1] if body.endswith("|") and len(body) > 1 else body[1:]
    cells = [c.strip() for c in TABLE_PIPE.split(inner)]
    if cells and all(DELIMITER_CELL.match(c) for c in cells):
        cells = [f"{m.group(1)}---{m.group(2)}"
                 for m in (DELIMITER_CELL.match(c) for c in cells)]
    return indent + "|" + "|".join(f" {c} " if c else " " for c in cells) + "|"


def compact_whitespace(text: str) -> str:
    """Undo the whitespace an editor's markdown formatter adds on save.

    Stories get hand-edited, and editors' markdown formatters pad every table
    cell to its column's width and pad delimiter rows to match. That padding is
    pure token cost in the corpus, and worse, the pipeline's chunker splits a
    section on raw character count (MAX_CHARS) — padded tables push sections
    past it and get sliced mid-row, separating header rows from their data.

    Table rows are collapsed to `| a | b |`, every other line loses trailing
    whitespace only (leading indent is structure), non-breaking spaces become
    plain spaces, runs of blank lines collapse to one, and the text ends in a
    single newline. Fenced code blocks pass through untouched. Idempotent, and
    a no-op on text that was never formatted.
    """
    out: list[str] = []
    in_fence = False
    fence_char = ""
    blank_run = 0
    for line in text.replace("\u00a0", " ").splitlines():
        fence = CODE_FENCE.match(line)
        if in_fence:
            if fence and fence.group(1)[0] == fence_char:
                in_fence = False
                out.append(line.rstrip())
            else:
                out.append(line)
            continue
        if fence:
            in_fence, fence_char = True, fence.group(1)[0]
            blank_run = 0
            out.append(line.rstrip())
            continue
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if blank_run == 1:
                out.append("")
            continue
        blank_run = 0
        if stripped.startswith("|"):
            out.append(_compact_table_row(line))
        else:
            out.append(line.rstrip())
    return "\n".join(out).rstrip() + "\n"


def strip_open_questions(text: str) -> str:
    """Remove the 'Open Questions / Uncertain Areas' section from STORY text.

    Drops from the section heading through to the next level-1/level-2 heading
    (or end of file). Returns the text unchanged if no such section exists.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if skipping:
            if SECTION_BOUNDARY.match(line):
                skipping = False
            else:
                continue
        if OPEN_QUESTIONS_HEADING.match(line):
            skipping = True
            continue
        out.append(line)
    return "".join(out).rstrip() + "\n"


def set_doc_id_heading(text: str, doc_id: str) -> tuple[str, str | None]:
    """Rewrite the doc's H1 to `doc_id`.

    A story's H1 is written by the synthesizing agent, so it can drift from the
    id it is filed under (a renamed story, a hand edit). The downstream chunker
    uses the H1 as every chunk's title and breadcrumb, so a stale H1 mislabels
    every chunk of the doc.

    Returns (text, old_title), where old_title is the previous H1 text only when
    it differed (None when it already matched, or the doc had no H1 — in which
    case one is inserted at the top).
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = H1_HEADING.match(line)
        if not match:
            continue
        old = match.group(1).strip()
        if old == doc_id:
            return text, None
        lines[i] = f"# {doc_id}\n"
        return "".join(lines), old
    return f"# {doc_id}\n\n{text}", None


def transform(text: str, doc_id: str) -> tuple[str, str | None]:
    """The export transforms, in order. Returns (text, previous_h1_if_rewritten)."""
    return set_doc_id_heading(strip_open_questions(compact_whitespace(text)), doc_id)


def _doc_id(story: Path, project: str) -> str:
    m = story / MANIFEST_NAME
    if m.exists():
        try:
            return json.loads(m.read_text(encoding="utf-8"))["doc_id"]
        except (ValueError, KeyError):
            pass
    return doc_id_for(project, story.name)


def collect(project: Project, *, to: str | Path, story: str | None = None,
            clean: bool = False) -> int:
    # Always a per-project subfolder: --clean then only ever empties this
    # project's export, never another project's.
    out_dir = corpus_dir(project.name, Path(to).expanduser().resolve())
    src_dir = STORIES_DIR / project.name
    stories = sorted(p for p in src_dir.iterdir() if p.is_dir()) if src_dir.is_dir() else []
    if story:
        stories = [p for p in stories if p.name == story]
        if not stories:
            emit(f"{RED}✗ no story {story!r} under {src_dir}{RESET}")
            return 2
    if not stories:
        emit(f"{YELLOW}no stories under {src_dir}{RESET}")
        return 1

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    for s in stories:
        path = s / STORY_DOC_NAME
        if not path.exists():
            emit(f"  {GRAY}- {s.name}: no {STORY_DOC_NAME} (run not finished?) — skipped{RESET}")
            skipped += 1
            continue
        raw = path.read_text(encoding="utf-8")
        result = validate_story_doc(raw)
        if not result.ok:
            emit(f"  {YELLOW}⚠ {s.name}: {STORY_DOC_NAME} fails validation — skipped "
                 f"({result.errors[0]}){RESET}")
            skipped += 1
            continue
        doc_id = _doc_id(s, project.name)
        text, old = transform(raw, doc_id)
        dest = out_dir / f"{doc_id}.md"
        atomic_write_text(dest, text)
        note = f" {GRAY}(H1 was {old!r}){RESET}" if old else ""
        emit(f"  {GREEN}✓{RESET} {path.relative_to(ROOT)} → {dest}{note}")
        written += 1

    emit(f"\n  {written} doc(s) written to {out_dir}" + (f", {skipped} skipped" if skipped else ""))
    return 0 if written else 1
