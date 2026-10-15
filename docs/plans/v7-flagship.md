# Plan: v7, the flagship: a CTO's review of the product on Router

Status: implemented 2026-10-15 (phase 0 confirmed the default on held-out repos, 5-0; the fact-sheet swap was dropped:
a tie). Deviations: file names kept (`report_v6.py`, `migrate_v6.md`, `v6.html`) to avoid churn; `rank` is optional
(report order); router-settings never go to an apply session (the human marks them done). Evidence: `eval/lean/LOG.md` (rounds 1-7, blind head-to-head, five repos plus held-out).
Scope: an MVP for Kedar; when in doubt, remove rather than add.

## Why

v6 is shaped like a migration tool with an improvements add-on. The overnight experiments say the value is the
other way round:

- The prompt that won every head-to-head (15 lines) makes the agent the product's CTO with nothing out of scope. Its
  best output is product judgement: delete an inner agent that only calls its one tool, collapse a two-level agent so
  answers stream sooner, stop rebuilding the chat engine per message, shadow models before a model swap, Switchyard
  where turns vary, a spend cap on a bot strangers can trigger. v6's prompt produced about 1.7 improvements per repo,
  mostly model and cache tweaks.
- The strongest items all carry live numbers (cache -76%, 160x cheaper, 2.7 s faster), measured by the agent through
  `check --run --live` with a spend-capped key. Without a key there are no numbers.
- Its biggest ideas are product calls (a model change, an architecture change, an account setting) and land as
  `needs-decision`; v6's item model treated those as second class.
- Cutting procedure from the prompt helped every time; cutting facts (fact sheet, scanner map) or tools agents use
  (`policy`, `levers`) hurt every time.

## Principle

**The review is the product; the migration is the gate.** The run moves the traffic and proves behaviour is preserved
(that is what makes the review safe to act on), then the agent reviews the whole product with everything Router offers
and ranks what to change. A human approves; an apply session builds and checks it.

Unchanged from v6: the agent decides everything that takes understanding; code keeps the runtime, the outside check,
facts and plumbing; git is the record; apply and undo are agent sessions fed the human's requests.

## What changes

| Area | v6 | v7 |
|---|---|---|
| Prompt | ~150 lines of procedure | the 15-line open-scope prompt (already default on `lean`); no procedure is ever added back |
| Item kinds | 7: move, trade-off, port, stays, needs-decision, improvement, note | 4: `change`, `recommendation`, `stays`, `note` |
| Who acts on a recommendation | implicit (improvement = code, needs-decision = "you") | explicit `actor`: `code`, `router-setting`, `product-call` |
| Evidence | free-text `detail` | plus structured `measured` (metric, before, after, n, how) |
| Live key | optional, "works best with one" | the flagship path; simulator-only is a labelled fallback |
| Build vs propose | the agent builds improvements as branches | build only what it is sure of (cheap, contained); everything bigger is a proposal the apply session builds after approval |
| Router setup page | static steps (cap, Flex default, shadow) | go-live and off switch stay; the rest comes from this app's `router-setting` recommendations |
| Dashboard overview | traffic, behaviour, what waits on you | the top recommendations with their numbers first; traffic and behaviour shown as the gate |
| Fact sheet | hand-edited `ROUTER.md`, Router's strategies in one line | the whole product (section 1a of `ROUTER_open.md`), refreshed from live probes |
| Release gate | ad-hoc runs | pairwise eval vs the current default on held-out repos |

Removed: `unwrap` from the migrate agent's tools (never used; undo is the apply session's, through git); the static
setup steps; the improvement/needs-decision split; v6 code paths that only existed for the split.

## The run

| Step | Who | What happens |
|---|---|---|
| `start` | code | as v6: clean tree, branch, baseline in the background, live catalog, scanner map; with a key, live mode is on |
| migrate | agent | moves every production call it can; one commit per decision (`change` items); live checks through the app's own code paths |
| review | agent | reads the product as its CTO; measures what decides a recommendation (live, small n); builds sure quick wins on branches; files the rest as recommendations with level, actor and numbers; records what it considered and rejected (`note`) |
| `finish` | code | as v6: outside check on the migration and every built branch, route truth, validation, dashboard |
| decide | human | approve, dismiss, postpone, undo; one list, ranked |
| `apply` | agent | builds approved recommendations (from the branch, or from the description), writes up `router-setting` steps for the human, undoes what was rejected; one commit per result |
| `apply --done` | code | as v6: validates, outside check, logs the session |

