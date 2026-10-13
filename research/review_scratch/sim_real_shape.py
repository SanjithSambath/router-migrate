"""Repro: eval's sim_valid_pct replays a synthetic 'pong' request per site. The real request shape of
screenshot-to-code's Anthropic site (thinking adaptive + output_config.effort) is rejected by router-sim itself.
Run: .venv/bin/python research/review_scratch/sim_real_shape.py
"""
import json, os, sys, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from router_migrate.sim.server import serve
srv, sim, base = serve()

def post(body):
    req = urllib.request.Request(base + "/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": "k", "User-Agent": "review"})
    try:
        return urllib.request.urlopen(req).status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:160]

synthetic = {"model": "claude-opus-5-5", "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with exactly: pong"}]}
real = dict(synthetic, max_tokens=50000, thinking={"type": "adaptive"}, output_config={"effort": "high"}, stream=True)
print("eval replay shape     ->", post(synthetic))
print("site's real shape     ->", post(real))
srv.shutdown()
