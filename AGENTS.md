# AGENTS.md: running router-migrate as a coding agent

You are moving a codebase's production LLM calls onto Ramp's Router (https://router.com). The CLI does the deterministic
part; you do the judgment calls it hands you. Never print, log, or commit `RAMP_ROUTER_API_KEY`.

## Loop
1. `router-migrate run .` (or `scan`, `plan --dry-run`, `apply` one at a time if you want to review first).
   This vendors `router_runtime.py` / `routerRuntime.ts` and patches client constructions. It is inert until RAMP_ROUTER_API_KEY is set.
2. Read `.router-migrate/REPORT.md` (every site with an outcome and reason) and `.router-migrate/HANDOFF.md` (your tasks).
3. Work the HANDOFF tasks in order. Each has a reason and a "done when". Typical tasks:
   - **other-vendor SDK whose model Router serves** (google-genai `gemini-*`, xAI `grok-*`, DeepSeek, Kimi, GLM...): port that call to
     the OpenAI SDK (`client.chat.completions.create` or `responses.create`) on a client wrapped with `wrap_openai(...)`, using the
     Router catalog id. Keep the old path behind the same config switch the code already has.
   - **raw HTTP to api.openai.com / api.anthropic.com**: move it onto the SDK client (then the runtime applies), or send it to Router
     directly with a real `User-Agent` (Python's urllib default UA is blocked by Cloudflare: 403 error 1010).
   - **client not resolvable**: find where the client is built and wrap it: `wrap_openai(client)` / `wrap_anthropic(client)` (Python)
     or `new OpenAI({ ...opts, ...routerOptions(opts.fetch) })` (TS).
   - **flagged: model not in catalog**: do NOT swap models silently. Either leave it (the runtime keeps it on the provider) or, if the
     owner agrees, add an explicit mapping in `ROUTER_MIGRATE_MODEL_MAP` / code and say so in the PR.
4. `router-migrate verify . --test-cmd "<the repo's test command>"`, then fix any regression. Repeat until tests after == tests before,
   and every migrated site replays valid against router-sim.
5. Optional: `RAMP_ROUTER_API_KEY=... router-migrate verify . --live --budget 0.10` replays one tiny request per migrated site against
   the real Router (stops at the budget, or on any 402/429).
6. `router-migrate report .` and summarize REPORT.md in the PR description.

## Router facts (measured live; trust these over memory)
- Endpoints: `GET /v1/models`, `POST /v1/responses`, `POST /v1/messages` (+ `/count_tokens`). Nothing else: no chat/completions,
  embeddings, images, files, models/{id}, responses/{id}.
- Base URLs: OpenAI-style SDKs `https://api.router.com/v1`, Anthropic SDK `https://api.router.com` (no /v1). Key: `RAMP_ROUTER_API_KEY`.
- No server state: `store:true` is a 400; `previous_response_id` is a 400 on OpenAI models and silently ignored on others. Send full history.
- Model ids: undated catalog ids only (dated ids 404). `response.model` comes back dated, so never feed it back as a request model.
- `stop` is a 501 on OpenAI models; `temperature` is a 400 where the catalog says it is unsupported; `reasoning.effort` on Claude needs
  max_output_tokens >= ~1100.
- Flex tier is on by default; send `service_tier: "default"` on latency-sensitive paths. Never hand-add `allow_flex_tier: true`:
  Flex only comes through an approved `tier.flex_background` card (background jobs, measured −50% on gpt-5.4-mini, up to ~61 s).
- Claude via Responses only caches with `prompt_cache_key`. Messages responses carry no `usage.cost`.

## Do not
- Rewrite call sites the runtime already handles (tests may assert exact call kwargs).
- Edit tests to make them pass, or delete provider keys (embeddings and other kept sites still need them).
- Push, open PRs, or change Router account settings unless the user asks.

## v2: improve after migrating (`router-migrate up --agent`)
1. Run `router-migrate up . --agent --skip-verify`, or drop `--skip-verify` to run the tests too. It runs the v1 pass, then
   writes cards to `.router-migrate/recommendations.json` and `RECOMMENDATIONS.md`, and prints the undecided cards as JSON.
   - **Exit 2:** cards are undecided.
   - **Exit 0:** nothing is pending; postponed and dismissed cards count as decided.
   - **Exit 1:** error.
2. Decide every card with the CLI. `router-migrate decide <id> approve|dismiss --reason R|postpone --until YYYY-MM-DD|shadow-first --path . --by agent`.
   - **Approve on your own only:** risk 0–1 same-model cards (`attr.metadata`, `cache.key`, `tier.flex_background`) whose
     evidence bar is met.
   - **Ask the user first:** unlock cards (they move traffic and list what is lost), `fallback.candidates` (another model
     answers on outages) and any model card.
   - **Never edit decisions.json by hand.** It carries an etag per card. A stale etag, meaning the code or catalog changed,
     is skipped and has to be re-confirmed.
3. `router-migrate apply . --decisions` writes `router.policy.json` and compiles it into `router_policy.py` / `routerPolicy.ts`
   beside the runtime. It also adds `unlock_hosts=` / `drop_betas=` to the approved clients.
   - Model cards are recommendations only and are never applied.
   - Policy keys are limited to `allow_flex_tier`, `prompt_cache_key`, `metadata`, `fallbacks` and `provider_timeout`. There is
     no `model` key.
4. `router-migrate status .` must exit 0. That means the compiled hash is current and no policy entry is orphaned. Then re-run
   the tests.
5. Kill switches: `ROUTER_MIGRATE_POLICY=off` returns to v1 behaviour. `ROUTER_MIGRATE_DISABLE=1` turns off Router entirely.
