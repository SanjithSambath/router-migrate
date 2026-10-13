## simonw/llm @ 05d7ae7ded

- **Lang**: Python. **Pkg manager**: pip/setuptools. Uses a PEP 735 `[dependency-groups] dev`, so you need pip ≥ 25.1 for `pip install . --group dev`. The Justfile uses `uv run`. **Lockfiles**: none.
- **Python req**: `>=3.10` (source: `pyproject.toml requires-python`; classifiers 3.10–3.14; CI `test.yml` matrix 3.10–3.14 × ubuntu/macos/windows; black `target-version py310`).
- **py3.9 verdict: NO.** PEP 604 unions appear in runtime signatures without `from __future__ import annotations` (e.g. `llm/default_plugins/openai_models.py:500` `Iterable[str | bytes], key: str | None`; pydantic field `int | None` in tests/conftest.py MockModel). The deps `httpx2`, `openai>3`, `httpx2-pytest>=2` and `sqlite-utils>=4` are recent and likely 3.10+.
- **Node**: n/a.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| llm/default_plugins/openai_models.py:1369 | openai-python | `openai.OpenAI(**kwargs)` in `_Shared.get_client` (:1346) | `api_base` from extra-openai-models.yaml or `llm openai endpoint URL`; else none (SDK `OPENAI_BASE_URL` implicit) | `get_key` (llm keys.json alias / `OPENAI_API_KEY`); `"DUMMY_KEY"` when `needs_key is None` |
| llm/default_plugins/openai_models.py:1367 | openai-python | `openai.AsyncOpenAI(**kwargs)` (same factory, `async_=True`) | same | same |
| llm/default_plugins/openai_models.py:508 | openai-python | `openai.OpenAI(api_key=key)` (embeddings only) | none | llm key `openai` / `OPENAI_API_KEY` |
| llm/default_plugins/openai_models.py:868 | raw-http-openai | `httpx2.get` | hardcoded `https://api.openai.com/v1/models` | same |

`get_client` also passes `default_headers=headers` (from YAML or `--header`), `http_client=logging_client()` when `LLM_OPENAI_SHOW_RESPONSES` is set, and, if set in YAML, `api_type` / `api_version` / `engine`. The `openai>=1` constructor does not accept those last three, so that legacy Azure path would raise TypeError.

### Prod call sites (13)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| openai_models.py:1431 | openai-python | chat.completions.create | yes | no | `model_name or model_id` |
| openai_models.py:1484 | openai-python | chat.completions.create | no | no | same |
| openai_models.py:1549 | openai-python | chat.completions.create | yes | yes | same |
| openai_models.py:1600 | openai-python | chat.completions.create | no | yes | same |
| openai_models.py:2542 | openai-python | responses.create | yes | no | same |
| openai_models.py:2667 | openai-python | responses.create | no | no | same |
| openai_models.py:2789 | openai-python | responses.create | yes | yes | same |
| openai_models.py:2916 | openai-python | responses.create | no | yes | same |
| openai_models.py:2988 | openai-python | completions.create (legacy) | yes | no | same (gpt-3.5-turbo-instruct / YAML `completion: true`) |
| openai_models.py:3007 | openai-python | completions.create (legacy) | no | no | same |
| openai_models.py:509 | openai-python | embeddings.create | no | no | `openai_model_id` (+`dimensions`) |
| openai_models.py:763 | openai-python | models.list | no | no | n/a (`llm openai endpoint URL --models`) |
| openai_models.py:868 | raw-http-openai | GET /v1/models | no | no | n/a (`llm openai models`) |

Counts: chat.completions 4 (2 async), responses 4 (2 async), legacy completions 2, embeddings 1, models.list 1, raw HTTP models 1.
Classes: `Chat` :1405, `AsyncChat` :1523, `Responses` :2450, `AsyncResponses` :2696, `Completion(Chat)` :2943, `OpenAIEmbeddingModel` :489.
Routing:
- Chat/AsyncChat: gpt-4o*, gpt-4.1*, 3.5, 4, 4-turbo.
- Responses/AsyncResponses: o1, o3, o3-mini, o4-mini, gpt-5*, 5.1, 5.2, 5.4, 5.5, 5.6-*, gpt-6-*. `-o chat_completions 1` delegates these to Chat.
- Other llm modules make no LLM calls. `httpx2` in `llm/cli.py:257,4246` and `llm/models.py:96,111` only fetches fragments, templates and attachments.

### Non-prod call sites
- None in repo code. Docs mention `extra-openai-models.yaml` with `api_base` (docs/other-models.md).

