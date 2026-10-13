"""Repro: a Responses conversation chained with previous_response_id is split across two backends.
Turn 1 (previous_response_id=None) is routed to Router (store forced off); turn 2 carries Router's response id and is
sent to the provider, which never saw it. Provider is a local stub; Router is router-sim.
Run: .venv/bin/python research/review_scratch/prev_response_chain.py
"""
import importlib.util, json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import httpx, openai
from router_migrate.catalog import Catalog
from router_migrate.runtime import render
from router_migrate.sim.server import serve

srv, sim, base = serve()
os.environ.update(RAMP_ROUTER_API_KEY="rm-review-sim-key", RAMP_ROUTER_BASE_URL=base, OPENAI_API_KEY="sk-test-not-real")
p = os.path.join(tempfile.mkdtemp(), "router_runtime.py"); open(p, "w").write(render("router_runtime.py", Catalog.load()))
spec = importlib.util.spec_from_file_location("rr", p); rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)

class ProviderStub(httpx.BaseTransport):
    """Stands in for api.openai.com: it only knows response ids it created itself."""
    def __init__(self): self.real, self.log = httpx.HTTPTransport(), []
    def handle_request(self, req):
        if req.url.host == "api.openai.com":
            b = json.loads(req.content)
            self.log.append(("provider", b.get("previous_response_id")))
            return httpx.Response(400, json={"error": {"message": "Previous response with id '%s' not found." % b.get("previous_response_id"),
                                                       "type": "invalid_request_error", "param": "previous_response_id", "code": "previous_response_not_found"}})
        self.log.append(("router", json.loads(req.content).get("previous_response_id")))
        return self.real.handle_request(req)

stub = ProviderStub()
client = rt.wrap_openai(openai.OpenAI(http_client=httpx.Client(transport=stub), max_retries=0))
prev = None
for turn in (1, 2):
    try:
        r = client.responses.create(model="gpt-4o-mini", input="turn %d" % turn, previous_response_id=prev)
        prev = r.id
        print("turn %d -> ok, id=%s" % (turn, r.id[:16]))
    except Exception as e:
        print("turn %d -> %s: %s" % (turn, type(e).__name__, str(e)[:120]))
print("backend per turn:", stub.log)
srv.shutdown()
