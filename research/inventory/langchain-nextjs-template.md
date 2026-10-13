## langchain-ai/langchain-nextjs-template @ fd4f2cd74e

- **Lang:** TypeScript (Next ^16.3.8, React 19, LangChain.js v1: `@langchain/openai` ^1.5.8, `@langchain/core` ^1.2.8, `@langchain/langgraph` ^1.4.10, `@langchain/classic`, `langchain` ^1.5.9)
- **Package manager:** yarn berry 3.5.1 (`packageManager: yarn@3.5.1`, `.yarnrc.yml` yarnPath, nodeLinker node-modules)
- **Lockfile:** `yarn.lock`. It resolves `@langchain/openai` 1.5.8 directly and 1.5.13 via @langchain/classic; openai 6.49.0 and 7.15.0 are both present.
- **Python:** n/a
- **Node:** `engines.node >=22`. The README requires Node 22.6+ for the test command (`--experimental-strip-types`). There is no .nvmrc and no CI (`.github/` holds only dependabot.yml).
- **Commands:**
  - test: `node --experimental-strip-types --test utils/integrations.test.mjs` (from README:104; not a package.json script)
  - lint: `yarn lint` (eslint .)
  - build: `yarn build` (next build --webpack)
  - typecheck: `yarn tsc --noEmit` (typescript 7.0.2, strict)
  - format: `yarn format`

### Clients (8 ChatOpenAI + 3 OpenAIEmbeddings)
No `useResponsesApi`, `configuration`, `baseURL` or `apiKey` is set anywhere. The key comes implicitly from env OPENAI_API_KEY; the underlying openai SDK also implicitly honors OPENAI_BASE_URL.

| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| app/api/chat/route.ts:51 | langchain-js | ChatOpenAI({temperature:0.8, model:'gpt-4o-mini'}) | none | env (implicit) |
| app/api/chat/agents/route.ts:74 | langchain-js | ChatOpenAI({model:'gpt-4o-mini', temperature:0}) | none | env |
| app/api/chat/retrieval/route.ts:80 | langchain-js | ChatOpenAI({model:'gpt-4o-mini', temperature:0.2}) | none | env |
| app/api/chat/retrieval/route.ts:89 | langchain-js | OpenAIEmbeddings() (default model) | none | env |
| app/api/chat/retrieval_agents/route.ts:76 | langchain-js | ChatOpenAI({model:'gpt-4o-mini', temperature:0.2}) | none | env |
| app/api/chat/retrieval_agents/route.ts:85 | langchain-js | OpenAIEmbeddings() | none | env |
| app/api/chat/structured_output/route.ts:34 | langchain-js | ChatOpenAI({temperature:0.8, model:'gpt-4o-mini'}) | none | env |
| app/api/retrieval/ingest/route.ts:49 | langchain-js | OpenAIEmbeddings() | none | env |
| app/langgraph/agent/agent.ts:9 | langchain-js | ChatOpenAI({model:'gpt-4o-mini', temperature:0}) (module scope) | none | env |
| app/ai_sdk/agent/action.ts:23 | langchain-js | ChatOpenAI({model:'gpt-4o-mini', temperature:0}) | none | env |
| app/ai_sdk/tools/action.ts:38 | langchain-js | ChatOpenAI({model:'gpt-4o-mini', temperature:0}) | none | env |

### Prod call sites (14: 11 chat, 3 embeddings)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| app/api/chat/route.ts:66 | langchain-js | ChatOpenAI.stream (prompt\|model\|StringOutputParser) | yes | yes | hardcoded 'gpt-4o-mini' |
| app/api/chat/agents/route.ts:107 | langchain-js | ChatOpenAI.streamEvents (createReactAgent: calculator + TavilySearch) | yes | yes | hardcoded 'gpt-4o-mini' |
| app/api/chat/agents/route.ts:134 | langchain-js | ChatOpenAI.invoke (agent.invoke, intermediate steps) | no | yes | hardcoded 'gpt-4o-mini' |
| app/api/chat/retrieval/route.ts:147 | langchain-js | ChatOpenAI.stream (LCEL RAG; model called 2x/request, lines 104 + 135) | yes | yes | hardcoded 'gpt-4o-mini' |
| app/api/chat/retrieval/route.ts:113 | langchain-js | OpenAIEmbeddings.embedQuery (implicit via asRetriever) | no | yes | library default |
| app/api/chat/retrieval_agents/route.ts:128 | langchain-js | ChatOpenAI.streamEvents (createReactAgent + createRetrieverTool) | yes | yes | hardcoded 'gpt-4o-mini' |
| app/api/chat/retrieval_agents/route.ts:157 | langchain-js | ChatOpenAI.invoke (agent.invoke) | no | yes | hardcoded 'gpt-4o-mini' |
| app/api/chat/retrieval_agents/route.ts:89 | langchain-js | OpenAIEmbeddings.embedQuery (implicit via asRetriever) | no | yes | library default |
| app/api/chat/structured_output/route.ts:72 | langchain-js | ChatOpenAI.withStructuredOutput(zod).invoke | no | yes | hardcoded 'gpt-4o-mini' |
| utils/supabase-vector-store.ts:25 | langchain-js | OpenAIEmbeddings.embedDocuments (wrapper; caller app/api/retrieval/ingest/route.ts:52) | no | yes | library default |
| app/langgraph/agent/agent.ts:18 | langchain-js | ChatOpenAI.invoke (StateGraph node, served by external LangGraph server) | no | yes | hardcoded 'gpt-4o-mini' |
| app/ai_sdk/agent/action.ts:33 | langchain-js | ChatOpenAI.streamEvents (classic AgentExecutor + createToolCallingAgent + Tavily) | yes | yes | hardcoded 'gpt-4o-mini' |
| app/ai_sdk/tools/action.ts:74 | langchain-js | ChatOpenAI.streamEvents (withStructuredOutput or bindTools + JsonOutputKeyToolsParser) | yes | yes | hardcoded 'gpt-4o-mini' |
| app/ai_sdk/tools/action.ts:87 | langchain-js | ChatOpenAI.stream (same chain) | yes | yes | hardcoded 'gpt-4o-mini' |

