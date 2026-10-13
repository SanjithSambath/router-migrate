# Router (Ramp) LLM gateway: documentation reference

Researched 2026-10-13 from https://docs.router.com. Every docs page is also served as raw
markdown at `<page>.md` (stated in https://docs.router.com/llms.txt); this file was built from
those `.md` sources plus a rendered-HTML cross-check of the Connect page. Full page list:
https://docs.router.com/sitemap.xml (30 pages). No Router API calls were made.

Abbreviations for citations used below:

| Tag | URL |
| --- | --- |
| CONNECT | https://docs.router.com/getting-started/connect |
| QUICK | https://docs.router.com/getting-started/quickstart |
| OVERVIEW | https://docs.router.com/getting-started/overview |
| AGENTS | https://docs.router.com/getting-started/coding-agents |
| ENDPOINT | https://docs.router.com/api/endpoint |
| RESP | https://docs.router.com/api/responses-and-messages |
| MODELS | https://docs.router.com/api/models |
| META | https://docs.router.com/api/metadata |
| DECIDE | https://docs.router.com/api/decisions |
| ERRORS | https://docs.router.com/api/errors-and-limits |
| CHOOSE | https://docs.router.com/guides/choose-a-model |
| FALLBACK | https://docs.router.com/guides/fallbacks |
| SPEND | https://docs.router.com/guides/control-spend |
| BYOK | https://docs.router.com/guides/bring-your-own-key |
| MONITOR | https://docs.router.com/guides/monitor |
| BEDROCK | https://docs.router.com/guides/bedrock |
| FLEX | https://docs.router.com/strategies/cost-efficient-routing |
| XPROV | https://docs.router.com/strategies/cross-provider-routing |
| SWITCH | https://docs.router.com/strategies/switchyard-routing |
| AUTO | https://docs.router.com/strategies/auto-routing |
| BENCH | https://docs.router.com/strategies/benchmark-routing |
| SHADOW | https://docs.router.com/strategies/shadow-models |
| CACHE | https://docs.router.com/strategies/cache-optimizations |
| FAQ | https://docs.router.com/resources/faq |
| SUPPORTED | https://docs.router.com/supported-models |
| INDEX | https://docs.router.com/ |

---

## 1. Baseline migration prompt (verbatim)

Source: https://docs.router.com/getting-started/connect#migrate-with-a-coding-agent
(section "Migrate with a coding agent"; the page introduces it with: "Paste this into your coding
agent, pointed at the codebase you want to move. It covers every provider SDK, not just OpenAI.")
Copied byte-for-byte from https://docs.router.com/getting-started/connect.md (63 lines,
sha256 `b28bac76314831adff0c0c86aeb0f8f5127499ffb14d9c9ef6bcdea0b0b670c8`); matches the rendered
HTML ("Show all 63 lines").

```text
Migrate this codebase to Router, a gateway that exposes one
OpenAI Responses-compatible API in front of models from multiple providers.

HOW ROUTER WORKS
- Base URL: https://api.router.com/v1
- Auth header: `Authorization: Bearer $RAMP_ROUTER_API_KEY`.
- Router holds the provider credentials. The application must never
  send provider API keys.
- This migration targets two routes: `GET /v1/models` and
  `POST /v1/responses`.
- Router also serves an Anthropic Messages compatibility surface at
  `POST /v1/messages` and `POST /v1/messages/count_tokens`. A codebase
  already on the Anthropic API can point at that surface instead of
  being rewritten; migrate it to Responses only if asked. There is no
  Chat Completions surface: OpenAI Chat Completions callers must be
  converted to Responses.
- Every request and response uses the OpenAI Responses schema,
  whichever provider serves it.
- Valid model IDs are account-specific. They come from
  `GET /v1/models`. Never invent one or reuse a provider's public
  model name.

WHAT TO CHANGE
1. Replace each LLM client with an OpenAI Responses client pointed at the Router base URL
   and `RAMP_ROUTER_API_KEY`.
2. Convert any Chat Completions call to a Responses call:
     messages                -> input
     system message          -> instructions
     max_tokens              -> max_output_tokens
     choices[0].message      -> output / output_text
     streamed chat chunks    -> Responses SSE events
   Inspect `response.output` rather than assuming text-only results
   when tools, reasoning, or media are involved.
3. Move model names into configuration read from an env var. Do not
   hardcode them at call sites.
4. Delete provider API keys from env files, secret managers, CI
   config, and client construction.
5. Leave prompts, tool definitions, retry policy, and business logic
   unchanged.

ROUTER-ONLY REQUEST FIELDS
Pass these through the SDK's `extra_body`, or the equivalent escape
hatch if the library has one:
- models: ordered fallback list of 1-15
  `provider:provider-model` candidates. Mutually exclusive with
  `model`.
- allow_flex_tier: boolean. Opts a request in or out of lower-cost
  Flex capacity. It returns 400 for ineligible models, so omit it
  unless you know the target model supports it.
- provider_timeout: seconds allowed per provider call.
- timeout_before_headers: seconds to wait for the first streaming
  event.
- metadata: short string labels such as feature or team, used for
  spend attribution.

RULES
- Change the transport only. Do not alter model behavior or output
  handling beyond what the schema requires.
- If a call site uses image, audio, file input, or web search,
  confirm the target model supports it before switching.
- List any call site you could not migrate instead of guessing.
- After migrating, verify with a single text-only request before
  running the full test suite.
```

### What the baseline prompt tells an agent to do (summary)

1. Treat Router as one OpenAI Responses-compatible API at `https://api.router.com/v1`, auth
   `Authorization: Bearer $RAMP_ROUTER_API_KEY`; never send provider keys.
2. Target only `GET /v1/models` and `POST /v1/responses`. Anthropic-API codebases *may* point at
   `POST /v1/messages` (+ `count_tokens`) instead and only convert to Responses if asked. No Chat
   Completions surface: convert Chat Completions callers to Responses.
3. Replace each LLM client with an OpenAI Responses client (base URL + `RAMP_ROUTER_API_KEY`).
4. Chat->Responses mapping: `messages->input`, system message->`instructions`,
   `max_tokens->max_output_tokens`, `choices[0].message->output/output_text`, chat chunks->Responses
   SSE events; inspect `response.output` when tools/reasoning/media are involved.
5. Move model names to env-var config; model IDs must come from `GET /v1/models` (never invent or
   reuse a provider's public model name).
6. Delete provider API keys from env files, secret managers, CI config, client construction.
7. Leave prompts, tools, retry policy, business logic unchanged; "change the transport only".
8. Router-only fields via `extra_body`: `models` (1-15 `provider:provider-model`, mutually
   exclusive with `model`), `allow_flex_tier` (claims it "returns 400 for ineligible models"),
   `provider_timeout`, `timeout_before_headers`, `metadata`.
9. Confirm model support before migrating image/audio/file/web-search call sites; list call sites
   it could not migrate; verify with one text-only request before the full test suite.

### Gaps / errors in the baseline prompt (relative to the rest of the docs and live measurements)

- Says `allow_flex_tier` "returns 400 for ineligible models"; RESP and CONNECT both say Router
  *ignores* `true` on ineligible models (only non-boolean values return 400). Internal conflict.
- Says to pass `models` through `extra_body`; CONNECT itself says SDK clients take a single `model`
  "so none of them can send the mutually exclusive `models` field" and shows a raw `client.post`
  instead. Sending `model` + `models` returns 400 (RESP, "Route selectors").
- Says "Every request and response uses the OpenAI Responses schema" while also recommending the
  Messages surface (which uses the Anthropic schema and Anthropic error envelope, ENDPOINT).
- Does not mention: the Anthropic SDK base URL is `https://api.router.com` *without* `/v1` (RESP);
  `fallback_timeout` (RESP); `store`, `previous_response_id`, `stop`, `temperature`,
  `prompt_cache_key`, dated model ids, User-Agent blocking, `use_responses_api` for LangChain,
  `context_window` for LlamaIndex, Vercel custom `fetch`, or what to do with embeddings/images/files
  call sites (none are served, ENDPOINT).
- Does not name any env var for the model or base URL (CONNECT's own snippet uses
  `RAMP_ROUTER_MODEL`).

---

## 2. Endpoints, base URLs, env vars, per-SDK setup

### Base URL and endpoints

- Base URL: `https://api.router.com/v1`; "keep `/v1` in the base URL, since every endpoint lives
  under it" (ENDPOINT, CONNECT).
- Documented endpoints (ENDPOINT, OVERVIEW):

| Endpoint | Docs description |
| --- | --- |
| `GET /v1/models` | Lists models available to your key, with limits, capabilities, pricing |
| `POST /v1/responses` | OpenAI Responses API |
| `POST /v1/messages` | Anthropic Messages API |
| `POST /v1/messages/count_tokens` | Counts input tokens of a Messages request (free) |
| `POST /v1/decisions` | OpenAI Decisions API ("limited availability") |
| `POST /v1/systemone` | TypeSafe System One API (TypeSafe Jev only) |

- "Router doesn't serve OpenAI Chat Completions, embeddings, files, or batches." (ENDPOINT)

### Authentication (ENDPOINT)

- `Authorization: Bearer <router-api-key>` **or** `X-Api-Key: <router-api-key>`; if both are sent
  Router uses `Authorization`.
- Use the Router key, never a provider key.
- `POST /v1/messages` requires a non-empty `anthropic-version` header (RESP); examples use
  `Anthropic-Version: 2023-06-01`.

### Env var names used in the docs

| Name | Where | Notes |
| --- | --- | --- |
| `RAMP_ROUTER_API_KEY` | CONNECT, QUICK, ENDPOINT, RESP, MODELS, all SDK snippets | The Router key |
| `RAMP_ROUTER_MODEL` | CONNECT, RESP (Anthropic snippet) | Model id from `GET /v1/models` |
| `RAMP_ROUTER_BASE_URL` | INDEX only (hero terminal animation: `export RAMP_ROUTER_BASE_URL=https://api.router.com/v1`) | Not used in any SDK snippet |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` | not mentioned anywhere | Docs give no guidance on reusing provider-SDK env vars; all snippets pass `base_url`/`api_key` explicitly |

### Per-SDK setup (exact strings)

| SDK / framework | Client / class | Base URL param and value | Router-only fields go in | Source |
| --- | --- | --- | --- | --- |
| OpenAI Python | `OpenAI(api_key=os.environ["RAMP_ROUTER_API_KEY"], base_url="https://api.router.com/v1")`, call `client.responses.create(...)` | `base_url="https://api.router.com/v1"` | `extra_body={...}` | CONNECT |
| Anthropic Python | `Anthropic(api_key=os.environ["RAMP_ROUTER_API_KEY"], base_url="https://api.router.com")`, `client.messages.create(...)` | `base_url="https://api.router.com"` ("the SDK adds `/v1` itself") | Router-specific fields "not currently supported on this endpoint" | RESP |
| LangChain Python | `ChatOpenAI(model=..., api_key=..., base_url="https://api.router.com/v1", use_responses_api=True, extra_body={...})`; install `langchain-openai>=0.3.9` | `base_url` | `extra_body` | CONNECT ("Keep `use_responses_api=True`") |
| LangChain TS | `new ChatOpenAI({ model, apiKey, useResponsesApi: true, configuration: { baseURL: "https://api.router.com/v1" }, modelKwargs: {...} })` | `configuration.baseURL` | `modelKwargs` | CONNECT |
| Vercel AI SDK | `createOpenAI({ apiKey, baseURL: "https://api.router.com/v1" })`; model `router.responses(process.env.RAMP_ROUTER_MODEL!)` | `baseURL` | custom `fetch` that mutates the JSON body ("`@ai-sdk/openai` validates a fixed set of provider options and drops anything else") | CONNECT |
| LlamaIndex | `OpenAIResponses(model, api_key, api_base="https://api.router.com/v1", max_output_tokens, context_window=128000, additional_kwargs={"extra_body": {...}})` from `llama_index.llms.openai` | `api_base` | `additional_kwargs={"extra_body": ...}` | CONNECT ("Use `OpenAIResponses`, not the plain `OpenAI` class. Without `context_window`, LlamaIndex raises `ValueError: Unknown model`") |
| Pydantic AI | `OpenAIResponsesModel(model, provider=OpenAIProvider(api_key, base_url="https://api.router.com/v1"), settings={"extra_body": {...}})` | `base_url` | `settings={"extra_body": ...}` | CONNECT ("Use `OpenAIResponsesModel`, not `OpenAIChatModel`") |
| LiteLLM | not documented | - | - | - |
| Ordered fallback list from OpenAI SDK | `client.post("/responses", cast_to=httpx.Response, body={"models": [...], "input": ...})` | (client base URL) | top-level body | CONNECT |

Streaming (OpenAI SDK): same call with `stream=True`; example passes `extra_body={"provider_timeout": 60, "timeout_before_headers": 10}` (CONNECT).

### Ramp CLI (`ramp router ...`) (AGENTS, QUICK, FAQ, SWITCH)

- Installer: `curl -fsSL https://router.com/install.sh | sh` (browser sign-in), headless:
  `... | sh -s -- --api-key 'your-router-key'`.
- `ramp router configure <agent>` e.g. `ramp router configure opencode`; agent names:
  `claude-code`, `codex`, `opencode`, `pi`, `hermes`, `conductor`. `ramp router cursor` for Cursor
  guided setup. `ramp router unconfigure`, `ramp router refresh` (Codex/Conductor catalog updates;
  also after first setting a Switchyard frontier model), `ramp uninstall`.
- The CLI is documented only for configuring **coding agents**, not for migrating application code.
- Manual agent setup notes: base URL `https://api.router.com/v1`; "Router does not support the
  legacy OpenAI Chat Completions API. You must configure your agent to use the Responses API." (AGENTS)

### Response headers (ENDPOINT, MONITOR)

- `x-request-id`, `x-trace-id`: echoed if sent, generated otherwise.
- `X-Router-Provider`: provider that served the request (e.g. `openai`, `fireworks`).
- `x-router-token-count-source`: `provider` | `estimated` on `count_tokens` (RESP).
- `retry-after`, `retry-after-ms`: preserved from provider when available (ERRORS).

---

## 3. Request / response field reference

### `POST /v1/responses` (RESP)

Route selectors: exactly one of

| Field | Type | Accepts |
| --- | --- | --- |
| `model` | string | Model ID from `GET /v1/models` |
| `models` | string[] | 1-15 `provider:provider-model[:service-tier]` candidates, tried in order |

Both or neither -> `400`.

Standard OpenAI fields passed through: "including `input`, `instructions`, `tools`, `text`,
`reasoning`, `metadata`, `max_output_tokens`, and `stream`". Support depends on the model's
capabilities in `GET /v1/models`; "a request for a capability the provider cannot satisfy returns
`501`." (RESP)

Router-only fields (RESP table):

| Field | Type | Behavior |
| --- | --- | --- |
| `allow_flex_tier` | boolean (or `null`) | Overrides the routing profile's cost-efficient default for this request. `true` on ineligible model: ignored. `false`/`null`: Flex off, "always safe to send". Explicit `true` also opts out of Jev Auto Routing and Switchyard. Non-boolean -> 400 `` `allow_flex_tier` must be a boolean. `` (`param: allow_flex_tier`, `code: invalid_request`) |
| `provider_timeout` | positive number (s) | Seconds a provider call may take. Buffered: per candidate (total can be timeout x candidates). Streaming: idle time between events after start |
| `timeout_before_headers` | positive number (s) | Streaming: seconds to wait for the first event; bounds the window in which fallback is still possible |
| `fallback_timeout` | positive number (s) | Only for streamed `models` list with >= 2 candidates (else 400). Per-candidate deadline to first text/refusal/tool-arg delta (reasoning and lifecycle events don't count). All miss -> `504`. Default 25 s implicit deadline on a streamed Flex attempt with same-model standard fallback (that default counts reasoning deltas too) |

- Timeouts above the gateway maximum are rejected with `400 invalid_request` naming the field and
  maximum, not clamped; docs advise reading the max from the error (RESP).
- Other documented request fields:
  - `service_tier`: send a tier `id` from the model's `router.service_tiers` (e.g. `ultrafast`)
    (MODELS). Cost-efficient routing applies only if the request "does not set `service_tier`"
    (FLEX). Bedrock: omit or `default` (BEDROCK).
  - Tier pinning via third segment of a candidate: `openai:gpt-5.4-mini:flex`,
    `fireworks:accounts/fireworks/models/kimi-k3:priority` (SPEND, CHOOSE).
    Tiers: OpenAI `default|flex|priority`; Fireworks `default|priority`; Google
    `standard|priority|flex`; xAI `default|priority`; Sail `default|flex`; Anthropic none
    (use fast mode); Bedrock not supported (SPEND).
  - `speed`: `"standard"` | `"fast"` (Claude fast mode, models with Fast column) on both
    `/v1/responses` and `/v1/messages`; any other value rejected (SPEND).
  - `prompt_cache_key`: improves provider affinity for caching; affinity 5 min default;
    `prompt_cache_retention: "24h"` extends to 24 h for OpenAI models (CACHE).
  - `include: ["reasoning.encrypted_content"]` when `capabilities.reasoning.continuation.required`
    (MODELS).
  - `metadata` (see below).
- Response object: standard OpenAI Response plus `model` = model that actually served (may differ
  after fallback/strategy) and `provider` (also on every streaming event and as
  `X-Router-Provider`). `usage.cost` (USD number) when priceable; on streaming it is on the final
  response event as `response.usage.cost`; omitted (not 0) when unpriceable (RESP).
- Streaming returns OpenAI Responses SSE events; once the stream starts Router no longer falls back
  and mid-stream failures appear as a truncated event stream (FALLBACK, ERRORS).

### `metadata` keys (META, RESP)

- Free-form short string labels, e.g. `feature`, `team`, `environment`. Stored with the usage
  record, shown as `request_metadata` and as `key:value` entries in `request_tags`.
- Special keys for Ramp Token Spend Management: `user` (must be an email address) and `product`
  (stable tool name).
- On `/v1/messages`: Router records the full `metadata` object but only forwards Anthropic's own
  `user_id` key when Anthropic serves the request (RESP).

### `POST /v1/messages` (RESP)

- Accepts an Anthropic Messages create payload incl. streaming; needs `anthropic-version` header.
- Any model whose `router.surfaces` includes `messages` (not only Claude).
- "Router-specific fields, such as `models` and the timeouts above, are not currently supported on
  this endpoint, and `usage.cost` is not currently included in Messages responses."
- Anthropic `cache_control` passed through to Anthropic models; not translated for other
  providers (CACHE). RESP says Messages gives "more precise control over caching for Claude
  models".
- Errors use the Anthropic envelope.

### `POST /v1/messages/count_tokens` (RESP)

Same body as `/v1/messages`; returns `{"input_tokens": N}`; exact for Anthropic-served models,
estimated otherwise (`x-router-token-count-source`). Not charged. Claude on Bedrock: no exact
counting (BEDROCK).

### `GET /v1/models` (MODELS)

OpenAI list shape plus per-model fields: `id`, `object`, `created`, `owned_by` (`router` for
provider-agnostic models and aliases), `display_name`, `context_length`, `context_window`,
`router`, `router_alias` (aliases only).

`router` object: `schema_version` (1), `request_name` (= id), `catalog_id`
(`provider:provider-model`), `provider_model`, `display_name`, `provider_display_name`, `aliases`
(e.g. provider-pinned names), `description`, `status` (`active` | `deprecated`), `surfaces`
(`responses`, `messages`, `systemone`, `decisions`), `listing` {`order`, `featured`, `snapshot`},
`limits` {`context_window`, `max_input_tokens`, `max_output_tokens`}, `capabilities`
{`modalities.input/output`, `tools.supported/parallel`, `structured_outputs`, `temperature`,
`prompt_caching`, `reasoning` {`supported`, `efforts[]`, `default_effort`, `per_turn_effort`,
`summary`, `continuation` {`request_include`, `required`}}, `verbosity`}, `pricing` (USD per 1M
tokens as decimal strings; `input`, `output`, `cache_read_input`, `cache_write_input`,
`cache_write_input_5m`, `cache_write_input_1h`, `supercache_*`, `long_context`,
`long_context_token_threshold`), `service_tiers[]` {`id`, `name`, `description`, `pricing`},
`providers[]` {`provider`, `provider_display_name`, `catalog_id`, `provider_model`, `pricing`},
`requires_byok`.

Capability booleans may be `null` = unknown; "treat it with caution" (MODELS). "A request to an
endpoint the model doesn't list [in `surfaces`] will fail" (MODELS).

---

## 4. Model naming and selection

- "Valid model IDs are account-specific. They come from `GET /v1/models`. Never invent one or reuse
  a provider's public model name." (CONNECT baseline prompt). `GET /v1/models` "is authoritative for
  the exact callable request IDs"; Supported Models labels "are not necessarily valid `model`
  values" (SUPPORTED).
- Catalog ids pin an exact model, e.g. `gpt-6-luna` (CHOOSE).
- Open-weight models: provider-agnostic `id` such as `kimi-k3` lets Router serve from any provider
  (with cross-provider fallback); provider-pinned aliases such as
  `accounts/fireworks/models/kimi-k3` appear in the row's `aliases` (CHOOSE, MODELS).
- Provider pin: `provider:model-id` (CHOOSE); `router.providers[].provider` gives the prefix
  (MODELS). Examples: `openai:gpt-5.4-mini`, `fireworks:accounts/fireworks/models/kimi-k3`,
  `anthropic:claude-fable-5` (FALLBACK, BENCH).
- Optional third segment = service tier: `openai:gpt-5.4-mini:flex` (CHOOSE, SPEND).
- Bedrock models have their own names: `us.openai.gpt-5.6-sol`,
  `global.anthropic.claude-sonnet-5-5` (`us.` = US cross-region, `global.` = global) (BEDROCK).
- `listing.snapshot` marks ids that pin a dated version, example given:
  `gpt-5-nano-2025-08-07` (MODELS).
- Aliases / benchmark aliases: `owned_by: "router"` with `router_alias` {`candidates`, floor
  `limits`}; no pricing, empty capabilities; don't hardcode aliases not in your catalog (BENCH,
  MODELS).
- Special model `switchyard` for coding agents (SWITCH).
- Supported Models table labels Anthropic models as `haiku-5-5`, `sonnet-5-5`, `opus-5-5`,
  `fable-5`, etc.; SPEND's fast-mode example uses `"model": "claude-opus-5-5"` (SUPPORTED, SPEND).
- How to pick (CHOOSE, MODELS, FALLBACK): read the catalog at runtime instead of hardcoding;
  filter by `surfaces`; check `capabilities` (tools, structured outputs, modalities, temperature,
  prompt caching, reasoning efforts); in a `models` list you are limited by the least capable
  candidate and the smallest context window/max output. Streaming, function tools, web search,
  JSON Schema output generally work across providers subject to model support. Modality table:
  file input "No Router upload API" for OpenAI/Anthropic/Google/xAI, Fireworks inline text only;
  audio only OpenAI/Google model-dependent; video only Google (CHOOSE).
- Google Gemini requires BYOK: `403 provider_key_required`; skipped in multi-model routing (FAQ,
  ERRORS).

### Routing strategies

- Cost-efficient (Flex) routing: "enabled by default", toggle on the Strategies page; applies only
  to an eligible direct `model` (not a `models` list) with no `service_tier`; picks standard vs
  Flex of the same model; can retry other tier on failure (FLEX).
- Fallbacks via `models`: advance on unknown/retired model, no permission, provider rate limit,
  provider 5xx, network failure, failure before streaming, previously failed candidate,
  non-streaming `incomplete` with `max_output_tokens`/no reason. Invalid requests, refusals,
  content filtering end immediately. Truncated attempts still billed (FALLBACK).
- Cross-provider routing: per-profile preference Cost only ... Throughput only (default Favor
  cost); session stickiness for caching; not applied when provider is pinned (XPROV).
- Switchyard: pick frontier + efficient models on Strategies page, "then select `switchyard` as the
  model in your coding agent"; "Without a frontier model, Switchyard runs as a toggle instead: it
  routes the model your coding agent already uses"; image input always uses frontier (SWITCH).
- Jev Auto Routing: 2-6 models with descriptions, opt-in; mutually exclusive with Switchyard;
  skipped on image input or `allow_flex_tier: true` (AUTO).
- Benchmark aliases (BENCH), Shadow models (mirror a % of `/v1/responses` traffic, needs content
  recording, not with BYOK) (SHADOW).

---

## 5. Errors and limits

### Error envelopes (ENDPOINT, ERRORS)

- `GET /v1/models`, `POST /v1/responses`, `POST /v1/decisions`: OpenAI envelope
  `{"error": {"message", "type", "param", "code"}}`.
- `POST /v1/messages`, `/count_tokens`: Anthropic envelope
  `{"type": "error", "error": {"type", "message"}}`.
- `POST /v1/systemone`: `{"detail": ...}`.
- After a stream starts: truncated event stream instead of an error body.

### Status codes (ERRORS, ENDPOINT)

| Status | Meaning (docs) |
| --- | --- |
| 400 | Invalid JSON, request shape, route selector, provider option; fix field in `param` |
| 401 | `invalid_api_key` (missing/unknown/expired/periodic spend-cap lock) or `api_key_deactivated` (disabled, lifetime cap) |
| 402 | `insufficient_credits` |
| 403 | Provider unavailable or `provider_key_required` (currently Google Gemini) |
| 404 | Model unavailable to this key (`model_not_found`) |
| 413 | Body > 100 MiB |
| 429 | Provider or gateway rate limit; respect `retry-after` |
| 500 | Router/gateway configuration failure |
| 501 | Provider cannot satisfy a requested capability (`not_implemented_error`); retrying unchanged won't help |
| 502 | Provider failure / all candidates failed (`provider_request_failed`, `all_candidates_failed`) |
| 503 | Credentials or pricing data temporarily unavailable |
| 504 | Provider request or stream timed out; also all `fallback_timeout` deadlines missed |

- Fallback failure message format: `<last provider message> (all N candidates failed:
  provider:model STATUS code; ...)` (ERRORS).
- Transport messages: `<PROVIDER> provider request failed.` / `... response could not be read.` /
  `... response was idle.` (ERRORS).

### Limits (ERRORS)

Request body 100 MiB; exactly one of `model`/`models`; up to 15 explicit candidates; 100 stored
keys per user; key list page 1-100, offsets <= 10,000; dashboard 90 days (analytics API 93);
no fallback after stream starts. "Context windows, tools, media support, tiers, and rate limits
vary by model." No numeric rate limits are published. Spend caps apply after usage is recorded,
so concurrent requests can overshoot (ERRORS).

---

## 6. What the docs say is unsupported

| Feature | Docs statement | Source |
| --- | --- | --- |
| Chat Completions | "There is no Chat Completions surface"; "Router doesn't serve OpenAI Chat Completions"; convert to Responses | CONNECT, ENDPOINT, FAQ, AGENTS |
| Embeddings, files, batches | "Router doesn't serve OpenAI Chat Completions, embeddings, files, or batches." | ENDPOINT |
| File upload | "No Router upload API" for most providers | CHOOSE |
| Images generation, audio endpoints, moderations, `GET /v1/models/{id}`, `GET /v1/responses/{id}` | Not mentioned (simply absent from endpoint list) | ENDPOINT |
| `store`, `previous_response_id`, conversations | Not mentioned anywhere | - |
| `stop`, `temperature` | `temperature` only as capability flag `capabilities.temperature`; `stop` never mentioned | MODELS |
| Router-only fields and `usage.cost` on Messages | "not currently supported on this endpoint" / "not currently included" | RESP |
| Decision models on responses/messages | returns 400 | RESP |
| Router-side response caching | "Router currently doesn't support caching responses on your behalf." | CACHE |
| Claude.ai Connectors | not supported | AGENTS |
| Bedrock: Converse API, non-OpenAI/Claude families, priority/flex, fast mode | not supported | BEDROCK |
| Anthropic service tiers | none on Router (use fast mode) | SPEND |
| LiteLLM | not documented at all | - |

---

## 7. Docs claim vs live-measured

| # | Live-measured fact | What the docs say (URL) | Verdict |
| --- | --- | --- | --- |
| 1 | Only `GET /v1/models`, `POST /v1/responses`, `POST /v1/messages` (+`count_tokens`) exist | Same four, **plus** `POST /v1/decisions` ("limited availability") and `POST /v1/systemone` (ENDPOINT, OVERVIEW, DECIDE). Baseline prompt targets only models + responses (+ messages) (CONNECT) | Conflict (docs list two extra endpoints; may be account-gated) |
| 2 | `chat/completions`, `embeddings`, `images`, `files`, `models/{id}`, `responses/{id}` retrieve -> 404/405 | Chat Completions, embeddings, files, batches "not served" (ENDPOINT). Images, `models/{id}`, `responses/{id}` not mentioned | Agree (chat/embeddings/files); not-mentioned (images, models/{id}, responses/{id}); exact status codes not documented |
| 3 | `store: true` -> 400 "must be false when using a shared gateway credential" | `store` never mentioned; RESP lists passthrough fields without `store` | Not mentioned |
| 4 | `previous_response_id` -> 400 on OpenAI models, silently ignored on others | Not mentioned. MODELS documents `reasoning.continuation.request_include` (`reasoning.encrypted_content`) implying stateless replay of reasoning | Not mentioned |
| 5 | Dated model ids -> 404 | MODELS documents `listing.snapshot` with example `gpt-5-nano-2025-08-07`, implying dated ids can be catalog entries; ERRORS: 404 = model unavailable to key; prompt: never reuse provider public names | Partial conflict (docs imply dated snapshot ids may be callable) |
| 6 | `stop` -> 501 on OpenAI models | `stop` not mentioned; general rule "capability the provider cannot satisfy returns 501" (RESP, ERRORS) | Agree in principle; `stop` not mentioned |
| 7 | `temperature` -> 400 when model capability `temperature: false` | MODELS exposes `capabilities.temperature`; docs say unsatisfiable capabilities return **501** (RESP, ERRORS) | Conflict on status code (docs imply 501, measured 400); otherwise not mentioned |
| 8 | Flex tier applied by default (account `allow_flex_tier_default` true) | "Cost-efficient routing is enabled by default" (FLEX); `allow_flex_tier` overrides profile default (RESP). Baseline prompt says `allow_flex_tier` "returns 400 for ineligible models" | Agree (FLEX/RESP); baseline prompt's 400 claim conflicts with RESP/CONNECT ("ignores the opt-in") |
| 9 | Claude via Responses needs `prompt_cache_key` for caching | CACHE: provide `prompt_cache_key` to improve provider affinity; RESP: Messages gives "more precise control over caching for Claude models"; never states caching is off without it | Partial / not explicitly mentioned |
| 10 | Messages responses have no `usage.cost` | "`usage.cost` is not currently included in Messages responses" (RESP) | Agree |
| 11 | Python `urllib` default User-Agent blocked by Cloudflare (error 1010) | Not mentioned | Not mentioned |
| 12 | LangChain needs `use_responses_api` | "Keep `use_responses_api=True`" (Python), `useResponsesApi: true` (TS) (CONNECT) | Agree |
| 13 | Vercel AI SDK default `router(model)` uses Responses | Docs always use explicit `router.responses(...)`; never discuss the default `router(model)` call (CONNECT) | Not mentioned (docs consistent with using Responses) |
| 14 | Switchyard only activates when `model="switchyard"` | "select `switchyard` as the model in your coding agent" (agree), but also "Without a frontier model, Switchyard runs as a toggle instead: it routes the model your coding agent already uses" (SWITCH) | Partial conflict (toggle mode implies it can act on other model names) |

Additional internal doc inconsistencies worth noting for the tool:

- `allow_flex_tier` on ineligible model: prompt says 400 (CONNECT prompt) vs ignored (RESP, CONNECT body, FLEX).
- `models` via `extra_body` (prompt) vs "none of them can send the mutually exclusive `models`
  field" (CONNECT body).
- Prompt omits `fallback_timeout` and the no-`/v1` Anthropic base URL (RESP).
- RESP typo: "`fallback_timeout` is only applicable for streaming requests that include one more
  than candidate model".
