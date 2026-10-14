# Adversarial review: router-migrate

Reviewed 2026-10-13, 01:00–01:25 PDT, against the working tree in `/Users/sanjithsambath/code/router-migrate`.

Scope:
- runtime (`router_runtime.py`, `routerRuntime.ts`);
- codemods (`apply_py.py`, `ts/tool.mjs`);
- planner/scanner;
- router-sim;
- eval outputs (`eval/logs/*.plan.json`, `*.diff`, `*.row.json`, `SCOREBOARD.md`).

No files under `router_migrate/`, `eval/` or `tests/` were modified. All repros live in `research/review_scratch/`. They use only local stubs, router-sim and 127.0.0.1 servers: no real provider or Router calls, and no API keys.

Note: an eval run was in progress during the review. At 01:05 the scoreboard listed 11 repos with no "Router on" column. At 01:20 it was regenerated with 15 repos and a new "tests, Router on (sim)" column, after `eval/run_eval.py` and `eval/scoreboard.py` changed on disk. Numbers below are from the 01:20 state unless stated.

Severity scale:
- **critical**: breaks working code by default, or breaks the most common usage once the key is set;
- **high**: real breakage or a materially wrong number shown to the judge;
- **medium**: a correctness gap that needs specific inputs;
- **low**: polish or reporting.

---

## Critical

### C1. Responses conversations chained with `previous_response_id` break on turn 2 (Python and TS)
- **Where:**
  - `router_migrate/runtime/router_runtime.py:427-437` (`plan_request`, `/responses` branch);
  - `router_migrate/runtime/routerRuntime.ts:412-418`;
  - `router_migrate/plan.py:228-229`. The planner only flags the call site, but the client is still patched (`plan.py:124-127`), so the runtime routes per request anyway.
- **What is wrong:** routing is decided per request, so turn 1 goes to Router and turn 2 goes to the provider.
  - Turn 1 (`previous_response_id=None` or absent) is routed to Router, and `store` is deleted, so Router stores nothing.
  - Turn 2 carries Router's `resp_…` id. The runtime sends it to the provider, which never saw that id.
  - Before migration, both turns hit OpenAI (store defaults to true) and the chain worked.
  - The same split breaks `responses.retrieve(id)` and `input_items.list(id)` after a routed `create`. Those calls stay on the provider (`plan.py:20`), but the id came from Router.
  - `has_store` is collected by the scanner (`scan_py.py:303`) but never read by `plan.py`.
- **Repro:** `.venv/bin/python research/review_scratch/prev_response_chain.py`
  ```
  turn 1 -> ok, id=resp_3650ad31937
  turn 2 -> BadRequestError: Error code: 400 - {'error': {'message': "Previous response with id 'resp_3650ad31937740938ebcb83a' not found." ...
  backend per turn: [('router', None), ('provider', 'resp_3650ad31937740938ebcb83a')]
  ```
- **Fix:**
  - Pin a conversation to one backend. If a client (or call site) ever sends `previous_response_id`, `conversation`, `store: true`, or calls `responses.retrieve` or `input_items`, do not route any `/responses` traffic from that client.
  - Statically, have the planner mark the whole client "kept" when any site on it uses those.
  - At runtime, keep a small set of Router-issued response ids and refuse to send any of them to the provider: raise a clear error instead.

### C2. Python codemod breaks imports in namespace packages and in scripts inside packages, even with the key unset
- **Where:** `router_migrate/apply_py.py:70-94` (`runtime_location`, `import_block`).
- **What is wrong:**
  - When the file's directory has no `__init__.py` (a PEP 420 namespace package, which is the common FastAPI `app/main.py` layout), the runtime is copied next to the file and imported absolutely: `from router_runtime import …`. That only resolves if the file's own directory is on `sys.path`. Importing `app.main` from the repo root fails.
  - For a script inside a package, run as `python pkg/sub/tool.py`, the relative import fails. The absolute fallback then looks in `pkg/sub/`, but the runtime lives in `pkg/`.
  - Both crash at import time with Router off, which breaks the "inert unless the key is set" guarantee (DECISIONS D2).
- **Repro:** `.venv/bin/python research/review_scratch/codemod_cases.py` (full output in `codemod_cases.out`)
  ```
  == ns_package
     check -c import app.main; print('import ok')  before: exit=0 import ok | after: exit=1 ModuleNotFoundError: No module named 'router_runtime'
  == script_in_package
     check pkg/sub/tool.py                          before: exit=0 script ok | after: exit=1 ModuleNotFoundError: No module named 'router_runtime'
  ```
- **Fix:**
  - Make the inserted import unable to fail. Wrap the final fallback too:
    `except ImportError: def _router_wrap_openai(c): return c`
  - Or, better, import the runtime by a path-independent name (one vendored top-level module plus a `sys.path`-free `importlib` load by `__file__`-relative path).
  - Add namespace-package and script-in-package fixtures to `tests/`.

