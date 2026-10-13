## simonw/llm-anthropic @ b66cdf112c

- **Lang**: Python only (single module `llm_anthropic.py`, 2147 lines; plugin for `llm`).
- **Pkg manager**: uv (CI `astral-sh/setup-uv`, `[tool.uv] package=true`, `[dependency-groups] dev`); publish workflow uses pip. **Lockfile**: none committed.
- **Python req**: `>=3.10` (pyproject `requires-python`); CI matrix 3.10-3.14 (.github/workflows/test.yml, publish.yml).
- **py3.9 verdict: NO.** requires-python excludes it; runtime-evaluated PEP 604 unions in pydantic option models (`int | None`, `list[str] | str | None`, llm_anthropic.py:844-898, 949, 959) raise TypeError at import on 3.9; deps `llm>=0.36`, `anthropic>=1.5,<2`, `vcrpy>=8.3` also target >=3.10 era.
- **Node**: n/a.
- **Deps**: `llm>=0.36`, `anthropic>=1.5,<2`. Dev: pytest, pytest-recording, vcrpy>=8.3, pytest-asyncio, cogapp, inline-snapshot[black].

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| llm_anthropic.py:114 | anthropic-python | `Anthropic(api_key=_api_key(key))` (`llm anthropic models`) | none | --key / llm keys `anthropic` / env ANTHROPIC_API_KEY |
| llm_anthropic.py:138 | anthropic-python | `Anthropic(api_key=_api_key(key))` (`llm anthropic refresh`) | none | same |
| llm_anthropic.py:1860 | anthropic-python | `Anthropic(api_key=self.get_key(key), base_url=self.base_url)` (count_tokens) | model ctor `base_url` (default None -> SDK env ANTHROPIC_BASE_URL) | llm KeyModel.get_key / env ANTHROPIC_API_KEY |
| llm_anthropic.py:1864 | anthropic-python | same (execute) | same | same |
| llm_anthropic.py:2009 | anthropic-python | `AsyncAnthropic(...)` (async count_tokens) | same | same |
| llm_anthropic.py:2014 | anthropic-python | `AsyncAnthropic(...)` (async execute) | same | same |

### Prod call sites (5)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| llm_anthropic.py:220 | anthropic-python | models.list (`client.models.with_raw_response.list(limit=1000, after_id)`, paginated, raw JSON) | no | no | n/a |
| llm_anthropic.py:1799 | anthropic-python | beta.messages.count_tokens (when betas) | no | sync + awaited async (shared helper `_count_tokens`) | param: `self.claude_model_id` |
| llm_anthropic.py:1800 | anthropic-python | messages.count_tokens | no | sync + async | param |
| llm_anthropic.py:1880 | anthropic-python | messages.stream (or beta.messages.stream if `betas`) ; `get_final_message()` at 1992 | yes (always) | no | param |
| llm_anthropic.py:2027 | anthropic-python | messages.stream (or beta.messages.stream) ; `await get_final_message()` at 2138 | yes (always) | yes | param |

Counts: messages.stream x2 (sync/async; dynamic beta), messages.count_tokens x1 + beta.messages.count_tokens x1 (each used sync+async), models.list x1. No messages.create anywhere.

Wrappers: `_count_tokens` (1790) is the single token-counting helper used by `ClaudeMessages.count_tokens`, `AsyncClaudeMessages.count_tokens`, `-o count_tokens 1`, and the `llm anthropic count` CLI command. `fetch_models` (212) backs `llm anthropic models` / `refresh`.

### Non-prod call sites
None directly against the SDK. Tests drive the plugin via `llm.get_model(...).prompt(...)` / CliRunner; README.md and demo.md are usage docs only.

### Models seen
Hardcoded registry (`register_builtin_models`, llm_anthropic.py:383-~800): claude-3-opus-20240229/-latest, claude-3-sonnet-20240229, claude-3-haiku-20240307, claude-3-5-sonnet-20240620/-20241022/-latest, claude-3-5-haiku-latest, claude-3-7-sonnet-20250219/-latest, claude-opus-4-0, claude-sonnet-4-0, claude-opus-4-1-20250805, claude-sonnet-4-5, claude-haiku-4-5-20251001, claude-opus-4-5-20251101, claude-opus-4-6, claude-sonnet-4-6, claude-opus-4-7, claude-opus-4-8, claude-fable-5, claude-sonnet-5, claude-opus-5, claude-fable-5-1 (+ dotted aliases). Extra models auto-registered from `anthropic.types.Model` Literal and cached `/v1/models`.

### Tests
- **Command**: `uv run pytest` (CI test.yml; also `uv run cog --check README.md`). Publish workflow: `python -m pytest`.
- **Framework**: pytest + pytest-asyncio (asyncio_mode=strict) + pytest-recording/vcrpy + inline-snapshot.
- **Network**: No. 32 `@pytest.mark.vcr` tests replay from `tests/cassettes/test_anthropic/<test>.yaml` (32 cassettes). pytest-recording default `record_mode=none` -> offline; recording requires `--record-mode=...` plus `PYTEST_ANTHROPIC_API_KEY`. No real keys in cassettes (`x-api-key` absent).
- **Mocking**:
  - tests/conftest.py:5 `vcr_config` -> `filter_headers=["X-API-KEY"]`, `decode_compressed_response=True`.
  - tests/test_anthropic.py:21 key = env `PYTEST_ANTHROPIC_API_KEY` or dummy `"sk-..."`.
  - Fake-client monkeypatches on module attrs: `monkeypatch.setattr(llm_anthropic, "Anthropic", ...)` at :138, :2406, :2620; `"AsyncAnthropic"` at :187; generic `_patch_fake_stream` helper at :2086/:2130 (FakeStream/FakeAsyncStream/FakeMessages with synthetic chunks + final message).
  - FakeClient passed directly to `model._count_tokens` at :2680, :2725.
  - `monkeypatch.setattr(llm_anthropic, "sdk_model_ids", ...)` at :2493, :2520, :2577, :2589.
  - Note: vcrpy cassettes are keyed on httpx requests to api.anthropic.com; a base_url change would break cassette matching (default match_on includes host/path).

### Unusual / migration-relevant
- Always uses streaming transport (`messages.stream` context manager + `get_final_message()`), even for non-stream llm calls.
- Beta surface switched dynamically: `client.beta.messages` when `betas` set (effort-2025-11-24, output-128k-2025-02-19, fast-mode-2026-02-01, MCP beta).
- Newer API features: `output_config.format` json_schema structured outputs via SDK `transform_schema` (fallback: forced tool `output_structured_data`), `output_config.effort`, adaptive thinking + `thinking.display`, redacted_thinking replay, `speed="fast"`, server tools web_search_20260318/20250305, code_execution_20260521 + `container` reuse, `mcp_toolset` + `mcp_servers`, `cache_control: ephemeral`, assistant prefill, `stop_reason=="refusal"` -> ClaudeRefusal, service_tier in usage.
- temperature/top_p/top_k sent via `extra_body`.
- `models.list` via `with_raw_response` -> relies on raw `/v1/models` JSON (capabilities, max_tokens) to auto-register models.
- base_url hook exists (`_Shared.base_url`, ctor param) but unused by built-ins; ANTHROPIC_BASE_URL env honored implicitly by SDK for the 4 model clients and the 2 CLI clients.
