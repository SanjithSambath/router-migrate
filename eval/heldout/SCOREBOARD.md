# router-migrate eval scoreboard

Generated 2026-10-13 11:07 PDT by `make eval` (eval/run_eval.py). 30 pinned open-source repos.

**Totals:** 119 production LLM sites, 66 migrated (55% of all sites, 67% of Router-eligible sites), 3 handoff, 32 kept, 18 flagged. Tests: 17 repos unchanged, 1 worse, 9 without runnable tests.

| repo | commit | sites | migrated | handoff | kept | flagged | call-site cov. | eligible cov. | tests before | tests after | tests, Router on (sim) | sim valid (unique shapes) | live | diff lines | runtime |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Aider-AI/aider | `5dc9490bb3` | 0 | 0 | 0 | 0 | 0 | - | - | 545 passed | 545 passed | N/A: suite mocks the SDK/HTTP | - | - | 0 | 3.5s |
| The-PR-Agent/pr-agent | 330f0813b5 | error: RuntimeError: editable reinstall failed: error: the argument '--no-deps' cannot be used multiple times

Usage: uv pip install [OPTIONS] <PACKAGE|--requirements <REQUIREMENTS>|--editable <EDITABLE>|--group <GROUP>>

For more information, try '--help'.
 |
| kharvd/gpt-cli | `bff5d87311` | 6 | 2 | 0 | 2 | 2 | 33% | 50% | 14 passed | 14 passed | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 20 | 0.8s |
| SWE-agent/mini-swe-agent | `04d809ceab` | 5 | 3 | 2 | 0 | 0 | 60% | 60% | 542 passed | 542 passed | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 24 | 2.0s |
| Pythagora-io/gpt-pilot | `9b763fdaf0` | 7 | 3 | 0 | 1 | 3 | 43% | 43% | 262 passed | 262 passed | N/A: suite mocks the SDK/HTTP | 100% (1/1) | 100% | 50 | 1.8s |
| khoj-ai/khoj | ae229ca894 | error: RuntimeError: editable reinstall failed: error: the argument '--no-deps' cannot be used multiple times

Usage: uv pip install [OPTIONS] <PACKAGE|--requirements <REQUIREMENTS>|--editable <EDITABLE>|--group <GROUP>>

For more information, try '--help'.
 |
| virattt/ai-hedge-fund | `78b779c138` | 3 | 2 | 0 | 1 | 0 | 67% | 67% | 625 passed | 625 passed | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 15 | 1.9s |
| TauricResearch/TradingAgents | `1394a3f72a` | 0 | 0 | 0 | 0 | 0 | - | - | 1358 passed | 1358 passed | N/A: suite mocks the SDK/HTTP | - | - | 0 | 2.6s |
| AntonOsika/gpt-engineer | `a90fcd543e` | 4 | 3 | 0 | 1 | 0 | 75% | 75% | 127 passed | 127 passed | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 19 | 1.1s |
| BuilderIO/micro-agent | `f33523ce9f` | 9 | 3 | 0 | 4 | 2 | 33% | 60% | 51 passed | 51 passed | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 5 | 0.3s |
| e2b-dev/fragments | `255358e470` | 3 | 2 | 0 | 1 | 0 | 67% | 67% | 76 passed | 76 passed | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 5 | 0.3s |
| miurla/morphic | `62e0816b01` | 4 | 2 | 0 | 2 | 0 | 50% | 100% | 1209 passed | 1209 passed | N/A: suite mocks the SDK/HTTP | 100% (1/1) | 100% | 8 | 0.6s |
| jina-ai/node-DeepResearch | `fd323b521a` | 5 | 5 | 0 | 0 | 0 | 100% | 100% | 2 passed | 0 passed, 2 failed | N/A: suite mocks the SDK/HTTP | 100% (1/1) | 100% | 3 | 0.3s |
| huggingface/chat-ui | `8e696f8877` | 6 | 5 | 0 | 1 | 0 | 83% | 100% | 2508 passed | 2508 passed | N/A: suite mocks the SDK/HTTP | 100% (1/1) | 100% | 6 | 0.8s |
| zylon-ai/private-gpt | 4d7f36e53d | error: RuntimeError: editable reinstall failed: error: the argument '--no-deps' cannot be used multiple times

