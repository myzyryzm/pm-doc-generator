"""
render.py — human-readable views of an action map (ACTIONS.md and console lines).
"""

from __future__ import annotations

from pathlib import PurePosixPath


def entry_ref(a: dict, full: bool = False) -> str:
    e = a.get("entry") or {}
    f = e.get("file") or "?"
    if not full:
        f = PurePosixPath(f).name
    line = f":{e['line']}" if e.get("line") else ""
    sym = f" {e['symbol']}" if e.get("symbol") else ""
    return f"{f}{line}{sym}"


def what(a: dict) -> str:
    """The trigger side of an action, in one phrase."""
    k = a["kind"]
    if k == "ui-action":
        s = a.get("surface") or {}
        bits = [s.get("screen"), s.get("control")]
        return ", ".join(b for b in bits if b) or a["label"]
    if k == "api-route":
        return f"{a['route']['method']} {a['route']['path']}"
    t = a.get("trigger") or {}
    return f"{t.get('type', '?')}: {t.get('detail', '')}".rstrip(": ")


def effects_phrase(a: dict) -> str:
    effs = [e for e in a.get("effects", []) if e.get("type") not in ("ui",)]
    return "; ".join(f"{e['type']} {e.get('target', '')}".strip() for e in effs)


def action_line(a: dict, repo: str) -> str:
    return f"{repo:<10} {a['kind']:<14} {a['id']:<44} {entry_ref(a)}"


def format_chain(chain: dict, by_id: dict[str, tuple[str, dict]], links: list[dict],
                 indent: str = "    ") -> list[str]:
    """Console lines for one chain: one line per member action."""
    call_to = {}
    for lk in links:
        call_to.setdefault(lk["from"], []).append(lk)
    lines = []
    for aid in chain["actions"]:
        repo, a = by_id[aid]
        tail = ""
        if a["kind"] == "ui-action":
            calls = [f"{lk['call']['method']} {lk['call']['path']}" for lk in call_to.get(aid, [])]
            tail = ("→ " + ", ".join(calls)) if calls else ""
        else:
            eff = effects_phrase(a)
            tail = f"→ {eff}" if eff else ""
        lines.append(f"{indent}{repo:<10} {a['kind']:<12} {aid:<42} {entry_ref(a):<36} {tail}".rstrip())
    return lines


def render_actions_md(project: str, maps: dict[str, dict], links: dict, generated_at: str) -> str:
    by_id = {a["id"]: (repo, a) for repo, m in maps.items() for a in m["actions"]}
    counts = []
    for repo, m in maps.items():
        kinds: dict[str, int] = {}
        for a in m["actions"]:
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        counts.append(f"{repo}: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))

    out = [f"# {project} — action map", "",
           f"_Generated {generated_at} from {len(maps)} repo(s) — {'; '.join(counts)}. "
           f"Prefixes stripped for linking: {', '.join(links.get('prefixes_stripped') or ['none'])}._",
           "",
           "Each line is one chain: what the user (or a scheduler) does, then every action it "
           "reaches in other repos. Ids are what `create-story --actions` accepts.", ""]

    calls_by_from: dict[str, list[dict]] = {}
    for lk in links["links"]:
        calls_by_from.setdefault(lk["from"], []).append(lk)

    areas: dict[str, list[dict]] = {}
    for c in links["chains"]:
        head = by_id[c["actions"][0]][1]
        if head["kind"] == "api-route" and c["actions"][0] in links["unlinked_routes"]:
            continue  # listed separately below
        if head["kind"] in ("process", "event-consumer"):
            continue
        areas.setdefault(c.get("area") or "Other", []).append(c)

    for area in sorted(areas):
        out += [f"## {area}", ""]
        for c in areas[area]:
            repo, head = by_id[c["actions"][0]]
            line = f"- **{head['label']}** `{head['id']}` — {what(head)} (`{entry_ref(head)}`)"
            for lk in calls_by_from.get(head["id"], []):
                _, tgt = by_id[lk["to"]]
                eff = effects_phrase(tgt)
                line += (f" → `{lk['call']['method']} {lk['call']['path']}` → `{tgt['id']}` "
                         f"(`{entry_ref(tgt)}`)" + (f" → {eff}" if eff else ""))
            if not calls_by_from.get(head["id"]):
                eff = effects_phrase(head)
                if eff:
                    line += f" → {eff}"
            out.append(line)
        out.append("")

    orphans = [by_id[r] for r in links["unlinked_routes"]]
    out += ["## Routes with no UI caller", ""]
    out += [f"- **{a['label']}** `{a['id']}` — `{what(a)}` (`{entry_ref(a)}`)"
            + (f" → {effects_phrase(a)}" if effects_phrase(a) else "")
            for _, a in orphans] or ["- (none)"]
    out += ["", "## UI calls that matched no route", ""]
    out += [f"- `{u['from']}` → `{u['call']['method']} {u['call']['path']}`"
            for u in links["unlinked_calls"]] or ["- (none)"]
    procs = [(r, a) for r, a in by_id.values() if a["kind"] in ("process", "event-consumer")]
    out += ["", "## Background processes", ""]
    out += [f"- **{a['label']}** `{a['id']}` — {what(a)} (`{entry_ref(a)}`)"
            + (f" → {effects_phrase(a)}" if effects_phrase(a) else "")
            for _, a in procs] or ["- (none)"]
    return "\n".join(out).rstrip() + "\n"
