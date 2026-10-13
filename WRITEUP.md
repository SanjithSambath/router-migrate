# router-migrate: results

**For:** Kedar Thakkar.

**What I built:** a CLI with `scan` / `plan` / `apply` / `verify` / `report`, plus a skill so a coding agent can run it. It moves a codebase's
production OpenAI/Anthropic/LangChain/Vercel AI SDK/LiteLLM traffic onto Router without breaking anything. Every number below is
from `eval/scoreboard.json` and `eval/baseline.json` (printed by `eval/summarize.py`); none are typed by hand.

## The design choice that matters
- **Translate below the call sites.** Instead of rewriting call sites to the Responses API (what the docs' migration prompt asks
  for), router-migrate puts a small vendored runtime *under* each SDK client. It translates `chat.completions` to Router's
  `/v1/responses` and back, SSE included, and passes `/responses` and `/messages` straight through.
- **Edit only constructors.** Codemods change client constructions only (`OpenAI()` becomes `_router_wrap_openai(OpenAI())`). Code that
  reads `.choices[0]`, stream loops and tool-call handling never changes.
- **Off by default, per-request when on.** The runtime is inert until `RAMP_ROUTER_API_KEY` is set. When it is set, each request
  goes to Router only if Router can serve it *exactly*. Everything else (embeddings, `n>1`, `seed`, models outside the catalog,
  provider-side vector stores, beta headers) stays on the original provider, unchanged.
- **Rules come from the live API, not the docs.** Every rule is backed by a live API measurement (`~/code/router-live-check`), and
  router-sim enforces the same rules in tests.

## Results: 15 pinned open-source repos (9 Python, 6 TS)

| | router-migrate |
|---|---|
| production LLM sites found | 170 |
| migrated to Router | **48**: 28% of all sites and 45% of Router-eligible sites. Of the 64 eligible sites that call OpenAI/Anthropic directly, 75% were migrated |
| repos where tests or typecheck got worse (Router off) | **0 of 15** (same counts before and after in every repo) |
| replay through the vendored runtime | router-sim: 24 of 24 unique request shapes valid. **Live Router: 12 of 12 repos with migrated sites at 100%**, total spend $0.000042 |
| diff size | 402 lines across all 15 repos (median 11 per repo) |

**Why the rest was not moved.**
- **Not Router traffic.** Of the 107 Router-eligible sites, 43 point at another vendor's endpoint, Azure, or another gateway: DeepSeek,
  OpenRouter, Groq, Mistral, vLLM, Vercel AI Gateway... Moving them changes the vendor and the model, which is a product decision,
  so they are kept or handed off with the exact reason. Embeddings (35 sites) have no Router endpoint.
- **Per-tenant keys (8 sites).** These clients use a key that each user supplies (chatbot-ui, screenshot-to-code). Moving them would
  bill every tenant to one Router key, so they are flagged rather than silently consolidated.
- **Other flags.** The rest are Anthropic beta headers and runtime-chosen models whose names are not in Router's catalog
  (chatbot-ui's Claude 2/3 list, TaskWeaver's `claude-3-opus-20240229`).

## Versus the baseline (the docs' migration prompt, run by a Claude agent on a fresh copy of each repo)
Both tools are scored per original call site, with the same tests. The baseline scorer is deliberately generous: a site counts as
moved if its file reaches `api.router.com` or `RAMP_ROUTER_*` in any way.
- **Sites moved:** across all 15 repos, the baseline moved 90 of 170 sites to router-migrate's 48.
- **Regressions:** the baseline left failing tests or new type errors in **8 of 15 repos**:
  - llm: 1208 → 1014 passing, 306 failing;
  - llm-anthropic: 123 → 44;
  - shell_gpt: 29 → 0;
  - opencommit: 243 → 181;
  - aicommits: 79 → 72;
  - gpt-researcher: 570 → 552;
  - mem0: 2007 → 1960 passing, 44 failing;
  - ai-chatbot: new type error.

  Counting only repos it left intact, the baseline moved 32 sites. router-migrate moved 48 and broke none.
- **Where the extra sites came from:** the baseline swapped vendors and models. chatbot-ui's Groq, Mistral and Perplexity routes, and
  gpt-researcher's 11 alternative providers, were pointed at Router behind an empty model map it asked the customer to fill in.
