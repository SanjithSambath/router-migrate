## mem0ai/mem0 @ d26ba5e451

- **Languages:** Python (`mem0/` package `mem0ai` 2.2.1), TypeScript (`mem0-ts/` npm `mem0ai` 3.3.1), plus JS/TS examples & integration packages.
- **Pkg manager / lockfiles:** Python built with hatchling, CI installs via `pip install -e ".[test,graph,vector_stores,llms,extras]"`, test via hatch env scripts; `poetry.lock` present at root (legacy). TS: pnpm 10.5.2 (`mem0-ts/pnpm-lock.yaml`).
- **Python req:** `>=3.10,<4.0` (pyproject `requires-python`); CI matrix `.github/workflows/ci.yml:71` = 3.10/3.11/3.12; hatch envs dev_py_3_10..3_12.
- **py3.9 verdict: NO.** pip would refuse install (`requires-python>=3.10`). Even forced: `mem0/vector_stores/qdrant.py:40,111` uses PEP 604 `bool | None` / `SparseVector | None` in signatures without `from __future__ import annotations` -> TypeError at import (qdrant is the default vector store, used by most Memory tests); `tests/test_oss_to_platform_migrate.py:207` same; extras pin `transformers>=5.3.0` (py>=3.10). All mem0/ and tests/ files do *parse* under 3.9 AST (no `match`), so only runtime-evaluated annotations + deps block it.
- **Node:** engines `>=18`; CI matrix 20, 22 (`ts-sdk-ci.yml:69`).

### Provider abstraction
- Python: `mem0/utils/factory.py` `LlmFactory.provider_to_class` (:42-60): ollama, openai, groq, together, aws_bedrock, litellm, azure_openai, openai_structured, anthropic, azure_openai_structured, gemini, deepseek, minimax, xai, sarvam, lmstudio, vllm, langchain. `EmbedderFactory` (:154-164): openai, ollama, huggingface, azure_openai, gemini, vertexai, together, lmstudio, langchain, aws_bedrock, fastembed(local). Rerankers: cohere, zero_entropy, llm (uses LlmFactory), huggingface/sentence-transformer (local).
- Wrapper callers (not SDK call sites): `mem0/memory/main.py:958` (fact extraction, `response_format={'type':'json_object'}`), `:2028` (procedural memory), `mem0/memory/utils.py:182`, `mem0/reranker/llm_reranker.py:142`, `server/main.py:355`; embeddings via `self.embedding_model.embed(...)` at ~10 places in `mem0/memory/main.py`.
- TS: `mem0-ts/src/oss/src/utils/factory.ts` LLMFactory (:111-147) / EmbedderFactory (:79-101). DeepSeek, xAI, MiniMax, Together, LMStudio, vLLM, Sarvam, LiteLLM all `extends OpenAILLM` -> share `llms/openai.ts:23/56`; TogetherEmbedder extends OpenAIEmbedder.

