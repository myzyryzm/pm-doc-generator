This is a **frontend** — the layer users touch, so its inventory is mostly `ui-action`s.

1. **Find the router** (route declarations, file-based routes, or a navigation config) and list every screen, noting which require sign-in and which are guest-only.
2. **Find the HTTP layer** — the module(s) wrapping fetch/axios/GraphQL. Record its base URL as top-level `api_base` (resolve it from config or env defaults; if it comes from an environment variable, record the default value, or the variable name if there is none).
3. **For each screen, list every control**: buttons, form submits, menu and tab links, toggles, pagination, delete/confirm flows, and the data that loads when the screen opens ("Open article page"). Follow each handler to the function that does the work — handlers are often passed as props through shared components, so trace the prop chain back to its source — and record every HTTP call it makes.
4. **Navigation-only controls**: include the main navigation entries (header/menu links that are the way to reach a screen) as `ui-action`s with a `navigate` effect and no calls. Skip incidental links.
5. If the repo also serves HTTP routes or runs background jobs, record those too.