- **Diff size:** median 565 lines per repo for the baseline, 11 for router-migrate.
- **What it could not see:** shell_gpt asserts the exact kwargs of each completion call, and llm-anthropic replays VCR cassettes
  keyed to api.anthropic.com. Rewriting call sites breaks both, even when the new code is correct.

## Flags I would want a reviewer to know
- `tests after` is measured with Router **off**, which is exactly what the zero-breakage claim covers.
  - With Router **on**, no repo's test suite sends even one request to router-sim: they all mock the SDK or HTTP by URL. So the
    Router-on correctness evidence is the runtime's own tests through the real SDKs (openai 2.x/3.x, anthropic, LangChain, AI SDK)
    plus static-kwarg replays of each migrated site against router-sim and live Router.
  - The replays use synthetic prompts, not the repos' own code paths.
- An independent adversarial review (`research/adversarial_review.md`) found 3 critical and 6 high issues in my first version.
  Every one is fixed and regression-tested, and the planner over-claims it found are why "migrated" dropped from 57 to 48.
  - The worst one: Responses chains split across Router and OpenAI on turn 2.
  - The full list is in DECISIONS D10.
- `previous_response_id` chains that start on Router are continued in process memory, with the history sent inline. On a
  multi-process server, a turn that lands on another worker gets a clear error; the planner keeps stateful Responses clients off
  Router for that reason.
- Live verification costs almost nothing: the whole live replay cost $0.000042. The live checks behind the rules cost about $0.15.

## Router issues found (live, against api.router.com)
1. **No server-side state.**
   - `store:true` returns 400, and `previous_response_id` returns 400 on OpenAI models and is silently ignored on others.
   - `responses.retrieve` returns 404, and conversations are unavailable.
   - Any Responses app that chains turns breaks on turn 2 unless something replays history. router-migrate does that replay for
     you; the docs prompt doesn't mention it.
2. **Flex tier is the account default and was applied without opt-in** (gpt-5-nano was flex 5 of 5 times). It also flipped tier
   between turns of one conversation. router-migrate sends `service_tier: "default"` unless the caller chose a tier.
3. **The catalog lists models the key can't call:** `gpt-5.3-codex` returns 403 `data_residency_mismatch`. The runtime falls back to
   the provider once and remembers the refusal.
4. **Claude via Responses only caches with `prompt_cache_key`:** 0 cached tokens without it, about 90% cheaper with it. The runtime
   adds it per feature.
5. **Dated model ids return 404,** so pinned snapshots can't move without a mapping. router-migrate maps only the 22 equivalences
   Router itself reported.
6. **`reasoning.effort` on Claude returns 400 when `max_output_tokens` < ~1100** (thinking budget ≥ 1024 doesn't fit).
7. **Timeouts return a plain-text 504 with no request id.** Messages responses carry no `usage.cost`. Fireworks-served models report
   `input_tokens: 1` on Messages.
8. **Python's stdlib `urllib` User-Agent gets Cloudflare 1010.**
9. **Switchyard changed model family mid-conversation:** gpt-5.6-luna on turns 1–2, claude-sonnet-4-5 from turn 3. It works as
   `model: "switchyard"` once a strategy is saved. Callers can't rely on one model's behavior across turns.

## Next steps
1. **Move per-tenant traffic.** Router per-tenant keys or sub-accounts would let the 8 flagged per-tenant sites move without
   collapsing billing. That is a Router feature request, not a tool change.
2. **Map gateway and vendor models.** A model-mapping UX for other gateways and vendors: show each alternative-vendor site with the
   closest Router model and let the customer accept per model. That is the 43-site pool.
3. **Shared chain store.** Replace the in-process chain memory with a pluggable store (Redis) for multi-worker servers. Better: Router
   supporting `previous_response_id` itself.
4. **Live replay through the repos' own code paths.** Import the patched module and call the site's function with recorded inputs,
   instead of synthetic requests built from literal kwargs.
5. **More eval repos.** Add Go and Java SDK codemods, and run a larger repo sample (the 15 pinned repos skew toward demo apps).