### Clients (base_url / api_key)
| file:line | sdk | ctor | base_url | api_key | prod |
|---|---|---|---|---|---|
| mem0/llms/openai.py:43 | openai-python | OpenAI(api_key, base_url) [OpenRouter branch] | config:openrouter_base_url | env:OPENROUTER_API_BASE | hardcoded:'https://openrouter.ai/api/v1' | env:OPENROUTER_API_KEY (branch taken when set) | True |
| mem0/llms/openai.py:53 | openai-python | OpenAI(api_key, base_url) | config:openai_base_url | env:OPENAI_BASE_URL | hardcoded:'https://api.openai.com/v1' | config:api_key | env:OPENAI_API_KEY | True |
| mem0/llms/openai_structured.py:19 | openai-python | OpenAI(api_key, base_url) | config:openai_base_url | env:OPENAI_BASE_URL | hardcoded:'https://api.openai.com/v1' | config:api_key | env:OPENAI_API_KEY | True |
| mem0/llms/azure_openai.py:63 | openai-python | AzureOpenAI(azure_deployment, azure_endpoint, api_version, api_key|azure_ad_token_provider, http_client, default_headers) | config:azure_kwargs.azure_endpoint | env:LLM_AZURE_ENDPOINT | config:azure_kwargs.api_key | env:LLM_AZURE_OPENAI_API_KEY; falls back to DefaultAzureCredential token provider | True |
| mem0/llms/azure_openai_structured.py:42 | openai-python | AzureOpenAI(...) | config:azure_kwargs.azure_endpoint | env:LLM_AZURE_ENDPOINT | config:azure_kwargs.api_key | env:LLM_AZURE_OPENAI_API_KEY; DefaultAzureCredential fallback | True |
| mem0/llms/anthropic.py:45 | anthropic-python | anthropic.Anthropic(api_key, base_url?) | config:anthropic_base_url | env:ANTHROPIC_BASE_URL (only passed if set) | config:api_key | env:ANTHROPIC_API_KEY | True |
| mem0/llms/deepseek.py:41 | openai-python | OpenAI(api_key, base_url) | config:deepseek_base_url | env:DEEPSEEK_API_BASE | hardcoded:'https://api.deepseek.com' | config:api_key | env:DEEPSEEK_API_KEY | True |
| mem0/llms/xai.py:41 | openai-python | OpenAI(api_key, base_url) | config:xai_base_url | env:XAI_API_BASE | hardcoded:'https://api.x.ai/v1' | config:api_key | env:XAI_API_KEY | True |
| mem0/llms/minimax.py:45 | openai-python | OpenAI(api_key, base_url) | config:minimax_base_url | env:MINIMAX_API_BASE | hardcoded default | config:api_key | env:MINIMAX_API_KEY | True |
| mem0/llms/lmstudio.py:41 | openai-python | OpenAI(base_url, api_key) | config:lmstudio_base_url (default 'http://localhost:1234/v1') | config:api_key | hardcoded:'lm-studio' | True |
| mem0/llms/vllm.py:40 | openai-python | OpenAI(api_key, base_url) | config:vllm_base_url | env:VLLM_BASE_URL | hardcoded:'http://localhost:8000/v1' | config:api_key | env:VLLM_API_KEY | hardcoded:'vllm-api-key' | True |
| mem0/llms/groq.py:26 | groq | Groq(api_key) | none (SDK default; GROQ_BASE_URL env honored implicitly by groq SDK) | config:api_key | env:GROQ_API_KEY | True |
| mem0/llms/together.py:23 | other:together | Together(api_key) | none (SDK default) | config:api_key | env:TOGETHER_API_KEY | True |
| mem0/llms/litellm.py:91 | litellm | (module-level litellm.completion, no client) | none passed (litellm env per provider, e.g. OPENAI_API_BASE) | env per provider (implicit, litellm) | True |
| mem0/llms/ollama.py:41 | ollama | ollama.Client(host) | config:ollama_base_url (None -> OLLAMA_HOST env / localhost:11434 implicit) | none | True |
| mem0/llms/gemini.py:40 | google-genai | genai.Client(vertexai=True, project, location) | none (Vertex; project/location from config or env:GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION, toggled by env:GOOGLE_GENAI_USE_VERTEXAI) | ADC | True |
| mem0/llms/gemini.py:43 | google-genai | genai.Client(api_key) | none | config:api_key | env:GOOGLE_API_KEY | True |
| mem0/llms/sarvam.py:81 | raw-http-openai | requests.post | config:sarvam_base_url | env:SARVAM_API_BASE | hardcoded:'https://api.sarvam.ai/v1' | config:api_key | env:SARVAM_API_KEY | True |
| mem0/llms/aws_bedrock.py:89 | other:boto3-bedrock | boto3.client('bedrock-runtime', **aws_config) | none (region from config/env:AWS_REGION, default us-west-2) | config aws_* | env:AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN/AWS_PROFILE | True |
| mem0/proxy/main.py:107 | litellm | (module-level litellm.completion) | param:base_url | param:api_key / litellm env | True |
| mem0/embeddings/openai.py:35 | openai-python | OpenAI(api_key, base_url) | config:openai_base_url | env:OPENAI_API_BASE (deprecated, warns) | env:OPENAI_BASE_URL | hardcoded:'https://api.openai.com/v1' | config:api_key | env:OPENAI_API_KEY | True |
| mem0/embeddings/azure_openai.py:34 | openai-python | AzureOpenAI(...) | config:azure_kwargs.azure_endpoint | env:EMBEDDING_AZURE_ENDPOINT | config:azure_kwargs.api_key | env:EMBEDDING_AZURE_OPENAI_API_KEY; DefaultAzureCredential fallback | True |
| mem0/embeddings/huggingface.py:22 | openai-python | OpenAI(base_url, api_key) | config:huggingface_base_url (TEI) | config:api_key | env:HUGGINGFACE_API_KEY | hardcoded:'hf' | True |
| mem0/embeddings/lmstudio.py:17 | openai-python | OpenAI(base_url, api_key) | config:lmstudio_base_url (default 'http://localhost:1234/v1') | config:api_key | hardcoded:'lm-studio' | True |
| mem0/embeddings/together.py:17 | other:together | Together(api_key) | none | config:api_key | env:TOGETHER_API_KEY | True |
| mem0/embeddings/gemini.py:20 | google-genai | genai.Client(api_key) | none | config:api_key | env:GOOGLE_API_KEY | True |
| mem0/embeddings/ollama.py:19 | ollama | ollama.Client(host) | config:ollama_base_url | none | True |
| mem0/embeddings/vertexai.py:42 | other:vertexai | TextEmbeddingModel.from_pretrained | none | ADC / config credentials json -> env:GOOGLE_APPLICATION_CREDENTIALS | True |
| mem0/embeddings/aws_bedrock.py:46 | other:boto3-bedrock | boto3.client('bedrock-runtime') | none | env:AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN | True |
| mem0/reranker/cohere_reranker.py:35 | other:cohere | cohere.Client(api_key) | none | config:api_key | env:COHERE_API_KEY | True |
| mem0/reranker/zero_entropy_reranker.py:38 | other:zeroentropy | ZeroEntropy(api_key) / ZeroEntropy() | none | config:api_key | env:ZERO_ENTROPY_API_KEY | True |
| mem0-ts/src/oss/src/llms/openai.ts:10 | openai-node | new OpenAI({apiKey, baseURL}) | config:baseURL (DEFAULT_MEMORY_CONFIG hardcodes 'https://api.openai.com/v1' which overrides OPENAI_BASE_URL env when defaults merged); subclasses inject their own baseURL | config:apiKey (defaults: env:OPENAI_API_KEY via config/defaults.ts) | True |
| mem0-ts/src/oss/src/llms/openai_structured.ts:10 | openai-node | new OpenAI({apiKey, baseURL}) | config:baseURL | config:apiKey | True |
| mem0-ts/src/oss/src/llms/deepseek.ts:11 | openai-node | super(OpenAILLM) -> new OpenAI | config:baseURL | env:DEEPSEEK_API_BASE | hardcoded:'https://api.deepseek.com' | config:apiKey | env:DEEPSEEK_API_KEY | True |
| mem0-ts/src/oss/src/llms/xai.ts:20 | openai-node | super(OpenAILLM) | config:baseURL | env:XAI_API_BASE | hardcoded:'https://api.x.ai/v1' | config:apiKey | env:XAI_API_KEY | True |
| mem0-ts/src/oss/src/llms/minimax.ts:11 | openai-node | super(OpenAILLM) | config:baseURL | env:MINIMAX_API_BASE | hardcoded:'https://api.minimax.io/v1' | config:apiKey | env:MINIMAX_API_KEY | True |
| mem0-ts/src/oss/src/llms/together.ts:11 | openai-node | super(OpenAILLM) | config:baseURL | env:TOGETHER_API_BASE | hardcoded:'https://api.together.ai/v1' | config:apiKey | env:TOGETHER_API_KEY | True |
| mem0-ts/src/oss/src/llms/sarvam.ts:20 | openai-node | super(OpenAILLM) | config:baseURL | env:SARVAM_API_BASE | hardcoded:'https://api.sarvam.ai/v1' | config:apiKey | env:SARVAM_API_KEY | True |
| mem0-ts/src/oss/src/llms/litellm.ts:7 | openai-node | super(OpenAILLM) | config:baseURL | env:LITELLM_API_BASE | hardcoded:'http://localhost:4000' | config:apiKey | env:LITELLM_API_KEY | hardcoded:'sk-anything' | True |
| mem0-ts/src/oss/src/llms/lmstudio.ts:12 | openai-node | super(OpenAILLM) | config:baseURL ?? hardcoded:'http://localhost:1234/v1' | config:apiKey | hardcoded default | True |
| mem0-ts/src/oss/src/llms/vllm.ts:20 | openai-node | super(OpenAILLM) | config:baseURL|vllmBaseURL|url | env:VLLM_BASE_URL | hardcoded:'http://localhost:8000/v1' | config:apiKey | env:VLLM_API_KEY | hardcoded default | True |
| mem0-ts/src/oss/src/llms/azure.ts:16 | openai-node | new AzureOpenAI({apiKey, endpoint, ...modelProperties}) | config:modelProperties.endpoint | config:apiKey (required) | True |
| mem0-ts/src/oss/src/llms/anthropic.ts:41 | anthropic-ts | new Anthropic(clientArgs) (lazy loadPeer) | config:baseURL (only if set) | config:apiKey | env:ANTHROPIC_API_KEY | True |
| mem0-ts/src/oss/src/llms/groq.ts:27 | groq | new Groq({apiKey}) | none | config:apiKey | env:GROQ_API_KEY | True |
| mem0-ts/src/oss/src/llms/google.ts:23 | google-genai | new GoogleGenAI({apiKey}) | none | config:apiKey | True |
| mem0-ts/src/oss/src/llms/mistral.ts:26 | mistral | new Mistral({apiKey}) | none | config:apiKey (required) | True |
| mem0-ts/src/oss/src/llms/ollama.ts:25 | ollama | new Ollama({host}) | config:url|baseURL | hardcoded:'http://localhost:11434' | none | True |
| mem0-ts/src/oss/src/llms/aws_bedrock.ts:150 | other:aws-sdk-bedrock-runtime | new BedrockRuntimeClient(clientConfig) | none (region config | env:AWS_REGION/AWS_DEFAULT_REGION) | AWS default credential chain / config | True |
| mem0-ts/src/oss/src/embeddings/openai.ts:11 | openai-node | new OpenAI({apiKey, baseURL}) | config:baseURL|url | config:apiKey (defaults env:OPENAI_API_KEY) | True |
| mem0-ts/src/oss/src/embeddings/together.ts:17 | openai-node | super(OpenAIEmbedder) | config:baseURL|url | env:TOGETHER_API_BASE | hardcoded:'https://api.together.ai/v1' | config:apiKey | env:TOGETHER_API_KEY | True |
| mem0-ts/src/oss/src/embeddings/azure.ts:17 | openai-node | new AzureOpenAI(...) | config:modelProperties.endpoint | config:apiKey | True |
| mem0-ts/src/oss/src/embeddings/huggingface.ts:38 | openai-node | new OpenAI({apiKey, baseURL}) | config:huggingfaceBaseUrl|baseURL | env:HUGGINGFACE_BASE_URL (required) | config:apiKey | env:HUGGINGFACE_API_KEY | 'hf' | True |
| mem0-ts/src/oss/src/embeddings/lmstudio.ts:17 | openai-node | new OpenAI({apiKey, baseURL}) | config:baseURL|url | hardcoded:'http://localhost:1234/v1' | config:apiKey | hardcoded default | True |
| mem0-ts/src/oss/src/embeddings/google.ts:25 | google-genai | new GoogleGenAI({apiKey}) | none | config:apiKey | env:GOOGLE_API_KEY | True |
| mem0-ts/src/oss/src/embeddings/ollama.ts:31 | ollama | new Ollama({host}) | config:url|baseURL | hardcoded:'http://localhost:11434' | none | True |
| mem0-ts/src/oss/src/embeddings/aws_bedrock.ts:148 | other:aws-sdk-bedrock-runtime | new BedrockRuntimeClient(...) | none (region config | env:AWS_REGION) | AWS credential chain | True |
| mem0-ts/src/oss/src/embeddings/vertexai.ts:115 | other:google-cloud-aiplatform | new PredictionServiceClient(clientOptions) | regional endpoint from location (env:GCP_LOCATION) | ADC / keyFilename | True |
| mem0-ts/src/oss/src/rerankers/cohere.ts:50 | other:cohere-ai | new CohereClient({token}) | none | config:apiKey | env:COHERE_API_KEY | True |
| mem0-ts/src/oss/src/rerankers/zeroentropy.ts:46 | other:zeroentropy | new ZeroEntropy({apiKey}) | none | config:apiKey | env:ZERO_ENTROPY_API_KEY | True |
| integrations/vercel-ai-sdk/src/provider-response-provider.ts:25 | vercel-ai-sdk | createOpenAI/createCohere/createAnthropic/createGroq/createGoogleGenerativeAI (lines 25-61) | provider_config spread (user settings, e.g. baseURL) | config:apiKey | provider env (implicit) | False |
| integrations/hermes-plugin-mem0/_openai_llm.py:47 | openai-python | OpenAI(api_key, base_url, timeout=120) | config:openai_base_url | secret:OPENAI_API_BASE | secret:OPENAI_BASE_URL | hardcoded:'https://api.openai.com/v1' | config:api_key | secret:OPENAI_API_KEY | False |
| examples/openai-inbuilt-tools/index.js:38 | openai-node | new OpenAI() | env:OPENAI_BASE_URL (implicit) | env:OPENAI_API_KEY (implicit) | False |
| examples/multimodal-demo/src/hooks/useChat.ts:38 | openai-node | new OpenAI({apiKey, dangerouslyAllowBrowser}) | none | user_input | False |
| examples/misc/movie_recommendation_grok3.py:40 | openai-python | OpenAI(api_key, base_url=x.ai) | hardcoded:x.ai | env:XAI_API_KEY | False |
| examples/misc/voice_assistant_elevenlabs.py:35 | openai-python | OpenAI() | env:OPENAI_BASE_URL (implicit) | env:OPENAI_API_KEY (implicit) | False |
| examples/graph-db-demo/kuzu-example.ipynb:61 | openai-python | OpenAI() | env:OPENAI_BASE_URL (implicit) | env:OPENAI_API_KEY (implicit) | False |

