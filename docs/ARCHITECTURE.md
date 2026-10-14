# Architecture

router-migrate has two halves:

- **What ships into your repo:** a small vendored runtime (`router_runtime.py` / `routerRuntime.ts`), one-line client
  wraps, and a compiled policy. This decides, per request, whether Router or the original provider serves it.
- **What runs once, at migration time:** setup, one agent session, an outside check, Improve, a dashboard and Apply.

For why it is built this way, see [METHODOLOGY.md](METHODOLOGY.md). For the levers, see [LEVERS.md](LEVERS.md).

## Pipeline

```
 /router-migrate  or  router-migrate run <repo> --agent claude
   │
   ▼
 1 setup (code) ────────── map of every LLM call + whether it routes now; baseline tests start in the background
   │
   ▼
 2 agent session ───────── migrates (any method, its own tools + check/levers/wrap/unwrap/adapt),
   │                       judges latency and task, proposes improvements, writes MIGRATION.json
   ▼
 3 outside check (code) ── parse · scope · typecheck · full suite once with Router on (background if slow) · no invalid Router
   │                       requests · route truth · proposals validated and priced · guardrails compiled
   │                       └─ on failure: one fact-only fix pass, then revert only the offending files
   ▼
 4 Improve (code) ──────── cards = agent proposals ∪ rule defaults; every lever considered, with reasons
   │
   ▼
 5 dashboard ───────────── REPORT.html + live page: moves, trade-offs with Undo, cards with provenance,
   │                       calls for your judgment, test status
   ▼
 6 Apply (human) ───────── approve → policy compile / codemod option / undo
   │
   ▼
 router-migrate check --final   before shipping
```

## Components

| Component | Where | Role |
|---|---|---|
| Runtime | `router_migrate/runtime/router_runtime.py`, `routerRuntime.ts` | Vendored into the repo. Sits under the SDK's HTTP client. Decides Router or provider per request, translates Chat Completions to Responses and back, applies the compiled policy, tags spend. Inert without `RAMP_ROUTER_API_KEY`. |
| Catalog | `router_migrate/catalog.py`, `router_migrate/data/` | Router's model catalog (ids, prices, surfaces, capabilities) and measured facts (which models have Flex). Rendered into the runtime at vendor time. |
| router-sim | `router_migrate/sim/server.py` | A local Router stand-in that enforces Router's measured contract. Used by `check --run`, the outside check's Router-on test run, and the eval. Also the HTTP(S) proxy for sandboxed runs, so nothing leaves the machine. |
| Scanner | `router_migrate/scan_py.py` (libcst), `router_migrate/ts/tool.mjs` (TypeScript compiler API) | Finds client constructions and LLM calls, their surface, base URL, key, model and flags. |
| Route check | `router_migrate/route_check.py` | For every site, builds a representative request and runs it through the **real** vendored runtime with the repo's compiled policy, against router-sim. `routes` is true only for a valid Router request. When false, the reason names the lever or hard limit. |
| Agent tools | `router_migrate/agent.py`, `router_migrate/harness.py` | `check`, `levers`, `wrap`, `unwrap`, `adapt`. Facts and exact edits for the agent. |
| Agent session | Claude Code (in your session or headless) | Decides every call's outcome, makes the edits, judges latency and task, proposes improvements. Writes `.router-migrate/MIGRATION.json`. |
| Outside check | `router_migrate/harness.py` (`final_check`) | Re-derives everything from the tree. Trusts nothing the agent wrote. |
| Improve | `router_migrate/improve.py`, `recommend.py`, `rules.py` | Turns agent proposals and rule defaults into cards, prices them, and lists every lever that did not fire with its reason. |
| Dashboard | `router_migrate/dashboard/` | `REPORT.html` (static) and a live local page (127.0.0.1, session token) with Approve, Dismiss, Undo and Apply. |
| Apply | `router_migrate/policy.py`, `apply_py.py` | Compiles `router.policy.json` into `router_policy.py` / `routerPolicy.ts`; runs codemods for client options; undoes trade-offs. |

## State files

All under `<repo>/.router-migrate/` unless noted.

