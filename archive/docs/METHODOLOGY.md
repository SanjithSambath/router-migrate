# router-migrate: how a migration actually happens (v4)

**Input:** a git repo plus one command:

```
router-migrate run <repo> --test-cmd "<the repo's tests>" --agent claude
```

**Output:**

- A diff. Each OpenAI/Anthropic client that should go through Ramp's Router reaches a small vendored runtime (or, when provably safe, points straight at Router).
- A ledger. It records every LLM call site in the repo, the decision for it, who made it, why, and the **evidence**: the lines read, or the simulator requests that prove the site routes.
- Cards a human approves on a local dashboard. Some are lossy or business moves the agent may only propose: a model swap, another vendor's endpoint, per-user keys, beta flags. Others are Improve levers: fallbacks, caching, spend tags.

This doc follows a repo from that command to that output. For every stage it gives five things:

- **Who decides:** a tool, the agent, or a human.
- **What exactly is inspected.**
- **Why it is that actor.**
- **What comes out.**
- **What can go wrong**, and what catches it.

---

## 0. The 60-second version

- **One principle: constrain outcomes, not process.** The agent's autonomy scales with what can be verified.
- **One stance: migrate by default.** The goal is to move as much production LLM traffic onto Router as is reasonable. If a call can only move at a cost a reasonable engineer would accept, the agent takes the move and records exactly what is given up. A human sees every trade-off on the dashboard, with an undo. A call stays where it is only when Router physically can't serve it, or when the loss would be the product itself. These are principles, not a checklist; the agent uses judgment.
  - **Hard limits stay hard.** These are things Router or the build physically can't take:
    - no Router endpoint for the surface (embeddings, images, audio, files, …);
    - provider-side state (`previous_response_id`, `store`, conversations, files);
    - a build that no longer parses, typechecks or passes its tests;
    - a request Router would reject.

    The tools refuse these; nothing else is refused.
  - **Everything else is the agent's call, with evidence.** A `migrated` claim counts once there is *static* evidence (the code routes as written) or *dynamic* evidence: a valid request from that site in the Router simulator, which the agent can produce itself with `probe`.
  - **Trade-offs are taken, listed and reversible.** `adapt` takes the lever that moves a call and records what it costs. Typical examples:
    - dropping a parameter Router rejects (`seed`, penalties);
    - a dated or retired model served by its nearest catalog model (`model_map`);
    - another vendor's OpenAI-compatible endpoint (`unlock_hosts`);
    - users' own keys, routing only the operator's (`operator_key_only`);
    - a performance-only beta flag.

    Policy levers leave the code untouched. Every one shows up under "Migrated with trade-offs" on the dashboard, with an undo. **Cards** remain for moves the agent judges to be product decisions, and for the Improve levers (Flex, fallbacks, caching, spend tags).
- **Pipeline:**
  - **Proposals.** Deterministic. Scan the code and run the rules. Every production site gets a proposal: an outcome, why, and the edit the rules would make. **Nothing is applied.** As a side check, the proposals are applied in a throwaway shadow, the tests run with Router on, and the tree is restored. That gives the gate its Router-on baseline.
  - **Agent loop.** For every proposal (P) and every hotspot (H, text that looks like LLM usage no scanner site explains), the agent reads code and config and decides. It edits with tools:
    - `wrap`, `wrap --native` and `unwrap`;
    - hand edits, when a probe can prove them.

    It proves migrated claims (`probe`), records decisions (`claim`) and proposes trade-offs (`card`).
  - **Gate (`finish`).** Parse, scope and invariants; latency guardrails compiled; typecheck and tests vs the baseline; tests with Router on against the simulator; then every migrated claim has evidence and every item has a decision. The agent cannot stop while it fails. After 3 failures every edit is reverted.
  - **Improve + dashboard.** As in v3: the agent judges latency, task and cacheability, tool rules make cards, and a human approves. The agent's own cards join the same queue.
