# router-migrate: spec

## Goal
A production-ready tool that moves a codebase's PRODUCTION LLM API calls onto Ramp's Router (router.com), plus an eval
proving it on 15 real open-source repos. Work trial for Kedar Thakkar (Router team, Ramp). He'll judge by running it on
open-source repos and measuring (1) share of LLM traffic moved to Router, (2) whether existing behavior survives.
Due Wed Oct 14, 1pm ET.

## Ground truth (beats training data and docs when they conflict)
- ~/code/router-live-check/results.md (v1), results_v2_annotated.md + results_v2_*.json + raw_v2/ (v2, SDK tests, Surprises)
- ~/code/router-live-check/models.json: live catalog for this key
- https://docs.router.com (Connect, Responses and Messages, List models, Errors and limits, Choose a model)
- Baseline to beat: the migration prompt at https://docs.router.com/getting-started/connect
Key facts (measured live):
- Only GET /v1/models, POST /v1/responses, POST /v1/messages(+/count_tokens). Chat Completions, embeddings, images, files,
  legacy completions, models.retrieve, responses.retrieve/cancel/delete, conversations: 404/405.
- Base URLs: OpenAI-style https://api.router.com/v1; Anthropic SDK https://api.router.com. Key: RAMP_ROUTER_API_KEY.
- No server state: store:true 400; previous_response_id 400 on OpenAI, 200-but-forgets elsewhere. Full-history replay works.
- Undated catalog IDs only; dated/prefixed 404; never reuse response.model; verify mapped models live (gpt-5.3-codex 403s).
- Responses strict: messages/max_tokens/response_format 400 (rename to input/max_output_tokens/text.format);
  seed/n/logprobs/unknown 400; stop 501 on OpenAI; temperature 400 where capabilities.temperature=false;
  reasoning.effort 400 on non-reasoning, and on Claude needs max_output_tokens >= ~1100.
- Flex default on: add service_tier:"default" on latency-sensitive paths. Never add allow_flex_tier:true.
- Claude via Responses: add prompt_cache_key or caching stops. Messages: no usage.cost; Fireworks input_tokens:1. Router-only
  fields only on Responses.
- LangChain needs use_responses_api=True/useResponsesApi:true. Vercel AI SDK: router(model)/.responses(), not .chat().
  urllib needs a User-Agent.
- Errors: plain-text 504s, no X-Router-Provider on errors, silent fallback switching.

## Deliverables (all in this folder, local git, small commits, never push)
1. CLI `router-migrate`: scan -> plan -> apply -> verify -> report.
   - scan: real parsers (Python libcst/ast; TS ts-morph/TS compiler API; ast-grep fallback). OpenAI SDK py/ts (sync, async,
     stream, chat, responses, embeddings, images, models.*), Anthropic SDK py/ts, LangChain py/js, Vercel AI SDK,
     LlamaIndex, Pydantic AI, LiteLLM, raw HTTP to api.openai.com/api.anthropic.com, env vars, model names in config.
   - plan: buckets swap / convert / agent-handoff / keep-on-provider / flag(with exact reason). Model map via catalog
     provider_model/catalog_id (live if key set, else bundled snapshot). Dry-run diff.
   - apply: deterministic codemods; Router client for generation only, active only when RAMP_ROUTER_API_KEY is set;
     metadata {"feature":"<file>:<function>"}; idempotent. HANDOFF.md + JSON specs for agent sites. Optional compat shim
     (py+ts) as last resort, always marked.
   - verify: repo typecheck/tests before vs after; requests routed to router-sim; optional --live with $ cap.
   - report: REPORT.md + report.json with every site, outcome, reason, call-site and weighted coverage.
2. router-sim: local mock enforcing the measured behavior above (both surfaces, streaming, catalog validation), logs requests.
3. .claude/skills/router-migrate/SKILL.md + AGENTS.md: agent runs CLI, completes HANDOFF tasks, reruns verify until green.
4. Unit tests with golden before/after fixtures per SDK/framework; cover >90% of rules.
5. eval/: clone pinned repos, baseline tests, run tool, tests again, sim checks, optional live replay.
   eval/SCOREBOARD.md, eval/scoreboard.json (one object per repo with keys: repo, commit, sites_found, migrated, handoff,
   kept, flagged, callsite_coverage, weighted_coverage, tests_before, tests_after, sim_valid_pct, live_success_pct,
   diff_lines, runtime_s), eval/scoreboard.html (self-contained, clean).
   Repos: openai/swarm@6af0b4caf3 TheR1D/shell_gpt@a082bd5327 microsoft/TaskWeaver@d44ddef23f simonw/llm@05d7ae7ded
   abi/screenshot-to-code@a8139779f7 anthropics/anthropic-quickstarts@9ec32b91df simonw/llm-anthropic@b66cdf112c
   di-sukharev/opencommit@a84b7cc597 mckaywrigley/chatbot-ui@81328b61d2 openai/openai-responses-starter-app@0fae283f12
   vercel/ai-chatbot@c2f8235e1f Nutlope/aicommits@a414dd43b6 assafelovic/gpt-researcher@3582a6841f
   langchain-ai/langchain-nextjs-template@fd4f2cd74e mem0ai/mem0@d26ba5e451
   Baseline: if time allows, docs migration prompt via `claude -p` on >=5 repos (separate copies), scored the same way.
6. README.md (pitch, install, one-command quickstart, how it works, rule table, limits), ARCHITECTURE.md, DECISIONS.md,
   PROGRESS.md, WRITEUP.md (1 page for Kedar: results vs baseline, flags, Router issues found, next steps),
   EMAIL_TO_KEDAR.md (draft, never send).
7. Makefile targets: `make test` (tool tests), `make cli-help` (prints CLI --help), `make eval` (full eval).

## Constraints
- Python 3.9 + Node on this machine. Installs only inside this folder. Nothing global.
- Key only from $RAMP_ROUTER_API_KEY; never print/log/commit/write it. Live Router spend cap $5; stop on 402/429.
- Read-only on ~/code/router-live-check. No pushes, PRs, emails, or Router account-setting changes.
- Never fake results; unmeasurable = say so. Timebox: >45 min stuck on one thing -> cut scope, log in DECISIONS.md.
- Priority: OpenAI py/ts chat->responses, Anthropic swap, LangChain flag, Vercel AI SDK before rare cases.

## Milestones (update PROGRESS.md + commit after each)
M1 scan on all 15 repos · M2 router-sim · M3 plan + model map · M4 codemods + unit tests · M5 verify + report ·
M6 eval + scoreboard · M7 handoff + skill + shim · M8 baseline comparison · M9 docs, writeup, CLI polish.
