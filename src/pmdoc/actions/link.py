"""
link.py — connect actions across repos by HTTP route. Pure functions, no I/O, no LLM.

A frontend action records the calls it makes (`POST articles`, relative to its
HTTP client's base URL); a backend action records the route it serves
(`POST /api/articles`). Linking matches the two by method and normalized path,
which turns per-repo inventories into cross-repo chains: the "Publish Article"
button → the create-article route → its database write.

Normalization rules, each covering a real shape:
  * template params `${slug}`, `:slug`, `<int:slug>`, `[slug]` all become `{slug}`;
  * query strings and fragments are dropped (`articles/feed?${qs}` → `/articles/feed`);
  * a relative call path is joined onto the path part of the client's `api_base`;
  * known prefixes (`/api`, `/v1`, …) are stripped from both sides;
  * literal segments are lower-cased; parameter names never affect a match.

Matching prefers the route with the most literal segments in common, so
`GET /articles/feed` binds to the feed route, not to `/articles/{slug}`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_TEMPLATE_PARAM_RE = re.compile(r"\$\{([^}]*)\}")
_COLON_PARAM_RE = re.compile(r"(?<=/):([A-Za-z_][A-Za-z0-9_]*)")
_ANGLE_PARAM_RE = re.compile(r"<(?:[a-z]+:)?([A-Za-z_][A-Za-z0-9_]*)>")
_BRACKET_PARAM_RE = re.compile(r"\[(?:\.\.\.)?([A-Za-z_][A-Za-z0-9_]*)\]")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _param_name(expr: str) -> str:
    """Name for a `${...}` expression: its last identifier (`${encodeURIComponent(a.slug)}`
    → `slug`), or `param`."""
    idents = _IDENT_RE.findall(expr)
    return idents[-1] if idents else "param"


def _is_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def normalize_path(path: str, prefixes: list[str] | tuple[str, ...] = ()) -> str:
    p = str(path).strip()
    if "://" in p:
        p = urlparse(p).path or "/"
    p = _TEMPLATE_PARAM_RE.sub(lambda m: "{" + _param_name(m.group(1)) + "}", p)
    p = p.split("?", 1)[0].split("#", 1)[0]
    p = _COLON_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", "/" + p.lstrip("/"))
    p = _ANGLE_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", p)
    p = _BRACKET_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", p)
    segs = [s for s in p.split("/") if s]
    segs = [s if _is_param(s) else s.lower() for s in segs]
    for prefix in prefixes:
        psegs = [s.lower() for s in str(prefix).split("/") if s]
        if psegs and segs[:len(psegs)] == psegs:
            segs = segs[len(psegs):]
            break
    return "/" + "/".join(segs)


def join_base(call_path: str, api_base: str | None) -> str:
    """Resolve a relative call path against the path part of the client's base URL.
    Absolute paths and full URLs are returned unchanged."""
    p = str(call_path).strip()
    if not api_base or p.startswith("/") or "://" in p:
        return p
    base_path = urlparse(api_base).path if "://" in api_base else api_base
    return base_path.rstrip("/") + "/" + p.lstrip("/")


def infer_common_prefix(route_paths: list[str]) -> str | None:
    """The first segment every route shares (e.g. `/api`), if there is one and it
    is a literal. With fewer than two routes there is nothing to infer from."""
    firsts = set()
    for rp in route_paths:
        segs = [s for s in normalize_path(rp).split("/") if s]
        if len(segs) < 2:
            return None
        firsts.add(segs[0])
    if len(route_paths) >= 2 and len(firsts) == 1:
        (only,) = firsts
        if not _is_param(only):
            return "/" + only
    return None


@dataclass(frozen=True)
class RouteRef:
    action_id: str
    method: str
    path: str          # normalized
    order: int = 0     # declaration order, for ties


def match_route(method: str, path: str, routes: list[RouteRef]) -> RouteRef | None:
    """Best route for a normalized (method, path), or None.

    A route matches when methods agree (`ANY` matches anything) and every
    segment is equal or the route's segment is a `{param}`. Among matches, most
    literal segments wins; ties go to the first declared."""
    method = method.upper()
    segs = [s for s in path.split("/") if s]
    best, best_score = None, -1
    for r in routes:
        if r.method != method and "ANY" not in (r.method, method):
            continue
        rsegs = [s for s in r.path.split("/") if s]
        if len(rsegs) != len(segs):
            continue
        score = 0
        for a, b in zip(segs, rsegs):
            if _is_param(b):
                continue
            if _is_param(a) or a != b:
                score = -1
                break
            score += 1
        if score < 0:
            continue
        if score > best_score or (score == best_score and best and r.order < best.order):
            best, best_score = r, score
    return best


def effective_prefixes(maps: dict[str, dict], configured: list[str]) -> list[str]:
    routes = [a["route"]["path"] for m in maps.values() for a in m["actions"]
              if a["kind"] == "api-route"]
    prefixes = [p for p in configured if p]
    inferred = infer_common_prefix(routes)
    if inferred and inferred not in prefixes:
        prefixes.append(inferred)
    # Longest first, so `/api/v1` is tried before `/api`.
    return sorted(set(prefixes), key=lambda p: -len(p.strip("/").split("/")))


def build_links(maps: dict[str, dict], configured_prefixes: list[str]) -> dict:
    """Link every outbound call (from a ui-action, or a process that calls an API)
    to the api-route that serves it.

    `maps` is {repo_name: normalized action map}. Returns the body of links.json
    (without timestamps): links, unlinked_calls, unlinked_routes, chains."""
    prefixes = effective_prefixes(maps, configured_prefixes)
    routes: list[RouteRef] = []
    for m in maps.values():
        for a in m["actions"]:
            if a["kind"] == "api-route":
                routes.append(RouteRef(a["id"], a["route"]["method"].upper(),
                                       normalize_path(a["route"]["path"], prefixes), len(routes)))

    links, unlinked_calls = [], []
    for m in maps.values():
        api_base = m.get("api_base")
        for a in m["actions"]:
            if a["kind"] == "api-route":
                continue
            for c in a.get("calls", []):
                norm = normalize_path(join_base(c["path"], api_base), prefixes)
                hit = match_route(c["method"], norm, routes)
                call = {"method": c["method"].upper(), "path": norm}
                if hit:
                    links.append({"from": a["id"], "to": hit.action_id, "call": call,
                                  "route": {"method": hit.method, "path": hit.path}})
                else:
                    unlinked_calls.append({"from": a["id"], "call": call})

    called = {lk["to"] for lk in links}
    unlinked_routes = [r.action_id for r in routes if r.action_id not in called]
    body = {"prefixes_stripped": prefixes, "links": links,
            "unlinked_calls": unlinked_calls, "unlinked_routes": unlinked_routes}
    body["chains"] = build_chains(maps, body)
    return body


def build_chains(maps: dict[str, dict], links_body: dict) -> list[dict]:
    """One chain per ui-action and per process/consumer (it plus the routes it
    calls, in call order), and one per route nothing calls. A chain is the unit the
    resolver hands to the story pipeline."""
    by_id = {a["id"]: (repo, a) for repo, m in maps.items() for a in m["actions"]}
    out_links: dict[str, list[str]] = {}
    for lk in links_body["links"]:
        targets = out_links.setdefault(lk["from"], [])
        if lk["to"] not in targets:
            targets.append(lk["to"])

    chains, used = [], set()

    def add(head_id: str, members: list[str]) -> None:
        repo, head = by_id[head_id]
        cid, n = "chain:" + head_id.split(":", 2)[-1], 1
        while any(c["id"] == cid for c in chains):
            n += 1
            cid = f"chain:{head_id.split(':', 2)[-1]}-{n}"
        repos = []
        for mid in members:
            r = by_id[mid][0]
            if r not in repos:
                repos.append(r)
        chains.append({"id": cid, "label": head["label"], "area": head.get("area", ""),
                       "actions": members, "repos": repos})

    for aid, (repo, a) in by_id.items():
        if a["kind"] == "ui-action":
            members = [aid] + out_links.get(aid, [])
            add(aid, members)
            used.update(members)
    for aid, (repo, a) in by_id.items():
        if a["kind"] in ("process", "event-consumer"):
            add(aid, [aid] + [t for t in out_links.get(aid, []) if t != aid])
            used.add(aid)
    for aid, (repo, a) in by_id.items():
        if a["kind"] == "api-route" and aid not in used:
            add(aid, [aid])
    return chains
