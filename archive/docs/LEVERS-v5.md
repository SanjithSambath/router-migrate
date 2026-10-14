# Levers

A lever is anything that changes what a Router request looks like, or whether a call reaches Router at all. This page
lists every lever router-migrate knows, with one policy per lever.

The policy follows one rule: **the less a lever loses, the less approval it needs.**

| Tier | Who decides | Applied how | Examples |
|---|---|---|---|
| Automatic | code | compiled policy, on every migration | spend tags, cache keys, timeout guardrail, standard-tier pin |
| Agent, with Undo | the migrating agent; a human can undo | `router-migrate adapt`; shown on the dashboard with Undo | drop `seed`, dated snapshot to current id, operator key only, drop a performance-only beta, another vendor's endpoint |
| Human card | a human, on the dashboard | `router-migrate apply . --decisions` after approval | Flex on user-facing calls, fallbacks, a different model, vendor or code ports |
| Hard limit | nobody | never applied | no Router endpoint, provider-side state the code uses |

Two things are never ours to change:

- **Provider selection.** Router picks which upstream serves a catalog model. We never pin a provider.
- **The code's own request.** Every lever applies to the Router-bound copy of a request only. When the runtime falls back
  to the provider, it sends exactly what the code built.

Every lever is considered for every migrated call. A lever that does not fire is listed on the dashboard with its reason
("gpt-4o has no Flex tier", "static prefix ~490 tokens, below ~1,024"). Silence is never an answer.

`router-migrate levers FILE:LINE` prints the facts behind each lever for one migrated call: Flex availability, list
prices, capability-compatible fallbacks, the cache threshold and the rule defaults.

---

## Automatic (lossless)

These apply on every migration. They lose nothing the app relies on, so they need no approval. Each is still listed on the
dashboard and can be turned off.

### Spend tags

| | |
|---|---|
| What it does | Adds `metadata.feature` (`<file>:<function>` of the calling code) and `metadata.product` (the repo) to every Router request. Also sent as `x-router-migrate-feature` and `x-router-migrate-client` headers. |
| Router default | No tags. Spend is one number per key. |
| Our policy | Always on. |
| Who decides | Code. |
| What is lost | Nothing in the app. A repo-relative file and function name is sent to Router. `ROUTER_MIGRATE_FEATURE_TAG=off` stops it. |

### Prompt cache keys

| | |
|---|---|
| What it does | Sets `prompt_cache_key` per feature. |
| Router default | Claude models served through `/v1/responses` do not cache without a key (measured). |
| Our policy | Always on. The key costs nothing. It only saves money when the static prompt prefix is above ~1,024 tokens; `levers` reports where that is true. |
| Who decides | Code. |
| What is lost | Nothing. |

### Timeout guardrail

| | |
|---|---|
| What it does | Sets `provider_timeout` (30 s by default, `ROUTER_MIGRATE_GUARD_TIMEOUT`) on calls a person waits on that do not stream. |
| Router default | No timeout. |
| Our policy | On for buffered user-waiting calls. A Router request that hits it fails over to the provider once, instead of hanging the user. |
| Who decides | Code, from the agent's latency judgment. |
| What is lost | Nothing the user sees. A slow request is answered by the provider instead. |

### Standard-tier pin on user-facing calls

| | |
|---|---|
| What it does | Sends `service_tier: "default"` on interactive and user-waiting calls. |
| Router default | **Flex is on for the account.** Flex is about half price (measured −50% on gpt-5.4-mini) and can take up to ~61 s per call (measured on gpt-5-nano). |
| Our policy | Pin the standard tier where a person waits. Background calls keep Router's Flex default. |
| Who decides | Code, from the agent's latency judgment. |
| What is lost | The Flex discount on user-facing calls. Turning Flex back on there is a human card (below). |

---

## Agent takes, with Undo (near-lossless)

These move traffic that would otherwise stay on the provider. Each costs something small and concrete. The agent takes
them by default when a reasonable engineer would accept the cost, records what is lost, and the dashboard shows each one
with an Undo. Policy levers leave the code untouched. Client levers re-wrap one client with an option.

All are taken with:

```bash
router-migrate adapt FILE:LINE <lever> --loses "<what is given up>" --why "<why it is acceptable>"
router-migrate adapt --undo T3        # the trade-off id the dashboard and ledger show
```

### Drop parameters Router rejects

| | |
|---|---|
| Lever | `--drop seed` (also `presence_penalty`, `frequency_penalty`, other sampling knobs) |
| What it does | Removes the parameters from the Router-bound request only. |
| Router default | Requests with these parameters stay on the provider (the runtime cannot translate them). |
| Our policy | Agent takes it. Example: AI-Scientist's `seed=0` kept three calls on OpenAI under every earlier version. |
| Who decides | The agent. A human can undo. |
| What is lost | Best-effort determinism (`seed`) or the penalty's effect. The provider fallback still sends them. |

### Dated snapshot to the current id of the same model

