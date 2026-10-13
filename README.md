# router-migrate

Move a codebase's production LLM calls onto [Ramp's Router](https://router.com) in one command, without breaking anything.

```bash
router-migrate run .          # scan -> plan -> apply -> verify -> report
export RAMP_ROUTER_API_KEY=... # traffic Router can serve now goes to Router; everything else keeps working as before
```

router-migrate finds every OpenAI / Anthropic / LangChain / Vercel AI SDK / LlamaIndex / LiteLLM call in a Python or TS/JS
repo. It patches only the lines where clients are constructed, and vendors one small, readable runtime file that speaks Router's
API. Existing code keeps calling `chat.completions.create` and reading `.choices[0].message`. The runtime translates those calls
to Router's Responses API, and translates the replies and streams back.

- **Inert until you opt in.** With no `RAMP_ROUTER_API_KEY`, every patched line returns the original client, so tests, mocks and
  cassettes see exactly the same requests. `ROUTER_MIGRATE_DISABLE=1` is the kill switch.
- **Never a hard failure.** Requests Router can't serve exactly go to the original provider unchanged: embeddings, images, audio,
  `n>1`, logprobs, models outside the catalog, `previous_response_id`, and other vendors' base URLs.
- **Every site accounted for.** REPORT.md lists each call site as migrated / kept / flagged / handoff, with the exact reason.
  HANDOFF.md gives a coding agent the judgment calls with "done when" criteria.
- **Verified.** `verify` runs your tests before and after. It then runs them with Router on against **router-sim**, a local
  Router stand-in that enforces Router's measured behavior. It also replays every migrated site through the vendored runtime,
  and optionally against the live API with a dollar cap.

## Install

Requires Python 3.9+ and Node 18+ (Node is only needed for TS/JS repos).

```bash
git clone <this repo> router-migrate && cd router-migrate
make setup                                # .venv (libcst, httpx, SDKs for verify) + node_modules (ts-morph), all local
export PATH="$PWD/bin:$PATH"              # bin/router-migrate
router-migrate --help
```

## Quickstart

```bash
cd your-repo
router-migrate scan .                     # what's there
router-migrate plan . --dry-run           # the exact diff, nothing written
router-migrate apply .                    # patch + vendor the runtime; writes .router-migrate/{REPORT,HANDOFF}.md
router-migrate verify . --test-cmd "pytest -q"     # tests before/after + router-sim checks
RAMP_ROUTER_API_KEY=... router-migrate verify . --live --budget 0.10   # optional: real Router, capped spend
router-migrate report .
```

Typical diff (openai/swarm):

```diff
+try:  # router-migrate: route LLM traffic through Ramp's Router when RAMP_ROUTER_API_KEY is set
+    from .router_runtime import wrap_openai as _router_wrap_openai
+except ImportError:  # router-migrate: module executed as a script
+    from router_runtime import wrap_openai as _router_wrap_openai
 ...
-            client = OpenAI()
+            client = _router_wrap_openai(OpenAI())
```

TS/JS:

```diff
-const client = new OpenAI({ apiKey });
+const client = new OpenAI({ apiKey, ...routerOptions() });
```

## How it works

```
your code ── client.chat.completions.create(...) ──► OpenAI SDK ──► router transport (vendored)
                                                                    ├─ servable? ─► Router POST /v1/responses ─► translated back to a ChatCompletion / chunk stream
                                                                    └─ otherwise ─► original provider, request untouched
```

| Surface | What happens |
|---|---|
| OpenAI `chat.completions` (sync, async, stream, tools, `parse`, legacy `functions`) | translated to `/v1/responses` and back, including SSE |
| OpenAI `responses` | sent to Router as-is, minus `store` (Router rejects `store:true`) |
| Anthropic `messages` (create, stream, `count_tokens`) | sent to Router `/v1/messages`; model id mapped |
| LangChain `ChatOpenAI` / `ChatAnthropic`, LlamaIndex, LiteLLM | client injection (`http_client`), base-URL swap, or install hook |
| Vercel AI SDK `createOpenAI` / `createAnthropic` / default `openai` provider | `fetch: routerFetch()` |
| embeddings, images, audio, files, assistants, vector stores, legacy completions | **kept** on the provider (Router has no such endpoint; measured 404) |
| Azure, Bedrock, Vertex, Groq/OpenRouter/DeepSeek/Ollama base URLs | **kept** |
| google-genai / Mistral / Cohere calls whose model Router serves; raw HTTP to api.openai.com | **handoff** to a coding agent |
| Anthropic beta, `previous_response_id`, models outside the catalog | **flagged**, and the runtime keeps them on the provider |

### Rules the runtime applies (each one measured against the live API)

| Rule | Why |
|---|---|
| `max_tokens` -> `max_output_tokens` (min 16) | Responses rejects `max_tokens` |
| `stop` stripped for OpenAI models and emulated client-side (works across stream chunks) | Router returns 501 for `stop` on OpenAI models |
| `temperature` / `top_p` dropped where the catalog says unsupported | 400 otherwise |
| tools sent as flat Responses tools with explicit `strict: false` | Responses defaults to strict schemas |
| `store` removed; `previous_response_id` stays on the provider | Router keeps no server state |
| `prompt_cache_key` added for Claude | Claude via Responses doesn't cache without it |
| `service_tier: "default"` on streaming calls | flex is on by default and adds latency |
| dated model ids mapped only where Router itself reports the equivalence | dated ids 404; undated aliases can be newer snapshots |
| `metadata.feature = "<file>:<function>"` | per-feature cost attribution with no call-site edits |

## Limits

- Python and TS/JS only. Python clients built through deep factories, and TS clients passed in from outside the repo, show up as
  **handoff** rather than being guessed.
- Models outside the Router catalog stay on their provider. That is deliberate: substituting a different model is a product decision
  (`ROUTER_MIGRATE_MODEL_MAP` opts in per model).
- When Router is active, the patched client uses the runtime's own HTTP transport, so `HTTP(S)_PROXY` environment settings and a
  custom `http_client` passed to that client are not applied to its requests. Environments that require an egress proxy should
  keep `ROUTER_MIGRATE_DISABLE=1` until the proxy is configured on the runtime.
- Some Router features are only reachable from code already on the Responses API (built-in tools, reasoning items, `models`
  fallback lists). Code that is moved by translation gets plain generation.

See [ARCHITECTURE.md](ARCHITECTURE.md), [DECISIONS.md](DECISIONS.md), [AGENTS.md](AGENTS.md) and [eval/SCOREBOARD.md](eval/SCOREBOARD.md).

## Development

```bash
make test        # runtime vs router-sim through real SDKs, golden codemod fixtures, planner, parsers
make compat      # runtime smoke under every eval env (openai 2.x/httpx and 3.x/httpx2, anthropic)
make eval        # the 15-repo eval -> eval/SCOREBOARD.md, scoreboard.json, scoreboard.html
make sim         # router-sim on :8787
```
