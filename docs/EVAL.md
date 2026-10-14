# Evaluation

The eval measures the two things that matter to Router:

1. **Traffic moved.** How many production LLM calls send valid requests to Router after the migration.
2. **Behavior kept.** Whether the repo still parses, typechecks and passes its tests, with nothing deleted.

Every arm runs on the same repos from the same commit, and is scored by the same code.

## Method

### Repo sets

| Set | Repos | Used for | Ground truth |
|---|---|---|---|
| Dev | 10 (AI-Scientist, chatgpt-telegram-bot, computer_use_ootb, llmcord, obsidian-copilot, obsidian-textgenerator-plugin, OpenManus, openui, page-assist, sec-insights) | Fresh for v3; used to tune v3 to v5 since | 97 production sites, **64 movable** |
| Held-out | 30 (`eval/heldout/`) | Not used for tuning | per-repo inventories |
| v1/v2 | 15 pinned repos (`eval/repos.json`) | v1 and v2 development | v1 scoreboard |

The dev set was fresh when v3 was evaluated. It is not fresh now. The held-out set is the honest test.

### Ground truth

Each repo has an inventory of production LLM call sites, written blind by an agent against a rubric before any arm ran
(`eval/v3/inventory/`). A site is **movable** when it is a production LLM generation call and no hard limit applies:

- Router has no endpoint for it (embeddings, images, audio, files, moderation, legacy completions, model lists);
- it uses provider-side state;
- it is a local or self-hosted model;
- it goes through the app's own proxy or gateway.

