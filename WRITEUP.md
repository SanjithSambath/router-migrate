# router-migrate: results (v1 migrate, v2 MVP unlock + improve, v3 agent-native)

**For:** Kedar Thakkar.

**What it is:** a CLI with a coding-agent skill and a local dashboard (`router-migrate up`).
- **v1** moves a codebase's production OpenAI, Anthropic, LangChain, AI SDK and LiteLLM traffic onto Router, losslessly and
  automatically.
- **v2** adds an Improve phase:
  - cards that *unlock* more traffic once you accept a mapping or a stated loss;
  - same-model improvements (spend tags, cache keys, Flex for background jobs, fallbacks), applied through a compiled
    per-feature policy;
  - model-change recommendations, which are never applied automatically;
  - a Router setup checklist.
- **v3** puts a coding agent in charge of what rules can't decide: `router-migrate run . --agent claude` applies the
  deterministic floor, then the agent works the residue with five commands (`run`, `check`, `wrap`, `claim`, `finish`)
  that give it facts and a gate it can't skip. On 10 fresh repos it was the only approach with no wrong moves. After the
  gate it runs the **Improve** phase (agent judgments + tool facts -> cards for every Router lever) and opens the dashboard.

Every number below is from `eval/scoreboard.json`, `eval/scoreboard_v2.json`, `eval/heldout/audit.json` and
`eval/v3/results.json`.

## v3: agent-native migration (Track 1, measured on 10 fresh repos)

**Thesis.** A migration has three kinds of work, and each belongs to whoever has an oracle for it: **facts** (does Router
serve this request, does the runtime reroute this call, did tests get worse) are decided by tools the agent can't
override; **judgments** (where a config value points in production, whose key this is) are the agent's, with evidence
that resolves to real lines; **safe edits** (the one-line wrap) are a deterministic codemod. So: apply the floor first,
give the agent only the residue, give it tools for what it can't know rather than what it already does well, gate the
outcome once, and revert to the floor on failure. Full spec and rationale: [SPEC_V3.md](SPEC_V3.md), DECISIONS.md D13.

**What was built.** Five commands over the v1/v2 core: `run` (floor + ledger + worklist of judgment sites and
unexplained hotspots), `check` (does the runtime reroute this call in the code as it stands: `routes` / `wont_route` /
`routes_if` with the condition), `wrap` (the codemod at any client construction), `claim` (outcome + reason code +
evidence; a `migrated` claim the tool contradicts is rejected), `finish` (parse, scope, typecheck and tests vs baseline,
a Router-on run against router-sim, claims, worklist; reverts agent edits to the floor after 3 failures). `router-migrate
run . --agent claude` runs the whole loop headless with a Stop hook. `check` agrees with plan.py on 170/170 dev sites.

**Results** (10 repos never used for v1/v2, blind inventories committed before any agent run, k=1;
[eval/v3/RESULTS.md](eval/v3/RESULTS.md)). Ground truth after blind adjudication of the 25 disputed sites: 16 sites should move.

| arm | repos broken | correct moves | wrong moves | agent $ total (median/repo) | diff lines |
|---|---|---|---|---|---|
| A rules only (`router-migrate run`) | 0/10 | 6 | 14 | 0 | 177 |
| B agent + Router's docs prompt, from the source | 1/10 | 6 | 7 | 35.27 (3.47) | 3,212 |
| B' agent + docs prompt, from the same floor, same abilities | 0/10 | 10 | 16 | 25.05 (2.09) | 997 |
| **C agent + toolkit (`run --agent claude`)** | **0/10** | **10** | **0** | **23.13 (1.27)** | 191 |

- **C is the only arm whose report contains no wrong move**, at the lowest median cost and with 11 edits in total across
  the 10 repos (B 176, B' 96). It ran the test suite itself once (B 66, B' 55): the gate does that.
- "Wrong moves" are mostly config-driven or user-defined provider lists that point at other vendors in production. The
  runtime never reroutes those, so they are report errors, not outages; but the report is what a customer acts on.
