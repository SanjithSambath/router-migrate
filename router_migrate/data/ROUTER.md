# Router fact sheet

What Ramp's Router (https://router.com, API at `https://api.router.com`) does, measured where possible. Every fact
carries its source. Anything not measured says so; section 12 lists what nobody has measured yet. Measurements come from
one test account in October 2026, so account settings (Flex default, catalog visibility, data residency) may differ for
the key you are given: when a fact decides a change and a migration key is available, measure it with
`router-migrate check --run "CMD" --live` and put the result in the item's evidence.

| Tag | Source |
|---|---|
| [v1] | router-live-check, 76 live calls, 2026-10-12 (`results.md`, numbered "Surprises") |
| [v2 #n] | router-live-check v2, about 375 live calls, 2026-10-12 (`results_v2_annotated.md`, finding n) |
| [M0] | `archive/research/m0_measurements.md`, 29 live calls, 2026-10-13 |
| [L14] | live check with a migration key, 2026-10-14 |
| [docs] | docs.router.com, read 2026-10-13 (`archive/research/router_docs.md`); stated by Router, not measured |
| [catalog] | `data/catalog.json` (`GET /v1/models`, 2026-10-12/13), `data/flex_models.json`, `data/dated_equivalents.json` |
| [runtime] | the vendored runtime's code (`runtime/router_runtime.py`, `runtime/routerRuntime.ts`): what it does, not a measurement |
| [sdk] | `archive/research/sdk_surfaces.md` (SDK source reading) |
| [sim] | `sim/server.py`, the local Router simulator; each of its rules is marked measured or upstream |
| [upstream] | the OpenAI or Anthropic API contract; not measured through Router |

## 1. What Router is for

- One gateway for an app's LLM traffic: one Router key per deployment instead of a key per provider; Router picks the
  upstream provider per request and reports spend per key and per request metadata [docs].
- One catalog of models from many vendors behind both API surfaces: OpenAI (34 ids), Anthropic (13), xAI (8), open-weight
  models under provider-agnostic ids (DeepSeek, Kimi, GLM, MiniMax, Qwen, Nemotron, gpt-oss: 17), plus Bedrock and Vertex
  (Gemini) ids that need your own provider key (24 ids, `requires_byok`) [catalog]. So a call can change vendor without
  changing SDK: the Anthropic SDK served gpt-4o-mini, and the OpenAI SDK served Claude [v2 #24].
- Strategies (Flex cost-efficient routing, cross-provider routing, Switchyard, Jev auto routing, shadow models) are
  account settings on app.router.com, not request fields; only Flex has per-request controls (section 6.1) [docs, M0].
- Not offered: response caching on Router's side [docs], server-side conversation state (section 7), file storage.

## 2. Endpoints and surfaces

| Endpoint | Status | Notes |
|---|---|---|
| `POST /v1/responses` | served | OpenAI Responses API; every Router lever applies here [docs, v2] |
| `POST /v1/messages` | served | Anthropic Messages API for any catalog model, not only Claude; needs an `anthropic-version` header (400 without) [v1 #12, v2 #24] |
| `POST /v1/messages/count_tokens` | served | free; exact for Anthropic-served models, estimated otherwise (header `x-router-token-count-source`) [docs, v2] |
| `GET /v1/models` | served | OpenAI list shape; the Anthropic SDK's `models.list()` parses it too [v2 #24] |
| `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/images/generations`, `/v1/files`, `GET /v1/models/{id}` | 404, plain text `404 page not found` | [v1 #16] |
| `GET /v1/responses/{id}`, `/input_items` | 404 except background responses | `cancel` and `DELETE` return 405 [v2 #2] |
| `POST /v1/conversations` | 404 | [v2 #2] |
| batches, moderation, audio endpoints | not in Router's endpoint list | [docs]; audio *input* on Responses returns 400 "Audio input is not available" [v2] |
| `POST /v1/decisions`, `POST /v1/systemone` | documented, limited availability | not measured [docs] |

Chat Completions has no Router endpoint: code that uses it either goes through the vendored runtime (which translates
each request to Responses, section 9) or is ported to Responses.

## 3. Auth, base URLs, clients

- `Authorization: Bearer <router key>`; `X-Api-Key` is also accepted [docs]. The provider key is not used for routed
  traffic.
- Base URL: OpenAI-style SDKs take `https://api.router.com/v1`; Anthropic SDKs take `https://api.router.com` (they add
  `/v1`; a `/v1` base produces `/v1/v1/messages`) [sdk, docs].
- `RAMP_ROUTER_BASE_URL`: the runtime's default is `https://api.router.com`, and it strips a trailing `/v1` [runtime].
  `check --run` points it at the local simulator and `check --run --live` at a local recording proxy to Router, so code
  or scripts that call Router directly should build URLs from it the same way.
- Python's stdlib `urllib` default User-Agent gets 403 `error code: 1010` (Cloudflare); requests, httpx, aiohttp, curl,
  node, Go, the OpenAI SDK and an empty User-Agent are fine [v2 #23].
- Pointed directly at Router (no runtime) [v2 #24]: LangChain needs `use_responses_api=True` / `useResponsesApi: true`
  (404 otherwise); the Vercel AI SDK v7 default `router(model)` uses Responses, `router.chat()` 404s; Pydantic AI works
  with `OpenAIResponsesModel`; LlamaIndex `OpenAIResponses` is untested (crashed on Python 3.9 before any request); the
  OpenAI SDKs' `chat.completions`, `embeddings`, `models.retrieve` and `responses.retrieve` fail; the Anthropic SDKs
  (Python, Node) work fully, including tool loops, streaming, `count_tokens`, `models.list()` and non-Claude models.

## 4. Models

- Only catalog ids are served [docs, v1]. An unknown id returns 404 `model_not_found` [v1 #6].
- Catalog ids are undated. Responses report the dated id that served (`gpt-4o-mini-2024-07-18`,
  `claude-haiku-4-5-20251001`), and that dated id returns 404 when sent as `model` on either surface [v1 #3]. The runtime
  maps the 22 dated ids Router itself reported back to catalog ids (`data/dated_equivalents.json`) [runtime].
- A provider prefix (`openai:gpt-4o-mini`) returns 404 as `model` but is accepted inside a `models` list [v1 #4].
- The catalog doesn't prove a key can call a model: `gpt-5.3-codex` is listed but returned 403 `data_residency_mismatch`
  for the test account [v2 #20]. BYOK ids (Bedrock, Vertex/Gemini) need the caller's provider key; Gemini returns 403
  `provider_key_required` without one [docs]. 7 ids are `deprecated`; the 6 swept were still served [catalog, v2].
- `temperature` returns 400 on the 36 ids whose catalog says `temperature: false` (OpenAI reasoning models, Claude Opus
  4.7 and later, Sonnet 5, every *-5-5 and Fable model) [v1 #8, catalog]. The runtime drops `temperature`/`top_p` for
  those models when it translates Chat Completions [runtime].
- Reasoning: `reasoning.effort` on Claude needs `max_output_tokens` of about 1,100 or more (400
  "thinking.enabled.budget_tokens" at 400; fine at 2,000) [v2 #17]. Reasoning models with small output caps (64 tokens)
  return `incomplete` with no text [v2 sweep, sdk]. `reasoning.effort`, `reasoning.summary` and `text.verbosity` work on
  gpt-5-nano [v2].
- Open-weight ids can be served by different providers on each surface or turn (deepseek-v4.1-flash: Fireworks on
  Responses, Baseten on Messages; gpt-oss-120b switched provider mid tool loop), and `response.model` changes format with
  them [v2 #15, #22]. kimi-k3 answered "Black" for a red-pixel image once (one sample) [v2 #19].
- Prices are per 1M tokens in the catalog (`input`, `output`, `cache_read_input`); `router-migrate levers` prints them,
  live when a key is available. Examples: claude-haiku-4-5 $1/$5, claude-haiku-5-5 $0.10/$0.50, claude-sonnet-5 $2/$10,
  claude-opus-5-5 $4/$20, gpt-5.4-mini $0.75/$4.50, gpt-5.4-nano $0.20/$1.25, gpt-4o-mini $0.15/$0.60, gpt-oss-120b
  $0.15/$0.60, deepseek-v4.1-flash $0.30/$1.20, grok-4.7 $2/$6 [catalog].

## 5. Request fields by surface

`/v1/responses` [v1 #10, v2, sim]:
- Accepted: `input`, `instructions`, `max_output_tokens`, `temperature` (where allowed), `top_p`, `tools`, `tool_choice`,
  `parallel_tool_calls`, `text` (format, verbosity), `reasoning`, `include`, `metadata`, `stream`, `user`, `truncation`,
  `top_logprobs`, `safety_identifier`, `max_tool_calls`, `frequency_penalty`, `service_tier`, `prompt_cache_key`, plus
  the Router-only fields below.
- Rejected (400): `seed`, `n`, `logprobs`, `max_tokens`, `messages`, `response_format` (use `text.format`), `store: true`,
  any unknown field. `stop` returns 501 on OpenAI models and is honored on others [v1 #7].
- Function tools default to `strict: true` on Responses (Chat Completions defaults to false); a loose schema then fails
  with 400 unless `strict: false` is sent [upstream, sdk]. The runtime sends `strict` explicitly [runtime].

`/v1/messages` [v1, v2, L14]:
- The Anthropic Messages payload, streaming, tool use, extended thinking, images and `cache_control` all work on Claude;
  tool loops, streaming and images also work on gpt-4o-mini [v2].
- Claude is strict: an unknown field returns 400 "Extra inputs are not permitted" (e.g. `provider_timeout`). Non-Claude
  models silently drop unknown fields (`provider_timeout`, `top_k` returned 200 on gpt-4o-mini) [v2 #10, #14].
- `stop_sequences` returns 501 on OpenAI models [v1 #7]. `temperature` follows the catalog flag as on Responses [v2].
- Router-only fields (`models`, the timeouts, `allow_flex_tier`) are not supported on this surface [docs, v2 #10].
- Errors are Anthropic-shaped: `{"type":"error","error":{"type":"invalid_request_error","message":"..."}}`, with no
  `code` field [docs, L14].

Router-only fields (a provider would reject them, so they belong only on requests that reach Router) [docs, upstream]:
`models`, `allow_flex_tier`, `provider_timeout`, `timeout_before_headers`, `fallback_timeout`. Fields Router uses that
OpenAI's own API also accepts: `service_tier`, `prompt_cache_key`, `prompt_cache_retention`, `metadata` [upstream].
`speed` (Claude fast mode) is documented on both surfaces; not measured [docs].

## 6. Levers

### 6.1 Flex (service tier)
- Where: `/v1/responses` with a single `model` that has a Flex tier: 17 OpenAI ids (gpt-6.x, gpt-5.x, o3, o4-mini;
  `data/flex_models.json`). Not Claude (Anthropic has no tiers on Router) [docs]; not gpt-4o-mini
  (`allow_flex_tier: true` returned 400 "unknown service tier 'flex'") [v1 #9]. On `/v1/messages` the same OpenAI models
  were also served at Flex by default (16 ids in the sweep), with no Router control on that surface [v2 sweep, docs].
- Default: cost-efficient routing is on by default [docs] (`allow_flex_tier_default: true` on the test account [M0]).
  Plain `model` requests to Flex-eligible models were served at Flex without opting in (gpt-5-nano 5 of 5; 13 models on
  Responses) [v2 #3]. The tier can change between turns of one conversation [v2 #4].
- Cost: exactly -50% on gpt-5.4-mini (identical prompts, n=5 per arm, `usage.cost`) [M0]. Other models: not measured.
- Latency: gpt-5.4-mini at Flex 650-850 ms, the same as standard (n=5) [M0]; one gpt-5-nano Flex call took 61 s [M0].
  Docs: a streamed Flex attempt has an implicit 25 s deadline before a same-model standard fallback [docs].
- Controls: `service_tier: "default"` gives the standard tier (gpt-4o-mini, gpt-5-nano, gpt-5.4-mini; every call)
  [v1, v2, M0]. `allow_flex_tier: true` opts in, but Router still chooses (gpt-5.4-mini 5 of 5 at Flex, gpt-5-nano 1 of 5)
  [M0]. `allow_flex_tier: false` is documented as "always safe" (not measured) [docs]. A `models` candidate pinned
  `openai:gpt-5-nano:flex` was served at Flex [v2].

### 6.2 Fallbacks (`models` list)
- Where: `/v1/responses` only; exactly one of `model` / `models` (both: 400); up to 15 candidates, each a catalog id or
  `provider:model[:tier]` [docs, sim].
- Behaviour: falls through a missing model, a model the key may not use (403) and an invalid parameter (temperature on
  gpt-5-nano), silently: the response doesn't say which candidate failed [v2 #8, M0]. Cross-vendor works (gpt-5-nano to
  claude-haiku-4-5) [M0]. Docs: no fallback once a stream has started; invalid requests and refusals end immediately;
  truncated attempts are still billed [docs].
- Flex and fallbacks don't combine: `allow_flex_tier: true` with a list is ignored (served at standard) [M0]. Without a
  tier field, a list of plain catalog ids was served at Flex (3 of 3) [M0] and a provider-pinned single candidate at
  standard (2 of 2) [v2]; pin the tier per candidate or send `service_tier` when it matters.
- `fallback_timeout`: only with `stream: true` and at least two candidates (else 400). With [gpt-5-nano, gpt-4o-mini] and
  2.0 s it fell back, but the first token took 5.5 s; at 0.3 s both timed out and it returned 504 after 1.1 s [v2 #6].
- Cost: the serving candidate's price, only when it serves. A different model answers those requests. The least
  capable candidate limits tools, structured output, modalities and context [docs].
- SDKs: openai-python `extra_body={"models": [...]}` with no `model` works; openai-node accepts `models` as an unknown
  param [M0].

### 6.3 Prompt caching
- Responses: `prompt_cache_key` improves provider affinity [docs]. gpt-4o-mini, 9,020-token prompt: the second call
  cached 8,832 tokens and cost $0.00069 instead of $0.00135 [v2 group 6]. Claude via Responses does not cache without a
  key ($0.0104 per call both times); with one, 10,362 tokens cached and the call cost $0.00106 (-90%) [v2 #16]. The
  runtime sets `prompt_cache_key` to the request's label on Claude requests over Responses [runtime].
- Messages: Anthropic `cache_control` works on Claude (second call read 10,350 cached tokens); it is not translated for
  other providers [v2 #16, docs].
- `prompt_cache_retention: "24h"` for OpenAI models is documented, not measured [docs].
- Caching pays only for an identical prefix above the provider minimum (about 1,024 tokens) [upstream]: fixed
  instructions first, per-request text last. Cached reads cost the catalog's `cache_read_input` (claude-sonnet-5: $0.20
  instead of $2 per 1M) [catalog].

### 6.4 Spend labels (`metadata`)
- Responses: up to 16 short string labels per request (e.g. `feature`, `team`), stored with the usage record and shown as
  request tags; `user` (an email) and `product` are special keys for Ramp spend management [docs]. The runtime adds
  `metadata.feature` [runtime].
- Messages: Router records the whole `metadata` object but forwards only `user_id` to Anthropic [docs];
  `{user_id, feature}` on claude-haiku-4-5 returned 200 [v2]. The runtime adds no metadata on Messages; it sends the
  label as the header `x-router-migrate-feature` [runtime] (whether Router's spend views use that header: not measured).

### 6.5 Timeouts
- `provider_timeout` (seconds a provider call may take; buffered: per candidate, streaming: idle time between events)
  and `timeout_before_headers` (streaming: wait for the first event) [docs]. Maximum 660 s; above it, 400 [v2 #9].
- A timeout returns a plain-text 504 (`error code: 504`) with no JSON body and no `x-request-id` [v2 #7]; the runtime
  returns it to the app (it doesn't retry timeouts on the provider) [runtime].
- Responses only: on Messages, 400 on Claude and ignored on gpt-4o-mini [v2 #10].

### 6.6 Model and vendor choice
- Any catalog model on either surface (section 4); cross-provider history replay works in all six directions measured,
  including gpt-5-nano encrypted reasoning items replayed to Claude and gpt-4o-mini [v2 #25].
- Structured output works across vendors: `responses.parse` with Pydantic on gpt-4o-mini and Claude, zod, LangChain
  `with_structured_output`, Vercel `generateObject` [v2 sdk].
- A different model gives different answers: evidence is side-by-side samples through both models via Router.

### 6.7 Built-in and server tools
- Responses: `web_search` works on gpt-4o-mini, claude-haiku-4-5 and gpt-oss-120b (one search answer: $0.011, $0.021 and
  $0.0004, with 3,000-10,500 input tokens) [v2 #18]. `code_interpreter` works on gpt-4o-mini; its `usage.cost` showed no
  container fee, so cost may be understated (not verified) [v2 #18]. Remote MCP tools are passed through [v2].
  `file_search` can't be used: there is no Files API, and vector stores made with a provider key aren't visible (404)
  [v2].
- Messages: Anthropic server tools `web_search_20260209` and `web_fetch_20260209` work with claude-sonnet-5 (200,
  `x-router-provider: anthropic`). With claude-haiku-4-5, Anthropic returns 400 "does not support programmatic tool
  calling" for those versions: set `allowed_callers: ["direct"]` on the tool or use a model that supports it [L14].

## 7. State

Router keeps no server-side state [v1 #1, v2 #1-2]:
- `store` defaults to false; `store: true` returns 400.
- `previous_response_id`: 400 on OpenAI models; 200 but silently forgets on Claude and gpt-oss.
- `conversation` returns 400; `/v1/conversations` 404; stored responses can't be retrieved (section 2).
- Background mode returns 501 except on one alias [v1 #15].
- Reasoning items travel inline: with `include: ["reasoning.encrypted_content"]` they replay to any model [v2 #25].

## 8. Errors, limits, observability

- Responses errors: `{"error":{"message","type","param","code"}}`; Messages errors: Anthropic-shaped, no `code` [docs,
  L14]; unknown routes: plain-text 404 [v1 #16]; timeouts: plain-text 504 [v2 #7].
- A parameter error can list Router's hidden candidates ("all 2 candidates failed: openai:gpt-5-nano:flex ...") [v2 #5].
- 401: bad key or a key locked by its spend cap; 402: insufficient credits; 429: rate limited (`retry-after`); 501: the
  provider can't do a requested capability [docs]. No numeric rate limits are published; spend caps apply after usage is
  recorded, so concurrent requests can overshoot [docs]. Request bodies up to 100 MiB [docs].
- `x-router-provider` is on every successful generation response, and missing on errors, `GET /v1/models` and
  `count_tokens` [v1 #14]. The response body also names the serving `model` and `provider` [docs].
- `usage.cost` (USD) is on Responses only and matched list price exactly for gpt-4o-mini and Claude [v2 #25]; Messages
  has none [v1 #13]. Responses that end `incomplete` on `max_output_tokens` reported 0 tokens and $0 [M0].
- Messages token counts are wrong for Fireworks-served models (`input_tokens: 1`) [v2 #11]; the same prompt costs
  different input tokens per surface on grok models (1,251 on Responses vs 99 on Messages for grok-4.7) [v2 #12].
- Latency (small samples, Router only; no direct-provider comparison): gpt-4o-mini buffered p50 721 ms (p95 1,001, n=10),
  first token p50 760 ms (n=5); claude-haiku-4-5 buffered p50 926 ms (p95 1,328), first token p50 848 ms [v2 group 8].

## 9. The vendored runtime

`router_runtime.py` (Python, an httpx transport placed under the SDK's HTTP client) and `routerRuntime.ts` (a `fetch`
wrapper) decide per request [runtime]:
- Inactive until `RAMP_ROUTER_API_KEY` is set; `ROUTER_MIGRATE_DISABLE=1` turns it off again. Inactive means every
  request goes to the provider exactly as before.
- `POST .../chat/completions` to OpenAI becomes `POST /v1/responses` on Router (request, JSON and SSE stream translated
  back); `POST .../responses` goes to Router with `store` dropped; `POST /v1/messages` to Anthropic goes to Router's
  `/v1/messages` with the model id mapped.
- It stays on the provider, unchanged: embeddings, images, audio, files, model lists; models not in the catalog;
  `n > 1`, `logprobs`, `seed`, a non-zero `presence_penalty`, output caps under 16 (Python), a message `name`,
  `logit_bias`, audio or prediction options; `store: true`, `conversation`, `background`, stored prompts, a
  `previous_response_id` the provider issued, provider file ids, `file_search`; any request with an `anthropic-beta`
  header (unless the client was wrapped with `drop_betas` listing it); Azure, Bedrock and Vertex clients.
- Chains: a `previous_response_id` that Router served is replaced by the full history inline, from the last 256 chains
  kept in memory per process; `GET /responses/{id}` for those is answered locally; an evicted Router id gets a clear 400.
- On an OpenAI reasoning model it asks for `reasoning.encrypted_content` so reasoning can replay without `store`.
- Chat translation details: `strict` sent explicitly on tools; `response_format` to `text.format`; `stop` on OpenAI
  models emulated client-side (costs a few extra output tokens); `reasoning_effort` passed only where the model supports
  it (and, on Claude, with room to think); legacy `functions` supported.
- When Router refuses before generating, the original request goes to the provider once. Python: 403, 404
  `model_not_found`, 400 unknown or unsupported parameter, invalid value, "Extra inputs are not permitted", "requires a
  caller-owned provider credential", and 501. TypeScript: 403, 404 for a model, 400 with an unknown-parameter,
  unsupported-parameter or invalid-value code. Timeouts, 429 and 5xx go back to the app. `ROUTER_MIGRATE_FALLBACK=0`
  turns the provider retry off. The provider keys must stay configured for this path.
- Labels: a label the app sets on the request in the header `x-router-migrate-feature` wins; otherwise the label is the
  calling `<file>:<function>` (TS: from the stack, which is meaningless after bundling). Router-bound requests carry it
  as `metadata.feature` (Responses), as the header `x-router-migrate-feature`, and as the key for `router.policy.json`.
  `ROUTER_MIGRATE_FEATURE_TAG=off` stops labels and the automatic Claude cache key.
- Other environment: `ROUTER_MIGRATE_MODEL_MAP='{"old":"catalog-id"}'`; `ROUTER_MIGRATE_SERVICE_TIER` (unset: the account
  default; set: one tier on every routed request); `ROUTER_MIGRATE_POLICY=off`; `ROUTER_MIGRATE_TRACE=<file>` (one line
  per request: where it went and why).
- Entry points: Python `wrap_openai(client, responses=True, unlock_hosts=None, operator_key_only=False)`,
  `wrap_anthropic(client, drop_betas=None, operator_key_only=False)`, `wrap_langchain(llm, ...)`, `http_client()` /
  `async_http_client()` (pass as `http_client=`), `install_openai_module()`, `install_litellm()`. TypeScript
  `routerFetch(baseFetch?, opts)` and `routerOptions(baseFetch?, opts)` (spread into client options), with
  `{operatorKeyOnly, unlockHosts, dropBetas}`. `operator_key_only` routes only requests carrying the operator's own
  provider key; `unlock_hosts` routes another vendor's OpenAI-compatible host for catalog models.
- Injection doesn't work on openai-python 0.x, openai-node v3, openai-node v7 with x509 workload identity, or LiteLLM's
  Responses bridge; LangChain Python needs both `http_client` and `http_async_client` [sdk].

## 10. `router.policy.json`

Repo root, committed; adds Router fields per label to Router-bound requests only, so the provider path never sees them
[runtime, `schemas/CONTRACTS.md` section 1].
```json
{"version": 1,
 "defaults": {"metadata": {"team": "search"}},
 "globs": {"src/workers/**": {"allow_flex_tier": true}},
 "features": {"reply": {"service_tier": "default", "prompt_cache_key": "$feature", "provider_timeout": 60}}}
```
- Keys: `allow_flex_tier` (bool), `service_tier` (`default` or `flex`), `prompt_cache_key` (`$feature` becomes the label),
  `metadata` (string to string), `fallbacks` (1-14 catalog ids), `provider_timeout` (1-660), `model_map`
  (`{old: catalog id}`), `drop_params` (parameter names). Any other key or a wrong type makes the whole policy invalid,
  and an invalid policy is ignored.
- Matching: the request's label; an exact entry in `features` beats the longest matching glob (`*` within a path segment,
  `**` across), which beats `defaults`; `metadata` merges, and the request's own fields always win.
- Applies to Router-bound `/v1/responses` requests (translated Chat or Responses). On `/v1/messages` only `model_map` and
  `drop_params` apply.
- Guards: `allow_flex_tier: true` is skipped for a model without Flex; fallback candidates that lack the request's tools,
  structured output, modalities or temperature support are dropped; `allow_flex_tier` is dropped when fallbacks apply.
- The runtime reads a compiled copy beside itself (`router_policy.py`, `routerPolicy.ts`/`.js`/`.mjs`), not the JSON
  file. `router-migrate adapt` writes and compiles both, and the CLI recompiles a hand-edited `router.policy.json` on
  `check` and `finish`; confirm the effect with `check --run` (the simulator logs each Router request's fields).

## 11. Ways to put a call on Router

| Way | Keeps | Gives up |
|---|---|---|
| Runtime under the existing client (`wrap`, or `http_client()` / `routerFetch()` by hand) | the code's own request shape; per-request provider fallback; the app unchanged until `RAMP_ROUTER_API_KEY` is set; Chat Completions translated | Router-only fields only through `router.policy.json`; a translation layer in the request path |
| Point the client at Router when the key is set (base URL from `RAMP_ROUTER_BASE_URL`, key from `RAMP_ROUTER_API_KEY`, the originals otherwise) | a plain SDK call; Router-only fields inline on that path | no per-request provider fallback; only what Router serves can use that client |
| Port the call (Chat Completions to Responses, Anthropic server tools to Responses `web_search`, another SDK or vendor model) | a call Router can serve natively, with every Responses lever | a code change whose output may differ; say what changes |

## 12. Unknown / not measured

- Router's added latency compared with calling the provider directly.
- The Flex discount on any model but gpt-5.4-mini; Flex latency beyond a handful of calls.
- `allow_flex_tier: false`; `service_tier: "flex"` as a request field; `service_tier: "default"` on Claude or open-weight
  models; any way to keep an OpenAI model at the standard tier on `/v1/messages`.
- `prompt_cache_retention: "24h"`; `speed: "fast"`.
- `anthropic-beta` features through Router (computer use, files, MCP connector, context management): the runtime keeps
  those requests on Anthropic.
- Anthropic server tools with `allowed_callers: ["direct"]` on claude-haiku-4-5 via Router; other server tool versions.
- Whether Router's spend views use the `x-router-migrate-feature` header (Messages traffic carries no `metadata.feature`).
- Fallback on real provider outages (5xx, 429): documented, not measured.
- Batches, background mode beyond one alias, the Decisions and System One endpoints.
- Whether `code_interpreter` container fees are billed; image input on open-weight models beyond one sample.
- Rate limits; spend-cap overshoot under concurrency.
- Account differences: catalog visibility, data residency, the Flex default and other strategy settings were measured on
  one test account.
