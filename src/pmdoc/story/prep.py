"""
prep.py — render every prompt a story needs, and the manifest that drives the run.

Pure Python, no model calls. For each repo the resolution touched, the trace
prompt gets that repo's entry points ("Start here") with the cross-repo links
spelled out, so the agent starts from known code instead of searching for it.
The synthesis prompt gets the chain in plain words, so the writer knows which
write-up covers which step.

Rendered prompts land in logs/<project>/<slug>/prompts/ (inspectable, never
committed); the manifest in stories/<project>/<slug>/manifest.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pmdoc import templates
from pmdoc.actions.render import effects_phrase, entry_ref, what
from pmdoc.actions.resolve import Resolution
from pmdoc.config import (
    CANNOT_COMPLETE_SENTINEL, KIND_TIER, MANIFEST_NAME, ROOT, STORY_HEADINGS,
    TECHNICAL_HEADINGS, TECHNICAL_SENTINEL, product_headings, story_dir, story_logs_dir,
)
from pmdoc.files import atomic_write_text
from pmdoc.project import Project

# Per-heading guidance. Heading NAMES live in config.py (shared with the
# validator); only the explanatory blurb under each one lives here.
PRODUCT_GUIDANCE_FRONTEND = {
    "Story": "(As a <role>, I want <goal> so that <reason>. Infer the role from who uses "
             "this — describe them in plain words, e.g. \"a signed-in author\".)",
    "Where This Lives": "(the screen(s), and the navigation that leads there, in product terms)",
    "What the User Sees & Does":
        "(every user-triggered scenario, each written as \"When the user … → …\". Enumerate "
        "every field, label, button, link, message, error, and enabled/disabled state and the "
        "condition for each, quoting on-screen text literally. Be exhaustive.)",
    "Rules & Behaviors":
        "(deliberate product rules in plain language: who can do what, required vs optional, "
        "what is checked, what happens automatically vs what the user must do)",
    "Edge Cases": "(notable conditional behavior and corner cases)",
    "Open Questions / Uncertain Areas": "(anything you couldn't confirm from the code alone)",
}
PRODUCT_GUIDANCE_BACKEND = {
    "Story": "(As a <role>, I want <goal> so that <reason>. Infer the role in plain words.)",
    "What Happens Behind the Scenes":
        "(in product terms: what triggers this, and what the user ends up seeing as a result. "
        "No UI here, but no code either — describe the user-visible outcome.)",
    "Rules & Behaviors":
        "(the rules the system enforces, in plain language and user-facing names: sign-in and "
        "permission checks, validation, what is stored, generated or changed automatically, "
        "and what is left untouched)",
    "Edge Cases": "(notable conditional behavior and corner cases)",
    "Open Questions / Uncertain Areas": "(anything you couldn't confirm from the code alone)",
}
TECHNICAL_GUIDANCE = {
    "Entry Points / Code Path": "(the path from trigger to effect, with `file:line` for every step)",
    "Data Flow": "(fields, entities, tables, transformations — exact names)",
    "Cross-Cutting Effects": "(shared modules, other services, side effects, config values in effect)",
}
STORY_GUIDANCE = {
    "Story": "(As a <role>, I want <goal> so that <reason>. Then, only if a reader would not "
             "already know it, one short paragraph defining the main thing being acted on.)",
    "Requirements": "(every user-triggered scenario, exhaustive, each as \"**When the user …** → …\" "
                    "with literal on-screen text quoted; `### ` subheadings by screen, step or "
                    "scenario once there are more than about eight bullets)",
    "Rules & Behaviors": "(deliberate product rules in plain language, each bullet leading with a "
                         "**bolded rule name**: validation, permissions, what happens automatically "
                         "behind the scenes vs what the user must act on, guaranteed vs best effort)",
    "Dependencies": "(preconditions: who the user must be, what must already exist, which state "
                    "or setting must be in place)",
    "Impacts": "(what else this changes or shows up in — other screens, other users, notifications, "
               "background work, other systems — grouped by surface with bold leads)",
    "Open Questions / Uncertain Areas": "(anything that couldn't be confirmed from the code alone)",
}

DEFAULT_STYLE_RULES = (
    "- Use the product's own words: button labels, screen names, and field labels exactly as a "
    "user sees them. If the request or the code uses an internal term, translate it."
)

_LAYER = {"backend": "a **backend** repo", "service": "a **backend service**",
          "worker": "a **worker** — background work started by schedules or events",
          "unknown": "a repo with **no UI**"}


def rel(path) -> str:
    """A path relative to the pm-doc-generator root when it is inside it (the
    rendered prompts under logs/). The manifest is committed, so it should not
    carry one machine's absolute paths."""
    from pathlib import Path
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def render_template(h1: str, headings, guidance: dict) -> str:
    """A markdown skeleton: an H1, then each `## heading` + its blurb."""
    lines = [h1, ""]
    for heading in headings:
        lines.append(f"## {heading}")
        if guidance.get(heading):
            lines.append(guidance[heading])
        lines.append("")
    return "\n".join(lines[:-1])


