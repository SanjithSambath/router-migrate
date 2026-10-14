# router-migrate

Move a codebase's production LLM traffic onto [Ramp's Router](https://router.com), and keep everything it did before.

router-migrate is a Claude Code skill plus a small CLI. Your coding agent does the migration: it reads the code, wraps
clients, and takes the small trade-offs that let more calls move. The CLI gives it facts it can't guess (does Router serve
this request?), exact edits, and one check at the end it can't influence. A vendored runtime then decides per request:
Router when Router can serve it exactly, the original provider otherwise. Without `RAMP_ROUTER_API_KEY` it does nothing.
Every cost is written down, and every trade-off has an Undo.

> **Status:** working MVP, under active development. Python and TS/JS; OpenAI, Anthropic, LangChain, Vercel AI SDK,
> LlamaIndex and LiteLLM. The evaluation on open-source repos is still being finalized; results will be published here
> once it is.

## Quickstart

### Install

**As a Claude Code plugin** (works in any repo, any model). In Claude Code:

```
/plugin marketplace add <path-to-this-checkout or owner/repo>
/plugin install router-migrate@router-migrate
```

The plugin puts `router-migrate` on the session's PATH; its first run installs its two Python dependencies (libcst, httpx)
into a cached venv, and the TS codemod's node modules when npm is available.

**From a checkout** (CLI on your PATH, skill available as `/router-migrate`):

```bash
git clone <this repo> router-migrate && cd router-migrate
make setup                                                  # local .venv and node_modules (or: pip install -e .)
export PATH="$PWD/bin:$PATH"
ln -s "$PWD/skills/router-migrate" ~/.claude/skills/router-migrate
```

**Requirements:** a git repo to migrate; Python 3.9+; Node 18+ for TS/JS repos.

### Migrate

**In Claude Code**, from the repo you want to migrate: `/router-migrate` (plugin: `/router-migrate:router-migrate`), or
just say "migrate this repo to Router". The session you are in is the agent, on whatever model it runs:

1. `router-migrate start .` records the "before" state, starts the baseline tests in the background, and prints the
   instructions, Router's facts and a map of every LLM call;
2. the session migrates with its own tools plus `check`, `levers`, `wrap`, `adapt`, and writes `MIGRATION.json`;
3. `router-migrate finish .` runs the outside check, builds the improvement cards and prints the dashboard URL.

**Headless** (CI, evals), the same prompt and the same `start`/`finish` code around a `claude -p` session:

```bash
router-migrate run . --agent claude [--model <model>]
```

**Other agents** (Codex and others): follow [AGENTS.md](AGENTS.md).

### Review, apply, ship

Approve, dismiss, postpone or undo on the dashboard and press Apply (or `router-migrate decide` + `router-migrate apply .
--decisions`). Then:

```bash
router-migrate check --final --tests  # the outside check and the test suite, Router on, before you ship
export RAMP_ROUTER_API_KEY=...        # traffic Router can serve goes to Router; everything else is unchanged
```

`ROUTER_MIGRATE_DISABLE=1` turns it all off again without a deploy.

## What you get

![Dashboard](docs/img/dashboard.png)

- **A small diff.** One line per client construction, plus the vendored runtime (`router_runtime.py` / `routerRuntime.ts`)
  and a compiled policy. Call sites are untouched: code keeps calling `chat.completions.create` and reading
  `.choices[0].message`.
- **A dashboard** (`REPORT.html` and a live local page):
  - every LLM call and where its traffic goes now: **verified** on Router, **unverified**, or **stays** (with the reason);
  - every trade-off taken, what it loses, with Undo;
  - improvement cards, each marked as the agent's proposal or a rule default, with its price impact;
  - the calls that need your judgment;
  - test status (the full suite runs once, in the background, with Router on).
- **An audit trail** in `.router-migrate/`: who changed what, why, and what it costs.

## How it works

```
 1 setup (code)          map every LLM call and whether it routes now; start baseline tests in the background
        │
 2 agent session         migrate by any method; judge latency and task; propose improvements; write MIGRATION.json
        │                tools: check · levers · wrap · unwrap · adapt
 3 outside check (code)  parse · scope (no test/lockfile/CI/runtime edits, no deletions, keys kept) · typecheck ·
        │                the full suite once with Router on (background if slow) · no invalid Router requests · route truth ·
        │                proposals validated and priced · guardrails compiled
        │                fail → one fact-only fix pass → revert only the offending files
 4 Improve (code)        cards = agent proposals ∪ rule defaults; every lever considered, with reasons
        │
 5 dashboard             moves, trade-offs with Undo, cards with provenance, calls for your judgment, tests
        │
 6 Apply (you)           approve → policy compile / codemod option / undo  →  router-migrate check --final
```

In production, per request:

```
your code ─▶ OpenAI / Anthropic SDK ─▶ router runtime ─┬─ Router can serve it exactly ─▶ Router (/v1/responses, /v1/messages)
                                                       └─ otherwise ───────────────────▶ original provider, unchanged
```

