"""
config.py — paths, document contracts, sentinels and limits shared by every stage.

The heading tuples here are rendered into the prompts AND checked by the
validator, so the two cannot drift apart. That drift is the class of bug the
validator exists to catch: an agent told to write one structure, then judged
against another.

v2: STORY_HEADINGS is a module constant today. Making it a per-project list
(`story.sections:` in projects/<name>.yaml) means threading it through
story.prep (template), story.validate (expected headings) and collect (the
Open Questions strip), and keeping the result chunker-compatible (`## `
sections, `### ` subsections for anything long).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# ── Directories ────────────────────────────────────────────────────────────
# Every tool path is anchored at ROOT, so the CLI works from any cwd. Agent
# sessions run with cwd = the repo under analysis, so nothing they do resolves
# here. Every output directory has one subfolder per project.

PROJECTS_DIR = ROOT / "projects"
PROMPTS_DIR = ROOT / "prompts"                          # local, per-project overrides
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"   # built-in, shipped
ACTIONS_DIR = ROOT / "actions"
STORIES_DIR = ROOT / "stories"
LOGS_DIR = ROOT / "logs"
CORPUS_DIR = ROOT / "corpus"

# The code being documented never lives in this repo. PROJECT_FOLDER is the
# folder holding the project's repos; relative repo paths in projects/<name>.yaml
# resolve against it. Read at call time so `PROJECT_FOLDER=... python cli.py ...`
# works for a one-off run against a different project.
PROJECT_FOLDER_ENV = "PROJECT_FOLDER"


def project_folder() -> Path | None:
    raw = os.environ.get(PROJECT_FOLDER_ENV, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def prompts_dir(project: str) -> Path:
    return PROMPTS_DIR / project


def corpus_dir(project: str, base: Path | None = None) -> Path:
    return (base or CORPUS_DIR) / project


def actions_dir(project: str) -> Path:
    return ACTIONS_DIR / project


def story_dir(project: str, slug: str) -> Path:
    return STORIES_DIR / project / slug


def story_logs_dir(project: str, slug: str) -> Path:
    return LOGS_DIR / project / slug


def map_logs_dir(project: str) -> Path:
    return LOGS_DIR / project / "map"


def doc_id_for(project: str, slug: str) -> str:
    """The corpus id: H1 of STORY.md and the collected filename.

    The RAG chunker derives module/submodule metadata by splitting the id on `_`,
    so the slug itself never contains one (see resolve.SLUG_RE)."""
    return f"{project}_{slug}"


STORY_DOC_NAME = "STORY.md"
STORY_TECHNICAL_NAME = "STORY.technical.md"
MANIFEST_NAME = "manifest.json"
STATE_FILE_NAME = "run_state.json"
SYNTHESIS_LOG_NAME = "_synthesis.log"
RESOLVE_LOG_NAME = "_resolve.log"

# ── Repo kinds ─────────────────────────────────────────────────────────────
# A repo's kind picks its prompt guidance and its tier. Tiers order the traces:
# a repo only ever receives product docs from strictly lower tiers as context, so
# a whole tier can run in parallel and still see exactly what a sequential run
# would show it.

REPO_KINDS = ("frontend", "backend", "service", "worker", "unknown")
KIND_TIER = {"backend": 0, "service": 0, "worker": 1, "unknown": 2, "frontend": 3}

# ── Agent sessions ─────────────────────────────────────────────────────────
# The tool set is restricted twice. `--tools` decides which tools EXIST in the
# session (no Write, no Edit, no Agent), and `--allowedTools` pre-approves the
# read-only Bash patterns. In headless mode any other Bash command is denied, so
# the agent physically cannot modify the repo it is reading.

AGENT_TOOLS = "Read,Grep,Glob,Bash"
ALLOWED_TOOLS = ("Read,Grep,Glob,Bash(grep:*),Bash(find:*),Bash(ls:*),Bash(cat:*),"
                 "Bash(rg:*),Bash(head:*),Bash(tail:*),Bash(wc:*)")

DEFAULT_MODEL = os.getenv("PMDOC_MODEL", "opus")
DEFAULT_RESOLVE_MODEL = os.getenv("PMDOC_RESOLVE_MODEL", "sonnet")
DEFAULT_EFFORT = os.getenv("PMDOC_EFFORT", "xhigh")
DEFAULT_MAX_TURNS = int(os.getenv("PMDOC_MAX_TURNS", "400"))
DEFAULT_MAP_MAX_TURNS = int(os.getenv("PMDOC_MAP_MAX_TURNS", "200"))

CLAUDE_TIMEOUT_S = 1800   # a full xhigh trace runs long
MAP_TIMEOUT_S = 1800
RESOLVE_TIMEOUT_S = 180   # one turn, no tools
REPAIR_TIMEOUT_S = 300    # a reprint needs no investigation

# How many times to ask a session to reprint its deliverable before giving up.
# A malformed final message is a turn-ending slip, not a capability gap, so one
# explicit diagnosis fixes it nearly every time; the second covers a stubborn
# preamble. Past that you pay to re-render a very large context.
MAX_REPAIR_ATTEMPTS = 2

# ── Sentinels and runtime markers ──────────────────────────────────────────

# Printed by a trace agent between its product doc and its technical appendix.
TECHNICAL_SENTINEL = "===TECHNICAL_APPENDIX==="
# Left in rendered prompts and replaced at run time (they depend on earlier steps).
PRIOR_CONTEXT_MARKER = "<!-- PRIOR_CONTEXT -->"
ALL_PRODUCT_DOCS_MARKER = "<!-- ALL_PRODUCT_DOCS -->"
# Sanctioned refusal channel. An agent that genuinely cannot produce the document
# says so with this prefix instead of inventing one, so the harness detects the
# refusal by exact match. These docs feed a RAG corpus, where an invented rule is
# worse than a missing document.
CANNOT_COMPLETE_SENTINEL = "CANNOT_COMPLETE:"

# ── Document structure (shared by the prompts and the validator) ───────────

PRODUCT_HEADINGS_FRONTEND = (
    "Story",
    "Where This Lives",
    "What the User Sees & Does",
    "Rules & Behaviors",
    "Edge Cases",
    "Open Questions / Uncertain Areas",
)
PRODUCT_HEADINGS_BACKEND = (
    "Story",
    "What Happens Behind the Scenes",
    "Rules & Behaviors",
    "Edge Cases",
    "Open Questions / Uncertain Areas",
)
TECHNICAL_HEADINGS = (
    "Entry Points / Code Path",
    "Data Flow",
    "Cross-Cutting Effects",
    "Open Questions / Uncertain Areas",
)
# The corpus contract. The downstream chunker splits on `## `, so each of these
# becomes its own retrievable chunk (or a run of `### ` sub-chunks when long).
STORY_HEADINGS = (
    "Story",
    "Requirements",
    "Rules & Behaviors",
    "Dependencies",
    "Impacts",
    "Open Questions / Uncertain Areas",
)
OPEN_QUESTIONS_HEADING = "Open Questions / Uncertain Areas"


def product_headings(kind: str) -> tuple[str, ...]:
    """Expected product-doc sections for a repo of this kind. Only a frontend
    has screens; every other kind documents the rules behind them."""
    return PRODUCT_HEADINGS_FRONTEND if kind == "frontend" else PRODUCT_HEADINGS_BACKEND


# ── Validation thresholds ──────────────────────────────────────────────────
# Length is a truncation backstop ONLY, never the primary gate: a 3.4k-character
# addendum sails past any length check anyone would set. Structure catches it.
MIN_PRODUCT_CHARS = 600
MIN_TECHNICAL_CHARS = 400
MIN_STORY_CHARS = 1000

# How far to hunt for the opening H1 before concluding there isn't one. Bounding
# this keeps preamble-stripping honest — it removes a two-line lead-in but will
# not chew through a long non-document looking for a heading.
MAX_PREAMBLE_LINES = 12

# The downstream chunker keeps a `## ` section whole up to this many characters,
# then splits it on `### `. Used for warnings only (see story.validate).
CHUNK_MAX_CHARS = 2200
