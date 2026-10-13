## assafelovic/gpt-researcher @ 3582a6841f

- **Lang**: Python (library `gpt_researcher/`, FastAPI `backend/`, `multi_agents/`, `cli.py`) + JS/TS Next.js UI (`frontend/nextjs`) + static JS (`frontend/scripts.js`).
- **Pkg manager**: pip/setuptools (`setup.py` reads requirements.txt; CI uses `pip install -e ".[test]"`) + poetry-core build backend (`pyproject.toml` has both `[project]` and `[tool.poetry]`, `poetry.toml` in-project venv, `[tool.uv.sources]` stanza) + npm for frontend. **Lockfiles**: none committed.
- **Python req**: `>=3.12` (pyproject `requires-python`, setup.py `python_requires`, classifiers 3.12-3.14, `.python-version` 3.12.1, CI matrix 3.12/3.13/3.14 in .github/workflows/tests.yml).
- **py3.9 verdict: NO.** Besides requires-python, prod code uses `match` statements (gpt_researcher/memory/embeddings.py:88, gpt_researcher/actions/retriever.py:49 - SyntaxError on 3.9) and runtime PEP 604 annotations without `from __future__` (e.g. base.py:118 `str | None`); deps langchain>=1.0 / langchain-core>=1.0 need >=3.10, numpy>=2.0 needs >=3.9/3.10.
- **Node**: 18.17.0 (frontend/nextjs/Dockerfile `node:18.17.0-alpine`; no .nvmrc/engines); Dockerfile.fullstack uses `node:slim`.

