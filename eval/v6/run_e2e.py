"""v6 end to end on eval repos, headless: the agent migrates and improves, `finish` checks, a scripted human makes
requests (approve up to two improvements that passed their check, undo one migration decision), the agent runs the
apply session, and everything is collected for scoring with docs/plans/v6-improve-rubric.md.

    .venv/bin/python eval/v6/run_e2e.py sec-insights AI-Scientist chatgpt-telegram-bot [--model M] [--parallel 3]

Uses the migration key (~/.config/router-migrate/key) when present. Results: eval/v6/runs/<repo>/."""
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "eval", "v3"), os.path.join(ROOT, "eval"), ROOT]
import run_v3  # noqa: E402  (trial copies with prepared dependencies)
from router_migrate import report_v6 as R  # noqa: E402

RM = os.path.join(ROOT, "bin", "router-migrate")
OUT = os.path.join(HERE, "runs")


def sh(args, cwd, timeout=7200):
    t = time.time()
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, ROUTER_MIGRATE_NO_BROWSER="1"))
    return {"exit": r.returncode, "seconds": round(time.time() - t, 1), "out": (r.stdout + r.stderr)[-6000:]}


def requests_for(dst):
    """A scripted human: approve up to two improvements whose branch passed its check; undo the last migration decision
    that has a commit (exercises the agent-run undo)."""
    doc, _ = R.read_report(dst)
    v = R.load(dst, "validation.json", {})
    made = []
    imps = [i for i in (doc or {}).get("items") or [] if i.get("kind") == "improvement"
            and ((v.get("branches") or {}).get(i.get("branch") or "") or {}).get("ok")]
    for it in imps[:2]:
        R.record_request(dst, it["id"], "apply", by="cli")
        made.append((it["id"], "apply"))
    migs = [i for i in (doc or {}).get("items") or [] if i.get("kind") in R.MIGRATION_KINDS and i.get("commit")]
    if migs:
        R.record_request(dst, migs[-1]["id"], "undo", reason="scripted e2e: exercise undo", by="cli")
        made.append((migs[-1]["id"], "undo"))
    return made


def one(name, model):
    dst, rec = run_v3.fresh_copy(name, "V6")
    out = os.path.join(OUT, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    steps = {}
    args = [RM, "run", dst, "--agent", "claude", "--test-cmd", run_v3.test_cmd(rec, dst), "--no-dashboard"]
    tc = run_v3.typecheck_cmd(rec, dst)
    if tc:
        args += ["--typecheck-cmd", tc]
    if model:
        args += ["--model", model]
    steps["run"] = sh(args, dst)
    try:
        steps["requests"] = requests_for(dst)
    except Exception as e:
        steps["requests"] = "error: %s" % e
    if steps["requests"] and not isinstance(steps["requests"], str):
        a = [RM, "apply", dst, "--agent", "claude"] + (["--model", model] if model else [])
        steps["apply"] = sh(a, dst)
    for f in ("MIGRATION.json", "validation.json", "requests.json", "apply_log.json", "ledger.json", "REPORT.html", "agent_transcript.jsonl"):
        p = os.path.join(dst, ".router-migrate", f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(out, f))
    steps["git_log"] = subprocess.run(["git", "log", "--all", "--oneline", "--decorate", "-40"], cwd=dst, capture_output=True, text=True).stdout
    base = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=dst, capture_output=True, text=True).stdout.split()
    if base:
        steps["diff_stat"] = subprocess.run(["git", "diff", "--stat", base[0], R.MIGRATION_BRANCH], cwd=dst, capture_output=True,
                                            text=True).stdout[-3000:]
    json.dump(steps, open(os.path.join(out, "steps.json"), "w"), indent=1)
    return name, {k: (v.get("exit") if isinstance(v, dict) else v) for k, v in steps.items() if k in ("run", "apply", "requests")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--model")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        for name, res in ex.map(lambda n: one(n, a.model), a.repos):
            print(name, res, flush=True)


if __name__ == "__main__":
    main()
