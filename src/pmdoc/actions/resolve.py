"""
resolve.py — turn a product question into the actions a story should trace.

"explain the add to cart feature" names nothing in the code. Resolution maps it
onto the action map: the "Add to cart" button in the frontend, the add-to-cart
route in the backend, and the chain that connects them. Those
become the "Start here" entry points of each repo's trace.

Three paths, in order of preference:
  * pinned  — the caller named action ids (`--actions`); no model call.
  * claude  — one short session with NO tools reads a compact action index and
              picks the smallest covering set. Cheap and seconds long.
  * prefilter — if that call fails twice, the deterministic token-overlap ranker
              picks the best chain, with a warning.

The prefilter also keeps the index small for big apps: only the top candidates
(plus their chain-mates) are shown to the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pmdoc import templates
from pmdoc.actions.render import entry_ref, format_chain, what
from pmdoc.claude import session
from pmdoc.claude.envelope import JsonBlockError, extract_json_block
from pmdoc.claude.repair import build_json_nudge
from pmdoc.config import KIND_TIER, RESOLVE_TIMEOUT_S, doc_id_for
from pmdoc.console import emit
from pmdoc.project import Project
from pmdoc.validate import ValidationResult

BOLD = "\033[1m"; GRAY = "\033[90m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; RESET = "\033[0m"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
PREFILTER_LIMIT = 60


class ResolveError(RuntimeError):
    pass


@dataclass
class Resolution:
    story_slug: str
    doc_id: str
    matched_action_ids: list[str]
    action_ids: list[str]                 # matched + chain members, the full traced set
    chains: list[dict]
    repos_involved: list[str]             # ordered by tier
    entry_points: dict[str, list[dict]]   # repo → action objects
    rationale: str = ""
    clarification: str | None = None
    method: str = "claude"
    candidates_shown: int = 0
    cost_usd: float | None = None

    def as_dict(self) -> dict:
        return {"method": self.method, "story_slug": self.story_slug, "doc_id": self.doc_id,
                "matched_action_ids": self.matched_action_ids, "action_ids": self.action_ids,
                "chains": self.chains, "repos_involved": self.repos_involved,
                "rationale": self.rationale, "candidates_shown": self.candidates_shown}


# ── Index + prefilter ──────────────────────────────────────────────────────

_STOPWORDS = set("""
a an the and or of to in on for with from by at as is are was be been it its this that
these those i me my we our you your how what when where which who why does do did can
could should would will explain describe documentation doc docs feature features
functionality work works working happen happens happening flow process story about
tell show walk through via using use user users page please need want get
""".split())
_WORD_RE = re.compile(r"[a-z0-9]+")


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str) -> set[str]:
    return {_stem(w) for w in _WORD_RE.findall(str(text).lower()) if w not in _STOPWORDS}


def build_index(maps: dict[str, dict], links: dict) -> list[dict]:
    chain_of: dict[str, list[str]] = {}
    for c in links.get("chains", []):
        for aid in c["actions"]:
            chain_of.setdefault(aid, []).append(c["id"])
    index = []
    for repo, m in maps.items():
        for a in m["actions"]:
            index.append({
                "id": a["id"], "kind": a["kind"], "repo": repo, "area": a.get("area", ""),
                "label": a["label"], "trigger": what(a), "aliases": a.get("aliases", []),
                "summary": (a.get("summary") or "")[:160], "chains": chain_of.get(a["id"], []),
            })
    return index


def score(prompt: str, entry: dict) -> float:
    ptoks = tokens(prompt)
    if not ptoks:
        return 0.0
    strong = tokens(entry["label"]) | tokens(" ".join(entry["aliases"]))
    medium = tokens(entry["area"])
    weak = tokens(entry["summary"]) | tokens(entry["id"]) | tokens(entry["trigger"])
    s = 0.0
    for t in ptoks:
        s += 3 if t in strong else 2 if t in medium else 1 if t in weak else 0
    norm_prompt = " " + " ".join(_WORD_RE.findall(prompt.lower())) + " "
    for phrase in [entry["label"], *entry["aliases"]]:
        p = " ".join(_WORD_RE.findall(phrase.lower()))
        if len(p.split()) >= 2 and f" {p} " in norm_prompt:
            s += 5
    return s


def prefilter(prompt: str, index: list[dict], limit: int = PREFILTER_LIMIT) -> list[dict]:
    """Top `limit` entries by score, plus every chain-mate of a kept entry.
    A small index is returned whole: the model is better at this than overlap."""
    if len(index) <= limit:
        return index
    ranked = sorted(index, key=lambda e: -score(prompt, e))
    keep = {e["id"] for e in ranked[:limit]}
    chains = {c for e in ranked[:limit] for c in e["chains"]}
    keep |= {e["id"] for e in index if set(e["chains"]) & chains}
    return [e for e in index if e["id"] in keep]


# ── Expansion ──────────────────────────────────────────────────────────────

def expand(matched: list[str], maps: dict[str, dict], links: dict, repo_kinds: dict[str, str]):
    """Matched ids → (all ids, chains, repos in tier order, entry points per repo).

    A matched chain head (a UI control, a process) pulls in every action its
    chain reaches; a matched route that no matched head covers is traced alone."""
    by_id = {a["id"]: (repo, a) for repo, m in maps.items() for a in m["actions"]}
    chains, ids = [], []
    for c in links.get("chains", []):
        if c["actions"][0] in matched:
            chains.append(c)
            ids += [a for a in c["actions"] if a not in ids]
    for aid in matched:
        if aid not in ids:
            ids.append(aid)
            if not any(aid in c["actions"] for c in chains):
                chains.append({"id": f"chain:{aid.split(':', 2)[-1]}", "label": by_id[aid][1]["label"],
                               "area": by_id[aid][1].get("area", ""), "actions": [aid],
                               "repos": [by_id[aid][0]]})
    entry: dict[str, list[dict]] = {}
    for aid in ids:
        repo, a = by_id[aid]
        entry.setdefault(repo, []).append(a)
    order = list(maps)
    repos = sorted(entry, key=lambda r: (KIND_TIER.get(repo_kinds.get(r, "unknown"), 2),
                                         order.index(r)))
    return ids, chains, repos, entry


# ── Slugs ──────────────────────────────────────────────────────────────────

def make_slug(text: str) -> str:
    words = [w for w in _WORD_RE.findall(str(text).lower()) if w not in _STOPWORDS][:4]
    s = "-".join(words)[:40].strip("-")
    return s if SLUG_RE.match(s or "") else "story"


def clean_slug(suggestion: str | None, prompt: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(suggestion or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:40].strip("-")
    return s if SLUG_RE.match(s) else make_slug(prompt)


# ── Claude ─────────────────────────────────────────────────────────────────

def _validate_resolution(raw: str, allowed: set[str]) -> ValidationResult:
    try:
        obj = extract_json_block(raw)
    except JsonBlockError as e:
        return ValidationResult(False, [f"no parseable JSON block: {e}"])
    ids = obj.get("matched_action_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return ValidationResult(False, ["`matched_action_ids` must be a list of id strings"])
    unknown = [i for i in ids if i not in allowed]
    if unknown:
        return ValidationResult(False, [f"unknown action id(s): {', '.join(unknown[:10])} — "
                                        "use ids exactly as listed in the index"])
    if not ids and not obj.get("clarification_needed"):
        return ValidationResult(False, ["no actions matched and no clarification asked — "
                                        "pick the closest actions or ask a question"])
    return ValidationResult(True)


def resolve_with_claude(prompt: str, candidates: list[dict], project: Project,
                        existing_slugs: list[str], *, log_path: Path, model: str) -> tuple[dict, float | None]:
    text = templates.render(
        "resolve", project.name,
        PROJECT_NAME=project.name,
        PROJECT_DESCRIPTION=project.description or "(no description provided)",
        USER_PROMPT=prompt,
        ACTION_INDEX="\n".join(json.dumps(e, ensure_ascii=False) for e in candidates),
        EXISTING_SLUGS=", ".join(existing_slugs) or "(none)",
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    allowed = {e["id"] for e in candidates}
    run = session.invoke(text, cwd=log_path.parent, log_path=log_path, model=model,
                         max_turns=1, tools="", allowed_tools=None, effort=None,
                         timeout=RESOLVE_TIMEOUT_S)
    cost = run.cost_usd
    for attempt in range(2):
        if not run.ok:
            raise ResolveError(run.error or "resolve session failed")
        result = _validate_resolution(run.text, allowed)
        if result.ok:
            return extract_json_block(run.text), cost
        if attempt == 1 or not run.session_id:
            raise ResolveError("; ".join(result.errors))
        run = session.resume(run.session_id, build_json_nudge(result.errors, 1),
                             cwd=log_path.parent, log_path=log_path, model=model, tools="",
                             allowed_tools=None, timeout=RESOLVE_TIMEOUT_S)
        if run.cost_usd is not None:
            cost = (cost or 0) + run.cost_usd
    raise ResolveError("resolution did not validate")


# ── Entry point ────────────────────────────────────────────────────────────

def resolve(project: Project, maps: dict[str, dict], links: dict, prompt: str, *,
            pinned: list[str] | None = None, slug: str | None = None,
            existing_slugs: list[str] = [], log_path: Path, model: str) -> Resolution:
    by_id = {a["id"]: a for m in maps.values() for a in m["actions"]}
    repo_kinds = {r.name: r.kind for r in project.repos}
    index = build_index(maps, links)
    candidates = prefilter(prompt, index)
    rationale, clarification, method, cost, suggestion = "", None, "claude", None, None

    if pinned:
        unknown = [i for i in pinned if i not in by_id]
        if unknown:
            raise ResolveError(f"unknown action id(s): {', '.join(unknown)} — see "
                               f"python cli.py list-actions {project.name}")
        matched, method, rationale = list(pinned), "pinned", "pinned with --actions"
    else:
        try:
            obj, cost = resolve_with_claude(prompt, candidates, project, list(existing_slugs),
                                            log_path=log_path, model=model)
            matched = list(dict.fromkeys(obj.get("matched_action_ids") or []))
            rationale = str(obj.get("rationale") or "")
            clarification = obj.get("clarification_needed") or None
            suggestion = obj.get("story_id_suggestion")
        except ResolveError as e:
            emit(f"  {YELLOW}⚠ resolver failed ({e}); falling back to keyword ranking{RESET}")
            matched, method = fallback_match(prompt, index), "prefilter"
            rationale = "keyword overlap between the prompt and action labels/aliases"

    ids, chains, repos, entry = expand(matched, maps, links, repo_kinds) if matched else ([], [], [], {})
    s = slug or clean_slug(suggestion, prompt)
    if slug and not SLUG_RE.match(slug):
        raise ResolveError(f"--id {slug!r} must match {SLUG_RE.pattern} (lowercase, hyphens, no `_`)")
    return Resolution(story_slug=s, doc_id=doc_id_for(project.name, s), matched_action_ids=matched,
                      action_ids=ids, chains=chains, repos_involved=repos, entry_points=entry,
                      rationale=rationale, clarification=clarification, method=method,
                      candidates_shown=len(candidates), cost_usd=cost)


def fallback_match(prompt: str, index: list[dict]) -> list[str]:
    """Best-scoring chain heads (ties included), preferring user-triggered actions."""
    heads = [e for e in index if e["kind"] in ("ui-action", "process", "event-consumer")] or index
    scored = sorted(((score(prompt, e), e) for e in heads), key=lambda t: -t[0])
    if not scored or scored[0][0] <= 0:
        return []
    top = scored[0][0]
    return [e["id"] for s, e in scored if s == top][:3]


def print_resolution(res: Resolution, prompt: str, maps: dict[str, dict], links: dict) -> None:
    by_id = {a["id"]: (repo, a) for repo, m in maps.items() for a in m["actions"]}
    via = {"claude": f"via claude, {res.candidates_shown} candidates", "pinned": "pinned",
           "prefilter": "via keyword fallback"}[res.method]
    emit(f"\n  {BOLD}Resolved:{RESET} \"{prompt}\"")
    emit(f"    story: {CYAN}{res.story_slug}{RESET}   doc id: {res.doc_id}   ({via})")
    for c in res.chains:
        emit(f"    {BOLD}chain{RESET} {c['label']}  {GRAY}[{c.get('area', '')}]{RESET}")
        emit("\n".join(format_chain(c, by_id, links.get("links", []), indent="      ")))
    if res.repos_involved:
        emit(f"    repos: {' → '.join(res.repos_involved)}")
    if res.rationale:
        emit(f"    {GRAY}why: {res.rationale}{RESET}")
    if res.clarification:
        emit(f"    {YELLOW}? {res.clarification}{RESET}")
