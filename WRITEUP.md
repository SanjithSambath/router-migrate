# router-migrate: results (v1 migrate, v2 MVP unlock + improve)

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

Every number below is from `eval/scoreboard.json`, `eval/scoreboard_v2.json` and `eval/heldout/audit.json`.

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
- **Unlocks.** Of the 43 dev-set sites on another vendor's endpoint, one is on a model Router serves under the same id:
  mem0's xAI client defaults to `grok-4.3`. DeepSeek `deepseek-chat`, MiniMax `MiniMax-M2.7` and the rest are not catalog ids,
  so moving them would change the model. All 6 beta-header sites need betas that can't be dropped (code execution, computer
  use, MCP), use per-user keys, or both.
- **Flex and cache.** No dev repo has a clear background job (worker, cron, batch) or a long static system prompt beside its
  call, so the engine proposes neither. It lists why per feature instead of guessing.
- **Model changes.** 39 of the 48 migrated sites choose the model at runtime from user config, a CLI flag or a library
  parameter. Model cards appear only where the app or its operator owns the model: 10 cards, in 3 repos.

## What M0 measured live (`research/m0_measurements.md`)
- **Flex:** costs exactly **50%** of default on gpt-5.4-mini (n=5 per arm). It is per model: 17 models have it, and
  gpt-4o-mini returns 400. One flex call took **61 s**, which is why `tier.flex_background` is for the background latency
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

## Next steps
1. **Fix the held-out findings first.** That means scanner patterns (aliased imports, wrapper functions, subclasses),
   resolving config defaults, the TS import-suffix rule and template detection. Then measure on a fresh held-out set.
2. **Build the evidence ladder, so model cards can climb from L1 to L2:** L2 replay with a judge, the shadow-config generator,
   and observe mode for real per-feature cost.
3. **Azure mapping unlock**, once the customer confirms the deployment-to-model mapping.
4. **Policy and docs plumbing:** client-id selectors baked into wraps, and two-way sync between RECOMMENDATIONS.md and
   decisions.json.
5. **More repo types:** app-style repos with hard-coded models in production routes, where model cards matter most.
6. **Rollout:** Jev/Switchyard automation and guarded rollouts, both needing account-setting work.
