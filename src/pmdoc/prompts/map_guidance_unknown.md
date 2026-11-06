This repo's kind was not configured and could not be detected, so **first work out what it does**: read its README, its package/dependency manifest, and its top-level entry points.

Then apply whichever of these fit:

- **If it has screens**: find the router, list every screen, and record every control on each (buttons, form submits, navigation, data loaded on open) as a `ui-action`, following handlers to the HTTP calls they make. Record the HTTP client's base URL as top-level `api_base`.
- **If it serves HTTP**: record one `api-route` per (method, path) from the route declarations, with `declared_at`, the handler `entry`, auth, and effects (tables read/written, outbound calls, queues, emails).
- **If it runs background work**: record each schedule, queue/topic consumer, webhook, CLI command or import/export script as a `process` or `event-consumer` with its `trigger` and effects.
