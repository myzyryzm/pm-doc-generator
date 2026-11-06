# Action Inventory: `{{REPO_NAME}}` ({{REPO_KIND}}) — project `{{PROJECT_NAME}}`

You are building an inventory of every **action** in this repository: everything a person or a system can trigger, and where in the code it starts. The inventory is used to answer product questions like "explain how adding a post works", so it must let someone go from a plain-English request to the exact entry points in the code. **You are in READ-ONLY mode — do not modify any files. Only read, search, and analyze.**

## The Product

{{PROJECT_DESCRIPTION}}

## This Repo

`{{REPO_NAME}}` is located at `{{REPO_PATH}}`. You are running inside it. Its kind is **{{REPO_KIND}}**.

Hints from the team that owns it:

{{REPO_HINTS}}

## What Counts as an Action

Four kinds. Any repo may contain any of them — record what is actually there.

- **`ui-action`** — something a person does on a screen: click a button, submit a form, follow a menu link, toggle a switch, change a page, or simply open a screen that loads data. One action per distinct control or behavior.
- **`api-route`** — one per (HTTP method, path) this repo serves. GraphQL: one per query/mutation, recorded as method `POST` and path `/graphql/<operationName>`.
- **`process`** — work started by a schedule, a script, startup, or an operator: cron jobs, nightly imports/exports, data syncs, CLI commands, backfills.
- **`event-consumer`** — work started by a message or event: queue/topic subscribers, stream processors, webhook receivers, storage/database triggers.

## How to Find Them

{{KIND_GUIDANCE}}

## Rules for Each Field

- **Be complete.** Enumerate every route, every user control that causes an HTTP call or a change of state, and every background process. Prefer many small, precise actions over a few broad ones. A PM will ask about any one of them.
- **`entry`** — where the action starts in code: the handler or job function, as `file` (relative to the repo root), `line` (a line you actually saw), and `symbol`. For a UI control, the entry is the handler that does the work, not the line of markup.
- **`trace`** — 2–6 short `file:line what-happens` steps from the trigger to the work, so a later investigation can start mid-path.
- **`calls`** — every HTTP call the action makes (UI actions, and any process that calls an API), with `method` and `path` exactly as written in the code (template expressions as `${expr}`), plus `via` (`file:line function`). Do not resolve the base URL into the path; record it once as the top-level `api_base`.
- **`effects`** — what the action changes or touches: database reads/writes (name the table, collection or model), outbound HTTP (name the service), queue/topic publishes, emails, file/storage writes, cache, navigation, and notable UI changes. `type` must be one of {{EFFECT_TYPES}}; put specifics in `target` and `detail`.
- **`trigger`** (process, event-consumer) — `type` must be one of {{TRIGGER_TYPES}}.
- **`auth`** — one of {{AUTH_VALUES}}.
- **`area`** — a short product-area name in Title Case (e.g. "Articles", "Comments", "Account"). Reuse the same few area names across related actions.
- **`label`** — for UI actions, the text on the control or screen; otherwise a short verb phrase ("Create article").
- **`summary`** — one sentence in product language: what the user or system gets.
- **`aliases`** — 5–8 phrases a product manager, support agent, or new hire might use when asking about this action. Mix the on-screen wording with everyday synonyms for the nouns and verbs ("post"/"article"/"story", "add"/"create"/"write"/"publish", "sign in"/"log in").
- **Skip** tests, stories/fixtures, vendored third-party code, build tooling, and dead code nothing routes to.

## How Your Output Is Captured — read this before you start

This session is headless. **Only your FINAL message is captured.** Everything you print before it is discarded.

- Do all your investigating first. Print the inventory once, when you are finished.
- Your final message must be **exactly one fenced ```json block and nothing else** — no preamble, no closing commentary.
- Emit each `id` as a short kebab-case slug, unique within this repo (the harness prefixes it with the repo name).
- If you genuinely cannot produce the inventory, make your final message a single line beginning `{{CANNOT_COMPLETE_SENTINEL}}` followed by the reason.

## Output Schema

The example values below are illustrative — they show the shape for each kind, not content to copy. Omit keys that do not apply to a kind (`surface`/`calls` are for ui-actions, `route`/`declared_at` for api-routes, `trigger` for processes and event consumers).

```json
{{SCHEMA_EXAMPLE}}
```
