## mckaywrigley/chatbot-ui @ 81328b61d2

- **Lang**: TypeScript (Next.js 14.1 App Router) + SQL (Supabase migrations)
- **Pkg manager**: npm. **Lockfiles**: `package-lock.json`, `__tests__/playwright-test/package-lock.json`
- **Python**: n/a (no Python code) -> python_requires null, py3.9 n/a
- **Node**: 20.11.0 (`.nvmrc`)
- Key locked versions: `ai` 2.2.31, `openai` 4.24.7, `@anthropic-ai/sdk` 0.18.0, `@google/generative-ai` 0.11.4, `langchain` 0.0.213 (only loaders/splitters), `@azure/openai` 1.0.0-beta.10 and `@mistralai/mistralai` 0.0.8 (both declared, never imported).

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| app/api/chat/openai/route.ts:22 | openai-node | `new OpenAI({apiKey, organization})` | none | profile.openai_api_key (env OPENAI_API_KEY overrides) |
| app/api/chat/azure/route.ts:47 | openai-node | `new OpenAI({apiKey, baseURL, defaultQuery api-version, defaultHeaders api-key})` | `${azure_openai_endpoint}/openai/deployments/${id}` (profile/env) | profile.azure_openai_api_key / env |
| app/api/chat/custom/route.ts:35 | openai-node | `new OpenAI({apiKey, baseURL})` | user-supplied Supabase `models.base_url` | Supabase `models.api_key` |
| app/api/chat/anthropic/route.ts:58 | anthropic-ts | `new Anthropic({apiKey})` | none | profile.anthropic_api_key / env |
| app/api/chat/google/route.ts:19 | other:@google/generative-ai | `new GoogleGenerativeAI(key)` | none | profile.google_gemini_api_key / env |
| app/api/chat/groq/route.ts:21 | openai-node | `new OpenAI({apiKey, baseURL})` | hardcoded `https://api.groq.com/openai/v1` | profile.groq_api_key / env |
| app/api/chat/mistral/route.ts:22 | openai-node | same | hardcoded `https://api.mistral.ai/v1` | profile.mistral_api_key / env |
| app/api/chat/openrouter/route.ts:22 | openai-node | same | hardcoded `https://openrouter.ai/api/v1` | profile.openrouter_api_key / env |
| app/api/chat/perplexity/route.ts:21 | openai-node | same | hardcoded `https://api.perplexity.ai/` | profile.perplexity_api_key / env |
| app/api/chat/tools/route.ts:22, app/api/command/route.ts:18, app/api/assistants/openai/route.ts:13 | openai-node | `new OpenAI({apiKey, organization})` | none | profile.openai_api_key / env |
| app/api/retrieval/process/route.ts:104/111, process/docx/route.ts:51/58, retrieve/route.ts:38/45 | openai-node | Azure-branch `new OpenAI({...deployments/embeddings_id})` else `new OpenAI({apiKey, organization})` | Azure endpoint or none | profile azure/openai key / env |
| components/chat/chat-helpers/index.ts:257 | ollama | browser `fetch` | env NEXT_PUBLIC_OLLAMA_URL | none |

### Prod call sites (19)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| app/api/chat/openai/route.ts:27 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/chat/azure/route.ts:54 | openai-node (Azure) | chat.completions.create | yes | yes | config: deployment id from profile/env |
| app/api/chat/custom/route.ts:40 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/chat/anthropic/route.ts:63 | anthropic-ts | messages.create (stream:true) | yes | yes | user_input |
| app/api/chat/google/route.ts:31 | other:@google/generative-ai | startChat().sendMessageStream | yes | yes | user_input |
| app/api/chat/groq/route.ts:26 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/chat/mistral/route.ts:27 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/chat/openrouter/route.ts:27 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/chat/perplexity/route.ts:26 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/chat/tools/route.ts:62 | openai-node | chat.completions.create (tools) | no | yes | user_input |
| app/api/chat/tools/route.ts:201 | openai-node | chat.completions.create | yes | yes | user_input |
| app/api/command/route.ts:23 | openai-node | chat.completions.create | no | yes | hardcoded `gpt-4-1106-preview` |
| app/api/assistants/openai/route.ts:18 | openai-node | beta.assistants.list | no | yes | n/a (no UI caller) |
| app/api/retrieval/process/route.ts:118 | openai-node | embeddings.create | no | yes | hardcoded `text-embedding-3-small` |
| app/api/retrieval/process/docx/route.ts:65 | openai-node | embeddings.create | no | yes | hardcoded `text-embedding-3-small` |
| app/api/retrieval/retrieve/route.ts:52 | openai-node | embeddings.create | no | yes | hardcoded `text-embedding-3-small` |
| components/chat/chat-helpers/index.ts:162 | ollama | http:POST {OLLAMA}/api/chat (browser) | yes | yes | user_input |
| lib/models/fetch-models.ts:57 | ollama | http:GET {OLLAMA}/api/tags (models list, browser) | no | yes | n/a |
| lib/models/fetch-models.ts:84 | raw-http-openai | models.list via GET openrouter.ai/api/v1/models (browser, unauth) | no | yes | n/a |