def h1_for_repo(doc_id: str, repo: str) -> str:
    return f"# {doc_id} — {repo}"


def notes_section(notes: str) -> str:
    return f"## Additional Notes\n\n{notes.strip()}" if notes and notes.strip() else ""


def style_rules(project: Project) -> str:
    # v2: project.style_rules and project.glossary get appended here.
    return DEFAULT_STYLE_RULES


def start_here(repo: str, res: Resolution, by_id: dict, links: list[dict]) -> str:
    """The resolved entry points for one repo, with links to the other repos spelled out."""
    out_links: dict[str, list[dict]] = {}
    in_links: dict[str, list[dict]] = {}
    for lk in links:
        if lk["from"] in res.action_ids and lk["to"] in res.action_ids:
            out_links.setdefault(lk["from"], []).append(lk)
            in_links.setdefault(lk["to"], []).append(lk)

    lines = ["An inventory of this repo identified where this behavior starts. Begin from these "
             "entry points and trace outward — they are where the behavior starts, not the whole "
             "of it. Verify each before relying on it.", ""]
    for a in res.entry_points.get(repo, []):
        lines.append(f"- **{a['label']}** ({a['kind']}) — {what(a)}")
        lines.append(f"  - entry: `{entry_ref(a, full=True)}`")
        if a.get("declared_at"):
            lines.append(f"  - declared at: `{a['declared_at']}`")
        for step in a.get("trace", [])[:6]:
            lines.append(f"  - trace: {step}")
        for c in a.get("calls", []):
            target = next((lk for lk in out_links.get(a["id"], [])
                           if lk["call"]["method"] == c["method"]), None)
            served = ""
            if target:
                trepo, ta = by_id[target["to"]]
                served = (f" → served by **{ta['label']}** in `{trepo}` "
                          f"(`{entry_ref(ta, full=True)}`)")
            via = f" via `{c['via']}`" if c.get("via") else ""
            lines.append(f"  - calls: `{c['method']} {c['path']}`{via}{served}")
        for lk in in_links.get(a["id"], []):
            frepo, fa = by_id[lk["from"]]
            lines.append(f"  - called by: **{fa['label']}** in `{frepo}` — {what(fa)}")
        eff = effects_phrase(a)
        if eff:
            lines.append(f"  - effects (from the inventory): {eff}")
        if a.get("auth") and a["auth"] != "unknown":
            lines.append(f"  - sign-in: {a['auth']}")
    others = [r for r in res.repos_involved if r != repo]
    if others:
        lines += ["", f"The rest of this flow is investigated separately in: "
                      f"{', '.join(f'`{r}`' for r in others)}. Stay within this repo, but say in "
                      "your write-up what this repo hands to, or receives from, those parts."]
    return "\n".join(lines)