### Prod call sites — Python `mem0/` (47)
| file:line | sdk | surface | stream | async | model source | notes |
|---|---|---|---|---|---|---|
| mem0/llms/openai.py:141 | openai-python | chat.completions.create | False | False | config:llm.config.model (default 'gpt-5-mini') | provider 'openai'. If OPENROUTER_API_KEY set, client points at OpenRouter and may pop 'model' in favor of config.models+route + extra_headers. Opt-in 'store', reasoning_effort, response_format, tools; response_callback hook |
| mem0/llms/openai_structured.py:48 | openai-python | chat.completions.parse | False | False | config:llm.config.model (default 'gpt-5-mini') | provider 'openai_structured'; client.beta.chat.completions.parse |
| mem0/llms/azure_openai.py:165 | openai-python | chat.completions.create | False | False | config:llm.config.model / azure_deployment (default 'gpt-5-mini') | AzureOpenAI client; provider 'azure_openai' |
| mem0/llms/azure_openai_structured.py:102 | openai-python | chat.completions.create | False | False | config:llm.config.model (default 'gpt-5-mini') | AzureOpenAI client; provider 'azure_openai_structured' (plain create despite name) |
| mem0/llms/anthropic.py:140 | anthropic-python | messages.create | False | False | config:llm.config.model (default 'claude-sonnet-4-6') | system extracted to top-level param; tools+tool_choice passthrough; response_format ignored |
| mem0/llms/deepseek.py:108 | openai-python | chat.completions.create | False | False | config:llm.config.model (default 'deepseek-chat') | OpenAI SDK -> DeepSeek base URL |
| mem0/llms/xai.py:109 | openai-python | chat.completions.create | False | False | config:llm.config.model (default 'grok-4.3') | OpenAI SDK -> api.x.ai |
| mem0/llms/minimax.py:113 | openai-python | chat.completions.create | False | False | config:llm.config.model (default 'MiniMax-M2.7') | OpenAI SDK -> MiniMax base URL |
| mem0/llms/lmstudio.py:113 | openai-python | chat.completions.create | False | False | config:llm.config.model (LM Studio default gguf) | OpenAI SDK -> local LM Studio |
| mem0/llms/vllm.py:107 | openai-python | chat.completions.create | False | False | config:llm.config.model (default 'Qwen/Qwen2.5-32B-Instruct') | OpenAI SDK -> vLLM server |
| mem0/llms/groq.py:118 | groq | chat.completions.create | False | False | config:llm.config.model (default 'llama-3.3-70b-versatile') | groq SDK (Groq class) |
| mem0/llms/together.py:91 | other:together | chat.completions.create | False | False | config:llm.config.model (default 'MiniMaxAI/MiniMax-M3') | together SDK (Together class) |
| mem0/llms/litellm.py:91 | litellm | litellm.completion | False | False | config:llm.config.model (default 'gpt-5-mini') | no api_key/base_url passed; litellm resolves from provider env vars |
| mem0/llms/ollama.py:143 | ollama | other:ollama.Client.chat | False | False | config:llm.config.model (default 'llama3.1:70b') |  |
| mem0/llms/gemini.py:224 | google-genai | other:genai.models.generate_content | False | False | config:llm.config.model (default 'gemini-2.0-flash') | google-genai Client (api key or vertexai=True); json response_schema + tools reformatted |
| mem0/llms/sarvam.py:81 | raw-http-openai | http:POST /chat/completions | False | False | config:llm.config.model (default 'sarvam-m') | requests.post to {base_url}/chat/completions, Bearer auth |
| mem0/llms/langchain.py:96 | langchain-py | langchain:BaseChatModel.invoke | False | False | config:llm.config.model (user-supplied BaseChatModel instance) | optionally bind_tools |
| mem0/llms/aws_bedrock.py:114 | other:boto3-bedrock | other:bedrock.list_foundation_models | False | False | n/a | model capability probe on init |
| mem0/llms/aws_bedrock.py:558 | other:boto3-bedrock | other:bedrock-runtime.converse | False | False | config:llm.config.model | tool-enabled path (_generate_with_tools) |
| mem0/llms/aws_bedrock.py:580 | other:boto3-bedrock | other:bedrock-runtime.converse | False | False | config:llm.config.model | anthropic.* models via Converse |
| mem0/llms/aws_bedrock.py:626 | other:boto3-bedrock | other:bedrock-runtime.converse | False | False | config:llm.config.model | minimax.* models |
| mem0/llms/aws_bedrock.py:635 | other:boto3-bedrock | other:bedrock-runtime.converse | False | False | config:llm.config.model | amazon nova models |
| mem0/llms/aws_bedrock.py:654 | other:boto3-bedrock | other:bedrock-runtime.invoke_model | False | False | config:llm.config.model | legacy providers (titan, cohere, meta, mistral, ...) |
| mem0/llms/aws_bedrock.py:667 | other:boto3-bedrock | other:bedrock.list_foundation_models | False | False | n/a | list_available_models() helper |
| mem0/llms/aws_bedrock.py:712 | other:boto3-bedrock | other:bedrock-runtime.converse | False | False | config:llm.config.model | validate_model_access() helper |
| mem0/llms/aws_bedrock.py:720 | other:boto3-bedrock | other:bedrock-runtime.invoke_model | False | False | config:llm.config.model | validate_model_access() helper |
| mem0/proxy/main.py:107 | litellm | litellm.completion | False | False | param | mem0.proxy Mem0().chat.completions.create(...) OpenAI-compatible facade; stream=param passthrough (can be True); base_url/api_key/api_version/model_list passthrough to litellm |
| mem0/embeddings/openai.py:55 | openai-python | embeddings.create | False | False | config:embedder.config.model (default 'text-embedding-3-small') |  |
| mem0/embeddings/openai.py:74 | openai-python | embeddings.create | False | False | config:embedder.config.model (default 'text-embedding-3-small') | batch |
| mem0/embeddings/azure_openai.py:55 | openai-python | embeddings.create | False | False | config:embedder.config.model | AzureOpenAI client |
| mem0/embeddings/azure_openai.py:67 | openai-python | embeddings.create | False | False | config:embedder.config.model | AzureOpenAI client, batch |
| mem0/embeddings/huggingface.py:42 | openai-python | embeddings.create | False | False | config:embedder.config.model (default 'tei') | only when huggingface_base_url set (TEI OpenAI-compatible); else local SentenceTransformer |
| mem0/embeddings/huggingface.py:52 | openai-python | embeddings.create | False | False | config:embedder.config.model (default 'tei') | batch; TEI path |
| mem0/embeddings/lmstudio.py:29 | openai-python | embeddings.create | False | False | config:embedder.config.model |  |
| mem0/embeddings/lmstudio.py:35 | openai-python | embeddings.create | False | False | config:embedder.config.model | batch |
| mem0/embeddings/together.py:30 | other:together | embeddings.create | False | False | config:embedder.config.model (default 'intfloat/multilingual-e5-large-instruct') |  |
| mem0/embeddings/together.py:35 | other:together | embeddings.create | False | False | config:embedder.config.model | batch |
| mem0/embeddings/gemini.py:37 | google-genai | other:genai.models.embed_content | False | False | config:embedder.config.model (default 'models/gemini-embedding-001') |  |
| mem0/embeddings/gemini.py:49 | google-genai | other:genai.models.embed_content | False | False | config:embedder.config.model | batch chunks |
| mem0/embeddings/ollama.py:49 | ollama | other:ollama.Client.embed | False | False | config:embedder.config.model (default 'nomic-embed-text') |  |
| mem0/embeddings/ollama.py:59 | ollama | other:ollama.Client.embed | False | False | config:embedder.config.model | batch |
| mem0/embeddings/vertexai.py:62 | other:vertexai | other:TextEmbeddingModel.get_embeddings | False | False | config:embedder.config.model (default 'gemini-embedding-001') |  |
| mem0/embeddings/vertexai.py:78 | other:vertexai | other:TextEmbeddingModel.get_embeddings | False | False | config:embedder.config.model | batch |
| mem0/embeddings/aws_bedrock.py:82 | other:boto3-bedrock | other:bedrock-runtime.invoke_model | False | False | config:embedder.config.model (default 'amazon.titan-embed-text-v1') |  |
| mem0/embeddings/langchain.py:35 | langchain-py | langchain:Embeddings.embed_query | False | False | config:embedder.config.model (user-supplied Embeddings instance) |  |
| mem0/reranker/cohere_reranker.py:66 | other:cohere | other:cohere.Client.rerank | False | False | config:reranker.config.model |  |
| mem0/reranker/zero_entropy_reranker.py:71 | other:zeroentropy | other:ZeroEntropy.models.rerank | False | False | config:reranker.config.model |  |

