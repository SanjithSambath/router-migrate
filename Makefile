# router-migrate. Everything installs inside this folder (.venv, node_modules, .tools).
PY := .venv/bin/python
RM := bin/router-migrate
.PHONY: help setup install test demo eval eval-dry dashboard sim compat compat-ts cli-help clean

help:             ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:[^#]*## /\t/'

setup:            ## create the local venv + node deps (node deps: the TS scanner/codemods in router_migrate/ts/tool.mjs)
	python3 -m venv .venv && $(PY) -m pip install -q -r requirements-dev.txt && npm install --silent --no-audit --no-fund

install:          ## editable install (pyproject.toml): puts the `router-migrate` console script in .venv/bin
	$(PY) -m pip install -q -e . --no-deps

test:             ## unit + golden tests (runtime vs router-sim, codemods, scanner, harness, dashboard); archive/ is not collected
	$(PY) -m pytest tests -q -p no:cacheprovider

demo:             ## placeholder: prints how to run a migration on one repo
	@echo "Facts for every LLM call in a repo (nothing changes):"
	@echo "  $(RM) check --all --path <repo>"
	@echo "Migrate and review it in Claude Code: /router-migrate (or $(RM) start <repo>); headless:"
	@echo "  $(RM) run <repo> --test-cmd \"<the repo's tests>\" --agent claude"
	@echo "Agent eval sets and arms: make eval-dry (or eval/run.py --help); prompt changes: eval/gate/gate.py."

# Agent eval (eval/run.py): every (arm, repo) trial of a set; resumable; spends real agent budget.
EVAL_ARGS ?= --set heldout2 --arms D3min --repos all
eval:             ## agent eval via eval/run.py (default EVAL_ARGS: heldout2, D3min, all repos; override EVAL_ARGS="--set v3 ...")
	$(PY) eval/run.py $(EVAL_ARGS)

eval-dry:         ## what `make eval` would run; runs nothing
	$(PY) eval/run.py $(EVAL_ARGS) --dry-run

dashboard:        ## REPORT.html + the live dashboard for a migrated repo: make dashboard REPO=<path>
	@[ -n "$(REPO)" ] || { echo "usage: make dashboard REPO=<path to a migrated repo>"; exit 2; }
	$(RM) dashboard "$(REPO)"

sim:              ## run router-sim on :8787
	$(PY) -m router_migrate.sim.server --port 8787

compat:           ## runtime smoke under every prepared eval env (different openai/anthropic/httpx(2) versions)
	@for e in eval/work/envs/*/; do [ -x $$e/bin/python ] && echo "$$(basename $$e): $$(PYTHONPATH=$$PWD $$e/bin/python tests/compat_check.py 2>&1 | tail -1)"; done

compat-ts:        ## TS runtime smoke with each prepared TS eval repo's own SDK versions
	$(PY) tests/ts_compat.py

cli-help:         ## print the CLI help
	@$(RM) --help

clean:            ## remove caches and build artifacts (never eval/work: in a git worktree it is the main checkout's)
	find . \( -path ./.venv -o -path ./node_modules -o -path ./.tools -o -path ./eval/work \) -prune -o -name __pycache__ -type d -exec rm -rf {} +
	rm -rf .pytest_cache tests/_rendered build dist *.egg-info

# Archived (archive/README.md): the v1 targets `eval` and `eval-quick` ran eval/run_eval.py for the 15-repo v1 scoreboard
# (its outputs are now under archive/eval-v1-v2/). eval/run_eval.py itself stays because eval/v3 imports its helpers; its
# own runner needs the v1 pipeline, archived in the v7 cleanup.