- Against the raw inventory (21 should move) B' moves more correct sites (15, with 11 wrong) than C (10, 0 wrong). The
  gap is OpenManus, where C's agent read the shipped config (OpenAI client pointed at Anthropic's endpoint), claimed
  "kept", and the blind adjudicator agreed.
- C's misses: two obsidian-copilot sites where it made exactly the right edit inside a dynamic constructor table that the
  static oracle can't see (recorded unconfirmed, not credited), three LangChain providers in obsidian-textgenerator built
  through one dynamic constructor (no arm moved them), and one ambiguous `models.list`.
- Intervals are wide (0/10 broken bounds the rate at <=30.8%); this is indicative, as planned. Caveats (one inventory
  agent, k=1, arm B's test-command bug, no network sandbox) are in RESULTS.md.

**Found while building, fixed:** three oracle bugs (a wrapped constructor lost its assignment target; the vendored runtime's
embedded catalog counted as the repo's model names; the per-tenant heuristic overrode a deliberate wrap); the Flex/fallback
conflict (v2); and, from the transcripts, a hotspot list padded with TS test files (658 -> 37 items on the costliest repo,
fixed after the eval).

## v3 Improve: from migrated to optimized (`router-migrate run . --agent claude`)

After `finish` passes, the same command keeps going: it turns the migration into improvements across every Router lever,
then opens a local dashboard showing the whole run with cards to approve. The split is the same as for migration:
- **Tools decide the facts:** which models have a Flex tier, catalog and cache prices, capability-matched fallbacks, what the
  policy compiler accepts, which betas can be dropped, which vendor endpoints serve a catalog model.
- **A second, short agent session decides the judgments** the v2 filename heuristics used to guess, each with cited lines:
  `router-migrate judge FILE:LINE latency|task|model|cacheable VALUE --why ... --evidence FILE:LINE`. The session is gated
  by `router-migrate improve . --hook` (it can't stop while a migrated feature is unjudged), and it can't edit code.
- **Cards** for six levers: Tiers / Flex, fallbacks, prompt caching, spend tags, unlocks, model recommendations, each
  showing tool facts and agent rationale apart. Plus a Router setup checklist made specific to the repo (which features
  the Flex default, Jev and Switchyard apply to; shadow pairs taken from the model cards; budgets).
- **Nothing applies itself.** The dashboard's Apply runs `router-migrate apply --decisions` (policy compile, unlock
  codemods) and a quick scope gate, records what was applied, and points at `finish` for the full gate. Model cards are
  never applied.

**Measured on the 10 fresh repos** (eval/v3/IMPROVE_RESULTS.md), starting from arm C's migrations: the 9 migrated features
across 4 repos were all judged for $2.32 of agent time (median $0.54 per repo, 2-6 minutes). Judgments took the cards from
4 to 15: the agent found the production model behind config values (unlocking fallbacks and model candidates) and two
long static prompts in AI-Scientist (7k and 18k tokens: caching). It disagreed with the filename heuristic on 8 of 9
features' latency class, each with cited callers (an unattended research pipeline is background; three apps stream to a
waiting user). No Flex card fired, correctly: the one background workload runs on models without a Flex tier. On the
showcase repo (a nightly job, a chat route, an xAI client, an Anthropic beta call) one real run produced cards on all six
levers ($0.44 migrate + $0.29 improve; eval/v3/improve_showcase/). Screenshots: eval/v3/screenshots/.

**End to end, the way a person runs it** (eval/v3/e2e/, `eval/v3/e2e_run.py`): `run --agent claude`, then the live
dashboard driven in a real browser (approve, "Apply N changes"), then `finish` again, then the request each feature now
sends. On a fresh repo (sec-insights): migrated and gated (15/15 tests), 5 cards, 3 approved and applied in the UI, `finish`
OK, both features now send a fallback models list with the product tag; $1.01 of agent time. On the showcase repo: 6 cards
applied from the UI across every lever that applies, including **Flex** (the nightly job now sends `allow_flex_tier` and a
cache key) and **both unlocks** (the xAI client patched to route `grok-4.3` through Router; the Anthropic beta dropped),
`finish` OK. The setup checklist's budget item is specific to the repo: list price per model with its features, a
per-1,000-calls figure at a stated token shape, and the features whose model is picked at runtime; no cap is invented.
Closing the loop found six defects card-level tests had missed, the most important being that **Flex and fallbacks on one
feature are either/or** (Router serves a models list without Flex), which the cards now say before anyone approves
(DECISIONS D15). It also showed agent variance plainly: the same command on chatgpt-telegram-bot migrated its 4
per-tenant-credential sites once and flagged them once; both passed the gate, and that call should be the user's.

## Results on the 15 pinned repos (9 Python, 6 TS)

| | v1 (automatic) | v2 MVP (every unlock and same-model card approved) |
|---|---|---|
| production LLM sites moved to Router | **48 of 170** (lossless) | **49** with unlocks, reported beside the lossless 48 and never summed |
| repos where tests or typecheck got worse (Router off) | 0 of 15 | **0 of 15**, after `apply --decisions` and a re-run of every suite |
| live verification | 12 of 12 repos replayed at 100% | unlocked site served live by Router (mem0 xAI `grok-4.3` through `unlock_hosts`) |
| live spend | $0.000042 | **$0.000795** for all of v2 (M0 + unlock replay; cap $5) |

**v2 cards: 30 in total, and 20 meet their lever's evidence bar.**

| Lever | Cards |
|---|---|
| `attr.metadata` | 10 |
| `fallback.candidates` | 9 |
| `model.newer_cheaper_same_family` | 10 (recommendations only) |
| `unlock.vendor_endpoint` | 1 |
| `cache.key`, `tier.flex_background`, `unlock.stated_loss`, `model.deprecated`, `model.right_size` | 0 each |

- **Unfired rules** are covered by a synthetic test repo. Every rejected lever is listed per repo under "Considered, not
  recommended", with its reason.
- **Card precision is unmeasured.** The cards were not adjudicated against a blind rubric.

## Why the v2 gain is small, and why that's the right answer
- **Unlocks.** Of the 43 dev-set sites on another vendor, Azure or another gateway, one is on a model Router serves under the same id:
  mem0's xAI client defaults to `grok-4.3`. DeepSeek `deepseek-chat`, MiniMax `MiniMax-M2.7` and the rest are not catalog ids,
  so moving them would change the model. All 6 beta-header sites need betas that can't be dropped (code execution, computer
  use, MCP), use per-user keys, or both.
