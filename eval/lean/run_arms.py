"""Scaffold/prompt experiments: run several arms (a migrate prompt + scaffold switches) on the same repos, headless,
migrate + improve only (no apply), and save each run's state for judging.

    .venv/bin/python eval/lean/run_arms.py --round r1 --arms v6,lean,min,min_bare --repos sec-insights,AI-Scientist,chatgpt-telegram-bot [--parallel 4]

Arms are defined in ARMS below (RM_PROMPT = prompt file, RM_SCAFFOLD = switches; see harness.scaffold()).
Results: eval/lean/runs/<round>/<arm>/<repo>/."""
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
import run_v3  # noqa: E402

RM = os.path.join(ROOT, "bin", "router-migrate")
PROMPTS = os.path.join(ROOT, "router_migrate", "prompts")
ARMS = {
    "v6": {},
    "lean": {"RM_PROMPT": "arms/cto_lean.md"},
    "min": {"RM_PROMPT": "arms/cto_min.md"},
    "min_bare": {"RM_PROMPT": "arms/cto_min.md", "RM_SCAFFOLD": "nomap,notools"},
}
STATE = ("MIGRATION.json", "validation.json", "ledger.json", "baseline.json", "REPORT.html")


def load_arms():
    p = os.path.join(HERE, "arms.json")  # later rounds add arms here without editing this file
    if os.path.exists(p):
        ARMS.update(json.load(open(p)))
    return ARMS


def one(rnd, arm, name, model):
    env_arm = load_arms()[arm]
    trial = "L_%s_%s" % (rnd, arm)
    dst, rec = run_v3.fresh_copy(name, trial)
    out = os.path.join(HERE, "runs", rnd, arm, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    args = [RM, "run", dst, "--agent", "claude", "--test-cmd", run_v3.test_cmd(rec, dst), "--no-dashboard"]
    tc = run_v3.typecheck_cmd(rec, dst)
    if tc:
        args += ["--typecheck-cmd", tc]
    if model:
        args += ["--model", model]
    env = dict(os.environ, ROUTER_MIGRATE_NO_BROWSER="1", **env_arm)
    t = time.time()
    r = subprocess.run(args, cwd=dst, capture_output=True, text=True, env=env, timeout=7200)
    info = {"arm": arm, "repo": name, "env": env_arm, "exit": r.returncode, "seconds": round(time.time() - t), "trial": dst,
            "out": (r.stdout + r.stderr)[-5000:]}
    for f in STATE:
        p = os.path.join(dst, ".router-migrate", f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(out, f))
    info["branches"] = subprocess.run(["git", "branch", "--list", "router-migrate/*"], cwd=dst, capture_output=True, text=True).stdout.split()
    json.dump(info, open(os.path.join(out, "run.json"), "w"), indent=1)
    return arm, name, r.returncode, info["seconds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--arms", required=True)
    ap.add_argument("--repos", default="sec-insights,AI-Scientist,chatgpt-telegram-bot")
    ap.add_argument("--model")
    ap.add_argument("--parallel", type=int, default=4)
    a = ap.parse_args()
    jobs = [(arm, repo) for arm in a.arms.split(",") for repo in a.repos.split(",")]
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        for res in ex.map(lambda j: one(a.round, j[0], j[1], a.model), jobs):
            print(*res, flush=True)


if __name__ == "__main__":
    main()