By surface:
- ChatOpenAI: `.stream` x3, `.streamEvents` x4, `.invoke` x4 (one of them is withStructuredOutput)
- OpenAIEmbeddings: `embedQuery` x2 (implicit), `embedDocuments` x1

### Non-prod call sites
- None executable.
- `data/DefaultRetrievalText.ts` is a docs/corpus string. Its snippets (ChatOpenAI 'gpt-3.5-turbo', OpenAIEmbeddings, FaissStore) are not run.
- The READMEs under `app/ai_sdk/*/README.md` repeat the action code.
- `utils/integrations.test.mjs` makes no LLM calls.

### Models seen
- `gpt-4o-mini`: all prod code
- `gpt-3.5-turbo`: docs string only
- Embeddings: model unspecified, so the @langchain/openai default applies

### Tests
- **Command:** `node --experimental-strip-types --test utils/integrations.test.mjs`. It comes from README:104 and needs Node 22.6+; it is not in package.json scripts and not in CI.
- **Framework:** `node:test` with `node:assert/strict`. It is the only test file.
- **Network:** not needed. The README says no API keys or live DB are required.
- **Mocking:** no LLM client is mocked or patched, because ChatOpenAI and OpenAIEmbeddings are never exercised. The tests cover `utils/calculator.ts` and `utils/supabase-vector-store.ts` using fakes passed in through the constructor:
  - fake embeddings `{embedDocuments, embedQuery}` (utils/integrations.test.mjs:6)
  - fake Supabase `from().upsert().select()` client (:46)
  - fake `rpc()` client (:84)
  - error-returning client (:132)
- No nock/msw/cassettes.

### Unusual / migration-relevant
- **`useResponsesApi` is never set.** With `gpt-4o-mini` and no built-in tools, @langchain/openai uses Chat Completions (`/v1/chat/completions`) plus `/v1/embeddings`.
- **Tool calling:** standard function tools via the LangGraph prebuilt `createReactAgent`, classic `AgentExecutor`/`createToolCallingAgent`, and `bindTools` with a raw OpenAI function schema.
- **Structured outputs:** `withStructuredOutput` at 2 sites with no `method` set, so the library default applies (json_schema `response_format` or tool calling). README claims `function_call`.
- **Streaming:** `chain.stream`, plus `streamEvents` v2 filtered on `on_chat_model_stream`. The API routes run on `runtime='edge'`; the server actions (`app/ai_sdk/*/action.ts`) do not.
- **Embeddings** go through the custom Supabase adapter (`documents` table, `match_documents` RPC). A router without `/v1/embeddings` breaks ingest and both retrieval flows.
- **Runtime network calls besides OpenAI:**
  - LangChain Hub `pull('hwchase17/openai-tools-agent')` (app/ai_sdk/agent/action.ts:19)
  - Tavily search
  - Supabase
- **LangGraph page** (`app/langgraph/page.tsx:183`) talks to a separate LangGraph server at `http://localhost:2024`, assistantId `agent`. There is no `langgraph.json` in the repo.
- **Vercel AI SDK** (`ai`, `@ai-sdk/react`, `@ai-sdk/rsc`) is used only for UI and stream plumbing. There are no `ai.*` model calls and no `@ai-sdk/openai`.
- **Unused env entries:**
  - `.env.example` lists the stale `SERPAPI_API_KEY`; the code uses `TAVILY_API_KEY`.
  - `ANTHROPIC_API_KEY` appears only as a commented option.
  - No real keys are present.
