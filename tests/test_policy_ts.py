"""v2 compiled policy in the vendored TypeScript runtime: shared parity fixtures (tests/fixtures/policy) against router-sim."""
import glob
import json
import os
import shutil
import subprocess

import pytest

from router_migrate.catalog import Catalog
from router_migrate.runtime import render

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "policy")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_ts_policy_fixtures(tmp_path, sim):
    p = tmp_path / "routerRuntime.ts"
    p.write_text(render("routerRuntime.ts", Catalog.load()))
    env = dict(os.environ, RAMP_ROUTER_API_KEY="rm-test-key-not-a-real-key", RAMP_ROUTER_BASE_URL=sim.base, NODE_NO_WARNINGS="1")
    env.pop("ROUTER_MIGRATE_POLICY", None)
    r = subprocess.run(["node", os.path.join(HERE, "ts", "policy_check.mjs"), str(p), FIXTURES], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in r.stdout.splitlines() if l.startswith("[")]
    results = json.loads(lines[-1]) if lines else []
    failures = [x for x in results if x[0] != "ok"]
    assert r.returncode == 0 and results and not failures, (failures or r.stdout[-2000:]) + [r.stderr[-2000:]]
    ran = {x[1] for x in results}
    names = {json.load(open(f))["name"] for f in glob.glob(os.path.join(FIXTURES, "*.json"))}
    assert names and all("fixture " + n in ran for n in names), sorted(names - {x[len("fixture "):] for x in ran})