Chat Completions calls are translated to Router's Responses API and back, streams included. If Router refuses a request
before generating, the runtime sends it to the provider once. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Levers

The less a lever loses, the less approval it needs. Full policy: [docs/LEVERS.md](docs/LEVERS.md).

| Lever | Who decides | What is lost |
|---|---|---|
| Spend tags (`metadata.feature`, `metadata.product`) | automatic | nothing |
| Prompt cache keys | automatic | nothing |
| Timeout guardrail on buffered user-waiting calls | automatic | nothing; a slow call fails over to the provider |
| Standard tier on user-facing calls (Flex stays Router's default for background calls) | automatic | the Flex discount on those calls |
| Drop parameters Router rejects (`seed`, penalties) | agent, with Undo | determinism hint, penalty effect |
| Dated snapshot → current id of the same model | agent, with Undo | snapshot pinning |
| Route only the operator's key (users' keys stay) | agent, with Undo | nothing for users |
| Drop a performance-only beta | agent, with Undo | the beta's effect |
| Another vendor's endpoint for the same model | agent, with Undo | that vendor's own serving and limits |
| Flex on user-facing calls | you | latency (up to about a minute) |
| Fallbacks | you | on failure a different model answers; cancels Flex |
| A different model | you (shadow first; never automatic) | the current model's behavior |
| Vendor and code ports (Azure, other SDKs) | you | depends on the port |

Router keeps provider selection. Hard limits stay on the provider: no Router endpoint (embeddings, images, audio, files,
moderation, model lists) and provider-side state the code actually uses.

## Safety model

- **Inert by default.** Without `RAMP_ROUTER_API_KEY`, every wrapped client behaves exactly as before. Tests, mocks and
  cassettes see the same requests.
- **Kill switch.** `ROUTER_MIGRATE_DISABLE=1` sends everything to the provider.
- **Per-request fallback.** Anything Router can't serve exactly goes to the provider unchanged. A Router refusal before
  generation is retried on the provider once. A planning error falls back too.
- **No destructive edits.** Tests, lockfiles, CI and the runtime are not edited. No files are deleted. Provider keys stay,
  because fallback traffic still needs them.
- **One outside check.** It re-derives everything from the tree and router-sim, and trusts nothing the agent wrote.
- **Truthful reporting.** A call is "verified" only when a valid Router request from it was seen.
- **Everything reversible.** Trade-offs have Undo. Model changes are never applied without you.
- **Offline during migration.** The agent never holds a Router key. Self-tests run against router-sim, with provider keys
  replaced by dummies.

## Limits

- Python and TS/JS. Clients built dynamically (factories, provider tables) depend on the agent finding them. The outside
  check verifies what a static check or a sandboxed run reaches, and reports the rest as unverified.
- Router serves `/v1/responses`, `/v1/messages` and `/v1/models`. Embeddings, images, audio and files stay on the provider.
- Router keeps no server-side state. Code that relies on stored responses or conversations stays on the provider.
- One Router key per deployment. Per-user keys stay on the provider unless the app also has an operator key.
- Only catalog model ids are served. A model outside the catalog stays on the provider unless a lever or a card maps it.
- Evaluated with one run per arm on small sets. See [docs/EVAL.md](docs/EVAL.md#limitations).

## Repo layout

```
router_migrate/          the CLI
  runtime/               the vendored runtime (Python, TS)
  sim/                   router-sim, a local Router stand-in
  dashboard/             REPORT.html and the live page
  prompts/               the agent's instructions
  ts/                    TypeScript scanner and codemods
  data/                  catalog and measured Router facts
skills/router-migrate/   the Claude Code skill (also linked from .claude/skills/)
.claude-plugin/          Claude Code plugin + marketplace manifest
bin/router-migrate       entry point (bootstraps its own venv when installed as a plugin)
eval/                    eval harness, repo sets, results (RESULTS.md)
tests/                   runtime vs router-sim, codemods, harness end to end, dashboard
docs/                    architecture, methodology, eval, levers
archive/                 earlier specs, decisions, research and eval material (v1–v4), indexed in archive/README.md
```

## Docs

- [WRITEUP.md](WRITEUP.md): one page on the result.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, state files, contracts, the per-request flow, the CLI.
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md): first principles, who decides what, prior art, v1 to v5.
- [docs/EVAL.md](docs/EVAL.md): method, results, limitations, how to reproduce.
- [docs/LEVERS.md](docs/LEVERS.md): every lever and its policy.
- [AGENTS.md](AGENTS.md): instructions for coding agents.
- [archive/docs/](archive/docs/): v1–v4 specs, decisions and system design.

## Development

```bash
make test        # runtime vs router-sim through real SDKs, codemod fixtures, planner, parsers
make compat      # runtime smoke under every SDK version in the eval envs
make sim         # router-sim on :8787
```
