#!/usr/bin/env python3
"""
cli.py — pm-doc-generator.

    python cli.py map-actions  <project> [--remap] [--repos a,b] [--parallel N]
    python cli.py list-actions <project> [--grep TEXT] [--kind KIND] [--repo NAME]
    python cli.py show-action  <project> <action-id>
    python cli.py create-story <project> "<prompt>" [--id SLUG] [--actions a,b] [--review] [--dry-run] ...
    python cli.py prep-story   <project> "<prompt>" [--id SLUG] [--actions a,b]
    python cli.py run-story    <project> <slug> [--resume] [--synthesis-only] ...
    python cli.py collect      <project> [--to DIR] [--story SLUG] [--clean]

Every command takes a project name (projects/<name>.yaml) or a path to a YAML file.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

RED = "\033[31m"; GRAY = "\033[90m"; RESET = "\033[0m"


def _split(csv: str | None) -> list[str] | None:
    return [s.strip() for s in csv.split(",") if s.strip()] if csv else None


def _project(args):
    from pmdoc.project import load_project
    return load_project(args.project, only_repos=_split(getattr(args, "repos", None)))


# ── Commands ───────────────────────────────────────────────────────────────

def cmd_map_actions(args) -> int:
    from pmdoc.actions.map import map_project
    ok = map_project(_project(args), remap=args.remap, parallel=args.parallel,
                     model=args.model, max_turns=args.max_turns, effort=args.effort)
    return 0 if ok else 1


def cmd_list_actions(args) -> int:
    from pmdoc.actions.render import action_line, what
    from pmdoc.actions.store import load_maps
    project = _project(args)
    maps = load_maps(project)
    needle = (args.grep or "").lower()
    n = 0
    for repo, m in maps.items():
        if args.repo and repo != args.repo:
            continue
        for a in m["actions"]:
            if args.kind and a["kind"] != args.kind:
                continue
            hay = " ".join([a["id"], a["label"], a.get("summary", ""), a.get("area", ""),
                            " ".join(a.get("aliases", [])), what(a)]).lower()
            if needle and needle not in hay:
                continue
            print(f"  {action_line(a, repo)}  {GRAY}{a['label']}{RESET}")
            n += 1
    print(f"\n  {n} action(s)")
    return 0


def cmd_show_action(args) -> int:
    import json
    from pmdoc.actions.render import format_chain
    from pmdoc.actions.store import load_links, load_maps
    project = _project(args)
    maps = load_maps(project)
    links = load_links(project)
    by_id = {a["id"]: (repo, a) for repo, m in maps.items() for a in m["actions"]}
    if args.action_id not in by_id:
        close = [i for i in by_id if args.action_id.split(":")[-1] in i][:8]
        print(f"{RED}no action {args.action_id}{RESET}" + (f" — did you mean: {', '.join(close)}"
                                                            if close else ""))
        return 1
    repo, a = by_id[args.action_id]
    print(json.dumps(a, indent=2, ensure_ascii=False))
    for c in links["chains"]:
        if args.action_id in c["actions"]:
            print(f"\n  chain {c['id']}  [{c.get('area', '')}]")
            print("\n".join(format_chain(c, by_id, links["links"])))
    return 0


def _story_kwargs(args) -> dict:
    return dict(model=args.model, max_turns=args.max_turns, effort=args.effort,
                parallel=args.parallel)


def cmd_create_story(args) -> int:
    from pmdoc.story.create import create_story
    return create_story(_project(args), args.prompt, slug=args.id, pinned=_split(args.actions),
                        notes=args.notes, review=args.review, dry_run=args.dry_run,
                        force=args.force, remap=args.remap, no_synthesis=args.no_synthesis,
                        resolve_model=args.resolve_model, **_story_kwargs(args))


def cmd_prep_story(args) -> int:
    from pmdoc.story.create import create_story
    return create_story(_project(args), args.prompt, slug=args.id, pinned=_split(args.actions),
                        notes=args.notes, review=args.review, force=args.force,
                        prep_only=True, resolve_model=args.resolve_model,
                        **_story_kwargs(args))


def cmd_run_story(args) -> int:
    from pmdoc.story.run import run_story
    from pmdoc.project import load_project
    return run_story(load_project(args.project), args.slug, resume=args.resume,
                     review=args.review, no_synthesis=args.no_synthesis,
                     synthesis_only=args.synthesis_only, **_story_kwargs(args))


def cmd_collect(args) -> int:
    from pmdoc.collect import collect
    from pmdoc.config import ROOT
    from pmdoc.project import load_project
    project = load_project(args.project)
    # An explicit --to is relative to where you run the command; the config
    # default is relative to this repo. Either way the export lands in a
    # <project>/ subfolder of it.
    to = Path(args.to) if args.to else ROOT / project.collect_out_dir
    return collect(project, to=to, story=args.story, clean=args.clean)


# ── Parser ─────────────────────────────────────────────────────────────────

def _session_flags(p, *, map_stage: bool = False) -> None:
    from pmdoc.config import (DEFAULT_EFFORT, DEFAULT_MAP_MAX_TURNS, DEFAULT_MAX_TURNS,
                              DEFAULT_MODEL)
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"agent model (default {DEFAULT_MODEL})")
    p.add_argument("--max-turns", type=int,
                   default=DEFAULT_MAP_MAX_TURNS if map_stage else DEFAULT_MAX_TURNS)
    p.add_argument("--effort", default=DEFAULT_EFFORT,
                   help=f"reasoning effort: low|medium|high|xhigh|max (default {DEFAULT_EFFORT})")
    p.add_argument("--parallel", type=int, default=1,
                   help="run up to N repo sessions at once")


def build_parser() -> argparse.ArgumentParser:
    from pmdoc.config import DEFAULT_RESOLVE_MODEL
    ap = argparse.ArgumentParser(prog="cli.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("map-actions", help="inventory every action in each repo (slow; cached)")
    p.add_argument("project")
    p.add_argument("--remap", action="store_true", help="rebuild maps that already exist")
    p.add_argument("--repos", help="comma-separated subset of the project's repos")
    _session_flags(p, map_stage=True)
    p.set_defaults(func=cmd_map_actions)

    p = sub.add_parser("list-actions", help="list mapped actions")
    p.add_argument("project")
    p.add_argument("--grep", help="substring filter over id/label/aliases/summary")
    p.add_argument("--kind", choices=["ui-action", "api-route", "process", "event-consumer"])
    p.add_argument("--repo")
    p.set_defaults(func=cmd_list_actions)

    p = sub.add_parser("show-action", help="print one action and the chains it belongs to")
    p.add_argument("project")
    p.add_argument("action_id")
    p.set_defaults(func=cmd_show_action)

    for name, func, helptext in (
            ("create-story", cmd_create_story, "prompt → resolve → trace each repo → STORY.md"),
            ("prep-story", cmd_prep_story, "resolve + render prompts and manifest only (no traces)")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("project")
        p.add_argument("prompt", help='what to explain, e.g. "explain the add post feature"')
        p.add_argument("--id", help="story slug (default: suggested by the resolver)")
        p.add_argument("--actions", help="pin action ids instead of resolving the prompt")
        p.add_argument("--notes", default="", help="extra guidance passed to every agent")
        p.add_argument("--review", action="store_true",
                       help="confirm the resolved actions, and pause after each repo trace")
        p.add_argument("--force", action="store_true", help="overwrite an existing story")
        p.add_argument("--resolve-model", default=DEFAULT_RESOLVE_MODEL)
        if name == "create-story":
            p.add_argument("--dry-run", action="store_true",
                           help="print the resolution and stop")
            p.add_argument("--remap", action="store_true",
                           help="rebuild the action maps before resolving")
            p.add_argument("--no-synthesis", action="store_true")
        _session_flags(p)
        p.set_defaults(func=func)

    p = sub.add_parser("run-story", help="run (or resume) the traces + synthesis for a prepared story")
    p.add_argument("project")
    p.add_argument("slug")
    p.add_argument("--resume", action="store_true", help="skip repos already completed")
    p.add_argument("--review", action="store_true", help="pause after each repo")
    p.add_argument("--no-synthesis", action="store_true")
    p.add_argument("--synthesis-only", action="store_true",
                   help="rebuild STORY.md from existing per-repo docs")
    _session_flags(p)
    p.set_defaults(func=cmd_run_story)

    p = sub.add_parser("collect", help="export STORY.md files as a chunker-ready corpus")
    p.add_argument("project")
    p.add_argument("--to", help="output directory; the export goes in its <project>/ subfolder "
                        "(default: the project's collect.out_dir)")
    p.add_argument("--story", help="collect only this story slug")
    p.add_argument("--clean", action="store_true", help="empty this project's export folder first")
    p.set_defaults(func=cmd_collect)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def on_sigint(signum, frame):
        from pmdoc.claude.session import terminate_sessions
        n = terminate_sessions()
        print(f"\n{RED}interrupted{RESET}" + (f" — stopped {n} claude session(s)" if n else ""),
              file=sys.stderr)
        sys.exit(130)

    signal.signal(signal.SIGINT, on_sigint)
    from pmdoc.project import ProjectError
    from pmdoc.actions.store import ActionMapError
    try:
        return args.func(args) or 0
    except (ProjectError, ActionMapError) as e:
        print(f"{RED}✗ {e}{RESET}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
