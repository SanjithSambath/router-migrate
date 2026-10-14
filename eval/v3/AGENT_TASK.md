# Per-repo task for the v3 fresh-repo eval

You are given one repository, `<name>`, from `eval/v3/REPOS.json`. Paths are under `R = ~/code/router-migrate`:
- `R/eval/work/v3/pristine/<name>`: a clone at the pinned commit. **Read-only:** never modify it.
- `R/eval/work/v3/base/<name>`: a copy at the same commit. Install dependencies here (`$SRC` in recipe commands).
- `R/eval/work/v3/envs/<name>`: put the Python virtualenv here (`$ENV`; `$PY` is `$ENV/bin/python`).

Do the two parts below **in this order**. Part A must not be influenced by the tool being evaluated:
- Never read anything under `R/router_migrate/` except the two catalog files named in `R/eval/heldout/RUBRIC.md`.
- Never read `R/eval/logs/`, `R/eval/heldout/logs/`, any `.router-migrate/` directory, or other repos' inventories.
- Never run `router-migrate` / `bin/router-migrate`.

## Hard rules
- Install only inside `R`; nothing global. Always `source R/scripts/env.sh` first: it pins the uv, npm and pip caches and the uv
  Pythons into `R/.tools`.
  - **Python:** `R/.venv/bin/uv venv --python 3.11|3.12 "$ENV"`, then `R/.venv/bin/uv pip install --python "$ENV/bin/python" ...`
    with `--exclude-newer 2026-10-13T00:00:00Z`.
  - **Node:** use `npm` with the cache from env.sh, or the local pnpm/yarn at `R/.tools/pm/node_modules/.bin/{pnpm,yarn}` with
    `npm_config_store_dir="$R/.tools/pnpm-store"`. Never `npm -g`, `corepack enable`, brew or curl-pipe installers.
- **No API keys.** Run everything with `RAMP_ROUTER_API_KEY` unset (`env -u RAMP_ROUTER_API_KEY`). Never call any LLM API; tests must
  run offline, and when a test hits the network, deselect it and say so.
- Don't commit anything. Don't touch other repos' directories.
- Other agents run at the same time. Put any scratch files in a directory named after your repo, e.g.
  `<scratchpad>/<name>/`, never in a shared path.
- **Timebox Part B to about 40 minutes.** If the suite can't run offline in that time, record why. A TS repo can fall back to a strict
  typecheck.

## Part A: blind inventory
Follow `R/eval/heldout/RUBRIC.md` exactly, reading the pristine copy. Write `R/eval/v3/inventory/<name>.json`.
- Be exhaustive for production code.
- Include non-production sites with `"prod": false` / `"expected": "nonprod"`.
- Use the line where the call or construction expression starts.

## Part B: test recipe
Write `R/eval/v3/recipes/<name>.json` in the same shape as the dev-set recipes; read `R/eval/recipes/swarm.json` (Python) and
`R/eval/recipes/aicommits.json` (TS) as examples. Fields:
- `name`, `lang` (`py`|`ts`), `python` (Python repos), `pm` (TS repos);
- `setup`: shell commands that create `$ENV` and install dependencies into `$SRC`. They run from `R` with `$SRC`, `$ENV`, `$PY` and
  `$RM` (= `R`) substituted. Start each one with `. scripts/env.sh && ...`.
  - If a Python package is installed editable from `$SRC` (`-e "$SRC"`), the eval re-points it at its working copy automatically.
  - Otherwise set `reinstall`: a list of commands that make `$PY` import the code from `$SRC` (for example a `.pth` file), or rely
    on running tests with `PYTHONPATH=.`.
- `test_cmd`: the offline test command, run with cwd = the repo copy. Use `$PY` for Python, and sandbox `HOME`/`TMPDIR` into `$ENV`
  as the examples do. Pick the repo's own CI test command where possible.
- `test_env`, `timeout_s`, and `test_kind` (`unit`, or `typecheck` when there are no runnable tests).
- `baseline`: `{passed, failed, errors, skipped, seconds, exit}` from a real run. Run the command twice and confirm identical counts.
  - The counts must be what `R/router_migrate/verify.py`'s `parse_counts` reads from the output. You may import and call that one
    function (`cd R && .venv/bin/python -c "from router_migrate.verify import parse_counts; ..."`) to check your output is parsed.
  - If nothing can run, use `{"exit": null, "reason": "..."}`.
- TS repos: also `typecheck_cmd` (e.g. `./node_modules/.bin/tsc --noEmit -p .`) and `typecheck_baseline: {exit, errors}`, with
  `errors` = the count of `error TS` in the output. Pre-existing errors are fine; just record them.
- `notes`: what you chose and why, anything deselected, Node/Python versions.

Run the setup and tests in `base/<name>` (`$SRC` = base).

When done, reply with:
- the inventory totals (prod sites by expected outcome);
- the recipe's test command and baseline counts (or why there are none);
- anything unusual, such as rubric cases you weren't sure how to classify.

## v3 additions
- The rubric is `R/eval/heldout/RUBRIC.md`; ignore the output path it names and write to `R/eval/v3/inventory/<name>.json`.
- Also never read `R/eval/v3/runs/`, `R/eval/v3/results.json` or `R/router_migrate/prompts/`.
- **Repos with no test suite:** write a smoke `test_cmd` that proves the code still imports and compiles, so a migration that
  breaks an import is caught. Python: install the deps, then `$PY -m compileall -q <package dirs>` plus a short
  `$PY -c "import <each production module that contains an LLM call>"` with dummy env vars (e.g. `OPENAI_API_KEY=sk-dummy`)
  so module import side effects don't fail; print a final line `N passed in Xs` (pytest style) so `parse_counts` reads it.
  TS: `typecheck_cmd` plus `test_kind: "typecheck"`. Record exactly what the smoke command checks in `notes`.
