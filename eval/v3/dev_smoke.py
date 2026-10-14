#!/usr/bin/env python3
"""T3 check: the full product path `router-migrate run <repo> --test-cmd ... --agent claude` on 3 dev repos. Done when each
ends with `finish` exit 0 and the repo's tests are not worse than its recipe baseline. Writes eval/v3/smoke/dev_smoke.json.

  .venv/bin/python eval/v3/dev_smoke.py swarm shell_gpt aicommits
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "eval"))
sys.path.insert(0, ROOT)
import run_eval as RE  # noqa: E402
sys.path.insert(0, HERE)
from run_v3 import agent_env  # noqa: E402  (importing run_v3 points RE at the v3 set; point it back at dev)

RE.SETDIR, RE.WORK = RE.SETS["dev"]
RE.LOGS = os.path.join(RE.WORK, "logs")
os.makedirs(RE.LOGS, exist_ok=True)
SCORE_ONLY = "--score-only" in sys.argv


def one(name):
    repo = {r["name"]: r for r in json.load(open(os.path.join(ROOT, "eval", "repos.json")))}[name]
    rec = json.load(open(os.path.join(ROOT, "eval", "recipes", name + ".json")))
    rec["name"] = name
    dst = os.path.join(RE.WORK, "v3smoke", name)
    t = time.time()
    if SCORE_ONLY:
        p = subprocess.run([os.path.join(ROOT, "bin", "router-migrate"), "finish", dst], env=agent_env(), capture_output=True, text=True, timeout=3600)
        return _score(name, rec, dst, p, t)
    src = RE.prepare(repo, rec)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    RE.sh("cp -cR %s %s 2>/dev/null || cp -R %s %s" % ((shlex.quote(src), shlex.quote(dst)) * 2))
    shutil.rmtree(os.path.join(dst, ".git"), ignore_errors=True)
    RE.sh("git init -q && printf 'node_modules/\\n' >> .git/info/exclude && git add -A && git -c user.email=e@e -c user.name=e commit -qm base", cwd=dst)
    cmd = RE.subst(rec["test_cmd"], rec, dst)
    env = agent_env()
    env["ROUTER_MIGRATE_PROMPT"] = os.environ.get("ROUTER_MIGRATE_PROMPT", "")
    p = subprocess.run([os.path.join(ROOT, "bin", "router-migrate"), "run", dst, "--test-cmd", cmd, "--agent", "claude"],
                       env=env, capture_output=True, text=True, timeout=5400)
    return _score(name, rec, dst, p, t)


def _score(name, rec, dst, p, t):
    led = json.load(open(os.path.join(dst, ".router-migrate", "ledger.json")))
    after = RE.tests(rec, dst, "v3smoke_after")
    before = rec.get("baseline") or {}
    worse = (after.get("passed") or 0) < (before.get("passed") or 0) or (after.get("failed") or 0) + (after.get("errors") or 0) > \
        (before.get("failed") or 0) + (before.get("errors") or 0)
    hist = (led.get("finish") or {}).get("history") or []
    os.makedirs(os.path.join(HERE, "smoke", name), exist_ok=True)
    shutil.copy(os.path.join(dst, ".router-migrate", "ledger.json"), os.path.join(HERE, "smoke", name, "ledger.json"))
    open(os.path.join(HERE, "smoke", name, "run.log"), "w").write((p.stdout or "")[-15000:] + (p.stderr or "")[-3000:])
    return {"repo": name, "finish_exit": p.returncode, "last_finish_ok": bool(hist and hist[-1]["ok"]), "tests_before": before,
            "tests_after": {k: after.get(k) for k in ("passed", "failed", "errors", "exit")}, "broken": worse,
            "cost": led.get("cost"), "result": led.get("result"), "minutes": round((time.time() - t) / 60, 1)}


if __name__ == "__main__":
    with ThreadPoolExecutor(3) as ex:
        rows = list(ex.map(one, [a for a in sys.argv[1:] if not a.startswith("--")]))
    json.dump(rows, open(os.path.join(HERE, "smoke", "dev_smoke.json"), "w"), indent=1, default=str)
    for r in rows:
        print("%-14s finish_exit=%s last_ok=%s broken=%s $%s %.1f min" % (r["repo"], r["finish_exit"], r["last_finish_ok"], r["broken"],
                                                                         (r["cost"] or {}).get("agent_usd"), r["minutes"]))
