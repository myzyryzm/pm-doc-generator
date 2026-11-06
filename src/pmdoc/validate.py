#!/usr/bin/env python3
"""
validate.py — gatekeeper between an agent's final message and the corpus.

Headless sessions return only the agent's FINAL message, so any turn that ends
with something other than the deliverable — a correction note, an addendum, a
closing question, a refusal — silently replaces the document with that text.
Nothing reaches stories/ (and so nothing reaches a RAG corpus) unless it passes
these checks.

Everything here is pure string work: no subprocess, no `claude`, no network,
so the rules that decide what is written are unit-testable.

  * Structure, not length, is the gate. A 3.4k-character addendum is past any
    length threshold anyone would set; it is caught because its first heading is
    an H2 and it has none of the required sections. Length checks are truncation
    backstops only.

  * A refusal is NOT repairable. When an agent says it lacks the material, that
    is a judgement about its evidence. Asking it to "reprint the document" is
    pressure to fabricate. Refusals fail immediately; only formatting slips are
    retried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pmdoc.config import (
    CANNOT_COMPLETE_SENTINEL, CHUNK_MAX_CHARS, TECHNICAL_SENTINEL,
    MAX_PREAMBLE_LINES, MIN_PRODUCT_CHARS, MIN_TECHNICAL_CHARS, MIN_STORY_CHARS,
    STORY_HEADINGS, TECHNICAL_HEADINGS, product_headings,
)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of checking one document.

    `repairable` drives the retry loop in claude.repair: True means the agent has the
    material but formatted its last message wrong (worth a `--resume` nudge);
    False means retrying would coerce a fabrication and the run must fail.
    """
    ok: bool
    errors: list[str] = field(default_factory=list)
    repairable: bool = True

    def __bool__(self) -> bool:
        return self.ok

    def summary(self) -> str:
        if self.ok:
            return "PASSED"
        tag = "repairable" if self.repairable else "NOT repairable"
        return f"FAILED ({tag})"


OK = ValidationResult(True)

_H1_RE = re.compile(r"^#\s+\S")
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


# ── Preamble stripping ─────────────────────────────────────────────────────

def strip_preamble(text: str) -> str:
    """Drop conversational lead-in printed before the document's opening H1.

    Agents habitually open with "I now have a complete picture. Here are the two
    documents." Fences and a stray `---` separator between the lead-in and the H1
    are dropped too.

    The search is bounded to MAX_PREAMBLE_LINES non-empty lines. That bound is the
    whole safety story: it removes a short lead-in, but it will not chew through a
    long non-document hunting for a heading it can promote. If no H1 turns up in
    the window the text is returned unchanged, and the H1 check downstream fails it.
    """
    text = _strip_code_fence(text)
    lines = text.splitlines()

    seen = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if _H1_RE.match(line):
            return "\n".join(lines[i:]).strip()
        seen += 1
        if seen >= MAX_PREAMBLE_LINES:
            break
    return text.strip()


