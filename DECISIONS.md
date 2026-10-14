# DECISIONS

Each entry: the decision, why, and the evidence. "Measured" means observed against the live Router API in
`~/code/router-live-check` (results.md, results_v2_*.json, raw_v2/). Where docs and measurements disagreed, measurements won.

## D1. Translate at the HTTP-client boundary, not at call sites (departs from SPEC's "codemod chat -> responses")

**Decision.** The migration injects a vendored runtime (`router_runtime.py` / `routerRuntime.ts`) under each SDK client: an
httpx transport (Python) or a `fetch` wrapper (TS). It rewrites `POST /chat/completions` into Router's `POST /v1/responses` and
rewrites the Responses result (JSON and SSE) back into a Chat Completions payload. Codemods only touch client constructions
(`OpenAI()` -> `_router_wrap_openai(OpenAI())`, `new OpenAI({...})` -> `new OpenAI({..., ...routerOptions()})`).

**Why.** What the eval rewards is traffic moved × zero breakage.
- Rewriting call sites to `responses.create` changes the *response shape* every downstream line reads: `.choices[0].message`, stream
  chunk loops, tool-call accumulation, `finish_reason`, usage fields.
- In the 15 repos those reads are spread across files, wrappers, and framework internals (LangChain, LlamaIndex, LiteLLM build their
  own requests). A call-site rewrite has to be right at every reader; a boundary translation has to be right once and is tested
  once, against the real SDK parsers.
- Test suites also constrain call sites. shell_gpt's tests assert the *exact kwargs* of the completion call (19 assertions), and
  opencommit's spy on `client.chat.completions.create`. Editing call sites breaks those tests even when behavior is right.

**Evidence.** `tests/test_runtime_py.py` and `tests/ts/runtime_check.mjs` drive the real openai-python 2.48, openai-node 4.104 and
anthropic SDKs through the runtime against router-sim. They cover:
- sync, async and streaming calls; tools, including streamed tool calls;
- `parse()` with Pydantic;
- legacy `functions`;
- stop emulation;
- errors.

**Cost.** The repo carries one vendored file (~600 lines, readable, with a kill switch). Native Responses features (built-in tools,
reasoning items) are only reachable through code already on `responses.create`. That code is passed through unchanged, minus the
`store` field Router rejects.

## D2. Inert unless `RAMP_ROUTER_API_KEY` is set; per-request fallback to the original provider

**Decision.**
- Every wrap returns the original object when the key is absent. `ROUTER_MIGRATE_DISABLE=1` is a kill switch.
- When active, each request is routed individually. Router takes it only if it can serve the request exactly: the model resolves
  to a catalog id, and the surface and params are supported. Everything else goes to the original provider unchanged: embeddings,
  images, audio, files, `n>1`, logprobs, `seed`, audio parts, unknown models, provider-issued `previous_response_id`, Anthropic beta headers, and non-default
  base URLs (Groq, Ollama, Azure...).

**Why.**
- Zero breakage is a property of the default path. Test suites, VCR cassettes (llm-anthropic) and HTTP mocks (simonw/llm) keep
  seeing exactly the old requests.
- Rollout is an env var, and rollback is unsetting it.
- Partial support never turns into a hard failure: an unservable request simply keeps working where it worked before.

## D3. Model mapping: exact ids, aliases, and only *measured* dated equivalences

**Decision.** `gpt-4o-mini-2024-07-18` maps to `gpt-4o-mini` only because Router itself returned that dated id for `gpt-4o-mini`
(`router_migrate/data/dated_equivalents.json`, 22 entries mined from raw_v2). Undated-alias guessing is not done:
- `gpt-4-turbo-preview` and `gpt-4o-2024-05-13` stay on the provider;
- `claude-3-5-sonnet-*` stays on Anthropic (not in the catalog).

**Why.** Dated ids return 404 on Router (measured). An undated id can be a *newer* snapshot than the dated one the code pinned, and
silently changing the model is a behavior change. Customers can opt in per model with `ROUTER_MIGRATE_MODEL_MAP`.

**First version was wrong.** I first stripped `-preview`/`-latest`/date suffixes, which mapped `gpt-4-turbo-preview` (0125-preview)
onto `gpt-4-turbo` (2024-04-09), a different model. The smoke test on swarm caught it; I replaced the rule with the measured table.

## D4. Translation details forced by measurements (each is a runtime rule plus a router-sim rule)

