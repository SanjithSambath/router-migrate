# router-migrate

Move a codebase's production LLM traffic onto [Ramp's Router](https://router.com), and keep everything it did before.

router-migrate is a Claude Code skill plus a small CLI. Your coding agent does the migration: it reads the code, wraps
clients, and takes the small trade-offs that let more calls move. The CLI gives it facts it can't guess (does Router serve
this request?), exact edits, and one check at the end it can't influence. A vendored runtime then decides per request:
Router when Router can serve it exactly, the original provider otherwise. Without `RAMP_ROUTER_API_KEY` it does nothing.
Every cost is written down, and every trade-off has an Undo.

## Quickstart

**In Claude Code** (any model), from your repo:

```
/router-migrate
```

or just say "migrate this repo to Router". The skill runs setup, your session migrates, and `router-migrate finish .`
checks the result and opens the dashboard. Then:

```bash
export RAMP_ROUTER_API_KEY=...        # traffic Router can serve goes to Router; everything else is unchanged
router-migrate check --final          # before you ship
```

**Headless** (CI, evals):

```bash
router-migrate run . --agent claude [--model claude-sonnet-5]
```

**Other agents** (Codex and others): follow [AGENTS.md](AGENTS.md).

**Requirements:** a git repo; Python 3.9+; Node 18+ for TS/JS repos; Claude Code for `/router-migrate` and `--agent claude`.

```bash
git clone <this repo> router-migrate && cd router-migrate
make setup                                       # local .venv and node_modules
export PATH="$PWD/bin:$PATH"                     # router-migrate on PATH
cp -r .claude/skills/router-migrate ~/.claude/skills/   # /router-migrate in every Claude Code session
```

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
 3 outside check (code)  parse · scope (no test/lockfile/CI/runtime edits, no deletions, keys kept) · affected tests,
        │                then the full suite once with Router on · no invalid Router requests · route truth ·
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

## Results

Dev set: 10 open-source repos, 64 movable production LLM calls, one run per arm. Moved = reported migrated **and** sends a
valid Router request. Details and method: [docs/EVAL.md](docs/EVAL.md).

<!-- numbers: WS4 -->
| Approach | Moved / 64 | Wrong moves | Broken repos | Agent $ | Minutes |
|---|---|---|---|---|---|
| Router's public docs prompt | 13 | 0 | 1 / 10 | 35.27 | 122 |
| Docs prompt, from our deterministic floor | 18 | 1 | 0 / 10 | 25.05 | 96 |
| v3 (agent + toolkit, keeps anything lossy) | 7 | 0 | 0 / 10 | 23.13 | 108 |
| v4 (migrate by default, per-item proof) | 21 | 0 | 0 / 9 (+1 timeout) | 17.79 | 180 |
| D3 (facts + optional tools + one outside check) | 17 | 0 | 0 / 10 | 9.87 | 71 |
| **v5** | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

The D3 count is low: the scorer misses some moves it can only see through sandboxed runs. A rescored table, v5's row and
held-out results replace this one.

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
.claude/skills/router-migrate/   the Claude Code skill
bin/router-migrate       entry point
eval/                    eval harness, repo sets, results
tests/                   runtime vs router-sim, codemod fixtures, planner
research/                live Router measurements
docs/                    current docs
archive/                 earlier specs, decisions and progress logs (v1–v4)
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
