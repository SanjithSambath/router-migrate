# Plan: v6, agent-forward migrate, improve, apply and undo

Status: implemented as an MVP on 2026-10-14 (first runs: `eval/v6/RESULTS.md`; open items listed there). Scope: a comprehensive MVP, not a production-grade system.

Why: in the agent.email test, migration worked but Improve produced nothing. The model sat behind a wrapper
(`complete(args)` with constants in `models.ts`), the rules engine could not see it, and the agent had no way to hand
over what it understood. The deeper cause: v5 let deterministic code decide things that take understanding (which
improvements exist, which edits are allowed, how to apply and undo), with the agent filling in forms around it.

## Principle

The agent does the work and makes every decision that takes understanding: migrating, improving, applying what the
human approves, and undoing what the human rejects. The harness is a light layer around it: facts, a sandbox, Router
access, an outside check the agent can't influence, and the human's approval.

| Stays deterministic | Why |
|---|---|
| The vendored runtime's per-request decision (Router or the original provider) | It runs in production on every request; there is no LLM there |
| The outside check: parse, typecheck, tests with Router on vs the baseline, existing tests unchanged, Router accepts the requests, route truth | Measurement, not judgment; its value is that the agent cannot influence it |
| Facts: catalog, prices, Flex eligibility, which fields apply on which surface, measured behaviour | True or false; the agent asks, the code answers |
| Plumbing: recording the human's decisions, compiling `router.policy.json`, launching sessions, the dashboard | No judgment involved |

Everything else is the agent's: what each call is for, which model, provider, API surface or SDK serves it, who waits on
it, what to change and how much, in what order to apply approved work, and how to back out rejected work.

## Router key: the methodology assumes one

It works without a key (offline simulator, catalog snapshot), and works best with one. Use a **dedicated migration key
with a Router spend cap** (e.g. $10-20), separate from production. Never printed, logged or committed.

| | Without a key | With a key |
|---|---|---|
| Does Router accept this request? | Simulator (built from measured rules; unknown shapes pass or fail by guess) | Real Router requests from the migrated code paths |
| Catalog and prices | Snapshot with its date | Live |
| A proposed model change | "Answers will differ; unverified" | The same, plus optional samples through both models when a few requests would settle a doubt (not required: MVP) |
| Facts like "does Router take Anthropic server tools on `/v1/messages`?" | Unknown | Measured during the run and written to the fact sheet |

Every step and every hand-off (migrate → improve → `finish` → dashboard → apply session → check) runs the same with or
without a key. Only the evidence differs, and each card says which it had: a "Router live" line reads either the live
result or "not checked: no key (simulator only)". The migration key is also separate from going live: production
traffic starts only when `RAMP_ROUTER_API_KEY` is set in the app's own environment.

The spend cap is the hard limit; the prompt also gives the agent a budget. With the key present the vendored runtime is
live in the agent's own runs, so its ad-hoc test runs send real (capped) traffic; the outside check still runs against
the simulator so its result is reproducible.

## The agent's range

Nothing ties a call to its current model, provider, API or SDK. If Router can't serve a call as written (e.g. Anthropic
server tools on `/v1/messages`), the agent may port it: to the Responses surface and its web search, to another SDK, to
another vendor's model, or restructure the code around it. It is judged on outcomes, not on which edits it made.

| Today (v5) | v6 |
|---|---|
| Lockfiles and package manifests may not be edited (reverted) | Allowed. Checked by outcome: install from the lockfile works, tests pass |
| Files may not be deleted (reverted) | Allowed. Shown on the dashboard; tests judge the result |
| Provider-key references must stay (reverted) | Outcome instead: nothing the app could do before is lost. Capability removals are flagged for the human |
| The vendored runtime may not be edited (reverted) | Flagged and shown as a diff for approval |
| Tests may not be edited | Kept: existing tests stay unchanged (the one way the agent could grade itself); new tests may be added |
| `finish` gives one fix pass, then reverts the offending files | `finish` runs once and reports. The agent iterates with `check --final` as often as it likes. A failing result blocks Apply; nothing is reverted behind anyone's back |
| Headless agent: no web access, no Router key | Web access (Router and SDK docs); the migration key when provided |
| Prompt framed as "smallest change; wrap the client" | "Serve this app's traffic through Router however is best", within the outcome constraints |

