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

(continued below as milestones complete)