`movable()` in `eval/v3/score_moved.py` is mechanical. No LLM judges outcomes. Calls that need a trade-off to move
(a dropped `seed`, another vendor's endpoint) still count as movable, because the stance is migrate by default.

### Scorer

A site counts as **moved** only when both hold:

- the arm reports it migrated, **and**
- it sends a valid Router request. Either the route check builds a representative request from the site's static
  arguments, runs it through the real vendored runtime with the trial's compiled policy, and router-sim accepts it; or the
  harness re-runs the agent's own sandboxed runs on the final code and sees a valid request from the site.

Wrapped-and-claimed earns nothing. That rule changed the picture: AI-Scientist's `seed=0` calls were wrapped and claimed by
several arms, but the runtime keeps them on OpenAI, so they never moved.

| Bucket | Meaning |
|---|---|
| Moved | reported migrated, valid Router traffic |
| Wrong | moved, but not movable (hard limit, non-production, user's own key with no operator key) |
| Wrapped, no traffic | reported migrated, but requests stay on the provider |
| Broken repo | tests or typecheck worse than baseline, or the outside check fails |

v5 reports the same truth to the user per call: **verified** (seen on Router), **unverified** (claimed, not seen, e.g. a
dynamic client no static check reaches) and **stays**.

Invalid runs (API errors, an agent that produced nothing, an agent timeout with no decisions) are not scored. `eval/run.py`
retries them from a fresh copy and reports them separately.

### Arms are ablations

Each arm changes one thing from its neighbor.

| Arm | What it is | Isolates |
|---|---|---|
| A | rules only (v1 floor) | no agent |
| B | an agent with Router's public docs prompt, from the untouched repo | the status quo |
| B′ | the docs prompt, starting from the floor | the floor's effect on B |
| B2 (B″) | a detailed prompt with all our rules and recipes, floor, same checks | prompt text vs tools |
| C | v3: agent + toolkit + floor + gate | tools |
| Cnf | C without the floor | the floor |
| D | v4: migrate by default, trade-offs, agent-produced evidence, per-item claims, gate loop | the v4 protocol |
| D3min | facts + optional tools + one outside check | process obligations |
| D3free | D3 with no outside check and no required report | the outside check |

The model was pinned (claude-sonnet-5) for every agent arm.

## Results

### Dev set: traffic moved

From [eval/v3/SITES_MOVED.md](../eval/v3/SITES_MOVED.md) and [eval/v3/ABLATION_B2.md](../eval/v3/ABLATION_B2.md). 64
movable sites across 10 repos. k=1.

| Arm | Moved / 64 | Wrong | Broken repos | Wrapped, no traffic | Trade-offs taken | Agent $ | Minutes |
|---|---|---|---|---|---|---|---|
| A rules only | 9 | 1 | 0 / 10 | 10 | 0 | 0 | 8 |
| B docs prompt | 13 | 0 | **1 / 10** | 0 | 0 | 35.27 | 122 |
| B′ docs prompt + floor | 18 | 1 | 0 / 10 | 7 | 0 | 25.05 | 96 |
| B2 detailed prompt | 12 | 1 | 0 / 10 | 4 | 0 | 13.82 | 60 |
| C v3 | 7 | 0 | 0 / 10 | 3 | 0 | 23.13 | 108 |
| Cnf v3, no floor | 7 | 0 | 0 / 10 | 3 | 0 | 15.63 | 82 |
| D v4 | **21** | 0 | 0 / 9 (+1 timeout) | 1 | 14 | 17.79 | 180 |
| D3min | 17 verified | 0 | 0 / 10 | 2 | 6 | **9.87** | **71** |

Minutes are wall clock per run, summed over repos. D3's agent time alone was 58 minutes.

<!-- numbers: WS4 -->
> **Pending.** The D3 row is under-counted: the scorer misses some D3 moves it can only see through the agent's own
> sandboxed runs. WS4 is fixing this and rescoring every arm. The rescored table replaces the one above.

<!-- numbers: WS4 -->
> **Pending.** v5 row (same 10 repos, same scorer).

### What the dev set shows

- **Prompts alone make wrong moves or break things.** The docs prompt broke a repo. With the floor it moved 18 but made a
  wrong move and left 7 calls wrapped with no Router traffic.
- **Tools remove wrong moves.** Every arm with fact-computing tools (C, Cnf, D, D3) made 0 wrong moves.
- **Migrate by default moves more.** v3 kept anything that needed a trade-off (7 moved). v4 took trade-offs and moved 21.
- **Process is the cost.** D3 drops v4's per-item claims, mandatory proof and gate loop. It keeps 0 wrong and 0 broken,
  at 55% of v4's dollars and 39% of its minutes (about 2.5× faster).

### Held-out set (partial)

<!-- numbers: WS4 -->
> **Pending.** 10 of 30 planned runs are done. Early runs: D3min moved 2/8 (gpt-cli), 4/14 (kotaemon), 4/7
> (ai-hedge-fund), 0/7 (claude-engineer), 3/9 (mini-swe-agent), with 0 wrong and 0 broken. The full table goes here.

### Findings

- **Wrapped is not moved.** AI-Scientist's `seed=0` kept three OpenAI calls off Router under every earlier scorer.
  Config-driven base URLs (`base_url=config.GROQ_BASE_URL`) and calls reachable from several clients did the same. The
  scorer now asks where each request really goes.
- **Tests miss destructive edits.** On held-out gpt-cli, arm B deleted the provider-key config and the Azure provider.
  Tests were 14/14 before and after. Scope checks catch this; tests don't.
- **A production crash, found and fixed.** The v4 pilot found that sec-insights deep-copies its LlamaIndex LLM, which
  crashed with Router on. Every arm since v1 had shipped it.
- **Large suites.** One suite took 325 s per run. Re-running it in a gate loop dominated v4's time. v5 runs affected tests
  first and the full suite once, in the background.

## Limitations

- **k=1.** One run per arm per repo. Agent variance is large: obsidian-textgenerator-plugin moved 4 sites in one run and
  0 in another under the same arm. Differences of a few sites between arms are within noise.
- **Small sets.** 10 dev repos, 64 movable sites. 0/10 broken bounds the true broken rate at about 31% (95%).
- **The dev set is no longer fresh.** It was used to tune v3 to v5.
- **Held-out is partial.** 10 of 30 runs.
- **LLM-written ground truth.** Inventories were written blind by an agent against a rubric. `movable()` is mechanical,
  but it reads the inventory's fields (endpoint, production, surface).
- **Static request shapes.** The route check builds requests from literal arguments. Arguments built at runtime are
  invisible to it, so a dynamic request may differ from the one scored.
- **Offline only.** No eval run held a Router key. Router-on runs used router-sim, which enforces Router's measured
  contract but is not Router.

## Reproduce

```bash
make setup
# one arm on the dev set, two repos, nothing spent
.venv/bin/python eval/run.py --set v3 --arms D3min --repos llmcord,openui --dry-run
# the full dev set, detached
nohup .venv/bin/python eval/run.py --set v3 --arms D3min,D3free,B --repos all --jobs 6 > eval/v3/run.log 2>&1 &
# held-out
nohup .venv/bin/python eval/run.py --set heldout --arms D3min,D3free,B --repos all --jobs 6 > eval/heldout/run.log 2>&1 &
# score and write the tables
.venv/bin/python eval/v3/score_moved.py score
.venv/bin/python eval/v3/score_moved.py report      # eval/v3/SITES_MOVED.md
.venv/bin/python eval/v3/ablation_b2.py report      # eval/v3/ABLATION_B2.md
```

Runs are resumable. Each run writes `status.json` and a line to `DONE.txt`. Agent spend comes from the agent transcript.
The eval needs Claude Code on `PATH` and no `RAMP_ROUTER_API_KEY` (the harness strips it).
