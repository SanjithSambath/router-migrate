# Release gate

Any change to the prompt, facts, tools or contract (an arm in `eval/lean/arms.json`) runs against the current default
on the working repos and the held-out repos, then a blind judge compares each pair head-to-head on both rubrics
(`base`, `open`), next to the outside check from each run's `validation.json`.

Rule: **SHIP** if on both rubrics wins >= losses (ties count as neither), the candidate never fails the outside check
where the baseline passes, and its verified-route count is never below the baseline's on a repo; otherwise **HOLD**,
with every reason. Runs cut off by an outage or quota are excluded and listed; under 3 valid pairs is **INSUFFICIENT**.

    .venv/bin/python eval/gate/gate.py --candidate ARM --baseline ARM --round NAME [--repos working|heldout|all|a,b] [--parallel N]
    .venv/bin/python eval/gate/gate.py --candidate open --baseline ultra_levels --round r4 --repos working --judge-only  # no runs
    .venv/bin/python eval/gate/gate.py --candidate open --baseline ultra_levels --round r4 --repos working --report      # cache only

Runs and judgments: `eval/lean/runs/<round>/`; verdict: `eval/gate/runs/<round>/GATE.md`. Tests: `.venv/bin/python -m pytest -q eval/gate`.