### C3. The runtime's import-time `os.environ.setdefault("OPENAI_API_KEY", "unused-router-serves-this")` shadows keys loaded later by `load_dotenv()`
- **Where:** `router_migrate/runtime/router_runtime.py:691-692`.
- **What is wrong:** apps commonly import modules first and call `load_dotenv()` afterwards. `load_dotenv()` uses `override=False`, so it will not replace the placeholder.
  - Once the runtime is imported with `RAMP_ROUTER_API_KEY` in the environment (for example set by the platform), any `OPENAI_API_KEY` that lives in `.env` is never loaded.
  - From then on, every request the runtime deliberately leaves on the provider is sent with the placeholder key and gets 401: embeddings, unmapped or dated models, `n>1`, logprobs, `previous_response_id`.
  - It also flips "is OpenAI configured?" checks, and it leaks to child processes.
  - The pattern is not hypothetical. gpt-researcher's `cli.py` imports `gpt_researcher` at line 23 and calls `load_dotenv()` at line 360. Its 6 `OpenAIEmbeddings` sites are "kept" on the provider.
- **Repro:**
  ```
  $ eval/work/envs/gpt-researcher/bin/python research/review_scratch/dotenv_case/app.py eval/work/runs/gpt-researcher/gpt_researcher/router_runtime.py
  OPENAI_API_KEY after load_dotenv(): placeholder 'unused-router-serves-this'
  $ (cd eval/work/runs/gpt-researcher && env -u OPENAI_API_KEY RAMP_ROUTER_API_KEY=x ../../envs/gpt-researcher/bin/python -c "from gpt_researcher import GPTResearcher; import os; print(os.environ.get('OPENAI_API_KEY'))")
  unused-router-serves-this
  ```
- **Fix:**
  - Never mutate `os.environ`.
  - When the SDK constructor would fail for lack of a key, pass `api_key=` explicitly inside `wrap_openai`. That needs the constructor wrapped, for example `_router_wrap_openai(lambda: OpenAI(...))`, or a codemod that adds `api_key=os.environ.get("OPENAI_API_KEY") or "router"`.
  - Or document that the provider key must still be set.

---

## High

### H1. Planner over-claims: 5 of 46 audited "migrated" sites are never routed, and 4 more route but are expected to break
The full table is in **Planner claims audited** below.

Root causes, with locations:
- **Cross-file "by-name" linking.** A call on `self.client` links to any client assigned to a name ending in `client`, anywhere in the repo (`scan_py.py:397-405`). The linked client's target is then trusted over the call file's own vendor (`plan.py:297-300` only checks `VENDOR_IN_PATH` for `provider-or-config` targets).
  - TaskWeaver `taskweaver/llm/zhipuai.py:96` is a `ZhipuAI` client, linked to the OpenAI client in `openai.py`.
  - opencommit `src/engine/deepseek.ts:58` uses `baseURL: 'https://api.deepseek.com/v1'` and is linked to `openAi.ts:56`.
  - `VENDOR_IN_PATH` (`plan.py:35-36`) also misses `zhipuai.py`, because "zhipu" must be followed by a separator.
- **anthropic-beta sent through `extra_headers` is invisible to the planner.** `anthropic-quickstarts/agents/agent.py:113` always sends `anthropic-beta: code-execution-2025-05-22`, so `plan_request` keeps every call on Anthropic (`router_runtime.py:439-440`).
- **Unreachable branch.** `browser-use-demo/browser_use_demo/loop.py:140` (`client.messages.create`) is only reached when `betas` is empty. That only happens for the Vertex/Bedrock clients. The Anthropic client always adds the prompt-caching beta and takes the `client.beta.messages` path.
- **"Model chosen at runtime ⇒ migrated"** (`plan.py:195`), even when every model the app can select is outside the catalog.
  - chatbot-ui `app/api/chat/anthropic/route.ts:63`: the app's Anthropic list (`lib/models/llm/anthropic-llm-list.ts`) holds claude-2.1, claude-instant-1.2, claude-3-haiku-20240307, claude-3-sonnet-20240229, claude-3-opus-20240229 and claude-3-5-sonnet-20240620. None of them resolve.
- **Fix:**
  - Only link by name within the same file, or when the attribute's class or type is resolvable.
  - Apply the vendor-path rule to every linked call.
  - Detect `extra_headers` / `betas` containing `anthropic-beta`.
  - When the model is dynamic, look for model-list constants in the repo, and call the site "conditional" instead of "migrated" when none of them resolve.