| | |
|---|---|
| Lever | `--model-map gpt-4o-2024-05-13=gpt-4o` |
| What it does | Serves a dated model id with the catalog id of the same model. |
| Router default | Dated ids return 404. Only catalog ids are served. |
| Our policy | Agent takes it when the target is the same model family and generation. A different model is a human card. |
| Who decides | The agent. A human can undo. |
| What is lost | Pinning to one snapshot. Output can drift when the catalog id moves to a newer snapshot. |

### Route only the operator's key

| | |
|---|---|
| Lever | `--operator-key-only` |
| What it does | Routes requests whose key equals the operator's `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. Requests made with a user's own key stay on the provider. |
| Router default | One Router key per deployment, so all traffic would share it. |
| Our policy | Agent takes it when the app has an operator key and users can bring their own. Where no operator key exists (page-assist), nothing moves, and the call is not migrated. |
| Who decides | The agent. A human can undo. |
| What is lost | Nothing for users. Operator traffic is billed through Router. |

### Drop a performance-only beta

| | |
|---|---|
| Lever | `--drop-betas <flag>` |
| What it does | Removes an Anthropic beta header from the Router-bound request. |
| Router default | Requests with beta headers stay on the provider. |
| Our policy | Agent takes it only when the beta affects speed or cost, not behavior (e.g. a prompt-caching or token-efficiency beta). A beta that *is* the feature (computer use, code execution, MCP) is a hard stop. |
| Who decides | The agent. A human can undo. |
| What is lost | The beta's effect, stated in `--loses`. |

### Another vendor's endpoint for the same model

| | |
|---|---|
| Lever | `--unlock-host api.x.ai` (plus `--model-map` when the vendor's id differs from Router's) |
| What it does | Routes requests the code sends to another vendor's OpenAI-compatible endpoint, when Router serves that model. |
| Router default | The runtime only reroutes requests addressed to OpenAI or Anthropic. |
| Our policy | Agent takes it when the model is the same one Router serves. If the move changes the model, it is a human card. |
| Who decides | The agent. A human can undo. |
| What is lost | That vendor's own serving, rate limits and billing relationship. |

---

## Human cards

These change cost, latency or behavior in a way a product owner should own. The agent and the rules propose them. A human
approves, dismisses or postpones each on the dashboard. Nothing applies until then.

### Flex on user-facing calls

| | |
|---|---|
| What it does | Removes the standard-tier pin from an interactive or user-waiting call, so Router's Flex default applies. |
| Router default | Flex on. |
| Our policy | Card. Offered only on models with a Flex tier (17 measured; `gpt-4o-mini` and `gpt-4o` have none). |
| Who decides | A human. |
| What is lost | Latency. A Flex call can take up to a minute. |

### Fallbacks

| | |
|---|---|
| What it does | Sends `models: [original, candidate, ...]`. Router tries the next model when one fails before answering. Candidates are capability-matched (tools, structured output, modalities, `stop`, `temperature`). |
| Router default | One model, no fallback. |
| Our policy | Card. |
| Who decides | A human. |
| What is lost | On failure, **a different model answers**. Also, **fallbacks cancel Flex**: Router serves a `models` list without Flex (measured), so Flex and fallbacks on one feature are one choice, shown as an either/or pair. |

### A different model

| | |
|---|---|
| What it does | Recommends a newer, cheaper or better-sized catalog model. |
| Router default | The model the code asks for. |
| Our policy | Card only, and only where the app or its operator owns the model choice. **Never applied automatically.** The card suggests a shadow run first. |
| Who decides | A human. |
| What is lost | The current model's behavior. |

### Vendor and code ports

| | |
|---|---|
| What it does | Moves a call that needs a code change, not a lever: Azure OpenAI deployments, Bedrock, Vertex, google-genai and other vendor SDKs whose model Router serves. |
| Router default | Not reachable without a code change. |
| Our policy | Card with the change described. The agent may make the port only if the human approves it. |
| Who decides | A human. |
| What is lost | Depends on the port: the vendor's SDK features, regional hosting, contractual terms. |

---

## Hard limits

These stay on the provider. No lever moves them.

| Limit | Examples |
|---|---|
| No Router endpoint | embeddings, images, audio, files, moderation, model lists, assistants, vector stores, legacy completions |
| Provider-side state the code actually uses | `previous_response_id`, `conversation`, `store=True` read back, `retrieve`, background responses, provider file ids |

The runtime re-checks every request. A call the code sometimes makes with `n>1`, logprobs or stored state goes to the
provider on those requests and to Router on the rest.

## Where each lever lives

| Lever | Stored in | Applied by |
|---|---|---|
| Spend tags, cache keys, timeout, tier pin | `router.policy.json` (automatic entries) | compiled to `router_policy.py` / `routerPolicy.ts` beside the runtime |
| `--drop`, `--model-map` | `router.policy.json` (feature entries) | compiled policy; the code is unchanged |
| `--unlock-host`, `--operator-key-only`, `--drop-betas` | the client's wrap call | codemod on one line |
| Cards | `.router-migrate/recommendations.json`, decisions in `decisions.json` | `router-migrate apply . --decisions` |

Every trade-off is also recorded in `.router-migrate/ledger.json` with who took it, why and what it loses.
