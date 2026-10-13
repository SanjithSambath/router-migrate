"""Run tests/ts/compat_check.mjs against every prepared TS eval repo (eval/work/base/<name>) with router-sim."""
import json, os, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from router_migrate.catalog import Catalog
from router_migrate.runtime import render
from router_migrate.sim.server import serve
srv, sim, base = serve()
rtp = os.path.join(tempfile.mkdtemp(), "routerRuntime.ts")
open(rtp, "w").write(render("routerRuntime.ts", Catalog.load()))
bad = 0
for r in json.load(open(os.path.join(ROOT, "eval", "repos.json"))):
    d = os.path.join(ROOT, "eval", "work", "base", r["name"])
    if not os.path.exists(os.path.join(d, "node_modules")):
        continue
    env = dict(os.environ, RAMP_ROUTER_API_KEY="rm-test-key-not-a-real-key", RAMP_ROUTER_BASE_URL=base, NODE_NO_WARNINGS="1")
    p = subprocess.run(["node", os.path.join(ROOT, "tests", "ts", "compat_check.mjs"), d, rtp], env=env, capture_output=True, text=True, timeout=120)
    print("%s: %s %s" % (r["name"], (p.stdout.strip().splitlines() or [""])[-1], "" if p.returncode == 0 else "FAIL " + p.stderr.strip()[-300:]))
    bad += p.returncode != 0
srv.shutdown()
print("sim requests: %d, valid: %d" % (len(sim.records), sum(1 for x in sim.records if x["valid"])))
sys.exit(1 if bad else 0)
