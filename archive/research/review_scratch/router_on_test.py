"""Run one repo test with Router ON against router-sim (as eval's sim_tests does) and show the real failure.
Usage: .venv/bin/python research/review_scratch/router_on_test.py <repo> <pytest node id>
Writes no bytecode into eval/work (PYTHONDONTWRITEBYTECODE=1, -p no:cacheprovider).
"""
import os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from router_migrate.sim.server import serve
repo, node = sys.argv[1], sys.argv[2]
srv, sim, base = serve()
home = os.path.join(ROOT, "research", "review_scratch", "home_" + repo); os.makedirs(home, exist_ok=True)
env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
env.update(RAMP_ROUTER_API_KEY="rm-review-sim-key", RAMP_ROUTER_BASE_URL=base, HOME=home, TMPDIR=home, PYTHONDONTWRITEBYTECODE="1")
py = os.path.join(ROOT, "eval", "work", "envs", repo, "bin", "python")
r = subprocess.run([py, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", node], cwd=os.path.join(ROOT, "eval", "work", "runs", repo), env=env, capture_output=True, text=True)
out = r.stdout + r.stderr
keep = [l for l in out.splitlines() if l.startswith("E ") or "passed" in l or "failed" in l or "error" in l.lower()][:25]
print("\n".join(keep))
print("requests that reached router-sim:", len(sim.records))
srv.shutdown()
