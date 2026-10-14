## microsoft/TaskWeaver @ d44ddef23f

- **Lang**: Python only. **Pkg manager**: pip/setuptools (`setup.py` reads `requirements.txt`). **Lockfiles**: none.
- **Python req**: `>=3.10` (source: `setup.py:83 python_requires`; classifiers only say "Python :: 3"; CI matrix `.github/workflows/pytest.yml` = `["3.11"]`).
- **py3.9 verdict: NO.** Install metadata blocks it, and it would also fail at import: `taskweaver/module/tracing.py:3` does `from typing import ParamSpec` (3.10+; imported by `taskweaver/role/role.py`), and `taskweaver/ces/environment.py:96` has `kernel_name: str | None` in a runtime-evaluated signature with no `from __future__ import annotations`. Dependency pins (`openai>=1.42`, `pandas>=2.0`, `numpy>=1.24.2`) would be fine on 3.9.
- **Node**: n/a (only `website/` docusaurus, not relevant).

### Config / base_url plumbing
- `LLMModuleConfig` (`taskweaver/llm/base.py:30`): `llm.api_type` (default `openai`; also `azure`, `azure_ad`, `azure_ml`, `ollama`, `google_genai`, `qwen`, `zhipuai`, `groq`, `anthropic`), `llm.embedding_api_type` (default `sentence_transformers`), `llm.api_base`, `llm.api_key`, `llm.model`, `llm.embedding_model`, `llm.response_format` (default `json_object`), `llm.use_mock`.
- Per-provider configs read `llm.<name>.<key>` and fall back to the shared `llm.<key>`. For OpenAI, name = api_type (`llm.openai.*`, `llm.azure.*`, `llm.azure_ad.*`).
- Resolution order (`taskweaver/config/config_mgt.py:85-97`): in-memory config, then the env var (`llm.api_base` becomes `LLM_API_BASE`), then `project/taskweaver_config.json`.
- OpenAI: `OpenAI(base_url=config.api_base, api_key=config.api_key)`. The `api_base` default is `https://api.openai.com/v1` and is always passed explicitly, so the SDK's `OPENAI_BASE_URL` env is never used. Azure: `AzureOpenAI(azure_endpoint=api_base, api_version=llm.azure.api_version [2024-06-01], api_key | azure_ad_token_provider)`.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| taskweaver/llm/openai.py:131 | openai-python | `OpenAI(base_url=, api_key=)` | config `llm.openai.api_base`, falling back to `llm.api_base` (default api.openai.com/v1) | config `llm.openai.api_key`, falling back to `llm.api_key` |
| taskweaver/llm/openai.py:136 | azure-openai | `AzureOpenAI(api_version, azure_endpoint, api_key)` | `llm.azure.api_base`, falling back to `llm.api_base` | `llm.api_key` |
| taskweaver/llm/openai.py:142 | azure-openai | `AzureOpenAI(..., azure_ad_token_provider=)` | `llm.azure_ad.api_base` | AAD via msal / DefaultAzureCredential |
| taskweaver/llm/anthropic.py:34 | anthropic-python | `Anthropic(api_key=)` | none (SDK env implicit) | bare `api_key` key, default `ANTHROPIC_API_KEY` env, then `llm.api_key` |
| taskweaver/llm/groq.py:43 | groq | `Groq(api_key=)` | none | `llm.groq.api_key`, falling back to `llm.api_key` |
| taskweaver/llm/zhipuai.py:68 | other:zhipuai | `ZhipuAI(base_url=, api_key=)` | `llm.zhipuai.api_base` (default open.bigmodel.cn/api/paas/v4) | `llm.api_key` |
| taskweaver/llm/google_genai.py:47/67 | google-genai (legacy `google.generativeai`) | `genai.configure` + `GenerativeModel` | none | `llm.api_key` |
| taskweaver/llm/qwen.py:48 | other:dashscope | module-global `dashscope.api_key` | none | `llm.api_key` |
| taskweaver/llm/ollama.py:200 | ollama (raw `requests`) | `requests.Session` | `llm.ollama.api_base` (default localhost:11434) | none |
| taskweaver/llm/azure_ml.py:79 | other:requests-azureml | `requests.Session` | `llm.azure_ml.api_base` (+`/score`) | Bearer `llm.api_key` |