### Prod call sites — TypeScript `mem0-ts/src/oss/src` (37)
| file:line | sdk | surface | stream | async | model source | notes |
|---|---|---|---|---|---|---|
| mem0-ts/src/oss/src/llms/openai.ts:23 | openai-node | chat.completions.create | False | True | config:llm.config.model (default 'gpt-5-mini') | generateResponse; also used by subclasses DeepSeekLLM, XAILLM, MiniMaxLLM, TogetherLLM, LMStudioLLM, VllmLLM, SarvamLLM, LiteLLM (TS LiteLLM = OpenAI SDK pointed at LiteLLM proxy) |
| mem0-ts/src/oss/src/llms/openai.ts:56 | openai-node | chat.completions.create | False | True | config:llm.config.model (default 'gpt-5-mini') | generateChat; same subclass fan-out |
| mem0-ts/src/oss/src/llms/openai_structured.ts:23 | openai-node | chat.completions.create | False | True | config:llm.config.model (default 'gpt-5-mini') |  |
| mem0-ts/src/oss/src/llms/openai_structured.ts:70 | openai-node | chat.completions.create | False | True | config:llm.config.model (default 'gpt-5-mini') |  |
| mem0-ts/src/oss/src/llms/azure.ts:29 | openai-node | chat.completions.create | False | True | config:llm.config.model (default 'gpt-5-mini') | AzureOpenAI |
| mem0-ts/src/oss/src/llms/azure.ts:62 | openai-node | chat.completions.create | False | True | config:llm.config.model (default 'gpt-5-mini') | AzureOpenAI |
| mem0-ts/src/oss/src/llms/anthropic.ts:83 | anthropic-ts | messages.create | False | True | config:llm.config.model (default 'claude-sonnet-4-6') |  |
| mem0-ts/src/oss/src/llms/groq.ts:35 | groq | chat.completions.create | False | True | config:llm.config.model (default 'llama3-70b-8192') | groq-sdk |
| mem0-ts/src/oss/src/llms/groq.ts:52 | groq | chat.completions.create | False | True | config:llm.config.model (default 'llama3-70b-8192') | groq-sdk |
| mem0-ts/src/oss/src/llms/google.ts:71 | google-genai | other:genai.models.generateContent | False | True | config:llm.config.model (default 'gemini-2.0-flash') | @google/genai |
| mem0-ts/src/oss/src/llms/google.ts:98 | google-genai | other:genai.models.generateContent | False | True | config:llm.config.model (default 'gemini-2.0-flash') |  |
| mem0-ts/src/oss/src/llms/mistral.ts:55 | mistral | other:mistral.chat.complete | False | True | config:llm.config.model (default 'mistral-tiny-latest') |  |
| mem0-ts/src/oss/src/llms/mistral.ts:105 | mistral | other:mistral.chat.complete | False | True | config:llm.config.model (default 'mistral-tiny-latest') |  |
| mem0-ts/src/oss/src/llms/ollama.ts:40 | ollama | other:ollama.chat | False | True | config:llm.config.model (default 'llama3.1:8b') |  |
| mem0-ts/src/oss/src/llms/ollama.ts:80 | ollama | other:ollama.chat | False | True | config:llm.config.model (default 'llama3.1:8b') |  |
| mem0-ts/src/oss/src/llms/ollama.ts:108 | ollama | other:ollama.pull | False | True | config:llm.config.model | model pull, not inference |
| mem0-ts/src/oss/src/llms/langchain.ts:136 | langchain-js | langchain:BaseLanguageModel.invoke | False | True | config:llm.config.model (user-supplied instance) | withStructuredOutput / bindTools runnable |
| mem0-ts/src/oss/src/llms/langchain.ts:174 | langchain-js | langchain:BaseLanguageModel.invoke | False | True | config:llm.config.model (user-supplied instance) |  |
| mem0-ts/src/oss/src/llms/aws_bedrock.ts:245 | other:aws-sdk-bedrock-runtime | other:ConverseCommand | False | True | config:llm.config.model | all Bedrock LLM traffic via Converse |
| mem0-ts/src/oss/src/embeddings/openai.ts:20 | openai-node | embeddings.create | False | True | config:embedder.config.model (default 'text-embedding-3-small') | also TogetherEmbedder subclass |
| mem0-ts/src/oss/src/embeddings/openai.ts:36 | openai-node | embeddings.create | False | True | config:embedder.config.model (default 'text-embedding-3-small') | batch |
| mem0-ts/src/oss/src/embeddings/azure.ts:27 | openai-node | embeddings.create | False | True | config:embedder.config.model (default 'text-embedding-3-small') | AzureOpenAI |
| mem0-ts/src/oss/src/embeddings/azure.ts:42 | openai-node | embeddings.create | False | True | config:embedder.config.model | AzureOpenAI batch |
| mem0-ts/src/oss/src/embeddings/huggingface.ts:47 | openai-node | embeddings.create | False | True | config:embedder.config.model (default 'tei') |  |
| mem0-ts/src/oss/src/embeddings/huggingface.ts:63 | openai-node | embeddings.create | False | True | config:embedder.config.model (default 'tei') |  |
| mem0-ts/src/oss/src/embeddings/lmstudio.ts:25 | openai-node | embeddings.create | False | True | config:embedder.config.model |  |
| mem0-ts/src/oss/src/embeddings/lmstudio.ts:42 | openai-node | embeddings.create | False | True | config:embedder.config.model |  |
| mem0-ts/src/oss/src/embeddings/google.ts:30 | google-genai | other:genai.models.embedContent | False | True | config:embedder.config.model (default 'gemini-embedding-001') |  |
| mem0-ts/src/oss/src/embeddings/google.ts:42 | google-genai | other:genai.models.embedContent | False | True | config:embedder.config.model |  |
| mem0-ts/src/oss/src/embeddings/ollama.ts:43 | ollama | other:ollama.embed | False | True | config:embedder.config.model (default 'nomic-embed-text:latest') |  |
| mem0-ts/src/oss/src/embeddings/aws_bedrock.ts:220 | other:aws-sdk-bedrock-runtime | other:InvokeModelCommand | False | True | config:embedder.config.model |  |
| mem0-ts/src/oss/src/embeddings/vertexai.ts:171 | other:google-cloud-aiplatform | other:PredictionServiceClient.predict | False | True | config:embedder.config.model (default 'gemini-embedding-001') |  |
| mem0-ts/src/oss/src/embeddings/vertexai.ts:222 | other:google-cloud-aiplatform | other:PredictionServiceClient.predict | False | True | config:embedder.config.model |  |
| mem0-ts/src/oss/src/embeddings/langchain.ts:33 | langchain-js | langchain:Embeddings.embedQuery | False | True | config:embedder.config.model (user instance) |  |
| mem0-ts/src/oss/src/embeddings/langchain.ts:44 | langchain-js | langchain:Embeddings.embedDocuments | False | True | config:embedder.config.model (user instance) |  |
| mem0-ts/src/oss/src/rerankers/cohere.ts:62 | other:cohere-ai | other:CohereClient.rerank | False | True | config:reranker.config.model |  |
| mem0-ts/src/oss/src/rerankers/zeroentropy.ts:58 | other:zeroentropy | other:ZeroEntropy.models.rerank | False | True | config:reranker.config.model |  |