- **Flex and cache.** No dev repo has a clear background job (worker, cron, batch) or a long static system prompt beside its
  call, so the engine proposes neither. It lists why per feature instead of guessing.
- **Model changes.** 39 of the 48 migrated sites choose the model at runtime from user config, a CLI flag or a library
  parameter. Model cards appear only where the app or its operator owns the model: 10 cards, in 3 repos.

## What M0 measured live (`research/m0_measurements.md`)
- **Flex:** costs exactly **50%** of default on gpt-5.4-mini (n=5 per arm). It is per model: 17 models have it, and
  gpt-4o-mini returns 400. One flex call on gpt-5-nano took **61 s**, which is why `tier.flex_background` is for the background latency
  class only.
- **Fallbacks:** Router's `models` list works through raw HTTP, openai-python `extra_body` and openai-node. It advances past a
  403 or an unknown model.
- **Jev:** unmeasured. It's off on the account, and turning it on is a setting change.
- **Switchyard:** `model: "switchyard"` from app code returned 200 (gpt-5.6-luna at flex) with routing toggled off.

## Overfitting check: the frozen v1 tool on 30 held-out repos (`eval/heldout/AUDIT.md`)
- **Method:** blind, rubric-based inventories, with all 94 disagreements adjudicated against the code.
- **Results:**
  - **Migrated claims:** **53%** correct (the dev-set review audit found 89%).
  - **Coverage:** 63% of the sites that should move do; the scanner finds 71% of Router-relevant sites.
- **Impact:** 27 of the 33 wrong "migrated" claims would never move traffic, because the runtime only reroutes requests
  addressed to OpenAI or Anthropic. 4 would move per-user-key traffic onto the operator's Router key.
- **Breakage:** one held-out regression, a TS import suffix in node-DeepResearch. The tool also patched scaffold templates
  in gpt-pilot.
