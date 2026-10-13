# router-migrate eval scoreboard

Generated 2026-10-13 01:01 PDT by `make eval` (eval/run_eval.py). 8 pinned open-source repos.

**Totals:** 54 production LLM sites, 19 migrated (35% of all sites, 45% of Router-eligible sites), 12 handoff, 12 kept, 11 flagged. Tests: 6 repos unchanged, 0 worse, 2 without runnable tests.

| repo | commit | sites | migrated | handoff | kept | flagged | call-site cov. | eligible cov. | tests before | tests after | sim valid | live | diff lines | runtime |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/swarm | `6af0b4caf3` | 1 | 1 | 0 | 0 | 0 | 100% | 100% | n/a | n/a | 100% | - | 6 | 0.5s |
| TheR1D/shell_gpt | `a082bd5327` | 1 | 1 | 0 | 0 | 0 | 100% | 100% | n/a | n/a | 100% | - | 6 | 0.6s |
| di-sukharev/opencommit | `a84b7cc597` | 5 | 3 | 2 | 0 | 0 | 60% | 60% | 243 passed | 243 passed | 100% | - | 6 | 0.3s |
| mckaywrigley/chatbot-ui | `81328b61d2` | 15 | 1 | 0 | 4 | 10 | 7% | 9% | 2 passed, 1 failed | 2 passed, 1 failed | 100% | - | 49 | 0.4s |
| openai/openai-responses-starter-app | `0fae283f12` | 7 | 1 | 1 | 5 | 0 | 14% | 50% | tsc: 0 errors | tsc: 0 errors | 100% | - | 18 | 0.3s |
| vercel/ai-chatbot | `c2f8235e1f` | 9 | 0 | 9 | 0 | 0 | 0% | 0% | tsc: 0 errors | tsc: 0 errors | - | - | 0 | 0.2s |
| Nutlope/aicommits | `a414dd43b6` | 4 | 4 | 0 | 0 | 0 | 100% | 100% | n/a | n/a | 100% | - | 6 | 0.3s |
| langchain-ai/langchain-nextjs-template | `fd4f2cd74e` | 12 | 8 | 0 | 3 | 1 | 67% | 89% | n/a | n/a | 100% | - | 18 | 0.3s |

## Definitions

- **sites_found**: production LLM sites: API calls (chat, responses, messages, embeddings, images...) and framework model constructions; tests/examples/evals excluded
- **migrated**: sites whose traffic goes to Router when RAMP_ROUTER_API_KEY is set (client patched, surface served by Router, model in catalog or chosen at runtime)
- **handoff**: sites that need an agent-made code change (other-vendor SDK whose model Router serves, raw HTTP, other gateways)
- **kept**: sites that stay on their provider by design (no Router endpoint, e.g. embeddings; or another vendor's endpoint)
- **flagged**: sites Router could serve but the tool does not move automatically (unmapped model, beta API, server-side state)
- **callsite_coverage**: migrated / sites_found
- **weighted_coverage**: migrated / Router-eligible sites (generation surfaces Router can serve)
- **tests_before/after**: the repo's own offline test suite (or typecheck where it has none), Router off, before vs after migrating
- **sim_valid_pct**: valid / total requests at router-sim from (a) the repo's tests run with Router on and (b) one synthetic replay per migrated site
- **live_success_pct**: same replay against api.router.com (null when not run)

## Per-repo notes

- **openai/swarm** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider tests/`): no runnable tests. Outcomes by sdk/surface: openai/chat.completions 1 migrated
- **TheR1D/shell_gpt** (unit; test cmd `mkdir -p "$ENV/home" "$ENV/tmp" && HOME="$ENV/home" TMPDIR="$ENV/tmp" $PY -m pytest -q -p no:cacheprovider -p no:warnings tests/`): no runnable tests. Outcomes by sdk/surface: openai/chat.completions 1 migrated
- **di-sukharev/opencommit** (unit; test cmd `export HOME="$PWD/node_modules/.cache/rm-home"; mkdir -p "$HOME"; unset OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_BASE_URL; npm run test:unit; u=$?; npm run test:e2e:core; e=$?; exit $(( u || e ))`): 243/243 passing before and after. Outcomes by sdk/surface: anthropic/messages 1 migrated; anthropic/models 1 handoff; openai/chat.completions 2 migrated; openai/models 1 handoff
- **mckaywrigley/chatbot-ui** (unit; test cmd `npm test`): 2/3 passing before and after. Outcomes by sdk/surface: anthropic/messages 1 migrated; openai/assistants 1 kept; openai/chat.completions 10 flagged; openai/embeddings 3 kept
- **openai/openai-responses-starter-app** (typecheck; test cmd `./node_modules/.bin/tsc --noEmit -p . --incremental false`): typecheck: 0 -> 0 errors. Outcomes by sdk/surface: openai/files 1 handoff, 1 kept; openai/responses 1 migrated; openai/vector_stores 4 kept
- **vercel/ai-chatbot** (typecheck; test cmd `./node_modules/.bin/tsc --noEmit -p . --incremental false`): typecheck: 0 -> 0 errors. Outcomes by sdk/surface: vercel-ai/framework.chat 9 handoff
- **Nutlope/aicommits** (unit; test cmd `unset OPENAI_BASE_URL; export OPENAI_API_KEY=; npm_config_store_dir="$RM/.tools/pnpm-store" npm_config_cache_dir="$RM/.tools/pnpm-cache" npm_config_state_dir="$RM/.tools/pnpm-state" npm_config_manage_package_manager_versions=false "$RM/.tools/pm/node_modules/.bin/pnpm" build && npm_config_store_dir="$RM/.tools/pnpm-store" npm_config_cache_dir="$RM/.tools/pnpm-cache" npm_config_state_dir="$RM/.tools/pnpm-state" npm_config_manage_package_manager_versions=false "$RM/.tools/pm/node_modules/.bin/pnpm" test`): typecheck 0 -> 0 errors. Outcomes by sdk/surface: vercel-ai/framework.chat 4 migrated
- **langchain-ai/langchain-nextjs-template** (unit; test cmd `node --experimental-strip-types --test utils/integrations.test.mjs`): typecheck 1 -> 1 errors. Outcomes by sdk/surface: langchain-openai/framework.chat 1 flagged, 8 migrated; langchain-openai/framework.embeddings 3 kept