def chain_summary(res: Resolution, by_id: dict) -> str:
    lines = []
    for c in res.chains:
        lines.append(f"- **{c['label']}**")
        for aid in c["actions"]:
            repo, a = by_id[aid]
            lines.append(f"  - in `{repo}` ({a['kind']}): {a['label']} — {a.get('summary', '')}")
    return "\n".join(lines) or "(no chain — see the write-ups)"


def prep_story(project: Project, res: Resolution, prompt: str, maps: dict[str, dict],
               links: dict, notes: str = "") -> dict:
    """Render prompts + manifest for a resolved story. Returns the manifest."""
    by_id = {a["id"]: (repo, a) for repo, m in maps.items() for a in m["actions"]}
    s_dir = story_dir(project.name, res.story_slug)
    p_dir = story_logs_dir(project.name, res.story_slug) / "prompts"
    s_dir.mkdir(parents=True, exist_ok=True)
    p_dir.mkdir(parents=True, exist_ok=True)

    common = dict(
        DOC_ID=res.doc_id, PROJECT_NAME=project.name,
        PROJECT_DESCRIPTION=project.description or "(no description provided)",
        USER_PROMPT=prompt, NOTES_SECTION=notes_section(notes),
        STYLE_RULES=style_rules(project), CANNOT_COMPLETE_SENTINEL=CANNOT_COMPLETE_SENTINEL,
    )

    repos = []
    for name in res.repos_involved:
        repo = project.repo(name)
        if repo.kind == "frontend":
            guidance = templates.load("trace_guidance_frontend", project.name).strip()
            pguide = PRODUCT_GUIDANCE_FRONTEND
        else:
            guidance = templates.render("trace_guidance_backend", project.name,
                                        LAYER=_LAYER.get(repo.kind, _LAYER["unknown"])).strip()
            pguide = PRODUCT_GUIDANCE_BACKEND
        h1 = h1_for_repo(res.doc_id, name)
        text = templates.render(
            "trace_repo", project.name, **common,
            H1_LINE=h1, REPO_NAME=name, REPO_KIND=repo.kind, REPO_PATH=str(repo.path),
            REPO_HINTS=repo.hints or "(none)",
            START_HERE=start_here(name, res, by_id, links.get("links", [])),
            KIND_GUIDANCE=guidance,
            PRODUCT_TEMPLATE=render_template(h1, product_headings(repo.kind), pguide),
            TECHNICAL_TEMPLATE=render_template(f"{h1} (Technical Appendix)", TECHNICAL_HEADINGS,
                                               TECHNICAL_GUIDANCE),
            TECHNICAL_SENTINEL=TECHNICAL_SENTINEL,
        )
        path = p_dir / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        repos.append({"name": name, "kind": repo.kind, "tier": KIND_TIER.get(repo.kind, 2),
                      "path": project.display_path(repo.path),
                      "entry_actions": [a["id"] for a in res.entry_points.get(name, [])],
                      "prompt_doc": rel(path)})

    synth = templates.render(
        "synthesis", project.name, **common,
        CHAIN_SUMMARY=chain_summary(res, by_id),
        STORY_TEMPLATE=render_template(f"# {res.doc_id}", STORY_HEADINGS, STORY_GUIDANCE),
    )
    synth_path = p_dir / "_synthesis.md"
    synth_path.write_text(synth, encoding="utf-8")

    # Snapshot the traced actions so the technical appendix and later revisions
    # don't depend on the action map staying unchanged.
    actions = {aid: by_id[aid][1] for aid in res.action_ids}
    manifest = {
        "project": project.name, "story_slug": res.story_slug, "doc_id": res.doc_id,
        "prompt": prompt, "notes": notes,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "resolution": res.as_dict(),
        "repos": repos,
        "synthesis_prompt": rel(synth_path),
        "actions": actions,
    }
    atomic_write_text(s_dir / MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest
