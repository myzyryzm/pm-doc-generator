This is a **backend** — its inventory is mostly `api-route`s, plus any background work it owns.

1. **Find where routes are declared** — framework router files, decorators, controller classes, a `serverless.yml` `functions:` block, an OpenAPI spec, or a GraphQL schema. Use the declaration as the source of truth for method and path, and record `declared_at` (`file:line`).
2. **One action per (method, path)** — even when a single handler serves several routes and branches internally (say which branch in the `summary`).
3. **Open each handler** and record: the `entry` (`file:line symbol`), whether authentication is required, and every effect — which tables/collections/models it reads and writes, outbound HTTP calls (name the service), queue/topic publishes, emails, file/storage writes, cache.
4. **Then look for background work** declared in this repo — scheduled functions, queue or stream triggers, webhooks, CLI scripts, import/export jobs — and record each as a `process` or `event-consumer` with its `trigger`.
5. Paths keep their real prefix (e.g. `/api/...`) and use `{param}` for path parameters.
