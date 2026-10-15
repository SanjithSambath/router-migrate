Put this application's production LLM traffic on Ramp's Router without breaking anything, then decide, as a CTO who
knows Router well, what you would change about the system on Router, at any scale; a human approves or undoes each
decision on a dashboard.

{CONTEXT}

Hard rules: existing tests unchanged; nothing lost without saying so; never print or commit a key; only
`router-migrate/*` branches (migration on `router-migrate/migration`, one commit per undoable decision).
Write `.router-migrate/MIGRATION.json` in the report format above, then run `router-migrate finish .` and fix what it
reports.
