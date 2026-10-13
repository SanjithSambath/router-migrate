## TheR1D/shell_gpt @ a082bd5327

- **Lang**: Python. **Pkg manager**: pip + hatchling (`pyproject.toml`). **Lockfiles**: none.
- **Python req**: `>=3.10` (source: `pyproject.toml requires-python`; classifiers 3.10–3.13; CI `lint_test.yml` matrix 3.10/3.11/3.12).
- **py3.9 verdict: NO (by metadata), and the code is probably OK.** `pip install .` on 3.9 refuses because of requires-python. I found no 3.10-only syntax in `sgpt/` or `tests/`: there are no `X | Y` unions or `match`, and `dict[str, Any]` / `list[str]` are fine on 3.9. Running from source on 3.9 might work if `openai>=2,<3`, `typer`, `rich<14` and `prompt_toolkit` resolve, but that is unverified. The `litellm==1.83.4` extra is also unverified on 3.9. `.devcontainer` still points at a 3.9 image, which is stale.
- **Node**: n/a.

### Config
- `~/.config/shell_gpt/.sgptrc` (key=value). `Config.get` prefers `os.getenv(KEY)` over the file (`sgpt/config.py:89-94`).
- `API_BASE_URL` (default `"default"`, which becomes `base_url=None`, so the SDK falls back to `OPENAI_BASE_URL`), `OPENAI_API_KEY` (getpass prompt on first run if absent), `USE_LITELLM` (default `false`), `DEFAULT_MODEL` (default `gpt-5.4-mini`), `REQUEST_TIMEOUT` (60).

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| sgpt/handlers/handler.py:31 | openai-python | `OpenAI(timeout=, api_key=, base_url=)`, built at module import | config/env `API_BASE_URL` (`default` becomes None, then env `OPENAI_BASE_URL`) | config/env `OPENAI_API_KEY` |
| sgpt/handlers/handler.py:26 | litellm | `completion = litellm.completion` (no client) | `base_url=` kwarg per call from `API_BASE_URL` | `api_key=` kwarg per call from `OPENAI_API_KEY` |

### Prod call sites (1 physical site, 2 bindings)
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| sgpt/handlers/handler.py:123 | openai-python | chat.completions.create (via module alias `completion`, handler.py:32) | yes (always) | no | `--model` CLI option, default `DEFAULT_MODEL` |
| sgpt/handlers/handler.py:123 | litellm | litellm.completion (when `USE_LITELLM=true`) | yes | no | same |

The wrapper is `Handler.get_completion` (handler.py:103, `@cache`-decorated). It is used by DefaultHandler, ChatHandler (chat_handler.py:173) and ReplHandler, and it recurses at handler.py:159 after executing a tool call. When functions are enabled it adds `tools`, `tool_choice="auto"` and `parallel_tool_calls=False`.

### Non-prod call sites
- None calling the SDK directly. `tests/_integration.py` exercises the real API through the CLI. It is not collected by pytest because of the leading underscore, and it needs a real key.

### Models seen
gpt-5.4-mini (default), gpt-4 (_integration test)

### Tests
- **Command**: CI runs `export OPENAI_API_KEY=test_api_key; pytest tests/ -p no:warnings -v -s` (`scripts/test.sh`: `pytest tests -p no:warnings`). **Framework**: pytest + typer `CliRunner`.
- **Needs network?** No (unit tests). The env key is still needed so the config import doesn't prompt via getpass.
- **Mocking (exact)**:
  - `@patch("sgpt.handlers.handler.completion")` appears 29 times: tests/test_default.py (11, first at :17), tests/test_shell.py (10, first at :11), tests/test_code.py (7, first at :12), tests/test_roles.py:11.
  - `completion.return_value = mock_comp("...")` or `side_effect=[mock_comp(a), mock_comp(b)]`. `mock_comp` (tests/utils.py:26) returns a list of `openai.types.chat.chat_completion_chunk.ChatCompletionChunk`, one per character, with `delta=ChoiceDelta(content=ch, role="assistant")` and `finish_reason=None`.
  - Exact-kwargs assertions: `completion.assert_called_once_with(**comp_args(role, prompt))` (11) and `assert_called_with` (8). `comp_args` is at tests/utils.py:58: messages=[system, user], model=cfg DEFAULT_MODEL, temperature=0.0, top_p=1.0, stream=True.
  - Also patched: `sgpt.printer.TextPrinter.live_print` / `MarkdownPrinter.live_print`, `os.system`, `rich.console.Console.print`. conftest.py:6 has an autouse fixture setting `os.name="test"`.
  - `tests/_integration.py:386` patches `sgpt.handlers.handler.Handler.get_completion`.
  - The litellm path is untested. `requests-mock` is a test extra but unused.

### Unusual / migration notes
- **Seam**: the module-global `completion` in `sgpt/handlers/handler.py` is chosen at import (OpenAI vs LiteLLM), and tests patch exactly that name. A router migration that keeps `completion = client.chat.completions.create` and only changes the `OpenAI(...)` ctor/base_url (or sets `API_BASE_URL`) leaves the tests untouched. Adding kwargs (e.g. `extra_headers`) to the call breaks 19 assertions unless `comp_args` is updated.
- A custom base URL is already supported via `API_BASE_URL`, so the router can be used with zero code change.
- Side-effect bug: `get_completion` mutates the module-global `additional_kwargs` with tool params, so they persist across calls in a process.
- LiteLLM path passes `api_key`, `base_url` and `timeout` per call. Its tool-call deltas are dicts, while the openai path uses pydantic objects.
- Always streams. `DISABLE_STREAMING` only affects printing. No `response.model` reuse.