### Prod call sites (18)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| taskweaver/llm/openai.py:222 | openai-python | chat.completions.create | param (default True) | no | `llm.<type>.model`, falling back to `llm.model` (default gpt-4) |
| taskweaver/llm/openai.py:297 | openai-python | embeddings.create | no | no | `llm.embedding_model` (default text-embedding-ada-002) |
| taskweaver/llm/anthropic.py:80 | anthropic-python | messages.stream | yes | no | bare `model` key (default claude-3-opus-20240229) |
| taskweaver/llm/anthropic.py:85 | anthropic-python | messages.create | no | no | same |
| taskweaver/llm/groq.py:62 | groq | chat.completions.create | no | no | `llm.groq.model`, falling back to `llm.model` |
| taskweaver/llm/zhipuai.py:96 | other:zhipuai | chat.completions.create | param | no | `llm.model` (default glm-4) |
| taskweaver/llm/zhipuai.py:149 | other:zhipuai | embeddings.create | no | no | `llm.embedding_model` (default embedding-2) |
| taskweaver/llm/google_genai.py:145 | google-genai | GenerativeModel.generate_content | no | no | `llm.model` (default gemini-pro) |
| taskweaver/llm/google_genai.py:148 | google-genai | GenerativeModel.generate_content | yes | no | same |
| taskweaver/llm/google_genai.py:154 | google-genai | embed_content | no | no | `llm.embedding_model` |
| taskweaver/llm/qwen.py:60 | other:dashscope | Generation.call | yes (always) | no | `llm.model` (default qwen-max-1201) |
| taskweaver/llm/qwen.py:84 | other:dashscope | TextEmbedding.call | no | no | `llm.embedding_model` |
| taskweaver/llm/ollama.py:105 / :113 | ollama (raw HTTP) | POST /api/chat | no / yes | no | `llm.model` (default llama2) |
| taskweaver/llm/ollama.py:155 / :163 | ollama (raw HTTP) | POST /api/generate (fallback) | no / yes | no | `llm.model` |
| taskweaver/llm/ollama.py:189 | ollama (raw HTTP) | POST /api/embeddings | no | no | `llm.embedding_model` |
| taskweaver/llm/azure_ml.py:80 | other:requests | POST <endpoint>/score | no | no | endpoint (no model param) |

Counts by SDK: openai-python 2 (1 chat, 1 embeddings); anthropic-python 2; groq 1; zhipuai 2; google-genai 3; dashscope 2; ollama raw HTTP 5 (all through the `_request_api` wrapper at ollama.py:202); azureml raw HTTP 1.
App-side wrappers are `LLMApi.chat_completion` / `chat_completion_stream` / `get_embedding(s)` (`taskweaver/llm/__init__.py:156,193,390,393`). Their callers are planner.py:284, code_generator.py:381, code_generator_plugin_only.py:149 (tools), code_generator_cli_only.py:108, memory/compression.py:104, memory/experience.py:133, and ext_role/image_reader.py:84.

### Non-prod call sites
- example (project template plugins, zipped into the package): `project/plugins/paper_summary.py:47` (langchain AzureChatOpenAI/ChatOpenAI.invoke), `project/plugins/sql_pull_data.py:60` (LCEL chain .invoke)
- eval: `auto_eval/evaluator.py:136`, `:244` (langchain .invoke on AzureChatOpenAI / ChatOpenAI / ChatGoogleGenerativeAI / AzureMLChatOnlineEndpoint from `config_llm`, line 43)
- test data (loaded, not executed): `tests/unit_tests/data/plugins/paper_summary.py:46`, `tests/unit_tests/data/plugins/sql_pull_data.py:54`
- `scripts/document_indexer.py` uses local HuggingFaceEmbeddings only (no API).

### Models seen
gpt-4 (default), gpt-4-1106-preview (project/taskweaver_config.json), text-embedding-ada-002, claude-3-opus-20240229, llama2, gemini-pro, qwen-max-1201, glm-4, embedding-2, all-mpnet-base-v2 / all-MiniLM-L6-v2 (local sentence-transformers)

### Tests
- **Command (CI)**: `pytest tests/unit_tests -v --junitxml=... --cov=com` (pytest.ini testpaths=tests). **Framework**: pytest.
- **Needs network?** Partial. The tests that run in CI don't hit any LLM. Tests that would need a real API or HF downloads (test_embedding.py all `skipif(True)`; test_multi_llm, test_experience, test_plugin_selector, test_role, test_tracing, test_environment use `skipif(IN_GITHUB_ACTIONS)`) only run locally.
- **Mocking**: no SDK/HTTP patching. TaskWeaver ships its own `MockApiService` (`taskweaver/llm/mock.py`), enabled by `llm.use_mock=True` (`taskweaver/llm/__init__.py:101`). Tests use it through the `app_config` marker: `tests/unit_tests/test_llm_base.py:12` (`llm.mock.mode=playback_only`) and `:29` (`mode=fixed`, `llm.mock.fixed_chat_responses`). The `app_injector` fixture in `tests/unit_tests/conftest.py:18` injects a dummy `llm.api_key`. `vcrpy` is in requirements.txt but unused.

### Unusual / migration notes
- Clean provider abstraction: every SDK call lives in `taskweaver/llm/*.py`. A router migration is mostly `api_type=openai` with `llm.api_base` pointing at the router. That is already supported with config only; no code change is needed for chat.
- Every OpenAI chat call sends `response_format={"type":"json_object"}` by default. Planner and code generator can send `json_schema` (strict, name "response"). `seed`, `stop=["<EOS>"]`, `frequency/presence_penalty`, and optionally `extra_body.guided_json` (vLLM) are also sent. Tools + `tool_choice` are passed in plugin-only mode.
- Embeddings default to local sentence-transformers. OpenAI embeddings are used only if `llm.embedding_api_type=openai`.
- Anthropic quirk: config keys are unprefixed (`model`, `api_key`, so env `MODEL` / `API_KEY`), and `llm.model` is ignored. `anthropic` is not in requirements.txt.
- Azure AD path (msal device-flow / DefaultAzureCredential token provider).
- Multi-LLM aliases via `ext_llms.llm_configs` (per-role api_type/base/key/model).
- `project/taskweaver_config.json` contains an `llm.api_key` value (not printed).
