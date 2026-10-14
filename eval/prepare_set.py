#!/usr/bin/env python3
"""Clone a repo set at its pinned commits and build each repo's environment from its recipe, inside this folder.

  .venv/bin/python eval/prepare_set.py --set heldout [--only a,b] [--no-setup]

Layout (WORK = eval/work for dev, eval/work/heldout for heldout):
  WORK/pristine/<name>  clone at the pinned commit (never modified)
  WORK/base/<name>      copy that dependencies are installed into ($SRC in recipe "setup" commands)
  WORK/envs/<name>      Python virtualenv for the repo ($ENV, $PY)
"""
import argparse
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_eval as RE  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(RE.SETS), default="dev")
    ap.add_argument("--only")
    ap.add_argument("--no-setup", action="store_true", help="clone only")
    args = ap.parse_args()
    RE.SETDIR, RE.WORK = RE.SETS[args.set]
    repos = json.load(open(RE.repos_file()))
    if args.only:
        keep = set(args.only.split(","))
        repos = [r for r in repos if r["name"] in keep]
    for r in repos:
        name = r["name"]
        pristine, base = os.path.join(RE.WORK, "pristine", name), os.path.join(RE.WORK, "base", name)
        if not os.path.isdir(os.path.join(pristine, ".git")):
            os.makedirs(os.path.dirname(pristine), exist_ok=True)
            code, out = RE.sh("git clone -q --filter=blob:none %s %s && git -C %s checkout -q %s" % (
                shlex.quote(r["url"]), shlex.quote(pristine), shlex.quote(pristine), r["commit"]), timeout=1800)
            print("%-32s clone %s" % (name, "ok" if code == 0 else "FAILED: " + out[-300:]), flush=True)
            if code:
                continue
        if not os.path.isdir(base):
            os.makedirs(os.path.dirname(base), exist_ok=True)
            RE.sh("git clone -q %s %s && git -C %s checkout -q %s" % (shlex.quote(pristine), shlex.quote(base), shlex.quote(base), r["commit"]))
        rp = os.path.join(RE.SETDIR, "recipes", name + ".json")
        if args.no_setup or not os.path.exists(rp):
            continue
        rec = json.load(open(rp))
        rec["name"] = name
        for cmd in rec.get("setup", []):
            code, out = RE.sh(RE.subst(cmd, rec, base), timeout=3600)
            if code:
                print("%-32s setup FAILED (%s): %s" % (name, cmd[:60], out[-400:]), flush=True)
                break
        else:
            print("%-32s setup ok" % name, flush=True)


if __name__ == "__main__":
    main()
