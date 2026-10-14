## vercel/ai-chatbot @ c2f8235e1f

- **Lang:** TypeScript (Next.js 16.2.10, React 19). **Pkg manager:** pnpm (`packageManager: pnpm@10.32.1`). **Lockfile:** pnpm-lock.yaml. **Python:** n/a (py3.9 verdict n/a). **Node:** no engines/.nvmrc; CI `actions/setup-node` `lts/*`; @types/node ^24.
- **SDK:** `ai` 7.0.15 (gateway provider built in, `@ai-sdk/gateway` 4.0.12 in lock), `@ai-sdk/react` 4.0.16, `@ai-sdk/provider` ^4, `@ai-sdk/otel`. No @ai-sdk/openai or xai packages; every model goes through Vercel AI Gateway.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| lib/ai/providers.ts:25 | vercel-ai-sdk | `gateway.languageModel(modelId)` (getLanguageModel) | none (gateway default) | env AI_GATEWAY_API_KEY (implicit) / Vercel OIDC |
| lib/ai/providers.ts:32 | vercel-ai-sdk | `gateway.languageModel(titleModel.id)` (getTitleModel) | none | same |
| lib/ai/providers.ts:11 (test) | vercel-ai-sdk | `customProvider({languageModels:{'chat-model','title-model'}})` with mocks | n/a | n/a |

### Prod call sites (12: 9 AI SDK, 3 raw gateway HTTP)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| app/(chat)/api/chat/route.ts:269 | vercel-ai-sdk | ai.streamText (5 tools, stopWhen isStepCount(5), providerOptions.gateway.order + openai.reasoningEffort) | Y | Y | user_input (selectedChatModel allowlisted, else DEFAULT_CHAT_MODEL) |
| app/(chat)/actions.ts:28 | vercel-ai-sdk | ai.generateText (title) | N | Y | hardcoded 'moonshotai/kimi-k2.5' |
| artifacts/code/server.ts:18, :40 | vercel-ai-sdk | ai.streamText (create/update) | Y | Y | param |
| artifacts/sheet/server.ts:11, :33 | vercel-ai-sdk | ai.streamText | Y | Y | param |
| artifacts/text/server.ts:11, :35 | vercel-ai-sdk | ai.streamText + smoothStream | Y | Y | param |
| lib/ai/tools/request-suggestions.ts:42 | vercel-ai-sdk | ai.streamText with `output: Output.array(zod)` (partialOutputStream; structured output, replaces streamObject) | Y | Y | param |
| lib/ai/models.ts:72 | raw http (ai-gateway.vercel.sh) | GET /v1/models/{id}/endpoints (capabilities) | N | Y | config chatModels |
| lib/ai/models.ts:126 | raw http | GET /v1/models (models.list) | N | Y | n/a |
| lib/ai/models.ts:210 | raw http | GET /v1/models/{id}/endpoints (health) | N | Y | user_input |

Wrappers: `getLanguageModel()`/`getTitleModel()` in lib/ai/providers.ts. Artifact handlers are invoked through tools createDocument/updateDocument. No generateObject, streamObject, embed or generateImage calls.

### Non-prod call sites
None. Tests never call the SDK directly; they drive the app through Playwright.

### Models seen
moonshotai/kimi-k2.5 (default + title), deepseek/deepseek-v3.2, openai/gpt-oss-20b, openai/gpt-oss-120b, xai/grok-4.1-fast-non-reasoning (lib/ai/models.ts:26-64). Gateway upstream order hints: fireworks, bedrock, deepinfra, groq, xai.

### Tests
- **Command:** `pnpm test` = `export PLAYWRIGHT=True && pnpm exec playwright test` (CI .github/workflows/playwright.yml). **Framework:** Playwright e2e (tests/e2e/{api,auth,chat,model-selector}.test.ts, 23 tests). webServer is `pnpm dev`.
- **Needs network:** partial. The LLM is mocked, but CI still needs real POSTGRES_URL, REDIS_URL, BLOB_READ_WRITE_TOKEN and AUTH_SECRET secrets. `getCapabilities()` also fetches https://ai-gateway.vercel.sh without auth on every chat POST.
- **Mocking:** an environment switch, not patching. `isTestEnvironment` (lib/constants.ts:5, PLAYWRIGHT / PLAYWRIGHT_TEST_BASE_URL / CI_PLAYWRIGHT) makes lib/ai/providers.ts:10 `require("./models.mock")` and wrap the mocks in `customProvider`.
  - lib/ai/models.mock.ts:31/76 holds hand-rolled `LanguageModel` v3 object literals. Their doStream emits text-start/delta/end/finish through a ReadableStream, with canned replies chosen by keyword.
  - lib/ai/models.test.ts:27 has `MockLanguageModelV3` + `simulateReadableStream` versions (from `ai/test`). Nothing imports it, so it is dead code.
  - Caveat: callers pass real ids (e.g. 'moonshotai/kimi-k2.5') to `myProvider.languageModel()`, but the mock provider only registers 'chat-model' and 'title-model'.

### Unusual / migration-relevant
- All traffic goes through the AI Gateway provider. `providerOptions.gateway.order` is gateway-specific routing that a router migration must replace or drop. `providerOptions.openai.reasoningEffort` is set for gpt-oss.
- Hardcoded `https://ai-gateway.vercel.sh/v1/models...` fetches (capabilities, availability, model list) are part of the request path.
- Uses AI SDK v7 names: `instructions`, `isStepCount`, the `telemetry` option, `Output.array`, `createUIMessageStream`, resumable-stream.
- Tool calling with multi-step loops. Streaming is consumed both as `result.stream` text-delta parts and as `toUIMessageStream`.
