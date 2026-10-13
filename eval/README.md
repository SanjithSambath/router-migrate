# eval

Reproduces `eval/SCOREBOARD.md` from scratch. Everything installs inside this folder.

```bash
source scripts/env.sh
# 1. pinned checkouts (eval/repos.json has full SHAs)
python3 - <<'PY'
import json, subprocess
for r in json.load(open("eval/repos.json")):
    d = "eval/work/pristine/" + r["name"]
    subprocess.run(["git", "clone", "-q", "--filter=blob:none", r["url"], d])
    subprocess.run(["git", "-C", d, "checkout", "-q", r["commit"]])
PY
# 2. per-repo environments: each eval/recipes/<name>.json has "setup" commands ($SRC = eval/work/base/<name>,
#    $ENV = eval/work/envs/<name>, $RM = this folder) and the measured baseline
# 3. the eval
make eval                       # all 15 repos: tests before/after, apply, router-sim, replay
.venv/bin/python eval/run_eval.py --only swarm,aicommits     # a subset
RAMP_ROUTER_API_KEY=... .venv/bin/python eval/run_eval.py --live --live-budget-per-repo 0.05   # + live replay
.venv/bin/python eval/baseline.py   # the docs' migration prompt via `claude -p`, scored identically
```

Outputs: `eval/scoreboard.json` (one object per repo), `eval/SCOREBOARD.md`, `eval/scoreboard.html`, and per repo in
`eval/logs/`: `<name>.diff` (the exact change), `<name>.plan.json` (every site with outcome and reason), `<name>.HANDOFF.md`,
`<name>.{before,after,sim}.log` (test output) and `<name>.row.json`.

What each test column means:
- **tests before / after**: the repo's own offline test command (from its CI), with Router *off*, on a fresh clone at the pinned
  commit, before and after `router-migrate apply`. For repos with no runnable offline tests (openai-responses-starter-app,
  ai-chatbot) the recipe uses a strict `tsc --noEmit` typecheck instead and the column shows TypeScript error counts.
- **sim valid**: the same test command run again with Router *on*, pointed at router-sim. Every request the repo's real code paths
  send is validated against Router's measured rules. Added to that is one synthetic replay per migrated site (its surface, model and
  stream flag through the vendored runtime).
