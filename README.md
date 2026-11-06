# pm-doc-generator

Turn a product question into a product document, by reading the code.

```bash
python cli.py map-actions  shop-app                                     # once per project
python cli.py create-story shop-app "explain the add to cart feature"   # once per question
```

A product manager asks about a feature in their own words. The tool works out which
buttons, API routes and background jobs that question is about, across every repo in
the project. It then traces each repo with a read-only Claude Code session and writes
`STORY.md`: a plain-language document with a fixed set of sections that splits cleanly
into chunks for a RAG index.

It is a generalized version of `vapi-rag-doc-generator`. That tool needed a description
that named entry-point files. This one finds the entry points itself, from an **action
map** it builds once per project.

## Quick start

Requirements: Python 3.10+, and [Claude Code](https://claude.com/claude-code) on `PATH`
and signed in. Every agent session is a headless `claude -p` subprocess.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # set PROJECT_FOLDER; model, effort, turn limits

python -m unittest discover -s tests -v    # offline; no Claude calls

python cli.py map-actions shop-app         # build actions/shop-app/*.json + ACTIONS.md
python cli.py create-story shop-app "explain the add to cart feature" --dry-run
python cli.py create-story shop-app "explain the add to cart feature"
python cli.py collect shop-app             # → corpus/shop-app/
```

The code being documented never lives in this repo. `PROJECT_FOLDER` in `.env` points at
the folder that holds the project's repos. For shop-app ("Summit Supply"), the example
project, that is a folder containing `shop-api/` (Express/TypeScript) and `shop-web/`
(React). To run once against a different folder, set it inline:

```bash
PROJECT_FOLDER=~/code/payment-app python cli.py map-actions payment-app
```

## Projects

A project is one YAML file in `projects/`. Pointing the tool at a new app means writing
one of these. No code changes are needed.

```yaml
name: shop-app                  # no "_" (the corpus splits doc ids on it)
description: >
  Outdoor-gear storefront: catalog, product pages, cart, checkout, member pricing.
repos:
  - name: shop-api
    path: shop-api              # relative to PROJECT_FOLDER, absolute, or ~
    kind: backend               # optional; auto-detected when omitted
    hints: |
      Every route is in src/routes.ts, mounted at /api. Logic lives in src/{cart,checkout,pricing}.ts.
  - name: shop-web
    path: shop-web
    hints: |
      All HTTP calls are in src/api/client.ts.
api_prefixes: ["/api"]          # stripped before linking UI calls to routes
glossary: {}                    # reserved for v2
style: { rules: [] }            # reserved for v2
collect: { out_dir: corpus }   # exports go to <out_dir>/<project>/
```

Relative repo paths resolve against `PROJECT_FOLDER`. If a config uses a relative path
and the variable is unset, loading fails with a message saying so. Saved action maps and
manifests store paths relative to the project folder, never one machine's absolute path.

**Hints** are the highest-leverage field. A few lines on where routes are declared,
where the HTTP client lives, and any indirection (shared form components, handlers that
serve two routes) make mapping faster and more complete.

**Repo kinds** decide the investigation guidance, the product-doc template, and run
order. When `kind` is omitted, the tool detects it from the repo's files.

| kind | detected from |
| --- | --- |
| `frontend` | react, vue, angular, next, nuxt, svelte, solid or vite, with no server framework |
| `worker` | serverless schedule/queue/stream events and no http; celery; worker-only Procfile; bull or node-cron |
| `backend` | serverless http events; fastapi, flask, django, express, fastify, koa, nest; `manage.py`; route decorators |
| `service` | config only; treated like a backend |
| `unknown` | anything else |

Use `--repos a,b` on any command to work on a subset.

## Action maps

`map-actions` runs one read-only session per repo. Each session inventories every
**action** in its repo:

| kind | what it is |
| --- | --- |
| `ui-action` | a control a user operates: a button, a form submit, a tab, a page load that fetches |
| `api-route` | one HTTP method and path, even when two routes share a handler |
| `process` | something that runs on a schedule, at startup, or by hand |
| `event-consumer` | something triggered by a queue, topic, stream or webhook |

Each action records a file:line entry point, what it calls, what it changes, whether it
needs sign-in, and a handful of aliases in product vocabulary ("add to cart", "buy
it now"). The aliases are what let a vague question find the right code.

Output goes to `actions/<project>/`:

- **`<repo>.json`** is the per-repo map, with session provenance. It is cached, so pass
  `--remap` to rebuild it.
- **`links.json`** joins frontend calls to backend routes. Python builds it
  deterministically, with no model involved.
- **`ACTIONS.md`** is a readable index of every chain, grouped by product area. It
  also lists routes no UI calls and UI calls that matched no route.

Linking normalizes both sides: `${slug}`, `:slug`, `<int:slug>` and `[slug]` all become
`{slug}`. It also drops query strings, resolves relative calls against the frontend's
API base URL, and strips configured or inferred prefixes such as `/api`. A literal
segment beats a parameter, so `/products/featured` never matches `/products/{id}`.

```bash
python cli.py list-actions shop-app --grep cart
python cli.py show-action  shop-app shop-web:ui:place-order
```

The maps are small and reviewable, and they are the first thing to check when a story
looks wrong.

## Creating a story

```bash
python cli.py create-story <project> "<question>" [--dry-run] [--review] [--id slug] [--actions a,b] [--notes "..."]
```

1. **Resolve.** A keyword prefilter ranks the action index, and a short, tool-less
   Claude call picks the smallest set of actions that answers the question. The whole
   chain of each picked action comes along. The result is printed before any tracing:
   story slug, doc id, every action with file:line, repo order, and the reason.
   - `--dry-run` stops here. It is cheap and makes a good first demo step.
   - `--review` asks for confirmation, and accepts replacement action ids.
   - `--actions` pins ids and skips the model.
   - Exit code 3 means the question was ambiguous or matched nothing. The resolver says
     what it needs.
2. **Prep.** The tool renders one trace prompt per involved repo, each with a
   **Start here** list of the resolved entry points, plus a synthesis prompt and
   `manifest.json`. `prep-story` stops after this step, and `run-story` continues from it.
3. **Trace.** One headless session runs per repo, with that repo as its working
   directory. Tools are restricted to read-only ones: Read, Grep, Glob and read-only
   shell commands. Repos run in tiers: backend and service, then worker, then unknown,
   then frontend. Each repo receives the product docs of every finished repo in a lower
   tier, so the frontend trace already knows the backend's rules and error cases.
   `--parallel N` runs repos in the same tier at the same time without changing what
   any of them sees.
4. **Synthesize.** One tool-less session weaves the per-repo docs into `STORY.md`.
   Python assembles `STORY.technical.md` beside it from the per-repo appendices and the
   resolved chain. That file is for engineers and is never exported.

```text
stories/<project>/<slug>/
  STORY.md              the product document
  manifest.json         question, resolution, repos, action snapshot
  STORY.technical.md    engineer appendix: chain with file:line, per-repo code paths
  <repo>.md             per-repo product write-up
  <repo>.technical.md   per-repo technical appendix
```

Runs checkpoint to `run_state.json`. After a failure or a `--review` pause, continue
with `run-story <project> <slug> --resume`. Rebuild only the final document with
`--synthesis-only`.

## Output format

Every `STORY.md` has an H1 equal to its doc id (`<project>_<slug>`) and exactly these
sections, in order:

```
## Story
## Requirements
## Rules & Behaviors
## Dependencies
## Impacts
## Open Questions / Uncertain Areas
```

The format is fixed because the downstream chunker depends on it. The chunker splits
on `## `, and on `### ` inside a section over about 2,200 characters. It uses the H1 as
every chunk's title, and it splits the filename on `_` into module and submodule. The
synthesis prompt asks for `### ` subsections in Requirements so that chunks stay
whole. When a section would still be sliced, the run prints a warning.

The headings live in one place, `src/pmdoc/config.py`. The prompts, the templates and
the validators all read them from there.

## Nothing is written unless it validates

An agent's final message is checked structurally before anything is written under its
real name. The check covers the H1, the required sections, the sentinel between the
product doc and the technical appendix, and preamble or "addendum" messages.

- **A formatting slip is repairable.** The session is resumed with `claude --resume`
  and asked to reprint the whole document, up to two times. The repaired text is
  what gets written.
- **A refusal is not repairable.** When an agent says it cannot complete the task, the
  repo fails without a retry. Nudging a refusal into a document would invent content.
- Rejected output is kept as `<name>.INVALID.md` next to the story, with the session
  id, so `claude --resume <id>` opens the conversation.

Synthesis refuses to run unless every per-repo doc on disk validates. A story woven
from a missing repo would be silently incomplete.

## collect

```bash
python cli.py collect <project> [--to dir] [--story slug] [--clean]
```

`collect` exports every valid `STORY.md` to `<dir>/<project>/<doc_id>.md` for the RAG
pipeline. `<dir>` defaults to the project's `collect.out_dir`, which is `corpus/`.
On the way out, it compacts editor whitespace, strips the Open Questions section, and
forces the H1 to the doc id. The Open Questions section holds everything unproven,
which must not be retrieved as fact. Sources are never modified. `--clean` empties
this project's export folder first and leaves other projects' exports alone.

## Configuration

`.env`:

| variable | default | used for |
| --- | --- | --- |
| `PROJECT_FOLDER` | none | the folder holding the project's repos; relative repo paths resolve against it |
| `PMDOC_MODEL` | `opus` | mapping, tracing, synthesis |
| `PMDOC_RESOLVE_MODEL` | `sonnet` | the resolution call |
| `PMDOC_EFFORT` | `xhigh` | reasoning effort for agent sessions |
| `PMDOC_MAX_TURNS` | `400` | per-repo trace sessions |
| `PMDOC_MAP_MAX_TURNS` | `200` | mapping sessions |

The model settings can also be set per command with `--model`, `--effort` and
`--max-turns`.

## Prompts

The prompt templates ship with the code in `src/pmdoc/prompts/`. To tune them for one
project, copy any template into `prompts/<project>/` under the same name and edit it.
A file there overrides the built-in for that project only. Anything not overridden
comes from the built-ins, so a new project needs no prompt files at all.

## Layout

```text
cli.py                  entry point
projects/*.yaml         project configs
prompts/<project>/      optional per-project prompt overrides
actions/<project>/      action maps, links, ACTIONS.md
stories/<project>/      generated stories
corpus/<project>/       collect output
logs/<project>/         session logs and rendered prompts
src/pmdoc/
  project.py detect.py config.py templates.py validate.py collect.py
  claude/   session.py envelope.py repair.py      headless claude -p, validation and repair
  actions/  schema.py map.py link.py render.py resolve.py store.py
  story/    prep.py run.py synthesize.py create.py
  prompts/  built-in prompt templates ({{PLACEHOLDER}} tokens)
tests/                  offline suite; claude is stubbed
```

Every session log is written to `logs/`, and each command prints a
`tail -f` line for it.

## v2 roadmap

- **Configurable sections.** A per-project `story.sections:` list would drive the
  synthesis template, the validator and `collect`. It has to stay chunker-compatible:
  one H1, `## ` sections, and an optional stripped section for unproven claims. Today
  the headings are one constant in `config.py`.
- **revise-story.** Archive `STORY.md` as `STORY_V{n}.md`, re-trace only the gap a
  reader reported, and merge without losing what the original got right.
- **Glossary and style rules.** The reserved `glossary` and `style.rules` keys would be
  injected into trace and synthesis prompts, and the glossary into resolution aliases.
- **Large repos.** Split mapping across several sessions by directory or area, then
  merge the maps.
- **Incremental remapping.** Remap only repos whose git HEAD changed since the map's
  provenance stamp.
- **Orchestrator UI, release pipeline and eval generation**, carried over from the
  original tool.
