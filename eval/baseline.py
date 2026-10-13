#!/usr/bin/env python3
"""Baseline: the migration prompt from https://docs.router.com/getting-started/connect, executed by a Claude coding agent on a
fresh copy of each repo, then scored exactly like router-migrate: same scanner, same planner in measure-only mode (only traffic
that actually points at Router counts as migrated), same recipe tests.

  python3 eval/baseline.py prepare --only a,b   # fresh copy in eval/work/baseline/<name> + tests before
  (agent step: the prompt in research/baseline_prompt.txt, verbatim, run in each copy with file tools only)
  python3 eval/baseline.py score --only a,b     # tests after, typecheck, measure-only scoring -> eval/baseline.json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
from router_migrate import plan as planmod, scan_py  # noqa: E402
from router_migrate.catalog import Catalog  # noqa: E402
import run_eval as RE  # noqa: E402

OUT = os.path.join(ROOT, "eval", "baseline.json")


def recipe(name):
    rp = os.path.join(ROOT, "eval", "recipes", name + ".json")
    rec = json.load(open(rp)) if os.path.exists(rp) else {}
    rec["name"] = name
    return rec


def runnable(rec):
    return bool(rec.get("test_cmd")) and (rec.get("baseline") or {}).get("exit") is not None


def load():
    return json.load(open(OUT)) if os.path.exists(OUT) else {}


def prepare(names):
    repos = {r["name"]: r for r in json.load(open(os.path.join(ROOT, "eval", "repos.json")))}
    res = load()
    for name in names:
        rec = recipe(name)
        src = RE.prepare(repos[name], rec)
        dst = os.path.join(RE.WORK, "baseline", name)
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        if rec.get("lang") == "py":  # editable install -> this copy
            for c in rec.get("reinstall") or [c + " --no-deps" for c in rec.get("setup", []) if " -e " in c and "$SRC" in c]:
                RE.sh(RE.subst(c, rec, dst), timeout=900)
        before = RE.tests(rec, dst, "baseline_before") if runnable(rec) else None
        res[name] = {"repo": repos[name]["repo"], "path": dst, "tests_before": RE.count(before, rec.get("test_kind", "unit")) if before else None}
        json.dump(res, open(OUT, "w"), indent=1)
        print("prepared %s at %s; tests before %s" % (name, dst, res[name]["tests_before"]))


def score(names):
    res = load()
    cat = Catalog.load()
    for name in names:
        rec = recipe(name)
        dst = res[name]["path"]
        if rec.get("lang") == "py":
            for c in rec.get("reinstall") or [c + " --no-deps" for c in rec.get("setup", []) if " -e " in c and "$SRC" in c]:
                RE.sh(RE.subst(c, rec, dst), timeout=900)
        after = RE.tests(rec, dst, "baseline_after") if runnable(rec) else None
        tc = RE.typecheck(rec, dst, "baseline_typecheck_after")
        sc = scan_py.scan(dst)
        if rec.get("lang") == "ts" or os.path.exists(os.path.join(dst, "package.json")):
            try:
                ts = json.loads(subprocess.run(["node", os.path.join(ROOT, "router_migrate", "ts", "tool.mjs"), "scan", dst], capture_output=True, text=True).stdout)
                sc["sites"] += ts.get("sites", [])
            except ValueError:
                pass
        p = planmod.build(sc, cat, measure_only=True)
        diff = subprocess.run("git -C %s add -A -N . >/dev/null 2>&1; git -C %s diff -- . ':(exclude)out' ':(exclude).router-migrate'" % (dst, dst),
                              shell=True, capture_output=True, text=True).stdout
        open(os.path.join(RE.LOGS, name + ".baseline.diff"), "w").write(diff)
        res[name].update(tests_after=RE.count(after, rec.get("test_kind", "unit")) if after else None, typecheck_after=tc, summary=p["summary"],
                         diff_lines=sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))),
                         sites=[{k: s.get(k) for k in ("file", "line", "sdk", "surface", "outcome", "reason")} for s in p["sites"] if s["prod"]])
        json.dump(res, open(OUT, "w"), indent=1)
        print("scored %s: %s; tests %s -> %s; diff %d lines" % (name, {k: p["summary"][k] for k in ("sites_found", "migrated", "flagged")},
                                                             res[name]["tests_before"], res[name]["tests_after"], res[name]["diff_lines"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["prepare", "score"])
    ap.add_argument("--only", required=True)
    a = ap.parse_args()
    (prepare if a.phase == "prepare" else score)(a.only.split(","))
