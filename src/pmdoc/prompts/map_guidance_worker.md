This is a **worker** — its inventory is mostly `process` and `event-consumer` actions.

1. **Find every trigger** — cron/schedule definitions, queue/topic subscriptions, stream and storage triggers, webhook receivers, CLI entry points, and import/export or sync scripts.
2. **One action per trigger.** Record `trigger.type`, `trigger.detail` (the cron expression, queue or topic name, event source), and `trigger.declared_at` (`file:line`).
3. **Open each handler** and record its `entry` and every effect — tables/collections it reads and writes, outbound HTTP calls (name the service), messages it publishes, emails, files it writes.
4. If the repo also serves HTTP routes, record each as an `api-route` (one per method and path).
