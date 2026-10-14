# Blind inventory task (heldout2)

Repository: `{name}` ({repo} at commit `{commit}`).
`R` = `/Users/sanjithsambath/code/router-migrate-v4` (the docs below say `~/code/router-migrate`; read that as `R`).

Pristine clone: `R/eval/work/heldout2/pristine/{name}`. **Read-only: never modify it.**

Do **only Part A (blind inventory)** of `R/eval/v3/AGENT_TASK.md`, following `R/eval/heldout/RUBRIC.md` exactly (counting
unit, prod/nonprod, the 8 outcome rules applied in order, the JSON schema). There is no Part B for you: do not install anything,
do not build environments, do not run the repo's tests.

Write the result to exactly `/Users/sanjithsambath/code/router-migrate-v4/eval/heldout2/inventory/{name}.json` (absolute path; not under `/Users/sanjithsambath/code/router-migrate/`) (ignore the output paths named in the rubric and the task file).

## Independence rules (this inventory is ground truth for evaluating a tool; it must not be influenced by that tool)
- Never read anything under `R/router_migrate/` except `R/router_migrate/data/catalog.json` (Router model ids under `models`) and
  `R/router_migrate/data/dated_equivalents.json`.
- Never read: any `logs/` directory under `R/eval/`, `R/eval/v3/runs*`, `R/eval/v3/results.json`, `R/eval/v3/sites_moved.json`,
  `R/eval/v3/production_config.json`, `R/eval/v3/adjudication*`, `R/eval/v3/inventory_adjudicated/`, `R/eval/heldout/adjudicat*`,
  any other `inventory/` directory or other repos' inventories, `R/eval/work/` other than this repo's pristine clone, any
  `.router-migrate/` directory, `R/.claude/`, `R/bin/`, `R/router_migrate/prompts/`, or the project's own docs (`R/*.md`).
- Never run `router-migrate` / `R/bin/router-migrate`, and never call any LLM API.
- `R/eval/work` is a symlink into `/Users/sanjithsambath/code/router-migrate/eval/work`; that main checkout
  (`/Users/sanjithsambath/code/router-migrate/`) is the same project: every rule above applies to it too.
- Put scratch files (if any) under `R/eval/work/heldout2/scratch/{name}/`.

## What to produce
`{{"repo": "{repo}", "commit": "{commit}", "sites": [...]}}` with every site carrying the rubric fields
`file, line, prod, sdk, surface, endpoint, key_source, model{{value, static}}, beta, stateful_responses, expected, why`.
- Be exhaustive for production code; include non-production sites with `"prod": false` / `"expected": "nonprod"`.
- `line` = the line where the call / construction expression starts (1-based, in the pristine clone).

**Additional field, on every production site: `production`** = where this call really goes in the repo's documented production
setup, so a scorer does not need hand overrides:
`"production": {{"host": "api.openai.com" | "api.anthropic.com" | "<other host>" | null, "model": "<model id>" | null, "source": "<path:line> <what it says>"}}`
- For config/env-driven endpoints or models (endpoint `env-config` or `runtime-user-defined`, or `model.static: false`), look up
  the documented default: README / docs setup instructions, `.env.example`, `config.example.*`, docker-compose, settings
  defaults in code, the SDK's default host when no base URL is configured. Cite the file and line you used in `source`.
  When several are documented, pick the one the README's main setup path uses and say so in `source`.
- If nothing is documented, use `null` for that part and say so in `source`.
- For a static endpoint and literal model, restate them with `source` = the call line.
- Non-production sites may omit `production`.

## Before you finish
Validate the file: `R/.venv/bin/python -c "import json; d=json.load(open('R/eval/heldout2/inventory/{name}.json')); print(len(d['sites']))"`
and check every site has all fields.

Reply with: totals (production sites by expected outcome, nonprod count) and any rubric cases you were unsure about.