## Agent layer

**Prompt** (`prompts/migrate_v6.md`, renamed `migrate.md`): the open-scope prompt as is: mandate, levels,
build-what-you're-sure-of, hard rules, report and finish. A change to it ships only through the release gate.

**Context the harness gives** (keep all; each cut hurt): the scanner map, the fact sheet path, the report format path,
test and typecheck commands, the key line.

**Tools** (`AGENT_COMMANDS`): `check` (including `--run [--live]`), `levers`, `wrap`, `adapt`, `policy`. Drop `unwrap`
from the migrate agent; the apply session keeps it.

**Facts** become the maintained asset:
- `ROUTER_open.md` replaces `ROUTER.md` (pending the open_oldfacts result; see Risks).
- `router-migrate facts --refresh` (key required): re-runs a fixed probe set (Flex served tier and discount on a few
  models, cache hit on Responses and Messages, fallback fall-through, `provider_timeout` 504 shape, Switchyard served
  model, server tools on Messages) and rewrites the measured lines with a date and source tag. Catalog-derived lines
  (counts, prices, Flex eligibility) regenerate from `GET /v1/models`. Anything not re-measured keeps its old tag.

## Report format (v7 contract)

```json
{"version": 7, "summary": "...", "reviewed": "...",
 "calls": [{"site": "app/llm.py:42", "feature": "chat", "model": "gpt-4o-mini", "task": "chat",
            "latency": "user_waiting", "now": "router", "item": "c1"}],
 "items": [
  {"id": "c1", "kind": "change", "title": "Route the shared OpenAI client", "summary": "...",
   "calls": ["app/llm.py:42"], "gains": "...", "loses": "nothing", "commit": "abc1234", "evidence": [...]},
  {"id": "r1", "kind": "recommendation", "level": "rework", "actor": "code", "rank": 1,
   "title": "Collapse the two-level agent so answers stream sooner", "summary": "...",
   "gains": "...", "loses": "...", "price": "...", "why": "...", "branch": null,
   "measured": [{"metric": "time to first token", "before": "4.5 s", "after": null, "n": 1,
                 "how": "check --run --live driving chat()"}]},
  {"id": "r2", "kind": "recommendation", "level": "quick-win", "actor": "router-setting", "rank": 3,
   "title": "Shadow gpt-6-luna before switching the RAG model", "where": "app.router.com > Routing > Shadow models", ...},
  {"id": "s1", "kind": "stays", ...},
  {"id": "n1", "kind": "note", "title": "Considered and rejected: Jev auto routing", ...}]}
```

Rules (enforced by `validate_report`):
- `change`: `commit` required; `loses` must not be "nothing" when the call's model, API or output changes (today's
  trade-off/port collapse into this rule).
- `recommendation`: `level` in quick-win | change | rework; `actor` in code | router-setting | product-call; `rank`
  unique; `branch` null or `router-migrate/improve/<name>`; `router-setting` needs `where`; gains, loses, why required.
- `measured` optional on any item; each entry `metric`, `before`, `after` (null when not built), `n`, `how`.
- v6 reports keep loading: move/trade-off/port read as `change`; improvement and needs-decision as `recommendation`
  (actor: code if it has a branch or is buildable, else product-call).

Status and actions: `recommendation` gets the improvement's lifecycle (proposed, approved, in session, applied,
declined, dismissed, postponed, undone, check failed); `change` gets in code / undo requested / undone.

## Live key as the flagship path

- With a key: `check --run --live` is the measurement instrument; the dashboard shows each recommendation's `measured`
  rows, and the overview says "measured on real Router".
- Without a key: everything runs; recommendations carry no numbers, and the overview says so in one line: "No Router
  key: requests checked against the simulator; costs, cache and latency not measured."
