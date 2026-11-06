"""
store.py — where action maps live on disk, and loading them back.

    actions/<project>/<repo>.json   one per repo (written by map-actions)
    actions/<project>/links.json    cross-repo links + chains (derived, rebuilt every time)
    actions/<project>/ACTIONS.md    the human view (derived)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pmdoc.actions.link import build_links
from pmdoc.actions.render import render_actions_md
from pmdoc.config import actions_dir
from pmdoc.files import atomic_write_text
from pmdoc.project import Project


class ActionMapError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_map_path(project: str, repo: str) -> Path:
    return actions_dir(project) / f"{repo}.json"


def links_path(project: str) -> Path:
    return actions_dir(project) / "links.json"


def actions_md_path(project: str) -> Path:
    return actions_dir(project) / "ACTIONS.md"


def missing_maps(project: Project) -> list[str]:
    return [r.name for r in project.repos if not repo_map_path(project.name, r.name).exists()]


def load_maps(project: Project) -> dict[str, dict]:
    """{repo: map} for every repo in the project, in config order. Raises if any is missing."""
    missing = missing_maps(project)
    if missing:
        raise ActionMapError(f"no action map for {', '.join(missing)} — run: "
                             f"python cli.py map-actions {project.name}")
    return {r.name: json.loads(repo_map_path(project.name, r.name).read_text(encoding="utf-8"))
            for r in project.repos}


def load_links(project: Project) -> dict:
    p = links_path(project.name)
    if not p.exists():
        return rebuild_derived(project)
    return json.loads(p.read_text(encoding="utf-8"))


def save_repo_map(project: str, repo: str, m: dict) -> Path:
    p = repo_map_path(project, repo)
    atomic_write_text(p, json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    return p


def rebuild_derived(project: Project) -> dict:
    """Recompute links.json and ACTIONS.md from whatever repo maps exist. Cheap and
    deterministic, so it runs after every mapping and whenever links are stale."""
    maps = {r.name: json.loads(repo_map_path(project.name, r.name).read_text(encoding="utf-8"))
            for r in project.repos if repo_map_path(project.name, r.name).exists()}
    if not maps:
        raise ActionMapError(f"no action maps for project {project.name}")
    body = build_links(maps, project.api_prefixes)
    stamp = now_iso()
    links = {"schema_version": 1, "project": project.name, "generated_at": stamp, **body}
    atomic_write_text(links_path(project.name), json.dumps(links, indent=2) + "\n")
    atomic_write_text(actions_md_path(project.name),
                      render_actions_md(project.name, maps, links, stamp))
    return links
