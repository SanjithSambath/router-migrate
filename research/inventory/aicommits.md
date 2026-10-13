## Nutlope/aicommits @ a414dd43b6

- **Lang:** TypeScript CLI (ESM, bundled by pkgroll). **Pkg manager:** pnpm (CI pnpm v9). **Lockfiles:** pnpm-lock.yaml, plus a stray package-lock.json. **Python:** n/a. **Node:** .nvmrc `v22.14.0` (CI `node-version-file: .nvmrc`). No engines field.
- **No longer raw https.** It uses the Vercel AI SDK: `ai` 7.0.32, `@ai-sdk/openai` 4.0.16, `@ai-sdk/openai-compatible` 3.0.13 and `@ai-sdk/togetherai` 3.0.14, all as devDependencies bundled into dist. There is no src/utils/openai.ts. The `proxy` config key is deprecated and ignored.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| src/feature/providers/base.ts:189 | vercel-ai-sdk | `createOpenAI({apiKey})(model)`, which means the **Responses API** | none (implicit env OPENAI_BASE_URL, else api.openai.com/v1) | config OPENAI_API_KEY (~/.aicommits ini) |
| src/feature/providers/base.ts:193 | vercel-ai-sdk | `createTogetherAI({apiKey, baseURL})` | hardcoded https://api.together.xyz/v1 | config OPENAI_API_KEY |
| src/feature/providers/base.ts:196 | vercel-ai-sdk | `createOpenAICompatible({name, apiKey, baseURL, headers})` | per-provider hardcoded (groq, xai, openrouter, ollama localhost:11434, lmstudio localhost:1234) or config OPENAI_BASE_URL for `custom` | config OPENAI_API_KEY |
| src/commands/pr.ts:181 | vercel-ai-sdk | `createOpenAI({apiKey})` | none | config OPENAI_API_KEY |
| src/commands/pr.ts:182 | vercel-ai-sdk | `createOpenAICompatible({name:'custom'})` | provider baseUrl/OPENAI_BASE_URL (+`/v1` appended) | config OPENAI_API_KEY |

### Prod call sites (5: 4 AI SDK, 1 raw HTTP)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| src/feature/generate-commit-message.ts:598 | vercel-ai-sdk | ai.generateText (one-shot / chunked fallback; maxOutputTokens, timeout, reasoning + providerOptions) | N | Y | config OPENAI_MODEL or provider default |
| src/feature/generate-commit-message.ts:872 | vercel-ai-sdk | `ToolLoopAgent.stream` (≈ai.streamText; agent built at :819; 3 tools, toolChoice required, prepareStep forced tools, stopWhen hasToolCall/stepCountIs) | Y | Y | config OPENAI_MODEL |
| src/commands/pr.ts:196 | vercel-ai-sdk | ai.generateText (PR title) | N | Y | config OPENAI_MODEL |
| src/commands/pr.ts:207 | vercel-ai-sdk | ai.generateText (PR body) | N | Y | config OPENAI_MODEL |
| src/feature/models.ts:100 | raw-http-openai | models.list (GET {baseUrl}/models, Together `/models?serverless=true`) | N | Y | n/a |

Wrapper: `generateCommitMessage()` (generate-commit-message.ts:518) is called from src/commands/aicommits.ts:132 and src/commands/prepare-commit-msg-hook.ts:71. The model comes from `Provider.getGenerationModel()`.

### Non-prod call sites (scripts)
- scripts/test-together-tool-compatibility.ts:163 `ToolLoopAgent.stream`, :164 `ToolLoopAgent.generate`, :92 GET Together /models
- scripts/benchmark-together-models.ts:341 calls the generateCommitMessage wrapper; :188 calls `legacy.generate` from an old aicommits build in a git worktree

### Models seen
- OpenAI: gpt-5.6-luna/terra/sol (gpt-5, -mini, -nano get reasoningEffort minimal)
- Together (default provider): deepseek-ai/DeepSeek-V4-Flash-0731 (fallback), zai-org/GLM-5.3-Flash, moonshotai/Kimi-K3, MiniMaxAI/MiniMax-M2.7
- Groq: llama-3.1-8b-instant, openai/gpt-oss-20b/120b, llama-3.3-70b-versatile
- xAI: grok-4.5, grok-build-0.1, grok-4.3
- Ollama: qwen3.5:4b, llama3.2:latest
- LM Studio: qwen/qwen3-4b-2507, qwen/qwen3-8b
- OpenRouter: openrouter/free, openrouter/auto

### Tests
- **Command:** CI runs `pnpm type-check && pnpm build && pnpm test`, where `test` = `tsx tests`. The CLI specs exec dist/cli.mjs, so a build is required first. **Framework:** manten (+ fs-fixture, execa). CI runs on ubuntu and windows.
- **Needs network:** partial. tests/specs/cli/commits.ts:17 and tests/specs/git-hook.ts:78 hit the real OpenAI API but self-skip without `OPENAI_API_KEY`. CI only exports `OPENAI_KEY`, so in practice they are skipped. Everything else is offline.
- **Mocking:**
  - Dependency injection of `MockLanguageModelV4` + `simulateReadableStream` from `ai/test` (tests/specs/generate-commit-message.ts:3, ~30 instances such as :131). Tests assert on `model.doStreamCalls[0].providerOptions`.
  - Local HTTP fake: tests/specs/cli/headless.ts:5 `startAgentServer()` returns OpenAI `chat.completion` JSON. It is wired up with ~/.aicommits `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`, which selects the custom provider and therefore openai-compatible.
  - `globalThis.fetch` monkeypatch in tests/specs/providers.ts:32 and auto-update.ts.

### Unusual / migration-relevant
- The provider abstraction is a ProviderDef table. The provider is auto-detected from the config `OPENAI_BASE_URL` (src/utils/config-runtime.ts:81-92), and the OPENAI_* key names are reused for every provider.
- App config is read only from the ~/.aicommits ini file and CLI flags; process.env OPENAI_* is not read. The exception is @ai-sdk/openai, which reads env OPENAI_BASE_URL implicitly.
- The OpenAI provider uses `/v1/responses` (the default for `createOpenAI()(id)`). All other providers use `/chat/completions`. A router has to support both, or the code must switch to `.chat(id)`.
- It depends heavily on tool calling: forced `toolChoice: {type:'tool'}` and `required`, plus parsing of staticToolCalls. Provider-specific options are `openai.reasoningEffort`, `xai.reasoningEffort` and `togetherai.reasoning.enabled=false`, together with the generic `reasoning` option.
- On failure it falls back to another model and persists that model to config. Timeout budgets are 10s remote and 60s local/Together.
- OpenRouter sends HTTP-Referer and X-Title headers.
