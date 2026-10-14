## abi/screenshot-to-code @ a8139779f7

- **Lang**: Python (FastAPI backend in `backend/`) + TypeScript (Vite/React frontend in `frontend/`)
- **Pkg manager**: poetry (backend) + pnpm (frontend, `packageManager: pnpm@10.32.1`)
- **Lockfiles**: `backend/poetry.lock`, `frontend/pnpm-lock.yaml`
- **Python req**: `^3.10` (source: `backend/pyproject.toml` `[tool.poetry.dependencies] python`; also poetry.lock metadata `python-versions = "^3.10"`; Dockerfile uses python:3.12.3)
- **py3.9 verdict: NO.** (1) Poetry constraint `^3.10` refuses install; (2) locked `google-genai 1.60.0` requires `>=3.10`; (3) `fs_logging/openai_input_compare.py:3` imports `typing.TypeAlias` (3.10+, ImportError on 3.9); (4) ~35 modules use PEP 604 `X | None` in runtime-evaluated function annotations (e.g. `routes/export.py:198`) with only 1 file using `from __future__ import annotations` -> TypeError at import on 3.9. openai 2.16.0 / anthropic 0.84.0 themselves allow >=3.9.
- **Node**: 22 (frontend/Dockerfile `node:22-bullseye-slim`; package.json engines `>=14.18.0`)

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| backend/agent/providers/factory.py:45 | openai-python | `AsyncOpenAI(api_key, base_url=openai_base_url)` | settings `openAiBaseURL` or env OPENAI_BASE_URL; forced None if IS_PROD | settings `openAiApiKey` or env OPENAI_API_KEY |
| backend/agent/providers/factory.py:58 | anthropic-python | `AsyncAnthropic(api_key)` | none (SDK implicit ANTHROPIC_BASE_URL) | settings `anthropicApiKey` or env ANTHROPIC_API_KEY |
| backend/agent/providers/factory.py:71 | google-genai | `genai.Client(api_key)` | none | settings `geminiApiKey` or env GEMINI_API_KEY |
| backend/asset_extraction.py:383 | google-genai | `genai.Client(api_key, http_options=v1alpha)` | none | param (gemini key) |
| backend/image_generation/replicate.py:81 | other:replicate-raw-httpx | `httpx.AsyncClient()` | hardcoded `https://api.replicate.com/v1` | settings `replicateApiKey` or env REPLICATE_API_KEY |
| backend/evals/asset_extraction_benchmark.py:378 (eval) | google-genai | `genai.Client(..., v1alpha)` | none | env GEMINI_API_KEY |

### Prod call sites (5)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| backend/agent/providers/openai.py:544 | openai-python | responses.create | yes | yes | param (Llm enum -> OPENAI_MODEL_CONFIG api_name) |
| backend/agent/providers/anthropic/provider.py:400 | anthropic-python | messages.stream | yes | yes | param (ANTHROPIC_MODEL_CONFIG) |
| backend/agent/providers/gemini.py:345 | google-genai | aio.models.generate_content_stream | yes | yes | param (_get_gemini_api_model_name) |
| backend/asset_extraction.py:266 | google-genai | aio.models.generate_content (JSON schema output) | no | yes | hardcoded `gemini-3.6-flash` |
| backend/image_generation/replicate.py:83 | other:replicate-raw-httpx | http:POST /v1/models/{m}/predictions, /v1/predictions + poll | no | yes | hardcoded (z-image-turbo default, flux-2-klein-4b, p-image-edit, remove-bg version) |

Wrappers: the three LLM sessions are invoked from `agent/engine.py:262` (`session.stream_turn`) after `create_provider_session` (`agent/engine.py:336`). Replicate `_run_prediction` is reached via `call_replicate` (image gen), `edit_image`, `remove_background`.

### Non-prod call sites (1)
- eval: `backend/evals/asset_extraction_benchmark.py:390` google-genai aio.models.generate_content
- (script `backend/run_image_generation_evals.py:143` calls the `call_replicate_model` wrapper - not a separate SDK invocation)

