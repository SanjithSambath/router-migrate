# v2 data contracts (MVP)

This file is authoritative for the v2 MVP's components. JSON Schemas live beside it. All files live under
`<repo>/.router-migrate/` unless the entry says otherwise.

## 1. `router.policy.json`

Lives at the repo root and is committed.

```json
{
  "version": 1,
  "defaults": {"metadata": {"team": "search"}},
  "globs":    {"src/workers/**": {"allow_flex_tier": true}},
  "features": {"src/app.py:ask": {"prompt_cache_key": "$feature", "fallbacks": ["gpt-5.4-mini"], "provider_timeout": 60}}
}
```

**Allowed keys per entry:**

| Key | Type | Notes |
|---|---|---|
| `allow_flex_tier` | bool | |
| `prompt_cache_key` | string | `"$feature"` is replaced by the feature id |
| `metadata` | object of string to string | |
| `fallbacks` | list of 1–14 catalog ids | |
| `provider_timeout` | number, 1–660 | seconds |

There is **no `model` key** and there are no generation parameters. Any other key, or a wrong type, makes the whole policy
**invalid**. An invalid policy means v1 behaviour.

**Feature id.** It is the v1 runtime's `metadata.feature` value, `<relative file>:<function>`.
- Python takes it from the call stack.
- TS takes it from the stack too, via `feature()` in routerRuntime.ts.

**Precedence and merging.** Exact feature beats the longest-matching glob, which beats defaults. Per key, the most
specific entry wins. `metadata` is merged: defaults < glob < feature, and the request's own metadata keys win over all
three. Globs use fnmatch semantics on the feature id: `*` matches within a path segment, `**` matches anything.

**Application.** Policy applies only to requests Router serves on `POST /v1/responses`, whether translated chat or the
Responses passthrough. `/v1/messages` gets no policy (Router-only fields are not supported there), and the runtime logs
that once.

**Effect of each key** on the Router request body `nb`:
- `metadata`: merged into `nb.metadata`, keeping the 16-key cap. When full, policy keys are dropped first.
- `prompt_cache_key`: set if `nb` has none.
- `provider_timeout`: set if `nb` has none.
- `allow_flex_tier`:
  - Set it only when the capability guard allows it.
  - When it is set, delete v1's automatic `service_tier: "default"`. A `service_tier` the caller set themselves is kept,
    and in that case `allow_flex_tier` is NOT sent.
- `fallbacks`:
  - After the capability guard, if any candidates remain, `nb.models = [nb.model, ...candidates]` and `nb.model` is
    deleted.
  - Router rejects `model` and `models` together (measured 400).
  - `allow_flex_tier` is then dropped, because flex applies only to a single `model` (docs).

**Capability guard.** It uses the catalog compiled into the runtime. A key that fails the guard is skipped, and logged
once per (feature, key).
- `allow_flex_tier: true` needs the model to have a flex tier: catalog row `c.f == true`. That comes from
  `router_migrate/data/flex_models.json`, the 17 models measured to be served at flex. gpt-4o-mini, for example, returns
  400 "unknown service tier flex".
- `allow_flex_tier: false` is always allowed.
- **`fallbacks` candidate:** each candidate must:
  - be in the catalog, status active, with surfaces including `responses`;
  - have `tools: true` if `nb.tools` is present;
  - have `structured_outputs: true` if `nb.text.format.type` is `json_schema` or `json_object`;
  - accept the request's modalities (image or file parts);
  - not be OpenAI-owned when `nb.stop` is present (OpenAI returns 501 on `stop`);
  - not have `temperature: false` when `nb.temperature` is set;
  - not be the original model.

  Candidates failing any check are dropped.
- **Hash and status.** `sha256` of the canonical JSON (sorted keys, no spaces) of the parsed policy. The compiled module
  carries `POLICY` and `POLICY_HASH`, and the runtime logs `router-migrate policy <hash8>` once on first use.
  `ROUTER_MIGRATE_POLICY=off` disables the policy.

