# router-migrate: system design, from first principles

This explains what happens from the moment you type `router-migrate up` to the last byte that reaches Router: what each
piece does, how it works, and why it was built that way instead of the obvious alternative.

Read it in three passes, each one deeper:

1. **[The one-minute version](#1-the-one-minute-version)**: the whole system in one picture.
2. **[One real call, traced end to end](#4-one-real-call-traced-end-to-end)**: a two-file repo run through every stage,
   with the actual files and request bodies the tool produced.
3. **[Each stage in depth](#5-each-stage-in-depth)**: how every stage works, the decision behind it, and its limits.

Every claim points at a file in this repo. Decision history with full rationale is in [DECISIONS.md](DECISIONS.md)
(D1 to D12), and the v2 design spec is [SPEC_V2.md](SPEC_V2.md).

---

## 1. The one-minute version

A company's code calls OpenAI and Anthropic in production. Ramp's **Router** is a gateway that can serve those calls
behind one key and one bill, and adds levers a single provider can't: a cheaper Flex tier, fallbacks across models and
vendors, and per-feature spend tags. router-migrate moves the calls Router can serve **exactly**, leaves everything else
untouched, proves nothing broke, and then offers opt-in improvements.

```
                         YOUR REPO
   ┌──────────────────────────────────────────────────────┐
   │  app code ──► OpenAI / Anthropic SDK client          │      call sites: never edited
   │                       │                              │
   │                       ▼                              │
   │              ┌──────────────────┐                    │      one line per client:
   │              │ vendored runtime │◄── router_policy   │      client = _router_wrap_openai(OpenAI())
   │              └──────────────────┘    (v2, optional)  │
   └──────────────────────┼───────────────────────────────┘
          per request:    │
          ┌───────────────┴────────────────┐
          ▼                                ▼
   Router can serve it EXACTLY       anything else, or no key set,
   (translated if needed)            or Router refuses before generating
          │                                │
          ▼                                ▼
     api.router.com                 api.openai.com / api.anthropic.com
                                    (original bytes, unchanged)
```

The tool around that runtime is a pipeline of deterministic steps, each writing a file you can read:

```
router-migrate up <repo>
  scan ──► plan ──► apply ──► verify ──► recommend ──► report ──► dashboard (or --agent JSON)
                                                                       │
                                     decide (UI, CLI or agent) ──► decisions.json
                                                                       │
                                apply --decisions ──► router.policy.json ──► compiled into the runtime
```

Four sentences carry the whole design:

1. **Translate at the HTTP boundary, not in the code.** A small runtime sits under the SDK client and converts requests
   and replies there, so call sites, response handling, tests and mocks never change.
2. **Decide per request, and fail toward the old behavior.** Each outgoing request goes to Router only if Router can serve
   it exactly. Anything else, and any error inside the runtime, sends the original request to the provider unchanged.
3. **Off until you turn it on.** Without `RAMP_ROUTER_API_KEY` the wrapped client is the same object, untouched. Rollout
   is setting an env var; rollback is unsetting it.
4. **Nothing changes without a recorded human decision.** v2 improvements are cards. Only an approval in `decisions.json`
   changes anything, and model swaps are never applied, only recommended.

---

## 2. The problem, from first principles

### 2.1 What "good" means

A migration tool is judged on two things that pull against each other:

1. **How much traffic moves** to Router.
2. **Whether anything breaks.**

Moving everything is easy and breaks things. Moving nothing breaks nothing. So for every single LLM call in the code, the
tool must answer one question: *can Router serve this exactly?* Then it moves only those calls and leaves the rest working
as before.

### 2.2 Facts about Router that shape everything

These were measured live against the real API (the read-only `~/code/router-live-check` project), not taken from docs.
Each fact forces a design consequence.

| Measured fact | What it forces |
|---|---|
| Router serves only `POST /v1/responses`, `POST /v1/messages` (plus `count_tokens`) and `GET /v1/models`. There is no Chat Completions, embeddings, files, images, audio or batches. | Most OpenAI code uses Chat Completions, so it must be **translated**, not just re-pointed. Embeddings and the rest must **stay on the provider**. |
| Router keeps no server-side state: `store: true` is a 400, and `previous_response_id` is a 400 on OpenAI models and silently forgotten on others. | Multi-turn Responses code needs its history **replayed inline** by the client, or must stay on the provider. |
| Router is strict. Dated model ids 404; `seed`, `n` and `logprobs` 400; `stop` is 501 on OpenAI models; `max_tokens` must be `max_output_tokens` (minimum 16); `temperature` is a 400 on some models. | Every request needs **its own check**. Anything Router would reject or serve differently must go to the provider unchanged. |
| Flex (half price, much slower) is on by default for the account. | A migrated call could silently get slower. v1 sends `service_tier: "default"` so nothing changes until someone opts in. |
| Claude through Responses caches prompts only when `prompt_cache_key` is set (10,362 cached tokens with it, 0 without). | The runtime adds a cache key for Claude, or migrated Claude traffic would cost more than before. |
| Cloudflare in front of Router blocks Python's default `urllib` User-Agent. | The runtime forwards the SDK's own User-Agent. |

The full list, with each fact's runtime rule and router-sim rule, is D4 in [DECISIONS.md](DECISIONS.md).

### 2.3 The core bet: translate at the HTTP boundary (D1)

The obvious design is a codemod that rewrites every `client.chat.completions.create(...)` into
`client.responses.create(...)`. It breaks things, because the call is the easy part. Every line that *reads* the result
expects the old shape: `.choices[0].message`, stream loops, tool-call accumulation, `finish_reason`, usage fields. Those
readers are spread across files, wrappers and framework internals (LangChain, LlamaIndex and LiteLLM build their own
requests). Test suites assert exact call arguments: shell_gpt alone has 19 such assertions.

So router-migrate leaves every call site alone. It slides a runtime under the SDK client, at the point where the SDK is
about to send bytes over HTTP. There it sees the finished request, decides whether Router can serve it, translates Chat
Completions to Responses and the reply back (including streaming), and hands the SDK exactly the shape it expected.

| Option | Why not |
|---|---|
| Rewrite call sites to the Responses API | Breaks every response reader, wrapper and exact-argument test. The docs' own migration prompt, run by Claude on the 15 dev repos, broke tests or typecheck in 8 of them, with a median diff of 565 lines. |
| Point `base_url` at Router | Router has no Chat Completions endpoint, so most calls would 404. Embeddings would break. |
| A proxy server the customer deploys | New infrastructure to run and secure, and it still needs the same translation. |
| **Runtime under the SDK client (chosen)** | The translation is written and tested once, against the real SDK parsers. Code edits shrink to one line per client construction: median **11 lines** on the dev repos. |

The cost: one readable vendored file per language goes into the customer's repo (`router_runtime.py`, 1,289 lines;
`routerRuntime.ts`, 1,151 lines), with a kill switch (`ROUTER_MIGRATE_DISABLE=1`).

---

## 3. Vocabulary

| Term | Meaning |
|---|---|
| **site** | One place in the code that matters: a client construction (`OpenAI()`) or a call (`client.chat.completions.create(...)`). Its id is `lang:file:line:col:kind`. |
| **surface** | Which API a call uses: `chat.completions`, `responses`, `messages`, `embeddings`, ... It decides whether Router can serve the call at all. |
| **outcome** | What the plan decides for a site: **migrated**, **kept**, **flagged** or **handoff** (see [5.2](#52-plan-decide-each-sites-fate)). |
| **catalog** | Router's model list (`GET /v1/models`), 98 models, bundled in `router_migrate/data/catalog.json` and refreshed with `--live`. |
| **runtime** | The vendored file that does per-request routing in production. |
| **feature id** | `<file>:<function>` of the code that made a request, for example `app/workers/digest.py:nightly_digest`. The runtime reads it from the call stack and sends it as `metadata.feature`. v2 policy is keyed by it. |
| **card** | One v2 recommendation: a rule, the sites it covers, what changes, what you lose, evidence, risk and an etag. |
| **lever** | The kind of improvement a card proposes: an unlock, a same-model lever (spend tags, caching, Flex, fallbacks) or a model change. |
| **evidence level** | L0 catalog or measured fact, L1 static code signal, L2 offline replay with a judge, L3 Router shadow, L4 observed in production. The MVP builds L0 and L1. |
| **etag** | A hash of a site's surrounding code and its model's catalog row. If either changes after you approve a card, the approval goes stale. |
| **policy** | `router.policy.json`: the approved same-model levers, keyed by feature id. It is compiled into code next to the runtime. |
| **unlock** | An approved change that moves traffic the lossless pass had to leave behind, at a cost you accept. |

---

## 4. One real call, traced end to end

A tiny repo with two files. Every block below is the tool's real output from this run, trimmed only where marked.

```python
# app/workers/digest.py
from openai import OpenAI

SYSTEM = """You write a short daily digest of support tickets for the on-call engineer."""
client = OpenAI()


def nightly_digest(tickets):
    resp = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(tickets)}],
        max_tokens=400,
    )
    return resp.choices[0].message.content
```

```python
# app/embed.py
from openai import OpenAI

client = OpenAI()


def embed(text):
    return client.embeddings.create(model="text-embedding-3-small", input=text).data[0].embedding
```

### Step 1: scan finds four sites

```
$ router-migrate up ./acme --agent
```

The scanner parses both files and records every client construction and every call, with what it can know statically.
From `.router-migrate/scan.json` (trimmed):

```json
{"id": "py:app/workers/digest.py:4:9:client", "kind": "client", "sdk": "openai", "snippet": "client = OpenAI()", "base_url": null}
{"id": "py:app/workers/digest.py:8:11:call", "kind": "call", "surface": "chat.completions", "receiver": "client",
 "model": {"value": "gpt-5.4-mini", "static": true}, "function": "nightly_digest", "stream": false,
 "has_prev_response": false, "has_beta": false, "static_kwargs": {"max_tokens": 400}, "prod": true}
{"id": "py:app/embed.py:3:9:client", "kind": "client", "sdk": "openai", "snippet": "client = OpenAI()"}
{"id": "py:app/embed.py:7:11:call", "kind": "call", "surface": "embeddings", "model": {"value": "text-embedding-3-small", "static": true}}
```

### Step 2: plan decides each call's fate, with a reason

From `.router-migrate/plan.json`:

| site | outcome | reason |
|---|---|---|
| `app/workers/digest.py:8` | **migrated** | `client patched; model 'gpt-5.4-mini' -> 'gpt-5.4-mini' (exact catalog id)` |
| `app/embed.py:7` | **kept** | `Router has no /v1/embeddings (measured 404)` |

The plan also lists which **client constructions** to patch. Both clients are patched, including the one that only does
embeddings: wrapping is safe because the runtime decides per request, and embeddings requests pass through to OpenAI
byte-for-byte.

### Step 3: apply edits one line per client and vendors the runtime

`.router-migrate/apply.diff` (one file shown; the other gets the same change):

```diff
 from openai import OpenAI
+try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
+    from ..router_runtime import wrap_openai as _router_wrap_openai
+except ImportError:  # router-migrate: module executed as a script
+    try:
+        from router_runtime import wrap_openai as _router_wrap_openai
+    except ImportError:  # router-migrate: runtime not importable from here; this module keeps its original behavior
+        _router_wrap_openai = lambda c, *a, **k: c  # noqa: E731

 SYSTEM = """You write a short daily digest of support tickets for the on-call engineer."""
-client = OpenAI()
+client = _router_wrap_openai(OpenAI())
```

`app/router_runtime.py` is copied in once per top-level package. The import has three tries ending in a no-op, so the
edit can never make the module fail to import (D6). The call site and `resp.choices[0].message.content` are untouched.

### Step 4: verify (skipped in this trace)

On a real repo, `up` runs the repo's tests before and after the edit with Router off, then again with Router on, pointed
at a local Router simulator. [5.5](#55-verify-prove-nothing-broke) explains how.

### Step 5: recommend computes signals, then cards

For each migrated call, recommend works out three signals. From `.router-migrate/recommendations.json`:

```json
"py:app/workers/digest.py:8:11:call": {
  "feature": "app/workers/digest.py:nightly_digest",
  "latency_class": "background",
  "model_owner": "app_literal",
  "resolved_model": "gpt-5.4-mini"
}
```

- `latency_class` is **background** because the path contains `workers/` and the function name contains `nightly`.
- `model_owner` is **app_literal** because the model is a string literal in the app's own code, so the app owns the
  choice and model cards are allowed.
- `resolved_model` is the exact catalog id.

Then the rules run. This repo got four cards and one "considered, not recommended":

| card | lever | risk | what it would change |
|---|---|---|---|
| Tag Router spend with `product=acme` | `attr.metadata` | 0 | policy `defaults.metadata` |
| Run `nightly_digest` on Flex (−50% per call, background job) | `tier.flex_background` | 1 | policy `allow_flex_tier: true` for that feature |
| Fail over `nightly_digest` from gpt-5.4-mini to gpt-4.1, then claude-haiku-4-5 | `fallback.candidates` | 2 | policy `fallbacks` for that feature |
| Try gpt-6-luna for `nightly_digest` (newer, −89% list price) | `model.newer_cheaper_same_family` | 2 | **nothing: recommendation only** |
| *considered:* `cache.key` | — | — | not recommended: "no static system prompt >= ~1,024 tokens found (longest ~20 tokens)" |

Each card says what you lose. The Flex card's first loss line reads: "latency: Flex requests can take much longer (61 s
measured on gpt-5-nano)". Its evidence is L0 ("gpt-5.4-mini has a measured flex tier") plus L1 ("background markers in
app/workers/digest.py:nightly_digest").

The Flex and fallback cards also warn about each other. Router ignores Flex when a request carries a `models` fallback
list (measured in M0), so approving both means the fallbacks win and Flex has no effect. This trace caught exactly that
case; [5.9](#59-apply-decisions-the-compiled-policy) has the details.

`up --agent` prints the four undecided cards as JSON, each with the exact `decide` command, and **exits 2** because
cards are undecided. Without `--agent`, `up` opens the dashboard instead.

### Step 6: decide

```
$ router-migrate decide rec_attr_metadata_12856f15 approve
$ router-migrate decide rec_tier_flex_background_85910cd9 approve
$ router-migrate decide rec_fallback_candidates_5381ab98 approve
$ router-migrate decide rec_model_newer_cheaper_same_family_6d273d8e dismiss --reason "quality unproven"
```

Each decision is stored in `.router-migrate/decisions.json` with the card's etag at the time:

```json
"rec_tier_flex_background_85910cd9": {"action": "approve", "etag": "cf0f51d01e64a060", "by": "cli", "at": "2026-10-13T16:38:21"}
```

The dashboard's buttons and an agent's commands write exactly the same entries.

### Step 7: apply --decisions writes the policy and compiles it

```
$ router-migrate apply ./acme --decisions
apply --decisions: 3 applied, 0 skipped; policy bae37bb3
  applied rec_attr_metadata_12856f15 (attr.metadata)
  applied rec_tier_flex_background_85910cd9 (tier.flex_background)
  applied rec_fallback_candidates_5381ab98 (fallback.candidates)
  warning app/workers/digest.py:nightly_digest: allow_flex_tier has no effect because fallbacks are also set (Router ignores Flex with a models list)
```

`router.policy.json` at the repo root, which is meant to be committed and reviewed:

```json
{
 "defaults": {"metadata": {"product": "acme"}},
 "features": {
  "app/workers/digest.py:nightly_digest": {"allow_flex_tier": true, "fallbacks": ["gpt-4.1", "claude-haiku-4-5"]}
 },
 "version": 1
}
```

It is compiled into `app/router_policy.py`, next to the runtime, which imports it like any module:

```python
POLICY = json.loads('{"defaults":{"metadata":{"product":"acme"}},"features":{...},"version":1}')
POLICY_HASH = 'bae37bb3ac475d726a36948ad007d452fd01ecc50bc04993185d4c8e94f1bc30'
```

`router-migrate status` confirms the compiled copy matches the JSON and shows the effective settings per feature:

```
policy: router.policy.json bae37bb3
  compiled app/router_policy.py                               current
  app/embed.py:embed                                           {"metadata": {"product": "acme"}}
  app/workers/digest.py:nightly_digest                         {"allow_flex_tier": true, "fallbacks": [...], "metadata": {"product": "acme"}}
cards: 4 total, 0 undecided, 3 approved
```

The dismissed model card changed nothing. Model cards never do.

### Step 8: the request in production

`nightly_digest(...)` runs with `RAMP_ROUTER_API_KEY` set. Here the base URL pointed at router-sim, the local Router
stand-in. The app code is unchanged, and the SDK builds the same request it always did:

```
POST https://api.openai.com/v1/chat/completions
{"model": "gpt-5.4-mini", "max_tokens": 400,
 "messages": [{"role": "system", "content": "You write a short daily digest..."},
              {"role": "user", "content": "login page 500s for EU users\nbilling export timed out"}]}
```

The runtime intercepts it under the SDK, decides Router can serve it exactly, translates it and applies the policy. The
body that reached Router:

```
POST https://api.router.com/v1/responses
{
 "input": [{"role": "system", "content": "You write a short daily digest..."},
           {"role": "user", "content": "login page 500s for EU users\nbilling export timed out"}],
 "max_output_tokens": 400,                                   <- max_tokens renamed (Responses API)
 "metadata": {"feature": "app/workers/digest.py:nightly_digest",   <- from the call stack (v1)
              "product": "acme"},                                  <- policy defaults (v2)
 "models": ["gpt-5.4-mini", "gpt-4.1", "claude-haiku-4-5"],  <- policy fallbacks (v2)
 "service_tier": "default"                                   <- v1 default; Flex dropped because of the models list
}
```

Router's reply comes back in Responses format. The runtime converts it to the Chat Completions shape, so
`resp.choices[0].message.content` works unchanged and the function returns a plain string, as before.

Three variations on the same call:

| Condition | What happens |
|---|---|
| `RAMP_ROUTER_API_KEY` unset | `_router_wrap_openai(OpenAI())` returns the client untouched. Every request goes to OpenAI exactly as before. |
| `embed("...")` with the key set | The runtime sees `/embeddings`, which Router can't serve, and passes the request to OpenAI unchanged. |
| Router answers 404 `model_not_found` | The runtime resends the **original** request to OpenAI, once, and remembers that model as denied for this process. |

---

## 5. Each stage in depth

The CLI entry point is `router_migrate/cli.py`. `up` lives in `router_migrate/v2cli.py`: `_up_steps` runs the v1 stages
from `cli.py` (`do_scan`, `do_plan`, `do_apply`, `do_verify`), then `improve.recommend` and `do_report`. Each stage
appends a line to `.router-migrate/events.jsonl`, which the dashboard shows as pipeline progress.

**Design choice: every stage reads and writes plain files** under `<repo>/.router-migrate/`. There is no database or
service. Every intermediate result can be inspected, diffed, committed and re-run. The dashboard, the CLI and a coding
agent are three readers and writers of the same files, so they cannot disagree.

### 5.1 Scan: find every LLM call

**Code:** `router_migrate/scan_py.py` (Python, using libcst) and `router_migrate/ts/tool.mjs` (TS/JS, using the
TypeScript compiler API). **Output:** `scan.json`.

It parses every source file into a syntax tree and looks for:
- **client constructions**: `OpenAI()`, `AsyncAnthropic()`, `ChatOpenAI(...)`, `createOpenAI(...)`, LlamaIndex and
  LiteLLM setup, recording the base URL and API-key arguments;
- **calls** on those clients: `chat.completions.create`, `responses.create`, `messages.stream`, `embeddings.create`, and
  so on, including aliases (`chat = client.chat`) and module-level `openai.*`;
- **raw HTTP** to `api.openai.com` and `api.anthropic.com`, and **framework calls** (Vercel AI SDK `generateText` /
  `streamText`);
- per-site facts: surface, streaming, model (a literal, or "chosen at runtime"), stateful features (`previous_response_id`,
  `store`, conversations, provider files), beta headers, and whether the file is production code.

**Why real parsers, not regex:** a parser knows that `client.chat.completions.create(` inside a string or comment is not
a call, knows which variable holds which client, and gives exact line and column spans so the codemod edits safely.
libcst preserves formatting exactly, so the diff shows only the intended change. The TypeScript compiler API gives the
compiler's own view of the code.

**Limit:** the parser is exact, but the matchers on top of it only recognize patterns someone wrote a rule for. In-house
wrappers, generic `litellm.completion` paths, HTTP clients pointed at a configured base URL, and dynamic dispatch are
invisible. On 30 held-out repos the scanner found 71% of the Router-relevant sites. This is the biggest open weakness.

### 5.2 Plan: decide each site's fate

**Code:** `router_migrate/plan.py` (`build`, `_classify`, `_post_checks`), with model data from
`router_migrate/catalog.py` and `router_migrate/data/`. **Output:** `plan.json`.

The plan is made of plain rules, with no LLM. The same code and the same catalog give the same plan every time. Each site
gets one outcome and a written reason:

| Outcome | Meaning | Example reason |
|---|---|---|
| **migrated** | Its traffic goes to Router once the key is set | `client patched; model 'gpt-4o-mini' -> 'gpt-4o-mini' (exact catalog id)` |
| **kept** | Stays on its provider by design | `Router has no /v1/embeddings (measured 404)`; another vendor's endpoint; an Azure deployment |
| **flagged** | Router might serve it, but not automatically | an unmapped model; an Anthropic beta header; per-tenant API keys |
| **handoff** | Needs a code change a coding agent should make, from a written spec | another vendor's SDK on a model Router serves; raw HTTP; another gateway |

The checks, in order:
1. **Surface.** Does Router have an endpoint for it? Generation yes; embeddings, files and images no.
2. **Target.** Does the client point at OpenAI or Anthropic, or at another vendor, Azure, a local server, or a config value?
3. **Model.** Is it in the catalog as an exact id, or as a **measured** dated equivalent (22 entries in
   `data/dated_equivalents.json`, each observed live)? If the model is chosen at runtime, at least one of the repo's model
   names must be in the catalog. Nothing is guessed: `gpt-4-turbo-preview` is a different model from `gpt-4-turbo`, so it
   stays (D3).
4. **Post-checks.** Per-tenant keys are flagged, because moving each user's key onto one Router key is a billing decision,
   not a codemod. Clients with stateful Responses chains get `responses=False`. Beta headers and provider-side files are
   handled here too.
5. **Production only.** Tests, examples and evals are reported but not edited (D5).

**Why deterministic:** it can be reviewed, tested with golden fixtures, explained per site, and run for free. Judgment
calls become `handoff` with an exact spec instead of a guess.

### 5.3 Apply: one line per client

**Code:** `router_migrate/apply_py.py` (a libcst transformer) and `ts/tool.mjs` (minimal span edits). **Output:** code
edits, `apply.diff`, `apply.json`.

- Each chosen constructor is wrapped: `_router_wrap_openai(OpenAI(...))`, `_router_wrap_anthropic(...)` and
  `_router_wrap_langchain(ChatOpenAI(...))`. In TS, `...routerOptions()` is spread into the constructor options, which
  supplies the SDK's `fetch`. LiteLLM and module-level `openai.*` get an install call after the imports.
- The runtime is copied once per top-level package, behind an import that cannot fail (shown in [step 3](#step-3-apply-edits-one-line-per-client-and-vendors-the-runtime)).
- Every inserted line carries a `router-migrate` comment, so it can be found with grep and reverted.
- **Idempotent:** a rerun recognizes earlier wraps and patches only new ones.

**Why wrap the constructor and not the call:** one patch covers every call made through that client, including calls in
files the scanner never saw. On the 15 dev repos the median diff was 11 lines.

### 5.4 The runtime: what happens to each request in production

**Code:** `router_migrate/runtime/router_runtime.py`, mirrored in `routerRuntime.ts`. Key functions:
`plan_request` / `_plan_request` (route or not), `chat_to_responses` and `responses_to_chat` (translation),
`_ChunkWriter` (streaming), `_fallback_ok` (when to retry on the provider), `_apply_policy` (v2), `_install` (wrap in
place).

This is the part that ships to production. For every request the SDK sends:

```
SDK builds request ──► plan_request()
   │
   ├─ key not set, or ROUTER_MIGRATE_DISABLE=1 ........................ provider, unchanged (D2)
   ├─ host is not api.openai.com / api.anthropic.com .................. provider
   │    (an approved v2 unlock adds one vendor host to this check)
   ├─ model does not resolve to a catalog id .......................... provider
   ├─ anything Router would refuse or serve differently ............... provider
   │    (embeddings, images, audio, files, n>1, logprobs, seed, presence_penalty,
   │     max_tokens<16, a provider-issued previous_response_id, beta headers)
   │
   ├─ POST /chat/completions ─► chat_to_responses() ─► Router /v1/responses
   │                            ◄── responses_to_chat() / _ChunkWriter (SSE stream) ◄──
   ├─ POST /responses ─► drop `store`; replay Router-started chains inline ─► Router /v1/responses
   ├─ GET  /responses/{Router id} ─► answered from memory (last 256 chains)
   └─ POST /v1/messages ─► model id mapped ─► Router /v1/messages
                           (with an anthropic-beta header: only if every flag was approved to drop)

Router refuses BEFORE generating (400 unknown parameter, 403, 404 model, 501)
   ─► resend the ORIGINAL request to the provider, once
Any unexpected exception inside plan_request ─► provider gets the request exactly as before
```

**Translation details forced by measurements (D4):**
- `max_tokens` becomes `max_output_tokens`.
- `stop` is emulated client-side for OpenAI models (Router returns 501) and passed through for others.
- `temperature` is dropped where the model rejects it.
- Function tools get an explicit `strict: false`, because Responses defaults to strict and Chat Completions does not.
- Claude requests get `prompt_cache_key` (without it, Claude via Responses doesn't cache).
- `service_tier: "default"` is sent unless the caller chose a tier.
- Claude gets reasoning effort only when the output budget is at least 1,100 tokens.
- `metadata.feature = "<file>:<function>"` is read from the call stack, so Router can attribute cost per feature with no
  call-site edits.
- The SDK's User-Agent is forwarded.

**Three safety properties:**
1. **Inert by default.** With no key, nothing changes at all.
2. **Per request, not per client.** One client can send some requests to Router and others to the provider. Partial
   support never becomes a hard failure.
3. **Fail toward the old behavior.** Every uncertain path (unknown model, unexpected error, refusal before generating)
   ends at the provider with the original request.

**Wrap in place (D10).** `_install` puts the Router transport *under* the client's existing httpx transport and proxy
mounts. The client object, its headers, hooks, proxies and connection pool stay the same, for provider traffic too.

**When the key takes effect.** `wrap_openai` checks for the key when the client is **constructed**. With no key at that
moment, it returns the client untouched. So rollout means setting the env var and restarting the process; setting it in
a running process does not reroute clients that already exist. The kill switch `ROUTER_MIGRATE_DISABLE=1` is read per
request.

**Why not retry after Router has started generating?** A request that fails mid-generation may already have done work or
streamed tokens. Retrying elsewhere could duplicate output or side effects, so only refusals *before* generation trigger
the provider fallback.

### 5.5 Verify: prove nothing broke

**Code:** `router_migrate/verify.py` and `router_migrate/sim/server.py` (router-sim). **Output:** `verify.json`.

1. **Router off, before and after.** It runs the repo's own tests (or typecheck) twice: once with the router-migrate
   changes temporarily removed (`git stash` of exactly the changed and vendored files) and once with them in place. The
   results must match.
2. **Router on, against router-sim.** It runs the tests again with the key set and the base URL pointed at router-sim, a
   local stand-in that enforces every measured Router rule. Each rejection is labelled `[measured]` or `[upstream]` (D7).
   Translation bugs fail loudly here.
3. **Replay.** It rebuilds each migrated site's request from its literal arguments and sends it through the vendored
   runtime to router-sim, and optionally to live Router under a dollar cap (`--live --max-usd`). It counts accepted unique
   request shapes.

**Limit:** most test suites mock the LLM, so they prove the code paths and request shapes, not real model behavior. Live
replay is the real-world check.

### 5.6 Report and handoff

`REPORT.md` and `report.json` list every site, its outcome, its reason, and the coverage numbers. `HANDOFF.md` and
`handoff.json` give exact specs for the sites a coding agent should change. `AGENTS.md` and
`.claude/skills/router-migrate/SKILL.md` describe the loop: do the task, rerun verify until green. This is the only place
an LLM touches customer code, and it works from a written spec, gated by the same verify step.

### 5.7 Recommend (v2): turn migrated traffic into decisions

v1 leaves the bill where it was: same models, list price, Flex deliberately off. v2's Improve phase has two jobs: move
more traffic (**unlocks**) and make moved traffic cheaper and more reliable (**levers**). Every change is opt-in.

**Code:** `router_migrate/recommend.py` (`build`, signal functions, rule blocks), called by `improve.recommend`.
**Output:** `recommendations.json` and `RECOMMENDATIONS.md` (rendered from the JSON, one way).

**Signals, per migrated site:**

| Signal | How it is computed | Why it matters |
|---|---|---|
| `latency_class` | **background** if the path or function name matches worker, job, task, cron, batch, queue, nightly, ...; **interactive** if streaming or a web route in an app; otherwise interactive (apps) or user-waiting (CLIs and libraries). Only clear markers count as background. | Flex can take a minute, so it is offered only for background work. |
| `model_owner` | **app_literal** for a string literal or a module constant; **operator_config** for env or settings; **end_user** for request, CLI-argument or user fields; **library_param** for a parameter in a library. | Model cards appear only where the app or its operator owns the model. In a library or CLI, the model is someone else's choice. |
| `resolved_model` | The literal, or the config default the scanner can follow, resolved to a catalog id. | Every model-dependent rule needs a known model. |

Repo kind (`repo_kind`) comes from dependencies: a web framework makes it an **app**, a console entry point a **cli**,
and anything else a **library**.

**Rules, in three groups:**

| Group | Rule | Fires when | Applied through |
|---|---|---|---|
| Unlock | `unlock.vendor_endpoint` | A kept site on another vendor's OpenAI-compatible endpoint uses a model id Router serves exactly | code: `unlock_hosts=[...]` on the existing wrap |
| Unlock | `unlock.stated_loss` | A flagged site's only obstacle is an Anthropic beta flag that can be dropped, on a client v1 already patched | code: `drop_betas=[...]` on the existing wrap |
| Same model | `attr.metadata` | Any Router-served feature | policy defaults |
| Same model | `cache.key` | An OpenAI-model feature with a static system prompt of at least ~1,024 tokens | policy |
| Same model | `tier.flex_background` | Background latency class and a model with a measured Flex tier | policy |
| Same model | `fallback.candidates` | A known model, plus up to two candidates that match its capabilities within 0.5 to 2 times its list price | policy |
| Model | `model.deprecated`, `model.newer_cheaper_same_family`, `model.right_size` | app_literal or operator_config owners only | **never applied** |

Every rule that doesn't fire for a scope is listed under **"Considered, not recommended"**, with the reason. Every card
shows its rule and inputs, what you lose, its evidence level, risk (0 to 3), and impact as a percentage per call from
catalog list prices. No dollar figure is shown, because traffic volume is unknown at scan time.

**M0 decided which rules exist** (D11, `research/m0_measurements.md`, measured live for under $0.001):
- Flex costs exactly **50%** of default on gpt-5.4-mini (n=5 per arm). On gpt-5-nano one Flex call took **61 s**, so Flex
  is offered only for the background latency class.
- Flex eligibility is per model (gpt-4o-mini returns 400), so the catalog carries a per-model Flex flag
  (`data/flex_models.json`, 17 models).
- Router's `models` fallback list works through raw HTTP, openai-python `extra_body` and openai-node, and advances past a
  403 or an unknown model.
- With a `models` list, `allow_flex_tier` is silently ignored. That is why the Flex and fallback cards warn about each
  other.
- Jev couldn't be measured without changing account settings, so nothing depends on it.

**Design rules behind the cards:**
- **Which model answers stays visible in code.** Model swaps are cards only. Same-model levers go in the policy file.
- **Unlocks are reported beside the lossless number, never added to it** (48 lossless, 49 with unlocks).
- **Restraint.** The dashboard shows the top 10 cards and folds the rest.

### 5.8 Decide: one record, three ways to write it

**Code:** `improve.decide`, `v2cli.cmd_decide`, and the dashboard's `POST /api/decide`. **Output:** `decisions.json`.

- **Dashboard:** Apply, Shadow first, Postpone, or Dismiss with a reason.
- **CLI:** `router-migrate decide <id> approve | dismiss --reason R | postpone --until YYYY-MM-DD | shadow-first`.
- **Agent:** `router-migrate up --agent` prints undecided cards as JSON, each with its exact `decide` command, and exits 2
  while any card is undecided. A card counts as decided once any action is recorded with its current etag.

**Why the etag:** a decision is about specific code and a specific model. If the code around the site or the model's
catalog row changes, the approval no longer refers to what was approved, so `apply --decisions` skips the card as stale
and `up --agent` shows it again.

"Shadow first" only records the decision today. Nothing runs a shadow test behind it yet (see [9](#9-known-gaps-in-priority-order)).

### 5.9 Apply decisions: the compiled policy

**Code:** `improve.apply_decisions`, `router_migrate/policy.py`, and `_apply_policy` in both runtimes. Semantics:
`router_migrate/schemas/CONTRACTS.md` section 1.

- Approved **policy** cards are merged into `router.policy.json` at the repo root, keyed by feature id, glob, or
  `defaults`. The allowed keys are `allow_flex_tier`, `prompt_cache_key`, `metadata`, `fallbacks` and `provider_timeout`.
  **There is no `model` key**, so policy can never change which model answers.
- **Precedence:** exact feature, then the longest matching glob, then defaults, per key. `metadata` is merged across
  layers, and the request's own metadata keys always win. TS features use globs like `**app/api/chat/route.*:POST`, so
  they also match the paths a bundler produces.
- **Compiled, not read from disk.** The JSON is compiled into `router_policy.py` / `routerPolicy.ts` next to the runtime
  and imported like code. Serverless and edge bundles ship imported modules but can silently drop a JSON file read from
  disk. `status` compares the two hashes and fails if the compiled copy is stale or an entry matches no site.
- **At request time**, `_apply_policy` adds the approved keys to the Router-bound body:
  - Flex is applied only if the model has a measured Flex tier and the caller didn't choose a tier.
  - Fallbacks must pass a **capability guard**. Each candidate must be in the catalog, active, on the Responses surface,
    support the request's tools, structured output and input modalities, accept `stop` and `temperature` if the request
    uses them, and differ from the original model. Survivors go out as `models: [original, ...fallbacks]`.
  - With a `models` list, Flex is dropped and v1's `service_tier: "default"` is kept, because Router ignores Flex there.
    The runtime logs that once, `apply --decisions` warns about it, and both cards say so.
  - An invalid policy, or `ROUTER_MIGRATE_POLICY=off`, gives exactly v1 behavior.
  - Policy is not applied on `/v1/messages`, because Router's extra fields are Responses-only. That is logged once.
- Approved **unlock** cards add `unlock_hosts=[...]` or `drop_betas=[...]` to the existing wrap (Python only so far).
- Model cards are skipped with the reason "recommendation only".

### 5.10 The dashboard

**Code:** `router_migrate/dashboard/server.py`, `page.html`, `render.py`, and `improve.bundle` (the data it shows).

- **Server:** Python standard library only (`ThreadingHTTPServer`), bound to **127.0.0.1** on a random port. A random
  session token is required on every request (`?t=` for the page, the `X-RM-Token` header for the API). It checks the
  `Host` and `Origin` headers (which blocks other websites and DNS rebinding), caps request bodies at 64 KiB, and sends a
  strict CSP. There are four endpoints: the page, the bundle, decide, and apply. Clicks call `improve.decide` and
  `improve.apply_decisions`, the same functions the CLI uses.
- **Page:** one self-contained HTML file with five tabs. **Overview** has pipeline progress, the lossless vs with-unlocks
  numbers, tests and spend. **Migration** lists every site with its outcome, reason and diff. **Improve** has the cards
  (top 10, then "Show N more"), the considered list and an Apply bar. **Router setup** is a static checklist of
  account-only settings: spend cap, the Flex default, Jev, Switchyard and shadow models. **Unsupported** lists what can't
  move and why.
- **Look:** dark only, using Router's Ryu "cement" design tokens. Warm greys, one yellow-green accent used only for the
  primary action, Inter for text and IBM Plex Mono for numbers and model ids, borders rather than shadows.
- **REPORT.html** is the same page with the data embedded and the buttons disabled. It works offline and can be attached
  to CI or an email.

**Why a local server instead of a hosted app:** the data is the customer's source code. Keeping it on 127.0.0.1, behind a
per-run token, means nothing leaves the machine.

---

## 6. What happens when things go wrong

| Situation | Behavior |
|---|---|
| `RAMP_ROUTER_API_KEY` not set when the client is built | Client returned untouched; all traffic goes to the provider as before. |
| `ROUTER_MIGRATE_DISABLE=1` | Checked per request: everything goes to the provider. |
| Model not in Router's catalog | That request goes to the provider. |
| Router returns 400 unknown parameter, 403, 404 model, or 501 | The original request is resent to the provider, once. A 404 `model_not_found` also marks that model denied, so later requests for it skip Router for the rest of the process. |
| Router fails after it started generating | The error is surfaced, not retried, to avoid duplicate output. |
| Exception inside the runtime's routing logic | Caught; the request goes to the provider unchanged. |
| The runtime file can't be imported | The no-op stub is used; the module behaves exactly as before the migration. |
| `previous_response_id` from a Router response that was evicted from memory | A clear 400 saying so, rather than sending it to a provider that never saw it. |
| `router.policy.json` invalid, or `ROUTER_MIGRATE_POLICY=off` | Exactly v1 behavior. Logged once with the policy hash, never with prompt content. |
| Code or catalog changed after a card was approved | `apply --decisions` skips it as stale; it must be re-confirmed. |

---

## 7. What is deterministic, and where LLMs appear

| Part | Deterministic? |
|---|---|
| scan, plan, apply, runtime, verify, report, recommend, policy, dashboard | **Yes.** Plain rules and code: the same input gives the same output. |
| Handoff sites (9 in the dev eval) | A coding agent makes the change from a written spec, then verify gates it. |
| Evaluation only: the docs-prompt baseline, blind held-out inventories, adjudication of disagreements | Claude, used to *grade* the tool, never inside it. |

---

## 8. How it was evaluated, and the honest numbers

**Dev set: 15 pinned open-source repos** (9 Python, 6 TS), the repos the tool was built against
([eval/SCOREBOARD.md](eval/SCOREBOARD.md), [eval/SCOREBOARD_V2.md](eval/SCOREBOARD_V2.md)):
- The scanner found 170 production call sites; 107 are on surfaces Router serves.
- **48 moved losslessly** (45% of the Router-eligible sites); 9 are handoffs, 16 flagged and 97 kept.
- With every unlock and same-model card approved: **49**, reported beside the 48, never added to it.
- Tests and typecheck unchanged in **15 of 15** repos, both lossless and with unlocks.
- Every migrated request shape replayed through the runtime was accepted: 24 of 24 unique shapes.
- **Baseline:** the docs' migration prompt, run by Claude on the same repos, left failing tests or new type errors in 8 of
  15. In the 7 it left intact it moved 32 sites, against router-migrate's 48. Its median diff was 565 lines, against 11.
- v2 cards: 30 in total, 20 meeting their evidence bar. Card precision has not been measured.
- Live spend for all of v2: $0.000795.

**Held-out set: 30 repos the tool was never tuned on** ([eval/heldout/AUDIT.md](eval/heldout/AUDIT.md)). Each was
inventoried blind by a separate agent against a rubric, and all 94 disagreements were judged against the code:
- The scanner found **71%** of the Router-relevant sites.
- **53%** of "migrated" claims were right. In 21 cases it migrated a site whose right answer was "keep".
- 27 of the 33 wrong "migrated" claims would never move traffic, because the runtime only reroutes requests addressed to
  OpenAI or Anthropic. 4 would move per-user-key traffic onto the operator's Router key.
- This is the number that predicts performance on unseen code. The held-out run is incomplete: 15 of 29 repos have test
  results.

Counts are **call sites, not traffic volume**. The denominator is the scanner's own count, so sites it missed are not in
it, and true coverage is lower than the reported figure.

---

## 9. Known gaps, in priority order

1. **Discovery and planning on unseen code** (71% discovery recall and 53% migrated precision, held-out). The fix:
   more general matchers (LiteLLM and framework paths, wrappers that forward to SDK clients, base URLs from config) and
   stricter planner rules, tuned on half of a fresh held-out set and scored on the other half.
2. **Evidence beyond L1.** Offline replay with a judge (L2), the shadow-config generator (L3) and observe mode with real
   per-feature cost (L4) are designed in SPEC_V2.md but not built. "Shadow first" records a decision but runs nothing.
3. **Smaller v2 gaps:** no TS codemod for unlocks, no client-level policy targeting, no Azure deployment mapping, and no
   two-way sync between `RECOMMENDATIONS.md` and `decisions.json`.
4. **Router-side unknowns:** there is no usage or shadow-results API, so dollar savings need customer-side counters.

---

## 10. Where things live

**The tool** (`router_migrate/`):

| File | Role |
|---|---|
| `cli.py`, `v2cli.py` | Commands. `up` chains every stage. |
| `scan_py.py`, `ts/tool.mjs` | Scanners (Python and TS). The TS file also holds the TS codemod. |
| `plan.py`, `catalog.py`, `data/` | Outcome rules, the model catalog, measured dated equivalents, Flex models, model release dates. |
| `apply_py.py` | Python codemod. |
| `runtime/router_runtime.py`, `runtime/routerRuntime.ts` | The vendored runtimes: routing, translation, fallback, policy. |
| `verify.py`, `sim/server.py` | Test runs and router-sim. |
| `report.py`, `rules.py` | v1 report and the rule registry. |
| `recommend.py`, `improve.py`, `policy.py` | v2 signals and cards, decisions, policy compile and status. |
| `dashboard/` | Local server, the page, and REPORT.html rendering. |
| `schemas/CONTRACTS.md` | v2 file formats: policy, recommendations, decisions, dashboard bundle. |

**In the customer's repo after a run:**

| Path | What it is |
|---|---|
| `<package>/router_runtime.py` or `routerRuntime.ts` | The vendored runtime. |
| `<package>/router_policy.py` or `routerPolicy.ts` | The compiled policy (only after `apply --decisions`). |
| `router.policy.json` | Approved same-model levers; commit it. |
| `.router-migrate/scan.json`, `plan.json`, `apply.diff`, `apply.json`, `verify.json` | v1 stage outputs. |
| `.router-migrate/REPORT.md`, `report.json`, `HANDOFF.md`, `handoff.json` | v1 report and agent tasks. |
| `.router-migrate/recommendations.json`, `RECOMMENDATIONS.md`, `decisions.json`, `unlocks.json` | v2 cards and decisions. |
| `.router-migrate/REPORT.html`, `events.jsonl` | Frozen dashboard and the stage log. |
