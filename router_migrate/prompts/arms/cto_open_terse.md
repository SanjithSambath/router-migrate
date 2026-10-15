Move this app's production LLM traffic onto Ramp's Router without breaking it, then, as its CTO, decide what the product
should become with everything Router offers. Rank changes by value with a level (quick-win, change, rework); build what
you're sure of, propose the rest with `"branch": null`. A human approves each on a dashboard.

{CONTEXT}

Rules: tests unchanged; nothing lost unsaid; no keys printed or committed; only `router-migrate/*` branches, one commit
per undoable decision. Write `.router-migrate/MIGRATION.json` (format above), run `router-migrate finish .`, fix what it
reports.
