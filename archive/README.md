# archive/

The current tool is v7 (see the top-level README and [docs/EVOLUTION.md](../docs/EVOLUTION.md)). Nothing in this repo was
deleted during the v5 cleanup or the v7 cleanup. The v7 cleanup left the package (`router_migrate/`) and the test suite
(`tests/`) holding only what v7 runs; every earlier command surface, its tests and the eval scripts that only drove it
moved here (rows marked v7 below), and code that died inside files v7 still uses was removed from those files (listed
under [Trimmed inside kept files](#trimmed-inside-kept-files-v7-cleanup); it is in the parent of the commit named there). In both cleanups, material the tool no longer uses or describes was moved here with `git mv`, so `git log --follow <new path>` still shows its full
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
| **v4: migrate by default** | `run` applies nothing up front. It records a baseline and a proposal per site. The agent migrates by default and records trade-offs. Evidence is produced by the agent and checked by tools, and the eval is weighted by traffic. | [docs/METHODOLOGY.md](docs/METHODOLOGY.md) (the v4 methodology), the v4 agent in `git show b0a246c^:router_migrate/agent.py`, [tests/test_agent_v4.py](tests/test_agent_v4.py) |
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
| `docs/LEVERS.md` | `archive/docs/LEVERS-v5.md` | v5 lever policy (automatic levers, agent trade-offs, human cards); v6 replaces it with the agent's own review and the Router fact sheet (`router_migrate/data/ROUTER.md`). |
| `router_migrate/v2cli.py` | `archive/router_migrate/v2cli.py` | v7 cleanup. The v2 commands `up`, `recommend`, `decide`, `status` and `apply --decisions`. |
| `router_migrate/improve.py` | `archive/router_migrate/improve.py` | v7 cleanup. v2/v3 Improve: cards, decisions, `apply --decisions`, setup checklist, the v5 dashboard bundle. Its policy-compile helpers (`runtime_paths`, `_compile_everywhere` → `compile_everywhere`, `_ts_policy_import`) moved to `router_migrate/policy.py` and the events log to `agent._event`, unchanged. |
| `router_migrate/recommend.py` | `archive/router_migrate/recommend.py` | v7 cleanup. The v2/v3 rules (signals, cards across every lever); v6/v7 used them only for `levers FILE:LINE` hints, now cut (`levers FILE:LINE` shows the model the call names). `enclosing_function` moved to `router_migrate/agent.py` and `_has_flex` to `harness.py`, unchanged. `line_maps` stayed here (only the v4 B2 eval arm used it). |
| `router_migrate/proposals.py` | `archive/router_migrate/proposals.py` | v7 cleanup. v3 merge of agent cards into rule cards. |
| `router_migrate/rules.py` | `archive/router_migrate/rules.py` | v7 cleanup. The v1/v2 rule registry (its test was archived in the v5 cleanup). |
| `router_migrate/report.py` | `archive/router_migrate/report.py` | v7 cleanup. The v1 `report` command (REPORT.md, report.json). |
| `router_migrate/dashboard/view.py`, `page.html`, `render.py` | `archive/router_migrate/dashboard/` | v7 cleanup. The v5 dashboard (calls/improvements view, its page, its renderer). `render.safe_json` moved to `dashboard/items.py`, unchanged. |
| `router_migrate/prompts/migrate.md`, `migrate_d3.md`, `improve.md` | `archive/router_migrate/prompts/` | v7 cleanup. The v3/v4 migrate prompt, the D3/v5 harness prompt and the v3 Improve prompt. v7 reads `migrate_v6.md` and `apply.md`; the eval reads `prompts/arms/`. |
| `router_migrate/data/model_created.json` | `archive/router_migrate/data/model_created.json` | v7 cleanup. Model release dates for recommend.py's newer-and-cheaper rule. |
| `tests/test_agent_v4.py` | `archive/tests/test_agent_v4.py` | v7 cleanup. v4 protocol tests (`run` proposals, `claim`, `card`, `finish` gate and revert, hooks). The tests of tools v7 still has (wrap/unwrap, `check --run` attribution, an `adapt` client lever and its undo) were ported to `tests/test_agent_tools.py`, which also holds the shared toy repo `tests/test_harness_v6.py` imports. |
| `tests/test_improve_v3.py` | `archive/tests/test_improve_v3.py` | v7 cleanup. v3 Improve (`judge`, `improve`, cards, `apply --decisions`). Its snapshot-concurrency test was ported to `tests/test_agent_tools.py`. |
| `tests/test_v2.py` | `archive/tests/test_v2.py` | v7 cleanup. v2 Improve (`recommend`, `decide`, `apply --decisions`, `up`, `status`). |
| `tests/test_dashboard_view.py`, `tests/test_dashboard_v5.py` | `archive/tests/` | v7 cleanup. The v5 dashboard view and bundle. |
| `tests/test_dashboard.py`, `tests/fixtures/dashboard_bundle.json` | `archive/tests/`, `archive/tests/fixtures/` | v7 cleanup. The v5 server and renderer. Its hardening tests (Host, Origin, body limits, methods, headers) were ported to the v7 server in `tests/test_dashboard_v6.py`. |
| `eval/v3/e2e_dashboard.py`, `e2e_run.py`, `run_improve.py` | `archive/eval-v3/` | v7 cleanup. v3 Improve end-to-end and dashboard runners (`improve --agent`, the v3 dashboard server, `improve.bundle`). Their recorded results stay in `eval/v3/improve/`, `eval/v3/e2e/`, `eval/v3/improve_showcase/`. |
| `eval/v3/check_agreement.py`, `check_agreement.json` | `archive/eval-v3/` | v7 cleanup. Agreement of v3 `check` with the v1 pipeline (`cli.do_scan/do_plan/do_apply`) and its result. |

## Deliberately left in place

These are legacy or v1-era, but code that is still live imports or reads them:

- `eval/run_eval.py`: the v1 eval runner. `eval/v3/run_v3.py`, `eval/v3/dev_smoke.py` and `eval/prepare_set.py` use it as a
  library (`SETS`, `prepare`, `sh`, `tests`, `typecheck`, `subst`, `contain`, `repos_file`, ...). Since the v7 cleanup its own runner
  (`run_repo`, the v1 scoreboard) refuses to run: the scan/plan/apply pipeline it drove is archived.
- `eval/scoreboard.py`: imported by `run_eval.write_scoreboard` and loaded by `tests/test_verify.py`
  (`test_scoreboard_router_on_na_and_legacy_rows`).
- `eval/heldout_audit.py`: imported by `eval/v3/run_v3.py`.
- `research/baseline_prompt.txt`: Router's docs migration prompt, read verbatim by `eval/v3/run_v3.py` (arm B').
- `eval/v3/run_v3.py`, `eval/v3/ablation_b2.py`, `eval/run.py`, `eval/v3/score_moved.py`: docs/EVAL.md's Reproduce,
  `eval/lean` and `eval/gate` use them. Since the v7 cleanup only arms B, D3min and D3free run; A, B′, B2, C, Cnf and D ran
  the v1 floor or the v4 toolkit and refuse with a pointer here, while their recorded results still score and report
  (`ablation_b2.py` keeps the v4 outcome vocabulary it validates B2 reports against).
- `router_migrate/schemas/CONTRACTS.md` (the v2 contracts) and `router_migrate/data/ROUTER_open.md`: the fact sheet
  points at CONTRACTS.md section 1 for the policy format, and eval arms read ROUTER_open.md.
- `.claude/skills/router-migrate`: a symlink to `skills/router-migrate`, so Claude Code sessions in this checkout get the
  v7 skill.

## Untracked but not deleted

Agent transcripts (`**/transcript.jsonl`, `**/transcript.jsonl.gz`) under `eval/v3/runs*/` and `eval/heldout*/runs*/` were
removed from the index with `git rm --cached`, and `.gitignore` now ignores them. They are still on disk in the working copy
where they were produced, and earlier commits still contain them.

## Trimmed inside kept files (v7 cleanup)

Removed from files v7 still uses, in commit `b0a246c` ("Archive the v1-v4 command surfaces") and `c4e205d` ("Archive the
v5 dashboard"); `git show b0a246c^:router_migrate/agent.py` (and so on) shows each file whole.

| File | Removed | What it was |
|---|---|---|
| `router_migrate/cli.py` | `do_scan`, `do_plan`, `do_apply`, `reconcile`, `write_handoff`, `acceptance`, `do_verify`, `do_report`, `print_summary`, `get_catalog`, `state_path`/`save`/`load`, the `scan`/`plan`/`verify`/`report`/`up`/`recommend`/`decide`/`status` parsers and the legacy `apply` dispatch | The v1 pipeline and the v2 loop. The scan → plan → apply driver lives on, test-only, as `tests/rules_pass.py`: the codemod goldens run through it. |
| `router_migrate/agent.py` | `cmd_run` (the v4 proposal pass), `cmd_check` (v4 facts), `cmd_claim`, `cmd_card`, `cmd_finish`, `gate`, `summarize`, `_finish_out`, `cmd_judge`, `cmd_improve`, `do_improve`, `plan_for_improve`, `_apply_route_truth`, `_improve_phase`, `_run_agent_then_gate`, `launch_agent`, `open_dashboard`, `serve_dashboard`, `compile_guardrails`, `shadow_floor_run`, `build_proposals`, `hotspots`, `worklist`, `resolved`, `next_for`, `restore`, `_source_files`, `_is_native`, `argparse_ns`, and the `probe`/`claim`/`card`/`judge`/`improve` parsers and constants (`OUTCOMES`, `REASON_CODES`, `CARD_KINDS`, `HARD_LIMITS`, `JUDGE_KEYS`, ...) | The v4 protocol and the v3 Improve phase. `probe` is no longer a command; `check --run` runs the same sandbox (`cmd_probe`). |
| `router_migrate/harness.py` | the `levers FILE:LINE` hints (`recommend.build` card titles) | Replaced by the catalog row and same-family models of the model the call names. |
| `router_migrate/policy.py` | `write_compiled`, `merge_entry` | Used only by `apply --decisions`. |
| `router_migrate/verify.py` | `verify`, `baseline_tests`, `_git`, `_tc` | The v1 `verify` command. |
| `router_migrate/dashboard/server.py` | `validate_decision`, `ACTIONS`, `DATE_RE`, the v5 routes (`/api/decide`, `/api/apply`), `_App`, `serve` | The v5 server; the hardening the v7 server builds on stays. |