- Unchanged: a dedicated, spend-capped migration key, never printed or committed, injected only into `--live`
  commands; the outside check stays on the simulator for reproducibility.

## Dashboard

Same six pages and card style; three changes.

1. **Overview leads with the review**: the top 3-4 recommendations by rank, each with level, actor, its headline number
   and Approve. Below: the gate (traffic on Router, behaviour preserved) and "how your traffic moved".
2. **Changes page** groups: Migration (changes), Recommendations (ranked, filter by level and actor), Stays, Notes.
   One card shape, one set of actions.
3. **Router setup**: go-live and off switch (plumbing, static) plus this app's `router-setting` recommendations, each
   with where to click and its approval state. The static cap, Flex and shadow steps are deleted.

## Apply

As v6, plus: a `router-setting` recommendation is never "built"; the session writes what to switch and where in the
note, and its status becomes "your step" until the human marks it done. Approved proposals are built from their
description and checked (shown working overnight: "Collapse the two-level agent" built after approval, check passed).

## Eval: the release gate

- `eval/lean/run_arms.py` + `pairwise.py` become `eval/gate/`: any change to the prompt, facts, tools or contract runs
  against the current default on the five working repos and the five held-out repos (computer_use_ootb,
  obsidian-copilot, obsidian-textgenerator-plugin, openui, page-assist).
- Ships if it wins or ties head-to-head on both rubrics and loses no traffic or behaviour on the outside check.
- Absolute 1-10 scores are dropped (they saturate at 8). Runs cut off by an outage or quota are invalid, never scored.

## Phases

| # | Work | Files | Done when |
|---|---|---|---|
| 0 | Confirm the default: rerun round 6 (open vs legacy, held-out) and judge open_oldfacts | `eval/lean` | open wins on held-out; fact-sheet choice made |
| 1 | v7 item model and contract, with v6 reports still loading | `report_v6.py` -> `report.py`, `schemas/V7_CONTRACTS.md`, tests | fixture covers 4 kinds x 3 actors; v6 fixtures still validate |
| 2 | Dashboard: overview leads with recommendations; groups; setup from router-setting items | `dashboard/items.py`, `v6.html` | a stored open-scope run renders top recommendations with numbers; static setup steps gone |
| 3 | Prompt and contract wording to v7; drop `unwrap` from migrate tools | `prompts/migrate.md`, `apply.md`, `harness.py` | gate passes vs the v6-contract open prompt |
| 4 | `measured` evidence and the live-key path | `report.py`, `items.py`, `harness.py` | key and no-key runs both finish; key run shows numbers |
| 5 | `facts --refresh` and the fact sheet swap | `harness.py`, `data/ROUTER.md` | refresh rewrites measured lines with dates; diff reviewed |
| 6 | Gate as a command; docs | `eval/gate/`, README, ARCHITECTURE, METHODOLOGY | one command runs the gate and prints the verdict |

Order: 0 first (it can overturn the default), then 1 -> 2 (what Kedar sees), 3, 4, 5, 6.

## Risks and open questions

- **The default is not yet confirmed on held-out repos.** Round 6 never got a valid legacy run. Phase 0 settles it.
- **Is the fuller fact sheet needed?** The mandate drove the gain; the new facts alone did not. open_oldfacts decides.
- **Numbers are small samples.** n = 1-5 on prompts reconstructed from code or driven through handlers. Good for
  ranking, not for promises; `measured.n` is shown on every number, and model swaps stay product calls with shadow
  models proposed.
- **Quality isn't measured.** Router's shadow models record cost and latency only. A later eval-on-real-traffic step
  is out of scope.
- **Run time.** Open-scope runs take ~35-45 min vs ~15 for the older prompt. Acceptable for a review; the dashboard
  already handles background checks.
- **Live probes send app-shaped prompts to Router** under the migration key. Fine for these repos; a customer repo may
  need a consent line before `--live`.

## Out of scope

Production traffic analysis, quality evals on real traffic, automatic application of account settings, multi-repo
portfolios, CI integration beyond the headless run.
