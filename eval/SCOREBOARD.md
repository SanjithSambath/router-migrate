# router-migrate eval scoreboard

Generated 2026-10-13 01:20 PDT by `make eval` (eval/run_eval.py). 15 pinned open-source repos.

**Totals:** 171 production LLM sites, 57 migrated (33% of all sites, 51% of Router-eligible sites), 12 handoff, 86 kept, 16 flagged. Tests: 15 repos unchanged, 0 worse, 0 without runnable tests.

| repo | commit | sites | migrated | handoff | kept | flagged | call-site cov. | eligible cov. | tests before | tests after | tests, Router on (sim) | sim valid | live | diff lines | runtime |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/swarm | `6af0b4caf3` | 1 | 1 | 0 | 0 | 0 | 100% | 100% | 6 passed | 6 passed | 6 passed | 100% | - | 6 | 0.5s |
| TheR1D/shell_gpt | `a082bd5327` | 1 | 1 | 0 | 0 | 0 | 100% | 100% | 29 passed | 29 passed | 29 passed | 100% | - | 6 | 0.6s |
| microsoft/TaskWeaver | `d44ddef23f` | 12 | 6 | 0 | 6 | 0 | 50% | 75% | 100 passed | 100 passed | 100 passed | 100% | - | 28 | 1.1s |
| simonw/llm | `05d7ae7ded` | 12 | 8 | 0 | 4 | 0 | 67% | 100% | 1208 passed | 1208 passed | 1089 passed, 226 failed | 100% | - | 10 | 6.7s |
| abi/screenshot-to-code | `a8139779f7` | 4 | 2 | 0 | 2 | 0 | 50% | 100% | 280 passed | 280 passed | 280 passed | 100% | - | 14 | 3.5s |
| anthropics/anthropic-quickstarts | `9ec32b91df` | 12 | 6 | 0 | 2 | 4 | 50% | 60% | 189 passed | 189 passed | 189 passed | 100% | - | 125 | 4.1s |
| simonw/llm-anthropic | `b66cdf112c` | 5 | 3 | 0 | 1 | 1 | 60% | 75% | 123 passed | 123 passed | 94 passed, 29 failed | 100% | - | 13 | 1.7s |
| di-sukharev/opencommit | `a84b7cc597` | 5 | 3 | 2 | 0 | 0 | 60% | 60% | 243 passed | 243 passed | 243 passed | 100% | - | 6 | 0.3s |
| mckaywrigley/chatbot-ui | `81328b61d2` | 15 | 1 | 0 | 4 | 10 | 7% | 9% | 2 passed, 1 failed | 2 passed, 1 failed | 2 passed, 1 failed | 100% | - | 49 | 0.4s |
| openai/openai-responses-starter-app | `0fae283f12` | 7 | 1 | 1 | 5 | 0 | 14% | 50% | tsc: 0 errors | tsc: 0 errors | tsc: 0 errors | 100% | - | 18 | 0.3s |
| vercel/ai-chatbot | `c2f8235e1f` | 9 | 0 | 9 | 0 | 0 | 0% | 0% | tsc: 0 errors | tsc: 0 errors | tsc: 0 errors | - | - | 0 | 0.2s |
| Nutlope/aicommits | `a414dd43b6` | 4 | 4 | 0 | 0 | 0 | 100% | 100% | 79 passed | 79 passed | 79 passed | 100% | - | 6 | 0.3s |
| assafelovic/gpt-researcher | `3582a6841f` | 19 | 2 | 0 | 17 | 0 | 11% | 15% | 570 passed | 570 passed | 570 passed | 100% | - | 16 | 3.6s |
| langchain-ai/langchain-nextjs-template | `fd4f2cd74e` | 12 | 8 | 0 | 3 | 1 | 67% | 89% | 4 passed | 4 passed | 4 passed | 100% | - | 18 | 0.3s |
| mem0ai/mem0 | `d26ba5e451` | 53 | 11 | 0 | 42 | 0 | 21% | 46% | 2007 passed | 2007 passed | 2007 passed | 100% | - | 59 | 16.2s |

