"""
run.py — trace each repo of a prepared story, then synthesize STORY.md.

For every repo in the manifest, one read-only headless session runs inside that
repo and prints a product doc and a technical appendix, separated by a sentinel.
Both are validated before anything is written; a formatting slip is repaired by
resuming the session, a refusal fails the repo.

Repos run in tiers (backend → worker → unknown → frontend). Each repo receives
the product docs of every completed repo in a LOWER tier as prior context, so a
frontend trace already knows the backend's rules and error cases. Because a
repo never reads from its own tier, `--parallel` runs a tier concurrently and
every trace still sees exactly what a sequential run would show it.

Progress is checkpointed in run_state.json, so `run-story --resume` continues
after a failure or a `--review` pause.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pmdoc.claude.repair import build_doc_nudge, reject, run_with_validation
from pmdoc.claude.session import ClaudeRun
from pmdoc.config import (
    DEFAULT_EFFORT, DEFAULT_MAX_TURNS, DEFAULT_MODEL, MANIFEST_NAME, PRIOR_CONTEXT_MARKER,
    ROOT, STATE_FILE_NAME, STORY_DOC_NAME, TECHNICAL_SENTINEL, story_dir, story_logs_dir,
)
from pmdoc.console import capture, emit
from pmdoc.project import Project, ProjectError
from pmdoc.validate import (
    ValidationResult, detect_refusal, strip_preamble, validate_product_doc,
    validate_raw_output, validate_technical_doc,
)

BOLD = "\033[1m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"
GRAY = "\033[90m"; CYAN = "\033[36m"; RESET = "\033[0m"


class StoryRunError(RuntimeError):
    pass


@dataclass
class RepoResult:
    name: str
    ok: bool
    product_path: str = ""
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None


def abspath(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def load_manifest(project: str, slug: str) -> dict:
    path = story_dir(project, slug) / MANIFEST_NAME
    if not path.exists():
        raise StoryRunError(f"no manifest at {path} — prepare it first: "
                            f"python cli.py prep-story {project} \"<prompt>\" --id {slug}")
    return json.loads(path.read_text(encoding="utf-8"))


def product_doc_path(s_dir: Path, repo: str) -> Path:
    return s_dir / f"{repo}.md"


def technical_doc_path(s_dir: Path, repo: str) -> Path:
    return s_dir / f"{repo}.technical.md"


def compute_tiers(repos: list[dict]) -> list[list[dict]]:
    tiers: dict[int, list[dict]] = {}
    for r in repos:
        tiers.setdefault(r["tier"], []).append(r)
    return [tiers[t] for t in sorted(tiers)]


def gather_prior_context(current: dict, repos: list[dict], completed: set[str], s_dir: Path) -> str:
    """Product docs of completed repos in strictly lower tiers, as a prompt section."""
    blocks = []
    for r in repos:
        if r["tier"] >= current["tier"] or r["name"] not in completed:
            continue
        doc = product_doc_path(s_dir, r["name"])
        if not doc.exists():
            emit(f"      {YELLOW}⚠ prior context for `{r['name']}` is missing — "
                 f"this trace will be less informed{RESET}")
            continue
        blocks.append(f"### Findings from `{r['name']}` ({r['kind']})\n\n"
                      + doc.read_text(encoding="utf-8"))
    if not blocks:
        return ""
    return ("## Prior Context (findings from already-analyzed repos)\n\n"
            "Use these findings to inform your trace — especially to map this repo onto the "
            "specific rules, messages and failure cases already identified. Do not re-explain "
            "them; build on them.\n\n" + "\n\n---\n\n".join(blocks))


def split_output(stdout: str) -> tuple[str, str]:
    """(product_doc, technical_appendix) split on the sentinel, each preamble-stripped.
    A stray sentinel printed before the product doc is ignored."""
    text = stdout.strip()
    while text.startswith(TECHNICAL_SENTINEL):
        text = text[len(TECHNICAL_SENTINEL):].strip()
    parts = text.split(TECHNICAL_SENTINEL, 1)
    product = strip_preamble(parts[0])
    technical = strip_preamble(parts[1]) if len(parts) == 2 else ""
    return product, technical


def make_trace_validator(kind: str):
    def _validate(raw: str) -> ValidationResult:
        # Refusal FIRST: an agent that declines has no reason to print a sentinel,
        # and nudging a refusal to "reprint the document" is the one thing that
        # must never happen.
        reason = detect_refusal(raw, check_harness_refs=False)
        if reason:
            return ValidationResult(False, [reason], repairable=False)
        raw_result = validate_raw_output(raw)
        if not raw_result.ok:
            return raw_result
        product, technical = split_output(raw)
        product_result = validate_product_doc(product, kind=kind)
        if not product_result.ok:
            return product_result
        return validate_technical_doc(technical)
    return _validate


def trace_repo(repo: dict, prompt_text: str, *, cwd: Path, s_dir: Path, log_path: Path,
               doc_id: str, model: str, max_turns: int,
               effort: str | None) -> tuple[bool, ClaudeRun]:
    """One read-only session for one repo, run inside it. Nothing is written
    under the real names unless both documents validate."""
    h1 = f"# {doc_id} — {repo['name']}"
    run, text, result = run_with_validation(
        prompt_text, cwd=cwd, log_path=log_path, model=model,
        max_turns=max_turns, validate_fn=make_trace_validator(repo["kind"]),
        nudge_fn=lambda errors, attempt: build_doc_nudge(errors, attempt, expect_sentinel=True,
                                                         h1_line=h1),
        effort=effort,
    )
    if text is None:
        reject(log_path, result, run, doc_path=s_dir / f"{repo['name']}.INVALID.md",
               run_path=s_dir / f"{repo['name']}.INVALID.json")
        return False, run
    product, technical = split_output(text)
    product_doc_path(s_dir, repo["name"]).write_text(product + "\n", encoding="utf-8")
    technical_doc_path(s_dir, repo["name"]).write_text(technical + "\n", encoding="utf-8")
    return True, run


def _load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"completed": [], "synthesis_done": False, "sessions": {}}


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_story(project: Project, slug: str, *, resume: bool = False, review: bool = False,
              no_synthesis: bool = False, synthesis_only: bool = False,
              model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAX_TURNS,
              effort: str | None = DEFAULT_EFFORT, parallel: int = 1) -> int:
    from pmdoc.story.synthesize import synthesize  # avoid an import cycle

    try:
        manifest = load_manifest(project.name, slug)
    except StoryRunError as e:
        emit(f"{RED}✗ {e}{RESET}")
        return 2
    repos = manifest["repos"]
    doc_id = manifest["doc_id"]
    # Where each repo lives is this machine's business (PROJECT_FOLDER), so take
    # it from the project config rather than from the committed manifest.
    try:
        repo_dirs = {r["name"]: project.repo(r["name"]).path for r in repos}
    except ProjectError as e:
        emit(f"{RED}✗ {e}{RESET}")
        return 2
    s_dir = story_dir(project.name, slug)
    l_dir = story_logs_dir(project.name, slug)
    l_dir.mkdir(parents=True, exist_ok=True)

    if synthesis_only:
        emit(f"\n  {BOLD}{doc_id} — synthesis only{RESET}")
        return 0 if synthesize(manifest, model=model, max_turns=max_turns, effort=effort) else 1

    state_path = s_dir / STATE_FILE_NAME
    if not resume and state_path.exists():
        state_path.unlink()
    state = _load_state(state_path)
    lock = threading.Lock()
    done = set(state["completed"])
    if resume:
        emit(f"  {CYAN}Resuming {doc_id} — {len(done)}/{len(repos)} repo(s) already done{RESET}")
    if review and parallel > 1:
        emit(f"  {YELLOW}--review pauses after every repo; running one at a time.{RESET}")
        parallel = 1

    emit(f"\n  {BOLD}{doc_id} — tracing {len(repos)} repo(s){RESET}  "
         f"{GRAY}model {model}, effort {effort or '-'}"
         + (f", parallel {parallel}" if parallel > 1 else "") + f"{RESET}")
    order = " → ".join("[" + ", ".join(r["name"] for r in t) + "]" for t in compute_tiers(repos))
    emit(f"  {GRAY}order: {order}{RESET}\n")

    def trace(idx: int, repo: dict, prefix: str = "") -> RepoResult:
        def body() -> RepoResult:
            emit(f"  {BOLD}[{idx + 1}/{len(repos)}] {repo['name']} ({repo['kind']}){RESET}")
            with lock:
                completed = set(done)
            prompt = abspath(repo["prompt_doc"]).read_text(encoding="utf-8").replace(
                PRIOR_CONTEXT_MARKER, gather_prior_context(repo, repos, completed, s_dir))
            log_path = l_dir / f"{repo['name']}.log"
            emit(f"      {GRAY}log:  tail -f {log_path}{RESET}")
            ok, run = trace_repo(repo, prompt, cwd=repo_dirs[repo["name"]], s_dir=s_dir,
                                 log_path=log_path, doc_id=doc_id,
                                 model=model, max_turns=max_turns, effort=effort)
            res = RepoResult(repo["name"], ok, str(product_doc_path(s_dir, repo["name"])),
                             run.session_id, run.cost_usd, run.num_turns)
            with lock:
                if run.session_id:
                    state["sessions"][repo["name"]] = run.session_id
                if ok:
                    state["completed"].append(repo["name"])
                    done.add(repo["name"])
                _save_state(state_path, state)
            if ok:
                cost = f", ${run.cost_usd:.2f}" if run.cost_usd is not None else ""
                emit(f"      {GREEN}✓ wrote {repo['name']}.md + {repo['name']}.technical.md"
                     f"{RESET} {GRAY}({run.num_turns} turns{cost}){RESET}")
            else:
                emit(f"      {RED}✗ failed — see {log_path}{RESET}")
            return res
        if not prefix:
            return body()
        with capture(lambda t: print("\n".join(f"  {prefix} {ln}"
                                               for ln in t.rstrip("\n").split("\n")))):
            return body()

    resume_hint = (f"python cli.py run-story {project.name} {slug} --resume"
                   + (" --review" if review else "") + (" --no-synthesis" if no_synthesis else "")
                   + (f" --parallel {parallel}" if parallel > 1 else ""))
    index_of = {r["name"]: i for i, r in enumerate(repos)}

    tiers = compute_tiers(repos)
    pending_all = [r for t in tiers for r in t if r["name"] not in done]
    for tier in tiers:
        todo = [r for r in tier if r["name"] not in done]
        if not todo:
            continue
        if parallel > 1 and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=min(parallel, len(todo))) as pool:
                results = list(pool.map(lambda r: trace(index_of[r["name"]], r,
                                                        f"[{r['name']}]"), todo))
        else:
            results = []
            for r in todo:
                res = trace(index_of[r["name"]], r)
                results.append(res)
                if not res.ok:
                    break
                if review and r is not pending_all[-1]:
                    emit(f"\n  {YELLOW}⏸  Paused for review.{RESET}")
                    emit(f"     Review: {res.product_path}")
                    emit(f"     Then continue: {resume_hint}")
                    return 0
        failed = [r.name for r in results if not r.ok]
        if failed:
            emit(f"\n  {RED}✗ failed: {', '.join(failed)}{RESET}")
            emit(f"  {GRAY}Fix the issue and continue with: {resume_hint}{RESET}")
            return 1

    emit(f"\n  {GREEN}{BOLD}✓ all repos traced.{RESET} {GRAY}per-repo docs in {s_dir}/{RESET}")

    if no_synthesis:
        emit(f"  {GRAY}Skipping synthesis. Build it later with: "
             f"python cli.py run-story {project.name} {slug} --synthesis-only{RESET}")
        return 0

    if not state.get("synthesis_done"):
        if not synthesize(manifest, model=model, max_turns=max_turns, effort=effort):
            emit(f"  {GRAY}Retry synthesis with: python cli.py run-story {project.name} {slug} "
                 f"--synthesis-only{RESET}")
            return 1
        state["synthesis_done"] = True
        _save_state(state_path, state)

    if state_path.exists():
        state_path.unlink()
    emit(f"\n  {GREEN}{BOLD}✅ {s_dir / STORY_DOC_NAME}{RESET}")
    return 0