| Rule | Evidence |
|---|---|
| `max_tokens`/`max_completion_tokens` -> `max_output_tokens`; values below 16 stay on the provider (see D10) | Responses rejects `max_tokens` (measured 400); minimum 16 |
| `stop` dropped for OpenAI-owned models and emulated client-side (also across stream chunks); forwarded for others | `stop` 501 on OpenAI models, honored on Claude (measured) |
| `temperature`/`top_p` dropped where `capabilities.temperature == false` | 400 on gpt-5-nano (measured) |
| `n>1`, logprobs, `logit_bias`, audio, prediction -> provider | Router 400 / no equivalent (measured: seed/n/logprobs 400) |
| function tools sent with explicit `strict: false` unless the caller set strict | Responses defaults tools to strict; loose Chat schemas would 400 |
| `store` removed (explicit `store:true` stays on the provider); Router-started `previous_response_id` chains replayed inline; `conversation`/`background` -> provider (see D10) | store:true 400, previous_response_id 400 or silently ignored (measured) |
| `prompt_cache_key` added for Claude via Responses (the caller's feature tag) | Claude caching only works with it: 10,362 tokens cached vs 0 (measured) |
| `service_tier: "default"` unless the caller set one (first version: streaming calls only; changed in D10) | Flex is on by account default and was applied mid-conversation (measured) |
| reasoning effort on Claude only when max_output_tokens >= 1100 | 400 budget_tokens >= 1024 (measured) |
| `metadata.feature = "<file>:<function>"` from the call stack | per-feature cost attribution in Router without touching call sites |
| User-Agent forwarded from the SDK, never Python-urllib | urllib default UA gets Cloudflare 1010 (measured) |

## D5. Coverage is counted per site, and both metrics are reported

- `callsite_coverage` = migrated / all production LLM sites. This includes embeddings and others Router cannot serve; it is the
  honest headline.
- `weighted_coverage` = migrated / Router-eligible sites (generation surfaces), i.e. how much of what Router *can* take was moved.
- Framework model constructions (`ChatOpenAI(...)`, `createOpenAI(...)`) count as sites: the calls go through framework internals.
- Tests/examples/evals are scanned and reported but excluded from coverage and **not edited by default** (`--include-nonprod`).
  The smoke test on swarm showed that patching examples sprays runtime copies into demo folders, which no reviewer wants.

## D6. Python runtime placement and imports

The runtime is copied once per top-level package and imported relatively, with an absolute fallback for files run as scripts:
`try: from ..router_runtime import ...` / `except ImportError: from router_runtime import ...`. A file outside any package gets a
sibling copy and an absolute import. Every inserted line carries a `router-migrate` comment so it is greppable and reversible.

## D7. router-sim enforces rules with their source

Every rejection in `router_migrate/sim/server.py` is labelled:
- `[measured]`: live Router behavior;
- `[upstream]`: the Responses/Messages contract Router forwards, e.g. strict tool schemas, function_call_output needing a prior call.

The sim's job is to make translation bugs fail loudly in tests, so it is strict where the evidence supports strictness and silent
elsewhere.

## D8. Python 3.11/3.12 via uv inside the folder

The machine has Python 3.9, but all 9 Python repos require >=3.10. uv
(pip-installed into `.venv`) fetched CPython 3.11/3.12 into `.tools/python`. On first use, uv also symlinked `python3.11`/`python3.12`
into `~/.local/bin`; I removed those and pinned `UV_PYTHON_BIN_DIR` inside the folder (`scripts/env.sh`).

## D9. Baseline: the docs' prompt, run by a Claude agent per repo, scored in measure-only mode

SPEC asks for the docs migration prompt via `claude -p`. The local `claude` CLI's OAuth session had expired ("Failed to
authenticate: OAuth session expired"), and re-authenticating is the user's call. So each baseline run used a Claude subagent
from this session instead:
- The prompt from `research/baseline_prompt.txt` is pasted verbatim, on a fresh copy of the repo at the pinned commit.
- Harness constraints match what router-migrate itself has at apply time: no Router key, no network, no installs or tests.

Scoring uses the same scanner, the same recipe tests, and the planner in `measure_only` mode, so only traffic that actually points
at Router counts as migrated.

My first scorer did not have that mode and credited the untouched repo with router-migrate's planned patches. It was caught
because an agent that never ran (0 s, auth failure) "scored" 2 migrated sites.

## D10. Adversarial review, and what changed because of it

I had a separate agent attack the tool, its diffs and its numbers. Its findings are in `research/adversarial_review.md`, with repro
scripts in `research/review_scratch/`. Every Critical and High item was fixed or re-measured. The decisions that changed:

**Responses state (C1, H2).** Router keeps no server state: `store:true` returns 400, and `previous_response_id` returns 400 on
OpenAI models or is silently ignored on others (measured). Per-request routing therefore split conversations: turn 1 went to
Router, and turn 2 sent Router's id to OpenAI, which had never seen it. Now:
- A chain that starts on Router stays on Router. The runtime remembers the last 256 Router responses in memory. On the next turn
  it sends the history inline, which is what Router's own 400 message tells callers to do.
- `responses.retrieve` / `input_items` for Router-issued ids are answered locally.
- A Router id that has aged out of memory gets a clear error, not a confusing provider 404.
- Ids the provider issued keep their chain on the provider.
- Statically, a client whose code uses `previous_response_id`, `conversation`, `store=True` or `retrieve` gets
  `_router_wrap_openai(OpenAI(), responses=False)`. Its chat traffic moves; its Responses traffic stays put. This matters because a
  multi-process server cannot share the in-memory history.
- Inputs that point at provider-side state stay on the provider: reasoning items without `encrypted_content`, `item_reference`,
  `file_search` vector stores, and `file_id`s.
- For OpenAI reasoning models, `include: ["reasoning.encrypted_content"]` is added so the reasoning items Router returns can be
  replayed. Measured live: turn 1 returned an encrypted reasoning item, and turn 2 accepted it.

**Fallback after a Router refusal (M4).** Router can refuse a request the catalog says it serves: `gpt-5.3-codex` returns 403
`data_residency_mismatch` (measured), and parameters it does not know return 400. The runtime now resends the *original* request to
the provider once, and only for refusals that happen before any generation:
- 400 `unknown_parameter` / `unsupported_parameter`;
- 403;
- 404 `model_not_found`;
- 501.

A 404 model is remembered for the rest of the process. Generation errors (bad input, 429, 5xx) are surfaced, never retried.
`ROUTER_MIGRATE_FALLBACK=0` turns the fallback off. There is no fallback when the request carries no provider credential.

**Wrap in place (H3, L5).** `wrap_openai(client)` used to rebuild the client with a new httpx client. That dropped `HTTP(S)_PROXY`
mounts, `proxy=`, default headers, auth and event hooks, *for provider traffic too*. Now the Router transport is installed under
the client's existing transport and every proxy mount, on the same object. The wrap is idempotent, and `with` blocks close the
real pool. Compat is verified under openai 2.16 / 2.54 (httpx) and 3.3 / 3.29 (httpx2).

**LangChain (H6, L2).** `ChatAnthropic` used to get Router's base_url and key, which sent *every* request to Router: dated models
returned 404 and beta headers were lost. Both LangChain chat models are now wrapped after construction:
`_router_wrap_langchain(ChatOpenAI(...))` wraps the SDK clients LangChain built. So they get the same per-request planning and
fallback as everything else, and `**kwargs` collisions are impossible. The old `chat_anthropic_kwargs()` now returns `{}` so
previously patched code degrades safely.

**No environment mutation (C3).** The runtime set a placeholder `OPENAI_API_KEY` so SDK constructors would not raise. That
placeholder shadowed keys that `load_dotenv()` loads later (dotenv does not override existing variables), which broke every request
left on the provider. It is gone. The provider key stays required, and it is needed anyway for whatever Router cannot serve.

**Imports that cannot fail (C2, M1, M2).**
- The inserted import tries the package-relative import, then the plain module, then no-op stubs. Namespace packages
  (`app/main.py` with no `__init__.py`) now route through Router via the relative tier. Scripts run from inside a package keep
  their original behavior instead of crashing.
- Reruns recognise earlier wraps per constructor and patch only new ones.
- A `router_runtime.py` that router-migrate did not write is never overwritten. Those sites are flagged.

**Silent semantic changes (M5).** Anything Router would serve *differently* now stays on the provider unchanged: `seed`,
`presence_penalty`, `name` on messages, `max_tokens` below Router's minimum of 16, and `allowed_tools`/custom `tool_choice`.
Clamping `max_tokens=1` to 16 was a behavior change I had accepted; the review was right that it should not be silent.
`service_tier: "default"` is now sent on every routed call unless the caller chose a tier. Flex is the account default (measured),
and no pre-migration call was ever flex. `ROUTER_MIGRATE_SERVICE_TIER=auto` restores the account default.

## D11. v2 M0: what the live measurements kept and cut

Measured on 2026-10-13 for $0.0004; details are in `research/m0_measurements.md`.
- **Kept: `tier.flex_background`.** Flex costs exactly 50% of default on gpt-5.4-mini (n=5 per arm, identical tokens).
  - Impact is shown as −50% per call, marked "measured on gpt-5.4-mini only". It is "unmeasured" for other models.
  - Flex latency reached 61 s on gpt-5-nano, so the card exists only for the background latency class.
  - Eligibility is per model (17 measured), not per vendor: Router rejects gpt-4o-mini's flex request.
- **Kept: `fallback.candidates`.** Router's `models` list works through raw HTTP, openai-python `extra_body` and openai-node
  extra params. It advances on no-permission (403) and on unknown models.
  - `allow_flex_tier` is ignored with a list. A list with no tier is served at flex (account default).
  - So the runtime keeps v1's `service_tier: "default"` alongside fallbacks and never sends `allow_flex_tier` with a list.
- **Not built: anything that depends on Jev.** "service_tier default vs Jev" could not be measured: Jev is off, and turning it
  on is an account change. The MVP has no Jev rule; the setup checklist states the documented claim and marks it unmeasured.
- **Setup checklist only, never written by the tool: Switchyard.** `model: "switchyard"` from app code returned 200 (served
  by gpt-5.6-luna at flex) while the routing toggle was off but a config was saved. Earlier, with no config, it returned 404.
  It is known to switch model family mid-conversation.
- **Flex guard is per model.** The policy's flex guard reads a per-model flex bit (`c.f`) compiled into both runtimes from
  `router_migrate/data/flex_models.json`, not an owner check.
- **Policy scope (MVP cut).** SPEC_V2 rule 5 asks for stable client ids baked into every wrap. The MVP policy matches on the
  feature id instead (`file:function`; the caller's own `metadata.feature` wins), so v1 wraps and diffs stay byte-for-byte
  unchanged. Site/client-id selectors are a next step.

## D12. v2 MVP design choices

- **Line numbers are mapped before reading code.** `recommend` reads code after `apply` has run, so plan.json's pre-apply line
  numbers are mapped through `apply.diff` first. Without this, every function name and etag was read at the wrong line,
  shifted by the inserted import.
- **Unlock rules are strict, so the dev-set unlock gain is small and honest: +1 site.**
  - `unlock.vendor_endpoint` needs a Python client that is not per-tenant and a model that is an exact Router catalog id, as a
    literal or as the file's default. mem0's xAI client (`grok-4.3`) qualifies. DeepSeek (`deepseek-chat`) and MiniMax
    (`MiniMax-M2.7`) are not catalog ids.
  - `unlock.stated_loss` needs a client that v1 patched and only droppable beta flags (prompt-caching,
    token-efficient-tools). On the dev set:
    - the anthropic-quickstarts beta sites need code-execution, computer-use or MCP betas, or use per-tenant keys;
    - llm-anthropic's betas are effort, fast-mode and mcp.
- **Scope cuts:**
  - **Model owner is a heuristic, not hand-audited.** Model cards are generated only for `app_literal` / `operator_config`
    owners. The SPEC M2 hand audit (eval/owner_audit.md) is not done.
  - **No TS vendor-unlock codemod.** There are no TS unlock candidates in the 15 repos; such sites are listed as "considered,
    not recommended".
- **Flex and cache cards need evidence the dev set doesn't provide.**
  - `tier.flex_background` only fires for clear background markers (worker/job/cron/batch/... paths).
  - `cache.key` only fires for a static prompt literal of at least ~1,024 tokens in the call's file.
  - The 15 repos have neither, so they get no flex or cache cards. The synthetic test repo covers both levers.
- **`up --agent`** prints only JSON on stdout (v1 step output goes to stderr). It exits 2 while any card is undecided.
- **Flex and fallbacks exclude each other on one feature.** Router ignores `allow_flex_tier` when a request carries a `models`
  list (M0), so the runtime drops Flex when fallbacks apply. Found while tracing one call for SYSTEM_DESIGN.md: approving both
  cards gave no Flex discount and no message. Now both cards name the conflict in "loses", `apply --decisions` warns, and the
  Python runtime logs the drop once (the TS runtime already did). No eval numbers change: the 15 repos have no Flex cards.
- **Eval v2 harness.** The held-out work had added a HOME/XDG sandbox to every eval test run. On the dev set this made one
  anthropic-quickstarts test skip ("~/.ssh empty"), so the sandbox now applies to the held-out set only; dev-set baselines were
  measured with the real HOME. anthropic-quickstarts was re-run under the dev-set settings: 189/189. The other 14 rows ran
  with the stricter sandbox and still matched their v1 baselines exactly.

## D13. v3 Track 1: what was built, where it departs from SPEC_V3, and what was cut

**Read order.** I started from an older revision of SPEC_V3.md (the file changed on disk minutes before the goal began, and
the newer revision was what got committed as "as given"). T0-T5 were built against the older text; once I read section 5 of
the committed spec I aligned the build with it (commit "spec section 5 alignment") and revised the spec where the build
deliberately departs ("Built:" notes in place, section 13).

**Design choices (first principles: tools for facts, the agent for judgments, the codemod for safe edits).**
- **Five commands, not eight.** `run` (floor + ledger + worklist), `check`, `wrap`, `claim`, `finish`. Discovery tools
  (`find`/`graph`/`config`) duplicate what a coding agent already does well; `find` became the hotspot list in `run`.
- **The oracle is the measure-only planner on the code as it stands**, made wrap-aware. One oracle serves `check`, `claim`,
  `finish` and every eval arm. Building it found three real bugs, each of which made correct code measure as not routed or
  the reverse: (1) the scanner lost a constructor's assignment target once wrapped; (2) the vendored runtime's embedded
  catalog counted as the repo's model names; (3) the per-tenant heuristic overrode clients someone deliberately wrapped.
  After the fixes `check` agrees with plan.py on 170/170 dev sites (eval/v3/check_agreement.json).
- **Tenancy is a judgment.** The floor still never wraps a client whose key looks per-tenant; but if the agent reads the code,
  decides the key is the operator's and wraps it, the tool reports that the code routes.
- **Three-valued trace, conditions on base URL / dispatch only.** "Model in catalog" is not a condition: the runtime decides
  it per request and falls back, so it never makes the report wrong.
- **Floor snapshot instead of a patch stack.** A git tree in a private index (no commit, no index change in the customer's
  repo); after 3 failed `finish` runs every agent-changed file is restored to the floor. Same guarantee (never below the
  floor), much less machinery. Per-patch drop is Track 2.
- **Router-on gate compares with the floor's own Router-on run**, not with Router off: suites that mock HTTP transports see
  Router requests inside their mocks under the deterministic floor too (found on the first synthetic repo).
- **The agent doesn't get the test command.** `finish` runs tests; agents re-running slow suites is wasted time.
- **Scoring credits a site as moved only when the code routes it AND the arm reports it migrated** (report precision is the
  product). The measure-only planner also recognizes migrations an agent writes by hand (a base URL assigned from a Router
  default or `RAMP_ROUTER_*` env, followed one constant deep), so arm B is not under-credited.

**Eval choices.**
- 10 fresh repos (held-out candidates never used, inventoried or tuned on), blind inventories committed before any arm C run.
- Arms: A (floor), **B (added)** Router's docs prompt + equal abilities from the untouched source, **B'** the same from A's
  floor (the spec's arm, which isolates the toolkit), C (`run --agent claude`). k=1, model = the CLI default
  (claude-sonnet-5) for every agent arm. The first B run was launched from the source by mistake; it is kept as arm B and
  B' was run from the floor as the spec says.
- The migrate prompt was frozen before arm C ran on the set. One C dry run (llmcord) happened first and showed a judgment
  error; the prompt was not tuned on it. The prompt was later revised to the spec's section 5.5-5.6 content (recipes, reason
  codes, Router facts), which was written before any v3 run.

**Cut (logged here per the goal's rule).**
- Arm A on the 30 held-out repos relabelled "seen" (spec 5.9): not run; the dev set's check-agreement run covers arm A's
  regression (48 migrated, 170/170 agreement).
- (Reinstated after all.) Blind adjudication: every site where an arm's report disagreed with the inventory on whether
  traffic should move (25 sites) was judged by a separate agent from the code and the rubric, with readings anonymized
  and source markers stripped (eval/v3/adjudication/). Results are reported against both the raw and the adjudicated
  inventory.

**After the eval (not used for any reported number).** The transcripts showed the hotspot list was the main cost driver:
on obsidian-copilot ~590 of 659 worklist items were lines in TS test/story files, which the Python scanner's production
check doesn't recognize. Fixed in `hotspots()` (TS/JS test, spec and story files excluded; model-name literals collapsed to
one hotspot per file): ~1,000 -> 179 hotspots across the 10 repos, obsidian-copilot 658 -> 37. C's results are from the
frozen toolkit (commit 5eec746).
- k=3 and pass^3, the reviewer subagent, per-patch drop, MCP, P4 agent judgments: Track 2 (WRITEUP.md next steps).
- Eval agents were not network-sandboxed (they had the model API and anything else); transcripts were checked for reads of
  inventory or results paths instead.
- Also after the eval: `wrap` now remaps the ledger's worklist, claim and fact lines in the file it edits. In the runs, the
  inserted import block left worklist items pointing 7 lines above their calls, so agents claimed the call at its new line
  (tool-confirmed) and again at the stale line to clear the worklist check (recorded unconfirmed, never credited).

## D14. v3 Improve phase

- **Same split as migration.** Facts (Flex eligibility, prices, capability-matched fallbacks, policy keys, droppable betas,
  catalog ids) stay with the tools; the agent records judgments with cited lines: `latency`, `task`, `model` (the production
  default behind a config value, validated against the catalog) and `cacheable` (static prompt prefix tokens). They replace
  v2's filename heuristics; every card shows tool facts and agent rationale separately.
- **A second, read-only agent session**, gated by `improve --hook` (blocks while a migrated feature is unjudged; cap 3).
  Separate context, no Edit tools: judgments can't change code by construction.
- **`model` was added after the first real run** (sec-insights): with the model unknown, only a spend-tag card could fire.
  The production model behind a config value has no oracle, so it is a judgment; the tool still checks it against the catalog.
- **The post-agent plan keeps the tool's reason when the agent agrees with its outcome** (an agent "kept" on another vendor's
  host must not erase the "another vendor's endpoint" reason the unlock rule reads; found on the showcase run).
- **Applying from the dashboard** records `applied_at`, runs a quick scope gate, and points at `finish`; edits from approved
  decisions (router.policy.json, compiled policy, unlock codemods) are in scope for the v3 gate.
- **Results** (eval/v3/IMPROVE_RESULTS.md): 9 features on 4 of the 10 fresh repos judged for $2.32; 4 -> 15 cards; latency
  judgments disagreed with the heuristic on 8 of 9 features. No Flex or unlock card fired on the fresh repos (no
  Flex-eligible background workload; the only beta needs to stay); all six levers fire on the showcase repo.
- **Not done:** model ownership is still v2's heuristic (it withholds model cards on 7 config-chosen features); judgments
  were not adjudicated against a blind reading (Track 2).


## D15. Improve, closed end to end: what driving the real dashboard found

The Improve phase was first verified card by card. Driving the whole loop the way a person does (`run --agent claude`, then
approve in the browser, Apply, `finish` again, then inspect the request each feature now sends; eval/v3/e2e_run.py) found six
defects that unit tests had not, each now fixed with a test:
- **Snapshot race.** The live page refreshes its bundle (which snapshots the tree) while Apply runs the quick gate (which
  snapshots it too); both shared one private git index, so one call could rebuild it between the other's `add` and its
  state-dir removal and the gate saw `.router-migrate/` as out-of-scope edits. Reproduced: 24 concurrent snapshots of one
  tree gave 5 different trees (3 of them containing the state dir) and 4 failed calls. Now one temporary index per
  snapshot, with the state dir excluded in the `add` pathspec itself.
- **Flex and fallbacks are either/or on one feature.** Router serves a request carrying a models list without Flex
  (measured in v2). Approving both, which "approve everything applicable" does, silently loses the half-price tier; the
  warning only appeared after Apply. Both cards now carry `conflicts_with` and a note before anyone approves (Flex
  suggested for background work), the dashboard shows it with the other card's state, and AGENTS.md/the skill say approve
  one, never both.
- **An applied unlock came back as a new card.** The unlock codemod moves the call's line, so its site id and card id
  change. Applied unlocks are now matched by feature (recorded in unlocks.json) and shown as applied.
- **Apply re-counted earlier cards** as newly applied; it now skips cards with `applied_at` and reports them separately.
- **The budget item counted every scanned feature** (kept ones included) and missed applied unlocks; it now counts and
  prices what routes through the key: list price per model with its features, a per-1,000-calls figure at a stated token
  shape (the agent's judged static prompt where there is one), and the features whose model is picked at runtime.
- **Policy applies were not in the ledger.** Every Apply now records an audited edit (files, cards, policy hash).

**Agent variance is real and recorded, not hidden.** chatgpt-telegram-bot run twice with the same command: the first agent
migrated the 4 per-tenant-credential sites, the second claimed them `flagged` (all tenants would share one Router key).
Both passed the gate and both are defensible; the ledger says why each time. That decision belongs to the user, not to
either agent: Track 2 turns "per-tenant provider credentials" into a question the run asks before migrating.

## D16. The dashboard, rebuilt for interpretability

**Goal.** A person who did not watch the run, technical or not, should see in one screen where the repo stands, what changed
and why, what the agent assumed, and what waits on them, and should see each change as code rather than as a rule id.

**Design.** The page now uses the Router console's own frame (left nav, light-weight page titles, bordered stat tiles, the
yellow-green primary action, olive icon squares, dashed green connectors) so it reads as a Router page:
- **Information architecture.** Overview (a headline sentence, five tiles, a provider -> Router flow diagram, the two
  steps as numbered cards with cost and time, what waits on you, what the agent assumed), Migrate (every call by status),
  Improve (six lever cards, then improvements grouped Live / Approved / Waiting / Model changes / Not chosen / Decided),
  Decisions (the to-do: approve-to-apply with "approve all", pick-one pairs, unlocks, models to test, calls that need
  judgment). Router setup and the Audit trail (claims, judgments, cost, every file diff) moved to the bottom of the nav.
- **One status vocabulary**, each with one color: lossless (green: moved by the rules), by the agent (lilac), unlocked
  (yellow-green: moved in Improve after a stated trade-off), needs your decision (amber), left in place (gray outline).
- **Changes as code.** Calls show the changed line as a word-level diff (the shim imports filtered out); improvements show
  the code or router.policy.json line they write beside their effect on the request body, key by key. Pending unlocks and
  model changes show a preview of the line they would write ("preview", "you edit").
- **Plain language from facts, computed in Python** (dashboard/view.py, part of the bundle) so the page stays a renderer:
  reason codes become sentences, judgments become "Nobody waits on it: it runs as a background job", and every assumption
  lists the changes it drives.
- **Either/or resolved in the UI**: once one side of a pair is approved or live, the other leaves the to-do list ("Not
  chosen").

**Kept:** the same stdlib server, session token, endpoints, frozen/live modes, no external assets but Google Fonts.

**Defects found while building it** (each with a test): (1) an unlock on a client the floor had already wrapped wrapped it
again (`_router_wrap_anthropic(_router_wrap_anthropic(Anthropic(), ...), ...)`), because the already-patched check compared
exact columns while unlock targets use (line, any column); (2) calls the agent moved were credited to the rules, because the
agent's claim keeps the line from before its own edit and the tool's re-check of the edited line says "migrated". The view
now credits a move to the rules only if the floor plan (made before any agent ran) moved that feature.

**Revision (same day): one job per page, and no idle re-renders.**
- **Each page has one job and each item one home.** Overview says where the repo stands (a headline, three doorway cards
  for Migrate / Improve / Decisions with their key numbers, the traffic diagram); it holds no item lists. Migrate is every
  call and how it reached Router (read-only). Improve is what is live in the code plus the lever map (live, waiting on you,
  or why a lever doesn't apply). Decisions is only what waits on you (ready to apply, approve, pick one, unlocks, models to
  test, calls for your judgment as one-line pointers to Migrate) with decided items as collapsed history. A change moves
  Decisions -> (Apply) -> Improve. The tile row, the overview lists and the sidebar spend footer were removed as duplicates.
- **Dropdowns closed by themselves and the diagram stuttered** because the live page re-rendered every 2 s: the bundle's
  `generated` stamp changes every second, so each poll looked like new data. The page now compares bundles without it,
  keeps open disclosures and half-typed forms across real updates, and redraws the diagram only when its geometry changes.
  Motion is limited to the page-enter fade on tab change, disclosure reveal, toast and apply-bar slide-in, the segment-bar
  grow and a calmer wire flow; all of it is off under prefers-reduced-motion.
- A syntax error in the page would blank the dashboard silently; tests/test_dashboard.py now parses the inline script.
