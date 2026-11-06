"""
project.py — load projects/<name>.yaml into the Project every stage works from.

A project is a named set of repos plus the context an agent needs to read them
well: a one-paragraph description of the product, and per-repo hints (where
routes are declared, which file is the HTTP client, any indirection worth
knowing). Pointing the tool at a new app means writing one of these files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pmdoc.config import PROJECT_FOLDER_ENV, PROJECTS_DIR, REPO_KINDS, project_folder
from pmdoc.detect import detect_kind


class ProjectError(ValueError):
    """The project config is missing or malformed."""


@dataclass(frozen=True)
class Repo:
    name: str
    path: Path            # absolute, verified to exist
    kind: str             # final kind after config override or detection
    kind_source: str      # "config" or "detected: <evidence>"
    hints: str = ""


@dataclass(frozen=True)
class Project:
    name: str
    description: str
    repos: list[Repo]
    api_prefixes: list[str] = field(default_factory=list)
    glossary: dict = field(default_factory=dict)       # v2: domain terms → on-screen names
    style_rules: list[str] = field(default_factory=list)
    collect_out_dir: str = "corpus"
    config_path: Path | None = None
    folder: Path | None = None                         # PROJECT_FOLDER at load time

    def display_path(self, path: Path) -> str:
        """`path` relative to the project folder when it is inside it. Committed
        files (action maps, manifests) store this, not one machine's absolute path."""
        if self.folder:
            try:
                return str(path.relative_to(self.folder)) or "."
            except ValueError:
                pass
        return str(path)

    def repo(self, name: str) -> Repo:
        for r in self.repos:
            if r.name == name:
                return r
        raise ProjectError(f"project `{self.name}` has no repo `{name}` "
                           f"(repos: {', '.join(r.name for r in self.repos)})")


def available_projects() -> list[str]:
    return sorted(p.stem for p in PROJECTS_DIR.glob("*.yaml"))


def _resolve_path(raw: str, base: Path | None, cfg_path: Path) -> Path:
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        if base is None:
            raise ProjectError(
                f"{cfg_path}: repo path `{raw}` is relative, but {PROJECT_FOLDER_ENV} is not "
                f"set. Set it in .env (or inline: {PROJECT_FOLDER_ENV}=/path/to/app python "
                f"cli.py ...) to the folder that holds this project's repos.")
        if not base.is_dir():
            raise ProjectError(f"{cfg_path}: repo path `{raw}` is relative to "
                               f"{PROJECT_FOLDER_ENV}={base}, which is not a directory")
        p = base / p
    return p.resolve()


def load_project(name_or_path: str, only_repos: list[str] | None = None) -> Project:
    """Load a project by name (projects/<name>.yaml) or by an explicit YAML path.

    Relative repo paths resolve against PROJECT_FOLDER (the folder holding the
    project's repos, which never lives inside this repo), so a config can say
    `path: backend`. Absolute and ~ paths are used as-is. `only_repos` narrows
    the repo list (the CLI's --repos), preserving config order.
    """
    folder = project_folder()
    candidate = Path(name_or_path)
    if candidate.suffix in (".yaml", ".yml") and candidate.exists():
        cfg_path = candidate.resolve()
    else:
        cfg_path = PROJECTS_DIR / f"{name_or_path}.yaml"
    if not cfg_path.exists():
        known = ", ".join(available_projects()) or "(none)"
        raise ProjectError(f"no project config at {cfg_path}. Known projects: {known}")

    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProjectError(f"{cfg_path}: invalid YAML — {e}") from e
    if not isinstance(data, dict):
        raise ProjectError(f"{cfg_path}: top level must be a mapping")

    name = str(data.get("name") or cfg_path.stem)
    if "_" in name:
        # The corpus id is `<project>_<slug>`; the chunker splits ids on `_`.
        raise ProjectError(f"{cfg_path}: project name `{name}` must not contain `_` "
                           "(use hyphens)")
    raw_repos = data.get("repos") or []
    if not isinstance(raw_repos, list) or not raw_repos:
        raise ProjectError(f"{cfg_path}: `repos` must be a non-empty list")

    repos: list[Repo] = []
    seen: set[str] = set()
    for i, r in enumerate(raw_repos):
        if not isinstance(r, dict) or not r.get("path"):
            raise ProjectError(f"{cfg_path}: repos[{i}] needs at least a `path`")
        path = _resolve_path(r["path"], folder, cfg_path)
        rname = str(r.get("name") or path.name)
        if rname in seen:
            raise ProjectError(f"{cfg_path}: duplicate repo name `{rname}`")
        seen.add(rname)
        if not path.is_dir():
            raise ProjectError(f"{cfg_path}: repo `{rname}` path does not exist: {path}")

        kind = r.get("kind")
        if kind:
            if kind not in REPO_KINDS:
                raise ProjectError(f"{cfg_path}: repo `{rname}` has kind `{kind}`; "
                                   f"expected one of {', '.join(REPO_KINDS)}")
            source = "config"
        else:
            kind, evidence = detect_kind(path)
            source = "detected: " + "; ".join(evidence)
        repos.append(Repo(name=rname, path=path, kind=kind, kind_source=source,
                          hints=str(r.get("hints") or "").strip()))

    if only_repos:
        wanted = [w.strip() for w in only_repos if w.strip()]
        unknown = [w for w in wanted if w not in seen]
        if unknown:
            raise ProjectError(f"unknown repo(s) {', '.join(unknown)} — project `{name}` has "
                               f"{', '.join(r.name for r in repos)}")
        repos = [r for r in repos if r.name in wanted]

    style = data.get("style") or {}
    collect = data.get("collect") or {}
    return Project(
        name=name,
        description=str(data.get("description") or "").strip(),
        repos=repos,
        api_prefixes=[str(p) for p in (data.get("api_prefixes") or [])],
        glossary=dict(data.get("glossary") or {}),
        style_rules=[str(s) for s in (style.get("rules") or [])],
        collect_out_dir=str(collect.get("out_dir") or "corpus"),
        config_path=cfg_path,
        folder=folder,
    )