| File | Written by | Contents |
|---|---|---|
| `ledger.json` | setup, tools, outside check | Baseline (test command and counts, typecheck), snapshot tree, every edit and who made it, trade-offs (`adapt`), sandbox runs, check history, cost. The audit trail. |
| `MIGRATION.json` | the agent | One entry per production LLM call it decided (schema below). Required by the outside check. |
| `plan_v3.json` | setup, outside check | The map: every LLM call the scanner sees, as the code stands, with route truth and the agent's outcome. |
| `recommendations.json` | Improve | Cards (agent proposals and rule defaults) and the considered-not-recommended list. |
| `decisions.json` | dashboard, `router-migrate decide` | One decision per card, pinned to the card's etag. |
| `router.policy.json` | repo root, committed | Per-feature and per-glob policy: automatic entries, agent trade-offs, approved cards. |
| `router_policy.py` / `routerPolicy.ts` | Apply, outside check | The compiled policy beside the runtime, with a hash. The runtime loads it lazily. |
| `REPORT.html` | dashboard | Self-contained report of the run. |

## Data contracts

### `MIGRATION.json` (the agent's report)

```json
{"sites": [
  {"site": "ai_scientist/llm.py:96", "outcome": "migrated", "levers": ["drop_params:seed"],
   "loses": "best-effort determinism from seed=0", "why": "research pipeline; seed is a hint, not a contract"},
  {"site": "bot/openai_helper.py:333", "outcome": "cannot_route", "why": "images: no Router endpoint"},
  {"site": "src/models/index.ts:133", "outcome": "needs_decision", "why": "users bring their own keys; no operator key"}
]}
```

- `outcome` is one of `migrated`, `kept`, `cannot_route`, `needs_decision`.
- Any entry with `levers` must say what it `loses`. The check rejects it otherwise.
- `migrated` is a claim. The outside check decides whether it is **verified** (route check or a sandboxed run saw a
  valid Router request from it), **unverified** (claimed, not seen), or wrong (the call **stays** on the provider).

### `router.policy.json`

```json
{"version": 1,
 "defaults": {"metadata": {"product": "my-app"}},
 "globs":    {"workers/**": {"prompt_cache_key": "$feature"}},
 "features": {"app/chat.py:answer": {"service_tier": "default", "provider_timeout": 30},
              "ai_scientist/llm.py:get_response_from_llm": {"drop_params": ["seed"]}}}
```

Exact feature beats the longest matching glob, which beats defaults. The policy applies to Router-bound requests only.
An invalid policy means no policy: the runtime still routes, with no levers. `ROUTER_MIGRATE_POLICY=off` disables it.
Full key list: [router_migrate/schemas/CONTRACTS.md](../router_migrate/schemas/CONTRACTS.md) (the v2 contract;
v4 added `service_tier`, `drop_params` and `model_map`).

### Cards (`recommendations.json`)

Each card carries: `id`, `rule` (the lever), `origin` (`rule`, `agent`, or `agent+rule` when both found it; agent cards add `rationale` and `gains`), `scope` (sites, features, latency class),
`change` (policy patch or code diff), `loses`, `evidence` (facts with their source, the agent's rationale with the lines
it read), `impact` (per-call price change at a stated token shape; no volume is invented), `etag` (code span plus catalog
row) and `state`. Cards with `conflicts_with` are either/or (Flex and fallbacks on one feature).

### Decisions (`decisions.json`)

`{"cards": {"<id>": {"action": "approve|dismiss|postpone|shadow_first", "etag": "...", "by": "ui|cli|agent", "at": "..."}}}`.
A decision whose etag no longer matches the card (the code or catalog changed) is skipped and must be re-confirmed.
Undo on a trade-off runs the same code as `adapt --undo`: policy keys leave the compiled policy, client options leave
the wrap, and the client stays wrapped so clean traffic still routes.

## The runtime: one request

```
RAMP_ROUTER_API_KEY unset, or ROUTER_MIGRATE_DISABLE=1 ─────────────────────────────▶ provider (runtime inert)
operator_key_only, and the request's key is not the operator's ────────────────────▶ provider
host is OpenAI or Anthropic (or an unlocked host):
  endpoint Router serves?  /chat/completions (translated), /responses, /v1/messages ── no ─▶ provider
  model resolves?  policy model_map → exact catalog id → alias → measured dated equivalence ── no ─▶ provider
  Chat → Responses translatable after policy drop_params?
     (no n>1, logprobs, audio, logit_bias, max_tokens<16, speaker names, ...) ── no ─▶ provider
  provider-side state? (store read back, previous_response_id, conversation, files) ── yes ─▶ provider
  beta header not approved to drop ── yes ─▶ provider
  ─▶ Router, with: metadata.feature/product, feature + client headers, compiled policy
       (cache key, timeout, tier pin, approved fallbacks)
Router refuses before generating (400 unknown parameter, 403, 404 model, 501) ─────▶ provider, once
any exception while planning the request ──────────────────────────────────────────▶ provider, unchanged
```

