"""Scaffold/prompt experiments: run several arms (a migrate prompt + scaffold switches) on the same repos, headless,
migrate + improve only (no apply), and save each run's state for judging.

    .venv/bin/python eval/lean/run_arms.py --round r1 --arms v6,lean,min,min_bare --repos sec-insights,AI-Scientist,chatgpt-telegram-bot [--parallel 4]

Arms are defined in ARMS below (RM_PROMPT = prompt file, RM_SCAFFOLD = switches; see harness.scaffold()).
Results: eval/lean/runs/<round>/<arm>/<repo>/."""
import argparse
import concurrent.futures as cf
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
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
LIVE = set()  # running harness processes, stopped with the runner


def _stop(*_):
    for p in list(LIVE):
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
    os._exit(1)


PREP = threading.Lock()  # preparing a repo's source isn't safe to run twice at once
STATE = ("MIGRATION.json", "validation.json", "ledger.json", "baseline.json", "REPORT.html")


def load_arms():
    p = os.path.join(HERE, "arms.json")  # later rounds add arms here without editing this file
    if os.path.exists(p):
        ARMS.update(json.load(open(p)))
    return ARMS


def one(rnd, arm, name, model):
    env_arm = load_arms()[arm]
    trial = "L_%s_%s" % (rnd, arm)
    with PREP, open(os.path.join(HERE, ".prep_%s.lock" % name), "w") as lk:  # one prepare per repo, across runners too
        fcntl.flock(lk, fcntl.LOCK_EX)
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
    proc = subprocess.Popen(args, cwd=dst, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            start_new_session=True)  # its own group, so stopping the runner stops the agent too
    LIVE.add(proc)
    try:
        text, _ = proc.communicate(timeout=7200)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        text, _ = proc.communicate()
    LIVE.discard(proc)
    return collect(arm, name, env_arm, dst, out, proc.returncode, round(time.time() - t), text)


def collect(arm, name, env_arm, dst, out, code, secs, text):
    """Save a finished trial's state for the judges (also run on its own: --collect, for a run whose runner died)."""
    os.makedirs(out, exist_ok=True)
    info = {"arm": arm, "repo": name, "env": env_arm, "exit": code, "seconds": secs, "trial": dst, "out": (text or "")[-5000:]}
    for f in STATE:
        p = os.path.join(dst, ".router-migrate", f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(out, f))
    tr = os.path.join(dst, ".router-migrate", "agent_transcript.jsonl")
    errs = [l for l in open(tr, errors="replace")] if os.path.exists(tr) else []
    info["invalid"] = any(('"is_error":true' in l and "API Error" in l) or "hit your session limit" in l or "usage limit" in l
                          for l in errs if '"type":"result"' in l)  # an outage or a spent quota, not a result
    info["branches"] = subprocess.run(["git", "branch", "--list", "router-migrate/*"], cwd=dst, capture_output=True, text=True).stdout.split()
    json.dump(info, open(os.path.join(out, "run.json"), "w"), indent=1)
    return arm, name, code, secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True)
    ap.add_argument("--arms", default="")
    ap.add_argument("--repos", default="sec-insights,AI-Scientist,chatgpt-telegram-bot")
    ap.add_argument("--model")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--jobs", help="explicit arm:repo,... (overrides --arms/--repos)")
    ap.add_argument("--collect", help="arm:repo,...: save already-finished trials' state (exit unknown)")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    os.environ.setdefault("RM_PREP_TAG", "_" + a.round)
    if a.collect:
        for arm, repo in (j.split(":") for j in a.collect.split(",")):
            dst = os.path.join(run_v3.TRIALS, "L_%s_%s" % (a.round, arm), repo)
            tr = os.path.join(dst, ".router-migrate", "agent_transcript.jsonl")
            res = [json.loads(l) for l in open(tr, errors="replace") if '"type":"result"' in l] if os.path.exists(tr) else []
            secs = round(sum(r.get("duration_ms") or 0 for r in res) / 1000) or None  # agent time only
            print(*collect(arm, repo, load_arms()[arm], dst, os.path.join(HERE, "runs", a.round, arm, repo), None, secs, ""))
        return
    jobs = [tuple(j.split(":")) for j in a.jobs.split(",")] if a.jobs else \
        [(arm, repo) for arm in a.arms.split(",") for repo in a.repos.split(",")]
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        futs = {ex.submit(one, a.round, arm, repo, a.model): (arm, repo) for arm, repo in jobs}
        for f in cf.as_completed(futs):
            try:
                print(*f.result(), flush=True)
            except Exception as e:  # one failed run never stops the others
                print(*futs[f], "ERROR", repr(e)[:300], flush=True)


if __name__ == "__main__":
    main()
