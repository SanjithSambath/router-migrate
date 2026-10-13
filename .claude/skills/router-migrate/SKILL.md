---
name: router-migrate
description: Move a codebase's production LLM API calls (OpenAI, Anthropic, LangChain, Vercel AI SDK, LlamaIndex, LiteLLM; Python and TS/JS) onto Ramp's Router gateway with the router-migrate CLI, finish the agent handoff tasks, and verify nothing broke. Use when asked to "migrate to Router", "route LLM traffic through router.com", or "use RAMP_ROUTER_API_KEY".
---

# router-migrate

Use the CLI for everything deterministic and do only the judgment calls by hand. The full agent protocol is in AGENTS.md at the
router-migrate repo root. Read it first.

## Steps
1. Locate the CLI: `router-migrate` (or `<router-migrate checkout>/bin/router-migrate`). Run `router-migrate --help` once.
2. Preview first: `router-migrate scan .`, then `router-migrate plan . --dry-run` to see the diff. Show the user the summary line.
3. `router-migrate apply .`, which vendors the runtime and patches client constructions. Nothing changes at runtime until
   `RAMP_ROUTER_API_KEY` is set.
4. Open `.router-migrate/HANDOFF.md`. For each task, make the smallest change that satisfies its "Done when" line, using the facts
   in AGENTS.md (endpoints, model ids, stop/temperature/store rules).
5. `router-migrate verify . --test-cmd "<repo test command>"`. Loop until tests after match tests before and the sim replay is
   all valid. If the user provided a key and a budget, add `--live --budget <usd>`.
6. `router-migrate report .`, then summarize `.router-migrate/REPORT.md`: migrated / handoff / kept / flagged, the coverage numbers,
   the test results, and anything left on the provider and why.

## Guardrails
- Never print or commit the key. Never edit tests to make them pass. Never swap a model for a different one without saying so.
- Stop and tell the user on any 402/429 from Router.