- **In production, the runtime decides per request.** If Router can serve the request exactly, it goes to Router. Otherwise it goes to the provider byte-for-byte unchanged. If Router refuses before generating anything, the request is retried on the provider once. With no `RAMP_ROUTER_API_KEY` the runtime does nothing. v4 no longer forces `service_tier="default"`: Flex stays the account default, bounded by guardrails the agent's latency judgments compile (§3, Stage 6).
- **Results:** §6.

---

## 1. First principles

### What "migrated" means

A production request goes to Router only if **all** of these are true at runtime:

| # | Condition | Fact or judgment |
|---|---|---|
| 1 | The request goes through a client the runtime is installed under (or a native client pointed at Router) | Fact (AST), but clients built in factories and dynamic tables are only visible by running them |
| 2 | The endpoint is one Router serves: `/v1/responses`, `/v1/messages` (+`count_tokens`); chat-completions via translation | Fact: **hard limit** |
| 3 | The model is a Router catalog id, or maps to one | Fact when literal, often chosen at runtime |
| 4 | The client points at OpenAI's or Anthropic's own endpoint | Often **judgment**: config, docs, deploy files |
| 5 | The key is the operator's, not a per-user key | Often **judgment** |
| 6 | No provider-side state | Fact: **hard limit** (the runtime re-checks every request) |
| 7 | The code is production code that runs | Judgment for gray cases |

### What the earlier versions taught us

- **Rules on judgments overfit.** The planner's precision went from 89% on development repos, to 53% held out, to **30%** on a fresh set.
- **An agent with only a prompt makes wrong moves.**
  - Router's public docs prompt: 6 correct / 7 wrong, and 1 repo broken.
  - A detailed prompt that encodes all our rules and recipes, with the same floor and the same gate checks (arm B″): 10 correct / **7 wrong**.
  - So the prompt text is not what makes C correct.
