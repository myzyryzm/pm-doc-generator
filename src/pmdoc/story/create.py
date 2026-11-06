"""
create.py — the one-shot path: prompt → (map if needed) → resolve → prep → run.

    python cli.py create-story shop-app "explain the add to cart feature"
"""

from __future__ import annotations

from pmdoc.actions.map import map_project
from pmdoc.actions.resolve import ResolveError, print_resolution, resolve
from pmdoc.actions.store import load_maps, missing_maps, rebuild_derived
from pmdoc.config import (
    DEFAULT_EFFORT, DEFAULT_MAP_MAX_TURNS, DEFAULT_MAX_TURNS, DEFAULT_MODEL,
    DEFAULT_RESOLVE_MODEL, LOGS_DIR, RESOLVE_LOG_NAME, STATE_FILE_NAME, STORIES_DIR,
    STORY_DOC_NAME, story_dir, story_logs_dir,
)
from pmdoc.console import emit
from pmdoc.project import Project
from pmdoc.story.prep import prep_story
from pmdoc.story.run import run_story

BOLD = "\033[1m"; YELLOW = "\033[33m"; RED = "\033[31m"; GRAY = "\033[90m"; RESET = "\033[0m"


def existing_slugs(project: str) -> list[str]:
    d = STORIES_DIR / project
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def create_story(project: Project, prompt: str, *, slug: str | None = None,
                 pinned: list[str] | None = None, notes: str = "", review: bool = False,
                 dry_run: bool = False, force: bool = False, remap: bool = False,
                 no_synthesis: bool = False, prep_only: bool = False,
                 resolve_model: str = DEFAULT_RESOLVE_MODEL, model: str = DEFAULT_MODEL,
                 max_turns: int = DEFAULT_MAX_TURNS, effort: str | None = DEFAULT_EFFORT,
                 parallel: int = 1) -> int:
    # 1. Action maps — built once per project, reused by every story.
    if remap or missing_maps(project):
        if not remap:
            emit(f"  {GRAY}no action map yet for {', '.join(missing_maps(project))} — "
                 f"building it first{RESET}")
        if not map_project(project, remap=remap, parallel=parallel, model=model,
                           max_turns=DEFAULT_MAP_MAX_TURNS, effort=effort):
            return 1
    maps = load_maps(project)
    links = rebuild_derived(project)   # cheap; picks up any api_prefixes change

    # 2. Resolve the prompt to actions.
    log_path = LOGS_DIR / project.name / RESOLVE_LOG_NAME
    try:
        res = resolve(project, maps, links, prompt, pinned=pinned, slug=slug,
                      existing_slugs=existing_slugs(project.name), log_path=log_path,
                      model=resolve_model)
    except ResolveError as e:
        emit(f"{RED}✗ {e}{RESET}")
        return 2
    print_resolution(res, prompt, maps, links)

    if res.clarification and not pinned:
        emit(f"\n  {YELLOW}The question is ambiguous. Rephrase it, or pin actions with "
             f"--actions (see: python cli.py list-actions {project.name}).{RESET}")
        return 3
    if not res.action_ids:
        emit(f"\n  {RED}✗ nothing in the action map matches this question.{RESET} "
             f"{GRAY}Try list-actions --grep, or --actions.{RESET}")
        return 3
    if dry_run:
        return 0

    if review:
        answer = input(f"\n  Proceed? [Y/n, or comma-separated action ids to use instead] ").strip()
        if answer.lower() in ("n", "no"):
            return 1
        if answer and answer.lower() not in ("y", "yes"):
            try:
                res = resolve(project, maps, links, prompt, pinned=[a.strip() for a in
                              answer.split(",") if a.strip()], slug=res.story_slug,
                              log_path=log_path, model=resolve_model)
            except ResolveError as e:
                emit(f"{RED}✗ {e}{RESET}")
                return 2
            print_resolution(res, prompt, maps, links)

    # 3. Guard the story directory.
    s_dir = story_dir(project.name, res.story_slug)
    if (s_dir / STORY_DOC_NAME).exists() and not force:
        emit(f"\n  {RED}✗ {s_dir / STORY_DOC_NAME} already exists.{RESET} {GRAY}Pass --force to "
             f"regenerate it, or --id <new-slug> to write a separate story.{RESET}")
        return 2
    if (s_dir / STATE_FILE_NAME).exists() and not force:
        emit(f"\n  {YELLOW}A run for {res.story_slug} is in progress.{RESET} {GRAY}Continue it with "
             f"python cli.py run-story {project.name} {res.story_slug} --resume, or pass "
             f"--force to start over.{RESET}")
        return 2
    if force and s_dir.exists():
        for p in s_dir.iterdir():
            if p.is_file():
                p.unlink()

    # 4. Prep, then run.
    prep_story(project, res, prompt, maps, links, notes=notes)
    emit(f"\n  {GRAY}prompts: {story_logs_dir(project.name, res.story_slug) / 'prompts'}/{RESET}")
    if prep_only:
        emit(f"  {GRAY}Next: python cli.py run-story {project.name} {res.story_slug}{RESET}")
        return 0
    return run_story(project, res.story_slug, review=review, no_synthesis=no_synthesis,
                     model=model, max_turns=max_turns, effort=effort, parallel=parallel)
