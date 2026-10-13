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
  images, audio, files, `n>1`, logprobs, audio parts, unknown models, `previous_response_id`, Anthropic beta headers, and non-default
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
| `max_tokens`/`max_completion_tokens` -> `max_output_tokens`, min 16 | Responses rejects `max_tokens` (measured 400) |
| `stop` dropped for OpenAI-owned models and emulated client-side (also across stream chunks); forwarded for others | `stop` 501 on OpenAI models, honored on Claude (measured) |
| `temperature`/`top_p` dropped where `capabilities.temperature == false` | 400 on gpt-5-nano (measured) |
| `n>1`, logprobs, `logit_bias`, audio, prediction -> provider | Router 400 / no equivalent (measured: seed/n/logprobs 400) |
| function tools sent with explicit `strict: false` unless the caller set strict | Responses defaults tools to strict; loose Chat schemas would 400 |
| `store` removed; `previous_response_id`/`conversation`/`background` -> provider | store:true 400, previous_response_id 400 or silently ignored (measured) |
| `prompt_cache_key` added for Claude via Responses (the caller's feature tag) | Claude caching only works with it: 10,362 tokens cached vs 0 (measured) |
| `service_tier: "default"` on streaming calls | Flex is on by account default and was applied mid-conversation (measured); streaming is the latency-sensitive path |
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
