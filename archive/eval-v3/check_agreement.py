#!/usr/bin/env python3
"""T1 check: on the 15 dev repos, apply the floor to a fresh copy, then ask `check`'s oracle (the measure-only planner on the
code as it stands) about every production site plan.json lists. They must agree on every site, and plan.json must still
migrate 48. Writes eval/v3/check_agreement.json.

  .venv/bin/python eval/v3/check_agreement.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from router_migrate import agent, cli  # noqa: E402
from run_v3 import line_map  # noqa: E402

repos = json.load(open(os.path.join(ROOT, "eval", "repos.json")))
rows, total_plan, total_sites, disagree = [], 0, 0, []
for r in repos:
    name = r["name"]
    src = os.path.join(ROOT, "eval", "work", "pristine", name)
    tmp = tempfile.mkdtemp(prefix="rm-agree-")
    dst = os.path.join(tmp, name)
    subprocess.run(["git", "clone", "-q", src, dst], check=True)
    subprocess.run(["git", "-C", dst, "checkout", "-q", r["commit"]], check=True)
    cli.do_scan(dst, quiet=True)
    p = cli.do_plan(dst, quiet=True)
    cli.do_apply(dst, quiet=True)
    p = json.load(open(os.path.join(dst, ".router-migrate", "plan.json")))  # reconciled after apply
    now = agent.current_plan(dst)
    maps = {}
    prod = [s for s in p["sites"] if s.get("prod")]
    mig = sum(1 for s in prod if s["outcome"] == "migrated")
    total_plan += mig
    agree = 0
    for s in prod:
        f = s["file"]
        if f not in maps:
            maps[f] = line_map(os.path.join(src, f), os.path.join(dst, f))
        cands = [x for x in now["sites"] if x["file"] == f and x.get("prod") and maps[f] and abs(maps[f](int(x["line"])) - int(s["line"])) <= 1
                 and x["kind"] == s["kind"]]
        x = sorted(cands, key=lambda x: abs(maps[f](int(x["line"])) - int(s["line"])))[0] if cands else None
        ok = x is not None and (x["outcome"] == "migrated") == (s["outcome"] == "migrated")
        agree += ok
        if not ok:
            disagree.append({"repo": name, "file": f, "line": s["line"], "plan": s["outcome"], "check": x and x["outcome"],
                             "plan_reason": s["reason"][:160], "check_reason": x and x["reason"][:160]})
    total_sites += len(prod)
    rows.append({"repo": name, "prod_sites": len(prod), "plan_migrated": mig, "agree": agree})
    print("%-28s sites %3d  plan migrated %2d  check agrees %3d" % (name, len(prod), mig, agree), flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
out = {"repos": rows, "total_prod_sites": total_sites, "plan_migrated": total_plan,
       "agreement": sum(r["agree"] for r in rows), "disagreements": disagree}
json.dump(out, open(os.path.join(HERE, "check_agreement.json"), "w"), indent=1)
print("TOTAL: %d sites, plan migrated %d, check agrees on %d (%d disagreements)" % (total_sites, total_plan, out["agreement"], len(disagree)))
