## openai/swarm @ 6af0b4c

- **Lang**: Python only. **Pkg manager**: pip/setuptools (`pyproject.toml` build-system only; metadata + deps in `setup.cfg`). **Lockfile**: none. **Node**: n/a.
- **python_requires**: `>=3.10` (source: `setup.cfg` `[options] python_requires`). No CI config (no `.github/`).
- **Py3.9 verdict: NO.** `swarm/core.py:72` uses a `match` statement -> SyntaxError at import of `swarm` on 3.9, so the library and all tests fail. (Other syntax is OK on 3.9: `list[ChatCompletion]` annotation in `tests/mock_client.py:56` is fine since PEP 585 is 3.9.)
- Deps: `openai>=1.33.0`, numpy, pytest, requests, tqdm, pre-commit, instructor (pydantic implied via openai).

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| swarm/core.py:29 (prod) | openai-python | `OpenAI()` only when `Swarm(client=None)`; otherwise injected client | none (OPENAI_BASE_URL implicit) | env OPENAI_API_KEY (implicit) |
| examples/support_bot/prep_data.py:9 | openai-python | `OpenAI()` | none | implicit env |
| examples/support_bot/main.py:10 | openai-python | `OpenAI()` | none | implicit env |
| examples/support_bot/customer_service.py:10 | openai-python | `OpenAI()` | none | implicit env |
| examples/triage_agent/evals_util.py:6 | openai-python (+instructor) | `instructor.from_openai(OpenAI())` | none | implicit env |
| examples/customer_service_streaming/prep_data.py:5 | openai-python | `OpenAI()` | none | implicit env |
| examples/customer_service_streaming/configs/tools/query_docs/handler.py:7 | openai-python | `OpenAI()` | none | implicit env |
| examples/customer_service_streaming/src/swarm/swarm.py:22 | openai-python | `OpenAI()` (legacy local `Swarm` class) | none | implicit env |

### Prod call sites (1)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| swarm/core.py:69 | openai-python | chat.completions.create (tools, tool_choice, parallel_tool_calls) | both (`stream` param: False via `run`, True via `run_and_stream`) | no | param: `model_override or agent.model`; `Agent.model` default hardcoded `'gpt-4o'` (swarm/types.py:16) |

Wrapper: `Swarm.get_chat_completion` (core.py:32) is the single choke point; called from `run_and_stream` (core.py:171) and `run` (core.py:260). Callers of `Swarm.run` (not SDK call sites): swarm/repl/repl.py:73, examples/basic/*, examples/*/evals*.py, examples/airline/evals/eval_utils.py:27.

### Non-prod call sites (19)
- **examples (embeddings.create, model `text-embedding-3-large`)**: support_bot/prep_data.py:34 (script), support_bot/main.py:23, support_bot/customer_service.py:26, customer_service_streaming/prep_data.py:31 (script), customer_service_streaming/configs/tools/query_docs/handler.py:20
- **examples chat.completions.create**: customer_service_streaming/src/utils.py:23 (stream=True) and :35 (non-stream) -- `get_completion` wrapper, default model `gpt-4-0125-preview`, used by local_engine/assistants_engine/assistants/run/eval_function; customer_service_streaming/src/evals/eval_function.py:45 (eval, `gpt-4-turbo-preview`)
- **eval via instructor**: triage_agent/evals_util.py:15 `chat.completions.create_with_completion(response_model=BoolEvalResult, model='gpt-4o')`
- **Assistants API (beta) in customer_service_streaming/src/swarm/engines/assistants_engine.py**: :23, :28 beta.threads.create; :66 beta.assistants.list; :74 beta.assistants.create (model from configs/assistants/user_interface/assistant.json = `gpt-4-0125-preview`); :141 beta.threads.messages.create; :148 beta.threads.runs.create; :155 beta.threads.runs.retrieve; :177, :229 beta.threads.messages.list; :214 beta.threads.runs.submit_tool_outputs

### Models seen
gpt-4o (default Agent model, evals), text-embedding-3-large, gpt-4 (unused const), gpt-4-0125-preview, gpt-4-turbo-preview

### Tests
- **Command**: `pytest tests/` from repo root (no CI/Makefile; pytest is in install_requires; `tests/__init__.py` exists and test_core imports `tests.mock_client`, so run from root). Files: tests/test_core.py (4 tests), tests/test_util.py (function_to_json only).
- **Framework**: pytest. **Network**: no -- fully mocked, no API key needed (but `from openai import OpenAI` must be importable).
- **Mocking: dependency injection, no `patch()`**:
  - tests/mock_client.py:44 `MockOpenAIClient` -- `self.chat = MagicMock(); self.chat.completions = MagicMock()`.
  - :54 `set_response` -> `self.chat.completions.create.return_value = response`; :61 `set_sequential_responses` -> `.create.side_effect = [...]`; :63 `assert_create_called_with`.
  - :8 `create_mock_response(message, function_calls=[], model="gpt-4o")` builds real `openai.types.chat.ChatCompletion` / `Choice` / `ChatCompletionMessage` / `ChatCompletionMessageToolCall(id="mock_tc_id")` objects.
  - :68-96 module-level code runs on import (throwaway client, two `create()` calls, prints) -- harmless.
  - tests/test_core.py:10 fixture `mock_openai_client`; injected via `Swarm(client=mock_openai_client)` at :22, :62, :101, :139.
  - Only non-streaming `Swarm.run` is covered; `run_and_stream` / chunk merging is untested. The mocked `create` accepts any kwargs, so request shape (tools, tool_choice=None, parallel_tool_calls) is not asserted.

### Unusual / migration-relevant
- **Injectable client**: `Swarm(client=...)` (core.py:27-30). Migration = pass `OpenAI(base_url=<router>, api_key=...)` (or set `OPENAI_BASE_URL`) -- no library changes needed. Duck-typed: only `.chat.completions.create` is used.
- Tool calling: tools built from Python function signatures (`swarm/util.py` `function_to_json`); `context_variables` param stripped from schemas. `tools=None` and `tool_choice=None` are sent explicitly when unset; `parallel_tool_calls` only sent when tools exist.
- **Streaming specifics** (core.py:181-188): `json.loads(chunk.choices[0].delta.json())` (deprecated pydantic v1 `.json()`), `delta["role"]` lookup, tool-call merging by `tool_calls[0]["index"]` assuming one tool call per chunk (`swarm/util.py:21-28`). Breaks on chunks with empty `choices` (e.g., usage-only final chunk) or deltas that lack `role`.
- **History contains non-standard keys** replayed to the API: `sender` on assistant messages (core.py:158, 270-272 via `model_dump_json`) and `tool_name` on tool messages (core.py:109, 129), plus null fields like `refusal`, `function_call`, `audio`. OpenAI ignores them; strict OpenAI-compatible routers/providers may 400.
- No async client usage; no Responses API; no Azure; no env var reads in code.
- `examples/customer_service_streaming` is a legacy, standalone prototype using the Assistants API (beta threads/runs/assistants) -- not supported by chat-only routers.
- Embeddings in support_bot / customer_service_streaming examples (qdrant on localhost).