- **Status:** the held-out run is incomplete. 15 of 29 repos have test results, and the fixes have not been made.

## Router issues found
1. **No server-side state:** `store: true` returns 400; `previous_response_id` and `retrieve` don't work.
2. **Flex on by default:** applied without opt-in and can change mid-conversation.
3. **Catalog vs access:** the catalog lists models the key gets 403 on (`gpt-5.3-codex`).
4. **Claude caching:** via Responses, Claude caches only with `prompt_cache_key`.
5. **Silent flex drop:** `allow_flex_tier` is silently ignored with a `models` list, and a list with no tier is served at flex.
6. **Zero usage:** incomplete replies report **0 tokens and $0** (gpt-5-nano, `max_output_tokens` hit), so their cost can't
   be measured.
7. **Bare 504s:** timeouts return a plain-text 504 with no request id. Messages replies have no `usage.cost`.
8. **Switchyard:** changes model family mid-conversation.

## What Router should build (from what every migration here had to work around)
1. **A Chat Completions surface, or an official adapter.** Most production OpenAI code uses it; every migration today needs
   a translation layer like our runtime.
2. **Responses state** (`store`, `previous_response_id`), or the same documented error on every model: today it 400s on
   OpenAI models and is silently forgotten on others.
3. **Flex eligibility and price in `GET /v1/models`**, and Flex honoured (or rejected) with a `models` fallback list
   instead of silently ignored. We had to measure all three per model (M0).
4. **Dated model-id aliases** (`gpt-4o-2024-11-20` 404s; computer_use_ootb ships one) and access-aware catalog entries
   (the catalog lists models the key gets 403 on).
5. **First-class per-feature attribution** on `metadata.feature`, which our runtime already sends from every call site:
   it is how a customer confirms after rollout which features really route (the production truth no static tool has).
6. **A usage / shadow-results API**, so tools can show real dollars and validate model swaps without dashboard clicks.
7. **Drop-in base URLs for OpenAI-compatible vendors** (xAI, DeepSeek) and simpler BYOK for Gemini: 7 of the held-out
   misses and several fresh-repo sites are other vendors on catalog models, which today need a code port.
8. **Non-zero usage on incomplete replies, request ids on 504s, and `usage.cost` on Messages replies**, so cost and
   failures can be measured at all.

## Next steps (Track 2, in priority order)
1. **k=3 trials and pass^3 on a larger fresh set (20-30 repos)**, with exact intervals and paired tests; C's cost is now
   known ($2.31 mean, $1.27 median per repo), so a 30-repo, 3-trial run is roughly $210 of agent time.
2. **Dynamic trace**: import client factories under `.env.example` values with the runtime pointed at router-sim to turn
   `routes_if` and unconfirmed claims (like obsidian-copilot's dynamic constructor table) into facts.
3. **Scanner coverage for dynamic constructors** (`new (this.llmClass)(cfg)`, constructor lookup tables): the one pattern no
   arm moved; promote C's recurring fixes into rules so the floor rises.
4. **Fresh-context reviewer subagent** for low-confidence judgments, if judgment errors dominate at k=3 (the dry run's
   llmcord call was one).
5. **(Fixed after the eval) stale lines after `wrap`.** `wrap` inserts an import block, which moved the calls below it while
   the worklist kept the old line numbers, so agents claimed some calls twice (once at the stale line, recorded
   "unconfirmed"); `wrap` now remaps the ledger's lines in the file it edits. Harmless to scoring, noisy in the ledger
   (eval/v3/screenshots/chatgpt-telegram-bot-migration-ledger.png).
6. **Per-patch drop** instead of whole revert, if runs show agents mixing good and bad edits in one session (Track 1 did not).
7. **Earlier v1/v2 items still open**: fix the held-out findings (aliased imports, wrappers, config defaults, the TS
   import-suffix rule, templates); the v2 evidence ladder (L2 replay with a judge, shadow configs, observe mode) and P4
   cards driven by agent judgments instead of filename heuristics; Azure mapping; client-level policy; MCP over the
   five commands; Jev/Switchyard automation and guarded rollouts.
