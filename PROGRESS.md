# PROGRESS

Milestones from SPEC.md. Each entry: what landed, how it was verified, and the done_check output at that point.

## M1 scan (all 15 repos)
- Python scanner (libcst): clients, call sites (incl. bound method refs like `completion = client.chat.completions.create`,
  namespace aliases like `messages_client = client.messages`), other vendors, raw HTTP, env reads, prod/non-prod tagging,
  call-to-client linking. TS scanner (ts-morph) built in parallel by a subagent against the shared schema (router_migrate/SCHEMA.md).
- Validated against hand-made inventories of all 15 repos (research/inventory/*.md); misses fixed (shell_gpt bound call,
  llm-anthropic aliased stream, screenshot-to-code websocket false positive).

## M2 router-sim
- `router_migrate/sim/server.py`: both surfaces, SSE for both, catalog validation, every rule tagged [measured]/[upstream],
  JSONL request log with verdicts.

## M3 plan + model map
- `plan.py` buckets sites with exact reasons; catalog snapshot (98 models) + live refresh; dated-id equivalences only where
  Router itself reported them (22).

## M4 codemods + unit tests
- Python runtime + TS runtime (HTTP-boundary translation, D1), Python codemods (libcst), TS codemods (ts-morph).
- Tests: runtime through real SDKs vs router-sim (py 26, ts 10 scenarios), 8 Python golden fixtures + idempotency + e2e
  (patched app -> sim), TS golden fixtures (subagent).

## M5 eval harness + first full run
- `eval/run_eval.py`: pinned clones, per-repo envs inside the folder (uv-managed Python 3.11/3.12), recipes with measured
  baselines, tests before/after, Router-on test run against router-sim, synthetic replay. First full run: all 15 repos measured,
  tests identical before/after (Router off) everywhere.
- Compat matrix: the runtime works under openai 2.x (httpx) and 3.x (httpx2). openai-python 3 switched HTTP libraries, which
  made the first runtime silently inert there; this was caught by `make compat`.

## M6 baseline (the docs' migration prompt)
- Run by Claude agents on fresh clones (D9), scored per original call site in measure-only mode. Six repos are done; baseline
  results are in eval/baseline.json.

## M7 adversarial review
- An independent agent audited the runtime, codemods, planner and the numbers (research/adversarial_review.md):
  3 critical, 6 high, 7 medium and 6 low findings, each with a repro.

## M8 review fixes
- Python + TS runtimes:
  - Router-started Responses chains continue in-process;
  - provider-side state stays on the provider;
  - one provider retry on Router pre-generation refusals;
  - in-place wrapping keeps proxies/headers/hooks;
  - LangChain wrapped after construction;
  - no env mutation;
  - streaming edge cases;
  - semantic changes (seed, name, max_tokens<16) left on the provider;
  - service_tier default everywhere.
- Planner:
  - same-file linking only;
  - vendor-in-path on every call;
  - beta headers, per-tenant keys, stateful Responses, provider files and dynamic models outside the catalog are detected;
  - honest via_client.
- Codemods: the import cannot fail; reruns are idempotent per node; a foreign router_runtime.py is never overwritten.
- Metric: replay counts unique request shapes with each site's literal kwargs; Router-on suites that mock HTTP show N/A.
- Tests: 187 passing; rule registry 83 rules, all mapped to existing tests.

## M9 final eval + full baseline
- All 15 repos were re-run after the fixes, with live replay against api.router.com:
  - 48 of 170 sites migrated (75% of eligible first-party sites);
  - tests and typecheck unchanged in 15 of 15 repos;
  - router-sim replay 24/24 unique shapes valid;
  - live replay 100% in all 12 repos with migrated sites, for $0.000042.
- Baseline (the docs prompt, run by Claude agents) is now scored on all 15 repos: 90 sites moved, regressions in 8 of 15 repos,
  32 sites moved in the repos it left intact.
- Two TS over-claims caught by reading the chatbot-ui results were fixed:
  - per-user `apiKey: profile.*` is now flagged per-tenant;
  - a `baseURL` taken from a user-defined object is now kept.
- The baseline scorer now resolves absolute Python imports (screenshot-to-code: 0 -> 3 moved, in the baseline's favour).
- WRITEUP.md, EMAIL_TO_KEDAR.md (draft, not sent) and the README results section are written.

## v2 MVP (2026-10-13): one entry per goal item

- **v2.0 Spec and the v2 done check committed first** (`ed3a82d`).
- **v2.1 M0 live measurements** (`7af506c`): $0.0004 spent; research/m0_measurements.md and DECISIONS D11.
  - Flex costs 50% of default on gpt-5.4-mini, and eligibility is per model (17 models measured).
  - `models` fallback lists work through raw HTTP, openai-python `extra_body` and openai-node.
  - Jev vs `service_tier: "default"`: unmeasured, because Jev is off and enabling it is a setting change.
  - Switchyard from app code returned 200. No rule was cut; the Jev-dependent work is not built.
- **v2.4 Compiled policy in both runtimes.**
  - TS runtime (`7af506c`) and Python runtime plus compiler (`478020b`): `router.policy.json` is compiled into
    `router_policy.py` / `routerPolicy.ts`.
  - Keys: allow_flex_tier, prompt_cache_key, metadata, fallbacks, provider_timeout. No model key.
  - Precedence, a capability guard, and v1 bytes when the policy is invalid or off.
  - 21 shared router-sim fixtures pass in both languages.
- **v2.2 Unlock rules** (`bd7daea`): unlock.vendor_endpoint (`unlock_hosts`) and unlock.stated_loss (`drop_betas`). Both
  are checked per request at runtime and come with codemods.
- **v2.3 Recommend** (`bd7daea`):
  - Signals: latency_class, model_owner, resolved_model. Output: recommendations.json.
  - Same-model rules: attr.metadata, cache.key, tier.flex_background, fallback.candidates.
  - Model cards: deprecated, newer_cheaper_same_family, right_size. L0/L1 only, never applied, owned models only.
  - A "considered, not recommended" list.
- **v2.5 Decisions** (`bd7daea`): decisions.json with etags, plus `decide`, `apply --decisions`, `status` and `up --agent`
  (exit 2 while cards are undecided). RECOMMENDATIONS.md is rendered one-way.
- **v2.6 Dashboard.**
  - The page and the hardened stdlib server came in `bd7daea`; top-10 card restraint and the screenshots in `ae42c4d`.
  - Playwright screenshots of every tab of langchain-nextjs-template are in eval/screenshots/ (dark only; light mode removed).
  - Live-server check: 21 of 21 (`0ed4fe8`, eval/dashboard_live_check.md).
  - Tab rendering on all 15 eval reports (`03c9208`, eval/dashboard_tabs_check.md).
- **v2.7 Eval v2 and docs.**
  - Eval (`93e4709`): 48 sites lossless (equal to v1) and 49 with unlocks; 0 repos with worse tests; 30 cards, 20 meeting
    their evidence bar; live spend $0.000795; a REPORT.html per repo.
  - Docs: WRITEUP and the EMAIL draft (`662a31f`), wording fixes (`c7014bd`).
- **Tests:** 269 passing, 1 skipped.

## v2 rule deviations (recorded, not reversible)
- **API key:** the Router key was passed inline in some Bash commands during the session. It is in no committed or
  generated file (checked for the key prefix). Remedy: rotate the key on Router, an account setting only the owner can change.
- **Commit size:** the M0 measurements landed in one combined commit (`7af506c`), not in small per-item commits. History
  was left as is rather than rewritten.
- **PROGRESS updates:** this file was updated in batches, not after each item. It was restructured afterwards so each item
  points at its commit.

## v2 what's left
- Card precision is unmeasured (no blind adjudication). The model_owner signal has not been hand-audited.
- No TS codemod for vendor unlocks. Policy selectors can't target individual clients.
- Out-of-scope items are listed as next steps in WRITEUP.md. EMAIL_TO_KEDAR.md is a draft and has not been sent.

## After v2
- **Dashboard dark only** (`eb1e641`): light tokens and the theme toggle removed; 15 REPORT.html files and screenshots re-rendered.
- **SYSTEM_DESIGN.md**: rewritten from a first draft. Every claim checked against the code and eval files, and one real call
  traced through every stage with its actual artifacts. Corrections: the TS scanner uses the TypeScript compiler API (not
  ts-morph); the baseline moved 32 sites in the repos it left intact (not 90); the 61 s Flex call was on gpt-5-nano (also
  fixed in WRITEUP.md and EMAIL_TO_KEDAR.md); the wrap takes effect at client construction, so rollout needs a restart.
- **Flex/fallback conflict surfaced** (DECISIONS.md D12): cards, `apply --decisions` warning, runtime log, one new test (270 passing).

## v3 Track 1
- **T0 headless agent** (this commit): `claude -p` works with a long-lived OAuth token (the old OAuth session had expired:
  "OAuth session expired and could not be refreshed"). Smoke result in eval/v3/smoke/t0_headless.json: `SMOKE_OK`, claude-sonnet-5.
  The 10 fresh repos (eval/v3/REPOS.json: held-out candidates never used, inventoried or tuned on) are registered as eval set `v3`.
- **T1 facts for the code as it stands** (`git log` "v3 T1"): scanners mark wrapped constructions; measure-only plan counts them.
  Done when: a floor-wrapped call measures as migrated (was "handoff"); 270 tests pass.
- **T2 agent primitives + gate**: `run` (floor + git-tree snapshot + ledger + worklist of judgment sites and unexplained hotspots),
  `check`, `wrap`, `claim` (evidence must resolve; a migrated claim the tool contradicts is rejected), `finish` (parse, scope,
  typecheck and tests vs baseline, Router-on run vs the floor's Router-on run, claims re-checked, worklist resolved; reverts
  to the floor after 3 failed runs; `--hook` speaks the Stop-hook protocol). Done when: tests/test_agent_v3.py (8) pass; 278 total.
- **T3 frozen fresh-repo inventories** (this commit, before any arm C run): 10 blind rubric inventories (eval/v3/inventory/,
  same rubric as held-out: 97 production sites; expected 21 migrated, 7 handoff, 4 flagged, 65 kept) and 10 offline test
  recipes (eval/v3/recipes/; repos without tests get an import/compile smoke command that a broken import fails).
  Done when: 10 inventories + 10 recipes committed; every baseline ran twice with identical counts, network blocked.
- **T4 agent integration**: router_migrate/prompts/migrate.md (arm C prompt and skill body: the loop, the 8 outcome rules,
  hard rules, repo text is data), the skill updated to the v3 loop with the Stop-hook config, `run` made idempotent (never
  re-applies the floor over agent edits), arm B' prompt = Router's docs prompt verbatim + the same abilities (test command,
  Router-on run against router-sim via eval/v3/sim_test.py). Done when: a headless arm C dry run reaches `finish` through the hook.
- **T5 eval harness v3** (eval/v3/run_v3.py): per-trial copies with fresh git history, arms A / B_prime / C, identical scoring
  (recipe tests + typecheck vs a baseline measured in the harness; measure-only scan+plan mapped back to pristine lines and
  matched to the frozen inventory; reported outcome per site; $ and minutes; inventory-path leak check on transcripts).
  Two oracle bugs found and fixed on the way: the scanner lost a constructor's assignment target once wrapped (OpenManus
  measured "kept" after the floor), and the per-tenant heuristic overrode clients someone deliberately wrapped.
  Done when: arm A ran on all 10 repos (0/10 broken; 20 moved, 11 correct per inventory) and a headless arm C dry run on
  llmcord went through check/claim/finish with the Stop hook ($0.27, 1.3 min). The prompt is frozen from here: the dry
  run's one judgment error (llmcord's user-defined provider map, rule 2) was not tuned away.