The decision is per request because only the request boundary knows every fact. The same call can carry `n=2` on one
request and not the next, or take its model from config. A runtime-chosen model outside the catalog simply stays on the
provider.

The runtime installs itself under the client's own transport, so proxies, certificates, headers, hooks and pools still
apply. Clients survive `deepcopy` (a LlamaIndex pattern; the v4 pilot found this crash).

## The outside check, in order

The agent cannot influence it. It reads only the tree, the baseline and router-sim.

1. **Parse.** Every changed file parses.
2. **Scope.** No edited tests, lockfiles, CI or vendored runtime files. No deleted files. No removed provider-key
   references (fallback traffic still needs them).
3. **Tests.** The full suite, once, with Router on against router-sim (the runtime is inert without a key, so this also
   covers Router off), compared with the baseline taken at setup in parallel with the agent. A Router-off run follows only
   when the result is worse, to name the cause. A suite slower than 90 s runs in the background after `finish`; Apply waits
   for it. `check --final` skips tests unless `--tests`. Typecheck where the repo has one.
4. **Router validity.** No invalid request reached router-sim.
5. **Route truth.** The route check plus sandboxed runs classify every claimed move: verified, unverified, stays.
6. **Proposals.** Each improvement the agent proposed is validated (catalog id, capability match) and priced.
7. **Guardrails.** Automatic policy entries are compiled from the agent's latency judgments.

On failure the agent gets one pass with the failure facts only. If it still fails, only the offending files are reverted.
The repo always ends in a state that passes.

## CLI surface

**For people:**

| Command | What it does |
|---|---|
| `/router-migrate` (Claude Code) | Runs the whole flow inside your session. |
| `router-migrate run <repo> --agent claude [--model M]` | The same, headless. For CI and evals. |
| `router-migrate start .` | Setup: the map, background baseline tests, the agent's instructions. |
| `router-migrate finish .` | Outside check, Improve, `REPORT.html`, opens the dashboard. |
| `router-migrate check --final` | Dry run of the outside check. Run before shipping. |
| `router-migrate dashboard .` | Reopen the live dashboard. |
| `router-migrate decide <card> approve\|dismiss\|postpone\|shadow-first` | Decide a card from the shell. |
| `router-migrate apply . --decisions` | Apply approved cards and undos: policy compile, codemod options. |
| `router-migrate status .` | Effective policy per feature; exit 1 if the compiled policy is stale or orphaned. |

**For the agent:**

| Command | What it does |
|---|---|
| `check --all` | Every LLM call the scanner sees, whether it routes now, why not, and the fix. |
| `check FILE:LINE[,...]` | The same for some calls. |
| `check --run "<cmd>"` | Runs code with the runtime pointed at router-sim (provider keys dummied, offline). Reports which calls sent valid Router requests. |
| `check --final` | Dry run of the outside check. |
| `levers FILE:LINE` | Improvement facts for a migrated call: Flex availability, prices, compatible fallbacks, cache threshold, rule defaults. |
| `wrap FILE:LINE` / `unwrap FILE:LINE` | Exact codemods at a client construction. `FILE:1` is a file's LiteLLM or module hook. |
| `adapt FILE:LINE --drop P \| --model-map A=B \| --unlock-host H \| --operator-key-only \| --drop-betas F --loses "..." --why "..."` | Take a trade-off. `adapt --undo T<n>` reverses one. |

Older pipeline commands (`scan`, `plan`, `apply` without `--decisions`, `verify`, `up`, `claim`, `probe`, `judge`) remain
for v1–v4 compatibility and are documented in [archive/docs/](../archive/docs/).

## Environment variables

| Variable | Effect |
|---|---|
| `RAMP_ROUTER_API_KEY` | Turns the runtime on. Without it, every wrapped client behaves exactly as before. |
| `ROUTER_MIGRATE_DISABLE=1` | Kill switch. Everything goes to the provider. |
| `ROUTER_MIGRATE_POLICY=off` | Ignore the compiled policy. |
| `ROUTER_MIGRATE_FEATURE_TAG=off` | Stop sending `metadata.feature` and cache keys derived from it. |
| `ROUTER_MIGRATE_GUARD_TIMEOUT` | Timeout guardrail in seconds (default 30). |
