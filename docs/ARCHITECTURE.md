# Architecture (v6)

router-migrate is a light harness around an agent that does the work. The agent migrates a repository's LLM traffic onto
Router, reviews the system for improvements, and later applies and undoes what a human asks. Code supplies facts, a
sandbox, live Router access, an outside check the agent can't influence, and the runtime that routes each request in
production. Design rationale: [METHODOLOGY.md](METHODOLOGY.md); the full plan: [plans/v6-agent-first.md](plans/v6-agent-first.md).

## Layers

```
┌─ 1. Agent (a Claude Code session on any model, or headless `claude -p`) ───────────────────────────────────────┐
│  prompts: migrate_v6.md (15 lines: migrate, then a CTO's review of the whole product), apply.md; facts: ROUTER.md │
│  reads the repo, changes it, tests it (simulator, or live with the key), commits, writes MIGRATION.json       │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        │ asks for facts                                         │ commits + MIGRATION.json / APPLY.json
┌─ 2. Toolbelt (CLI; all optional) ───────────────┐   ┌─ 3. Checks (code; the agent can't influence them) ────────┐
│  check --all | FILE:LINE | --run CMD [--live]   │   │  outside check: parse · typecheck · tests with Router on   │
│  check --final [--tests]   dry run              │   │  vs the baseline · existing tests unchanged · Router       │
│  levers   lever table, catalog, same family,    │   │  accepts the requests · route truth · flags (deleted       │
│           the old rules as hints                │   │  files, dependencies, provider keys, runtime edits)        │
│  wrap / unwrap / adapt   codemods, trade-offs   │   │  on the migration, every improvement branch, after apply  │
└─────────────────────────────────────────────────┘   │  runtime (in the app): Router or provider, per request    │
                                                       └───────────────────────────────────────────────────────────┘
┌─ 4. Human loop ────────────────────────────────────────────────────────────────────────────────────────────────┐
│  dashboard (REPORT.html + local server): one item model; requests → Apply hands the list to an agent session   │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Flow

| Step | Who | What happens |
|---|---|---|
| `start` | code | Refuses a dirty tree; creates branch `router-migrate/migration` from HEAD; excludes `.router-migrate/` from git; starts the baseline (tests, typecheck) in the background; with a migration key, saves the live catalog; prints the instructions with this repo's context and the scanner's map |
| migrate + improve | agent | Serves each production call through Router however is best; one commit per decision; labels features; live checks; reviews the whole product as its CTO, at three levels (quick-win, change, rework): builds what it is sure of as branches `router-migrate/improve/<name>`, proposes the rest unbuilt, and files choices only the human can make as decisions; writes `MIGRATION.json` |
| `finish` | code | Requires everything committed; the outside check on the migration (the checkout) and on each improvement branch (temporary worktrees); route truth; `validation.json`; the dashboard. Slow suites (> 90 s) run in the background and Apply waits. Never reverts |
| review | human | Approve / dismiss / postpone improvements and decisions; mark changes for undo (dashboard or `router-migrate request`) |
| `apply` | agent | Gets every pending request at once and decides how to carry them out (building approved proposals from their description; account settings are written up for the human); commits each result; writes `APPLY.json` |
| `apply --done` | code | Validates `APPLY.json`, runs the outside check, appends the session to `apply_log.json`, refreshes the report |

Headless, `router-migrate run <repo> --agent claude` runs `start`, a session with the same prompt, and `finish`; when
`finish` fails it resumes the session once with the failure facts. `router-migrate apply <repo> --agent claude` does the
same for an apply session. The dashboard's Apply starts a headless apply session when the `claude` CLI is logged in, and
otherwise shows the Claude Code command.

## Components

| Module | Role |
|---|---|
| `harness.py` | `start`, `finish`, `apply`, `request`, `run`, the outside check (`check_tree`), branch validation, route truth, live checks, `levers`, the headless launcher |
| `report_v6.py` | Validation of `MIGRATION.json` and `APPLY.json`, item status, requests, `apply_blocked`, the migration key |
| `live_proxy.py` | The recording pass-through to the real Router for `check --run --live` |
| `route_check.py` | Does each call route as the code stands: the scanner's sites through the real runtime's planner |
| `runtime/router_runtime.py`, `runtime/routerRuntime.ts` | The vendored runtime (see below) |
| `sim/` | router-sim: a local Router built from measured behaviour (outside check, `check --run`) |
| `dashboard/items.py`, `dashboard/v6.html` | The v6 dashboard: bundle from the state files and git, live server, REPORT.html |
| `scan_py.py`, `ts/tool.mjs`, `plan.py`, `apply_py.py` | Scanner and codemods behind `check --all`, `wrap`, `unwrap` |
| `recommend.py` | The v2/v3 rules; in v6 only hints printed by `levers` |
| `catalog.py`, `data/` | Catalog snapshot, Flex list, `ROUTER.md` (the fact sheet) |

## State

Everything lives in `<repo>/.router-migrate/` (excluded from git) plus git itself. Formats are frozen in
[router_migrate/schemas/V6_CONTRACTS.md](../router_migrate/schemas/V6_CONTRACTS.md).

| File | Written by | Holds |
|---|---|---|
| `ledger.json` | code | base commit, baseline, live/simulator, probes and live checks, sessions, costs |
| `MIGRATION.json` | agent | `summary`, `reviewed`, `calls[]`, `items[]` (one item format for moves, trade-offs, ports, calls that stay, needs-decision, improvements, notes) |
| `validation.json` | code | the outside check per branch: failures, flags, tests, typecheck, Router requests, route truth |
| `requests.json` | dashboard / `request` | apply / undo / dismiss / postpone per item, with etags |
| `APPLY.json` → `apply_log.json` | agent → code | each apply session: summary, per-item results and notes, commits, the check after |
| `catalog_live.json`, `baseline.json` | code | the live catalog (with a key); the "before" test and typecheck results |

## The runtime: one request

1. **Active?** No `RAMP_ROUTER_API_KEY`, or `ROUTER_MIGRATE_DISABLE=1`: the request goes to the provider untouched.
2. **Plan.** Classify the request: Chat Completions, Responses, Anthropic Messages, or something else.
3. **Hard limits.** No Router endpoint, provider-side state, a non-catalog model, an unapproved beta: provider.
4. **Translate.** Chat Completions becomes a Responses request; the response is mapped back, streams included.
5. **Feature label.** The app's own label from the `x-router-migrate-feature` request header when it passes one, else
   the calling `file:function` from the stack. It goes on every Router request (and into `metadata` on Responses).
6. **Policy.** `router.policy.json` entries (model_map, drop_params, and on Responses the Router-only fields).
7. **Send.** To Router with the Router key. A refusal Router makes before generating that the provider can serve goes
   to the provider once.
8. **Errors.** Anything unexpected in planning sends the original request to the provider unchanged.

## The outside check

On the tree at the migration branch head (and in a temporary worktree per improvement branch), against the base commit:

1. **Parse** every changed file.
2. **Existing tests unchanged:** an existing test file edited or deleted fails; new tests are fine.
3. **Flags for the human** (never failures): deleted files, dependency files changed, provider-key references removed,
   the vendored runtime edited by hand.
4. **Typecheck** no worse than the baseline, when the repo has one.
5. **Tests with Router on** (the runtime pointed at router-sim), no worse than the baseline; when worse, one Router-off
   run says whether the change or Router is the cause.
6. **Router validity:** no request rejected by router-sim.
7. **Route truth:** verified (the route check, or a valid simulated or live request from the call's feature),
   unverified, reported moved but not routing, stays with the reason.
8. **The report** validates against the contracts; each improvement branch exists.

It never edits the tree. A failure blocks Apply for improvements; undo requests can always run.

## CLI

| Command | For |
|---|---|
| `start [PATH] [--test-cmd C] [--typecheck-cmd C]` | Set up and print the instructions (Claude Code) |
| `finish [PATH] [--no-dashboard] [--no-browser]` | The outside check and the dashboard |
| `run PATH --agent claude [--model M]` | Headless start → agent → finish |
| `request ITEM apply\|undo\|dismiss\|postpone [--reason R] [--until D]` | Record what you want for an item |
| `apply [PATH]` / `apply --done` / `apply PATH --agent claude` | The apply session |
| `check --all \| FILE:LINE \| --run CMD [--live] \| --final [--tests]` | Agent tool: route facts, sandbox, live, dry run |
| `levers [MODEL\|FILE:LINE]` | Agent tool: lever table, catalog, same-family models, hints |
| `wrap \| unwrap FILE:LINE`, `adapt FILE:LINE --LEVER --loses ...` | Optional agent helpers |
| `dashboard [PATH]` | REPORT.html and the live dashboard |

## Environment variables

| Variable | Effect |
|---|---|
| `RAMP_ROUTER_API_KEY` | In the app's environment: turns the runtime on (production traffic goes to Router) |
| `RAMP_ROUTER_BASE_URL` | Router base URL (default `https://api.router.com`) |
| `ROUTER_MIGRATE_DISABLE=1` | Kill switch: everything goes to the provider |
| `ROUTER_MIGRATE_KEY`, `ROUTER_MIGRATE_KEY_FILE` | The migration key for live checks (default file `~/.config/router-migrate/key`) |
| `ROUTER_MIGRATE_FEATURE_TAG=off` | No stack-derived feature labels (labels the app passes still apply) |
| `ROUTER_MIGRATE_POLICY=off` | Ignore the compiled policy |
| `ROUTER_MIGRATE_TRACE=<file>` | One JSON line per routing decision (used by `check --run`) |
| `ROUTER_MIGRATE_NO_BROWSER=1` | Don't open a browser for the dashboard |
