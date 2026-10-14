#!/usr/bin/env python3
"""Run a repo's tests with Router pointed at router-sim (a local stand-in that enforces Router's measured behavior).

  python3 eval/v3/sim_test.py <repo dir> '<test command>'

Sets RAMP_ROUTER_API_KEY to a placeholder and RAMP_ROUTER_BASE_URL to the simulator (with /v1), runs the command, then
prints the test output tail and every request the simulator received, with any it rejected and why."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from router_migrate.sim.server import serve  # noqa: E402

root, cmd = sys.argv[1], sys.argv[2]
srv, sim, base = serve()
try:
    env = dict(os.environ, RAMP_ROUTER_API_KEY="rm-sim-key-not-a-real-key", RAMP_ROUTER_BASE_URL=base + "/v1")
    r = subprocess.run(cmd, cwd=root, shell=True, env=env, capture_output=True, text=True, timeout=1800, executable="/bin/bash")
    print(((r.stdout or "") + (r.stderr or ""))[-3000:])
    print("exit code: %d" % r.returncode)
    recs = [x for x in sim.records if x.get("path", "").startswith("/v1/")]
    print("router-sim received %d request(s), %d valid" % (len(recs), sum(1 for x in recs if x.get("valid"))))
    for x in recs[:40]:
        print("  %s %s model=%s valid=%s%s" % (x.get("method"), x.get("path"), x.get("model"), x.get("valid"),
                                             "" if x.get("valid") else " error=%s" % x.get("error")))
finally:
    srv.shutdown()
