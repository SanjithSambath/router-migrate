"""The vendored TypeScript runtime, exercised through the real openai-node SDK against router-sim (via node)."""
import json
import os
import shutil
import subprocess

import pytest

from router_migrate.catalog import Catalog
from router_migrate.runtime import render

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_ts_runtime_end_to_end(tmp_path, sim):
    p = tmp_path / "routerRuntime.ts"
    p.write_text(render("routerRuntime.ts", Catalog.load()))
    env = dict(os.environ, RAMP_ROUTER_API_KEY="sk-routgw-test-not-a-real-key", RAMP_ROUTER_BASE_URL=sim.base, NODE_NO_WARNINGS="1")
    r = subprocess.run(["node", os.path.join(HERE, "ts", "runtime_check.mjs"), str(p)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in r.stdout.splitlines() if l.startswith("[")]
    results = json.loads(lines[-1]) if lines else []
    failures = [x for x in results if x[0] != "ok"]
    assert r.returncode == 0 and results and not failures, (failures or r.stdout[-2000:]) + [r.stderr[-2000:]]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_ts_runtime_inactive_returns_base(tmp_path):
    p = tmp_path / "routerRuntime.ts"
    p.write_text(render("routerRuntime.ts", Catalog.load()))
    code = "const rt = await import(process.argv[1]); const f = async () => 1; " \
           "console.log(JSON.stringify([rt.routerFetch(f) === f, rt.routerFetch() === undefined, Object.keys(rt.routerOptions()).length]))"
    env = {k: v for k, v in os.environ.items() if k != "RAMP_ROUTER_API_KEY"}
    env["NODE_NO_WARNINGS"] = "1"
    r = subprocess.run(["node", "--input-type=module", "-e", code, str(p)], env=env, capture_output=True, text=True, timeout=60)
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [True, True, 0], r.stderr
