"""
map.py — build a repo's action map with one read-only headless session.

The mapping agent explores the repo and prints a single JSON block listing every
action. Python validates the shape (repairing via --resume if needed),
normalizes ids, stamps provenance, and saves it. Links and ACTIONS.md are then
rebuilt across every repo in the project.

This is the slow, once-per-project step. Stories reuse the saved maps until a
repo changes enough to warrant `map-actions --remap`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pmdoc import templates
from pmdoc.actions.schema import (
    ACTION_KINDS, AUTH_VALUES, EFFECT_TYPES, SCHEMA_VERSION, TRIGGER_TYPES, lint_action_map,
    normalize_action_map, schema_example_json, validate_action_map,
)
from pmdoc.actions.store import (
    actions_md_path, now_iso, rebuild_derived, repo_map_path, save_repo_map,
)
from pmdoc.claude.envelope import JsonBlockError, extract_json_block
from pmdoc.claude.repair import build_json_nudge, reject, run_with_validation
from pmdoc.config import (
    CANNOT_COMPLETE_SENTINEL, DEFAULT_EFFORT, DEFAULT_MAP_MAX_TURNS, DEFAULT_MODEL,
    MAP_TIMEOUT_S, map_logs_dir,
)
from pmdoc.console import capture, emit
from pmdoc.project import Project, Repo
from pmdoc.validate import ValidationResult, detect_refusal

BOLD = "\033[1m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"
GRAY = "\033[90m"; CYAN = "\033[36m"; RESET = "\033[0m"


def _guidance_name(kind: str) -> str:
    return {"frontend": "map_guidance_frontend", "backend": "map_guidance_backend",
            "service": "map_guidance_backend", "worker": "map_guidance_worker"
            }.get(kind, "map_guidance_unknown")


def render_map_prompt(project: Project, repo: Repo) -> str:
    return templates.render(
        "map_actions", project.name,
        PROJECT_NAME=project.name,
        PROJECT_DESCRIPTION=project.description or "(no description provided)",
        REPO_NAME=repo.name,
        REPO_KIND=repo.kind,
        REPO_PATH=str(repo.path),
        REPO_HINTS=repo.hints or "(none)",
        KIND_GUIDANCE=templates.load(_guidance_name(repo.kind), project.name).strip(),
        SCHEMA_EXAMPLE=schema_example_json(),
        EFFECT_TYPES=", ".join(f"`{t}`" for t in EFFECT_TYPES),
        TRIGGER_TYPES=", ".join(f"`{t}`" for t in TRIGGER_TYPES),
        AUTH_VALUES=", ".join(f"`{t}`" for t in AUTH_VALUES),
        CANNOT_COMPLETE_SENTINEL=CANNOT_COMPLETE_SENTINEL,
    )


def validate_map_output(raw: str) -> ValidationResult:
    """Refusal first (never repaired), then JSON shape (repairable)."""
    reason = detect_refusal(raw, check_harness_refs=False)
    if reason:
        return ValidationResult(False, [reason], repairable=False)
    try:
        obj = extract_json_block(raw)
    except JsonBlockError as e:
        return ValidationResult(False, [f"no parseable JSON block: {e}"])
    return validate_action_map(obj)


def map_repo(project: Project, repo: Repo, *, model: str = DEFAULT_MODEL,
             max_turns: int = DEFAULT_MAP_MAX_TURNS, effort: str | None = DEFAULT_EFFORT,
             ) -> dict | None:
    """Map one repo. Returns the saved map, or None on failure."""
    log_dir = map_logs_dir(project.name)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{repo.name}.log"
    prompt = render_map_prompt(project, repo)
    (log_dir / f"{repo.name}.prompt.md").write_text(prompt, encoding="utf-8")

    emit(f"  {BOLD}{repo.name}{RESET} ({repo.kind}, {repo.kind_source}) — {repo.path}")
    emit(f"      {GRAY}log:  tail -f {log_path}{RESET}")

    run, text, result = run_with_validation(
        prompt, cwd=repo.path, log_path=log_path, model=model, max_turns=max_turns,
        validate_fn=validate_map_output, nudge_fn=build_json_nudge, effort=effort,
        timeout=MAP_TIMEOUT_S,
    )
    if text is None:
        reject(log_path, result, run,
               doc_path=repo_map_path(project.name, repo.name).with_suffix(".INVALID.txt"),
               run_path=repo_map_path(project.name, repo.name).with_suffix(".INVALID.json"))
        return None

    m, warnings = normalize_action_map(extract_json_block(text), repo.name)
    m = {
        "schema_version": SCHEMA_VERSION,
        "project": project.name,
        "repo": repo.name,
        "kind": repo.kind,
        "kind_source": repo.kind_source,
        "path": project.display_path(repo.path),
        "generated_at": now_iso(),
        "model": model,
        "session_id": run.session_id,
        "cost_usd": run.cost_usd,
        "num_turns": run.num_turns,
        "api_base": m.get("api_base"),
        "actions": m["actions"],
    }
    path = save_repo_map(project.name, repo.name, m)
    kinds: dict[str, int] = {}
    for a in m["actions"]:
        kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    cost = f", ${run.cost_usd:.2f}" if run.cost_usd is not None else ""
    emit(f"      {GREEN}✓ {summary}{RESET} {GRAY}({run.num_turns} turns{cost}) → {path}{RESET}")
    for w in warnings + lint_action_map(m, repo.kind):
        emit(f"      {YELLOW}⚠ {w}{RESET}")
    return m


def map_project(project: Project, *, remap: bool = False, parallel: int = 1,
                model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAP_MAX_TURNS,
                effort: str | None = DEFAULT_EFFORT) -> bool:
    """Map every repo that lacks a map (or all of them with remap), then rebuild
    links + ACTIONS.md. Returns True when every repo has a map afterwards."""
    todo = [r for r in project.repos
            if remap or not repo_map_path(project.name, r.name).exists()]
    skipped = [r.name for r in project.repos if r not in todo]

    emit(f"\n  {BOLD}Mapping actions — project {project.name}{RESET}")
    if skipped:
        emit(f"  {GRAY}already mapped (use --remap to rebuild): {', '.join(skipped)}{RESET}")

    failed: list[str] = []
    if parallel > 1 and len(todo) > 1:
        def one(repo: Repo):
            prefix = f"[{repo.name}]"
            with capture(lambda t: print("\n".join(f"  {prefix} {ln}"
                                                   for ln in t.rstrip("\n").split("\n")))):
                return repo.name, map_repo(project, repo, model=model, max_turns=max_turns,
                                           effort=effort)
        with ThreadPoolExecutor(max_workers=min(parallel, len(todo))) as pool:
            for name, m in pool.map(one, todo):
                if m is None:
                    failed.append(name)
    else:
        for repo in todo:
            if map_repo(project, repo, model=model, max_turns=max_turns, effort=effort) is None:
                failed.append(repo.name)

    if len(failed) == len(project.repos):
        emit(f"\n  {RED}✗ no repo could be mapped{RESET}")
        return False

    links = rebuild_derived(project)
    emit(f"\n  {GREEN}{BOLD}Linked{RESET} {len(links['links'])} UI call(s) to routes; "
         f"{len(links['chains'])} chain(s).")
    if links["unlinked_calls"]:
        emit(f"  {YELLOW}⚠ {len(links['unlinked_calls'])} UI call(s) matched no route:{RESET}")
        for u in links["unlinked_calls"][:10]:
            emit(f"      {GRAY}{u['from']} → {u['call']['method']} {u['call']['path']}{RESET}")
    if links["unlinked_routes"]:
        emit(f"  {GRAY}{len(links['unlinked_routes'])} route(s) have no UI caller "
             f"(fine for APIs used by other systems){RESET}")
    emit(f"  {CYAN}→ {actions_md_path(project.name)}{RESET}")
    if failed:
        emit(f"  {RED}✗ failed: {', '.join(failed)} — see logs/{project.name}/map/{RESET}")
        return False
    return True
