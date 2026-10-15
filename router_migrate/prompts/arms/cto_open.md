Put this application's production LLM traffic on Ramp's Router without breaking anything. Then act as its CTO: with
everything Router offers (every model, tier, fallback, strategy and surface in the fact sheet) and everything you learn
about the product, decide what this system should become. Nothing is out of scope: faster or slower, a different
priority, a different model, a step made deterministic or with no LLM at all, a call fixed or removed, a redesign of how
the product uses models. Rank what you'd change by value, each with a level (quick-win, change, rework); build and test
what you're sure of, propose the rest with `"branch": null` for an apply session to build after approval. A human
approves or undoes each decision on a dashboard.

{CONTEXT}

Hard rules: existing tests unchanged; nothing lost without saying so; never print or commit a key; only
`router-migrate/*` branches (migration on `router-migrate/migration`, one commit per undoable decision).
Write `.router-migrate/MIGRATION.json` in the report format above, then run `router-migrate finish .` and fix what it
reports.
