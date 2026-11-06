# Resolve a Product Question to Actions — project `{{PROJECT_NAME}}`

A product manager asked a question about this product. Your job is to pick, from the action index below, the actions a documentation writer must trace to answer it completely. You have no tools and need none: everything you need is in this message.

## The Product

{{PROJECT_DESCRIPTION}}

## The Question

> {{USER_PROMPT}}

## Action Index

One JSON object per line. `kind` is `ui-action` (a control on a screen), `api-route` (an HTTP endpoint), `process` (scheduled or scripted work) or `event-consumer` (work triggered by a message). `chains` names the cross-repo chains an action belongs to: a ui-action heads a chain that includes the routes it calls.

{{ACTION_INDEX}}

## How to Choose

- Translate the question's words into the product's: "post" may be an article, "sign in" may be login, "add" may be create or publish. Match on meaning, using `label`, `aliases`, `summary` and `trigger`.
- Pick the **smallest set of actions that fully covers the question**. Prefer the user-triggered head of a chain (the ui-action or process); its chain's routes are added automatically.
- Include more than one chain only when the question genuinely spans them (e.g. "editing and deleting a post", or a flow that a background process completes later).
- Use ids **exactly** as written in the index.
- Set `clarification_needed` to a one-sentence question only when the question is genuinely ambiguous between unrelated areas of the product. Otherwise `null`, and pick your best match.
- `story_id_suggestion`: 2–4 lowercase words joined by hyphens, naming the functionality (e.g. `add-post`, `favorite-article`). No underscores. Avoid these existing ids: {{EXISTING_SLUGS}}.

## Output

Reply with exactly one fenced ```json block and nothing else:

```json
{
  "story_id_suggestion": "add-post",
  "matched_action_ids": ["<id from the index>"],
  "repos_involved": ["<repo>"],
  "rationale": "one or two sentences: how the question maps to these actions",
  "clarification_needed": null
}
```