### Models seen
- Chat class: gpt-4o, gpt-4o-mini, gpt-4.1, gpt-4.1-mini, gpt-4.1-nano, gpt-3.5-turbo, gpt-3.5-turbo-16k, gpt-4, gpt-4-turbo, gpt-4-turbo-2024-04-09.
- Responses class: o1, o1-2024-12-17, o3-mini, o3, o4-mini, the gpt-5 / 5-mini / 5-nano family (+ dated variants), gpt-5.1, gpt-5.2, gpt-5.2-chat-latest, gpt-5.2-pro, the gpt-5.4 / mini / nano family (+ dated), gpt-5.5, gpt-5.5-2026-04-23, gpt-5.6-sol/terra/luna, gpt-6-astra/sol/luna.
- Completion class: gpt-3.5-turbo-instruct.
- Embeddings: text-embedding-ada-002, text-embedding-3-small, text-embedding-3-large (512/256/1024-dim variants).

### Tests
- **Command**: CI runs `pip install . --group dev` then `python -m pytest -vv`. The Justfile uses `uv run pytest`. CI also runs cog --check, black, mypy, ruff and `tests/test-llm-load-plugins.sh`; that last step installs plugins from PyPI and is not pytest. **Framework**: pytest + pytest-asyncio (pytest.ini `asyncio_default_fixture_loop_scope = function`) + syrupy.
- **Needs network?** No. All OpenAI traffic is mocked at the HTTP layer or replayed from cassettes.
- **Mocking (exact)**:
  - **httpx2-pytest `httpx2_mock` fixture** (≈224 refs). Fixtures in tests/conftest.py:
    - `mocked_openai_chat` :281 — POST https://api.openai.com/v1/chat/completions
    - `mock_openai_responses` :296 — factory for POST /v1/responses, default model gpt-5.6-luna
    - `mocked_openai_responses` :340
    - `mocked_openai_chat_returning_fenced_code` :346
    - `mocked_openai_chat_stream` :390 — SSE via `IteratorStream`
    - `mocked_openai_completion` :400 — /v1/completions
    - logprobs variants :497 and :508
    - `mocked_localai` :542 — http://localai.localhost/chat/completions and /completions, the YAML `api_base` path
  - Heavy direct users: tests/test_openai_endpoint.py (71 refs; includes https://example.test/v1/responses), test_openai_responses.py (50), test_cli_openai_models.py (29, includes /v1/models), test_openai_messages.py (24).
  - **pytest-recording (vcrpy) cassettes** in `tests/cassettes/{test_openai_responses (6), test_tools (2), test_tools_streaming (4)}/*.yaml`.
    - Markers: `@pytest.mark.vcr` at test_openai_responses.py:1157ff and test_tools.py:214, 278, 498; `@pytest.mark.vcr(record_mode="none")` at test_tools_streaming.py:12ff.
    - `vcr_config` (conftest.py:576) filters the `Authorization` header.
    - Key comes from `PYTEST_OPENAI_API_KEY` env or `"badkey"`.
  - **Fake models** for non-OpenAI paths: `MockModel` / `AsyncMockModel` / `EmbedDemo` and `llm_echo.Echo`, registered through pluggy in autouse fixtures (conftest.py:242, :265). The autouse `env_setup` (:72) sets `LLM_USER_PATH` to a tmp dir.
  - No `patch("openai...")`.

### Unusual / migration notes
- **Router integration with no code change:** add an `extra-openai-models.yaml` entry with `api_base` (+ `api_key_name`, `headers`), use `llm openai endpoint URL`, or set `OPENAI_BASE_URL`. The env var works for every path, including embeddings and `llm openai models`, except that the latter's URL is hardcoded and ignores the env.
- **Embeddings** use their own `OpenAI(api_key=)`, so YAML `api_base` / headers do not apply.
- **Responses API**: `store=False` on every call, `include=["reasoning.encrypted_content"]`, and encrypted reasoning items are echoed back in input (no `previous_response_id`). Also `text.format` json_schema (strict False), function tools, and server-side tools `WebSearch` (:1664) and `CodeInterpreter` (:1809) that mutate kwargs via `prepare_request`. A router must support `/v1/responses` with these fields, or users must force `-o chat_completions 1`.
- **Chat**: `stream_options.include_usage`, tools, `response_format` json_object/json_schema(name "output"), service_tier, reasoning_effort, verbosity, audio/vision attachments.
- `response.response_json` stores the full `model_dump()` (including `model`) in the logs DB.
- `logging_client()` (llm/utils.py:148) is a sync `httpx2.Client` but is also handed to `AsyncOpenAI` when debugging is on.
- The `httpx2` + `openai>3` dependency names suggest the pristine env needs the newest openai major and `httpx2-pytest`. Cassettes must replay over the httpx2 transport.
