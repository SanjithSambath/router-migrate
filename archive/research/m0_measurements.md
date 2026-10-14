# M0: live measurements for v2 (2026-10-13)

The script is `research/m0/run_m0.py`. Raw requests and replies are in `research/m0/raw/` (no headers, no key) and the
summary is in `research/m0/results.json`. The run made 29 POST calls and spent **$0.000401** (`eval/live_spend_v2.jsonl`),
then took one read-only GET of the account's strategy settings. Account state during the run:
- `allow_flex_tier_default: true`
- `auto_routing_enabled` (Jev): **false**
- `switchyard_routing_enabled`: **false**

No settings were changed.

| # | Question | Result | Consequence for v2 |
|---|---|---|---|
| 1 | Real Flex discount via `usage.cost` | **gpt-5.4-mini:** flex costs exactly 50% of default on identical prompts (13 in, 5 out tokens; $0.0000177 vs $0.0000355; n=5 per arm, all 5 flex calls served `service_tier: flex`). **gpt-5-nano:** unmeasurable. Every reply was `incomplete` (hit `max_output_tokens`) and Router reported **0 tokens and $0** cost. One `allow_flex_tier: true` call took **61 s** at flex; the other four were served at default. | `tier.flex_background` stays. Impact is shown as **−50% per call (measured on gpt-5.4-mini only)**, and as "unmeasured" for any other model. The 61 s flex latency confirms flex is for the **background** latency class only. |
| 2 | Which models have a flex tier | Earlier sweep: 17 OpenAI models were served at flex by default. Router rejects gpt-4o-mini with "unknown service tier flex". | The guard is per model (`data/flex_models.json`), not per vendor. |
| 3 | `models` fallback list | **Raw HTTP:** `models: [...]` with no `model` works. It advances past a model the key may not use (gpt-5.3-codex, 403 data residency) and past an unknown model id. It works with `service_tier: "default"` (served at default) and with `provider_timeout` across vendors (gpt-5-nano → claude-haiku-4-5). `allow_flex_tier: true` with a `models` list is accepted but **ignored**: the request was served at default. A `models` list with no tier field was served at **flex**, the account default. **openai-python 2.48:** `responses.create(extra_body={"models": [...]})` with no `model` works. **openai-node:** passing `models` as an unknown param works. | `fallback.candidates` stays. The runtime keeps v1's `service_tier: "default"` when it sends a `models` list, so the tier doesn't silently become flex, and it drops `allow_flex_tier` because flex doesn't apply to a list (CONTRACTS §1). |
| 4 | `service_tier: "default"` vs Jev | **Unmeasured.** Jev is off on this account, and turning it on is an account-setting change, which isn't allowed. | There is no Jev rule in the MVP; Jev automation is out of scope. The Router setup checklist states the documented claim (`allow_flex_tier: true` opts a request out of Jev) and marks it unmeasured. v1 keeps sending `service_tier: "default"`. |
| 5 | Switchyard from app code (`model: "switchyard"` on `/v1/responses`) | With `switchyard_routing_enabled: false` but a strategy config saved on the account: **200**, served by `gpt-5.6-luna` at **flex**. Earlier (results_v2 pass A, before a config was saved): 404. Pass B with routing on served gpt-5.6-luna on turns 1–2, then claude-sonnet-4-5 from turn 3. | Setup checklist only: the tool never writes `model: "switchyard"`. The card states that the model family can change mid-conversation and that it rides the account flex default. |

**Router issues found in M0:**
- **Incomplete replies report zero usage and zero cost.** Responses that end `incomplete` on `max_output_tokens` report `usage.input_tokens = 0`, `output_tokens = 0`, `cost = 0`, even though the call ran (once for 61 s). Per-call cost can't be measured on those, and they may be billed or under-reported; unknown which.
- **`allow_flex_tier: true` is silently ignored with a `models` list.** There's no error and no indication in the response beyond `service_tier: default`.
- **A `models` list with no tier is served at flex by the account default.** Code that adds fallbacks therefore also gets flex latency unless it sets `service_tier`.