**Compiled targets.** These sit beside the vendored runtime and are written by `apply --decisions`:
- `router_policy.py`: `POLICY = {...}`, `POLICY_HASH = "..."`. The runtime imports it lazily as a sibling module; if it is
  absent, the runtime behaves as v1.
- `routerPolicy.ts`: `export const POLICY = {...}; export const POLICY_HASH = "...";`.
  - The TS runtime gets the policy through `globalThis.__ROUTER_MIGRATE_POLICY__`, which `routerPolicy.ts` sets when it is
    imported.
  - The codemod adds `import "./routerPolicy"` (with the correct relative path and extension, the same as the runtime
    import) next to each runtime import, but only when a policy exists.
  - That way v1 files are untouched when there is no policy.

**Shared parity fixtures.** `tests/fixtures/policy/*.json` holds
`{"name", "policy", "feature", "request_path", "request": <SDK-level body>, "expect": {"router_path", "body_has": {...}, "body_lacks": [...]}}`.
- Both runtimes must pass every fixture.
- `request` is what the SDK would send to the provider (a chat.completions or responses body).
- `expect.body_has` is a subset match on the Router body; `body_lacks` lists top-level keys that must be absent.

## 2. `recommendations.json`

```json
{"catalog_version": "...", "rules_version": "v2-mvp-1", "repo": "owner/name", "generated": "...",
 "cards": [{"id": "rec_<rule>_<hash8>", "rule": "cache.key", "lever_group": "unlock|same_model|model|setup",
   "title": "Cache the 1,800-token system prompt in summarize()",
   "scope": {"sites": ["<site id>"], "features": ["src/app.py:summarize"], "latency_class": "background"},
   "model_owner": "app_literal|operator_config|end_user|library_param|unknown",
   "change": {"kind": "policy|code|config|account", "diff": "<unified diff or policy JSON patch>"},
   "loses": ["..."], "rule_inputs": {"...": "..."},
   "evidence": {"level": "L0|L1", "results": [{"level": "L0", "fact": "...", "source": "catalog|measured|static"}]},
   "impact": {"per_call_pct": -50, "monthly_usd": null, "ignores": "..."},
   "risk": 0, "effort": "none|low|medium", "options": [], "etag": "<sha256 hex 16>", "state": "new|approved|dismissed|postponed|shadow_first|applied",
   "applies": true}],
 "considered_not_recommended": [{"rule": "tier.flex_background", "scope": {"features": ["..."]}, "reason": "interactive latency class"}]}
```

`applies: false` marks model cards (cards only in the MVP; never applied).

## 3. `decisions.json`

```json
{"cards": {"<card id>": {"action": "approve|dismiss|postpone|shadow_first", "reason": "...", "until": "YYYY-MM-DD",
  "etag": "<card etag at decision time>", "by": "ui|agent|cli", "at": "<iso>"}}}
```

## 4. Dashboard bundle

`REPORT.html` embeds one JSON object, `window.__RM__`:

```json
{"repo": "owner/name", "root": "/abs/path", "generated": "...", "tool_version": "...",
 "stages": [{"stage": "scan", "status": "done|running|error|skipped", "counts": {}, "msg": "..."}],
 "summary": "<plan.json summary>",
 "funnel": {"all_gen_ai": 0, "router_servable": 0, "moved": 0, "needs_review": 0, "kept": 0},
 "kpis": {"lossless_migrated": 0, "migrated_with_unlocks": 0, "cards_total": 0, "live_spend_usd": 0.0, "tests_before": {}, "tests_after": {}},
 "sites": "<plan.json sites[] with outcome/reason, plus 'chips': [...]>",
 "diff": "<apply.diff text>",
 "recommendations": "<recommendations.json>",
 "decisions": "<decisions.json>",
 "setup": [{"id": "setup.jev", "title": "...", "why": "...", "values": {"...": "..."}, "link": "https://app.router.com/strategies", "done": false}],
 "unsupported": [{"site": "<id>", "file": "...", "line": 0, "surface": "embeddings", "reason": "..."}],
 "policy": {"exists": false, "hash": null, "compiled_hash": null, "orphans": []}}
```