Prod totals: 84 (py 47, ts 37). Chat-style LLM inference sites: py openai-python chat.completions.create 8 + .parse 1, anthropic messages.create 1, litellm.completion 2 (provider + proxy), groq 1, together 1, ollama 1, gemini 1, sarvam raw-http 1, langchain 1, bedrock converse 5/invoke_model 3; ts openai-node chat.completions.create 6 (fan-out to 8 OpenAI-compatible subclasses), anthropic 1, groq 2, google 2, mistral 2, ollama 2, langchain 2, bedrock Converse 1. Embeddings: py 18 sites, ts 16 sites (openai-compatible embeddings.create: py 10 incl. together, ts 8). Rerank: py 2, ts 2.

### Non-prod call sites
- **Integration packages (separately published, prod:false/kind:prod):** `integrations/vercel-ai-sdk/src/provider-response-provider.ts:72` doGenerate, `:77` doStream (wraps createOpenAI/createAnthropic/createCohere/createGroq/createGoogleGenerativeAI at :25-61; default baseURL hardcoded `https://api.openai.com` in mem0-provider.ts:54 / mem0-facade.ts:15); `integrations/hermes-plugin-mem0/_openai_llm.py:59` chat.completions.create. Other integrations (openclaw, n8n, zapier, claude-code/cursor/codex plugins, strands, cli/) only talk to Mem0 platform API or configure mem0 OSS — no direct LLM SDK calls.
- **Examples (12):** openai-inbuilt-tools/index.js:57 responses.create (gpt-4o, web_search_preview); vercel-ai-sdk-chat-app/src/hooks/useChat.ts:126 ai.streamText; mem0-demo/app/api/chat/route.ts:76 ai.streamText(openai('gpt-4o')); multimodal-demo/{src/hooks/,}useChat.ts:174 chat.completions.create (x2 dup); yt-assistant-chrome/src/background.js:137 GET /v1/models, :167 POST /v1/chat/completions (raw fetch); misc/multillm_memory.py:87 litellm.completion; misc/movie_recommendation_grok3.py:55 chat.completions.create (x.ai); misc/voice_assistant_elevenlabs.py:150 audio.transcriptions.create (whisper-1); misc/personalized_search.py:38 ChatOpenAI; multiagents/llamaindex_learning_system.py:45 llama_index OpenAI. Framework-only examples (agno OpenAIChat, openai-agents Agent, google-adk, strands, autogen config_list) not counted. `mem0-ts/src/oss/examples/*` only configure Memory.
- **Notebooks (2):** examples/notebooks/customer-support-chatbot.ipynb:100 anthropic messages.create; examples/graph-db-demo/kuzu-example.ipynb:242 chat.completions.create.
- **Tests with real calls (32):** integrations/vercel-ai-sdk/tests/** generateText/streamText/generateObject (OpenAI gpt-4-turbo, Anthropic, Groq, Cohere, Google) — need MEM0_API_KEY + provider keys. Not run in vercel-ai-cd.yml.
- **Mock-only references (not listed as call sites):** tests/llms 159 grep hits, tests/embeddings 77, tests/test_proxy.py 6, mem0-ts src tests ~105.

### Models seen
MiniMax-M2.7, MiniMaxAI/MiniMax-M3, Qwen/Qwen2.5-32B-Instruct, amazon.titan-embed-text-v1, claude-3-5-sonnet-20240620, claude-3-5-sonnet-20241022, claude-3-5-sonnet-latest, claude-3-haiku-20240307, claude-sonnet-4-6, deepseek-chat, gemini-1.5-flash, gemini-2.0-flash, gemini-embedding-001, gpt-4-turbo, gpt-4-turbo-preview, gpt-4.1-nano-2025-04-14, gpt-4o, gpt-5-mini, grok-3-beta, grok-4.3, intfloat/multilingual-e5-large-instruct, llama-3.3-70b-versatile, llama3-70b-8192, llama3.1:70b, llama3.1:8b, mistral-tiny-latest, models/gemini-embedding-001, multi-qa-MiniLM-L6-cos-v1, nomic-embed-text, nomic-embed-text:latest, rerank-v3.5, sarvam-m, tei, text-embedding-3-small, thenlper/gte-large, us.anthropic.claude-sonnet-4-20250514-v1:0, whisper-1, zerank-1

### Tests
- **Python:** `make test` -> `hatch run test` -> `pytest tests/` (pyproject `[tool.hatch.envs.default.scripts]`). pytest + pytest-mock + pytest-asyncio. No network: all providers mocked.
- **TS:** `cd mem0-ts && pnpm run test:unit` (`jest --coverage --ci --testPathIgnorePatterns ... integration`); `pnpm run test:integration` (jest.integration.config.js) hits Mem0 platform with `MEM0_API_KEY` secret (not an LLM API).
- **needs network:** partial (core unit tests: no; mem0-ts integration: Mem0 platform; vercel-ai-sdk integration tests: real LLM APIs).
- **Mocking (Python):** `unittest.mock.patch` of the SDK class imported in each provider module, then assert on `mock_client.chat.completions.create`/`messages.create`/`embeddings.create`:
  - tests/llms/test_openai.py:14 — patch('mem0.llms.openai.OpenAI') -> mock_client.chat.completions.create.return_value
  - tests/llms/test_openai_structured.py:11 — patch('mem0.llms.openai_structured.OpenAI')
  - tests/llms/test_azure_openai.py:16 — patch('mem0.llms.azure_openai.AzureOpenAI'); also DefaultAzureCredential/get_bearer_token_provider (:417-418), httpx.Client (:315)
  - tests/llms/test_azure_openai_structured.py:43 — patch('mem0.llms.azure_openai_structured.AzureOpenAI')
  - tests/llms/test_anthropic.py:14 — patch('mem0.llms.anthropic.anthropic') (module)
  - tests/llms/test_deepseek.py:13 — patch('mem0.llms.deepseek.OpenAI')
  - tests/llms/test_xai.py:14 — patch('mem0.llms.xai.OpenAI')
  - tests/llms/test_minimax.py:14 — patch('mem0.llms.minimax.OpenAI')
  - tests/llms/test_lm_studio.py:11 — patch('mem0.llms.lmstudio.OpenAI')
  - tests/llms/test_vllm.py:13 — patch('mem0.llms.vllm.OpenAI'); Memory-level tests patch LlmFactory.create/EmbedderFactory.create (:191-194)
  - tests/llms/test_groq.py:11 — patch('mem0.llms.groq.Groq')
  - tests/llms/test_together.py:11 — patch('mem0.llms.together.Together')
  - tests/llms/test_litellm.py:11 — patch('mem0.llms.litellm.litellm') (module)
  - tests/llms/test_ollama.py:11 — patch('mem0.llms.ollama.Client')
  - tests/llms/test_gemini.py:13 — patch('mem0.llms.gemini.genai.Client')
  - tests/llms/test_sarvam.py:23 — patch('mem0.llms.sarvam.requests.post')
  - tests/llms/test_aws_bedrock.py:16 — patch('mem0.llms.aws_bedrock.boto3')
  - tests/embeddings/test_openai_embeddings.py:11 — patch('mem0.embeddings.openai.OpenAI')
  - tests/embeddings/test_azure_openai_embeddings.py:11 — patch('mem0.embeddings.azure_openai.AzureOpenAI')
  - tests/embeddings/test_gemini_embeddings.py:11 — patch('mem0.embeddings.gemini.genai.Client')
  - tests/embeddings/test_ollama_embeddings.py:14 — patch('mem0.embeddings.ollama.Client')
  - tests/embeddings/test_huggingface_embeddings.py:82 — patch('mem0.embeddings.huggingface.OpenAI'); SentenceTransformer patched at :12
  - tests/embeddings/test_lm_studio_embeddings.py:12 — patch('mem0.embeddings.lmstudio.OpenAI')
  - tests/embeddings/test_together_embeddings.py:14 — patch('mem0.embeddings.together.Together')
  - tests/embeddings/test_vertexai_embeddings.py:10 — patch('mem0.embeddings.vertexai.TextEmbeddingModel')
  - tests/embeddings/test_aws_bedrock_embeddings.py:12 — patch('mem0.embeddings.aws_bedrock.boto3.client')
  - tests/test_proxy.py:38 — patch('mem0.proxy.main.litellm'); also mem0.llms.openai.OpenAI (:30), mem0.embeddings.openai.OpenAI (:22)
  - tests/test_memory.py:91 — patch('mem0.utils.factory.EmbedderFactory.create'/'VectorStoreFactory.create'/'LlmFactory.create') -> MagicMock llm.generate_response
  - tests/memory/test_main.py:27 — patch('mem0.utils.factory.LlmFactory.create') (+EmbedderFactory.create :18)
  - tests/test_main.py:14 — patch('openai.OpenAI') + LlmFactory/EmbedderFactory (:22-24)
  - tests/rerankers/conftest.py:10 — patch('mem0.reranker.llm_reranker.LlmFactory')
  - mem0-ts/src/oss/tests/openai-structured-llm.test.ts:11 — jest.mock('openai', factory) — same pattern in deepseek/xai/minimax/together/litellm/openai-embedder/azure-embedder/huggingface-embedder/lmstudio-embedder tests
  - mem0-ts/src/oss/tests/anthropic-llm.test.ts:9 — jest.mock('@anthropic-ai/sdk')
  - mem0-ts/src/oss/tests/google-embedder.test.ts:10 — jest.mock('@google/genai')
  - mem0-ts/src/oss/tests/ollama-embedder.test.ts:18 — jest.mock('ollama')
  - mem0-ts/src/oss/tests/aws-bedrock-embedder.test.ts:20 — jest.mock('@aws-sdk/client-bedrock-runtime')
  - mem0-ts/src/oss/tests/factory.unit.test.ts:59 — jest.mock('../src/llms/<provider>') for every LLM/embedder class (factory routing tests)
  - mem0-ts/src/oss/src/rerankers/cohere.test.ts:3 — jest.mock('cohere-ai')

### Unusual / migration notes
- Provider abstraction: mem0/utils/factory.py LlmFactory.provider_to_class (18 LLM providers, line 42-60) and EmbedderFactory (11 embedders, line 154-164); every Memory LLM call goes through LLMBase.generate_response wrappers (call sites: mem0/memory/main.py:958 fact extraction w/ response_format json_object, :2028 procedural memory, mem0/memory/utils.py:182, mem0/reranker/llm_reranker.py:142, server/main.py:355). TS mirrors this in mem0-ts/src/oss/src/utils/factory.ts (LLMFactory 19 providers, EmbedderFactory 12).
- Python OpenAILLM silently switches to OpenRouter when OPENROUTER_API_KEY env is set (mem0/llms/openai.py:42), may drop 'model' for 'models'+'route' and add HTTP-Referer/X-Title headers.
- Most OpenAI-compatible providers (deepseek, xai, minimax, lmstudio, vllm, HF TEI embeddings) are OpenAI SDK clients with per-provider base_url config + *_API_BASE env; sarvam uses raw requests.post to /chat/completions.
- OpenAI base_url precedence (py llm): config.openai_base_url > OPENAI_BASE_URL > https://api.openai.com/v1 (explicit, so SDK default env handling is replicated). Embeddings also accept deprecated OPENAI_API_BASE.
- TS DEFAULT_MEMORY_CONFIG (mem0-ts/src/oss/src/config/defaults.ts:23) hardcodes llm baseURL 'https://api.openai.com/v1', which wins over the openai-node SDK's OPENAI_BASE_URL env fallback unless user overrides config.
- TS LiteLLM provider is NOT the litellm lib: it is OpenAI SDK pointed at a LiteLLM proxy (LITELLM_API_BASE, default http://localhost:4000).
- mem0/proxy/main.py: OpenAI-compatible 'Mem0().chat.completions.create' facade implemented on litellm.completion with stream passthrough, tools, response_format, base_url/api_key passthrough.
- Structured outputs: openai_structured uses client.beta.chat.completions.parse; core extraction passes response_format={'type':'json_object'}; gemini maps to response_schema; tools/tool_choice passthrough in most providers (marked TODO remove).
- Embeddings are heavily used (every add/search/update) across 11 py + 12 TS embedders; Bedrock LLM uses Converse + InvokeModel; Gemini via google-genai; Vertex embeddings via vertexai SDK / @google-cloud/aiplatform.
- No streaming in core library LLM providers (all non-stream); only proxy (param) and vercel-ai-sdk integration doStream stream.
- integrations/vercel-ai-sdk (@mem0/vercel-ai-provider) is an AI SDK LanguageModelV3 provider wrapper that delegates to createOpenAI(.chat/.completion/.languageModel)/createAnthropic/createCohere/createGroq/createGoogleGenerativeAI; its tests hit real APIs.
- server/ (self-hosted FastAPI) only configures mem0 Memory (openai/anthropic/gemini bundled; OPENAI_API_KEY, MEM0_DEFAULT_LLM_MODEL default 'gpt-5-mini'); no direct SDK calls.
- evaluation/ directory is empty at this sha (no embedchain/openmemory dirs present). Examples using agno/openai-agents/google-adk/strands frameworks only set model ids (not counted as SDK call sites).
- Python 3.9: requires-python >=3.10; CI matrix 3.10/3.11/3.12; mem0/vector_stores/qdrant.py:40,111 uses PEP 604 'X | None' annotations without __future__ import (TypeError at import on 3.9, qdrant is the default vector store); tests/test_oss_to_platform_migrate.py:207 same; transformers>=5.3.0 extra requires >=3.10. All files parse under 3.9 AST though.
- No `responses.*` API, no `previous_response_id`, no `response.model` reuse in core; `store` is opt-in (`mem0/llms/openai.py:133`). Core uses non-streaming chat completions only, with tools/tool_choice + `response_format` json_object; `reasoning_effort` / `max_completion_tokens` switching in `mem0/llms/base.py:77-162`.