## Definitions

- **sites_found**: production LLM sites: API calls (chat, responses, messages, embeddings, images...) and framework model constructions; tests/examples/evals excluded
- **migrated**: sites whose traffic goes to Router when RAMP_ROUTER_API_KEY is set (client patched, surface served by Router, model in catalog or chosen at runtime)
- **handoff**: sites that need an agent-made code change (other-vendor SDK whose model Router serves, raw HTTP, other gateways)
- **kept**: sites that stay on their provider by design (no Router endpoint, e.g. embeddings; or another vendor's endpoint)
- **flagged**: sites Router could serve but the tool does not move automatically (unmapped model, beta API, server-side state)
- **callsite_coverage**: migrated / sites_found
- **weighted_coverage**: migrated / Router-eligible sites (generation surfaces Router can serve)
- **tests_before/after**: the repo's own offline test suite (or typecheck where it has none), Router off, before vs after migrating
- **tests, Router on**: the same tests with RAMP_ROUTER_API_KEY set and Router pointed at router-sim. Suites that mock HTTP by exact URL (VCR cassettes, httpx mocks for api.openai.com/api.anthropic.com) cannot match once requests go to Router; those failures are mock misses, not behavior changes (see per-repo notes)
- **sim_valid_pct**: valid / total requests at router-sim from (a) the repo's tests run with Router on and (b) one synthetic replay per migrated site
- **live_success_pct**: same replay against api.router.com (null when not run)

## Per-repo notes

- **openai/swarm** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider tests/`): 6/6 passing before and after. Outcomes by sdk/surface: openai/chat.completions 1 migrated
- **TheR1D/shell_gpt** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider -p no:warnings tests/`): 29/29 passing before and after. Outcomes by sdk/surface: openai/chat.completions 1 migrated
- **microsoft/TaskWeaver** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider tests/unit_tests`): 100/100 passing before and after. Outcomes by sdk/surface: anthropic/messages 1 migrated; anthropic/messages.stream 1 migrated; azure-openai/framework.chat 1 kept; groq/chat.completions 1 kept; langchain-openai/framework.chat 2 migrated; openai/chat.completions 2 migrated; openai/embeddings 2 kept; other/other.chat 2 kept
- **simonw/llm** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider`): 1208/1208 passing before and after. Outcomes by sdk/surface: openai/chat.completions 4 migrated; openai/completions.legacy 2 kept; openai/embeddings 1 kept; openai/models 1 kept; openai/responses 4 migrated
- **abi/screenshot-to-code** (unit; test cmd `cd backend && $PY -m pytest -p no:cacheprovider`): 280/280 passing before and after. Outcomes by sdk/surface: anthropic/messages.stream 1 migrated; openai/responses 1 migrated; other/other.chat 2 kept
- **anthropics/anthropic-quickstarts** (unit; test cmd `(cd computer-use-demo && "$ENV/computer-use-demo/bin/python" -m pytest tests -p no:cacheprovider); r1=$?; (cd computer-use-best-practices && "$ENV/computer-use-best-practices/bin/python" -m pytest -p no:cacheprovider --deselect tests/test_shell.py::test_bash_echo --deselect tests/test_shell.py::test_python_print --deselect tests/test_shell.py::test_output_truncated --deselect tests/test_editor.py::test_shell_shares_scratch_dir) && exit $r1`): 189/189 passing before and after. Outcomes by sdk/surface: anthropic/files 2 kept; anthropic/messages 6 migrated; anthropic/messages.beta 4 flagged
- **simonw/llm-anthropic** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider`): 123/123 passing before and after. Outcomes by sdk/surface: anthropic/messages.beta 1 flagged; anthropic/messages.count_tokens 1 migrated; anthropic/messages.stream 2 migrated; anthropic/models 1 kept
- **di-sukharev/opencommit** (unit; test cmd `export HOME="$PWD/node_modules/.cache/rm-home"; mkdir -p "$HOME"; unset OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_BASE_URL; npm run test:unit; u=$?; npm run test:e2e:core; e=$?; exit $(( u || e ))`): 243/243 passing before and after. Outcomes by sdk/surface: anthropic/messages 1 migrated; anthropic/models 1 handoff; openai/chat.completions 2 migrated; openai/models 1 handoff
- **mckaywrigley/chatbot-ui** (unit; test cmd `npm test`): 2/3 passing before and after. Outcomes by sdk/surface: anthropic/messages 1 migrated; openai/assistants 1 kept; openai/chat.completions 10 flagged; openai/embeddings 3 kept
- **openai/openai-responses-starter-app** (typecheck; test cmd `./node_modules/.bin/tsc --noEmit -p . --incremental false`): typecheck: 0 -> 0 errors. Outcomes by sdk/surface: openai/files 1 handoff, 1 kept; openai/responses 1 migrated; openai/vector_stores 4 kept
- **vercel/ai-chatbot** (typecheck; test cmd `./node_modules/.bin/tsc --noEmit -p . --incremental false`): typecheck: 0 -> 0 errors. Outcomes by sdk/surface: vercel-ai/framework.chat 9 handoff
- **Nutlope/aicommits** (unit; test cmd `unset OPENAI_BASE_URL; export OPENAI_API_KEY=; npm_config_store_dir="$RM/.tools/pnpm-store" npm_config_cache_dir="$RM/.tools/pnpm-cache" npm_config_state_dir="$RM/.tools/pnpm-state" npm_config_manage_package_manager_versions=false "$RM/.tools/pm/node_modules/.bin/pnpm" build && npm_config_store_dir="$RM/.tools/pnpm-store" npm_config_cache_dir="$RM/.tools/pnpm-cache" npm_config_state_dir="$RM/.tools/pnpm-state" npm_config_manage_package_manager_versions=false "$RM/.tools/pm/node_modules/.bin/pnpm" test`): 79/79 passing before and after. Outcomes by sdk/surface: vercel-ai/framework.chat 4 migrated
- **assafelovic/gpt-researcher** (unit; test cmd `TIKTOKEN_CACHE_DIR="$ENV/tiktoken_cache" $PY -m pytest tests/ --forked --timeout=60 -p no:warnings -p no:cacheprovider --deselect tests/test_researcher_logging.py --deselect tests/test_logging_output.py --deselect tests/test_mcp.py`): 570/570 passing before and after. Outcomes by sdk/surface: azure-openai/framework.chat 1 kept; langchain-anthropic/framework.chat 1 migrated; langchain-openai/framework.chat 10 kept, 1 migrated; langchain-openai/framework.embeddings 6 kept
- **langchain-ai/langchain-nextjs-template** (unit; test cmd `node --experimental-strip-types --test utils/integrations.test.mjs`): 4/4 passing before and after. Outcomes by sdk/surface: langchain-openai/framework.chat 1 flagged, 8 migrated; langchain-openai/framework.embeddings 3 kept
- **mem0ai/mem0** (unit; test cmd `$PY -m pytest tests/ -q -p no:cacheprovider`): 2007/2007 passing before and after. Outcomes by sdk/surface: anthropic/messages 2 migrated; azure-openai/chat.completions 4 kept; azure-openai/embeddings 4 kept; google-genai/other.chat 1 kept; google-genai/other.embeddings 2 kept; groq/chat.completions 3 kept; litellm/chat.completions 2 migrated; openai/chat.completions 5 kept, 6 migrated; openai/chat.completions.parse 1 migrated; openai/embeddings 12 kept; other/chat.completions 1 kept; other/embeddings 2 kept; other/other.chat 8 kept
