# archive/

Nothing in this repo was deleted during the v5 cleanup. Material that the current tool (v5: facts, tools and one outside
check) no longer uses or describes was moved here with `git mv`, so `git log --follow <new path>` still shows its full
history. Files are kept as they were. Paths inside them still point at their old locations (for example `SPEC_V2.md`,
`eval/SCOREBOARD.md`, `research/m0_measurements.md`). To find something, look up its old path in the table below.

Archived scripts are kept for the record. They are **not** expected to run from here: they resolve paths relative to their
old locations (`eval/`, `scripts/`, `tests/`). To re-run one, `git mv` it back first.

## How the project got here

| Stage | What it was | Where it is described |
|---|---|---|
| **v1: deterministic pipeline** | `scan -> plan -> apply -> verify -> report`. A libcst / ts-morph scanner finds every LLM client and call site. A planner buckets each one (migrated / kept / flagged / handoff) with a reason. Codemods vendor `router_runtime.py` / `routerRuntime.ts` under the SDK client, which translates at the HTTP boundary (D1). The eval covered 15 pinned repos: tests before and after with Router off, the repo's tests with Router on against router-sim, and replay of every migrated request shape. Result: 48 of 170 production sites moved losslessly, and all 15 test suites were unchanged. | [docs/SPEC.md](docs/SPEC.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/PROGRESS.md](docs/PROGRESS.md), [docs/DECISIONS.md](docs/DECISIONS.md) D1–D10, [eval-v1-v2/SCOREBOARD.md](eval-v1-v2/SCOREBOARD.md) |
| **v2: policy / Improve** | v1 stays the automatic lossless first pass. v2 adds `up`, `recommend`, `decide` and `status`: Improve cards (unlocks, same-model policy, Flex tier, caching, model recommendations), which a human or agent approves, dismisses or postpones. A compiled per-feature policy is applied at runtime. M0 measured the levers live for $0.0004. | [docs/SPEC_V2.md](docs/SPEC_V2.md), [docs/DECISIONS.md](docs/DECISIONS.md) D11–D12, [research/m0_measurements.md](research/m0_measurements.md), [eval-v1-v2/SCOREBOARD_V2.md](eval-v1-v2/SCOREBOARD_V2.md) |
| **v3: agent + toolkit** | A coding agent runs the migration. It uses `run`, `check`, `wrap`, `claim` and `finish`, and a gate it cannot skip. The deterministic floor is never lowered. Arms A (rules-only), B (docs prompt), B' (docs prompt + same abilities) and C (agent + toolkit) ran on 10 fresh repos with blind adjudication. The v3 Improve phase adds agent judgments, cards across every Router lever, and the whole-run dashboard. | [docs/SPEC_V3.md](docs/SPEC_V3.md), [docs/DEMO_V3.md](docs/DEMO_V3.md), [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md), [docs/DECISIONS.md](docs/DECISIONS.md) D13–D16, `eval/v3/` (still live) |
| **v4: migrate by default** | `run` applies nothing up front. It records a baseline and a proposal per site. The agent migrates by default and records trade-offs. Evidence is produced by the agent and checked by tools, and the eval is weighted by traffic. | [docs/METHODOLOGY.md](docs/METHODOLOGY.md) (the v4 methodology; a new one is being written under `docs/`), `router_migrate/agent.py` |
| **D3 / v5: facts + tools + outside check** | `router_migrate/harness.py`: the agent gets the facts (`check --all`) and a few optional tools, and picks its own method. One outside check then re-derives "moved", runs the tests and typecheck against the baseline, and does a Router-on run and a required report. One resume with the failure facts follows, and anything that still fails is reverted. | current `README.md`, `WRITEUP.md`, `docs/` (not archived) |

Other material archived here: the background research in [research/](research/), which covers Router docs, SDK surfaces, hand-made inventories of the 15
dev repos, M0 live measurements and the v1 adversarial review with its repro scripts.

## Index of moved items

