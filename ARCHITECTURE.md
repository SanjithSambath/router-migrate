# ARCHITECTURE

```
            scan                    plan                         apply                          verify / report
 repo ──► scan_py.py (libcst) ─┐                          ┌─► apply_py.py (libcst)  ─┐
          ts/tool.mjs scan    ─┴► sites ─► plan.py ─► patches ┤                        ├─► patched repo + vendored runtime
          (ts-morph)              (SCHEMA.md)  + catalog.py  └─► ts/tool.mjs apply  ─┘        │
                                               (snapshot | live)                              ├─► tests before/after (Router off)
                                                                                              ├─► repo tests with Router on ─► router-sim
                                                                                              ├─► synthetic replay per site ─► router-sim
                                                                                              └─► optional --live replay ─► api.router.com
```

## Runtime: the part that ships into the customer's repo

`router_migrate/runtime/router_runtime.py` (Python) and `routerRuntime.ts` (TS/JS) are rendered with the current catalog
(model ids, aliases, measured dated equivalences, capability bits) and copied into the target repo. They have no dependencies
beyond what the SDKs already need (httpx; Web `fetch`/streams).

Per request:
```
SDK client ──POST──► transport/fetch ──► plan_request()
                                          │ inactive (no RAMP_ROUTER_API_KEY) ............ provider (unchanged)
                                          │ host not api.openai.com/api.anthropic.com ..... provider (Groq, Ollama, Azure...)
                                          │ model not resolvable to a catalog id .......... provider
                                          │ embeddings/images/audio/files/n>1/logprobs/seed provider
                                          │ provider-side state (vector stores, unencrypted reasoning items, file ids,
                                          │   provider-issued previous_response_id, client opted out) ... provider
                                          │ /chat/completions ─► chat_to_responses() ─► Router /v1/responses
                                          │                      ◄─ responses_to_chat() / _ChunkWriter (SSE) ◄─┘
                                          │ /responses ─► drop store, expand Router-started chains inline ─► Router /v1/responses
                                          │ GET /responses/{router id} ─► answered from the in-process chain memory
                                          └ /v1/messages ─► model map ─► Router /v1/messages (passthrough)
Router replies 400 unknown/unsupported parameter, 403, 404 model_not_found or 501 ─► original request to the provider, once
```
The transport is installed in place: it wraps the httpx client's own transport and each proxy mount, so the client object,
its headers, hooks, proxies and pool are unchanged.
`metadata.feature` is derived from the first stack frame that belongs to the repo, so Router can attribute cost per
`file:function` without any call-site edits.

## Codemods

Edits are made only where a client or framework model is constructed:

| Pattern | Python | TS/JS |
|---|---|---|
| OpenAI / AsyncOpenAI | `_router_wrap_openai(OpenAI(...))` (transport installed in place under the client's transport + proxy mounts when active; `responses=False` for stateful Responses clients) | `new OpenAI({..., ...routerOptions()})` |
| Anthropic | `_router_wrap_anthropic(Anthropic(...))` | `new Anthropic({..., ...routerOptions()})` |
| module-level `openai.*` | `_router_install_openai_module()` after imports | - |
| LangChain ChatOpenAI | `_router_wrap_langchain(ChatOpenAI(...))` (wraps root_client / root_async_client in place) | `configuration: { ...routerOptions() }` |
| LangChain ChatAnthropic | `_router_wrap_langchain(ChatAnthropic(...))` (wraps `_client` / `_async_client`) | - |
| LlamaIndex OpenAI | `http_client=..., async_http_client=...` | - |
| LiteLLM | `_router_install_litellm()` (client_session globals) | - |
| Vercel AI SDK | - | `createOpenAI({..., ...routerOptions()})`; default `openai` import -> `createOpenAI(...)` |

Not edited: Azure, Bedrock and Vertex clients; clients with a static non-OpenAI/Anthropic base URL; embeddings-only clients;
non-production code (unless `--include-nonprod`).

## Files

| Path | Role |
|---|---|
| `router_migrate/cli.py` | subcommands, state in `<repo>/.router-migrate/` (scan.json, plan.json, apply.json, apply.diff, verify.json, REPORT.md, report.json, HANDOFF.md, handoff.json) |
| `router_migrate/scan_py.py` | Python scanner: imports, client constructors, API call chains (incl. bound refs and namespace aliases), raw HTTP, env, call -> client links |
| `router_migrate/ts/tool.mjs` | TS/JS scanner and codemods (ts-morph, minimal span edits) |
| `router_migrate/plan.py` | outcome + reason per site, client patch list, coverage summary |
| `router_migrate/catalog.py` + `data/` | catalog snapshot (98 models), live refresh, measured dated equivalences |
| `router_migrate/apply_py.py` | libcst transformer, runtime placement and import insertion |
| `router_migrate/verify.py` | test runners and parsers, git-stash baseline, router-sim test run, synthetic/live replay |
| `router_migrate/sim/server.py` | router-sim |
| `eval/run_eval.py`, `eval/recipes/*.json` | 15-repo eval (env setup + test commands with recorded baselines) |
| `eval/baseline.py` | docs migration prompt via `claude -p`, scored identically |
| `AGENTS.md`, `.claude/skills/router-migrate/SKILL.md` | the agent protocol for HANDOFF tasks |