### H2. Responses passthrough silently breaks stateful items that live on the provider: reasoning-item replay and vector stores
- **Where:** `router_runtime.py:429-437` and `routerRuntime.ts:414-418` delete `store` but forward the input unchanged. router-sim accepts `reasoning` items without checking them (`sim/server.py:198-200`).
- **What is wrong:** under Router, `store` is always false (measured: `store:true` returns 400). Two "migrated" sites replay prior output items on later turns.
  - openai-responses-starter-app `app/api/turn_response/route.ts:17` pushes every `response.output_item.done` item into `conversationItems` and resends it (`lib/assistant.ts:311-321`), with model gpt-5.2.
  - screenshot-to-code `backend/agent/providers/openai.py:544` does the same: `append_tool_results` extends `_input_items` with all output items (`openai.py:575-582`, `426-476`).
  - Both resend reasoning items (`rs_…`) without `encrypted_content` and without `include: ["reasoning.encrypted_content"]`. With `store=false`, OpenAI rejects such items ("Item with id 'rs_…' not found. Items are not persisted when store is false"). That is high-confidence documented OpenAI behavior, but unmeasured through Router.
  - The starter app also sends `file_search` with `vector_store_ids` created by its own `/api/vector_stores/*` routes, which stay on the provider. Router's credential cannot see those stores (measured: `file_search` with an unknown store returns 404 "Vector store … not found").
- **Evidence:** source lines above; live results `results_v2_annotated.md` Surprises #1, #2 and #18.
- **Fix:**
  - When routing `/responses`, add `include: ["reasoning.encrypted_content"]`.
  - Strip or convert `reasoning` items that lack `encrypted_content`.
  - Keep requests on the provider when they carry `file_search` with `vector_store_ids`, or `code_interpreter` with `file_ids`.
  - Add both cases to router-sim.

