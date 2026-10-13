## anthropics/anthropic-quickstarts @ 9ec32b9

Monorepo of independent quickstarts; no root package manager (root `pyproject.toml` only holds pyright config). Only Anthropic SDKs are used (no OpenAI). Every quickstart's app code is counted as prod. Totals: **151 call sites: 109 prod, 42 non-prod**. Of the prod sites, **13 use the Messages API** (5 `messages.create`, 6 `beta.messages.create`, 1 `beta.messages.stream`, 1 `messages.count_tokens`), **1 uses claude-code-sdk**, and **95 use Managed Agents beta resources** (`beta.sessions/environments/agents/files/deployments/webhooks`).

### Per-quickstart setup
| quickstart | lang | pkg mgr / lockfile | python / node req (source) | tests | py3.9 |
|---|---|---|---|---|---|
| agents/ | py | none (no requirements; needs anthropic, mcp) | none declared | `agents/test_message_params.py` is a script, not pytest-collected. It calls the live API. | no: `list[Tool] \| None` in runtime annotations (agent.py:37), no `__future__` |
| autonomous-coding/ | py | pip `requirements.txt` (claude-code-sdk>=0.0.25) | none declared | `python test_security.py` (bash allowlist only, no LLM) | no: claude-code-sdk needs 3.10+ |
| browser-toolset/python, computer-toolset/python | py | pip `requirements.txt` (anthropic>=1.12.0) | none declared | `exercise.py` drives the toolset without a model or key | likely no (`from __future__ import annotations` is used, but websockets>=16.1 and anthropic 1.12 probably need 3.10+) |
| browser-toolset/typescript, computer-toolset/typescript | ts | npm, no lockfile; `@anthropic-ai/sdk ^0.132.0` | node >=20 (engines) | `npm run exercise` (no model) | n/a |
| browser-use-demo/ | py | setup.py + requirements.txt; `anthropic[bedrock,vertex]>=1.9,<2` | `python_requires>=3.11` (setup.py) | `pytest tests/` (pytest.ini; pytest-asyncio auto; minversion) | no (StrEnum, >=3.11) |
| computer-use-demo/ | py | pip `dev-requirements.txt` -> `computer_use_demo/requirements.txt` | CI python 3.11.6 (.github/workflows/tests.yaml); setup.sh needs <=3.12 | **CI**: `pytest tests --junitxml=junit/test-results.xml` (+ ruff, pyright) | no (StrEnum, `with (...)`, `str \| None`) |
| computer-use-best-practices/ | py | uv (pyproject + dependency-groups; `requirements.txt` exported by uv, no uv.lock) | `requires-python>=3.11` (pyproject), macOS only | `python -m pytest` (testpaths=tests) | no |
| customer-support-agent/ | ts (Next 14) | npm, package-lock.json; `@anthropic-ai/sdk ^0.27.1` | node >=18.17.0 (engines) | none (lint only) | n/a |
| financial-data-analyst/ | ts (Next 14, edge) | npm, package-lock.json; `@anthropic-ai/sdk ^0.29.0` | none | none | n/a |
| managed-agents/assistant-ui | ts (Next) | npm, package-lock.json; sdk ^0.113.0 | node >=22 | `npm test` = `node --import tsx --test lib/managed-agents/reducer.test.ts` (pure reducer, no network) | n/a |
| managed-agents/chat-sdk | ts | npm, no lockfile | node >=22.9 | none | n/a |
| managed-agents/copilot-kit-ag-ui | ts | npm workspaces, package-lock.json; sdk pinned 0.109.0 + `@ag-ui/claude-managed-agents` | none | none | n/a |
| managed-agents/linear, slack, mcp-server-typescript | ts | bun (`bun.lock` only in mcp-server-typescript) | none | none | n/a |
| managed-agents/roadtrip-planner | ts (Next) | npm, no lockfile | node >=20 | none | n/a |
| managed-agents/self-hosted-sandboxes/* | ts (cloudflare-worker/containers via wrangler, vercel) / py (daytona, modal, docker-memory, archil) / shell (docker, openshell) | npm or pip | modal image py3.12; docker-memory python:3.12-slim | none | n/a |
| managed-agents/sentry | py | uv/pyproject (`anthropic>=1.9,<2`) | `requires-python>=3.11,<3.13` | none | no (`datetime.UTC`, `match`) |
| managed-agents/knowledge-wiki | py notebook | requirements.txt (+ research-preview SDK wheel that is not on PyPI) | none | none | n/a |
| managed-agents/daily-brief | shell + `ant` CLI | none | none | none | n/a |

JSON-level values: pkg_manager = "pip + uv + npm + bun", python_requires = ">=3.11" (dominant, from browser-use-demo/best-practices/sentry plus CI 3.11.6), node = "20" (most common engines floor; range is 18.17 to 22.9), test_cmd = the CI command for computer-use-demo. **Python 3.9 verdict: NO** for every Python quickstart that has a declared or implied version.

### Clients
| file:line | sdk | ctor | base_url | api_key |
|---|---|---|---|---|
| agents/agent.py:64 | anthropic-python | `Anthropic(api_key=os.environ.get(...))` only if `Agent(client=None)` (injectable) | implicit ANTHROPIC_BASE_URL | env ANTHROPIC_API_KEY |
| autonomous-coding/client.py:102 | claude-code-sdk | `ClaudeSDKClient(options=ClaudeCodeOptions(model, mcp puppeteer, hooks))` | CLI env | env ANTHROPIC_API_KEY (checked at :57) |
| browser-toolset/python/run.py:71, computer-toolset/python/run.py:47 | anthropic-python | `Anthropic()` | implicit | implicit env / SDK credential profile |
| browser-toolset/typescript/run.ts:58, computer-toolset/typescript/run.ts:32 | anthropic-ts | `new Anthropic()` | implicit | implicit |
| browser-use-demo/browser_use_demo/loop.py:107 / :110 / :112 | anthropic-python | `Anthropic(api_key, max_retries=4)` / `AnthropicVertex()` / `AnthropicBedrock()` | implicit / Vertex region env / AWS region | streamlit input, `~/.anthropic/api_key`, env / Google ADC / AWS chain |
| browser-use-demo/browser_use_demo/tools/browser.py:923 | anthropic-python | `AsyncAnthropic(api_key=env)` | implicit | env ANTHROPIC_API_KEY |
| computer-use-demo/computer_use_demo/loop.py:125 / :128 / :130 | anthropic-python | `Anthropic(api_key, max_retries=4)` / `AnthropicVertex()` / `AnthropicBedrock()` | implicit / CLOUD_ML_REGION / AWS | streamlit input, file, env / ADC / AWS |
| computer-use-best-practices/computer_use/loop.py:223 / :226 / :228 | anthropic-python | `AnthropicVertex()` / `AnthropicBedrock()` / `anthropic.Anthropic()` via `_make_client(cfg.provider)` | implicit | ADC / AWS / env |
| computer-use-best-practices/dev_ui/localization_demo/server.py:41 (dev) | anthropic-python | `anthropic.Anthropic()` | implicit | env |
| customer-support-agent/app/api/chat/route.ts:7 | anthropic-ts | `new Anthropic({apiKey: env})` | implicit | env ANTHROPIC_API_KEY |
| financial-data-analyst/app/api/finance/route.ts:7 | anthropic-ts | `new Anthropic({apiKey: env!})` | implicit | env ANTHROPIC_API_KEY |
| managed-agents/{assistant-ui/lib/anthropic.ts:8, chat-sdk/src/managed-agents.ts:14, chat-sdk/setup/create-agent.ts:20, update-agent.ts:15, linear/src/agent.ts:7, linear/src/managed-agents-webhook.ts:14, slack/src/agent.ts:5, slack/src/managed-agents-webhook.ts:6, mcp-server-typescript/src/managed-agents.ts:6, roadtrip-planner/src/lib/client.ts:12} | anthropic-ts | `new Anthropic()` | implicit | implicit env (chat-sdk also accepts ANTHROPIC_AUTH_TOKEN or CLI creds) |
| managed-agents/sentry/managed_agents.py:23 | anthropic-python | `Anthropic()` (module-level) | implicit | env via dotenv |
| managed-agents/knowledge-wiki/...ipynb:157 | anthropic-python | `Anthropic(timeout=Timeout(900, connect=10), max_retries=6)` | implicit | env |
| sandboxes daytona_webhook.py:75, modal_sandbox_webhook.py:108 | anthropic-python | `AsyncAnthropic(auth_token=ENV_KEY, webhook_key=...)` | implicit (forwards ANTHROPIC_BASE_URL to sandbox, default api.anthropic.com) | env ANTHROPIC_ENVIRONMENT_KEY |
| sandboxes daytona/modal sandbox_runner.py:87, docker-memory/worker.py:66 | anthropic-python | `AsyncAnthropic(auth_token=...)` | env ANTHROPIC_BASE_URL | work-secret token |
| cloudflare-worker/src/index.ts:177, runner.ts:52; cloudflare-containers/src/index.ts:205, container.ts:80 | anthropic-ts | `new Anthropic({authToken, baseURL, webhookKey?})` | wrangler.toml `ANTHROPIC_BASE_URL="https://api.anthropic.com"` | environment key |
| vercel/api/webhook.ts:391; vercel/runner/runner.mjs:69 | anthropic-ts | `new Anthropic({authToken, baseURL?, webhookKey?})` | env ANTHROPIC_BASE_URL (default api.anthropic.com) | environment key / work secret |

### Prod call sites: Messages API and Claude Code
| file:line | sdk | surface | stream | async | model source |
|---|---|---|---|---|---|
| agents/agent.py:113 | anthropic-python | messages.create (+`extra_headers anthropic-beta: code-execution-2025-05-22`, server tools web_search/code_execution, MCP tools) | no | no | config ModelConfig.model, default `claude-sonnet-5-5` |
| agents/utils/history_util.py:31 | anthropic-python | messages.count_tokens | no | no | param (agent model) |
| autonomous-coding/agent.py:45 | other:claude-code-sdk | ClaudeSDKClient.query + receive_response (:49) | yes | yes | `--model` default `claude-sonnet-4-5-20250929` |
| browser-toolset/python/run.py:82 | anthropic-python | beta.messages.create via `beta.messages.tool_runner` | no | no | env MODEL, default `claude-sonnet-5-5` |
| browser-toolset/typescript/run.ts:65 | anthropic-ts | beta.messages.create via `beta.messages.toolRunner` | no | yes | env MODEL, default `claude-sonnet-5-5` |
| computer-toolset/python/run.py:57 | anthropic-python | beta.messages.create via `tool_runner` | no | no | env MODEL, default `claude-sonnet-5-5` |
| computer-toolset/typescript/run.ts:53 | anthropic-ts | beta.messages.create via `toolRunner` | no | yes | env MODEL, default `claude-sonnet-5-5` |
| browser-use-demo/browser_use_demo/loop.py:137 | anthropic-python | beta.messages.create (`betas=["prompt-caching-2024-07-31"]`, first-party only) | no | no | user_input (streamlit; per-provider defaults) |
| browser-use-demo/browser_use_demo/loop.py:140 | anthropic-python | messages.create (Bedrock/Vertex path) | no | no | user_input |
| browser-use-demo/browser_use_demo/tools/browser.py:948 | anthropic-python | messages.create (AsyncAnthropic, element finder) | no | yes | hardcoded `claude-sonnet-5-5` |
| computer-use-demo/computer_use_demo/loop.py:168 | anthropic-python | beta.messages.create via `with_raw_response` (betas computer-use-2024-10-22 / 2025-01-24 / 2025-11-24, prompt-caching, token-efficient-tools; `extra_body` thinking/output_config.effort) | no | no | user_input (streamlit; default `claude-opus-4-8`; Bedrock `anthropic.claude-3-5-sonnet-20241022-v2:0`; Vertex `claude-3-5-sonnet-v2@20241022`) |
| computer-use-best-practices/computer_use/loop.py:267 | anthropic-python | beta.messages.stream, or messages.stream when betas are empty (advisor-tool-2026-03-01, compact-2026-01-12 + context_management, computer-use-2025-01-24) | yes | no | `--model` (default `claude-sonnet-4-6`); advisor `claude-opus-4-6` |
| customer-support-agent/app/api/chat/route.ts:226 | anthropic-ts | messages.create (assistant prefill `{`) | no | yes | request body (UI default `claude-haiku-4-5-20251001`) |
| financial-data-analyst/app/api/finance/route.ts:214 | anthropic-ts | messages.create (tools + tool_choice auto, image/PDF blocks) | no | yes | request body (UI default `claude-sonnet-5-5`) |

### Prod call sites: Managed Agents beta (95, model set on the agent definition, not per call)
- **assistant-ui (20)**: sessions.list/create/retrieve/update/delete/archive, sessions.events.send/list/stream (stream: app/api/sessions/[id]/stream/route.ts:34), sessions.resources.add/list, files.upload/list/download. Files: app/api/sessions/**/route.ts, lib/owned-session.ts, lib/managed-agents/session-files.ts.
- **chat-sdk (7 prod + 4 setup scripts)**: src/managed-agents.ts:76, :215, :274 (events.stream), :287; src/sessions.ts:40, :57, :117. Setup: setup/create-agent.ts:26 (environments.create), :39 (agents.create, model env QUICKSTART_MODEL with default `claude-opus-4-8`); setup/update-agent.ts:19, :20.
- **copilot-kit-ag-ui (1)**: server/src/index.ts:48 `new ManagedAgentsAgent(...)` from `@ag-ui/claude-managed-agents`, which wraps the SDK sessions API internally.
- **linear (8), slack (7)**: src/agent.ts (sessions.create/update/events.send) and src/managed-agents-webhook.ts (webhooks.unwrap, sessions.retrieve/update/events.list).
- **mcp-server-typescript (13)**: src/managed-agents.ts:45–216 (agents.list/retrieve, sessions.create/retrieve/archive/events.send/list/stream at :160), src/scope.ts:68.
- **roadtrip-planner (6)**: src/app/api/{chat,interrupt,session,stream}/route.ts and src/lib/client.ts:32 (events.stream at stream/route.ts:32).
- **self-hosted-sandboxes (25)**: environments.work.poll/ack/stop/heartbeat/worker/poller, webhooks.unwrap, sessions.events.stream/toolRunner, across cloudflare-containers (5), cloudflare-worker (6), daytona (4), modal (4), vercel (5) and docker-memory/worker.py:71 (`EnvironmentWorker.handle_item`).
- **sentry (8 prod + 3 teardown)**: deploy.py:44, :50 (deployments.update/create); managed_agents.py:93 (sessions.events.stream); run_now.py:16, :31, :58; runs.py:14, :19. Teardown script: teardown.py:19, :42, :62 (archive of environments, agents, vaults and memory_stores; skills.delete).