Usage: uv pip install [OPTIONS] <PACKAGE|--requirements <REQUIREMENTS>|--editable <EDITABLE>|--group <GROUP>>

For more information, try '--help'.
 |
| yihong0618/bilingual_book_maker | `e1027a4823` | 21 | 9 | 0 | 10 | 2 | 43% | 60% | n/a | n/a | n/a | 100% (3/3) | - | 22 | 13.3s |
| stackblitz/bolt.new | `eda10b1212` | 0 | 0 | 0 | 0 | 0 | - | - | 24 passed | 24 passed | N/A: suite mocks the SDK/HTTP | - | - | 0 | 0.2s |
| mayooear/ai-pdf-chatbot-langchain | `4b2647c419` | 1 | 0 | 0 | 1 | 0 | 0% | - | 11 passed, 1 failed | 11 passed, 1 failed | N/A: suite mocks the SDK/HTTP | - | - | 0 | 0.2s |
| Cinnamon/kotaemon | `9ad3e4e49a` | 8 | 6 | 0 | 2 | 0 | 75% | 100% | n/a | n/a | n/a | 100% (2/2) | - | 22 | 2.6s |
| srbhr/Resume-Matcher | `db0e6ac9bc` | 0 | 0 | 0 | 0 | 0 | - | - | n/a | n/a | n/a | - | - | 0 | 4.9s |
| egoist/shell-ask | `ccb5388661` | 3 | 3 | 0 | 0 | 0 | 100% | 100% | tsc: 1 errors | tsc: 1 errors | N/A: suite mocks the SDK/HTTP | 100% (2/2) | 100% | 4 | 0.2s |
| ChatGPTNextWeb/NextChat | `839a24df5d` | 0 | 0 | 0 | 0 | 0 | - | - | 157 passed | 157 passed | N/A: suite mocks the SDK/HTTP | - | - | 0 | 0.3s |
| anc95/ChatGPT-CodeReview | `8bd66227f0` | 1 | 0 | 1 | 0 | 0 | 0% | 0% | 52 passed | 52 passed | N/A: suite mocks the SDK/HTTP | - | - | 0 | 0.1s |
| lfnovo/open-notebook | `a0f6f3081a` | 0 | 0 | 0 | 0 | 0 | - | - | n/a | n/a | n/a | - | - | 0 | 4.1s |
| browser-use/web-ui | `61962296c3` | 12 | 8 | 0 | 3 | 1 | 67% | 67% | n/a | n/a | n/a | 100% (2/2) | - | 46 | 1.4s |
| eyaltoledano/claude-task-master | `c0c98d367c` | 2 | 1 | 0 | 0 | 1 | 50% | 50% | n/a | n/a | n/a | 100% (1/1) | - | 8 | 0.9s |
| karakeep-app/karakeep | `b783cf6d15` | 4 | 2 | 0 | 2 | 0 | 50% | 100% | n/a | n/a | n/a | 100% (1/1) | - | 5 | 0.8s |
| Doriandarko/claude-engineer | `0a9e4b309b` | 8 | 0 | 0 | 1 | 7 | 0% | 0% | 9 passed | 9 passed | N/A: suite mocks the SDK/HTTP | - | - | 27 | 1.9s |
| dzhng/deep-research | `1f8f3e285b` | 5 | 5 | 0 | 0 | 0 | 100% | 100% | n/a | n/a | n/a | 100% (1/1) | - | 2 | 0.2s |
| e2b-dev/surf | `d12fcd748f` | 2 | 2 | 0 | 0 | 0 | 100% | 100% | n/a | n/a | n/a | 100% (1/1) | - | 3 | 0.3s |

## Definitions