### Clients (prod; all constructed in two factories)
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| gpt_researcher/llm_provider/generic/base.py:159 | langchain-py | ChatOpenAI(**kwargs, stream_usage=True) | env OPENAI_BASE_URL -> openai_api_base (also set in utils/llm.py:113) | env OPENAI_API_KEY (implicit) |
| base.py:164 | langchain-py | ChatAnthropic | none | env ANTHROPIC_API_KEY (implicit) |
| base.py:173 | langchain-py | AzureChatOpenAI(azure_deployment=model) | env AZURE_OPENAI_ENDPOINT (implicit) | env AZURE_OPENAI_API_KEY |
| base.py:178/183/188/193/209/218/226/231/239/252/273/279/352 | langchain-py | ChatCohere / ChatVertexAI / ChatGoogleGenerativeAI / ChatFireworks / ChatTogether / ChatMistralAI (env MISTRAL_BASE_URL) / ChatHuggingFace / ChatGroq / ChatBedrock / ChatXAI / ChatLiteLLM / GigaChat / ChatNetmind | provider defaults | provider env (implicit) |
| base.py:204 | langchain-py | ChatOllama | env OLLAMA_BASE_URL (default http://localhost:11434) | none |
| base.py:244/257/265/293/311/319/327/335 | langchain-py | ChatOpenAI w/ hardcoded base: dashscope, deepseek, atlascloud, openrouter (+InMemoryRateLimiter), aimlapi, forge, avian, minimax | hardcoded URLs | env DASHSCOPE/DEEPSEEK/ATLASCLOUD/OPENROUTER/AIMLAPI/FORGE/AVIAN/MINIMAX_API_KEY |
| base.py:302 / :344 | langchain-py | ChatOpenAI vllm_openai / nebius | env VLLM_OPENAI_API_BASE / env NEBIUS_BASE_URL (default tokenfactory.nebius.com) | env VLLM_OPENAI_API_KEY / NEBIUS_API_KEY |
| gpt_researcher/memory/embeddings.py:92-218 (21 ctors) | langchain-py | OpenAIEmbeddings (custom/openai/aimlapi/openrouter/minimax/nebius), AzureOpenAIEmbeddings, Cohere, VertexAI, GoogleGenerativeAI, Fireworks, GigaChat, Ollama, Together, Netmind, MistralAI, HuggingFace, Nomic, VoyageAI, DashScope, Bedrock | openai: env OPENAI_BASE_URL; custom: OPENAI_BASE_URL default http://localhost:1234/v1; others hardcoded/env | per-provider env |
| gpt_researcher/llm_provider/image/image_generator.py:80 | google-genai | genai.Client(api_key) | none | env GOOGLE_API_KEY / GEMINI_API_KEY |
| multi_agents/ag2/agents/orchestrator.py:44 | other:ag2 | ConversableAgent llm_config model task.model or "gpt-4o", api_type openai | none | env OPENAI_API_KEY (implicit) - never actually prompts (request_reply=False) |

### Prod call sites (14)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| gpt_researcher/llm_provider/generic/base.py:365 | langchain-py | ChatOpenAI.ainvoke (any of 27 providers) | no | yes | config FAST_LLM/SMART_LLM/STRATEGIC_LLM via create_chat_completion |
| gpt_researcher/llm_provider/generic/base.py:384 | langchain-py | ChatOpenAI.astream | yes | yes | same |
| gpt_researcher/utils/llm.py:214 | langchain-py | (prompt \| llm \| PydanticOutputParser).ainvoke | no | yes | config SMART_LLM |
| gpt_researcher/utils/tools.py:116 | langchain-py | llm.bind_tools(...).ainvoke (turn 1) | no | yes | config SMART_LLM (backend chat) |
| gpt_researcher/utils/tools.py:188 | langchain-py | bind_tools ainvoke (turn 2 after ToolMessages) | no | yes | config SMART_LLM |
| gpt_researcher/mcp/research.py:79 | langchain-py | bind_tools(MCP tools).ainvoke | no | yes | config STRATEGIC_LLM |
| gpt_researcher/context/compression.py:187 | langchain-py | EmbeddingsFilter -> OpenAIEmbeddings.embed_documents/embed_query (sync in to_thread) | no | no | config EMBEDDING (default openai:text-embedding-3-small) |
| gpt_researcher/context/compression.py:264 | langchain-py | same (WrittenContentCompressor) | no | no | config EMBEDDING |
| gpt_researcher/vector_store/vector_store.py:24 | langchain-py | user VectorStore.add_documents (embeds) | no | no | user-supplied vector store |
| gpt_researcher/vector_store/vector_store.py:61 | langchain-py | user VectorStore.asimilarity_search (embeds query) | no | yes | user-supplied |
| gpt_researcher/llm_provider/image/image_generator.py:283 | google-genai | models.generate_content (image) | no | to_thread | config IMAGE_GENERATION_MODEL (models/gemini-2.5-flash-image) |
| gpt_researcher/llm_provider/image/image_generator.py:366 | google-genai | models.generate_images (Imagen) | no | to_thread | config IMAGE_GENERATION_MODEL (imagen-*) |
| gpt_researcher/llm_provider/image/modelslab_image_generator.py:209 | raw http (ModelsLab) | POST modelslab.com/api/v6/images/text2img (fallback requests :219, poll :92/:110) | no | yes | config IMAGE_GENERATION_MODEL (default flux) |
| gpt_researcher/context/jev_filter.py:101 | raw http (TypeSafe Jev) | POST api.typesafe.ai/v1/systemone relevance scoring | no | yes | env JEV_MODEL (jev-latest) |

Counts: LangChain chat 6 (4 ainvoke incl. 3 tool-calling, 1 LCEL chain, 1 astream); LangChain embeddings 4 (2 EmbeddingsFilter + 2 user vector store); google-genai image 2; raw-http image 1; raw-http scoring 1. No direct openai-python / anthropic-python calls in prod.

**Wrappers**: `create_chat_completion` (gpt_researcher/utils/llm.py:41) -> `get_llm` -> `GenericLLMProvider.get_chat_response` (base.py:361) is called from ~28 prod places: agent.py:573, actions/query_processing.py:118/132/145, actions/report_generation.py:38/89/139/187/292/309 (all stream=True), actions/agent_creator.py:45, skills/curator.py:65, skills/deep_research.py:287/341/373, skills/image_generator.py:235/301, mcp/tool_selector.py:148, utils/tools.py:216 (fallback), backend/chat/chat.py (via create_chat_completion_with_tools :155), cli.py:189, multi_agents/agents/utils/llms.py:20 (`call_model`, used by multi_agents editor/writer/reviser/reviewer/visualizer/fact_checker and ag2 editor). `construct_subtopics` (utils/llm.py:165) called from skills/writer.py:216.

### Non-prod call sites (16)
- **evals** (11): evals/context_filter/judge.py:37 openai-python `chat.completions.create` (AsyncOpenAI :31); evals/quality_eval/metrics.py:240, 338, 360, 454, 491 ChatOpenAI.ainvoke (gpt-4-turbo grader); evals/simple_evals/simpleqa_eval.py:158 ChatOpenAI.invoke; evals/hallucination_eval/evaluate.py:39 `judges` pkg HaluEvalDocumentSummaryNonFactual.judge (openai/gpt-4o); deep_agents/recency_benchmark.py:228 ChatOpenAI.invoke (grader, reused by breadth/hybrid benchmarks); deep_agents/report_benchmark.py:135 ChatOpenAI.invoke; deep_agents/report_benchmark.py:114 + deep_agents/drb_generate.py:109 deepagents `agent.ainvoke`.
- **example** (1): deep_agents/main.py:76 deepagents `agent.astream` (agent built at deep_agents/agent.py:83 with `openai:gpt-5.4` model string). Wrapper-only examples: backend/report_type/deep_research/example.py:76/109/142 (create_chat_completion), docs/docs/examples/*.py (GPTResearcher API only).
- **scripts in tests/** (not collected; hyphenated names) (3 listed): tests/test-your-embeddings.py:42 embed_query; tests/vector-store.py:110 FAISS.from_documents(OpenAIEmbeddings()), :155 (+:175/:196/:215) InMemoryVectorStore asimilarity_search. tests/test-openai-llm.py:25 and tests/test-your-llm.py:11 call the wrappers live.

### Models seen
Defaults: openai:gpt-5.4 (SMART/STRATEGIC), openai:gpt-5.4-mini (FAST), openai:text-embedding-3-small, models/gemini-2.5-flash-image, flux, jev-latest. Hardcoded lists (temperature/reasoning gating in base.py:43-90; cost tables utils/costs.py & actions/utils.py): gpt-5/-mini/-nano, gpt-5.4(-mini/-nano/-pro), gpt-5.5(-pro), o1/o1-mini/o3/o3-mini/o4-mini (+dated), gpt-4o/-mini, gpt-4.1(-mini/-nano), gpt-4, gpt-4-32k, gpt-3.5-turbo, claude-sonnet-4-5/4-6, claude-opus-4-5/4-6/4-7, claude-haiku-4-5, claude-3-5-haiku, deepseek/deepseek-reasoner, text-embedding-3-large, text-embedding-004, imagen-4.0-*, gemini image models. multi_agents task.json: gpt-5.4; ag2 default gpt-4o. Evals: gpt-4-turbo, openai/gpt-4o.

### Tests
- **Command (CI .github/workflows/tests.yml)**: `pip install -e ".[test]"` then `python -m pytest tests/ --forked --timeout=60 -p no:warnings --deselect tests/test_researcher_logging.py --deselect tests/test_logging_output.py --deselect tests/test_mcp.py` with env `GPTR_BLOCK_NETWORK=1`; tiktoken cache warmed before (o200k_base, cl100k_base). Also an `imports` job (import every submodule on 3.12-3.14) and `collect` job (`pytest tests/ --collect-only -q`). pyproject: asyncio_mode=auto, testpaths=tests, python_files=test_*.py.
- **Framework**: pytest + pytest-asyncio + pytest-timeout + pytest-forked (some unittest.TestCase).
- **Network**: partial. CI suite is offline-enforced by tests/conftest.py:21 (socket.connect guard for non-loopback when GPTR_BLOCK_NETWORK=1). Deselected live tests: test_researcher_logging.py and test_logging_output.py (real OpenAI client, need OPENAI_API_KEY), test_mcp.py (real web research). tiktoken needs network or pre-warmed cache. Hyphenated scripts in tests/ are live but not collected.
- **Mocking** (no cassettes, no respx/HTTP mocking of LLM APIs - patch project wrappers):
  - `patch("gpt_researcher.utils.llm.get_llm", return_value=provider)` + `provider.get_chat_response = AsyncMock(...)` - tests/test_llm_max_tokens.py:13, tests/test_llm_kwargs_override.py:16.
  - `patch("gpt_researcher.agent.create_chat_completion", new_callable=AsyncMock)` + `patch("langchain_openai.OpenAIEmbeddings")` - tests/test_quick_search.py:10-11, tests/test_quick_search_summary_context.py:24.
  - module-level `create_chat_completion` swaps: deep_research (tests/test_deep_research_parsing.py:95), report_generation (tests/test_report_generation_available_images.py:77), agent_creator (tests/test_create_agent_json_repair_primary.py:23), skills.curator (tests/test_source_curator_json_parsing.py:51), skills.image_generator (tests/test_image_generator_json_repair.py:38), utils.tools (tests/chat/test_chat_search_sources.py:148); MCP `_call_llm_for_tool_selection` (tests/test_mcp_tool_selector_json_repair.py:28).
  - `monkeypatch.setattr(GenericLLMProvider, "from_provider", lambda *a, **kw: SimpleNamespace(llm=ScriptedModel(...)))` for bind_tools/tool_calls flow - tests/chat/test_chat_search_sources.py:65, :178.
  - Fake LLM object with `astream` injected into `GenericLLMProvider(...)` - tests/test_llm_usage_tracking.py:26.
  - `Memory` replaced on gpt_researcher.agent - tests/test_agent_visited_urls.py:22, tests/test_jev_context_filter.py:199.
  - Jev HTTP: `httpx.MockTransport` - tests/test_jev_context_filter.py:83; FakeClient(JevClient) :23.
  - tests/test_nebius_provider.py:39 builds real ChatOpenAI/OpenAIEmbeddings with dummy key and only asserts `openai_api_base`.

### Unusual / migration-relevant
- Single provider abstraction `GenericLLMProvider.from_provider` (base.py:146) with 27 provider branches over LangChain chat classes; provider+model from `"provider:model"` env strings (FAST_LLM/SMART_LLM/STRATEGIC_LLM, EMBEDDING). The cleanest router hook is the `openai` branch: OPENAI_BASE_URL is honored explicitly for chat (base.py:152, utils/llm.py:113) and embeddings (embeddings.py:105), and `custom` embeddings default to OPENAI_BASE_URL. Nine other OpenAI-compatible providers use ChatOpenAI with hardcoded base URLs.
- `create_chat_completion` gates temperature/reasoning_effort on hardcoded model-name lists (base.py:43-90), so router model aliases would change request params; LLM_KWARGS env JSON overrides; 10 retries with backoff; cost tracking reads LangChain `usage_metadata`/`response_metadata` (ChatOpenAI stream_usage=True) with tiktoken fallback.
- Tool calling via LangChain `bind_tools` (backend chat quick_search tool; MCP tools). No native structured outputs: PydanticOutputParser and json_repair on free text. No Responses API, store, or previous_response_id usage.
- Embeddings (Router unsupported) default to OpenAI text-embedding-3-small, but only run when CONTEXT_FILTER=embeddings or for written-content similarity in detailed reports (falls back to BM25 if embeddings unavailable). Default CONTEXT_FILTER=auto uses the TypeSafe Jev HTTP API if TYPESAFE_API_KEY is set, else local BM25.
- Image generation is opt-in (IMAGE_GENERATION_ENABLED=false by default): google-genai or ModelsLab raw HTTP.
- `_check_pkg` (base.py:407) runs `pip install` at runtime for missing provider packages.
- litellm is only reachable through `langchain_community.ChatLiteLLM` (provider `litellm`).
- multi_agents/ (LangGraph) is served by backend `/api/multi_agents` and routes all LLM calls through `call_model` -> `create_chat_completion` using `cfg.smart_llm_provider` with the model from task/STRATEGIC_LLM. The AG2 variant's autogen agents never request LLM replies.
- The frontend makes no LLM calls (websocket to backend; optional @langchain/langgraph-sdk Client in components/Langgraph/Langgraph.js).
- No lockfiles. requirements.txt and pyproject deps diverge (google-genai and tavily-python appear only in requirements.txt).