- **Agent + tools (v3, arm C): 10 correct / 0 wrong**, 0 broken. Tools that compute facts, plus a gate, are what remove wrong moves.
- **But v3 built the tools the wrong way around:**
  - **The floor edited code before the agent looked.** Confident sites were never reviewed. Removing the floor (arm Cnf) gave the same 10 correct / 0 wrong, at 68% of the cost and 76% of the time. *Pre-applying the floor does not help.*
  - **migrate.md was a script written in English:** 7 ordered rules plus 4 recipes.
  - **A claim only counted if the static planner could see it route.** Correct dynamic work stayed "unconfirmed" (obsidian-copilot's `routerFetch()` in a dynamic constructor table), and recall stalled at 10/16 for every arm.

  We were bounding the agent by what our heuristics could verify, not by what is true.

**v4's answer:** keep tools for facts and gates, drop them as gatekeepers of judgment, and let the agent *produce* the evidence. A valid request in the simulator, tagged with the site, is evidence that doesn't depend on our heuristics.

---

## 2. The actors, and where natural language appears

| Actor | Decides | Never does |
|---|---|---|
| **Tool** (libcst, the TypeScript compiler API, the catalog, router-sim) | Facts: what a line is, its surface, hard limits, whether a client is wrapped, whether a probe's request was valid, whether tests got worse. Proposals (defaults). Mechanical edits (`wrap`, `unwrap`, `wrap --native`). | Refuse anything that isn't a hard limit or an invariant. Apply an edit unasked. |
| **Agent** (Claude Code, headless) | Every site's outcome; any edit it can prove; which code paths to probe; latency class; which trade-offs are worth a card | Cross a hard limit; break an invariant (§3, Stage 4); make a lossy or business move itself |
| **Human** (dashboard) | Cards: model swaps, other vendors' endpoints, per-user keys, beta flags, Flex, fallbacks | — |
| **Runtime** (~1,350 lines of Python / ~1,200 of TS, vendored) | Per request in production: Router or provider | Fail a request because of itself |

**Natural language is used in exactly these places:**

1. `prompts/migrate.md` (65 lines):
   - the goal;
   - the measured Router facts;
   - the hard limits;
   - four invariants;
   - the tools;
   - **hints** (the rules' heuristics, explicitly non-binding: "your evidence decides").

   There is no ordered rule list and there are no recipes.
2. `prompts/improve.md`: the Improve judgments (unchanged from v3).
3. The agent's `--why` on every claim and card. It is stored and shown to humans, never parsed.
4. Tool outputs. Every refusal ends with a `next:` hint (e.g. "prove it: `router-migrate probe FILE:LINE --cmd ...`, or claim what is true").
5. Card text on the dashboard.

Decisions themselves are structured: outcome enums, reason codes, FILE:LINE evidence, probe ids (`PRn`).

---

## 3. The pipeline, stage by stage

```
 command ─▶ [0 baseline] ─▶ [1 scan] ─▶ [2 rules → proposals] ─▶ [3 shadow Router-on baseline] ─▶ [4 handoff: ledger + P/H items]
             tool             tool          tool (nothing applied)      tool (apply → test → revert)        tool
                                                                                                             │
            ┌────────────────────────────────────────────────────────────────────────────────────────────────┘
            ▼
 [5 agent: read → check → edit? → probe → claim | card]  ⇄  [6 gate: finish]  ── fail ×3 ─▶ revert every edit
      agent decides; tools give facts, make edits, record evidence      tool
            │ gate passes
            ▼
 [7 Improve: signals → judgments → cards] ─▶ [8 dashboard: human approves cards] ─▶ apply (policy / code) ─▶ gate
            ▼
 [9 production runtime: per-request route / fall back, guardrails, policy]
```

### Stage 0. Baseline

- **Who:** tool (`cmd_run`).
- **What:**
  - The repo must be a git repo; every later check diffs against a snapshot tree.
  - It runs the tests and the typecheck on the untouched code and records the counts. Later checks are relative to these, so pre-existing failures are never blamed on the migration.
  - It is idempotent: a second `run` reprints the items.

### Stage 1. Scan

- **Who:** tool.
  - Python: `scan_py.py`, a libcst visitor.
  - TS/JS: `ts/tool.mjs`, the TypeScript compiler API.
- **What it records:**
  - **Client constructions:** OpenAI/Anthropic SDKs, Azure, Bedrock, LangChain, LlamaIndex, the Vercel AI SDK, LiteLLM, other vendors' SDKs. For each, the `base_url` and `api_key` expressions (static or dynamic, plus a per-tenant test) and whether the client is wrapped.
  - **Calls:** method chain → surface (chat, responses, messages, embeddings, images, …).
  - **Module-level usage, raw HTTP to LLM hosts, call flags:** stream, stateful params, beta headers, literal models.
  - Production vs non-production paths.
- **Unchanged from v3; it now produces defaults, not verdicts.** It still misses dynamic construction (`new (this.llmClass)(config)`, factory tables). In v4 those are found by hotspots and the agent, and proven by `probe`.

### Stage 2. Rules → proposals

- **Who:** tool (`plan.py`, then `build_proposals`).
- **What:** the v3 rule ladder still runs. In order:
  1. non-production;
  2. raw HTTP;
  3. no endpoint;
  4. v0 API;
  5. Azure/Bedrock/Vertex;
  6. beta;
  7. other vendor SDK;
  8. stateful;
  9. client target from the base URL expression;
  10. model in catalog;
  11. the vendor name in the path;
  12. stateful use on the same client.

  Its output is now **a proposal per production site**: `{outcome, reason, hard_limit?, suggested edit}`.
- **Nothing is applied.** The suggested edits are written to `.router-migrate/proposals.diff` for the agent to read.
- **Why keep rules at all:** they are free and instant, and they are right most of the time on confident sites. Re-deriving "an `OpenAI()` with no base URL on `gpt-4o-mini` routes" by hand would only waste agent time. But they are the 30%-precision component, so they **advise**. The agent reviews every site, including the confident ones.

### Stage 3. Shadow Router-on baseline

- **Who:** tool (`shadow_floor_run`).
- **What:**
  1. Apply the suggested edits.
  2. Run the tests with Router on against router-sim.
  3. Restore the tree exactly; the agent never sees it.
- **Why:** test suites that mock the HTTP transport see Router requests in their mocks under *any* correct migration. Only failures beyond this run are the agent's.
- **Crash guard:** the shadow run may produce no test counts while the Router-off run passes. Then the suggested edits *crash* with Router on, so the tool:
  - holds Router-on runs to the Router-off baseline instead;
  - prints a `WARNING` with the error line.

  A crash is a finding, never a baseline. *This guard exists because the v4 pilot caught exactly that case (§6).*

### Stage 4. Handoff: ledger and items

- **Ledger** (`.router-migrate/ledger.json`, version 4):
  - `baseline` {tests before, Router-on baseline + source + note, typecheck, the repo's Python};
  - `proposals`;
  - `worklist`;
  - `claims`;
  - `edits` {who: agent-wrap / agent-unwrap / guardrails / decisions};
  - `probes` {cmd, requests, blocked egress, provider-kept reasons};
  - `confirmations`;
  - `cards`;
  - `guardrails`;
  - `finish`.
- **Items** the agent must decide:
  - **P:** one per production site, with the proposal.
  - **H:** hotspots. These are production lines that look like LLM usage (endpoint strings, LLM imports, model literals) where no scanner site explains them. It is a deliberately dumb, high-recall regex whose job is to make the agent look.

### Stage 5. The agent

- **Launch:**

  ```
  claude -p <migrate.md> --output-format stream-json --max-turns 250   (timeout 45 min)
    --allowedTools Read,Grep,Glob,Edit,MultiEdit,TodoWrite,Task,Bash(router-migrate:*),
                   Bash(git diff/status/log:*),Bash(ls/rg/grep/find/cat/head/tail/sed -n/wc:*)
    --settings <Stop hook = "router-migrate finish <repo> --hook">
  ```

  - There is no `RAMP_ROUTER_API_KEY` and no arbitrary shell.
  - **The only way to execute code is `probe`**, which runs inside the sandbox described below.
- **The tools** (each prints facts plus a `next:` hint):

| Tool | What it does | Refuses only |
|---|---|---|
| `check FILE:LINE` | Facts for the code as it stands: does it route (static), hard limit, the proposal, evidence so far, native eligibility | — |
| `wrap FILE:LINE` | The runtime codemod on one construction (Python: `http_client=` / `_router_wrap_*`; TS: `routerFetch()`/`routerOptions()`; `FILE:1` = LiteLLM / module hook) | shapes it can't edit (then hand-edit and prove) |
| `wrap --native FILE:LINE` | Points a Python OpenAI/Anthropic client straight at Router (`base_url` + Router key when set, no runtime) | anything not provably safe: a non-native surface (chat needs translation), a non-catalog or dynamic model, beta flags, provider state, a per-tenant key, no linked site |
| `unwrap FILE:LINE` | Exact inverse of `wrap`/`--native` (import block removed when unused) | — |
| `probe [LOCS] --cmd "<cmd>"` | Runs any command with the runtime on and pointed at router-sim; records which sites sent valid requests | — |
| `claim LOCS OUTCOME --reason CODE --why --evidence FILE:LINE\|PRn [--latency …] [--latency-critical]` | Records the decision | `migrated` on a hard limit; malformed evidence |
| `card LOC --kind model_swap\|vendor_endpoint\|per_user_key\|beta_flags` | Records an option for a human, with the exact change and what is lost | a `model_swap` target that isn't a catalog id |
| `finish` | The gate (Stage 6) | — |

- **Hand edits** are allowed anywhere a tool can't reach, for example `routerFetch()` in one branch of a TS provider table. They count only when proven: an edited file must be linked to a claim, and a migrated claim needs evidence.
- **Invariants** (enforced by the gate):
  - no edits to tests, lockfiles or CI;
  - no deleted files;
  - no removed provider-key references, because kept traffic still needs them;
  - no cost or model trade-off without a card.
- **Probe sandbox:**
  - The runtime is on (`RAMP_ROUTER_*` → a fresh router-sim) and provider keys are replaced by dummies.
  - The repo's own interpreter comes first on `PATH`, and the repo's test env prefix is applied.
  - **router-sim is every client's HTTP(S) proxy.** The runtime sends Router traffic through the client's own proxy mounts, which the sim serves. Every other destination, including a provider's HTTPS `CONNECT`, is refused and listed as `blocked`. A probe stays offline and can't spend money. Clients that ignore proxy settings still meet a dummy key.
  - The probe also writes a **runtime trace** (`ROUTER_MIGRATE_TRACE`): for each LLM request that stayed on the provider, the runtime records *why* (model not in catalog, untranslatable field, beta header, stateful, operator-key-only). That separates "kept by design" from "my wrap didn't take".
- **Attribution: how a request names its site.** Each Router request carries two `file:function` tags:
  - `x-router-migrate-feature` (and `metadata.feature`): the calling frame.
  - `x-router-migrate-client`: where the wrapped client was built.

  Framework calls (LlamaIndex, LangChain) start in library code, so for them only the client names the site. A site is confirmed when a valid request's feature **or** client matches the site's `file:function`. For a native client there is no runtime and so no tag. It is confirmed by an untagged valid request on its surface with its exact static model.
- **Why an agent here:** each item is a "read three files and decide" question with no fixed shape. The proposals make the common case cheap; the agent's evidence makes the uncommon case count.

### Stage 6. The gate: `finish`

- **Who:** tool. It runs when the agent calls it **and** whenever the agent tries to stop (the Stop hook).
- **Checks, cheapest first** (`gate()` + `cmd_finish`):
  1. **Parse:** every changed file parses.
  2. **Scope and invariants:**
     - no deleted files, test files, lockfiles or CI edited;
     - every changed file is linked to a claim, an agent wrap/unwrap, guardrails or an approved decision (the runtime files, `.md` and `.example` are exempt);
     - no provider-key reference removed.
  3. **Guardrails compiled** from the claims' latency judgments into `router.policy.json`, then compiled to `router_policy.py` / `routerPolicy.ts`:
     - `user_waiting` + non-streaming → `provider_timeout: 30` (`ROUTER_MIGRATE_GUARD_TIMEOUT`), so a slow Flex call fails over instead of hanging a user;
     - `--latency-critical` (the `--why` must cite the need) → `service_tier: "default"` for that feature only.

     Flex, the account default, applies everywhere else. Guardrails only bound latency, so they need no human. The keys they set are tracked in `led["guardrails"]` and re-derived each run; keys from approved cards are never touched.
  4. **Typecheck:** no more errors than the baseline.
  5. **Tests:** not worse than the Router-off baseline.
  6. **Router-on tests** against router-sim:
     - any invalid request fails;
     - not worse than the shadow baseline (or the Router-off baseline after a crash).

     Requests from this run also **confirm** sites, by the same attribution as `probe`.
  7. **Hard limits:** no `migrated` claim on a hard-limit site.
  8. **Evidence:** every `migrated` claim is `static` (the code routes as written) or `dynamic` (a probe or the gate's own Router-on run). A native claim needs dynamic evidence, because it has no fallback.
  9. **Decisions:** every P and H item has a claim (`needs_human` is allowed).
- **Three strikes:** on the 3rd failed run with a hard failure (parse, scope, invariant, typecheck, tests or sim):
  - every edit is reverted to the snapshot;
  - migrated claims become `needs_human R_GATE_FAILED`;
  - undecided items become `needs_human R_UNDECIDED`.

  If only evidence or decisions are missing, the edits stay and unproven claims are reported *unconfirmed*. The repo always ends known-good.

### Stage 7. Trade-offs: taken by default, listed, undoable

`router-migrate adapt FILE:LINE [levers] --loses "..." --why "..."` takes a trade-off so a call moves, and records it in `led["tradeoffs"]`.
- **Policy levers** (`--drop PARAMS`, `--model-map OLD=NEW`) compile into `router.policy.json` and the runtime's policy files. The code is untouched. The runtime applies them only to the Router-bound copy of a request, so a fallback still sends exactly what the code built. It looks them up by the request's feature, or by where its client was built (for framework calls).
- **Client levers** (`--unlock-host`, `--operator-key-only`, `--drop-betas`) re-wrap the client with that option.
- **With no lever**, `adapt` records a hand edit, so every trade-off reaches the dashboard.

There is no allow-list: the agent decides which costs are acceptable. A model target must be a catalog id. The trade-off still needs the usual evidence (static, or a probe showing a valid Router request). **Undo** (the dashboard's Undo button, then Apply) calls `undo_tradeoff`: policy keys leave the compiled policy, and client options leave the wrap (the client stays wrapped, so clean traffic still routes).

**Cards** are for what the agent judges a human must decide first. The agent proposes; a human approves on the dashboard; `apply --decisions` applies; the gate re-runs.

| Card | Applied as | What is lost |
|---|---|---|
| `model_swap` (`--to <catalog id>`) | policy `model_map` for the feature's glob. The runtime swaps the model only on Router-bound requests; the code is unchanged | a different model answers |
| `vendor_endpoint` | code: `wrap(..., unlock_hosts=[host])`. That vendor's OpenAI-compatible traffic on a catalog model goes through Router | that vendor's own serving and limits |
| `per_user_key` | code: `wrap(..., operator_key_only=True)`. The runtime routes only requests whose key equals the operator's `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`; users' keys stay on the provider (the hybrid) | nothing for users; operator traffic is billed through Router |
| `beta_flags` | code: `wrap(..., drop_betas=[...])` | the beta's feature, stated |

The dashboard also lists these as code-change cards ("not applied until approved"), next to the Improve cards.

### Stage 8. Improve and the dashboard

- **Unchanged from v3.**
  - **Signals:** latency class, model owner, resolved model, static prompt size.
  - **Agent judgments** (`judge`) override the heuristics.
  - **Card rules:** `attr.metadata`, `cache.key`, `tier.flex_background`, `fallback.candidates`, unlocks, model recommendations.
  - **Dashboard:** approve / dismiss / postpone / shadow-first, each pinned to an etag of the code span plus the catalog row.
  - **Apply** merges policy and re-runs the codemod for unlocks.
- **Changed in v4:**
  - The agent's migration cards join the queue (`agent_cards`).
  - Clients come from the current scan (agent-wrapped clients count).
  - Flex no longer has to be "turned on". It is the default, and the latency guardrails above bound it per feature.

### Stage 9. In production: the runtime decides per request

```
RAMP_ROUTER_API_KEY unset, or ROUTER_MIGRATE_DISABLE=1 ───────────────────────────────▶ provider (runtime inert)
operator_key_only and the request's key isn't the operator's ────────────────────────▶ provider
host is api.openai.com (or an approved unlock host), path /chat/completions or /responses:
   model resolves? (approved model_map for this feature → exact catalog id → alias → dated equivalence → MODEL_MAP env)  no ─▶ provider
   /chat/completions translatable? (no n>1, logprobs, seed, presence_penalty≠0, audio/modalities, prediction, logit_bias,
       max_tokens<16, a speaker name on a user/assistant/system message, non-function tool_choice)  no ─▶ provider
       (a `name` on a tool result is dropped: it only repeats the tool's name; the output is keyed by call id)
   /responses: stateful (store, conversation, background, stored prompt, provider files, pinned client) ─▶ provider
host is api.anthropic.com /v1/messages[/count_tokens]: beta header not approved to drop ─▶ provider; model resolves? ─▶ Router
every Router request: metadata.feature + x-router-migrate-feature (calling file:function), x-router-migrate-client (where
   the client was built), compiled policy (spend tags, cache key, fallbacks, model_map, guardrails); no forced service tier
Router refuses before generating (501 / 403 / 404 model_not_found / 400 unknown|unsupported parameter) ─▶ provider, once
any exception while planning ─▶ provider, unchanged
```

**Why per request:** the same call can carry `n=2` on one request and not the next, or take its model from config. The request boundary is the only place where every fact is known. That is why a static "migrated" for a runtime-chosen model is safe: an uncataloged model just stays on the provider.

---

## 4. Who decides what

| Decision | Tool | Agent | Human |
|---|---|---|---|
| Is this an LLM call; which surface? | ✔ AST | finds what the AST missed (hotspots, factories), proves it by probe | |
| Hard limit (no endpoint, provider state, broken build) | ✔ refuses | | |
| Does the base URL / key / production-ness mean it moves? | proposes | ✔ decides, with evidence | |
| The edit | mechanical edits on request (`wrap`, `--native`, `unwrap`) | ✔ chooses; hand-edits what tools can't reach | |
| Is the edit proven? | ✔ static facts, sim-validated requests | ✔ produces the evidence (`probe`) | |
| Did anything break? | ✔ gate | | |
| Latency class → guardrails | heuristic default; compiles | ✔ judges (`--latency`, `--latency-critical` with a cited need) | |
| Model swap, other vendor's endpoint, per-user keys, beta flags | applies once approved | ✔ proposes (card) | ✔ approves |
| Flex / fallbacks / caching / spend tags | states the trade (cards) | judges inputs | ✔ approves |
| Per-request route in production | ✔ runtime | | |

---

## 5. What changed from v3, and why

| v3 | v4 | Why |
|---|---|---|
| Floor codemod applied before the agent looked; confident sites never reviewed | Proposals only; the agent decides every site; `unwrap` exists | arm Cnf: the floor added no accuracy and cost 1.5× |
| migrate.md: 7 ordered rules + 4 recipes | Router facts + goal + hard limits + invariants + tools; rules are **hints** | arm B″: the rules as prompt text give 7 wrong moves; correctness came from tools, not prose |
| A claim counts only if the static oracle sees it route; it rejects otherwise | Claims are recorded; `migrated` needs **static or dynamic** evidence; the agent produces dynamic evidence with `probe` | recall stalled at 10/16 on what our heuristics could see |
| Every change outside a fixed recipe set is suspect | Any edit allowed if it is linked and proven | autonomy scales with verifiability |
| `service_tier="default"` forced on every Router request | No forced tier (Flex stays the default); guardrails: `provider_timeout` for user-waiting buffered calls, `service_tier` only where the agent cites a latency need | the old setting silently disabled Router's default-on Flex everywhere |
| Wrap only | `wrap --native` (no runtime) when every served site is Router-native, on an exact catalog model, operator key, no beta/state; must be probed | the runtime is unnecessary where nothing needs translation or fallback |
| Lossy moves either refused or unlock cards only | Cards for model swap (policy `model_map`), vendor endpoint, per-user key (`operator_key_only`), beta flags | a human owns cost and product trade-offs; the agent surfaces them |
| Feature tag in the Responses body only | Feature **and client-origin** headers on every Router request (Messages too) | Messages bodies can't carry metadata; framework calls start in library code |

---

## 6. Evidence: eval and what it caught

### How the eval is run

- **Repos:** the same 10 fresh repos as v3, never used to build the tool: obsidian-copilot, obsidian-textgenerator-plugin, page-assist, AI-Scientist, computer_use_ootb, chatgpt-telegram-bot, openui, OpenManus, sec-insights, llmcord.
- **Ground truth:** blind per-repo inventories committed before any toolkit run, plus blind adjudication of disagreements. 16 sites should move.
- **Scoring:** a site counts as moved only if the code routes it **and** the arm reports it migrated. For v4 (arm D), "routes" includes the harness's own **re-verification**: it re-runs every probe the agent recorded, on the final code, against a fresh simulator, so the agent's evidence isn't taken on trust.
- **Model and runs:** pinned model, k=1 per arm.

| Arm | What it is | Correct (adj) | Wrong (adj) | Broken | $ | Minutes |
|---|---|---|---|---|---|---|
| A | rules only (floor) | 6 | 14 | 0/10 | 0 | 8 |
| B | Router's docs prompt, from source | 6 | 7 | 1/10 | 35.27 | — |
| B′ | docs prompt, from the floor | 10 | 16 | 0/10 | 25.05 | 96 |
| B″ | detailed prompt (all rules + recipes) + floor + same gate | 10 | 7 | 0/10 | 13.76 | 60 |
| C | v3: agent + toolkit + floor | 10 | 0 | 0/10 | 23.13 | 108 |
| Cnf | v3 toolkit, no floor | 10 | 0 | 0/10 | 15.63 | 82 |
| **D** | **v4** | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

### What the v4 pilot caught before the full run

The first D run (sec-insights) was stopped because it exposed defects that would have invalidated the numbers. All are fixed and covered by tests:

1. **A production crash the floor had shipped in every arm since v1.** sec-insights deep-copies its LlamaIndex LLM (`Settings.llm.model_copy(deep=True)`). With Router on, our `http_client` sat in that model and couldn't be deep-copied (an httpx lock), so the agent tool crashed. Fix: the runtime's clients share themselves under deepcopy, as httpx clients are meant to be shared.
2. **The gate baselined the crash away.** The shadow Router-on run crashed too, so "no worse than the shadow" passed. Fix: the crash guard (Stage 3).
3. **Framework sites could never be proven.** A LlamaIndex request's calling frame is the caller's script, not the site. Fix: client-origin attribution. Both sec-insights sites are now confirmed by probes through the real functions.
4. **Probes reached the internet.** Unrouted traffic (embeddings) hit api.openai.com with the dummy key. Fix: router-sim as the proxy; `blocked` egress is listed.
5. **Why a request stayed on the provider was invisible.** The trace showed LlamaIndex agent turns kept on the provider only because tool results carry `name`. Fix: the runtime drops `name` on tool results (lossless); speaker names still stay on the provider.
6. **Agent ergonomics.** Commands find the ledger from a subdirectory, and `python` in a probe is the repo's interpreter.

### Limitations

- k=1 per arm, so intervals are wide.
- The 10 repos were already used to tune v3's hotspot filter.
- Dynamic evidence is only as broad as the code paths the agent chooses to probe. The harness re-verification guards against false evidence, not against unprobed paths.
- TS probes rely on Node honoring proxy env (`NODE_USE_ENV_PROXY`, Node 24+); older Node TS probes are protected by dummy keys only. The provider-kept trace is Python-only.

### Production readiness

- **Ready:**
  - the runtime's safety properties: inert by default, kill switch, per-request fallback, deepcopy-safe clients;
  - the codemods and their exact inverses;
  - the gate and revert discipline;
  - the evidence trail: every migrated claim names its static fact or its probe.
- **MVP:**
  - the dashboard is local and single-user;
  - policy applies to the Responses surface (`/v1/messages` gets `model_map` only);
  - `wrap --native` is Python-only;
  - TS has no provider-kept trace.
- **Operational gaps:**
  - the catalog is baked in at generation (`apply --live` refreshes it);
  - Router-served `previous_response_id` chains live in process memory;
  - provider keys must stay configured for the fallback path.

### Next steps

1. Fresh repo set with k=3 (variance, plus a set not used for tuning).
2. Probe recipes per framework (FastAPI route, Next.js handler, LangChain chain) so the agent spends fewer turns building harnesses.
3. Bring the provider-kept trace to TS, and native wraps to TS clients.

---

## Appendix: file map

| Stage | File |
|---|---|
| CLI, ledger, proposals, shadow baseline, check/wrap/unwrap/probe/claim/card, gate, finish, guardrails, agent launch | `router_migrate/agent.py` |
| Python scanner / TS scanner + codemod | `router_migrate/scan_py.py`, `router_migrate/ts/tool.mjs` |
| Rules (proposal defaults) | `router_migrate/plan.py` |
| Python codemod (wrap, native, unwrap) | `router_migrate/apply_py.py` |
| router-sim (contract, request log, probe proxy) | `router_migrate/sim/server.py`, `router_migrate/verify.py` |
| Runtime (routing, translation, attribution, trace, policy) | `router_migrate/runtime/router_runtime.py`, `routerRuntime.ts` |
| Policy (guardrails, model_map, cards) | `router_migrate/policy.py`, `router_migrate/improve.py`, `router_migrate/recommend.py` |
| Agent prompts (the only natural-language control) | `router_migrate/prompts/migrate.md`, `improve.md` |
| Eval | `eval/v3/ablation_b2.py` (arms B″, Cnf, D; `trial`, `report`), `eval/v3/RESULTS.md`, `eval/v3/inventory/`, `eval/v3/adjudication/` |
