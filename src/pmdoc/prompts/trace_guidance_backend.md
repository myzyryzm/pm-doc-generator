This is {{LAYER}} — it has **no UI**, so frame everything as the **rules behind what the user sees**. The reader never sees this system directly; they only see its consequences in the product.

1. **What triggers it** — in product terms, which user action or event sets this off (the Start Here section names the caller where it is known).
2. **What it checks** — sign-in and permissions, required fields and validation, and what the user gets back when each check fails.
3. **What it does to the user's data** — what is created or changed, what is generated automatically (identifiers, timestamps, derived values), and what is left untouched. Use the user-facing name of each piece of data, not its internal name.
4. **What else it sets in motion** — other systems called, messages or emails sent, background work started — and for each, whether it is guaranteed or best effort, and whether it happens immediately or later.
5. **If this is scheduled or event-driven work** — when it runs, what it picks up, and what the user notices afterwards (and roughly how long after).
6. **Edge cases & quirks** — deliberate rules vs incidental behavior. Flag uncertainty rather than guessing.
