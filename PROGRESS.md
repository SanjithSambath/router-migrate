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

(continued below as milestones complete)
