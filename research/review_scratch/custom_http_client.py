"""Repro: wrap_openai rebuilds the httpx client. Headers / auth / event hooks configured on a caller-supplied
http_client disappear, including on requests that stay on the provider (here: embeddings). Local stub transport only.
Run: .venv/bin/python research/review_scratch/custom_http_client.py
"""
import importlib.util, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import httpx, openai
from router_migrate.catalog import Catalog
from router_migrate.runtime import render
p = os.path.join(tempfile.mkdtemp(), "router_runtime.py"); open(p, "w").write(render("router_runtime.py", Catalog.load()))
spec = importlib.util.spec_from_file_location("rr", p); rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)
os.environ.update(OPENAI_API_KEY="sk-test-not-real")

seen, hooks = [], []
class Stub(httpx.BaseTransport):
    def handle_request(self, req):
        seen.append({k: req.headers.get(k) for k in ("x-gateway-auth",)})
        return httpx.Response(200, json={"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.0]}], "model": "m", "usage": {"prompt_tokens": 1, "total_tokens": 1}})

def make():
    return openai.OpenAI(http_client=httpx.Client(transport=Stub(), headers={"X-Gateway-Auth": "team-42"},
                                                  event_hooks={"request": [lambda r: hooks.append(r.url.path)]}), max_retries=0)
for label, active in (("Router OFF", False), ("Router ON ", True)):
    seen.clear(); hooks.clear()
    if active: os.environ["RAMP_ROUTER_API_KEY"] = "rm-review-fake"
    else: os.environ.pop("RAMP_ROUTER_API_KEY", None)
    c = rt.wrap_openai(make())
    c.embeddings.create(model="text-embedding-3-small", input="x")  # never routed: stays on the provider
    print("%s: provider request header X-Gateway-Auth=%s, request event hook fired=%s" % (label, seen[-1]["x-gateway-auth"], bool(hooks)))
