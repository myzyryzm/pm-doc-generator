"""Shared test helpers: import path, fixture maps, and sample documents."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

from pmdoc.config import TECHNICAL_SENTINEL  # noqa: E402


def fixture_maps() -> dict:
    return {name: json.loads((FIXTURES / "actions" / f"{name}.json").read_text())
            for name in ("backend", "frontend")}


DOC_ID = "mini_add-post"

GOOD_PRODUCT_BACKEND = (
    f"# {DOC_ID} — backend\n\n"
    "## Story\nAs a signed-in author, I want my article saved so that readers can find it.\n\n"
    "## What Happens Behind the Scenes\n"
    + ("The article is saved with a web address built from its title. " * 12) + "\n\n"
    "## Rules & Behaviors\n"
    + ("A title, a description and a body are required; tags are optional. " * 12) + "\n\n"
    "## Edge Cases\nTwo articles with the same title still get different addresses.\n\n"
    "## Open Questions / Uncertain Areas\nNone.\n"
)
GOOD_PRODUCT_FRONTEND = (
    f"# {DOC_ID} — frontend\n\n"
    "## Story\nAs a signed-in author, I want to publish an article so that others can read it.\n\n"
    "## Where This Lives\nThe New Article editor, reached from the header.\n\n"
    "## What the User Sees & Does\n"
    + ("When the user clicks Publish Article → the article page opens. " * 12) + "\n\n"
    "## Rules & Behaviors\n" + ("The button is greyed out while saving. " * 12) + "\n\n"
    "## Edge Cases\nErrors appear above the form.\n\n"
    "## Open Questions / Uncertain Areas\nNone.\n"
)


def good_technical(repo: str) -> str:
    return (
        f"# {DOC_ID} — {repo} (Technical Appendix)\n\n"
        "## Entry Points / Code Path\n" + ("src/article.py:15 create_article. " * 10) + "\n\n"
        "## Data Flow\n" + ("put_item writes slug, title, body, tagList. " * 10) + "\n\n"
        "## Cross-Cutting Effects\nNone.\n\n"
        "## Open Questions / Uncertain Areas\nNone.\n"
    )


def trace_output(product: str, repo: str) -> str:
    return f"{product}\n{TECHNICAL_SENTINEL}\n{good_technical(repo)}"


GOOD_STORY = (
    f"# {DOC_ID}\n\n"
    "## Story\nAs a signed-in author, I want to publish an article so that readers can find it.\n\n"
    "## Requirements\n"
    + "".join(f"- **When the user does step {i}** → something visible happens.\n" for i in range(12))
    + "\n## Rules & Behaviors\n" + ("- **Required fields.** Title, description and body. \n" * 6)
    + "\n## Dependencies\n- The user is signed in.\n\n"
    "## Impacts\n- **Home:** the article appears in the global feed.\n\n"
    "## Open Questions / Uncertain Areas\n- **Whether tags are trimmed** is unconfirmed.\n"
)
