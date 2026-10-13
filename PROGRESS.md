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

## v2 MVP (2026-10-13)
- **M0:** live measurements for $0.0004, written up in research/m0_measurements.md and DECISIONS D11.
  - Flex costs 50% of default on gpt-5.4-mini, and eligibility is per model.
  - Router's `models` fallback list works through raw HTTP and both SDKs.
  - Jev is unmeasured; Switchyard works from app code.
- **Policy:** `router.policy.json` is compiled into `router_policy.py` / `routerPolicy.ts`.
  - It supports precedence, a capability guard, and v1 behaviour when the policy is invalid or turned off.
  - The same 21 fixtures pass in both runtimes against router-sim.
- **Recommend:**
  - Signals: latency_class, model_owner, resolved_model.
  - Rules: unlock.vendor_endpoint, unlock.stated_loss, attr.metadata, cache.key, tier.flex_background and fallback.candidates.
  - Model cards (deprecated, newer_cheaper, right_size) are cards only, for owned models only.
  - Every rejected lever goes on a "considered, not recommended" list.
- **Decisions:** decisions.json with etags, plus `decide`, `apply --decisions`, `status` and `up --agent` (exit 2 while cards
  are undecided). RECOMMENDATIONS.md is rendered one-way.
- **Dashboard:** a stdlib server on 127.0.0.1 (session token, Host/Origin checks, body cap) serving one page with five tabs in
  the Ryu cement tokens. REPORT.html is the frozen copy, and eval/screenshots/ holds Playwright shots of every tab, light and
  dark.
- **Eval v2:**
  - 48 sites lossless (equal to v1) and 49 with unlocks.
  - 0 repos with worse tests after approving every unlock and same-model card.
  - 30 cards; 20 meet their evidence bar.
  - Live spend $0.000795. There is a REPORT.html for every repo.
- **Tests:** 269 passing, 1 skipped.
- Live dashboard check (eval/dashboard_live_check.md): `up --no-browser` on langchain-nextjs-template. 21/21 checks pass: token, Host and Origin rejection, body cap; decide and apply through the API write decisions.json and the compiled policy.
- Dashboard tabs check (eval/dashboard_tabs_check.md): headless Chromium on all 15 eval REPORT.html files. The Router setup and Unsupported tabs render every bundle item in every repo.
