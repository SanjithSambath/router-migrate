"""Regenerate tests/ts/fixtures/<case>/after + expect.json from `before` (review the diff before committing!).
   .venv/bin/python tests/ts_regen_goldens.py [case ...]"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from router_migrate import cli  # noqa: E402

FIX = os.path.join(HERE, "ts", "fixtures")
RUNTIMES = ("routerRuntime.ts", "routerRuntime.js", "routerRuntime.mjs", "routerRuntime.cjs")


def migrate(case, dst_root):
    work = os.path.join(dst_root, case)
    shutil.copytree(os.path.join(FIX, case, "before"), work)
    cli.do_scan(work, quiet=True)
    cli.do_plan(work, quiet=True)
    cli.do_apply(work, quiet=True)
    return work


def tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".router-migrate", "node_modules")]
        for f in files:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            # vendored runtime copies: content is tested by tests/test_runtime_ts.py; only their location is golden
            out[rel] = "<router runtime>" if f in RUNTIMES and "router-migrate" in open(p).read(400) else open(p).read()
    return out


def outcomes(work):
    plan = json.load(open(os.path.join(work, ".router-migrate", "plan.json")))
    return {"%s:%d" % (s["file"], s["line"]): s["outcome"] for s in plan["sites"]}


if __name__ == "__main__":
    for case in sorted(os.listdir(FIX)):
        if len(sys.argv) > 1 and case not in sys.argv[1:]:
            continue
        tmp = tempfile.mkdtemp()
        work = migrate(case, tmp)
        after = os.path.join(FIX, case, "after")
        shutil.rmtree(after, ignore_errors=True)
        for rel, content in tree(work).items():
            os.makedirs(os.path.dirname(os.path.join(after, rel)), exist_ok=True)
            open(os.path.join(after, rel), "w").write(content)
        json.dump({"outcomes": outcomes(work)}, open(os.path.join(FIX, case, "expect.json"), "w"), indent=2, sort_keys=True)
        print(case, outcomes(work))
        shutil.rmtree(tmp)
