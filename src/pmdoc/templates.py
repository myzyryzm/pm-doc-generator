"""
templates.py — render prompt templates.

The built-in templates ship with the code in src/pmdoc/prompts/. A project can
override any of them by putting a file of the same name in prompts/<project>/
(local, gitignored), so one project's prompts can be tuned without touching
another's or the defaults. A template not overridden comes from the built-ins.

Prompts live as editable markdown files rather than strings buried in code, so
tuning what an agent is told never means touching Python. Placeholders are
`{{NAME}}`. Rendering fails loudly on a missing variable or an unresolved
placeholder: a prompt that silently ships `{{START_HERE}}` to an agent produces
a confidently wrong trace.

Runtime markers (`<!-- PRIOR_CONTEXT -->`, `<!-- ALL_PRODUCT_DOCS -->`) are not
placeholders: they survive rendering and are filled in later, when the content
they stand for exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from pmdoc.config import DEFAULT_PROMPTS_DIR, prompts_dir

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


class TemplateError(ValueError):
    pass


def template_path(name: str, project: str | None = None) -> Path:
    fname = name if name.endswith(".md") else f"{name}.md"
    candidates = ([prompts_dir(project) / fname] if project else []) + [DEFAULT_PROMPTS_DIR / fname]
    for path in candidates:
        if path.exists():
            return path
    raise TemplateError(f"missing prompt template `{fname}` — looked in "
                        + ", ".join(str(c.parent) for c in candidates))


def load(name: str, project: str | None = None) -> str:
    return template_path(name, project).read_text(encoding="utf-8")


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def render_text(text: str, **vars: object) -> str:
    """Substitute {{NAME}} from vars. Every placeholder must be supplied; values
    are inserted once (a value containing `{{X}}` is not re-expanded)."""
    missing = placeholders(text) - set(vars)
    if missing:
        raise TemplateError(f"no value for placeholder(s): {', '.join(sorted(missing))}")
    return _PLACEHOLDER_RE.sub(lambda m: str(vars[m.group(1)]), text)


def render(name: str, project: str | None = None, **vars: object) -> str:
    return render_text(load(name, project), **vars)