## Git is the record

Every change is a commit, so anything can be shown, applied or undone with ordinary git, and by the agent.

- `start` requires a clean working tree, creates the branch `router-migrate` from HEAD, and keeps its own state out of
  git (`.router-migrate/` goes in `.git/info/exclude`; the repo's `.gitignore` is untouched).
- The migration agent commits **one commit per decision a human might want to reverse**: the base routing, each
  trade-off, each port. `MIGRATION.json` names the commit for each decision.
- Each improvement is a branch off the migration, `router-migrate/improve/<name>`, with the agent's reasoning in
  `MIGRATION.json` (and the commit messages).
- Nothing is merged into the user's main branch by the tool. `router-migrate` is the branch they review and merge.

## Flow

```
router-migrate start .                        code: clean tree required; branch router-migrate; baseline tests and
                                              typecheck in the background; live catalog + Router fact sheet (snapshot
                                              without a key); the scanner's map as a hint
  ── agent: migrate ─────────────────────────────────────────────────────────────────────────────────────────────
     find every production LLM call (the map is incomplete); serve each through Router however is best (wrap, port,
     restructure), in the repo's own style; label requests per feature where the app knows; live-check with the key;
     one commit per decision; say what can't move and why
  ── agent: improve (same session or a sub-agent; its choice) ───────────────────────────────────────────────────
     a bird's-eye review of the system on Router; what is worth building becomes a branch, built and tested
     (optional samples through both models with the key); the rest becomes short notes; if nothing, one line on what was
     reviewed and why nothing was proposed
router-migrate finish .                       code: the outside check on the migration branch and on each improvement
                                              branch (background when the suite is slow); REPORT.html + dashboard
  ── human: dashboard ───────────────────────────────────────────────────────────────────────────────────────────
     every call (verified / unverified / stays), every decision with its commit, every improvement with its diff,
     evidence, gain, loss, price and what was checked; approve / dismiss / postpone improvements; mark any decision
     or applied improvement for undo
     Apply → writes the list of requests and starts an apply session
  ── agent: apply session ───────────────────────────────────────────────────────────────────────────────────────
     input: the whole list (apply A, B; undo T2 and the port of X) + the branches, commits and MIGRATION.json
     it chooses how: order, merges, conflict resolution, partial reverts, adapting later work to an undo; commits
     each result; says what it did, and anything it declined or couldn't do and why
router-migrate check --final --tests          code: the outside check on the result; the dashboard shows it
```

Two entry points, same prompts and code:
- **Claude Code** (any model): `/router-migrate` runs `start`, the session migrates and improves, then `finish`. Apply
  in the dashboard shows the one command to paste (`/router-migrate apply`); the session reads the request list.
- **Headless** (`router-migrate run <repo> --agent claude [--model M]`): the launcher runs the same sessions; Apply in the
  dashboard starts a headless apply session when the agent CLI is available, else shows the command.
- Other agents follow AGENTS.md.

## Architecture

```
┌─ 1. Agent (Claude Code session, any model; or headless) ───────────────────────────────────────────────────────┐
│  three prompts: migrate, improve, apply-and-undo; with the Router fact sheet (data/ROUTER.md)                   │
│  reads the repo, changes it, tests it (simulator, or live with the key), commits, reports                       │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        │ asks for facts                                         │ commits + MIGRATION.json
┌─ 2. Toolbelt (CLI; all optional) ───────────────┐   ┌─ 3. Checks (code; the agent can't influence them) ────────┐
│  check --all | FILE:LINE | --run CMD [--live]   │   │  outside check: parse · typecheck · tests with Router on   │
│  check --final [--tests]   dry run              │   │  vs baseline · existing tests unchanged · Router accepts   │
│  levers   catalog, prices, Flex, surfaces,      │   │  the requests · route truth · capability removals flagged  │
│           old rules as hints                    │   │  on the migration branch, each improvement branch, and     │
│  wrap / unwrap / adapt   codemods, trade-offs   │   │  after every apply session                                 │
└─────────────────────────────────────────────────┘   │  runtime (in the app): Router or provider, per request    │
                                                       └───────────────────────────────────────────────────────────┘
┌─ 4. Human loop ────────────────────────────────────────────────────────────────────────────────────────────────┐
│  dashboard (REPORT.html + local server): decide → Apply hands the list to an apply session → check → dashboard  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

State in `.router-migrate/` (excluded from git):

| File | Written by | Holds |
|---|---|---|
| `ledger.json` | code | baseline, the starting commit, check results, route truth, session costs |
| `MIGRATION.json` | agent | `summary` (one paragraph); `calls[]` (site, model, task, latency, feature label, where it goes now); `items[]` in the one item format (see Dashboard); the "reviewed, nothing proposed because" line when Improve is empty |
| `validation.json` | `finish` | per branch: outside-check result, tests, Router requests |
| `requests.json` | dashboard / `decide` | what the human wants: apply, dismiss, postpone, undo; with etags |
| `apply_log.json` | agent (apply session) | what it did per request, what it declined or couldn't do, and why |

## What changes in the code

| Module | Lines today | MVP change |
|---|---|---|
| `recommend.py` | 680 | Delete the guessing heuristics and the rules-to-cards gate; `levers` prints the rules as hints next to the facts |
| `proposals.py` | 137 | Delete |
| `improve.py` | 729 | Replace `recommend`, `agent_cards`, `apply_decisions` and the trade-off undo path with: read improvements from MIGRATION.json, write `requests.json`, start the apply session. Keep `decide`, etags, `bundle`, the test-status gate, the policy compiler |
| `harness.py` | 621 | `start`: clean tree, branch, git exclude, key detection, live catalog. `finish`: runs once, checks the migration and each improvement branch, no revert loop. New: the apply session (prompt + request list; headless launcher or the Claude Code command). Headless launcher gets web tools and the key when provided |
| `agent.py` | 2060 | Delete `compile_guardrails`, `undo_tradeoff`'s mechanical path (the agent undoes), the scope gate's edit-category bans; scanner wording ("not linked", not "outside this repo") |
| `router_runtime.py` / `routerRuntime.ts` | 1422 / 1227 | Use a feature label the app passes per request (header) when present, the stack trace otherwise; the policy hook copies the import the agent wrote. Routing and fallback unchanged |
| `route_check.py`, sim | — | `check --run --live`: run a command with the real key and report Router's answers |
| `dashboard/page.html` | 1638 | Improvement cards on the existing card format (diff, evidence, side-by-side results, what was checked); Undo on any decision; Apply hands off the request list and shows the apply session's log; the agent's "nothing proposed because" line |
| `prompts/` | — | `migrate.md` reframed; `improve.md` (bird's-eye review); `apply.md` (the request list in, the agent chooses how) |
| `data/ROUTER.md` | new | The fact sheet: what Router is for, every lever, where each applies, cost, measured vs unknown; updated by live runs |

Net effect: roughly 900 lines of heuristics, gates and apply/undo special cases removed; a few hundred added (branch
handling in `start`/`finish`, the apply session, the per-request label, live check, dashboard hand-off).

## Component by component: current vs v6

### Improve

| | Current (v5) | v6 |
|---|---|---|
| Who finds improvements | Rules engine; the agent labels latency/task/cacheable and may add 6 kinds of proposals | The agent, from a whole-system review; rules are hints it can ignore |
| Scope | Flex, fallbacks, cache key, model, vendor port, other | Anything that uses Router better: models per tier, Flex for work nobody waits on, fallbacks instead of hand-rolled retries, cache-friendly prompts, consolidating clients, moving calls to the surface where Router's levers apply, cross-vendor models, spend tags, timeouts that fit the app |
| Depends on | Regex signals (model owner, latency from file names, prompt length, family) | The agent reading the code; facts from `levers` |
| Output | A policy line or a sentence, untested | A branch the agent built and tested, or a note |
| Evidence | Catalog checks only | The outside check per branch; optional samples through both models for a model change; cards say exactly what was checked |

### Apply and undo

| | Current (v5) | v6 |
|---|---|---|
| Who applies | `apply_decisions`: compiles policy entries or re-wraps a client; skips model cards | The agent, given the whole list of approved improvements |
| Who undoes | `undo_tradeoff`: removes policy keys, re-wraps; hand edits and applied improvements can't be undone | The agent, given the whole list of undo requests (any decision, port, trade-off or applied improvement) |
| How | Fixed per lever | The agent's choice: merge order, conflict resolution, partial reverts, adapting later work to an undo; one commit per result; anything declined is explained |
| Verified by | A quick parse/scope gate | The full outside check after the session; the dashboard shows the result and the agent's log |

### Migration

| | Current (v5) | v6 |
|---|---|---|
| Method | Agent-first, but framed as "wrap in place" and fenced by edit-category bans | "Serve this traffic through Router however is best"; ports and restructuring allowed; outcome checks |
| Latency guardrails | Written automatically (fixed 30 s timeout, tier pin) | Deleted; the agent sets them from the app's own timeout and retry code where they apply |
| Feature labels | Stack traces (meaningless after bundling) | The agent wires the app's own labels per request; stack trace as a labelled fallback |
| Import style | Fixed form (broke agent.email's typecheck) | The agent's job; the outside check catches misses |
| Live verification | None | With the key: real Router requests from the migrated paths; facts recorded |
| Record | Ledger entries keyed by `FILE:LINE` | Commits, one per decision, named in MIGRATION.json |

### Runtime

Routing and fallback unchanged (Router is treated as the stable path; what it accepts is measured with the key).
Changes: the per-request feature label; the policy hook copies the agent's import.

### Outside check

Same role. It drops the edit-category bans (lockfiles, deletions, provider-key references), keeps "existing tests
unchanged", flags capability removals and runtime edits for the human, runs on every branch and after every apply
session, and never reverts anything.

### Dashboard: one item model, rendered uniformly

Everything the agent produces reaches the dashboard as the same kind of **item**, so every page is consistent and the
dashboard is a renderer, not a collection of special cases. The agent is free in what it does; the item format only
fixes how it is presented.

**Item** (written by the agent in `MIGRATION.json` `items[]`; code adds what it measured):

| Field | Written by | Content |
|---|---|---|
| `id`, `kind` | agent | `move` (a call now on Router), `trade-off` (a move with a cost), `port` (changed API, SDK or model to move), `stays` (can't move, and why), `needs-decision`, `improvement`, `note` |
| `title`, `summary` | agent | Title of at most 8 words; one plain sentence: what changes, for which calls |
| `calls` | agent | The call sites and feature labels it covers |
| `gains`, `loses`, `price` | agent (price from catalog numbers) | Concrete, with numbers where there are any; `loses` is "nothing" only when true |
| `why` | agent | Its reasoning, a short paragraph |
| `evidence[]` | agent and code, each labelled by source | What was checked and the result: tests vs baseline, typecheck, Router live, simulator, side-by-side samples, prices |
| `commit` / `branch` | agent | Where the change lives; the dashboard shows the diff from git |
| `depends_on`, `excludes` | agent | Other items it needs or conflicts with |
| `status` | code | `proposed · approved · dismissed · postponed · applied · undo requested · undone · check failed` |

**One card anatomy** for every item, everywhere:

```
┌─ [kind]  Title                                                     [status] ┐
│  One-sentence summary · calls it covers                                      │
│  You gain …          You give up …          Price …                          │
│  Checked: tests 947 pass (same as before) · Router live ✓ · 10 side-by-side │
│  ▸ Why (the agent's reasoning)   ▸ Diff (from git)   commit / branch        │
│  [Approve] [Dismiss] [Postpone]                              or   [Undo]    │
└──────────────────────────────────────────────────────────────────────────────┘
```

Evidence is never a bare ✓. Each line says what ran and against what. A change that alters answers says
"answers will differ", with the side-by-side samples when the key allowed them.

**Pages** (all built from items, plus the check results):

1. **Overview:** three numbers (traffic on Router: verified / unverified / stays · behaviour: the outside check · waiting
   on you), the agent's one-paragraph summary of what it did and why, and the "nothing proposed because" line when
   Improve is empty.
2. **Calls:** every production LLM call in one table: where it goes now, its feature label, and a link to its item.
3. **Changes:** every item (migration decisions and improvements) as cards, filterable by kind and status.
4. **Your queue:** only the items waiting on you, with one Apply button that hands the whole list to the apply session.
5. **History:** each apply session's log (what it did, what it declined and why), its commits, and the check after it.
6. **Router setup:** the existing checklist.

REPORT.html is the same pages, frozen. Writing rules for the agent (titles, one-sentence summaries, concrete
gains and losses, plain language) live in the prompt, so every repo's dashboard reads the same way.

## Implementation steps (MVP)

1. **Harness basics** (parallel with 2): clean tree, branch and git exclude in `start`; `finish` runs once on every
   branch, no revert loop; the scope gate's bans become outcome checks and flags; key detection, live catalog,
   `check --run --live`; headless web tools; the runtime's per-request label and policy hook.
2. **Agent work in, rules out:** MIGRATION.json v6 (calls, commits, improvements, notes); delete the heuristics, the
   rules-to-cards gate, `proposals.py`, `compile_guardrails`, `agent_cards`, the mechanical apply/undo paths.
3. **Prompts and fact sheet:** `migrate.md`, `improve.md`, `apply.md`, `data/ROUTER.md` (including live measurements).
4. **Apply session:** `requests.json` from the dashboard; the Claude Code command and the headless launcher; the check
   after; `apply_log.json` on the dashboard.
5. **Dashboard:** one item model and one card component for every kind; the six pages (Overview, Calls, Changes, Your
   queue, History, Router setup) built from items; diffs from git; the Apply hand-off; REPORT.html from the same pages.
6. **Docs**, then end to end with a migration key on agent.email plus 2-3 eval repos with test suites, the agent doing
   all of it (migrate, improve, apply two, undo one).

## Deferred (not MVP)

- New runtime fallback logic (Router is treated as the stable path; revisit if live measurements show gaps).
- Shadowing real production traffic for model changes (MVP uses side-by-side samples with the migration key).
- JSON test reporters; function-based site identity.
- Opening PRs (the `router-migrate` branch is the hand-off).

## Verification

- Unit: clean-tree refusal and branch setup; `finish` on several branches without reverting; per-request labels reach
  Router from a Next.js production build; the request list round-trips from the dashboard to the apply session.
- Apply and undo, end to end with an agent: approve two improvements that touch the same file and undo one migration
  decision that a later improvement depends on; the result passes the outside check and the log explains each choice.
- Improve is scored with a rubric written before any run: did it find what an expert would, are the branches correct,
  are its claims and estimates true (checked against live measurements where the key allows), did it avoid behaviour
  changes nobody asked for. Held-out repos plus agent.email; no expected answers written down for any repo; k = 1
  stated as a limitation.
- Dashboard: a fixture with one item of every kind and status renders with the same card on every page, at desktop and
  phone width, in REPORT.html and live; a report that breaks the item format fails `finish` with the exact field.
- Success, in priority order: traffic moved, behaviour preserved, then improvement quality.

## Risks

- **Spend.** A dedicated key with a Router spend cap is the hard limit; the agent also has a budget in its prompt; costs
  per session go in the ledger and on the dashboard.
- **Large or speculative changes.** Every change is a branch or a commit, checked, and needs approval; the dashboard
  shows its size; the prompt says a smaller change with evidence beats a large one without.
- **The apply session does something unexpected.** It works on the `router-migrate` branch only, commits each step,
  logs its reasoning, and the outside check runs after; anything can be undone by a further request or plain git.
- **Model quality.** Side-by-side samples are evidence, not proof; model changes still need the human's approval.
- **Time.** Improve and apply add agent time; slow suites run in the background; the agent is told the suite's duration.
