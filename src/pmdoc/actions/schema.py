"""
schema.py — what an action map is, and the checks it must pass before it is saved.

An action is anything a person or a system can trigger: a control on a screen,
an HTTP route, a scheduled job, a queue consumer. The mapping agent emits these
as JSON; this module validates the shape (so a malformed map is repaired via
`--resume` rather than saved), then normalizes ids and defaults so the linker
and resolver can rely on them.

The example below is rendered into the mapping prompt, so the prompt and the
validator describe the same schema.
"""

from __future__ import annotations

import json
import re

from pmdoc.validate import ValidationResult

SCHEMA_VERSION = 1

ACTION_KINDS = ("ui-action", "api-route", "process", "event-consumer")
KIND_SHORT = {"ui-action": "ui", "api-route": "api", "process": "proc", "event-consumer": "evt"}
EFFECT_TYPES = ("db-read", "db-write", "http-call", "queue", "email", "event", "file",
                "cache", "navigate", "ui", "other")
TRIGGER_TYPES = ("schedule", "queue", "event", "webhook", "stream", "manual", "startup")
AUTH_VALUES = ("required", "optional", "none", "unknown")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ANY")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    s = _SLUG_RE.sub("-", str(text).lower()).strip("-")
    return s[:max_len].strip("-") or "action"


SCHEMA_EXAMPLE = {
    "api_base": "the base URL/path relative HTTP calls are resolved against, if this repo makes HTTP calls (else null)",
    "actions": [
        {
            "id": "publish-article",
            "kind": "ui-action",
            "area": "Articles",
            "label": "Publish Article",
            "summary": "Submitting the article editor creates the post and opens it.",
            "surface": {"screen": "New Article editor", "route": "/editor",
                        "control": "button \"Publish Article\""},
            "entry": {"file": "src/pages/NewArticle.tsx", "line": 15, "symbol": "onSubmit"},
            "trace": ["src/components/Header.tsx:53 'New Article' link -> /editor",
                      "src/pages/NewArticle.tsx:15 onSubmit -> createArticle",
                      "src/api/client.ts:78 createArticle -> POST articles"],
            "calls": [{"method": "POST", "path": "articles",
                       "via": "src/api/client.ts:78 createArticle"}],
            "effects": [{"type": "navigate", "target": "/article/{slug}", "detail": "on success"}],
            "auth": "required",
            "aliases": ["new article", "create article", "add post", "write a post",
                        "publish post"],
        },
        {
            "id": "create-article",
            "kind": "api-route",
            "area": "Articles",
            "label": "Create article",
            "summary": "Creates an article owned by the signed-in user.",
            "route": {"method": "POST", "path": "/api/articles"},
            "declared_at": "serverless.yml:90",
            "entry": {"file": "src/article.py", "line": 15, "symbol": "create_article"},
            "effects": [{"type": "db-write", "target": "articles table", "detail": "put_item"}],
            "auth": "required",
            "aliases": ["create article", "new post", "save article"],
        },
        {
            "id": "nightly-digest",
            "kind": "process",
            "area": "Notifications",
            "label": "Nightly digest email",
            "summary": "Emails each user the day's new posts from authors they follow.",
            "trigger": {"type": "schedule", "detail": "cron(0 6 * * ? *)",
                        "declared_at": "serverless.yml:140"},
            "entry": {"file": "src/jobs/digest.py", "line": 12, "symbol": "run"},
            "effects": [{"type": "db-read", "target": "follows table"},
                        {"type": "email", "target": "digest email"}],
            "auth": "none",
            "aliases": ["daily digest", "email summary"],
        },
    ],
}