By sdk/surface: openai-node chat.completions.create 11 (8 streamed), embeddings.create 3, beta.assistants.list 1; anthropic-ts messages.create 1; @google/generative-ai 1; Ollama raw HTTP 2 (chat + tags); OpenRouter models raw HTTP 1.
Wrapper: `fetchChatResponse` (chat-helpers/index.ts:249) does the browser fetch for both Ollama and the internal `/api/chat/*` routes.

### Non-prod call sites
None (no examples/scripts/evals; tests do not touch LLM code).

### Models seen
OpenAI: gpt-4o, gpt-4-turbo-preview, gpt-4-vision-preview, gpt-4, gpt-3.5-turbo, gpt-4-1106-preview, text-embedding-3-small. Anthropic: claude-2.1, claude-instant-1.2, claude-3-haiku-20240307, claude-3-sonnet-20240229, claude-3-opus-20240229, claude-3-5-sonnet-20240620. Google: gemini-1.5-flash, gemini-1.5-pro-latest, gemini-pro, gemini-pro-vision. Mistral: mistral-tiny/small-latest/medium-latest/large-latest. Groq: llama3-8b-8192, llama3-70b-8192, mixtral-8x7b-32768, gemma-7b-it. Perplexity: mixtral-8x7b-instruct, mistral-7b-instruct, codellama-70b-instruct, sonar-{small,medium}-{chat,online}. Local: Xenova/all-MiniLM-L6-v2. Plus dynamic OpenRouter/Ollama/custom model ids. Source: `lib/models/llm/*-llm-list.ts`.

### Tests
- Command: `npm test` -> `jest` (next/jest, jsdom). Only `__tests__/lib/openapi-conversion.test.ts` (pure function `openapiToFunctions`).
- Separate Playwright package `__tests__/playwright-test` (`npm run integration` = `playwright test`): `tests/login.spec.ts` drives a running app at http://localhost:3000 login page (needs Supabase), no LLM calls. Not wired into any CI (no `.github/workflows`).
- Needs network: no (jest). Mocking: none - no LLM client is exercised or mocked anywhere.

### Unusual / migration notes
- One route per provider; provider chosen client-side (`components/chat/chat-helpers/index.ts:204-219`, openai -> azure when `profile.use_azure_openai`). Chat routes run on the **edge** runtime; tools and retrieval routes on node.
- Legacy **ai@2.2.31** `OpenAIStream` / `AnthropicStream` / `StreamingTextResponse` (removed in ai v4) wrap SDK streams into plain-text responses.
- Groq, Mistral, OpenRouter, Perplexity: openai-node with hardcoded baseURLs. Azure: plain OpenAI client with deployment baseURL + `api-version=2023-12-01-preview` + `api-key` header. `@azure/openai` and `@mistralai/mistralai` unused.
- Custom provider: arbitrary user base_url/api_key from Supabase `models` table (service-role key).
- BYOK: keys stored on Supabase `profiles`; server env vars (VALID_ENV_KEYS) override for all users (`lib/server/server-chat-helpers.ts:40-67`).
- Anthropic SDK 0.18 `messages.create({stream:true})`; OpenAI image_url data URLs converted to base64 image blocks; `max_tokens` from `CHAT_SETTING_LIMITS`.
- Google uses deprecated `@google/generative-ai` (startChat/sendMessageStream), manual ReadableStream.
- Tool calling: OpenAPI schema -> OpenAI function tools; server executes the HTTP tool calls then streams a second completion.
- Embeddings (text-embedding-3-small, OpenAI or Azure) feed Supabase pgvector RPCs; alternative local @xenova/transformers embeddings.
- Browser-side direct calls: Ollama `/api/chat` + `/api/tags`, OpenRouter `/api/v1/models`.
- Assistants: only `beta.assistants.list`, with no UI caller found.
- `.env.local.example` has `NEXT_PUBLIC_OPENAI_ORGANIZATION_ID` but code reads `OPENAI_ORGANIZATION_ID`.