### H3. Wrapping rebuilds the httpx client: proxies, default headers, auth and event hooks are dropped, also for traffic that stays on the provider
- **Where:**
  - `router_runtime.py:615-622` (`_make_client` passes `transport=`, so httpx disables env proxies and `proxy=` mounts) and `:640-651` (`_with_client`: only the inner transport survives, not the `httpx.Client`'s headers, auth or hooks);
  - TS `routerRuntime.ts:387` (falls back to `globalThis.fetch`, so the SDK's `httpAgent` is ignored) and `:407`.
- **Repro, Python:** `runtime_cases.py` A7 and `custom_http_client.py`.
  ```
  before wrap: client._client._mounts = {'https://': 'HTTPTransport'}      (HTTPS_PROXY set)
  after wrap:  client._client._mounts = {} -> HTTPS_PROXY ignored for Router AND provider-fallback traffic
  explicit httpx.Client(proxy=...): mounts before=1 after=0
  Router OFF: provider request header X-Gateway-Auth=team-42, request event hook fired=True
  Router ON : provider request header X-Gateway-Auth=None, request event hook fired=False
  ```
- **Repro, TS (opencommit's real pattern):** `node research/review_scratch/ts_runtime/proxy_agent.mts`. It uses @anthropic-ai/sdk 0.19.2 from opencommit's node_modules plus `httpAgent: new HttpsProxyAgent(proxy)`.
  ```
  Router OFF (key unset):            proxy:CONNECT 127.0.0.1:56950, provider:direct
  Router ON (request not routable):  provider:direct
  ```
  opencommit's `OCO_PROXY` (`src/engine/anthropic.ts:47-50`) stops working as soon as the key is set.
- **Fix:**
  - Python: wrap the existing client's transport *and mounts* in place, so the client object, headers, auth and hooks are kept. For example, set `client._client._transport = RouterTransport(inner=old)` and wrap each mount, instead of `with_options(http_client=new)`.
  - TS: when the caller passed `httpAgent`/`fetchOptions`, use the SDK's own default fetch as `inner`, not `globalThis.fetch`.

### H4. TS codemod makes `.mjs` files import a `.ts` runtime with an extensionless specifier, which crashes at load even with the key unset
- **Where:** `router_migrate/ts/tool.mjs:808-819` (`runtimePlan`). A JS file in a package that has a tsconfig takes the TS branch, so it gets `routerRuntime.ts` and an extensionless or `.js` specifier.
- **Concrete victim:** `anthropic-quickstarts/managed-agents/self-hosted-sandboxes/vercel/runner/runner.mjs`, which now has `import { routerOptions } from "../lib/routerRuntime";`.
  - The webhook reads only `runner.mjs` and writes it alone into the sandbox (`api/webhook.ts:76-77, 252-253`), then runs `node runner.mjs`. The import cannot resolve there.
  - Locally, Node cannot import `.ts` with no extension either.
- **Repro:** `research/review_scratch/mjs_import/`
  ```
  $ cd research/review_scratch/mjs_import/runner && node runner.mjs
  Error [ERR_MODULE_NOT_FOUND]: Cannot find module '.../mjs_import/lib/routerRuntime' imported from .../runner/runner.mjs
  ```
- **Fix:**
  - For `.mjs`/`.cjs`/`.js` files that Node runs without a compile step, always vendor a transpiled `.mjs`/`.cjs` and use an explicit extension.
  - Do not patch files that are shipped standalone. A heuristic: read via `readFileSync(new URL(..., import.meta.url))`.
  - Also skip clients that have no routable call sites. The managed-agents clients only call `client.beta.*` APIs, so the patch adds risk with zero traffic moved: 34 patches for 6 migrated sites in quickstarts.

### H5. `sim_valid_pct = 100%` on every repo is close to tautological
- **Where:**
  - `eval/run_eval.py:154-160`;
  - `router_migrate/verify.py:117-212` (`replay`), `:153-156` (reuse);
  - `eval/scoreboard.py:79` (definition).
- **What is wrong:**
  - **Part (a) of the definition contributes nothing.** It counts "requests at router-sim from the repo's tests run with Router on", and that is 0 in all 15 repos (`sim_detail.repo_test_requests`).
    - Suites either mock the SDK or mock HTTP by URL. With Router on, rewritten requests are swallowed by the mocks. Verified on `llm`: `research/review_scratch/router_on_test.py llm tests/test_options_parameter.py::test_direct_prompt_preserves_options` gives "No response can be found for POST request on http://127.0.0.1:…/v1/responses", and "requests that reached router-sim: 0".
  - **Part (b) is one synthetic request per site.** Each replay sends `"Reply with exactly: pong"` with `max_tokens=16`, and `gpt-4o-mini` or `claude-haiku-4-5` whenever the model is dynamic. That is 48 of the 57 migrated sites in the current plans.
    - The replay builds its *own* `OpenAI(http_client=httpx.Client(transport=rt.RouterTransport(...)))`, so it never touches the patched repo code.
    - Identical signatures reuse one verdict. langchain-nextjs-template shows 8 "valid" from 1 request, and quickstarts 6 from 2.
  - **router-sim itself rejects the real request of a site that scores "100% valid".** screenshot-to-code's Anthropic site sends `thinking: {type: adaptive}` and `output_config: {effort}` (`agent/providers/anthropic/provider.py:378-383`).
    ```
    $ .venv/bin/python research/review_scratch/sim_real_shape.py
    eval replay shape     -> (200, '')
    site's real shape     -> (400, '{"type": "error", "error": {"type": "invalid_request_error", "message": "output_config: Extra inputs are not permitted"}}')
    ```
- **Fix:**
  - Rename the metric to `synthetic_replay_ok` and count unique requests only.
  - Drive replay through the repo's patched code path: import the patched module and call the site's function with a stubbed input. Failing that, at least replay the site's static kwargs (tools, response_format, thinking, output_config, extra_headers).
  - Report the "Router on" repo-test run as "N/A: suite mocks HTTP" when sim requests == 0.

### H6. LangChain `ChatAnthropic` "migration" swaps base_url and key wholesale, with no per-request planning or fallback
- **Where:** `router_runtime.py:695-699` (`chat_anthropic_kwargs`); applied at gpt-researcher `gpt_researcher/llm_provider/generic/base.py:164` (diff in `eval/logs/gpt-researcher.diff`).
- **What is wrong:** with the key set, *every* ChatAnthropic request goes to Router.
  - Dated or unmapped models (for example `claude-3-5-sonnet-20240620`) now 404 at Router, where before they worked on Anthropic.
  - Beta headers go to Router too.
  - The model is not mapped, so the "measured dated equivalence" table is skipped.
  - This contradicts D2: "an unservable request simply keeps working where it worked before".
- **Repro:** `.venv/bin/python research/review_scratch/chat_anthropic_case.py`
  ```
  ChatAnthropic-style key swap -> NotFoundError Error code: 404 - ... 'The model `claude-3-5-sonnet-20240620` does not e...
  wrap_anthropic (transport) path -> FROM-ANTHROPIC (stays on Anthropic, as DECISIONS D2 promises)
  ```
- **Fix:**
  - Pass a custom `httpx` client through LangChain's `ChatAnthropic` client kwargs, or patch the underlying `anthropic.Anthropic`. langchain-anthropic builds it in `_client`/`_async_client`, and they can be wrapped post-construction.
  - Or only emit the kwargs when the static model resolves to a catalog id, and count the site "flagged" otherwise.

---

## Medium

### M1. Re-running Python apply turns "migrated" into "flagged", and never patches clients added later to an already-patched file
- **Where:** `apply_py.py:123-124` returns "already patched" for *all* targets once any marker is in the file. `cli.py:170-176` and `199-208` (`reconcile`) then downgrade those sites.
- **Repro:** `codemod_cases.py`, case `idempotent`:
  ```
  run1 outcomes: [('svc/llm.py', 6, 'chat.completions', 'migrated')] summary migrated=1
  run2 outcomes: [('svc/llm.py', 10, 'chat.completions', 'flagged')] summary migrated=0
  run3 (new Anthropic client added) outcomes: [... 'flagged'), ('svc/llm.py', 16, 'messages', 'flagged')]
  run3 Anthropic() wrapped: False
  ```
  The TS path is correct: a rerun keeps "migrated" (`codemod_repos/ts_idem`).
- **Fix:**
  - Recognize `_router_wrap_*(<ctor>)` as already patched per call node, and count it as patched.
  - Patch only the unpatched constructors.
  - Merge the import instead of skipping the file.

### M2. A pre-existing `router_runtime.py` in the target package is silently overwritten
- **Where:** `router_migrate/cli.py:144-146`.
- **Repro:** `codemod_cases.py`, case `preexisting_runtime`:
  ```
  before: svc/router_runtime.py starts with "def route(x):\n    return 'user"
  after:  svc/router_runtime.py starts with '# router_runtime.py (vendored '
  import svc.api -> (1, "ImportError: cannot import name 'route' from 'svc.router_runtime' ...")
  ```
- **Fix:** refuse, or pick a unique name such as `_router_migrate_runtime.py`, when a non-vendored file already exists. Check the header line before overwriting.

### M3. Streaming translation gaps, in both Python `_ChunkWriter` (`router_runtime.py:311-392`, `503-520`) and TS `chunkStream` (`routerRuntime.ts:245-359`)
No live measurement of streamed tool calls exists in `router-live-check` (grep of `raw_v2` for `function_call_arguments` finds nothing). The streaming tool path is validated only against the event sequence that router-sim invents.

Repro: `runtime_cases.py` part B and `ts_runtime/check.mts`.

| case | observed | expected (Chat Completions) |
|---|---|---|
| B7 SSE framed with `\r\n\r\n` (valid SSE) | py: 0 chunks, no finish, **no error**; TS: `content:"" finish:[] done:true` | text "hello" |
| B3 `response.refusal.delta` | content "", no refusal, finish "stop" (non-stream path does map refusal) | `delta.refusal` |
| B2 tool args only in `function_call_arguments.done` / `output_item.done` | `args: ''` | `{"x":1}` |
| B5 `response.incomplete` with `content_filter` | finish "stop" (non-stream returns "content_filter") | "content_filter" |
| B6 tool call truncated by max_output_tokens | finish "tool_calls" with args `{"x": "trunc` | "length" |
| B8 last event without trailing blank line | sync: finish "stop"; **async: no finish chunk** (sync flushes at :492-494, async does not) | finish "stop" |
| B1 text + 2 interleaved tool calls + usage | correct | n/a |
| B4 stop string split across deltas | correct (`abc`, stop) | n/a |

Fix:
- Split on `\r?\n\r?\n`.
- Handle `response.refusal.delta`.
- Emit any arguments not yet streamed from `function_call_arguments.done`.
- Map `content_filter` and truncated-tool-call to their finish reasons.
- Flush the buffer in the async path.
- Capture one real streamed tool call per provider family against live Router before trusting this path.

### M4. "Per-request fallback" only covers pre-flight checks; any Router 4xx/5xx is a hard failure
- **Where:** `router_runtime.py:538-547` (routed responses are returned as-is), `routerRuntime.ts:427-436`.
- **Examples:**
  - `resolve_model('gpt-5.3-codex') -> gpt-5.3-codex`. It is in the catalog, but live returns 403 `data_residency_mismatch` (Surprise #20).
  - A valid Chat `tool_choice: {"type":"allowed_tools", ...}` is mapped to `{"type":"function","name":None}` (`router_runtime.py:213-216`) and fails with 400, where OpenAI would have served it:
    ```
    A4 RAISED BadRequestError (no provider fallback): ... "Missing required parameter: 'tool_choice.name'."
       tool_choice sent to Router: {'type': 'function', 'name': None} | provider requests: 0
    ```
  - `presence_penalty` is forwarded (`:194`), but only `frequency_penalty` was measured 200. Router rejects unknown fields with 400.
  - Plain-text 504s.
- **Fix:**
  - Treat `allowed_tools`/`custom` `tool_choice` as Untranslatable.
  - Drop catalog ids the account has been seen to 403.
  - Optionally retry once on the provider for 400 `unknown_parameter`, 403 and 404 `model_not_found` from Router. This is safe because those are pre-generation errors.

### M5. Silent semantic changes on routed Chat calls
Repro: `runtime_cases.py` A1, A2, A3 and A8.
- **`seed` is silently dropped** (`router_runtime.py:151` does not list it). By contrast, `logit_bias` is treated as Untranslatable and stays on the provider. That is inconsistent: callers who set a seed for reproducibility lose it with no signal.
- **`max_tokens=1` becomes `max_output_tokens=16`.** Classification or probe calls produce up to 16× more output.
- **Metadata goes from 16 caller keys to 17** (`feature` is added, `:240-244`). OpenAI caps metadata at 16 keys; the sim does not check.
- **`name` on user messages is dropped** (A1: "alice"/"bob" lost). Multi-speaker prompts change.
- **Assistant turns whose content is a `refusal` part are dropped from history** (A1).
- **Non-streaming calls are served at the account default tier, which is flex** (A8: `service_tier sent: None | sim served tier: flex`). Only streaming calls get `service_tier: "default"` (`:247-251`). Latency-sensitive non-streaming backends (most server code) get flex latency without opting in. DECISIONS D4 says this is intentional, but the scoreboard's "behavior survives" framing does not mention it.
- **Fix:**
  - Send `service_tier: "default"` unless the caller set one.
  - Treat `seed` like `logit_bias`.
  - Do not clamp below the caller's value; leave it on the provider instead.
  - Merge `feature` into an existing key when 16 keys are present.
  - Fold `name` into content or keep the request on the provider.

### M6. Per-user and per-tenant provider keys are collapsed onto one Router key, and the planner does not notice
- **Evidence:**
  - chatbot-ui builds `new Anthropic({ apiKey: profile.anthropic_api_key || "" })` per user (`app/api/chat/anthropic/route.ts:58-60`).
  - mem0's hermes plugin deliberately scopes keys per profile: "or a multiplexed secondary's memory extraction runs on the default profile's OpenAI account" (`integrations/hermes-plugin-mem0/_openai_llm.py:40-47`).
  - After migration, all of that traffic is billed to, and attributed to, the operator's single Router key. That changes billing and isolation, not just routing.
- **Fix:** when `api_key` comes from a per-request or per-user expression (not env or config), plan the site "flagged: per-tenant credentials".

### M7. Kill switch is case-sensitive
- **Where:** `router_runtime.py:71`, `routerRuntime.ts:41-44`.
- **Repro:** `kill_switch.out`
  ```
  ROUTER_MIGRATE_DISABLE=True  -> active()=True
  ROUTER_MIGRATE_DISABLE=TRUE  -> active()=True
  ROUTER_MIGRATE_DISABLE=on    -> active()=True
  ```
- **Fix:** `.strip().lower() in {"1","true","yes","on"}`. Better still, treat any non-empty value except "0"/"false" as off.

---

## Low

- **L1.** Translated Chat responses keep only `content-type`, `x-request-id` and `x-router-provider`. This includes Router 4xx/5xx and 429 bodies (`router_runtime.py:522-526`, `543`). `retry-after`, `x-should-retry` and `x-ratelimit-*` are lost, so the SDK's backoff ignores server hints. Fix: copy upstream headers, minus content-length and encoding.
- **L2.** The kwargs codemod appends `http_client=…` after `**kwargs` (`apply_py.py:41-56`). For example, gpt-researcher now has `ChatOpenAI(**kwargs, http_client=_router_http_client(), …)`. If `kwargs` ever carries `http_client` or `http_async_client`, it raises `TypeError: got multiple values` even with Router off. The ChatAnthropic variant hits the same error with `api_key`/`base_url` when Router is on. Fix: merge into a dict, `ChatOpenAI(**{**kwargs, "http_client": kwargs.get("http_client") or _router_http_client()})`.
- **L3.** `via_client` in plan.json is often wrong, which makes REPORT and HANDOFF misleading:
  - all 8 simonw/llm chat and responses sites point at the embeddings client `openai_models.py:508:17`;
  - quickstarts `agents/agent.py:113` points at `browser-toolset/python/run.py:71`;
  - aicommits' `generate-commit-message.ts` sites point at `src/commands/pr.ts:181`.

  The outcome happens to be right for llm, because every client in that file was patched.
- **L4.** `metadata.feature` and `prompt_cache_key` send repo-relative file paths and function names to Router on every request (`router_runtime.py:96-106`, `240-246`). Document this, or offer to hash it.
- **L5.** `with _router_wrap_openai(OpenAI()) as c:` closes only the copy that `with_options` made. The original client's connection pool is never closed (diff: quickstarts `daytona/sandbox_runner.py`, `modal/sandbox_runner.py`).
- **L6.** There is no catch-all around `plan_request`/`chat_to_responses`. An unexpected exception (for example a non-iterable `stop`, or `metadata` as a list) escapes the transport, and the SDK turns it into `APIConnectionError` after retries instead of sending the request to the provider unchanged. Fix: `except Exception: return None` in `plan_request` (log once).

---

## Things checked that held up

- **Never routes with the key unset.** `active()` is checked per request (`router_runtime.py:408`, `routerRuntime.ts:394`). With no key, `wrap_openai` returns the same object (A6: `key unset -> hosts contacted: ['api.openai.com'] | wrap_openai returns same object: True`).
- **No key crossing in either direction.** Router requests are built with fresh headers (A6: `['accept','authorization','content-length','content-type','host','user-agent']`; the provider key is absent). Provider-bound requests are the original request object, so the Router key is never sent to the provider.
- **Message translation of common shapes is correct** (A1):
  - multiple system messages;
  - developer role;
  - assistant text plus several `tool_calls`, in the correct item order;
  - list-content tool messages joined;
  - empty assistant text skipped.
- **Streaming of text plus multiple interleaved tool calls, usage chunk ordering, and stop strings split across deltas are correct** (B1, B4).
- **Thread-safety:** no shared mutable state per request. `_ChunkWriter` is per stream, and the `_LIBS`/`_CLASSES` caches race benignly. Stream close propagates (`_SyncChatStream.close` → `up.close()`; TS `cancel` → `reader.cancel()`).
- **The runtime works with openai-python ≥3 (`httpx2`)**, as exercised in simonw/llm's env.
- **TS apply is idempotent and reports correctly on rerun.**
- **Eval imports the migrated code.** After the 01:13–01:20 run:
  - `eval/work/envs/{gpt-researcher,mem0,swarm,shell_gpt,llm,llm-anthropic,TaskWeaver}` editable installs point to `eval/work/runs/<name>`;
  - `import gpt_researcher.llm_provider.generic.base`, `swarm.core` and `llm.default_plugins.openai_models` resolve to `runs/…` and contain the `_router_` patch, even from cwd=/tmp.
  - At 01:09, swarm and shell_gpt still pointed at `eval/work/base/<name>`, but tests run with cwd=`runs/<name>`, which puts the migrated copy first on `sys.path`.
  - Caveat: `tests_after` runs with Router **off** (`verify.py:63`), so the runtime is inert there. It proves only that the patched imports load.
- **Router-on test failures are now shown on the scoreboard** (since 01:20): llm 1089 passed / 226 failed, llm-anthropic 94 passed / 29 failed. The two I sampled are mock misses, as the definition says. "Tests: 15 repos unchanged, 0 worse" still refers only to the Router-off runs.

---

## Planner claims audited

**Totals:** 46 migrated sites checked across 12 repos. **37 confirmed** (9 of them conditional on config), **5 wrong** (never routed), **4 routed but expected to break**. Not audited: mem0 (11 sites).

Every site was checked by opening the source in `eval/work/pristine/<name>` and the patch in `eval/logs/<name>.diff`.

| repo | site | verdict | evidence |
|---|---|---|---|
| swarm | swarm/core.py:69 | confirmed | `OpenAI()` default client wrapped; model default gpt-4o (catalog) |
| shell_gpt | sgpt/handlers/handler.py:32 | confirmed (conditional) | routes with default `API_BASE_URL=default` and `gpt-5.4-mini`; `USE_LITELLM=true` path is not hooked |
| TaskWeaver | taskweaver/llm/openai.py:222 | confirmed | `OpenAI(base_url=config.api_base)`, default api.openai.com |
| TaskWeaver | taskweaver/llm/anthropic.py:80, :85 | confirmed (2) | `Anthropic(api_key=…)` wrapped |
| TaskWeaver | project/plugins/paper_summary.py:35, sql_pull_data.py:29 | confirmed, conditional (2) | ChatOpenAI branch only when `OPENAI_API_TYPE=openai` (else AzureChatOpenAI) |
| TaskWeaver | taskweaver/llm/zhipuai.py:96 | **wrong** | `self.client = zhipuai.ZhipuAI(...)` (:68), never wrapped; linked by name to openai.py's client |
| simonw/llm | llm/default_plugins/openai_models.py:1431, 1484, 1549, 1600 (chat), 2542, 2667, 2789, 2916 (responses) | confirmed (8) | `get_client` constructors (:1367/:1369) wrapped; Responses calls already send `store: False` + encrypted reasoning. via_client wrongly names the embeddings client |
| llm-anthropic | llm_anthropic.py:1800, 1880, 2027 | confirmed, conditional (3) | routes plain prompts; schema/thinking prompts send `output_config` (router-sim rejects it on Claude); `betas` path stays on Anthropic |
| screenshot-to-code | backend/agent/providers/openai.py:544 | **routes, expected to break** | replays `rs_` reasoning items without encrypted content under store=false (H2) |
| screenshot-to-code | backend/agent/providers/anthropic/provider.py:400 | **routes, expected to break** | sends `thinking:{type:adaptive}` + `output_config` that router-sim itself rejects (H5); unmeasured live |
| anthropic-quickstarts | agents/agent.py:113 | **wrong** | always sends `anthropic-beta: code-execution-2025-05-22` → runtime keeps it on Anthropic |
| anthropic-quickstarts | browser-use-demo/browser_use_demo/loop.py:140 | **wrong** | branch reached only for AnthropicVertex/AnthropicBedrock clients; the Anthropic client always takes `client.beta.messages` |
| anthropic-quickstarts | browser-use-demo/browser_use_demo/tools/browser.py:948 | confirmed | `AsyncAnthropic` wrapped, model claude-sonnet-5-5 |
| anthropic-quickstarts | computer-use-best-practices/dev_ui/localization_demo/server.py:128 | confirmed (conditional) | routes with `thinking:disabled`; with effort set it sends `output_config` (as above) |
| anthropic-quickstarts | customer-support-agent/app/api/chat/route.ts:226 | confirmed (conditional) | default model claude-haiku-4-5-20251001 resolves; 2 of 3 UI models (claude-3-haiku-20240307, claude-3-5-sonnet-20240620) stay on Anthropic |
| anthropic-quickstarts | financial-data-analyst/app/api/finance/route.ts:214 | confirmed | both UI models resolve |
| opencommit | src/engine/openAi.ts:95 | confirmed | client wrapped, user fetch preserved |
| opencommit | src/engine/anthropic.ts:98 | confirmed | default claude-sonnet-4-6; but OCO_PROXY breaks when active (H3) |
| opencommit | src/engine/deepseek.ts:58 | **wrong** | `baseURL: 'https://api.deepseek.com/v1'` → host not api.openai.com → never routed |
| chatbot-ui | app/api/chat/anthropic/route.ts:63 | **wrong** | none of the 6 selectable Anthropic models are in the catalog; per-user keys (M6) |
| openai-responses-starter-app | app/api/turn_response/route.ts:17 | **routes, expected to break** | provider-side vector stores for file_search; reasoning-item replay (H2) |
| aicommits | src/commands/pr.ts:196, :207; src/feature/generate-commit-message.ts:598, :872 | confirmed, conditional (4) | only when the configured provider is `openai` (createOpenAI branch) |
| gpt-researcher | gpt_researcher/llm_provider/generic/base.py:159 (ChatOpenAI) | confirmed | http_client injected |
| gpt-researcher | gpt_researcher/llm_provider/generic/base.py:164 (ChatAnthropic) | **routes, expected to break for non-catalog models** | wholesale key swap, no fallback (H6) |
| langchain-nextjs-template | app/ai_sdk/agent/action.ts:23, app/ai_sdk/tools/action.ts:38, app/api/chat/agents/route.ts:74, app/api/chat/retrieval/route.ts:80, app/api/chat/retrieval_agents/route.ts:76, app/api/chat/route.ts:51, app/api/chat/structured_output/route.ts:34, app/langgraph/agent/agent.ts:9 | confirmed (8) | `configuration: { ...routerOptions() }`, gpt-4o-mini, import paths resolve to `<root>/lib/routerRuntime.ts` |

**Wrong (5):**
- TaskWeaver `zhipuai.py:96`;
- quickstarts `agents/agent.py:113`;
- quickstarts `browser_use_demo/loop.py:140`;
- opencommit `deepseek.ts:58`;
- chatbot-ui `anthropic/route.ts:63`.

**Routed but expected to break (4):**
- screenshot-to-code `openai.py:544` and `anthropic/provider.py:400`;
- openai-responses-starter-app `turn_response/route.ts:17`;
- gpt-researcher `base.py:164`.

Removing only the 5 wrong sites from the 57 claimed moves the headline from 57/171 (33%) to at most 52/171 (30%), and the 9 conditional sites are counted as migrated even though they only route under specific config.

---

## Repro index (`research/review_scratch/`)

| file | proves |
|---|---|
| `prev_response_chain.py` / `.out` | C1 |
| `codemod_cases.py` / `.out`, `codemod_repos/` | C2, M1, M2 (+ header/TYPE_CHECKING/`__future__` placement OK) |
| `dotenv_case/app.py` / `app.out` | C3 |
| `custom_http_client.py` / `.out`, `runtime_cases.py` A7, `ts_runtime/proxy_agent.mts` / `.out` | H3 |
| `mjs_import/` | H4 |
| `sim_real_shape.py` / `.out`, `router_on_test.py` | H5 |
| `chat_anthropic_case.py` / `.out` | H6 |
| `runtime_cases.py` / `.out` (A1–A8, B1–B8), `ts_runtime/check.mts` / `.out` | M3, M4, M5, key handling |
| `kill_switch.out` | M7 |

Side effects of the review:
- I ran `pytest` read-only in `eval/work/runs/shell_gpt` and `eval/work/base/shell_gpt` with a scratch `HOME`, before noticing that an eval was running. I found no new `__pycache__` in `base/shell_gpt`, and the eval later recreated `runs/shell_gpt`.
- `router_on_test.py` runs with `PYTHONDONTWRITEBYTECODE=1`.