def schema_example_json() -> str:
    return json.dumps(SCHEMA_EXAMPLE, indent=2)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_action_map(obj) -> ValidationResult:
    """Structural check of the agent's JSON. Every error is repairable: the agent
    did the inventory, it only has to reprint it in the right shape."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ValidationResult(False, ["top level must be a JSON object"])
    actions = obj.get("actions")
    if not isinstance(actions, list):
        return ValidationResult(False, ["`actions` must be a list"])
    if not actions:
        return ValidationResult(False, ["`actions` is empty — list every user control, route "
                                        f"and background process, or reply CANNOT_COMPLETE"])

    for i, a in enumerate(actions):
        where = f"actions[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{where}: must be an object")
            continue
        label = a.get("label") or a.get("id") or "?"
        where = f"actions[{i}] ({label})"
        for key in ("id", "label", "summary", "area"):
            if not _nonempty_str(a.get(key)):
                errors.append(f"{where}: `{key}` must be a non-empty string")
        kind = a.get("kind")
        if kind not in ACTION_KINDS:
            errors.append(f"{where}: `kind` is {kind!r}; expected one of {', '.join(ACTION_KINDS)}")
        entry = a.get("entry")
        if not isinstance(entry, dict) or not _nonempty_str(entry.get("file")):
            errors.append(f"{where}: `entry.file` is required (the file where the action starts)")
        elif entry.get("line") is not None and not isinstance(entry.get("line"), int):
            errors.append(f"{where}: `entry.line` must be an integer or null")
        aliases = a.get("aliases")
        if aliases is not None and not (isinstance(aliases, list)
                                        and all(isinstance(x, str) for x in aliases)):
            errors.append(f"{where}: `aliases` must be a list of strings")

        if kind == "ui-action":
            if not isinstance(a.get("surface"), dict):
                errors.append(f"{where}: ui-action needs a `surface` object (screen/route/control)")
        elif kind == "api-route":
            route = a.get("route")
            if not isinstance(route, dict) or not _nonempty_str(route.get("path")) \
                    or str(route.get("method", "")).upper() not in HTTP_METHODS:
                errors.append(f"{where}: api-route needs `route` with `method` (HTTP verb) and `path`")
        elif kind in ("process", "event-consumer"):
            trig = a.get("trigger")
            if not isinstance(trig, dict) or trig.get("type") not in TRIGGER_TYPES:
                errors.append(f"{where}: {kind} needs `trigger.type` in {', '.join(TRIGGER_TYPES)}")

        # Any kind may make outbound HTTP calls (a startup task, a sync job).
        calls = a.get("calls", [])
        if not isinstance(calls, list):
            errors.append(f"{where}: `calls` must be a list")
        else:
            for j, c in enumerate(calls):
                if not isinstance(c, dict) or not _nonempty_str(c.get("path")) \
                        or str(c.get("method", "")).upper() not in HTTP_METHODS:
                    errors.append(f"{where}: calls[{j}] needs `method` (HTTP verb) and `path`")

        effects = a.get("effects", [])
        if not isinstance(effects, list):
            errors.append(f"{where}: `effects` must be a list")
        else:
            # An unfamiliar effect type is coerced in normalize_action_map rather
            # than repaired: re-rendering a 40-action inventory to rename "http"
            # to "http-call" costs more than it is worth.
            for j, e in enumerate(effects):
                if not isinstance(e, dict) or not _nonempty_str(e.get("type")):
                    errors.append(f"{where}: effects[{j}] needs a string `type`")
        auth = a.get("auth", "unknown")
        if auth not in AUTH_VALUES:
            errors.append(f"{where}: `auth` is {auth!r}; expected one of {', '.join(AUTH_VALUES)}")

    return ValidationResult(not errors, errors, repairable=True)


# Effect types agents reach for when they ignore the list, mapped to the closest one.
EFFECT_ALIASES = {
    "http": "http-call", "api": "http-call", "api-call": "http-call", "fetch": "http-call",
    "request": "http-call", "state": "ui", "render": "ui", "redirect": "navigate",
    "navigation": "navigate", "db": "db-write", "write": "db-write", "read": "db-read",
    "storage-read": "cache", "storage-write": "cache", "local-storage": "cache",
    "storage": "file", "s3": "file", "upload": "file", "publish": "event", "notify": "email",
    "message": "queue", "sqs": "queue", "sns": "event",
}


def coerce_effect(e: dict) -> dict:
    """Map an off-list effect type onto the list, keeping the original in `detail`."""
    t = str(e.get("type", "")).strip().lower()
    if t in EFFECT_TYPES:
        return dict(e, type=t)
    mapped = EFFECT_ALIASES.get(t, "other")
    detail = " ".join(x for x in (f"[{t}]", str(e.get("detail") or "")) if x).strip()
    return dict(e, type=mapped, detail=detail)


def normalize_action_map(obj: dict, repo: str) -> tuple[dict, list[str]]:
    """Return (normalized copy, warnings). Assumes validate_action_map passed.

    Ids become `<repo>:<ui|api|proc|evt>:<slug>` so they are unique across a
    project and say where they live; duplicates get -2, -3. Methods are
    upper-cased and optional fields get their defaults."""
    warnings: list[str] = []
    seen: set[str] = set()
    out_actions = []
    for a in obj["actions"]:
        a = dict(a)
        base = f"{repo}:{KIND_SHORT[a['kind']]}:{slugify(a['id'])}"
        aid, n = base, 1
        while aid in seen:
            n += 1
            aid = f"{base}-{n}"
        if n > 1:
            warnings.append(f"duplicate action id {base} renamed to {aid}")
        seen.add(aid)
        a["id"] = aid
        a.setdefault("aliases", [])
        a["effects"] = [coerce_effect(e) for e in a.get("effects", [])]
        a.setdefault("auth", "unknown")
        if "calls" in a or a["kind"] == "ui-action":
            a["calls"] = [dict(c, method=str(c["method"]).upper()) for c in a.get("calls", [])
                          if isinstance(c, dict) and c.get("path") and c.get("method")]
        if a["kind"] == "api-route":
            a["route"] = dict(a["route"], method=str(a["route"]["method"]).upper())
        out_actions.append(a)

    out = {k: v for k, v in obj.items() if k != "actions"}
    out["actions"] = out_actions
    return out, warnings


def lint_action_map(m: dict, repo_kind: str) -> list[str]:
    """Non-fatal sanity warnings, printed after mapping."""
    kinds = [a["kind"] for a in m["actions"]]
    warnings = []
    if repo_kind in ("backend", "service") and "api-route" not in kinds:
        warnings.append("backend repo mapped with no api-route actions")
    if repo_kind == "frontend" and "ui-action" not in kinds:
        warnings.append("frontend repo mapped with no ui-action actions")
    for a in m["actions"]:
        if a["kind"] == "ui-action" and not a.get("calls") and not a.get("effects"):
            warnings.append(f"{a['id']}: ui-action with no calls and no effects")
    return warnings