### Non-prod call sites (42)
- managed-agents/knowledge-wiki notebook `distill_documents_into_knowledge_wiki.ipynb` (31; line numbers are JSON lines): messages.create :281 (PDF normalization, model env COOKBOOK_BUILD_MODEL with default `claude-sonnet-5`); beta.dreams.create :1188 / retrieve :1203 (research preview `dreaming-2026-04-21`); and 27 Managed Agents calls (environments, memory_stores, memories, agents, sessions, events).
- managed-agents/knowledge-wiki/utilities.py:41, :50, :102 (sessions.retrieve / events.list helpers for the notebook).
- computer-use-best-practices/dev_ui/localization_demo/server.py:128 messages.create (dev UI, forced `tool_choice`).
- managed-agents/chat-sdk/setup/* (4) and managed-agents/sentry/teardown.py (3), listed above.

### Models seen
claude-sonnet-5-5, claude-opus-5-5, claude-haiku-4-5(-20251001), claude-sonnet-4-5(-20250929), claude-opus-4-5-20251101, claude-opus-4-1-20250805, claude-opus-4-20250514, claude-sonnet-4-20250514, claude-3-7-sonnet-20250219, claude-sonnet-4-6, claude-opus-4-6/4-7/4-8, claude-opus-5, claude-sonnet-5, claude-fable-5 (copilot-kit agent .md), claude-3-5-sonnet-20240620, claude-3-haiku-20240307. Bedrock: `anthropic.claude-sonnet-4-5-20250929-v1:0`, `anthropic.claude-3-5-sonnet-20241022-v2:0`. Vertex: `claude-sonnet-4-5@20250929`, `claude-3-5-sonnet-v2@20241022`.

### Tests
- **CI** (.github/workflows/tests.yaml, triggered only by changes under `computer-use-demo/**` or `.github/**`): `cd computer-use-demo && pip install -r dev-requirements.txt && pytest tests --junitxml=junit/test-results.xml` on Python 3.11.6, plus ruff and pyright. build.yaml builds the Docker image and checks that it serves on its ports. No network access or key needed.
  - computer-use-demo/tests/loop_test.py:21: `client = mock.Mock(); client.beta.messages.with_raw_response.create.return_value.parse.side_effect = [Mock(spec=BetaMessage, content=[TextBlock, ToolUseBlock]), ...]`
  - loop_test.py:46: `mock.patch("computer_use_demo.loop.Anthropic", return_value=client)` and `mock.patch("computer_use_demo.loop.ToolCollection", ...)`. At :95 and :96 it patches `"computer_use_demo.loop.AnthropicVertex"` and `"computer_use_demo.loop.AnthropicBedrock"` and asserts request kwargs (betas/omit, extra_body thinking).
  - streamlit_test.py:23 `mock.patch("computer_use_demo.loop.sampling_loop")`. conftest.py:7 is an autouse fixture that patches the env dict (HEIGHT/WIDTH/DISPLAY_NUM).
- **browser-use-demo**: `pytest tests/` (pytest.ini, asyncio_mode=auto). tests/test_sampling_loop.py:310 uses `@patch("browser_use_demo.loop.Anthropic")` with `mock_client.beta.messages.create = Mock(return_value=resp)`. conftest.py patches `streamlit.*`, `browser_use_demo.tools.BrowserTool`, `browser_use_demo.loop.APIProvider` and `monkeypatch.setenv("ANTHROPIC_API_KEY","test-api-key")`. No network.
- **computer-use-best-practices**: `python -m pytest` (README:452; testpaths=tests). tests/test_loop_hosted.py:96 builds a real `anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))` that returns canned `text/event-stream` SSE. It is injected at :107 via `monkeypatch.setattr(loop, "_make_client", lambda _p: client)`, and the test asserts the serialized JSON body and `anthropic-beta` header. Other tests use monkeypatch on `constants.cfg` / `loop.cfg` and `time.sleep`. No network.
- **agents/test_message_params.py**: manual script (`python agents/test_message_params.py`) that calls the live API with a real key. No mocks.
- **Node apps**: only assistant-ui has a test (`node --test` on a pure reducer). The Next.js apps and other TS apps have no tests. The toolset `exercise` scripts run without a model.
- tests_need_network = "partial": CI and pytest suites are offline; agents/test_message_params.py is live.

### Unusual / migration-relevant
- **Most prod call sites use Managed Agents beta APIs** (sessions, events SSE, environments.work, webhooks, files, deployments, dreams, vaults, memory_stores). These are not Messages calls: the model is set on the agent definition in `agents/*.md` frontmatter, applied with the `ant` CLI. A Messages-only router cannot serve them. Shell scripts that use `ant` (daily-brief, `*/agents/setup.sh`, docker/openshell/archil `start.sh`/`on-work.sh`) are not counted. openshell `policy.yaml` allowlists `api.anthropic.com`. Sandboxes forward `ANTHROPIC_BASE_URL`, and wrangler.toml hardcodes it to `https://api.anthropic.com`.
- **Bedrock/Vertex**: computer-use-demo, browser-use-demo and computer-use-best-practices choose between Anthropic, AnthropicVertex and AnthropicBedrock per provider (streamlit radio, `API_PROVIDER` env, or `cfg.provider`). Model IDs are provider-specific.
- **Beta usage on the Messages API**: computer-use tool versions behind betas, prompt-caching beta, token-efficient-tools, advisor tool (`advisor_20260301` with its own model), compaction (`compact_20260112` context_management edits), GA computer toolset `computer_toolset_20260801` (toolset_name tool_use and tool_result), SDK `tool_runner`/`toolRunner` with SDK-provided browser/computer tool classes, and agents/ sending an `anthropic-beta` header on non-beta `messages.create` with server tools.
- **Thinking params**: `thinking {type: adaptive|enabled|disabled}` and `output_config.effort` (sent via `extra_body` in computer-use-demo and as kwargs in best-practices).
- **Streaming**: only best-practices uses SSE (`.stream()` context manager with thinking/text/content_block_* events). computer-use-demo uses `with_raw_response` to show the raw httpx request/response in the UI. Two loops use `import httpx2 as httpx`.
- **customer-support-agent**: assistant prefill `{` for JSON (newer models may reject prefill); RAG via AWS Bedrock Agent Runtime `RetrieveCommand` with `BAWS_*` credentials; `ai` and `@ai-sdk/anthropic` deps are unused; amplify.yml writes secrets into `.env`; the route logs the first 4 characters of the API key.
- **financial-data-analyst**: Next.js edge runtime; structured chart output via forced-style tool use.
- **autonomous-coding**: uses the Claude Code SDK (CLI subprocess with puppeteer MCP and hooks), not the Messages API.
- **Injectable clients**: `agents.Agent(client=...)` and best-practices `_make_client()` (monkeypatched in tests).