- **sites_found**: production LLM sites: API calls (chat, responses, messages, embeddings, images...) and framework model constructions; tests/examples/evals excluded
- **migrated**: sites whose traffic goes to Router when RAMP_ROUTER_API_KEY is set (client patched in the same file or the only patchable client family in the repo, surface served by Router, model in catalog or chosen at runtime with at least one of the repo's model names for that family in the catalog)
- **handoff**: sites that need an agent-made code change (other-vendor SDK whose model Router serves, raw HTTP, other gateways)
- **kept**: sites that stay on their provider by design (no Router endpoint, e.g. embeddings; another vendor's endpoint or vendor-named module; stateful Responses chains; provider-side vector stores/files)
- **flagged**: sites Router could serve but the tool does not move automatically (unmapped model, runtime-chosen model with no catalog match among the repo's model names, Anthropic beta header, per-tenant provider keys)
- **callsite_coverage**: migrated / sites_found
- **weighted_coverage**: migrated / Router-eligible sites (generation surfaces Router can serve)
- **tests_before/after**: the repo's own offline test suite (or typecheck where it has none), Router off, before vs after migrating
- **tests, Router on**: the same tests with RAMP_ROUTER_API_KEY set and Router pointed at router-sim. Suites that mock HTTP by exact URL (VCR cassettes, httpx mocks for api.openai.com/api.anthropic.com) cannot match once requests go to Router; those failures are mock misses, not behavior changes (see per-repo notes). Shown as 'N/A: suite mocks the SDK/HTTP' when the run sent 0 requests to router-sim (sim_requests_from_tests = 0): the suite never exercised Router
- **sim_requests_from_tests**: requests that reached router-sim during the Router-on test run (0 means the suite mocks the SDK or HTTP; it then validates nothing)
- **sim_valid_pct**: replay_ok / replay_unique_requests: each migrated site's request is rebuilt from its statically known literal kwargs (tools, response_format, thinking, output_config, extra_headers, temperature, max_tokens, stop, reasoning_effort...; non-literal values are not known and not sent), its literal model when Router's catalog resolves it (else a default model), and synthetic message content, then sent through the vendored runtime to router-sim; sites with an identical request shape count once. It does not run the repo's own code path. Rows marked 'legacy def.' come from older result files (one synthetic 'pong' request per site, reused verdicts counted per site) and are excluded from the mean
- **replay_unique_requests / replay_ok**: unique request shapes replayed / accepted by router-sim
- **live_success_pct**: same replay against api.router.com (null when not run)

## Per-repo notes

- **Aider-AI/aider** (unit; test cmd `rm -rf "$ENV/home" .aider.chat.history.md .aider.tags.cache.v4 && mkdir -p "$ENV/home" && T=$(mktemp -d /tmp/aider-tests.XXXXXX) && HOME="$ENV/home" TMPDIR="$T" AIDER_ANALYTICS=false $PY -m pytest -q -p no:cacheprovider -p no:subtests tests/basic tests/browser tests/scrape --deselect tests/basic/test_urls.py::test_urls --deselect tests/basic/test_sendchat.py::TestSendChat::test_simple_send_non_retryable_error --deselect tests/basic/test_sendchat.py::TestSendChat::test_simple_send_with_retries_rate_limit_error --deselect tests/browser/test_browser.py::TestBrowser::test_browser_flag_imports_streamlit --deselect tests/scrape/test_scrape.py::TestScrape::test_cmd_web_imports_playwright --deselect tests/scrape/test_scrape.py::TestScrape::test_scrape_actual_url_with_playwright --deselect tests/scrape/test_scrape.py::TestScrape::test_scrape_self_signed_ssl --deselect tests/scrape/test_scrape.py::TestScrape::test_scrape_with_playwright_error_handling --deselect tests/scrape/test_scrape.py::TestScrape::test_scraper_print_error_not_called; rc=$?; rm -rf "$T" .aider.chat.history.md .aider.tags.cache.v4; exit $rc`): 545/545 passing before and after. Outcomes by sdk/surface: 
- **kharvd/gpt-cli** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider tests/`): 14/14 passing before and after. Outcomes by sdk/surface: anthropic/messages 1 flagged; anthropic/messages.stream 1 flagged; google-genai/other.chat 2 kept; openai/responses 2 migrated
- **SWE-agent/mini-swe-agent** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && PATH="$ENV/bin:$PATH" HOME="$ENV/home" TMPDIR="$ENV/tmp" LITELLM_LOCAL_MODEL_COST_MAP=True $PY -m pytest -q -p no:cacheprovider --deselect tests/run/test_swebench.py::test_redo_existing_false_skips_existing`): 542/542 passing before and after. Outcomes by sdk/surface: litellm/chat.completions 2 migrated; litellm/responses 1 migrated; openai/chat.completions 1 handoff; openai/responses 1 handoff
- **Pythagora-io/gpt-pilot** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" TIKTOKEN_CACHE_DIR="$ENV/tiktoken" PYTHONPATH=. $PY -m pytest -p no:cacheprovider --no-cov tests/`): 262/262 passing before and after. Outcomes by sdk/surface: anthropic/messages 2 flagged; anthropic/messages.stream 1 flagged; groq/chat.completions 1 kept; openai/chat.completions 3 migrated
- **virattt/ai-hedge-fund** (unit; test cmd `export FINANCIAL_DATASETS_API_KEY=; mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider hedge_fund/`): 625/625 passing before and after. Outcomes by sdk/surface: langchain-anthropic/framework.chat 1 migrated; langchain-openai/framework.chat 1 kept, 1 migrated
- **TauricResearch/TradingAgents** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider -p no:subtests tests/ --deselect tests/test_cache_writes.py::test_on_windows_the_lock_is_waited_for_while_another_writer_holds_it --deselect tests/test_cache_writes.py::test_on_windows_a_lock_that_cannot_be_taken_is_reported_not_retried_forever --deselect tests/test_cache_writes.py::test_on_windows_a_held_lock_is_reported_when_not_waiting`): 1358/1358 passing before and after. Outcomes by sdk/surface: 
- **AntonOsika/gpt-engineer** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" TIKTOKEN_CACHE_DIR="$ENV/tiktoken" GIT_CEILING_DIRECTORIES="$ENV" $PY -m pytest -q -p no:cacheprovider --ignore=tests/test_install.py tests/`): 127/127 passing before and after. Outcomes by sdk/surface: azure-openai/framework.chat 1 kept; langchain-anthropic/framework.chat 1 migrated; langchain-openai/framework.chat 2 migrated
- **BuilderIO/micro-agent** (unit; test cmd `unset OPENAI_KEY ANTHROPIC_KEY MODEL ANTHROPIC_MODEL OPENAI_API_ENDPOINT USE_ASSISTANT USE_MOCK_LLM MOCK_LLM_RECORD_FILE LANGUAGE; mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" ./node_modules/.bin/vitest run --exclude src/tests/integration`): 51/51 passing before and after; typecheck 4 -> 4 errors. Outcomes by sdk/surface: anthropic/messages.stream 2 flagged; openai/assistants 4 kept; openai/chat.completions 3 migrated
- **e2b-dev/fragments** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" ./node_modules/.bin/tsx --test lib/*.test.ts app/api/*.test.ts`): 76/76 passing before and after; typecheck 0 -> 0 errors. Outcomes by sdk/surface: vercel-ai-openai/framework.chat 1 kept; vercel-ai/framework.chat 2 migrated
- **miurla/morphic** (unit; test cmd `unset OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_GENERATIVE_AI_API_KEY AI_GATEWAY_API_KEY OPENAI_COMPATIBLE_API_KEY OLLAMA_BASE_URL RUN_LLM_E2E; mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" TZ=UTC DATABASE_URL=postgresql://user:pass@localhost:5432/db NODE_ENV=test ./node_modules/.bin/vitest run`): 1209/1209 passing before and after; typecheck 0 -> 0 errors. Outcomes by sdk/surface: anthropic/models 1 kept; openai/models 1 kept; vercel-ai/framework.chat 2 migrated
- **jina-ai/node-DeepResearch** (unit; test cmd `unset OPENAI_BASE_URL LLM_PROVIDER; mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" OPENAI_API_KEY= ./node_modules/.bin/jest --ci --silent --testTimeout=30000 src/__tests__/server.test.ts -t "should require authentication when secret is set|should reject requests without user message"`): 2 -> 0 passing; typecheck 0 -> 0 errors. Outcomes by sdk/surface: vercel-ai/framework.chat 5 migrated
- **huggingface/chat-ui** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && CI=1 OPENAI_API_KEY= HOME="$ENV/home" TMPDIR="$ENV/tmp" ./node_modules/.bin/vitest run --project=server --project=ssr --maxWorkers=4`): 2508/2508 passing before and after; typecheck 52 -> 52 errors. Outcomes by sdk/surface: openai/chat.completions 5 migrated; openai/completions.legacy 1 kept
- **yihong0618/bilingual_book_maker** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: anthropic/messages 3 kept; openai/chat.completions 2 flagged, 1 kept, 7 migrated; openai/chat.completions.parse 2 migrated; openai/files 2 kept; openai/models 1 kept; other/other.chat 3 kept
- **stackblitz/bolt.new** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && export HOME="$ENV/home" TMPDIR="$ENV/tmp" ANTHROPIC_API_KEY= && ./node_modules/.bin/remix vite:build && ./node_modules/.bin/vitest --run`): 24/24 passing before and after; typecheck 0 -> 0 errors. Outcomes by sdk/surface: 
- **mayooear/ai-pdf-chatbot-langchain** (unit; test cmd `unset OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY LANGCHAIN_API_KEY LANGSMITH_API_KEY LANGCHAIN_TRACING_V2 LANGSMITH_TRACING; mkdir -p "$ENV/home" "$ENV/tmp" && cd backend && HOME="$ENV/home" TMPDIR="$ENV/tmp" CI=1 FORCE_COLOR=0 ./node_modules/.bin/jest --ci`): 11/12 passing before and after; typecheck 2 -> 2 errors. Outcomes by sdk/surface: langchain-openai/framework.embeddings 1 kept
- **Cinnamon/kotaemon** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: openai/chat.completions 4 migrated; openai/chat.completions.parse 2 migrated; openai/embeddings 2 kept
- **srbhr/Resume-Matcher** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: 
- **egoist/shell-ask** (typecheck; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" ./node_modules/.bin/tsc --noEmit -p . --incremental false`): typecheck: 1 -> 1 errors. Outcomes by sdk/surface: vercel-ai/framework.chat 3 migrated
- **ChatGPTNextWeb/NextChat** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" node --no-warnings --experimental-vm-modules ./node_modules/.bin/jest --ci`): 157/157 passing before and after; typecheck 0 -> 0 errors. Outcomes by sdk/surface: 
- **anc95/ChatGPT-CodeReview** (unit; test cmd `unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_ENDPOINT MODEL USE_GITHUB_MODELS GITHUB_TOKEN GITHUB_ACTIONS AZURE_API_VERSION AZURE_DEPLOYMENT REASONING_EFFORT temperature top_p max_tokens LANGUAGE PROMPT TARGET_LABEL IGNORE ignore IGNORE_PATTERNS INCLUDE_PATTERNS MAX_PATCH_LENGTH LOG_LEVEL; mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" CI=1 FORCE_COLOR=0 ./node_modules/.bin/jest --ci`): 52/52 passing before and after; typecheck 6 -> 6 errors. Outcomes by sdk/surface: openai/chat.completions 1 handoff
- **lfnovo/open-notebook** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: 
- **browser-use/web-ui** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: azure-openai/framework.chat 1 kept; langchain-anthropic/framework.chat 1 flagged; langchain-openai/framework.chat 2 kept, 6 migrated; openai/chat.completions 2 migrated
- **eyaltoledano/claude-task-master** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: vercel-ai-anthropic/framework.chat 1 flagged; vercel-ai-openai/framework.chat 1 migrated
- **karakeep-app/karakeep** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: openai/chat.completions 2 migrated; openai/embeddings 2 kept
- **Doriandarko/claude-engineer** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" PYTHONDONTWRITEBYTECODE=1 $PY -c "import glob; [compile(open(p).read(), p, \"exec\") for g in (\"*.py\", \"tools/*.py\", \"prompts/*.py\", \"Claude-Eng-v2/*.py\") for p in sorted(glob.glob(g))]; import ce3, importlib, pkgutil, tools; [importlib.import_module(\"tools.\" + m.name) for m in pkgutil.iter_modules(tools.__path__)]" && HOME="$ENV/home" TMPDIR="$ENV/tmp" PYTHONDONTWRITEBYTECODE=1 $PY -m pytest -q -p no:cacheprovider -o addopts= test.py`): 9/9 passing before and after. Outcomes by sdk/surface: anthropic/messages 7 flagged; ollama/messages 1 kept
- **dzhng/deep-research** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: vercel-ai/framework.chat 5 migrated
- **e2b-dev/surf** (unit; test cmd `-`): no runnable tests. Outcomes by sdk/surface: openai/responses 2 migrated