| Old path | New path | Why archived |
|---|---|---|
| `SPEC.md` | `archive/docs/SPEC.md` | v1 spec (15-repo work trial brief). Superseded by later specs and the current docs. |
| `SPEC_V2.md` | `archive/docs/SPEC_V2.md` | v2 Improve-phase spec. |
| `SPEC_V3.md` | `archive/docs/SPEC_V3.md` | v3 agent-native spec (thesis, Track 1 results). Superseded by v4/v5. |
| `PROGRESS.md` | `archive/docs/PROGRESS.md` | Milestone log for v1–v3 (M1..., v2.x, v3 T0–T7). |
| `DECISIONS.md` | `archive/docs/DECISIONS.md` | Decision record D1–D16 with evidence. Still the best reference for *why* the runtime works as it does. |
| `DEMO_V3.md` | `archive/docs/DEMO_V3.md` | 2-minute v3 demo script on chatgpt-telegram-bot. |
| `SYSTEM_DESIGN.md` | `archive/docs/SYSTEM_DESIGN.md` | System walk-through built around `router-migrate up` (v2/v3). |
| `ARCHITECTURE.md` | `archive/docs/ARCHITECTURE.md` | v1 scan/plan/apply/verify architecture diagram. |
| `METHODOLOGY.md` | `archive/docs/METHODOLOGY.md` | v4 methodology. A new METHODOLOGY is being written under `docs/`. |
| `eval/README.md` | `archive/eval-v1-v2/README.md` | Describes reproducing the v1 scoreboard (`make eval` → `eval/run_eval.py`, `eval/baseline.py`). |
| `eval/run_eval_v2.py` | `archive/eval-v1-v2/run_eval_v2.py` | v2 eval runner (15 dev repos → `scoreboard_v2.json`, per-repo REPORT.html). |
| `eval/summarize.py` | `archive/eval-v1-v2/summarize.py` | Printed the v1 WRITEUP numbers from `scoreboard.json`. |
| `eval/baseline.py` | `archive/eval-v1-v2/baseline.py` | v1 baseline (Router docs prompt run by `claude -p`, scored like the tool). The v3 arms B/B' replaced it. |
| `eval/baseline.json` | `archive/eval-v1-v2/baseline.json` | v1 baseline results. |
| `eval/scoreboard.json`, `eval/scoreboard.html`, `eval/SCOREBOARD.md` | `archive/eval-v1-v2/` | v1 15-repo scoreboard (48/170 sites, 15/15 suites unchanged). |
| `eval/scoreboard_v2.json`, `eval/SCOREBOARD_V2.md` | `archive/eval-v1-v2/` | v2 scoreboard. |
| `eval/live_spend_v2.jsonl` | `archive/eval-v1-v2/live_spend_v2.jsonl` | Live Router spend ledger from the v2 eval and M0. |
| `eval/dashboard_live_check.md`, `eval/dashboard_tabs_check.md` | `archive/eval-v1-v2/` | v2 dashboard manual check notes. |
| `eval/logs/` | `archive/eval-v1-v2/logs/` | v1 per-repo logs (`<name>.diff`, `.plan.json`, `.HANDOFF.md`, test logs, `.row.json`). The eval runner now writes its logs under `eval/work/<set>/logs`. |
| `eval/logs_v2/` | `archive/eval-v1-v2/logs_v2/` | v2 per-repo logs. |
| `eval/reports/` | `archive/eval-v1-v2/reports/` | v2 per-repo REPORT.html dashboards. |
| `eval/screenshots/` | `archive/eval-v1-v2/screenshots/` | v2 dashboard screenshots. |
| `research/` (everything except `baseline_prompt.txt`) | `archive/research/` | Background research: `router_docs.md`, `sdk_surfaces.md`, `repos_inventory.json`, `inventory/` (hand-made v1 inventories), `m0/` + `m0_measurements.md` (v2 M0 live measurements), `adversarial_review.md` + `review_scratch/` (v1 adversarial review and repros). |
| `tests/test_cli_e2e.py` | `archive/tests/test_cli_e2e.py` | Exercises the v1 pipeline `apply -> verify -> report` and `scan --json` (plus a no-agent `run`, which `tests/test_agent_v4.py` also covers). |
| `tests/test_rules_registry.py` | `archive/tests/test_rules_registry.py` | Checks that every rule in `router_migrate/rules.py` (the v1/v2 rule registry, `make rules`) names an existing test. It scans its own directory for tests, so from here it would only see `archive/tests/`. |
| `scripts/done_check.sh`, `done_check_v2.sh`, `done_check_v3.sh`, `done_check_v3_improve.sh` | `archive/scripts/` | Per-version definitions of done. Each chains to the v1 check, which requires the archived top-level docs and v1 scoreboard, so none can pass any more. |
| Makefile targets `eval`, `eval-quick` (v1) | removed from `Makefile` (not moved) | They ran `eval/run_eval.py` to rebuild the v1 scoreboard. `make eval` now runs the agent eval (`eval/run.py`). To rebuild the v1 scoreboard, run `.venv/bin/python eval/run_eval.py [--no-tests]` directly. |

## Deliberately left in place

These are legacy or v1-era, but code that is still live imports or reads them:

- `eval/run_eval.py`: the v1 eval runner. `eval/v3/run_v3.py`, `eval/v3/dev_smoke.py` and `eval/prepare_set.py` use it as a
  library (`SETS`, `prepare`, `sh`, `tests`, `typecheck`, `subst`, `contain`, `repos_file`, ...).
- `eval/scoreboard.py`: imported by `run_eval.write_scoreboard` and loaded by `tests/test_verify.py`
  (`test_scoreboard_router_on_na_and_legacy_rows`).
- `eval/heldout_audit.py`: imported by `eval/v3/run_v3.py`.
- `research/baseline_prompt.txt`: Router's docs migration prompt, read verbatim by `eval/v3/run_v3.py` (arm B').
- `tests/test_v2.py`: v2 Improve tests. Its `FILES` fixture app is imported by `tests/test_improve_v3.py`,
  `tests/test_dashboard_view.py` and `eval/v3/e2e_run.py`.

## Untracked but not deleted

Agent transcripts (`**/transcript.jsonl`, `**/transcript.jsonl.gz`) under `eval/v3/runs*/` and `eval/heldout*/runs*/` were
removed from the index with `git rm --cached`, and `.gitignore` now ignores them. They are still on disk in the working copy
where they were produced, and earlier commits still contain them.

| `docs/LEVERS.md` | `archive/docs/LEVERS-v5.md` | v5 lever policy (automatic levers, agent trade-offs, human cards); v6 replaces it with the agent's own review and the Router fact sheet (`router_migrate/data/ROUTER.md`). |
