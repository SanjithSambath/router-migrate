# router-migrate v2: spec (the Improve phase)

## Goal
v1 moves production LLM calls onto Router losslessly (48 of 170 production sites in 15 repos, 15/15 test suites unchanged).
On its own that leaves the bill where it was: same models at list price, and v1 sends `service_tier:"default"`, which
switches off Flex. v2 keeps v1 as the automatic first pass, unchanged, and adds an **Improve** phase:

1. **Unlock**: move MORE traffic when the customer confirms a mapping or accepts a stated loss (the only part that raises
   Kedar's scored metric: share of traffic moved x nothing broken).
2. **Same-model improvements** (tier, cache key, timeouts, fallbacks, spend tags) applied through a compiled per-feature policy.
3. **Model-change recommendations** applied as visible code/config edits, each backed by an evidence ladder.
4. **Router setup checklist** for account-only settings (Jev, Switchyard, shadow models, budgets, BYOK).
5. **One decision file** shared by a Router-styled local dashboard and coding agents. One command: `router-migrate up`.

Design doc (summary of it is this file; this file is authoritative): "Router Migrate v2: From Migration to Improvement — Plan".

## Ground truth (beats training data; measurements beat docs)
- Everything in SPEC.md "Ground truth", DECISIONS.md D1-D10, eval/logs/*.plan.json, router_migrate/data/catalog.json.
- Docs (read the .md variants, e.g. https://docs.router.com/strategies/shadow-models.md; index at /llms.txt):
  strategies/cost-efficient-routing, strategies/auto-routing (Jev), strategies/switchyard-routing, strategies/shadow-models,
  strategies/benchmark-routing, strategies/cache-optimizations, strategies/cross-provider-routing, guides/fallbacks,
  guides/control-spend, guides/monitor, guides/bring-your-own-key, api/metadata, api/models, api/responses-and-messages,
  api/errors-and-limits. Blog: https://builders.ramp.com/post/thompson-sampling-model-routing
- Facts already established (re-verify the starred ones in M0):
  - Flex is on by default (account/profile switch). Per request: `allow_flex_tier` true|false|null (Responses only), or a
    `:flex` / `:priority` third segment in the model id. Applies only to a single `model` with no `service_tier`.
    Tiers: OpenAI default/flex/priority, Google standard/priority/flex, Sail default/flex, Fireworks+xAI default/priority,
    Anthropic none (has `speed:"fast"`), Bedrock standard. Streamed Flex attempt falls back after 25 s with no output.
    Flex price is NOT in the catalog (catalog pricing = standard). *Docs conflict: allow_flex_tier:true on ineligible
    models is "ignored" vs "400" -> only send it to models with a flex tier.
  - `allow_flex_tier:true` opts the request out of Jev and Switchyard. Jev and Switchyard are mutually exclusive.
  - Jev auto routing: 2-6 described models on the Strategies page per routing profile; each API key has one profile.
  - Switchyard: Strategies page + `model:"switchyard"`; documented for coding agents only. Measured: served gpt-5.6-luna
    turns 1-2, then claude-sonnet-4-5 from turn 3 (model family switch mid-conversation).
  - Shadow models: Strategies page, 1-3 candidates + sample %, POST /v1/responses only, needs content recording, not on
    BYOK keys, shadow calls not billed, records cost+latency (judge agreement "coming soon"), NO read API (Shadow Logs page).
  - Fallbacks: `models:[...]` (1-15 candidates, provider:model[:tier]) instead of `model`, Responses only; advances on
    retired model/no permission/429/5xx/network/pre-stream failure; refusals end the request. *Not measured yet.
  - Caching: `prompt_cache_key` (provider affinity 5 min; `prompt_cache_retention:"24h"` on OpenAI*). Measured: Claude
    cached 10,362 tokens with the key, 0 without.
  - Metadata: any `metadata` object on Responses/Messages; stored as request tags; dashboard can break spend down by them;
    `metadata.product` / `metadata.user` attribute spend in Ramp. Only `usage.cost` (Responses replies) and GET /v1/models
    are machine-readable. NO usage/logs/analytics API.
  - Benchmark aliases: `owned_by:"router"` with `router_alias` in /v1/models; no pricing; limits = min across candidates.
  - Budgets: per-key caps (401 when hit). BYOK and budgets are account settings.
  - Router-only fields (allow_flex_tier, models, timeouts) are NOT supported on /v1/messages; usage.cost not on Messages.
  - Catalog (Oct 12-13 snapshot): 98 models with pricing; 7 OpenAI models marked deprecated.
- Eval facts for v2 (from eval/logs/*.plan.json): of 48 migrated sites only 9 have a static model literal; 39 pick the
  model at runtime. Kept production sites: 38 call another vendor's endpoint, 7 Azure deployments, 6 Anthropic beta
  headers, 8 per-tenant credentials, 9 handoffs for Vercel AI Gateway, ~35 embeddings (not unlockable).

## Hard design rules
1. **v1 is the default path.** No decision = v1 behavior byte for byte. v1 scoreboard numbers must not change.
2. **Policy never changes which model answers in normal operation.** Policy keys allowed: `allow_flex_tier`,
   `service_tier`, `prompt_cache_key`, `prompt_cache_retention`, `provider_timeout`, `timeout_before_headers`,
   `metadata` (merged), `fallbacks` (sent as Router `models:[original, ...fallbacks]`), `speed`, `observe`. No `model`
   key, no generation params. Model, effort and alias changes are code/config edits shown in the diff.
3. **Policy is compiled, not read from disk at runtime.** `router.policy.json` (committed) -> `apply` compiles it into
   `router_policy.py` / `routerPolicy.ts` beside the vendored runtime, imported like a module so serverless/edge bundles
   include it. Runtime logs the policy hash once. `router-migrate status` exits non-zero if compiled hash != JSON hash or
   an entry matches no site.
4. **Precedence:** exact feature id > site/client id > glob > defaults. If `allow_flex_tier` is set, omit v1's
   `service_tier:"default"`. Original model is always first in a fallback list; v1's provider fallback (D10) still applies
   after Router's list, only for pre-generation refusals. Capability guard: an override applies only if the catalog says
   the target supports the request (surface, tools, structured output, modalities, context, tier); otherwise send v1's
   request and log once. Invalid policy / `ROUTER_MIGRATE_POLICY=off` => v1 behavior.
5. **Stable ids.** Codemods bake a literal client id into each wrap (`_router_wrap_openai(OpenAI(), _rm_id="...")`,
   `routerOptions({id:"..."})`); keep idempotency and old wraps working. Python also derives `file:function`; TS relies on
   the baked id (minified stacks).
6. **Model owner per site:** `app_literal` | `operator_config` (env/config default the scanner can follow) | `end_user`
   (CLI flag, user config, request param) | `library_param` (the repo is a library) | `unknown`. Model-change cards only for
   `app_literal` / `operator_config`.
7. **Nothing applies itself.** Only `decisions.json` drives `apply --decisions`. Approvals carry the card's etag =
   hash(site code span + that model's catalog row); stale etag => re-confirm.
8. **Unlock is reported beside, never inside, the lossless headline:** `lossless_migrated` and `migrated_with_unlocks`.
9. **Restraint:** <=3 options per site; top 10 cards visible; every rejected lever listed under "Considered, not
   recommended" with its reason; every card prints the rule id and its inputs; savings shown as % per call from catalog
   list prices until observe-mode data exists, with an "ignores" line.
10. **Local server security:** bind 127.0.0.1, random session token in URL + header, reject foreign Origin/Host (DNS
    rebinding), body size cap. Server writes decisions.json and may invoke the same CLI apply an agent would (echo it).
11. **Replay privacy:** prompts from repo tests/fixtures/cassettes first; live capture only with `--capture` opt-in; never
    committed (gitignored); tools stubbed; single turns; per-run $ cap shown before running; prompts do leave the machine
    for Router (say so).
12. **Python and TS runtime parity** via shared JSON fixtures run against router-sim.

## Levers (rule ids; risk 0-4; min evidence; applied via)
Unlock (first):
- `unlock.vendor_endpoint` - client targets another vendor's endpoint and the model is in Router's catalog (exact or
  measured equivalence) -> move to Router, same model. risk 2, L0 + live replay, code edit. Loses vendor's own serving.
- `unlock.azure_mapping` - Azure deployment -> catalog model only after customer confirms mapping in decisions. risk 2.
- `unlock.stated_loss` - kept for a param (seed, presence_penalty, max_tokens<16, n>1, logprobs, beta header, ...) ->
  move if the customer accepts dropping/emulating it; card lists exactly what is lost. risk 1-3, code edit.
Same model:
- `attr.metadata` (0, none, policy) · `budget.per_key` (0, none, account) · `cache.key` (0, L1, policy; needs static
  system prompt >= ~1,024 tokens or a shared prefix) · `tier.flex_background` (1, L0+L1, policy; OpenAI/Google/Sail models
  with a flex tier, latency class background only) · `tier.priority_interactive` (1, L1, policy; costs more, say so) ·
  `speed.fast_claude` (1, L1, policy) · `fallback.candidates` (2, L0 capability match, policy; never in bulk group) ·
  `timeout.provider` (1, L1, policy).
Model (code/config edits; app_literal/operator_config only):
- `model.deprecated` (2, L0+L2) · `model.effort_lower` (2, L2) · `model.newer_cheaper_same_family` (2, L2) ·
  `model.right_size` (3, L2, L3 advised) · `model.router_choose` (Jev/benchmark alias; 3, L3; account + edit) ·
  `model.switchyard` (experimental; tool-calling loops only; 3, L3) · `model.cross_vendor` (4, L3).
Evidence ladder: L0 catalog fact · L1 static signal · L2 offline replay (exact match for structured output, LLM judge via
Router otherwise; store per-feature dataset) · L3 Router shadow (tool emits exact Strategies config; customer records
result on the card) · L4 observed in production (observe mode).
Signals added to scan: latency_class (interactive | user_waiting | background), model_owner, resolved_model,
request_shape (tools, structured_output, max_output, static_system_prompt_tokens, multi_turn), task_hint (weak).

## Data contracts (all under <repo>/.router-migrate/ unless noted; JSON Schemas in router_migrate/schemas/)
- `recommendations.json`: {catalog_version, rules_version, cards:[{id, rule, lever_group(unlock|same_model|model|setup),
  title, scope:{sites[], features[], latency_class}, model_owner, change:{kind(policy|code|config|account), diff},
  loses, rule_inputs, evidence:{level, results[]}, impact:{per_call_pct, monthly_usd|null, ignores}, risk, effort,
  options[<=3], etag, state}], considered_not_recommended:[{rule, scope, reason}]}
- `decisions.json`: {cards:{<id>:{action(approve|dismiss|postpone|shadow_first), reason?, until?, etag, by(ui|agent|cli), at}}}
- `RECOMMENDATIONS.md`: human/agent view; `- [ ] <!-- rec:<id> --> title`; ticking = approve; two-way sync with decisions.json.
- `router.policy.json` (repo root, committed) -> compiled `router_policy.py` / `routerPolicy.ts` beside the runtime.
- `events.jsonl`: {ts, stage(scan|plan|apply|verify|report|recommend|evidence|apply_decisions), status, counts, cost_usd, msg}
- `REPORT.html`: the dashboard frozen with its data (self-contained, no network).

## CLI
- `router-migrate up [path] [--agent] [--no-browser] [--live] [--max-usd N]`: scan, plan, apply (lossless), verify,
  recommend, report; opens dashboard unless --agent/--no-browser. Exit 0 = nothing pending, 2 = undecided cards, 1 = error.
  Postponed/dismissed count as decided.
- `recommend`, `decide <id> approve|dismiss --reason R|postpone --until DATE|shadow-first`, `apply --decisions`,
  `status` (effective setup per feature, policy hash, orphans), `evidence replay <id> --max-usd N [--capture]`,
  `evidence shadow-config <id>`. Keep all v1 subcommands working.
- Runtime env: `ROUTER_MIGRATE_POLICY=off`, `ROUTER_MIGRATE_OBSERVE=<path|stderr>` (per-feature counters: calls, tokens,
  usage.cost, latency, errors; no content), plus v1's.

## Dashboard (single self-contained HTML, no build step, stdlib server)
Tabs: Overview (live stage stepper, funnel all-gen-AI -> Router-servable -> moved -> needs review -> kept, KPI tiles,
live-check cost) · Migration (site table, emulated/dropped param chips, diff drawer) · Improve (Unlock cards, then cards by
evidence: bar met / needs evidence / experimental, then "Considered, not recommended"; bulk approve for risk 0-1 bar-met
cards, unselected by default; sticky "Apply N changes" bar; card: title with the number, scope chips, owner, diff, loses,
rule, evidence dots L0-L4, risk, effort, Apply / Shadow first / Postpone / Dismiss(reason)) · Router setup (checklist
with exact Strategies values + links to app.router.com/strategies, Mark done) · Unsupported (embeddings/images/audio/
files/batches by site with reason).
Visual language (Router docs = Ramp "Ryu" cement theme; match it, never copy logos/wordmarks or load Lausanne):
bg #FFFFFF, subtle #FCFBFA, muted #F4F2F0, border #E9E5E2, strong border #D2CECB, text #1A1919, secondary #6E6A68;
dark: bg #1A1919, subtle #201F1E, muted #2A2827, border #32302F, text #D2CECB, secondary #A39D99.
Accent "solar" #E4F222 with #1A1919 text, ONLY for the primary action. Positive #26763B/#E7F3EA, negative #B74018/#F9EAE2,
warning #876634/#F6F2D0, info #425E93/#D8DEED. Inter (stand-in for Lausanne), headings weight 400, -0.02em; IBM Plex Mono
for every number, $ value, model id, policy key. 4px spacing scale; radius 8 (buttons/inputs) 12 (panes); 1px borders, no
card shadows; buttons 32/40px; middle-dot metadata ("Risk 1 · Evidence L0 · policy only"); savings beside baseline.
Fonts may load from Google Fonts with a system fallback; everything else inline. Works offline with system fonts.

## Milestones (each: tests + PROGRESS.md + commit; Done-when must be demonstrably true)
M0 measure (~$1 live): service_tier:"default" vs Jev; real Flex discount via usage.cost on identical prompts (flex vs
   default, n>=5 each); `models` fallback through openai-python/openai-node with extra_body; OpenAI 24h cache retention;
   Switchyard via Responses from app code. Raw results -> research/m0/ ; summary research/m0_measurements.md; DECISIONS D11.
   Rules that depend on a fact that failed to verify are cut or marked, not guessed.
M1 unlock rules + second coverage number on the 15 repos (with unlocks accepted: tests/sim/live replay, 0 new failures).
M2 signals + recommendations.json schema + all same-model rules + golden tests per lever; owner accuracy checked by hand
   on all 48 migrated sites (eval/owner_audit.md).
M3 compiled policy in both runtimes (precedence, capability guard, baked ids, observe mode) + `status`; router-sim tests
   for every key; invalid policy => v1 behavior; py/ts parity on shared fixtures.
M4 decisions.json + RECOMMENDATIONS.md sync + `decide` + `apply --decisions` + exit codes + `--agent` + AGENTS.md and
   .claude/skills/router-migrate/SKILL.md updated; agent vs scripted-human produce identical decisions on 3 repos.
M5 dashboard: events, hardened server (tests for token/Origin/Host rejection), REPORT.html; Playwright screenshots of every
   tab on langchain-nextjs-template (light + dark) -> eval/screenshots/.
M6 evidence: L2 replay runner (fixtures first, stubbed tools, $ cap, judge via Router), shadow-config generator, model
   rules as code/config edits; add 3-5 app-style OSS repos with hard-coded models in production routes (pin commits,
   justify choice in eval/README.md); one model card climbs L1 -> L2 with results shown.
M7 eval v2 + docs: eval/scoreboard_v2.json/.md/.html on 15 + app-style repos (held-out 30: run if time, else state status
   honestly); eval/RUBRIC_V2.md + adjudication of every card; README v2 section, ARCHITECTURE, DECISIONS, PROGRESS, WRITEUP
   (<=1.5 pages: v1 vs v2 numbers, unlock gain, cards by lever, precision, what's unmeasured, Router issues found),
   EMAIL_TO_KEDAR.md updated (draft, never send). eval/reports/<repo>/REPORT.html for every repo.

scoreboard_v2.json: {"totals":{lossless_migrated, migrated_with_unlocks, sites_found, repos, repos_tests_worse_lossless,
repos_tests_worse_unlocks, cards_total, cards_bar_met, precision_bar_met|null, live_spend_usd}, "repos":[{repo, commit,
lossless_migrated, unlock_candidates, migrated_with_unlocks, tests_after_unlocks, cards_total, cards_by_group,
cards_bar_met, model_cards_on_non_owned_sites, same_model_safety_ok, report_html}]}

## Constraints
- Installs only inside this folder. Key only from $RAMP_ROUTER_API_KEY; never print/log/commit/write it.
- Live Router spend for this run <= $5 total; append every live call's usage.cost (or estimate if absent, marked) to
  eval/live_spend_v2.jsonl; stop live work on 402/429.
- Read-only on ~/code/router-live-check. No pushes, PRs, emails, Router account-setting changes (Strategies page stays
  untouched; the tool only emits configs).
- Never fabricate numbers; unmeasured = say so. Don't edit scripts/done_check*.sh. v1's scripts/done_check.sh must still pass.
- >45 min stuck: cut scope, log in DECISIONS.md. Small commits.