def _strip_code_fence(text: str) -> str:
    """Unwrap a whole-document ```markdown fence, if the agent added one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


# ── Refusal detection ──────────────────────────────────────────────────────

# A genuine refusal: the agent is saying it lacks the evidence to write the
# document. Checked ONLY against the head, because a real doc may legitimately say
# "the user can't edit this field" deep in its body.
#
# Addendum phrasing deliberately does NOT belong here — see _ADDENDUM_PATTERNS.
_REFUSAL_HEAD_PATTERNS = (
    "i can't write", "i cannot write", "i can't produce", "i cannot produce",
    "i can't complete", "i cannot complete", "i was unable to", "i'm unable to",
    "the source material isn't", "the source material is not",
)
_REFUSAL_HEAD_CHARS = 500

# References to the pipeline's own machinery — an agent describing the harness
# instead of the product. Every entry must be unambiguous. Bare directory names
# (`stories/`, `logs/`) are deliberately absent: `src/stories/` is ordinary
# Storybook layout. `manifest.json` is absent too — plenty of web repos ship one.
_HARNESS_PATTERNS = (
    "python cli.py", "run_state.json", "--output-format", "--max-turns",
    "--no-synthesis", "src/pmdoc", "story.technical.md", "actions.md",
    "map-actions", "create-story", "===technical_appendix===",
)

# An agent that ended its turn on a correction note rather than the deliverable.
# This is NOT a refusal: it means the investigation succeeded and the documents
# exist earlier in the conversation, which is exactly the case a `--resume`
# reprint recovers. Detected only to give a clearer error than "missing sections".
_ADDENDUM_PATTERNS = (
    "corrections and additions", "corrections to what i printed",
    "stands as printed", "everything else in both documents",
    "both remaining investigations", "the two documents above stand",
    "everything is done:",
)


def detect_refusal(text: str, *, check_harness_refs: bool = True) -> str | None:
    """Return a reason if this is a refusal/meta-message rather than a document.

    `check_harness_refs` is off for technical appendices, which legitimately quote
    file paths and env-var names that would otherwise look like harness chatter.
    """
    body = strip_preamble(text)

    # Agents habitually wrap a literal token in markdown — `CANNOT_COMPLETE:` or
    # **CANNOT_COMPLETE:** — so lead-in formatting is stripped before matching.
    # Observed in the wild: without this the prefix check misses entirely.
    opener = body.lstrip().lstrip("`*_# ")
    if opener.startswith(CANNOT_COMPLETE_SENTINEL):
        reason = opener[len(CANNOT_COMPLETE_SENTINEL):].strip().lstrip("`*_ ")
        first_line = reason.splitlines()[0] if reason else "(no reason given)"
        return f"agent declined via {CANNOT_COMPLETE_SENTINEL} — {first_line}"

    head = body[:_REFUSAL_HEAD_CHARS].lower()
    for pattern in _REFUSAL_HEAD_PATTERNS:
        if pattern in head:
            return f"opens with meta-commentary, not a document (matched {pattern!r})"

    if check_harness_refs:
        low = body.lower()
        for pattern in _HARNESS_PATTERNS:
            if pattern in low:
                return (f"references the pipeline's own machinery ({pattern!r}) — the agent is "
                        "describing the harness, not the product")
    return None


def detect_addendum(text: str) -> str | None:
    """Return a reason if this reads as a correction note rather than a document.

    Repairable by definition: the agent finished the work and printed it earlier
    in the conversation, so resuming and asking for a full reprint recovers it —
    with the corrections already folded in.
    """
    head = strip_preamble(text)[:_REFUSAL_HEAD_CHARS].lower()
    for pattern in _ADDENDUM_PATTERNS:
        if pattern in head:
            return (f"final message is a correction note, not the deliverable "
                    f"(matched {pattern!r})")
    return None


# ── Heading helpers ────────────────────────────────────────────────────────

def _normalize(heading: str) -> str:
    """Fold a heading for tolerant comparison: lowercase, strip punctuation."""
    return re.sub(r"[^a-z0-9 ]+", " ", heading.lower()).strip()


def _headings_in(text: str) -> list[tuple[int, str]]:
    """Every markdown heading as (level, text), ignoring fenced code blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ANY_HEADING_RE.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def _match_expected(text: str, expected: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split `expected` into (found, missing) against the document's headings.

    Matching is substring-on-normalized-text in both directions, so a doc that
    writes `## Edge Cases & Quirks` still satisfies `Edge Cases`. This tolerance is
    what keeps the check from rejecting legitimate docs that reword a section.
    """
    present = [_normalize(h) for _, h in _headings_in(text)]
    found, missing = [], []
    for exp in expected:
        norm = _normalize(exp)
        if any(norm in p or p in norm for p in present):
            found.append(exp)
        else:
            missing.append(exp)
    return found, missing


def _check_document(text: str, *, expected: tuple[str, ...], min_matches: int,
                    min_chars: int, require: str | None, label: str,
                    check_harness_refs: bool = True) -> ValidationResult:
    """Shared structural checks. Order matters: refusal is fatal and checked first."""
    errors: list[str] = []

    reason = detect_refusal(text, check_harness_refs=check_harness_refs)
    if reason:
        return ValidationResult(False, [f"{label}: {reason}"], repairable=False)

    body = strip_preamble(text)
    if not body.strip():
        return ValidationResult(False, [f"{label}: empty output"], repairable=True)

    addendum = detect_addendum(body)
    if addendum:
        errors.append(f"{label}: {addendum}")

    # An H1 opener is the single highest-signal check: an addendum's first heading
    # is typically `## Corrections to what I printed`.
    if not _H1_RE.match(body.splitlines()[0]):
        first = body.splitlines()[0][:70]
        errors.append(f"{label}: does not begin with an H1 heading (starts with {first!r})")

    headings = _headings_in(body)
    if headings and headings[0][0] != 1:
        level, htext = headings[0]
        errors.append(f"{label}: first heading is H{level} (`{'#' * level} {htext[:50]}`), expected H1")

    if require:
        found_req, _ = _match_expected(body, (require,))
        if not found_req:
            errors.append(f"{label}: required section `## {require}` is missing")

    found, missing = _match_expected(body, expected)
    if len(found) < min_matches:
        errors.append(
            f"{label}: only {len(found)} of {len(expected)} expected sections present "
            f"(need at least {min_matches}; missing: {', '.join(missing)})"
        )

    if len(body) < min_chars:
        errors.append(f"{label}: too short — {len(body)} chars, expected at least {min_chars}")

    return ValidationResult(not errors, errors, repairable=True)


# ── Public validators ──────────────────────────────────────────────────────

def validate_raw_output(raw: str) -> ValidationResult:
    """Check the agent's unsplit final message for the two-document sentinel.

    Zero occurrences is the decisive, binary signature of the bug this module
    exists for: the agent's last message is not the deliverable at all.
    """
    count = raw.count(TECHNICAL_SENTINEL)
    if count == 0:
        return ValidationResult(False, [
            f"final message contains no `{TECHNICAL_SENTINEL}` sentinel — the agent did not "
            "print the two-document deliverable as its last message"
        ], repairable=True)
    return OK


def validate_product_doc(text: str, *, kind: str) -> ValidationResult:
    return _check_document(
        text,
        expected=product_headings(kind),
        min_matches=3,
        min_chars=MIN_PRODUCT_CHARS,
        require="Story",
        label="product doc",
    )


def validate_technical_doc(text: str) -> ValidationResult:
    return _check_document(
        text,
        expected=TECHNICAL_HEADINGS,
        min_matches=2,
        min_chars=MIN_TECHNICAL_CHARS,
        require=None,
        label="technical appendix",
        # The appendix's whole job is to cite paths, env vars and function names.
        check_harness_refs=False,
    )


def validate_story_doc(text: str) -> ValidationResult:
    """Strictest of the three — STORY.md is what reaches the corpus."""
    return _check_document(
        text,
        expected=STORY_HEADINGS,
        min_matches=3,
        min_chars=MIN_STORY_CHARS,
        require="Story",
        label="STORY.md",
    )


# ── Chunk-budget warnings (never fail validation) ──────────────────────────

_H2_RE = re.compile(r"^##\s+(?!#)(.+)$", re.MULTILINE)


def chunk_budget_warnings(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Sections the downstream chunker would have to character-split.

    The chunker keeps a `## ` section whole up to max_chars and otherwise splits
    it on `### `. A long section with no `### ` gets sliced mid-paragraph (or
    mid-table), so it is worth a warning — but not a rejection: the content is
    still correct, it just retrieves less cleanly."""
    warnings = []
    matches = list(_H2_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1).strip().lower().startswith("open questions"):
            continue   # stripped by collect; never reaches the chunker
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        if len(body) > max_chars and not re.search(r"^###\s", body, re.MULTILINE):
            warnings.append(f"`## {m.group(1).strip()}` is {len(body)} chars with no `### ` "
                            f"subsections; the chunker will split it mid-text")
    return warnings
