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
  that give it facts and a gate it can't skip. On 10 fresh repos it was the only approach with no wrong moves.

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

## Next steps (Track 2, in priority order)
1. **k=3 trials and pass^3 on a larger fresh set (20-30 repos)**, with exact intervals and paired tests; C's cost is now
   known ($2.31 mean, $1.27 median per repo), so a 30-repo, 3-trial run is roughly $210 of agent time.
2. **Dynamic trace**: import client factories under `.env.example` values with the runtime pointed at router-sim to turn
   `routes_if` and unconfirmed claims (like obsidian-copilot's dynamic constructor table) into facts.
3. **Scanner coverage for dynamic constructors** (`new (this.llmClass)(cfg)`, constructor lookup tables): the one pattern no
   arm moved; promote C's recurring fixes into rules so the floor rises.
4. **Fresh-context reviewer subagent** for low-confidence judgments, if judgment errors dominate at k=3 (the dry run's
   llmcord call was one).
5. **One line-number space.** v1's REPORT.md and plan.json use pre-floor line numbers while `check`/`claim` use current
   ones; agents that read the report sometimes claimed a site twice (once at the stale line, recorded "unconfirmed").
   Harmless to scoring, noisy in the ledger (eval/v3/screenshots/chatgpt-telegram-bot-migration-ledger.png).
6. **Per-patch drop** instead of whole revert, if runs show agents mixing good and bad edits in one session (Track 1 did not).
7. **Earlier v1/v2 items still open**: fix the held-out findings (aliased imports, wrappers, config defaults, the TS
   import-suffix rule, templates); the v2 evidence ladder (L2 replay with a judge, shadow configs, observe mode) and P4
   cards driven by agent judgments instead of filename heuristics; Azure mapping; client-level policy; MCP over the
   five commands; Jev/Switchyard automation and guarded rollouts.
