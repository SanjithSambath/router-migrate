# router-migrate. Everything installs inside this folder (.venv, node_modules, .tools).
PY := .venv/bin/python
.PHONY: setup test cli-help eval eval-quick sim clean

setup:            ## create the local venv + node deps
	python3 -m venv .venv && $(PY) -m pip install -q -r requirements-dev.txt && npm install --silent --no-audit --no-fund

test:             ## tool unit + golden tests (runtime vs router-sim, codemods, scanner, planner)
	$(PY) -m pytest tests -q -p no:cacheprovider

cli-help:         ## print the CLI help
	@bin/router-migrate --help

sim:              ## run router-sim on :8787
	$(PY) -m router_migrate.sim.server --port 8787

eval:             ## full eval on the 15 pinned repos -> eval/SCOREBOARD.md, scoreboard.json, scoreboard.html
	$(PY) eval/run_eval.py

eval-quick:       ## eval without dependency installs / test runs (scan+plan+apply+replay only)
	$(PY) eval/run_eval.py --no-tests

clean:
	rm -rf eval/work/runs .pytest_cache

compat:           ## runtime smoke under every prepared eval env (different openai/anthropic/httpx(2) versions)
	@for e in eval/work/envs/*/; do [ -x $$e/bin/python ] && echo "$$(basename $$e): $$(PYTHONPATH=$$PWD $$e/bin/python tests/compat_check.py 2>&1 | tail -1)"; done
