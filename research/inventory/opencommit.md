## di-sukharev/opencommit @ a84b7cc597

- **Lang:** TypeScript CLI + GitHub Action (esbuild bundle to out/cli.cjs, which is checked in). **Pkg manager:** npm. **Lockfile:** package-lock.json (v3). **Python:** n/a. **Node:** no engines/.nvmrc; CI `node-version: '20.x'`; @types/node ^16.
- **SDKs (lock versions):** openai 4.57.0, @anthropic-ai/sdk 0.19.2, @azure/openai 1.0.0-beta.12, @google/generative-ai 0.24.1, @mistralai/mistralai 1.3.5, axios 1.9.0, https-proxy-agent ^8.

### Engine factory
`getEngine()` in src/utils/engine.ts:21 switches on `OCO_AI_PROVIDER` (default `openai`). It builds `{model: OCO_MODEL, baseURL: OCO_API_URL, apiKey: OCO_API_KEY, proxy: OCO_PROXY|HTTPS_PROXY|HTTP_PROXY, customHeaders: OCO_API_CUSTOM_HEADERS, maxTokensInput/Output, isReasoning}`. Config comes from env (+ .env via dotenv) and overrides the ~/.opencommit ini file.

Callers of `engine.generateCommitMessage(messages)`: src/generateCommitMessageFromGitDiff.ts:215, :290, :372 and src/modules/commitlint/config.ts:69.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| src/engine/openAi.ts:56 | openai-node | `new OpenAI({apiKey, baseURL?, httpAgent: HttpsProxyAgent?, defaultHeaders?, fetch})` | OCO_API_URL, else SDK default (implicit OPENAI_BASE_URL / api.openai.com) | OCO_API_KEY |
| src/engine/groq.ts:7 | openai-node (subclass) | GroqEngine | hardcoded https://api.groq.com/openai/v1 (overwrites OCO_API_URL) | OCO_API_KEY |
| src/engine/deepseek.ts:24 | openai-node (subclass) | DeepseekEngine `super({baseURL: deepseek, ...config})` | https://api.deepseek.com/v1, but see bug note | OCO_API_KEY |
| src/engine/orcarouter.ts:11 | openai-node (subclass) | OrcaRouterEngine | OCO_API_URL ?? https://api.orcarouter.ai/v1 | OCO_API_KEY |
| src/engine/anthropic.ts:53 | anthropic-ts | `new AnthropicClient({apiKey, baseURL?, httpAgent?})` | OCO_API_URL | OCO_API_KEY |
| src/engine/azure.ts:23 | azure-openai | `new OpenAIClient(endpoint, new AzureKeyCredential(key))` | OCO_API_URL (endpoint) | OCO_API_KEY |
| src/engine/gemini.ts:75 | google-genai (legacy @google/generative-ai) | `new GoogleGenerativeAI(apiKey)`; `getGenerativeModel(..., {baseUrl: OCO_API_URL})` at :87/:93 | OCO_API_URL | OCO_API_KEY |
| src/engine/mistral.ts:21 | mistral | `new Mistral({apiKey, serverURL?})` | OCO_API_URL | OCO_API_KEY |
| src/engine/openrouter.ts:13 | raw-http-openai (axios) | axios.create | hardcoded https://openrouter.ai/api/v1/chat/completions | Bearer OCO_API_KEY |
| src/engine/aimlapi.ts:12 | raw-http-openai (axios) | axios.create | OCO_API_URL \|\| https://api.aimlapi.com/v1/chat/completions | Bearer OCO_API_KEY |
| src/engine/flowise.ts:15 | raw http (axios) | axios.create({url: `${baseURL}/${apiKey}`}) | OCO_API_URL | key used as URL path segment |
| src/engine/ollama.ts:31 | raw http (axios) | axios.create | OCO_API_URL \|\| http://localhost:11434 | none (custom headers) |
| src/engine/mlx.ts:23 | raw-http-openai (axios) | axios.create | OCO_API_URL \|\| http://localhost:8080 | none |
| src/engine/llamacpp.ts:28 | raw-http-openai (axios) | axios.create | OCO_API_URL \|\| default | none (custom headers) |

### Prod call sites (13: 12 completion sites + 1 model-list fetch)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| src/engine/openAi.ts:95 | openai-node | chat.completions.create (openai, groq, orcarouter; max_completion_tokens for o*/gpt-5) | N | Y | config OCO_MODEL |
| src/engine/deepseek.ts:58 | openai-node | chat.completions.create (+ non-standard `thinking:{type:'disabled'}`) | N | Y | config OCO_MODEL |
| src/engine/anthropic.ts:98 | anthropic-ts | messages.create (claude-haiku-5-5 special-case: thinking disabled, stop_reason checks) | N | Y | config OCO_MODEL |
| src/engine/azure.ts:44 | azure-openai | getChatCompletions(deployment=OCO_MODEL), equivalent to chat.completions.create | N | Y | config OCO_MODEL |
| src/engine/gemini.ts:108 | google-genai (legacy) | generateContent (safetySettings, temp 0) | N | Y | config OCO_MODEL |
| src/engine/mistral.ts:51 | mistral | chat.complete | N | Y | config OCO_MODEL |
| src/engine/openrouter.ts:28 | raw-http-openai | POST /api/v1/chat/completions | N | Y | config OCO_MODEL |
| src/engine/aimlapi.ts:28 | raw-http-openai | POST /v1/chat/completions | N | Y | config OCO_MODEL |
| src/engine/flowise.ts:39 | raw http (Flowise) | POST {OCO_API_URL}/{OCO_API_KEY} | N | Y | config OCO_MODEL (unused in payload) |
| src/engine/ollama.ts:47 | raw http (Ollama native) | POST /api/chat (stream:false, think) | N | Y | config OCO_MODEL ?? 'mistral' |
| src/engine/mlx.ts:39 | raw-http-openai | POST /v1/chat/completions (no `model` sent) | N | Y | none |
| src/engine/llamacpp.ts:43 | raw-http-openai | POST /v1/chat/completions | N | Y | config OCO_MODEL |
| src/utils/modelCache.ts:76 | raw http | models.list wrapper for 9 providers (openai, anthropic, mistral, groq, openrouter, deepseek, orcarouter `/v1/models`; ollama `/api/tags`; llamacpp `/v1/models`) | N | Y | n/a |

