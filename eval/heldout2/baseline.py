#!/usr/bin/env python3
"""Run a heldout2 recipe's test_cmd on base/<name> (twice) the way run_eval.tests() does, and record the baseline.

  .venv/bin/python eval/heldout2/baseline.py <name> [--runs 2] [--write]
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import run_eval as RE  # noqa: E402

RE.SETS.setdefault("heldout2", (HERE, os.path.join(RE.EVAL, "work", "heldout2")))
RE.SETDIR, RE.WORK = RE.SETS["heldout2"]
RE.LOGS = os.path.join(HERE, "logs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--write", action="store_true", help="write baseline + stability into the recipe")
    a = ap.parse_args()
    os.makedirs(RE.LOGS, exist_ok=True)
    rp = os.path.join(HERE, "recipes", a.name + ".json")
    rec = json.load(open(rp))
    rec["name"] = a.name
    src = os.path.join(RE.WORK, "base", a.name)
    runs = []
    for i in range(a.runs):
        r = RE.tests(rec, src, "baseline%d" % i)
        c = {k: r.get(k) for k in ("passed", "failed", "errors", "skipped", "seconds", "exit")}
        runs.append(c)
        print(json.dumps(c), flush=True)
        if i == 0:
            print(r["tail"][-1500:])
    key = lambda c: {k: v for k, v in c.items() if k != "seconds"}  # noqa: E731
    same = all(key(c) == key(runs[0]) for c in runs)
    print("identical:", same)
    if a.write:
        rec = json.load(open(rp))
        rec["baseline"] = runs[0]
        rec["stability"] = {"runs": len(runs), "identical": same, "seconds": [c["seconds"] for c in runs]}
        json.dump(rec, open(rp, "w"), indent=2)
        open(rp, "a").write("\n")


if __name__ == "__main__":
    main()
