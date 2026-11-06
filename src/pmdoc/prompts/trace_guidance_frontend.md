This is a **frontend** repo — the user-facing layer, so it is the main source of the product story. Trace the code to recover behavior, and for every finding ask *"what does the user see or do as a result?"*

1. **How the user gets there** — the navigation, links and screens this functionality lives in, and who can reach them (signed in, guests, owners only), in plain product terms.
2. **Every user-triggered scenario** — for each action (click, type, submit, open, cancel, delete), what changes on screen. If findings from backend repos are included below, use them to name the exact rules and failure cases the screen reflects.
3. **Visual states** — enumerate every field, label, placeholder, button, link, message, error, loading and disabled state, and the condition for each. Quote on-screen text literally. This trace IS the recovery of lost design documentation — be exhaustive (match the detail of a thorough acceptance-criteria ticket).
4. **Rules & behaviors** — what is required vs optional, what is checked before anything is sent, what happens on success and on failure, and where the user ends up afterwards.
5. **Edge cases & quirks** — deliberate rules vs incidental behavior. Flag uncertainty rather than guessing.