Also in src: src/engine/testAi.ts is a stub engine (`OCO_AI_PROVIDER=test`) that returns canned strings and makes no API call. src/version.ts:12 queries the npm registry, which is not an LLM call.

### Non-prod call sites
None directly. Tests stub the client methods named above.

### Models seen
- Defaults per provider (MODEL_LIST[...][0], src/commands/config.ts:588):

  | provider | default model |
  |---|---|
  | openai | gpt-4o-mini |
  | anthropic | claude-sonnet-4-6 |
  | gemini | gemini-1.5-flash |
  | groq | llama3-70b-8192 |
  | mistral | ministral-3b-2410 |
  | deepseek | deepseek-v4-flash |
  | aimlapi | openai/gpt-4o |
  | openrouter | openai/gpt-4o-mini |
  | orcarouter | orcarouter/auto |
  | ollama, mlx, llamacpp | '' (empty) |

- Hardcoded special case: claude-haiku-5-5.
- MODEL_LIST (src/commands/config.ts:48) holds about 500 ids in total: openrouter 324, aimlapi 100, mistral 39, openai 26, groq 7, anthropic 5, gemini 5, deepseek 2, orcarouter 1.

### Tests
- **Command (CI .github/workflows/test.yml):**
  - Linux job: `npm run test:unit` (`NODE_OPTIONS=--experimental-vm-modules jest test/unit`), then `npm run test:e2e:core` (build + jest on 6 e2e files), then `npm run test:e2e:prompt-module`.
  - macOS job: `npm run test:e2e:smoke` (--help/--version only).
  - `npm test` = jest test/unit.
- **Framework:** jest 29 + ts-jest ESM preset; cli-testing-library for e2e. Docker variants are under test/Dockerfile.
- **Needs network:** no. No real keys are needed: dummy `OCO_API_KEY` values point at local mock servers.
- **Mocking:**
  - Unit tests (spying on the SDK objects of a real engine instance):
    - openai: `jest.spyOn(engine.client.chat.completions,'create')` in test/unit/openAi.test.ts:27, deepseek.test.ts:23 and orcarouter.test.ts:28.
    - anthropic: `jest.spyOn(engine.client.messages,'create')` (anthropic.test.ts:25).
    - gemini: `engine.client.getGenerativeModel = jest.fn()` (gemini.test.ts:45).
    - ollama and llamacpp: `engine.client = {post: jest.fn()}` (ollama.test.ts:21, llamacpp.test.ts:24).
    - fetch-based code: `global.fetch` replaced (modelCache.test.ts:26, version.test.ts).
    - setup.test.ts:42-68 uses `jest.unstable_mockModule` for @clack/prompts, cleye, commands/config and utils/modelCache.
  - E2E: the built CLI runs against node:http mock servers in test/e2e/utils.ts: `startMockOpenAiServer` :422, `startMockAnthropicServer` :542 (POST /v1/messages, records x-api-key) and `startMockGeminiServer` :601 (x-goog-api-key). They are wired up through `OCO_API_URL` + `OCO_AI_PROVIDER` + dummy `OCO_API_KEY` (getMockOpenAiEnv :270).
  - The prompt-module e2e uses `OCO_AI_PROVIDER: 'test'` + `OCO_TEST_MOCK_TYPE` (test/e2e/prompt-module/commitlint.test.ts:27), which routes to the TestAi stub.

### Unusual / migration-relevant
- Wide provider abstraction (15 providers) behind one interface that takes OpenAI-format messages. There is one generic `OCO_API_URL`/`OCO_API_KEY`/`OCO_MODEL`. openai, groq, deepseek, orcarouter, aimlapi, mlx and llamacpp are OpenAI-compatible; anthropic, gemini, mistral, azure, ollama (native /api/chat) and flowise are not.
- Proxy support: `HttpsProxyAgent` is passed as `httpAgent` to the openai and anthropic SDKs and set on `axios.defaults` (src/utils/proxy.ts). Custom headers are supported.
- Likely bug: `getEngine` always passes a `baseURL` key, which is undefined when OCO_API_URL is unset. `DeepseekEngine`'s `{baseURL: deepseek, ...config}` therefore resets it to undefined, and openai-node falls back to OPENAI_BASE_URL or api.openai.com. The unit test omits the key, so it never catches this.
- OpenRouter ignores OCO_API_URL. AIMLAPI treats OCO_API_URL as the full chat/completions URL. MLX sends no model.
- No streaming, tools or structured outputs; every call is a single non-streaming completion. `<think>` tags are stripped. Reasoning models are detected by regex `/^(o[1-9]|gpt-5)/`, or set explicitly with OCO_REASONING.
- Old or deprecated SDKs: openai v4.57, anthropic 0.19, @azure/openai beta, legacy @google/generative-ai.