### Models seen
OpenAI: gpt-5.4-mini, gpt-5.4-2026-03-05, gpt-5.5, gpt-5.6-sol, gpt-5.6-terra, gpt-6.1-sol (effort variants none/low/medium/high/xhigh/max encoded in enum values like `"gpt-5.5 (high thinking)"`). Anthropic: claude-sonnet-4-6, claude-opus-5-5, claude-opus-5, claude-opus-4-8, claude-fable-5 (effort low..max). Gemini: gemini-3-flash-preview, gemini-3.1-pro-preview, gemini-3.5-flash, gemini-3.6-flash. Replicate: prunaai/z-image-turbo, black-forest-labs/flux-2-klein-4b, prunaai/p-image-edit. Source: `backend/llm.py`, `backend/agent/providers/anthropic/provider.py:58-77`, `backend/agent/providers/gemini.py:44`.

### Tests
- Command: `cd backend && poetry run pytest` (root `package.json` `test:backend`; AGENTS.md/CLAUDE.md; `pytest.ini` asyncio_mode=auto, testpaths=tests). Frontend: `pnpm -C frontend test` (jest + ts-jest; `qa.test.ts` E2E puppeteer only with RUN_E2E=true). No CI workflows in `.github/`.
- Framework: pytest (+pytest-asyncio); 40 test files in `backend/tests/`.
- Needs network: no - no test calls a real LLM; keys set to `"fake-key"` via monkeypatch.
- Mocking: hand-rolled fakes, no respx/vcr:
  - `tests/test_openai_provider_session.py:29` `_FakeOpenAIClient` -> `.responses.create(**kw)` records kwargs, returns empty async stream (asserts on request params).
  - `tests/test_asset_extraction.py:108` `monkeypatch.setattr("asset_extraction.genai.Client", FakeClient)`.
  - `tests/test_anthropic_many_image_limit.py:58`, `tests/test_gemini_provider_session.py:15` sessions built with `client=object()` (conversion only).
  - `tests/test_agent_engine.py:76`, `tests/test_agent_runs.py:576` patch `agent.engine.create_provider_session` -> fake ProviderSession.
  - `tests/test_tool_image_outputs.py:172` patch `agent.providers.gemini.httpx.AsyncClient`; `tests/test_agent_runs.py:364` patch `httpx.AsyncClient`.
  - Replicate: `tests/test_image_generation_replicate.py:55` patch `replicate.call_replicate_model`/`call_replicate_version`; `tests/test_batching.py` patch `agent.tools.runtime.remove_background_once`/`edit_image_once`.

### Unusual / migration notes
- Custom provider abstraction (`ProviderSession.stream_turn`) with OpenAI-format prompt messages converted per provider. Agentic tool loop (edit_file, generate_images, edit_images, remove_backgrounds, extract_assets, screenshot_preview).
- OpenAI uses **Responses API** streaming with strict function tools, `reasoning={effort, summary:"auto"}` (efforts include `none`, `xhigh`, `max`), `input_image` detail `original`, `prompt_cache_retention="24h"`, `max_output_tokens=50000`. Stateless replay of output items (no `previous_response_id`/`store`). Parses many `response.*` stream events incl. reasoning summary and function-call arg deltas.
- Anthropic: `messages.stream` + `get_final_message()`, adaptive thinking + `output_config.effort`, top-level `cache_control`, tools, many-image dimension limiting.
- Gemini: streaming with thinking_config/include_thoughts; asset extraction uses structured JSON (`response_schema` pydantic) on v1alpha.
- OPENAI_BASE_URL user-overridable from the UI settings dialog unless IS_PROD; keys also user-supplied per request.
- Image generation is Replicate raw HTTP (not OpenAI images) - not a Router surface.
- Provider-specific exception handling: `routes/generate_code.py:669/682/697` catches `openai.AuthenticationError/NotFoundError/RateLimitError`.
- Cost tracking per model via `costs/pricing.py` keyed by API model name; `GENERATION_MAX_COST_USD = 3.0` ceiling.
- `langfuse` dependency declared but unused. Frontend makes no direct LLM calls.
